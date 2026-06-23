"""Tests for backend/permissions.py — PermissionBroker."""

import threading
import time
import os

import pytest

from backend.permissions import PermissionBroker


@pytest.fixture
def broker():
    return PermissionBroker()


# ── create ────────────────────────────────────────────────────────────────────

class TestCreate:
    def test_returns_request_with_id(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        assert pr.id
        assert pr.session_id == "s1"
        assert pr.agent == "claude"
        assert pr.tool_name == "Bash"
        assert pr.input == {"command": "ls"}

    def test_pending_after_create(self, broker):
        pr = broker.create("s1", "claude", "Bash", {})
        assert broker.get(pr.id) is pr

    def test_delivered_to_session_queue(self, broker):
        q = broker.session_queue("s1")
        pr = broker.create("s1", "claude", "Bash", {})
        assert q.get(timeout=1) is pr

    def test_multiple_requests_queue_in_order(self, broker):
        q = broker.session_queue("s2")
        prs = [broker.create("s2", "claude", f"Tool{i}", {}) for i in range(5)]
        received = [q.get(timeout=1) for _ in range(5)]
        assert [r.id for r in received] == [p.id for p in prs]

    def test_different_sessions_have_separate_queues(self, broker):
        qa = broker.session_queue("a")
        qb = broker.session_queue("b")
        broker.create("a", "claude", "Bash", {})
        broker.create("b", "claude", "Bash", {})
        assert qa.qsize() == 1
        assert qb.qsize() == 1


# ── get ───────────────────────────────────────────────────────────────────────

class TestGet:
    def test_returns_none_for_unknown_id(self, broker):
        assert broker.get("nonexistent") is None

    def test_returns_request_after_create(self, broker):
        pr = broker.create("s1", "claude", "Bash", {})
        assert broker.get(pr.id) is pr


# ── wait ──────────────────────────────────────────────────────────────────────

class TestWait:
    def test_returns_decision_after_decide(self, broker):
        pr = broker.create("s1", "claude", "Bash", {})
        threading.Thread(
            target=lambda: (time.sleep(0.05), broker.decide(pr.id, "allow", "once")),
            daemon=True,
        ).start()
        decision = broker.wait(pr.id, timeout=2.0)
        assert decision["behavior"] == "allow"
        assert decision["scope"] == "once"

    def test_returns_none_for_unknown_id(self, broker):
        assert broker.wait("nonexistent", timeout=0.1) is None

    def test_times_out_if_no_decision(self, broker):
        pr = broker.create("s1", "claude", "Bash", {})
        t0 = time.time()
        result = broker.wait(pr.id, timeout=0.15)
        elapsed = time.time() - t0
        assert result is None
        assert elapsed < 1.0

    def test_decision_includes_updated_input(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        broker.decide(pr.id, "allow", "once", updated_input={"command": "ls -la"})
        decision = broker.wait(pr.id, timeout=1.0)
        assert decision["updatedInput"] == {"command": "ls -la"}

    def test_decision_falls_back_to_original_input(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        broker.decide(pr.id, "allow", "once")
        decision = broker.wait(pr.id, timeout=1.0)
        assert decision["updatedInput"] == {"command": "ls"}


# ── decide ────────────────────────────────────────────────────────────────────

class TestDecide:
    def test_returns_true_on_success(self, broker):
        pr = broker.create("s1", "claude", "Bash", {})
        assert broker.decide(pr.id, "allow") is True

    def test_returns_false_for_unknown_id(self, broker):
        assert broker.decide("nonexistent", "allow") is False

    def test_deny_decision_fields(self, broker):
        pr = broker.create("s1", "claude", "Bash", {})
        broker.decide(pr.id, "deny", "once", message="Not allowed")
        decision = broker.wait(pr.id, timeout=1.0)
        assert decision["behavior"] == "deny"
        assert decision["message"] == "Not allowed"

    def test_stop_decision(self, broker):
        pr = broker.create("s1", "claude", "Bash", {})
        broker.decide(pr.id, "stop")
        decision = broker.wait(pr.id, timeout=1.0)
        assert decision["behavior"] == "stop"


# ── allow-for-session cache ───────────────────────────────────────────────────

class TestAllowCache:
    def test_allow_session_populates_cache(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        broker.decide(pr.id, "allow", "session")
        broker.wait(pr.id, timeout=1.0)
        assert broker.is_allowed("s1", "claude", "Bash", {"command": "ls"})

    def test_allow_once_does_not_populate_cache(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        broker.decide(pr.id, "allow", "once")
        broker.wait(pr.id, timeout=1.0)
        assert not broker.is_allowed("s1", "claude", "Bash", {"command": "ls"})

    def test_deny_does_not_populate_cache(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        broker.decide(pr.id, "deny", "session")
        broker.wait(pr.id, timeout=1.0)
        assert not broker.is_allowed("s1", "claude", "Bash", {"command": "ls"})

    def test_cache_is_session_scoped(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        broker.decide(pr.id, "allow", "session")
        broker.wait(pr.id, timeout=1.0)
        assert not broker.is_allowed("s2", "claude", "Bash", {"command": "ls"})

    def test_cache_is_tool_scoped(self, broker):
        pr = broker.create("s1", "claude", "Bash", {"command": "ls"})
        broker.decide(pr.id, "allow", "session")
        broker.wait(pr.id, timeout=1.0)
        assert not broker.is_allowed("s1", "claude", "Read", {"command": "ls"})

    def test_remember_allow_directly(self, broker):
        broker.remember_allow("s1", "claude", "Bash", {"command": "ls"})
        assert broker.is_allowed("s1", "claude", "Bash", {"command": "ls"})


# ── session-wide allow ────────────────────────────────────────────────────────

class TestBroadSessionAllow:
    def test_allow_session_applies_to_different_fingerprints(self, broker):
        # 1. Ask for 'sed', allow for session
        pr = broker.create("s1", "claude", "Bash", {"command": "sed ..."})
        broker.decide(pr.id, "allow", "session")
        broker.wait(pr.id, timeout=1.0)

        # 2. Check 'ls' — should be allowed now!
        assert broker.is_allowed("s1", "claude", "Bash", {"command": "ls -la"})

    def test_allow_session_applies_to_different_agents(self, broker):
        # 1. Claude asks for 'sed', allow for session
        pr = broker.create("s1", "claude", "Bash", {"command": "sed ..."})
        broker.decide(pr.id, "allow", "session")
        broker.wait(pr.id, timeout=1.0)

        # 2. Grok asks for 'sed' (or anything) — should be allowed now!
        assert broker.is_allowed("s1", "grok", "Bash", {"command": "sed ..."})
        assert broker.is_allowed("s1", "grok", "Bash", {"command": "ls"})

    def test_high_risk_commands_always_prompt(self, broker):
        # 1. Allow Bash for the session
        broker.remember_allow("s1", "claude", "Bash", {"command": "ls"}, scope="session")
        assert broker.is_allowed("s1", "claude", "Bash", {"command": "ls"})

        # 2. But 'sudo' and 'rm' should STILL return False (prompt required)
        assert not broker.is_allowed("s1", "claude", "Bash", {"command": "sudo apt update"})
        assert not broker.is_allowed("s1", "claude", "Bash", {"command": "rm -rf /"})
        assert not broker.is_allowed("s1", "claude", "Bash", {"command": "chmod +x script.sh"})
        
        # 3. Bare pip install should also return False
        assert not broker.is_allowed("s1", "claude", "Bash", {"command": "pip3 install requests"})
        
        # 4. Pip install WITH venv path should return True
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        venv_pip = os.path.join(project_root, "leadagent", "bin", "pip3")
        assert broker.is_allowed("s1", "claude", "Bash", {"command": f"{venv_pip} install requests"})


# ── fingerprint ───────────────────────────────────────────────────────────────

class TestFingerprint:
    def test_bash_commands_same_binary_match(self, broker):
        fp1 = broker._fingerprint("Bash", {"command": "git status"})
        fp2 = broker._fingerprint("Bash", {"command": "git log --oneline"})
        assert fp1 == fp2  # both start with "git"

    def test_bash_different_binary_no_match(self, broker):
        fp1 = broker._fingerprint("Bash", {"command": "ls -la"})
        fp2 = broker._fingerprint("Bash", {"command": "rm -rf /tmp/x"})
        assert fp1 != fp2

    def test_file_path_fingerprint_by_directory(self, broker):
        fp1 = broker._fingerprint("Read", {"file_path": "/tmp/foo/a.py"})
        fp2 = broker._fingerprint("Read", {"file_path": "/tmp/foo/b.py"})
        assert fp1 == fp2

    def test_empty_input_fingerprint(self, broker):
        fp = broker._fingerprint("SomeTool", {})
        assert isinstance(fp, str)

    def test_non_dict_input_fingerprint(self, broker):
        fp = broker._fingerprint("Bash", "not a dict")
        assert fp == ""


# ── thread safety ─────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_creates_all_distinct(self, broker):
        ids = []
        lock = threading.Lock()

        def _create():
            pr = broker.create("s1", "claude", "Bash", {})
            with lock:
                ids.append(pr.id)

        threads = [threading.Thread(target=_create) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == 50
        assert len(set(ids)) == 50  # all unique
