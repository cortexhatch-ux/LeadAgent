"""
GAN-style multi-agent debate engine with an umpire.

Round flow:
  Round N  →  all debaters argue (see previous round's positions)
  Umpire   →  reads all positions WITHOUT attribution, asks one fresh question
               to force a new angle before the next round starts
  Round N+1 → all debaters must address the umpire's question + keep arguing
  ...
  Synthesis → each agent declares consensus / still disputes / changed mind
"""

import re
import queue
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from typing import Iterator, Any

from backend.agents import agent_factory
from backend.agents_catalog import enabled_agents, AGENT_ORDER
from backend.db import db
from backend.memory_client import memory_client
from backend.router import agent_router

# ── Prompts ───────────────────────────────────────────────────────────────────

_ROUND1 = """\
You are participating in a structured multi-agent debate. Multiple AI agents \
will argue about the following topic across {rounds} rounds.

Topic:
{prompt}

{context}

Give your honest, thorough assessment. Take clear positions — hedging is \
unhelpful here. Be specific: cite real risks, real opportunities, concrete examples.

IMPORTANT: Keep your response under 1200 characters. Be ruthlessly concise — \
every word must earn its place. Other agents will read your response and argue against it."""

_UMPIRE = """\
You are an impartial debate moderator. You have NO stake in the outcome and take NO sides.

The topic under debate:
{prompt}

{context}

Here is what has been argued so far (anonymised — you don't know who said what):
{anonymous_positions}

Your job: inject ONE sharp question or observation that will force the debaters \
to explore an angle they have NOT yet covered. Think about:
- An emerging technology or trend they ignored
- A second-order consequence nobody mentioned
- A stakeholder or market segment left out
- A historical precedent that challenges their assumptions
- A constraint (regulatory, ethical, financial) they glossed over
- A "what if the opposite is true?" provocation

Output ONLY the question or observation. No preamble, no explanation. \
1–3 sentences max. Make it count."""

_DEBATE = """\
This is round {round} of {rounds} in a structured multi-agent debate.

Original topic:
{prompt}

{context}

The other agents argued in the previous round:
{other_positions}

The debate moderator then asked:
❓ {umpire_question}

Your instructions this round:
1. Address the moderator's question directly — this is the new angle you must explore
2. Find at least 2 specific flaws in the other agents' reasoning from the previous round
3. Defend your own position with new evidence or reasoning
4. Acknowledge any point that genuinely updated your view (briefly)

Push back hard. Intellectual honesty matters more than being polite.

IMPORTANT: Keep your response under 1200 characters. Be ruthlessly concise."""

_SYNTHESIS = """\
This is the final synthesis after a {rounds}-round structured debate.

Original topic:
{prompt}

Complete debate history (including all umpire interventions):
{history}

Produce your final structured position using exactly these headings:

## CONSENSUS
Every point you now agree is correct — whether you raised it or another agent did. \
Be honest: if they convinced you, say so.

## MY POSITION
Points you still hold that other agents disputed. Give your single strongest argument for each.

## CHANGED MY MIND
Positions from Round 1 you've updated after the debate, and why. Write "None." if none.

No length limit — be thorough."""

_UMPIRE_SYNTHESIS = """\
You are the LeadAgent Umpire. You have just moderated a multi-round debate.

Original topic:
{prompt}

{context}

Here is the final consensus from each debater:
{individual_syntheses}

Your task: Produce a single, unified "FINAL CONSENSUS" report.
Identify where the agents reached agreement, where they still differ, and what the final verdict is.

Structure your response EXACTLY like this:

### 🏆 CONSENSUS REACHED
(Bulleted list of points agreed upon by all agents)

### ─── ● FINAL VERDICT
(A concise, authoritative summary of the debate outcome)
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

MARKER_ROUND = "__DEBATE_ROUND__:{round}"
MARKER_AGENT = "__DEBATE_AGENT__:{agent}"
MARKER_AGENT_END = "__DEBATE_AGENT_END__:"
MARKER_UMPIRE = "__DEBATE_UMPIRE__"
MARKER_UMPIRE_END = "__DEBATE_UMPIRE_END__"
MARKER_SYNTHESIS = "__DEBATE_SYNTHESIS__"
MARKER_DONE = "__DEBATE_DONE__"


def _fmt_history(
    history: list[list[str]], debaters: list[str], umpire_qs: list[str]
) -> str:
    lines = []
    for r_idx, round_resps in enumerate(history):
        lines.append(f"ROUND {r_idx + 1}")
        for d_idx, resp in enumerate(round_resps):
            lines.append(f"Agent {debaters[d_idx]}:\n{resp}\n")
        if r_idx < len(umpire_qs):
            lines.append(f"Moderator Question:\n{umpire_qs[r_idx]}\n")
    return "\n".join(lines)


def _anonymise_round(round_resps: list[str]) -> str:
    return "\n---\n".join([f"Position: {r}" for r in round_resps])


def _run_sync(agent_name: str, prompt: str, cwd: str, mode: str = "plan", status_q: queue.Queue = None) -> str:
    agent = agent_factory.get_agent(agent_name)
    chunks = []
    try:
        # simple=True: debate is prose-only, no MCP/tool overhead
        for chunk in agent.execute_stream(prompt, cwd, session_id="debate", simple=True, mode=mode):
            if chunk.strip().startswith(("__STATUS__:", "__PROGRESS__:")):
                if status_q is not None:
                    status_q.put(chunk)
                continue
            chunks.append(chunk)
    except Exception as e:
        print(f"[Debate _run_sync] {agent_name} error: {e}")
        return f"[Agent error: {e}]"

    return "".join(chunks)


# ── Main Engine ───────────────────────────────────────────────────────────────


def run_debate(
    prompt: str, rounds: int = 3, cwd: str = ".", agents: list[str] = None
) -> Iterator[str]:
    try:
        enabled = enabled_agents()
        all_available = [a for a in AGENT_ORDER if a in enabled]

        if agents:
            active_debaters = [a for a in agents if a in all_available]
        else:
            # Default: all enabled agents except ollama (too slow for real-time debate)
            active_debaters = [a for a in all_available if a != "ollama"]

        if not active_debaters:
            yield "Error: No agents available for debate.\n"
            return

        # ── Context Injection ──
        # Fetch relevant project knowledge, past Q&A, and file structures
        # session_id="debate" ensures we don't pollute the user's regular chat cache
        raw_context = agent_router.check_memory(prompt, session_id="debate")
        injected_context = ""
        if raw_context:
            injected_context = (
                "=== PROJECT CONTEXT (Found in Knowledge Graph) ===\n"
                f"{raw_context}\n"
                "=== END PROJECT CONTEXT ===\n"
            )
        
        # Use 'plan' mode for debate round execution
        mode = "plan"

        history: list[list[str]] = []
        umpire_questions: list[str] = []

        for r in range(1, rounds + 1):
            yield f"{MARKER_ROUND.format(round=r)}\n"

            # Build prompts for all debaters before spawning threads
            prompts: dict[str, str] = {}
            for agent_name in active_debaters:
                if r == 1:
                    prompts[agent_name] = _ROUND1.format(
                        rounds=rounds, 
                        prompt=prompt, 
                        context=injected_context
                    )
                else:
                    other_positions = []
                    for idx, other_name in enumerate(active_debaters):
                        if other_name != agent_name:
                            other_positions.append(
                                f"Agent {other_name}: {history[-1][idx]}"
                            )
                    prompts[agent_name] = _DEBATE.format(
                        round=r,
                        rounds=rounds,
                        prompt=prompt,
                        context=injected_context,
                        other_positions="\n\n".join(other_positions),
                        umpire_question=umpire_questions[-1],
                    )

            # Run all debaters in parallel
            responses: dict[str, str] = {}
            status_q: queue.Queue = queue.Queue()
            pool = ThreadPoolExecutor(max_workers=len(active_debaters))
            
            futures = {
                pool.submit(_run_sync, name, prompts[name], cwd, mode, status_q): name
                for name in active_debaters
            }
            
            active_futures = list(futures.keys())
            t_start = _time.perf_counter()
            timeout = 120  # seconds

            while active_futures:
                # 0. Check for overall timeout
                if _time.perf_counter() - t_start > timeout:
                    for fut in active_futures:
                        fut.cancel()
                    break

                # 1. Process all pending status updates from the queue
                while True:
                    try:
                        status_msg = status_q.get_nowait()
                        yield status_msg
                    except queue.Empty:
                        break
                
                # 2. Check for completed futures
                from concurrent.futures import wait, FIRST_COMPLETED
                done, _ = wait(active_futures, timeout=0.1, return_when=FIRST_COMPLETED)
                
                for fut in done:
                    name = futures[fut]
                    try:
                        res = fut.result()
                    except Exception as e:
                        res = f"[Agent error: {e}]"
                    
                    responses[name] = res
                    active_futures.remove(fut)
                    
                    # Emit the finished agent response immediately
                    yield f"{MARKER_AGENT.format(agent=name)}\n"
                    yield res
                    yield f"\n{MARKER_AGENT_END}\n"

            pool.shutdown(wait=False)

            # Fill in any agents that didn't complete (marked as timed out)
            for name in active_debaters:
                if name not in responses:
                    responses[name] = "[Agent timed out]"

            round_responses = [responses.get(name, "[Agent failed to respond]") for name in active_debaters]
            history.append(round_responses)

            # ── Umpire inter-round (skip after final round) ───────────────────────
            if r < rounds:
                yield f"{MARKER_UMPIRE}\n"

                outsiders = [a for a in all_available if a not in active_debaters]
                umpire_agent = (
                    outsiders[0]
                    if outsiders
                    else active_debaters[(r - 1) % len(active_debaters)]
                )

                anon = _anonymise_round(round_responses)
                umpire_prompt = _UMPIRE.format(
                    prompt=prompt, 
                    context=injected_context,
                    anonymous_positions=anon
                )
                try:
                    question = _run_sync(umpire_agent, umpire_prompt, cwd, mode)
                except Exception:
                    question = "What fundamental assumption in this debate hasn't been challenged yet?"

                umpire_questions.append(question)
                yield f"❓ {question}\n"
                yield f"{MARKER_UMPIRE_END}\n"

        # ── Synthesis ─────────────────────────────────────────────────────────────
        yield f"{MARKER_SYNTHESIS}\n"

        full_history = _fmt_history(history, active_debaters, umpire_questions)

        # Run synthesis in parallel too
        synth_prompts = {
            name: _SYNTHESIS.format(rounds=rounds, prompt=prompt, history=full_history)
            for name in active_debaters
        }
        synth_responses: dict[str, str] = {}
        status_q = queue.Queue()
        pool = ThreadPoolExecutor(max_workers=len(active_debaters))
        
        futures = {
            pool.submit(_run_sync, name, synth_prompts[name], cwd, mode, status_q): name
            for name in active_debaters
        }
        
        active_futures = list(futures.keys())
        t_start = _time.perf_counter()
        timeout = 120  # seconds

        while active_futures:
            # 0. Overall timeout
            if _time.perf_counter() - t_start > timeout:
                for fut in active_futures:
                    fut.cancel()
                break

            # Status updates
            while True:
                try:
                    status_msg = status_q.get_nowait()
                    yield status_msg
                except queue.Empty:
                    break
            
            # Results
            from concurrent.futures import wait, FIRST_COMPLETED
            done, _ = wait(active_futures, timeout=0.1, return_when=FIRST_COMPLETED)
            for fut in done:
                agent_name = futures[fut]
                try:
                    response = fut.result()
                except Exception as e:
                    response = f"[Agent error: {e}]"
                
                synth_responses[agent_name] = response
                active_futures.remove(fut)

                yield f"{MARKER_AGENT.format(agent=agent_name)}\n"

                # Flag errors to prevent poisoning the graph
                is_error = "[Agent error:" in response or "[Agent timed out]" in response

                # PERSISTENCE: Store synthesis in episodic memory and graph
                memory_client.store(
                    content=f"DEBATE CONSENSUS ({agent_name}) on '{prompt}':\n\n{response}",
                    metadata={"type": "debate_synthesis", "agent": agent_name, "topic": prompt, "error": is_error},
                    tier="episodic",
                )

                qid = db.add_question(f"[DEBATE] {prompt}", response, agent_name, error_sourced=is_error)

                try:
                    text = f"{prompt} {response}"
                    technical = set(
                        re.findall(
                            r"\b[A-Z][a-zA-Z]{2,}\b"
                            r"|\b\w+\.(?:py|js|go|ts|json|yaml)\b"
                            r"|\b[a-z]+_[a-z_]{2,}\b",
                            text,
                        )
                    )
                    entities = [t for t in technical if 3 < len(t) < 60][:50]
                    for name in entities:
                        db.add_entity(name, "extracted", "", error_sourced=is_error)
                        db.link_question_to_entity(qid, name)
                except Exception as e:
                    print(f"[Debate Persistence] extraction error: {e}")

                yield response
                yield f"\n{MARKER_AGENT_END}\n"

        pool.shutdown(wait=False)

        for name in active_debaters:
            if name not in synth_responses:
                synth_responses[name] = "[Agent timed out]"

        # ── Final Umpire Synthesis ──────────────────────────────────────────
        yield f"{MARKER_UMPIRE}\n"
        yield f"__STATUS__:Umpire generating unified consensus...\n"

        # Format individual syntheses for the umpire
        individual_blocks = []
        for name, resp in synth_responses.items():
            individual_blocks.append(f"AGENT {name.upper()} CONSENSUS:\n{resp}")
        
        individual_syntheses = "\n\n---\n\n".join(individual_blocks)

        umpire_synth_prompt = _UMPIRE_SYNTHESIS.format(
            prompt=prompt,
            context=injected_context,
            individual_syntheses=individual_syntheses
        )

        # Select umpire for final synthesis (Claude is best for this)
        outsiders = [a for a in all_available if a not in active_debaters]
        final_umpire = (
            "claude" if "claude" in all_available and "claude" not in active_debaters
            else outsiders[0] if outsiders
            else "claude" if "claude" in all_available
            else active_debaters[0]
        )

        try:
            final_consensus = _run_sync(final_umpire, umpire_synth_prompt, cwd, mode)
            yield final_consensus
        except Exception as e:
            yield f"\n[Error generating final consensus: {e}]\n"

        yield f"{MARKER_UMPIRE_END}\n"
        yield f"{MARKER_DONE}\n"

    except Exception as e:
        print(f"[Debate Error] Critical failure: {e}")
        yield f"\n❌ Debate Engine Error: {e}\n"
        yield f"{MARKER_DONE}\n"
