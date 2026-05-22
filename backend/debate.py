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

import shutil
import time
from typing import Iterator

from backend.agents import agent_factory
from backend.agents_catalog import enabled_agents, is_authenticated, is_installed
from backend.memory_client import memory_client
from backend.db import db

# ── Prompts ───────────────────────────────────────────────────────────────────

_ROUND1 = """\
You are participating in a structured multi-agent debate. Multiple AI agents \
will argue about the following topic across {rounds} rounds.

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

def _available_agents(requested: list[str] | None = None) -> list[str]:
    enabled = enabled_agents()
    pool = [
        a for a in enabled
        if is_installed(a) and is_authenticated(a) is not False
    ]
    if requested:
        pool = [a for a in requested if a in pool]
    return pool


def _run_sync(agent_name: str, prompt: str, cwd: str) -> str:
    retries = 3
    backoff = 2
    for attempt in range(retries):
        agent = agent_factory.get_agent(agent_name)
        chunks: list[str] = []
        try:
            for chunk in agent.execute_stream(prompt, cwd, simple=True):
                if chunk.startswith("__TIMING__") or chunk.startswith("Using CLI Agent:"):
                    continue
                chunks.append(chunk)
            return "".join(chunks).strip()
        except Exception as e:
            if "AGENT_TRANSIENT_ERROR" in str(e) and attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    return ""


def _pick_umpire(all_agents: list[str], debaters: list[str]) -> str:
    """
    Prefer an agent NOT in the debate pool (true outsider).
    If all available agents are debating, rotate through debaters — the one
    whose turn it is acts as umpire for that inter-round gap (they won't see
    which position is theirs since we anonymise).
    """
    outsiders = [a for a in all_agents if a not in debaters]
    if outsiders:
        return outsiders[0]
    # Rotate through debaters; use a simple cycle via round number — caller passes index
    return debaters[0]  # caller overrides with index


def _anonymise_round(round_resp: dict[str, str]) -> str:
    """Return all positions without agent names — umpire stays impartial."""
    parts = []
    for i, (_, response) in enumerate(round_resp.items(), 1):
        parts.append(f"[Debater {i}]:\n{response}")
    return "\n\n".join(parts)


def _fmt_history(history: list[dict[str, str]], agents: list[str], umpire_questions: list[str]) -> str:
    parts: list[str] = []
    for i, round_resp in enumerate(history):
        parts.append(f"=== ROUND {i + 1} ===")
        for ag in agents:
            if ag in round_resp:
                parts.append(f"[{ag.upper()}]:\n{round_resp[ag]}")
        if i < len(umpire_questions):
            parts.append(f"--- UMPIRE AFTER ROUND {i + 1} ---\n❓ {umpire_questions[i]}")
    return "\n\n".join(parts)


# ── Stream markers (parsed by Go CLI) ────────────────────────────────────────

MARKER_ROUND      = "__DEBATE_ROUND__:"
MARKER_AGENT      = "__DEBATE_AGENT__:"
MARKER_AGENT_END  = "__DEBATE_AGENT_END__:"
MARKER_UMPIRE     = "__DEBATE_UMPIRE__"
MARKER_UMPIRE_END = "__DEBATE_UMPIRE_END__"
MARKER_DROPPED    = "__DEBATE_DROPPED__:"   # payload: agent name
MARKER_SYNTHESIS  = "__DEBATE_SYNTHESIS__"
MARKER_DONE       = "__DEBATE_DONE__"


# ── Main engine ───────────────────────────────────────────────────────────────

def run_debate(
    prompt: str,
    rounds: int,
    cwd: str,
    agents: list[str] | None = None,
) -> Iterator[str]:
    """
    Yields a stream of text + markers.

    Marker protocol (one per line):
      __DEBATE_ROUND__:<n>
      __DEBATE_AGENT__:<name>
      __DEBATE_AGENT_END__:<name>
      __DEBATE_UMPIRE__
      __DEBATE_UMPIRE_END__
      __DEBATE_SYNTHESIS__
      __DEBATE_DONE__
    """
    all_available = _available_agents()
    debaters = _available_agents(agents)

    if not debaters:
        yield "❌ No agents available for debate.\n"
        return
    if len(debaters) == 1:
        yield f"⚠️  Only one agent available ({debaters[0]}) — debate needs at least 2.\n"
        return

    rounds = max(1, min(rounds, 5))
    history: list[dict[str, str]] = []
    umpire_questions: list[str] = []
    active_debaters = list(debaters)

    for r in range(1, rounds + 1):
        yield f"{MARKER_ROUND}{r}\n"

        round_responses: dict[str, str] = {}
        umpire_q = umpire_questions[-1] if umpire_questions else None

        for agent_name in list(active_debaters):
            yield f"{MARKER_AGENT}{agent_name}\n"

            if r == 1:
                agent_prompt = _ROUND1.format(rounds=rounds) + f"\n\nTopic: {prompt}"
            else:
                prev = history[-1]
                other_blocks = "\n\n".join(
                    f"--- {a.upper()} argued:\n{prev[a]}"
                    for a in active_debaters if a != agent_name and a in prev
                )
                agent_prompt = _DEBATE.format(
                    round=r,
                    rounds=rounds,
                    prompt=prompt,
                    other_positions=other_blocks,
                    umpire_question=umpire_q or "No umpire question this round.",
                )

            try:
                response = _run_sync(agent_name, agent_prompt, cwd)
                if not response.strip():
                    yield f"⚠️  {agent_name} returned an empty response — skipping this round.\n"
                else:
                    yield response
                    round_responses[agent_name] = response
            except Exception as e:
                yield f"❌ Error running {agent_name}: {e}\n"
            finally:
                yield f"\n{MARKER_AGENT_END}{agent_name}\n"

        if len(active_debaters) < 2:
            if round_responses:
                history.append(round_responses)
            yield f"⚠️  Only {active_debaters[0]} remains — ending debate early, proceeding to synthesis.\n"
            break

        history.append(round_responses)

        # ── Umpire inter-round (skip after final round) ───────────────────────
        if r < rounds:
            yield f"{MARKER_UMPIRE}\n"

            outsiders = [a for a in all_available if a not in active_debaters]
            umpire_agent = outsiders[0] if outsiders else active_debaters[(r - 1) % len(active_debaters)]

            anon = _anonymise_round(round_responses)
            umpire_prompt = _UMPIRE.format(prompt=prompt, anonymous_positions=anon)
            try:
                question = _run_sync(umpire_agent, umpire_prompt, cwd)
            except Exception:
                question = "What fundamental assumption in this debate hasn't been challenged yet?"
            umpire_questions.append(question)
            yield question
            yield f"\n{MARKER_UMPIRE_END}\n"

    # ── Synthesis ─────────────────────────────────────────────────────────────
    yield f"{MARKER_SYNTHESIS}\n"

    full_history = _fmt_history(history, debaters, umpire_questions)

    synthesis_agents = active_debaters or list({ag for r in history for ag in r})
    for agent_name in synthesis_agents:
        yield f"{MARKER_AGENT}{agent_name}\n"
        synth_prompt = _SYNTHESIS.format(rounds=rounds, prompt=prompt, history=full_history)
        response = _run_sync(agent_name, synth_prompt, cwd)
        
        # PERSISTENCE: Store synthesis in episodic memory and graph
        memory_client.store(
            content=f"DEBATE CONSENSUS ({agent_name}) on '{prompt}':\n\n{response}",
            metadata={"type": "debate_synthesis", "agent": agent_name, "topic": prompt},
            tier="episodic"
        )
        # Add to graph as a high-confidence past discussion
        db.add_question(f"[DEBATE] {prompt}", response, agent_name)

        yield response
        yield f"\n{MARKER_AGENT_END}{agent_name}\n"

    yield f"{MARKER_DONE}\n"
