"""Tests for backend/debate.py — _fmt_history, _anonymise_round, _run_sync, run_debate."""

from unittest.mock import patch, MagicMock

import pytest

from backend import debate
from backend.debate import (
    _fmt_history,
    _anonymise_round,
    _run_sync,
    run_debate,
    MARKER_ROUND,
    MARKER_AGENT,
    MARKER_AGENT_END,
    MARKER_UMPIRE,
    MARKER_UMPIRE_END,
    MARKER_SYNTHESIS,
    MARKER_DONE,
)


# ── _fmt_history ─────────────────────────────────────────────────────────────

class TestFmtHistory:
    def test_empty_history(self):
        assert _fmt_history([], [], []) == ""

    def test_single_round_no_umpire(self):
        out = _fmt_history(
            [["argA", "argB"]],
            ["claude", "gemini"],
            [],
        )
        assert "ROUND 1" in out
        assert "Agent claude" in out
        assert "argA" in out
        assert "Agent gemini" in out
        assert "argB" in out
        assert "Moderator" not in out

    def test_round_with_umpire_question(self):
        out = _fmt_history(
            [["a", "b"], ["c", "d"]],
            ["claude", "gemini"],
            ["What about X?"],
        )
        assert "ROUND 1" in out
        assert "ROUND 2" in out
        assert "Moderator Question" in out
        assert "What about X?" in out


# ── _anonymise_round ─────────────────────────────────────────────────────────

class TestAnonymiseRound:
    def test_strips_agent_names(self):
        out = _anonymise_round(["claude says X", "gemini says Y"])
        # Both prefixed with "Position:" — no agent attribution
        assert out.count("Position:") == 2
        # Content preserved, but no "Agent" labels
        assert "Agent claude" not in out
        assert "Agent gemini" not in out

    def test_separator_between_positions(self):
        out = _anonymise_round(["a", "b", "c"])
        assert "\n---\n" in out
        assert out.count("---") >= 2


# ── _run_sync ────────────────────────────────────────────────────────────────

class TestRunSync:
    def test_concatenates_chunks(self):
        fake_agent = MagicMock()
        fake_agent.execute_stream.return_value = iter(["Hello ", "world"])
        with patch("backend.debate.agent_factory.get_agent", return_value=fake_agent):
            result = _run_sync("claude", "prompt", ".")
        assert result == "Hello world"

    def test_uses_session_id_debate_and_simple(self):
        fake_agent = MagicMock()
        fake_agent.execute_stream.return_value = iter([])
        with patch("backend.debate.agent_factory.get_agent", return_value=fake_agent):
            _run_sync("claude", "p", "/tmp")
        kwargs = fake_agent.execute_stream.call_args.kwargs
        assert kwargs.get("session_id") == "debate"
        assert kwargs.get("simple") is True


# ── run_debate ───────────────────────────────────────────────────────────────

class TestRunDebate:
    def test_error_when_no_agents_available(self):
        with patch("backend.debate.enabled_agents", return_value=set()):
            chunks = list(run_debate("topic", rounds=1, agents=None))
        joined = "".join(chunks)
        assert "Error" in joined
        assert "No agents available" in joined

    def test_filters_requested_agents_to_available(self):
        # Requested agents include one unavailable; only available used
        with (
            patch("backend.debate.enabled_agents", return_value={"claude"}),
            patch("backend.debate._run_sync", return_value="response"),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            chunks = list(run_debate("topic", rounds=1, agents=["claude", "skynet"]))
        joined = "".join(chunks)
        # claude appears in marker, skynet must NOT
        assert "claude" in joined
        assert "skynet" not in joined

    def test_default_picks_first_three_available(self):
        with (
            patch("backend.debate.enabled_agents", return_value={"claude", "gemini", "codex", "grok"}),
            patch("backend.debate._run_sync", return_value="resp"),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            chunks = list(run_debate("topic", rounds=1, agents=None))
        joined = "".join(chunks)
        # Should have round + synthesis markers
        assert MARKER_ROUND.format(round=1) in joined
        assert MARKER_SYNTHESIS in joined
        assert MARKER_DONE in joined

    def test_round_and_synthesis_markers_present(self):
        with (
            patch("backend.debate.enabled_agents", return_value={"claude"}),
            patch("backend.debate._run_sync", return_value="resp"),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            chunks = list(run_debate("topic", rounds=1, agents=["claude"]))
        joined = "".join(chunks)
        assert MARKER_AGENT.format(agent="claude") in joined
        assert MARKER_AGENT_END in joined
        assert MARKER_DONE in joined

    def test_umpire_runs_between_rounds(self):
        calls = []

        def fake_run(agent, prompt, cwd, mode="plan", status_q=None):
            calls.append((agent, prompt[:30]))
            return "resp"

        with (
            patch("backend.debate.enabled_agents", return_value={"claude", "gemini", "codex"}),
            patch("backend.debate._run_sync", side_effect=fake_run),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            chunks = list(run_debate("topic", rounds=2, agents=["claude", "gemini"]))
        joined = "".join(chunks)
        # Umpire should be invoked between rounds
        assert MARKER_UMPIRE in joined
        assert MARKER_UMPIRE_END in joined

    def test_umpire_uses_outsider_when_available(self):
        chosen = []

        def fake_run(agent, prompt, cwd, mode="plan", status_q=None):
            chosen.append(agent)
            return "resp"

        with (
            patch("backend.debate.enabled_agents", return_value={"claude", "gemini", "codex"}),
            patch("backend.debate._run_sync", side_effect=fake_run),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            list(run_debate("topic", rounds=2, agents=["claude", "gemini"]))

        # codex (outsider) should be invoked at least once as umpire
        assert "codex" in chosen

    def test_umpire_fallback_when_no_outsiders(self):
        # Only debaters available → umpire must come from debaters
        invoked = []

        def fake_run(agent, prompt, cwd, mode="plan", status_q=None):
            invoked.append(agent)
            return "resp"

        with (
            patch("backend.debate.enabled_agents", return_value={"claude", "gemini"}),
            patch("backend.debate._run_sync", side_effect=fake_run),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            list(run_debate("topic", rounds=2, agents=["claude", "gemini"]))
        # No outsiders, but no crash; both debaters get called
        assert "claude" in invoked
        assert "gemini" in invoked

    def test_umpire_fallback_question_on_exception(self):
        call_count = {"n": 0}

        def fake_run(agent, prompt, cwd, mode="plan", status_q=None):
            call_count["n"] += 1
            # Cause umpire to fail (umpire runs after first round of debaters)
            if "moderator" in prompt.lower() or "impartial" in prompt.lower():
                raise Exception("umpire failed")
            return "resp"

        with (
            patch("backend.debate.enabled_agents", return_value={"claude", "gemini", "codex"}),
            patch("backend.debate._run_sync", side_effect=fake_run),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            chunks = list(run_debate("topic", rounds=2, agents=["claude", "gemini"]))
        joined = "".join(chunks)
        # Hardcoded fallback question
        assert "fundamental assumption" in joined

    def test_agent_error_captured_in_response(self):
        def fake_run(agent, prompt, cwd, mode="plan", status_q=None):
            raise RuntimeError("agent crash")

        with (
            patch("backend.debate.enabled_agents", return_value={"claude"}),
            patch("backend.debate._run_sync", side_effect=fake_run),
            patch("backend.debate.memory_client.store"),
            patch("backend.debate.db.add_question", return_value="q1"),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            chunks = list(run_debate("topic", rounds=1, agents=["claude"]))
        joined = "".join(chunks)
        assert "Agent error" in joined
        assert "agent crash" in joined

    def test_synthesis_persists_to_memory_and_db(self):
        store_calls = []
        question_calls = []

        with (
            patch("backend.debate.enabled_agents", return_value={"claude"}),
            patch("backend.debate._run_sync", return_value="synthesised"),
            patch("backend.debate.memory_client.store", side_effect=lambda **kw: store_calls.append(kw)),
            patch(
                "backend.debate.db.add_question",
                side_effect=lambda *a, **kw: question_calls.append((a, kw)) or "qid-1",
            ),
            patch("backend.debate.db.add_entity"),
            patch("backend.debate.db.link_question_to_entity"),
        ):
            list(run_debate("My Topic", rounds=1, agents=["claude"]))

        # Memory store called for synthesis
        assert any(c.get("tier") == "episodic" for c in store_calls)
        # Question stored with DEBATE prefix
        assert any("[DEBATE]" in args[0] for args, _ in question_calls)
        assert any(kwargs.get("error_sourced") is False for _, kwargs in question_calls)
