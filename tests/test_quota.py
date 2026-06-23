"""Tests for backend/quota.py — QuotaManager."""

import json
import os
import time

import pytest

from backend.quota import QuotaManager, AgentQuota, RESET_INTERVALS


@pytest.fixture
def qm(tmp_path):
    return QuotaManager(state_file=str(tmp_path / "quota.json"))


class TestInitialState:
    def test_all_agents_present(self, qm):
        assert set(qm.state.keys()) == set(RESET_INTERVALS.keys())

    def test_all_agents_not_exhausted(self, qm):
        for state in qm.state.values():
            assert not state.exhausted

    def test_loads_from_existing_file(self, tmp_path):
        state_file = tmp_path / "quota.json"
        data = {
            "claude": {"exhausted": True, "exhausted_at": time.time(), "limit_type": "daily"},
            "gemini": {"exhausted": False},
            "codex": {},
            "grok": {},
        }
        state_file.write_text(json.dumps(data))
        qm = QuotaManager(str(state_file))
        assert qm.state["claude"].exhausted is True
        assert qm.state["gemini"].exhausted is False


class TestSetExhausted:
    def test_marks_agent_exhausted(self, qm):
        qm.set_exhausted("claude", True, "daily")
        assert qm.state["claude"].exhausted is True
        assert qm.state["claude"].limit_type == "daily"

    def test_sets_reset_at(self, qm):
        t0 = time.time()
        qm.set_exhausted("claude", True, "daily")
        expected_interval = RESET_INTERVALS["claude"]["daily"]
        assert qm.state["claude"].reset_at == pytest.approx(t0 + expected_interval, abs=2)

    def test_clears_exhaustion(self, qm):
        qm.set_exhausted("claude", True)
        qm.set_exhausted("claude", False)
        assert qm.state["claude"].exhausted is False
        assert qm.state["claude"].reset_at is None
        assert qm.state["claude"].exhausted_at is None

    def test_weekly_limit_type(self, qm):
        qm.set_exhausted("gemini", True, "weekly")
        assert qm.state["gemini"].limit_type == "weekly"

    def test_unknown_agent_no_error(self, qm):
        qm.set_exhausted("unknown_agent", True)  # should not raise

    def test_persists_to_file(self, tmp_path):
        state_file = tmp_path / "quota.json"
        qm = QuotaManager(str(state_file))
        qm.set_exhausted("claude", True)
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["claude"]["exhausted"] is True


class TestSetResetFromCli:
    def test_sets_exact_reset_time(self, qm):
        t0 = time.time()
        qm.set_reset_from_cli("claude", 3600, "daily")
        assert qm.state["claude"].exhausted is True
        assert qm.state["claude"].reset_at == pytest.approx(t0 + 3600, abs=2)

    def test_sets_limit_type(self, qm):
        qm.set_reset_from_cli("claude", 7200, "weekly")
        assert qm.state["claude"].limit_type == "weekly"


class TestGetAvailableAgents:
    def test_all_available_when_fresh(self, qm):
        available = qm.get_available_agents()
        assert set(available) == set(RESET_INTERVALS.keys())

    def test_exhausted_agent_excluded(self, qm):
        qm.set_exhausted("claude", True)
        assert "claude" not in qm.get_available_agents()

    def test_auto_clears_expired_exhaustion(self, qm):
        qm.set_exhausted("claude", True)
        # Force reset_at to the past
        qm.state["claude"].reset_at = time.time() - 1
        available = qm.get_available_agents()
        assert "claude" in available
        assert not qm.state["claude"].exhausted


class TestResetSecondsRemaining:
    def test_returns_none_when_not_exhausted(self, qm):
        assert qm.reset_seconds_remaining("claude") is None

    def test_returns_approximate_remaining(self, qm):
        qm.set_exhausted("claude", True, "daily")
        remaining = qm.reset_seconds_remaining("claude")
        interval = RESET_INTERVALS["claude"]["daily"]
        assert remaining == pytest.approx(interval, abs=5)

    def test_returns_zero_when_past_reset(self, qm):
        qm.set_exhausted("claude", True)
        qm.state["claude"].reset_at = time.time() - 10
        assert qm.reset_seconds_remaining("claude") == 0

    def test_uses_exact_reset_at_when_set(self, qm):
        qm.set_reset_from_cli("claude", 1800)
        remaining = qm.reset_seconds_remaining("claude")
        assert remaining == pytest.approx(1800, abs=5)


class TestGetWaitStatus:
    def test_all_available_message(self, qm):
        assert qm.get_wait_status() == "All agents available."

    def test_shows_exhausted_agent(self, qm):
        qm.set_exhausted("claude", True, "daily")
        status = qm.get_wait_status()
        assert "claude" in status

    def test_shows_multiple_exhausted(self, qm):
        qm.set_exhausted("claude", True)
        qm.set_exhausted("gemini", True)
        status = qm.get_wait_status()
        assert "claude" in status
        assert "gemini" in status
