import re
import json
from typing import Optional

from backend.db import db
from backend.memory_client import memory_client
from backend.context_cache import context_cache
from backend.agents_catalog import enabled_agents, is_authenticated

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
        routing_prompt = f"""
Analyze the following user prompt and categorize it for multi-agent routing.
Respond ONLY with a JSON object in this format:
{{
  "task_type": "coding" | "research" | "deep_analysis" | "long_context" | "creative" | "logic" | "general",
  "complexity": "low" | "medium" | "high",
  "mode": "plan" | "execute",
  "recommended_agents": ["claude", "gemini", "codex", "grok"],
  "explanation": "short reason",
  "parallel": true | false
}}

Prompt: {prompt}
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

    def check_memory(self, prompt: str, session_id: str = "default") -> Optional[str]:
        # ── Semantic memory ───────────────────────────────────────────────────
        semantic_results = memory_client.search(prompt, limit=3)
        all_snippets = (
            [r["content"] for r in semantic_results] if semantic_results else []
        )
        new_snippets = context_cache.filter_memory(session_id, all_snippets)

        # ── Graph: matched entities ───────────────────────────────────────────
        all_entity_rows = db.query_all(
            "MATCH (e:Entity) WHERE lower($prompt) CONTAINS lower(e.name) RETURN e.name, e.type, e.description",
            {"prompt": prompt},
        )
        new_entity_rows = context_cache.filter_entities(session_id, all_entity_rows)
        entity_names = [
            row[0] for row in all_entity_rows
        ]  # all — for relationship + QA lookup
        {row[0] for row in new_entity_rows}

        # ── Graph: 1-hop relationships (batched) ───────────────
        all_rel_rows = []
        if entity_names:
            all_rel_rows = db.query_all(
                "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) "
                "WHERE a.name IN $names "
                "RETURN a.name, b.name, b.type, b.description, r.type",
                {"names": entity_names},
            )
        new_rel_rows = context_cache.filter_relations(session_id, all_rel_rows)

        # ── Graph: past Q&A (batched) ───────────────────────────
        all_qa_rows = []
        if entity_names:
            all_qa_rows = db.query_all(
                "MATCH (q:Question)-[:ABOUT]->(e:Entity) "
                "WHERE e.name IN $names "
                "RETURN q.prompt, q.answer, q.agent ORDER BY q.timestamp DESC LIMIT 10",
                {"names": entity_names},
            )
        new_qa_rows = context_cache.filter_qa(session_id, all_qa_rows)

        # ── Graph: Filesystem structure (keyword match on path/name) ─────────
        all_file_rows = db.query_all(
            "MATCH (f:File) WHERE $prompt CONTAINS f.name OR $prompt CONTAINS f.path "
            "RETURN f.path, f.name, f.extension LIMIT 5",
            {"prompt": prompt},
        )
        new_file_rows = context_cache.filter_entities(
            session_id + ":files", all_file_rows
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
            len(all_entity_rows)
            - len(new_entity_rows)
            + len(all_rel_rows)
            - len(new_rel_rows)
            + len(all_qa_rows)
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

    def learn_from_prompt(self, prompt: str, answer: str, agent: str):
        """
        Extract entities from a completed Q&A via regex heuristics.
        No LLM call — zero quota spend for bookkeeping.
        """
        prompt = self._extract_user_prompt(prompt)
        try:
            text = f"{prompt} {answer}"
            technical = set(
                re.findall(
                    r"\b[A-Z][a-zA-Z]{2,}\b"  # CamelCase identifiers
                    r"|\b\w+\.(?:py|js|go|ts|json|yaml)\b"  # filenames
                    r"|\b[a-z]+_[a-z_]{2,}\b",  # snake_case names
                    text,
                )
            )
            entities = [t for t in technical if 3 < len(t) < 60][:50]

            # Store the question first to get a stable ID
            qid = db.add_question(prompt, answer[:500], agent)

            for name in entities:
                db.add_entity(name, "extracted", "")
                db.link_question_to_entity(qid, name)

        except Exception as e:
            print(f"[learn_from_prompt] {e}")

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

        if preferred_agent and preferred_agent in available:
            return [preferred_agent], {}

        if prompt:
            inferred = self._infer_agents_from_prompt(prompt)
            valid = [a for a in inferred if a in available]
            if valid:
                return valid, {"mode": _detect_execute_mode(prompt)}

        # ── Tier 0: SLM-based routing (Ollama) ──
        slm_decision = _classify_task_slm(prompt) if prompt else None
        if slm_decision:
            task_type = slm_decision.get("task_type", task_type)
            complexity = slm_decision.get("complexity", "low")
            recommended = [
                a for a in slm_decision.get("recommended_agents", []) if a in available
            ]
            if recommended:
                # For high complexity, generate internal reasoning block
                if complexity == "high":
                    reasoning = self._reason_task_slm(prompt)
                    if reasoning:
                        slm_decision["internal_reasoning"] = reasoning
                # Deterministic override: never trust Ollama's mode for file-edit tasks
                slm_decision["mode"] = _detect_execute_mode(prompt)
                return recommended, slm_decision

        if task_type == "general" and prompt:
            task_type, complexity = _classify_task(prompt)
        else:
            _, complexity = _classify_task(prompt) if prompt else ("general", "low")

        # ── Tier 1: Affinity-based routing (Learned) ─────────────────────────
        best_affinity = self._get_best_affinity_agent(task_type, available)
        if best_affinity:
            # Check if this task warrants adversarial review (Consensus Round 3)
            if task_type in ("deep_analysis", "logic") or complexity == "high":
                # For high complexity, add a second agent to critique
                critics = [a for a in available if a != best_affinity]
                if critics:
                    return [best_affinity, critics[0]], {"complexity": complexity, "mode": _detect_execute_mode(prompt)}
            return [best_affinity], {"complexity": complexity, "mode": _detect_execute_mode(prompt)}

        # ── Tier 2: Strength-based routing (Static fallback) ─────────────────
        for agent in available:
            if task_type in self.strengths.get(agent, []):
                return [agent], {"complexity": complexity, "mode": _detect_execute_mode(prompt)}

        return [available[0]], {"complexity": complexity, "mode": _detect_execute_mode(prompt)}

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
