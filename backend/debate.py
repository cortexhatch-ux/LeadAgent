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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from backend.agents import agent_factory
from backend.agents_catalog import enabled_agents
from backend.db import db
from backend.memory_client import memory_client

# ── Prompts ───────────────────────────────────────────────────────────────────

_ROUND1 = """\
You are participating in a structured multi-agent debate. Multiple AI agents \
will argue about the following topic across {rounds} rounds.

Topic:
{prompt}

Give your honest, thorough assessment. Take clear positions — hedging is \
unhelpful here. Be specific: cite real risks, real opportunities, concrete examples.

IMPORTANT: Keep your response under 1200 characters. Be ruthlessly concise — \
every word must earn its place. Other agents will read your response and argue against it."""

_UMPIRE = """\
You are an impartial debate moderator. You have NO stake in the outcome and take NO sides.

The topic under debate:
{prompt}

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


def _run_sync(agent_name: str, prompt: str, cwd: str) -> str:
    agent = agent_factory.get_agent(agent_name)
    chunks = []
    # session_id=debate avoids cluttering main context cache
    for chunk in agent.execute_stream(prompt, cwd, session_id="debate", simple=True):
        chunks.append(chunk)
    return "".join(chunks)


# ── Main Engine ───────────────────────────────────────────────────────────────


def run_debate(
    prompt: str, rounds: int = 3, cwd: str = ".", agents: list[str] = None
) -> Iterator[str]:
    all_available = list(enabled_agents())
    if agents:
        active_debaters = [a for a in agents if a in all_available]
    else:
        # Default: take first 3 enabled agents
        active_debaters = all_available[:3]

    if not active_debaters:
        yield "Error: No agents available for debate.\n"
        return

    history: list[list[str]] = []
    umpire_questions: list[str] = []

    for r in range(1, rounds + 1):
        yield f"{MARKER_ROUND.format(round=r)}\n"

        # Build prompts for all debaters before spawning threads
        prompts: dict[str, str] = {}
        for agent_name in active_debaters:
            if r == 1:
                prompts[agent_name] = _ROUND1.format(rounds=rounds, prompt=prompt)
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
                    other_positions="\n\n".join(other_positions),
                    umpire_question=umpire_questions[-1],
                )

        # Run all debaters in parallel
        responses: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(active_debaters)) as pool:
            futures = {
                pool.submit(_run_sync, name, prompts[name], cwd): name
                for name in active_debaters
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    responses[name] = fut.result()
                except Exception as e:
                    responses[name] = f"[Agent error: {e}]"

        # Emit in stable order so the UI is consistent round-to-round
        round_responses = []
        for agent_name in active_debaters:
            yield f"{MARKER_AGENT.format(agent=agent_name)}\n"
            yield responses[agent_name]
            yield f"\n{MARKER_AGENT_END}\n"
            round_responses.append(responses[agent_name])

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
            umpire_prompt = _UMPIRE.format(prompt=prompt, anonymous_positions=anon)
            try:
                question = _run_sync(umpire_agent, umpire_prompt, cwd)
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
    with ThreadPoolExecutor(max_workers=len(active_debaters)) as pool:
        futures = {
            pool.submit(_run_sync, name, synth_prompts[name], cwd): name
            for name in active_debaters
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                synth_responses[name] = fut.result()
            except Exception as e:
                synth_responses[name] = f"[Agent error: {e}]"

    for agent_name in active_debaters:
        response = synth_responses[agent_name]
        yield f"{MARKER_AGENT.format(agent=agent_name)}\n"

        # PERSISTENCE: Store synthesis in episodic memory and graph
        memory_client.store(
            content=f"DEBATE CONSENSUS ({agent_name}) on '{prompt}':\n\n{response}",
            metadata={"type": "debate_synthesis", "agent": agent_name, "topic": prompt},
            tier="episodic",
        )

        qid = db.add_question(f"[DEBATE] {prompt}", response, agent_name)

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
                db.add_entity(name, "extracted", "")
                db.link_question_to_entity(qid, name)
        except Exception as e:
            print(f"[Debate Persistence] extraction error: {e}")

        yield response
        yield f"\n{MARKER_AGENT_END}\n"

    yield f"{MARKER_DONE}\n"
