import logging
import re
import json
from typing import Optional

logger = logging.getLogger(__name__)

from backend.db import db
from backend.memory_client import memory_client
from backend.context_cache import context_cache
from backend.agents_catalog import enabled_agents, is_authenticated
from backend.security import is_blocked_entity

# _ENTITY_BLOCKLIST lives in security.py — use is_blocked_entity() instead.

# ── Sensitivity Classification (Provenance-Aware Export Guards) ──────────────
_SENSITIVE_PATTERN = re.compile(
    r"(?i)"
    r"/Users/[\w.-]+/"             # Local user paths
    r"|/home/[\w.-]+/"              # Linux home paths
    r"|[a-zA-Z]:\\[Uu]sers\\"        # Windows user paths
    r"|\b[\w.-]+\.local\b"           # .local hostnames
    r"|\b[\w.-]+\.internal\b"        # .internal hostnames
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}" # Private IP Class A
    r"|192\.168\.\d{1,3}\.\d{1,3}"   # Private IP Class C
    r"|172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}" # Private IP Class B
)


def _is_sensitive(text: str) -> bool:
    """Heuristic to check if an entity looks like private infrastructure or identifiers."""
    return bool(_SENSITIVE_PATTERN.search(text))


_PROMPT_MAX_CHARS = 8000  # Truncate before any regex/brain work

# All agents are subscription CLIs
_CLI_MAP = {
    "claude": "claude",
    "gemini": "gemini",
    "codex": "codex",
    "grok": "grok",
}


def _cli_available(agent: str) -> bool:
    cmd = _CLI_MAP.get(agent)
    if cmd is None:
        return False
    # In Docker mode the CLI lives in a sibling container — trust is_installed_anywhere()
    from backend.agents import is_installed_anywhere

    return is_installed_anywhere(agent)


# ── Rule-based task classifier (no API call, no quota spend) ─────────────────

_EXECUTE_PATTERN = re.compile(
    # File edit operations
    r"\b(edit|update|modify|change|fix|rewrite|replace|add to|insert|delete from|remove from|refactor|rename)\b.{0,80}"
    r"\.(md|py|go|ts|js|json|yaml|yml|txt|sh|toml|cfg|env|html|css)\b"
    r"|\b(update|edit|modify|change|fix|rewrite)\b.{0,40}\b(readme|config|file|script|template|dockerfile)\b"
    # Git / shell operations
    r"|\b(push|commit|branch|checkout|merge|rebase|pull request|PR|open a PR|create a PR|create.*branch|git)\b"
    r"|\b(run|execute|install|build|deploy|start|stop|restart|test|lint|format|compile)\b.{0,40}"
    r"\b(command|script|server|backend|frontend|docker|container|service|process)\b"
    r"|\b(make.*changes|apply.*changes|check.*changes|move.*changes)\b"
    # Affirmative go-aheads and "implement the suggestions"-style follow-ups
    r"|\b(implement|apply|make|do)\b.{0,60}"
    r"\b(suggestions?|recommendations?|proposals?|improvements?|fix(?:es)?|changes?|plans?|patch(?:es)?|edits?)\b"
    r"|\b(go ahead|proceed|do it|make it so|ship it|implement it|apply it|implement this|apply this)\b",
    re.IGNORECASE | re.DOTALL,
)


def _detect_execute_mode(prompt: str) -> str:
    """Return 'execute' if the prompt is clearly a file-edit task, else 'plan'."""
    return "execute" if _EXECUTE_PATTERN.search(prompt) else "plan"


_TASK_PATTERNS = {
    "coding": re.compile(r"\b(code|function|class|method|def |bug|fix|refactor|implement|debug|test|compile|syntax|variable|loop|algorithm|api|sql|json|yaml|exception|import|module|library|framework|deploy|dockerfile|script)\b", re.IGNORECASE),
    "deep_analysis": re.compile(r"\b(analyze|analyse|review|audit|security|performance|architecture|evaluate|assess|critique|deep.dive|trade.?off|bottleneck)\b", re.IGNORECASE),
    "research": re.compile(r"\b(what is|explain|how does|why|when did|history|compare|difference|overview|describe|tell me about|who is|where is|background)\b", re.IGNORECASE),
    "long_context": re.compile(r"\b(summarize|summarise|entire|full file|whole file|all of|document|codebase|transcript|paste)\b", re.IGNORECASE),
    "creative": re.compile(r"\b(write a|create a|generate|draft|compose|story|poem|blog|essay|marketing|copy)\b", re.IGNORECASE),
    "logic": re.compile(r"\b(solve|calculate|math|proof|prove|equation|formula|logic|reasoning|puzzle|optimise|optimize)\b", re.IGNORECASE),
}


def _classify_task(prompt: str) -> tuple[str, str]:
    best, best_n = "general", 0
    for task, pattern in _TASK_PATTERNS.items():
        n = len(pattern.findall(prompt))
        if n > best_n:
            best, best_n = task, n

    complexity = "low"
    if best_n > 5:
        complexity = "high"
    elif best_n > 2:
        complexity = "medium"

    return best, complexity


def _classify_task_slm(prompt: str) -> Optional[dict]:
    """Use local Ollama to classify task and recommend agents. Zero cloud quota cost."""
    from backend.agents import agent_factory
    try:
        ollama = agent_factory.get_agent("ollama")
        routing_prompt = f"""You are a routing engine. Read the user request and decide which AI agent(s) to call.

Respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON.

{{
  "task_type": "coding" | "research" | "deep_analysis" | "long_context" | "creative" | "logic" | "general",
  "complexity": "low" | "medium" | "high",
  "mode": "plan" | "execute",
  "recommended_agents": ["<agent>"],
  "multi_agent_explicit": false,
  "explanation": "<one sentence>"
}}

AGENT SELECTION RULES — read carefully:

1. DEFAULT: always recommend exactly ONE agent.
2. Only recommend TWO agents when the user EXPLICITLY asks for comparison, a second opinion, debate, or multiple perspectives. This means words like "compare X and Y", "get both claude and gemini", "debate this", "second opinion". NOT just because the task is hard.
3. NEVER recommend more than two agents. NEVER recommend all agents.
4. Set "multi_agent_explicit": true ONLY when rule 2 applies — the user literally asked for multiple agents.

AGENT STRENGTHS:
- claude: coding, logic, nuanced analysis, architecture
- gemini: research, long documents, summarization
- codex: pure code generation, refactoring
- grok: creative writing, general questions, quick lookups

COMPLEXITY RULES:
- low: single factual question, quick lookup, short task
- medium: moderate coding task, multi-step explanation
- high: large refactor, multi-system architecture, deep security audit

EXAMPLES:
- "how does X work?" → 1 agent (gemini or grok), low complexity
- "fix this bug in my code" → 1 agent (claude or codex), medium complexity
- "compare claude vs gemini on this problem" → 2 agents, multi_agent_explicit: true
- "install all the dependencies" → 1 agent, this is NOT a multi-agent request
- "what are all the files in the project?" → 1 agent, "all" here is not about agents
- "give me a second opinion on this architecture" → 2 agents, multi_agent_explicit: true
- "write a complex distributed system with auth, DB, and API" → 1 agent (claude), high complexity — hard task does NOT mean fan-out

User request: {prompt}
"""
        response = ""
        for chunk in ollama.execute_stream(routing_prompt, simple=True):
            if "[Ollama Error]: Model" in chunk:
                print(f"[_classify_task_slm] Skipping SLM routing: {chunk.strip()}")
                return None
            response += chunk

        # Extract JSON from potential markdown blocks
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        print(f"[_classify_task_slm] Error: {e}")
    return None


from backend.models import ErrorType

# ── AgentRouter ───────────────────────────────────────────────────────────────


class AgentRouter:
    # Fallback Directed Acyclic Graph: specifies backups for specific failure modes.
    FALLBACK_DAG = {
        # If Claude fails with context overflow, try Gemini (huge context).
        ("claude", ErrorType.CONTEXT_OVERFLOW): "gemini",
        # If a coding agent has a logic error, try a stronger/different model.
        ("codex", ErrorType.LOGIC_ERROR): "claude",
        ("claude", ErrorType.LOGIC_ERROR): "codex",
        # Default fallbacks
        ("grok", ErrorType.UNKNOWN): "claude",
        # Quota exhaustion — switch to the next available agent
        ("gemini", ErrorType.TRANSIENT_CAPACITY): "claude",
        ("claude", ErrorType.TRANSIENT_CAPACITY): "gemini",
        ("codex", ErrorType.TRANSIENT_CAPACITY): "claude",
        ("grok", ErrorType.TRANSIENT_CAPACITY): "claude",
    }

    # Which agent is best-suited for which task type.
    # Claude: nuanced coding/logic; Gemini: research/long context;
    # Codex: coding (OpenAI subscription); Grok: research/creative.
    strengths = {
        "claude": ["coding", "logic", "nuance", "deep_analysis"],
        "gemini": ["research", "long_context"],
        "codex": ["coding", "logic"],
        "grok": ["research", "creative", "general"],
    }

    def _get_best_affinity_agent(
        self, task_type: str, available_agents: list[str]
    ) -> Optional[str]:
        """Query KuzuDB for the agent with the highest affinity score for this task type."""
        try:
            rows = db.query_all(
                "MATCH (t:TaskType {name: $t})-[r:AFFINITY]->(a:AgentNode) "
                "WHERE a.name IN $available "
                "RETURN a.name, r.score ORDER BY r.score DESC LIMIT 1",
                {"t": task_type, "available": available_agents},
            )
            if rows:
                return rows[0][0]
        except Exception as e:
            print(f"[_get_best_affinity_agent] {e}")
        return None

    def _reason_task_slm(self, prompt: str) -> Optional[str]:
        """Use local Ollama to generate a structured reasoning/planning block for complex tasks."""
        from backend.agents import agent_factory
        try:
            ollama = agent_factory.get_agent("ollama")
            reasoning_prompt = f"""
You are the LeadAgent reasoning engine. Analyze the user's prompt and create a detailed step-by-step implementation plan.
Focus on identifying potential pitfalls, required tools, and architectural impact.

User Prompt: {prompt}

Output your plan as a structured "INTERNAL REASONING" block.
"""
            response = ""
            for chunk in ollama.execute_stream(reasoning_prompt, simple=True):
                if "[Ollama Error]" in chunk:
                    return None
                response += chunk
            return response.strip()
        except:
            return None

    def update_outcome(self, task_type: str, agent: str, success: bool):
        """Update affinity based on whether the agent's work was successful."""
        delta = 0.1 if success else -0.2
        db.update_affinity(task_type, agent, delta)

    def check_memory(
        self,
        prompt: str,
        session_id: str = "default",
        project_id: str = "default",
        memory_scope: str = "shared",
    ) -> Optional[str]:
        prompt = prompt[:_PROMPT_MAX_CHARS]
        # ── Semantic memory ───────────────────────────────────────────────────
        sem_project = None if memory_scope == "global" else project_id
        semantic_results = memory_client.search(prompt, limit=3, project_id=sem_project, strict=(memory_scope == "strict"))
        all_snippets = []
        for r in (semantic_results or []):
            content = (
                r.get("content")
                or r.get("observation", {}).get("narrative")
                or r.get("observation", {}).get("title")
                or ""
            )
            if content:
                all_snippets.append(content)
        new_snippets = context_cache.filter_memory(session_id, all_snippets)

        # ── Project-ID scoping ────────────────────────────────────────────────
        # "strict"  → only this project
        # "shared"  → this project OR the global "default" pool (backward compat)
        # "global"  → no filter (admin / single-project setups)
        if memory_scope == "strict":
            pid_clause = "AND e.project_id = $pid "
            pid_params: dict = {"pid": project_id}
        elif memory_scope == "shared":
            # Global pool entities require confidence >= 0.7 to filter out auto-extracted noise.
            pid_clause = "AND (e.project_id = $pid OR (e.project_id = 'default' AND e.confidence >= 0.7)) "
            pid_params = {"pid": project_id}
        else:  # global
            pid_clause = ""
            pid_params = {}

        # ── Graph: matched entities ───────────────────────────────────────────
        # Export Guard: Return project_id to filter sensitive data from cross-project sources
        all_entity_rows = db.query_all(
            "MATCH (e:Entity) WHERE lower($prompt) CONTAINS lower(e.name) "
            + pid_clause
            + "RETURN e.name, e.type, e.description, e.project_id",
            {"prompt": prompt, **pid_params},
        )

        # Export guard: enforce project isolation as a defence-in-depth layer
        # even if the DB query returns cross-project rows unexpectedly.
        filtered_entity_rows = []
        for row in all_entity_rows:
            e_name, e_type, e_desc, e_pid = row
            if e_pid != project_id and project_id != "default":
                # strict: reject all cross-project entities unconditionally
                if memory_scope == "strict":
                    continue
                # shared: allow 'default' pool only; reject all other projects
                if e_pid != "default":
                    continue
                # shared + default pool: still filter sensitive names
                if _is_sensitive(e_name) or _is_sensitive(e_desc or ""):
                    continue
            filtered_entity_rows.append((e_name, e_type, e_desc))

        new_entity_rows = context_cache.filter_entities(session_id, filtered_entity_rows)
        entity_names = [
            row[0] for row in filtered_entity_rows
        ]  # filtered — for relationship + QA lookup

        # ── Graph: 1-hop relationships (batched) ───────────────
        all_rel_rows = []
        if entity_names:
            if memory_scope == "strict":
                rel_pid_clause = "AND a.project_id = $pid AND b.project_id = $pid "
            elif memory_scope == "shared":
                rel_pid_clause = (
                    "AND (a.project_id = $pid OR a.project_id = 'default') "
                    "AND (b.project_id = $pid OR b.project_id = 'default') "
                )
            else:
                rel_pid_clause = ""
            all_rel_rows = db.query_all(
                "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) "
                "WHERE a.name IN $names "
                + rel_pid_clause
                + "RETURN a.name, b.name, b.type, b.description, r.type, b.project_id",
                {"names": entity_names, **pid_params},
            )

        # Apply export guard to relationships
        filtered_rel_rows = []
        for r in all_rel_rows:
            if r[5] != project_id and project_id != "default":
                if _is_sensitive(r[1]) or _is_sensitive(r[3] or ""):
                    continue
            filtered_rel_rows.append(r[:5])

        new_rel_rows = context_cache.filter_relations(session_id, filtered_rel_rows)

        # ── Graph: past Q&A (batched) ───────────────────────────
        all_qa_rows = []
        if entity_names:
            # Build qa_pid_clause independently — Question nodes have no confidence field.
            if memory_scope == "strict":
                qa_pid_clause = "AND q.project_id = $pid "
            elif memory_scope == "shared":
                qa_pid_clause = "AND (q.project_id = $pid OR q.project_id = 'default') "
            else:
                qa_pid_clause = ""
            all_qa_rows = db.query_all(
                "MATCH (q:Question)-[:ABOUT]->(e:Entity) "
                "WHERE e.name IN $names "
                + qa_pid_clause
                + "RETURN q.prompt, q.answer, q.agent, q.project_id ORDER BY q.timestamp DESC LIMIT 10",
                {"names": entity_names, **pid_params},
            )

        # Apply export guard to QA
        filtered_qa_rows = []
        for r in all_qa_rows:
            if r[3] != project_id and project_id != "default":
                if _is_sensitive(r[0]) or _is_sensitive(r[1]):
                    continue
            filtered_qa_rows.append(r[:3])

        new_qa_rows = context_cache.filter_qa(session_id, filtered_qa_rows)

        # ── Graph: Filesystem structure (keyword match on path/name) ─────────
        if memory_scope == "strict":
            file_pid_clause = "AND f.project_id = $pid "
        elif memory_scope == "shared":
            file_pid_clause = "AND (f.project_id = $pid OR f.project_id = 'default') "
        else:
            file_pid_clause = ""
        all_file_rows = db.query_all(
            "MATCH (f:File) WHERE ($prompt CONTAINS f.name OR $prompt CONTAINS f.path) "
            + file_pid_clause
            + "RETURN f.path, f.name, f.extension, f.project_id LIMIT 5",
            {"prompt": prompt, **pid_params},
        )
        
        # Apply export guard to files
        filtered_file_rows = []
        for r in all_file_rows:
            if r[3] != project_id and project_id != "default":
                if _is_sensitive(r[0]) or _is_sensitive(r[1]):
                    continue
            filtered_file_rows.append(r[:3])

        new_file_rows = context_cache.filter_entities(
            session_id + ":files", filtered_file_rows
        )

        # ── Build context blocks from new-only items ──────────────────────────
        combined = []

        if new_snippets:
            combined.append("[RELEVANT MEMORIES]\n" + "\n".join(new_snippets))

        entity_lines = [f"  {r[0]} ({r[1]}): {r[2]}" for r in new_entity_rows if r[2]]
        rel_lines = [
            f"  {r[1]} ({r[2]}){' — ' + r[3] if r[3] else ''} [via {r[4]}]"
            for r in new_rel_rows
        ]
        if entity_lines or rel_lines:
            combined.append(
                "[PROJECT KNOWLEDGE]\n" + "\n".join(entity_lines + rel_lines)
            )

        if new_qa_rows:
            qa_lines = [f"  Q: {r[0]}\n  A ({r[2]}): {r[1]}" for r in new_qa_rows]
            combined.append("[PAST DISCUSSIONS]\n" + "\n---\n".join(qa_lines))

        if new_file_rows:
            file_lines = [f"  {r[0]}" for r in new_file_rows]
            combined.append(
                "[PROJECT STRUCTURE (Matched Files)]\n" + "\n".join(file_lines)
            )

        if not combined:
            return None

        # Commit injected items so they're skipped on the next message
        context_cache.commit(
            session_id,
            entities=new_entity_rows,
            relations=new_rel_rows,
            qa_rows=new_qa_rows,
            memory_snippets=new_snippets,
        )

        skipped = (
            len(filtered_entity_rows)
            - len(new_entity_rows)
            + len(filtered_rel_rows)
            - len(new_rel_rows)
            + len(filtered_qa_rows)
            - len(new_qa_rows)
            + len(all_snippets)
            - len(new_snippets)
        )
        if skipped:
            print(
                f"[ContextCache] session={session_id} skipped {skipped} already-injected blocks"
            )

        return "\n\n".join(combined)

    @staticmethod
    def _extract_user_prompt(prompt: str) -> str:
        """Strip Go CLI conversation history prefix, return only the last user message."""
        # CLI prepends "[Conversation so far]\nUser: ...\nAssistant: ...\nUser: <actual>"
        # Find the last "User:" line and return everything after it.
        last = prompt.rfind("\nUser:")
        if last != -1:
            return prompt[last + 6:].strip()  # 6 = len("\nUser:")
        # Also strip our injected task delimiters if present
        task_start = prompt.find("=== YOUR TASK ===")
        if task_start != -1:
            end = prompt.find("=== END TASK ===", task_start)
            inner = prompt[task_start + 17: end if end != -1 else None].strip()
            return inner
        return prompt.strip()

    def learn_from_prompt(self, prompt: str, answer: str, agent: str, project_id: str = "default", session_id: str = "default"):
        """
        Extract entities from a completed Q&A via regex heuristics.
        No LLM call — zero quota spend for bookkeeping.
        """
        prompt = self._extract_user_prompt(prompt)
        prompt = prompt[:_PROMPT_MAX_CHARS]
        answer = answer[:_PROMPT_MAX_CHARS]
        try:
            # Always store the question for history; skip entity graph pollution for un-scoped prompts
            qid = db.add_question(prompt, answer[:500], agent, source_project_id=project_id, session_id=session_id)

            if project_id != "default":
                text = f"{prompt} {answer}"
                technical = set(
                    re.findall(
                        r"\b[A-Z][a-zA-Z]{2,}\b"  # CamelCase identifiers
                        r"|\b\w+\.(?:py|js|go|ts|json|yaml)\b"  # filenames
                        r"|\b[a-z]+_[a-z_]{2,}\b",  # snake_case names
                        text,
                    )
                )
                entities = [
                    t for t in technical
                    if 3 < len(t) < 60 and not is_blocked_entity(t)
                ][:50]

                for name in entities:
                    db.add_entity(name, "extracted", "", source_project_id=project_id, auto_extracted=True, source_agent=agent)
                    db.link_question_to_entity(qid, name, project_id=project_id)

            # Distil Q&A pair into semantic memory so it's retrievable across sessions
            memory_client.store(
                content=f"Q: {prompt}\nA: {answer[:400]}",
                metadata={"agent": agent, "project_id": project_id, "session_id": session_id, "type": "qa"},
                tier="semantic",
            )

        except Exception as e:
            logger.warning("[learn_from_prompt] %s", e)

    _KNOWN_AGENTS = ("claude", "gemini", "codex", "grok")

    _AGENT_INTENT_RE = re.compile(
        r"\b(?:use|ask|with|via|switch to|route to|send to|have|let|get|want)\s+(claude|gemini|codex|grok)\b"
        r"|\bi\s+want\s+(claude|gemini|codex|grok)\s+to\b"
        r"|\b(claude|gemini|codex|grok)\s+(?:please|should|can you|to)\b",
        re.IGNORECASE,
    )

    # Phrases that imply fan-out to multiple agents
    _MULTI_AGENT_RE = re.compile(
        r"\b(?:both|all|each|compare|versus|vs\.?|side.by.side)\b"
        r"|\band\s+(?:claude|gemini|codex|grok)\b"
        r"|\b(?:claude|gemini|codex|grok)\s+and\b",
        re.IGNORECASE,
    )

    def _infer_agents_from_prompt(self, prompt: str) -> list[str]:
        """
        Return a list of explicitly named agents from the prompt.
        Returns multiple agents when fan-out intent is detected,
        a single-item list for single-agent intent, or [] for auto-routing.
        """
        named = [
            a
            for a in self._KNOWN_AGENTS
            if re.search(rf"\b{a}\b", prompt, re.IGNORECASE)
        ]

        if len(named) >= 2 and self._MULTI_AGENT_RE.search(prompt):
            return named  # fan-out

        if len(named) == 1:
            return named  # single explicit agent

        # No explicit names — check leading word
        first = prompt.split()[0].rstrip(",:").lower() if prompt.split() else ""
        if first in self._KNOWN_AGENTS:
            return [first]

        # Single-agent natural language match
        m = self._AGENT_INTENT_RE.search(prompt)
        if m:
            return [next(g for g in m.groups() if g).lower()]

        return []

    def get_fallback(self, agent: str, error_type: ErrorType) -> Optional[str]:
        """Retrieve the designated fallback agent for a given failure mode."""
        fallback = self.FALLBACK_DAG.get((agent, error_type))
        if fallback:
            enabled = enabled_agents()
            if fallback in enabled and is_authenticated(fallback) is not False:
                return fallback
        return None

    def route_multi(
        self, task_type: str, prompt: str = "", preferred_agent: str = None
    ) -> tuple[list[str], dict]:
        """
        Like route() but returns (list[agents], metadata).
        Multiple agents = fan-out request.
        """
        available = [
            a
            for a in enabled_agents()
            if _cli_available(a) and is_authenticated(a) is not False
        ]
        if not available:
            return ["none"], {}

        # ── User Overrides (Strictly single-agent) ──
        if preferred_agent and preferred_agent in available:
            return [preferred_agent], {"mode": _detect_execute_mode(prompt)}

        user_msg = self._extract_user_prompt(prompt) if prompt else ""
        if prompt:
            # Strip conversation history — only match agent intent in the actual user message.
            # Running regex on full history causes false fan-out when prior turns name agents.
            inferred = self._infer_agents_from_prompt(user_msg)
            valid = [a for a in inferred if a in available]
            if valid:
                # If the user named exactly one agent, stick to it (no double billing)
                if len(valid) == 1:
                    return valid, {"mode": _detect_execute_mode(prompt), "force_single": True, "routing_reason": "explicit"}
                return valid, {"mode": _detect_execute_mode(prompt), "routing_reason": "explicit"}

        # ── Tier 0: SLM-based routing (Ollama) ──
        # Pass only the clean user message — not full history — so Ollama sees intent, not noise.
        slm_decision = _classify_task_slm(user_msg) if prompt else None
        if slm_decision:
            task_type = slm_decision.get("task_type", task_type)
            complexity = slm_decision.get("complexity", "low")
            recommended = [
                a for a in slm_decision.get("recommended_agents", []) if a in available
            ]
            if recommended:
                # Fan-out only when Ollama explicitly confirmed the user asked for multiple agents.
                # Hard tasks, high complexity, or agent names appearing elsewhere do NOT qualify.
                multi_explicit = slm_decision.get("multi_agent_explicit", False)
                if not multi_explicit:
                    recommended = recommended[:1]
                # Hard cap: never more than 2 regardless of what Ollama says
                recommended = recommended[:2]
                # For high complexity single-agent tasks, generate internal reasoning block
                if complexity == "high":
                    reasoning = self._reason_task_slm(user_msg)
                    if reasoning:
                        slm_decision["internal_reasoning"] = reasoning
                # Deterministic override: never trust Ollama's mode for file-edit tasks
                slm_decision["mode"] = _detect_execute_mode(prompt)
                slm_decision["routing_reason"] = "slm"
                return recommended, slm_decision

        if task_type == "general" and prompt:
            task_type, complexity = _classify_task(prompt)
        else:
            _, complexity = _classify_task(prompt) if prompt else ("general", "low")

        # ── Tier 1: Affinity-based routing (Learned) ─────────────────────────
        best_affinity = self._get_best_affinity_agent(task_type, available)
        if best_affinity:
            # Add a critic only when the prompt is substantive enough to warrant it:
            # - task type suggests deep work, AND
            # - prompt is long enough to be a real analysis request (not a follow-up), AND
            # - keyword signal is strong (best_n > 3 avoids short prompts that weakly match)
            _, complexity_n = _classify_task(prompt)
            task_signal = sum(len(p.findall(prompt)) for p in _TASK_PATTERNS.values())
            wants_critic = (
                not preferred_agent
                and (task_type in ("deep_analysis", "logic") or complexity == "high")
                and len(prompt) > 150
                and task_signal > 3
            )
            if wants_critic:
                critics = [a for a in available if a != best_affinity]
                if critics:
                    return [best_affinity, critics[0]], {"complexity": complexity, "mode": _detect_execute_mode(prompt), "routing_reason": "affinity+critic"}
            return [best_affinity], {"complexity": complexity, "mode": _detect_execute_mode(prompt), "routing_reason": "affinity"}

        # ── Tier 2: Strength-based routing (Static fallback) ─────────────────
        for agent in available:
            if task_type in self.strengths.get(agent, []):
                return [agent], {"complexity": complexity, "mode": _detect_execute_mode(prompt), "routing_reason": "strength"}

        return [available[0]], {"complexity": complexity, "mode": _detect_execute_mode(prompt), "routing_reason": "default"}

    def get_explanation(self, agent: str, prompt: str) -> str:
        task_type, complexity = _classify_task(prompt)
        return f"[{agent}] matched: complexity={complexity}, task={task_type}"

    _PARALLEL_RE = re.compile(
        r"\b(?:parallel|simultaneously|concurrently|at the same time|side.by.side)\b",
        re.IGNORECASE,
    )

    def detect_parallel(self, prompt: str) -> bool:
        return bool(self._PARALLEL_RE.search(prompt))

    def route(
        self, task_type: str, prompt: str = "", preferred_agent: str = None
    ) -> str:
        """Single-agent routing — returns first result from route_multi."""
        agents, _ = self.route_multi(task_type, prompt, preferred_agent)
        return agents[0]


agent_router = AgentRouter()
