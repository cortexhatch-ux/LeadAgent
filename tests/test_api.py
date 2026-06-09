"""Tests for backend/main.py — FastAPI endpoints via TestClient."""

import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.permissions import PermissionBroker


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fresh_broker(monkeypatch):
    """Swap the global broker for a fresh instance per test."""
    b = PermissionBroker()
    monkeypatch.setattr("backend.main.broker", b)
    return b


# ── GET / ─────────────────────────────────────────────────────────────────────

class TestRoot:
    def test_returns_running_status(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "running" in resp.json()["status"].lower()


# ── GET /health ───────────────────────────────────────────────────────────────

class TestHealth:
    def test_returns_status_field(self, client):
        with patch("backend.main.db.query_all", return_value=[[0]]):
            resp = client.get("/health")
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_returns_uptime(self, client):
        with patch("backend.main.db.query_all", return_value=[[0]]):
            resp = client.get("/health")
        assert resp.json()["uptime_seconds"] >= 0


# ── GET /roles ────────────────────────────────────────────────────────────────

class TestRoles:
    def test_returns_dict(self, client):
        resp = client.get("/roles")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_contains_general_role(self, client):
        resp = client.get("/roles")
        assert "general" in resp.json()


# ── POST /permission/_request ─────────────────────────────────────────────────

class TestPermissionRequest:
    def test_creates_request_returns_id(self, client, fresh_broker):
        resp = client.post("/permission/_request", json={
            "session_id": "s1", "agent": "claude",
            "tool_name": "Bash", "input": {"command": "ls"},
        })
        assert resp.status_code == 200
        assert "id" in resp.json()
        assert resp.json()["id"]

    def test_request_appears_in_broker(self, client, fresh_broker):
        resp = client.post("/permission/_request", json={
            "session_id": "s1", "agent": "claude", "tool_name": "Bash", "input": {},
        })
        req_id = resp.json()["id"]
        assert fresh_broker.get(req_id) is not None

    def test_request_delivered_to_session_queue(self, client, fresh_broker):
        q = fresh_broker.session_queue("s1")
        client.post("/permission/_request", json={
            "session_id": "s1", "agent": "claude", "tool_name": "Read", "input": {},
        })
        item = q.get(timeout=1)
        assert item.tool_name == "Read"


# ── POST /permission/{id}/decide ──────────────────────────────────────────────

class TestPermissionDecide:
    def test_allow_once(self, client, fresh_broker):
        pr = fresh_broker.create("s1", "claude", "Bash", {})
        resp = client.post(f"/permission/{pr.id}/decide", json={
            "behavior": "allow", "scope": "once",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_deny(self, client, fresh_broker):
        pr = fresh_broker.create("s1", "claude", "Bash", {})
        resp = client.post(f"/permission/{pr.id}/decide", json={"behavior": "deny"})
        assert resp.status_code == 200

    def test_unknown_id_returns_404(self, client, fresh_broker):
        resp = client.post("/permission/nonexistent/decide", json={"behavior": "allow"})
        assert resp.status_code == 404

    def test_decision_unblocks_wait(self, client, fresh_broker):
        import threading
        pr = fresh_broker.create("s1", "claude", "Bash", {})

        result = {}
        def _wait():
            result["decision"] = fresh_broker.wait(pr.id, timeout=5.0)

        t = threading.Thread(target=_wait)
        t.start()

        client.post(f"/permission/{pr.id}/decide", json={
            "behavior": "allow", "scope": "session",
        })
        t.join(timeout=3)
        assert result["decision"]["behavior"] == "allow"


# ── GET /permission/{id}/wait ─────────────────────────────────────────────────

class TestPermissionWait:
    def test_unknown_id_returns_404(self, client, fresh_broker):
        resp = client.get("/permission/nonexistent/wait")
        assert resp.status_code == 404

    def test_returns_decision_after_decide(self, client, fresh_broker):
        import threading, time
        pr = fresh_broker.create("s1", "claude", "Bash", {})

        def _decide():
            time.sleep(0.1)
            fresh_broker.decide(pr.id, "allow", "once")

        threading.Thread(target=_decide, daemon=True).start()
        resp = client.get(f"/permission/{pr.id}/wait")
        assert resp.status_code == 200
        assert resp.json()["behavior"] == "allow"


# ── POST /session/clear ───────────────────────────────────────────────────────

class TestSessionClear:
    def test_clears_session(self, client):
        resp = client.post("/session/clear?session_id=test-session")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "test-session"


# ── POST /chat ────────────────────────────────────────────────────────────────

class TestChat:
    def _mock_route_multi(self, agent_name="claude", mode="plan"):
        return patch(
            "backend.main.agent_router.route_multi",
            return_value=([agent_name], {"mode": mode}),
        )

    def _mock_memory(self):
        return patch("backend.main.agent_router.check_memory", return_value=None)

    def _mock_agent(self, chunks):
        mock_agent = MagicMock()
        mock_agent.execute_stream.return_value = iter(chunks)
        return patch("backend.main.agent_factory.get_agent", return_value=mock_agent)

    def test_returns_streaming_response(self, client):
        with (
            self._mock_memory(),
            self._mock_route_multi(),
            self._mock_agent(["Hello world\n"]),
            patch("backend.main.memory_client.store"),
            patch("backend.main.agent_router.learn_from_prompt"),
        ):
            resp = client.post("/chat", json={"prompt": "hi", "session_id": "s1"})
        assert resp.status_code == 200
        assert "Hello world" in resp.text

    def test_no_agents_streams_error_message(self, client):
        # When no agents are available the endpoint streams a 200 with an error message
        with (
            self._mock_memory(),
            patch("backend.main.agent_router.route_multi", return_value=(["none"], {})),
        ):
            resp = client.post("/chat", json={"prompt": "hi", "session_id": "s1"})
        assert resp.status_code == 200
        assert "No agents available" in resp.text

    def test_includes_timing_in_response(self, client):
        with (
            self._mock_memory(),
            self._mock_route_multi(),
            self._mock_agent(["response chunk\n"]),
            patch("backend.main.memory_client.store"),
            patch("backend.main.agent_router.learn_from_prompt"),
        ):
            resp = client.post("/chat", json={"prompt": "hi", "session_id": "s1"})
        assert "__TIMING__:" in resp.text

    def test_status_markers_in_response(self, client):
        with (
            self._mock_memory(),
            self._mock_route_multi(),
            self._mock_agent(["ok\n"]),
            patch("backend.main.memory_client.store"),
            patch("backend.main.agent_router.learn_from_prompt"),
        ):
            resp = client.post("/chat", json={"prompt": "hi", "session_id": "s1"})
        assert "__STATUS__:" in resp.text

    def test_warning_shown_when_preferred_agent_unavailable(self, client):
        # route_multi returns gemini but user asked for claude → warning emitted
        with (
            self._mock_memory(),
            self._mock_route_multi(agent_name="gemini"),
            self._mock_agent(["ok\n"]),
            patch("backend.main.memory_client.store"),
            patch("backend.main.agent_router.learn_from_prompt"),
        ):
            resp = client.post("/chat", json={
                "prompt": "hi", "session_id": "s1", "preferred_agent": "claude",
            })
        assert "unavailable" in resp.text

    def test_permission_request_injected_into_stream(self, client, fresh_broker):
        import threading, time

        def _fake_stream(*args, **kwargs):
            time.sleep(0.05)
            yield "agent response\n"

        mock_agent = MagicMock()
        mock_agent.execute_stream.side_effect = _fake_stream

        def _inject_perm():
            time.sleep(0.02)
            pr = fresh_broker.create("s1", "claude", "Bash", {"command": "ls"})
            fresh_broker.decide(pr.id, "allow")

        threading.Thread(target=_inject_perm, daemon=True).start()

        with (
            self._mock_memory(),
            self._mock_route_multi(),
            patch("backend.main.agent_factory.get_agent", return_value=mock_agent),
            patch("backend.main.memory_client.store"),
            patch("backend.main.agent_router.learn_from_prompt"),
        ):
            resp = client.post("/chat", json={"prompt": "hi", "session_id": "s1"})

        assert "__PERMISSION_REQUEST__:" in resp.text
