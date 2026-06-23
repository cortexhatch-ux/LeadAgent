"""Tests for backend/main.py — FastAPI endpoints via TestClient."""

import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.permissions import PermissionBroker


_TEST_KEY = "test-api-key-fixture-only"


@pytest.fixture
def client(monkeypatch):
    # Patch get_api_key so GuardMiddleware sees a known key, then set that key
    # on all requests via headers so they pass the auth check.
    monkeypatch.setattr("backend.security.get_api_key", lambda: _TEST_KEY)
    monkeypatch.setattr("backend.main.get_api_key", lambda: _TEST_KEY)
    return TestClient(app, base_url="http://localhost:8000", headers={"X-LeadAgent-Key": _TEST_KEY})


@pytest.fixture
def bare_client():
    """TestClient with no auto-auth headers — for tests that exercise key validation itself."""
    return TestClient(app, base_url="http://localhost:8000")


@pytest.fixture
def fresh_broker(monkeypatch):
    """Swap the global broker for a fresh instance per test."""
    b = PermissionBroker()
    monkeypatch.setattr("backend.main.broker", b)
    return b


# ── GuardMiddleware (Host / Origin) ───────────────────────────────────────────

class TestGuardMiddleware:
    def test_loopback_host_allowed(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_lan_host_rejected(self, client):
        resp = client.get("/", headers={"host": "192.168.1.50:8000"})
        assert resp.status_code == 421

    def test_rebinding_hostname_rejected(self, client):
        resp = client.get("/", headers={"host": "evil.example.com"})
        assert resp.status_code == 421

    def test_cross_origin_rejected(self, client):
        resp = client.get("/", headers={"origin": "https://evil.example.com"})
        assert resp.status_code == 403

    def test_loopback_origin_allowed(self, client, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: None)
        resp = client.get("/", headers={"origin": "http://localhost:8000"})
        assert resp.status_code == 200


# ── POST /memory/query (read-only guard) ──────────────────────────────────────

class TestMemoryQueryGuard:
    def test_read_query_allowed(self, client):
        with patch("backend.main.db.query_all", return_value=[["ok"]]):
            resp = client.post("/memory/query", json={"cypher": "MATCH (n) RETURN n"})
        assert resp.status_code == 200
        assert resp.json()["result"] == [["ok"]]

    def test_delete_query_rejected(self, client):
        with patch("backend.main.db.query_all") as q:
            resp = client.post(
                "/memory/query", json={"cypher": "MATCH (n) DETACH DELETE n"}
            )
        assert resp.status_code == 403
        q.assert_not_called()

    def test_create_query_rejected(self, client):
        resp = client.post("/memory/query", json={"cypher": "CREATE (n:Evil)"})
        assert resp.status_code == 403

    def test_missing_cypher_returns_400(self, client):
        resp = client.post("/memory/query", json={})
        assert resp.status_code == 400


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


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_rate_limit_hit_returns_429(self, client):
        from backend.security import _check_rate_limit, _RATE_LIMIT_RPM, _rate_buckets
        import time as _t
        # Fill the bucket for a fake IP on /chat
        ip, path = "10.0.0.99", "/chat"
        key = (ip, path)
        _rate_buckets.pop(key, None)
        now = _t.monotonic()
        from collections import deque
        _rate_buckets[key] = deque([now] * _RATE_LIMIT_RPM)
        # The next check should fail
        assert not _check_rate_limit(ip, path)
        _rate_buckets.pop(key, None)

    def test_rate_limit_not_hit_within_budget(self):
        from backend.security import _check_rate_limit, _rate_buckets
        ip, path = "10.0.0.88", "/chat"
        _rate_buckets.pop((ip, path), None)
        assert _check_rate_limit(ip, path)
        _rate_buckets.pop((ip, path), None)


# ── API key enforcement ───────────────────────────────────────────────────────

class TestAPIKey:
    def test_correct_key_allowed(self, bare_client, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: "test-key-abc")
        resp = bare_client.get("/roles", headers={"x-leadagent-key": "test-key-abc"})
        assert resp.status_code == 200

    def test_wrong_key_rejected(self, bare_client, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: "test-key-abc")
        resp = bare_client.get("/roles", headers={"x-leadagent-key": "wrong-key"})
        assert resp.status_code == 401

    def test_missing_key_rejected(self, bare_client, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: "test-key-abc")
        resp = bare_client.get("/roles")
        assert resp.status_code == 401


# ── API key matrix: all non-exempt endpoints require key (issue #1) ───────────

_KEY = "matrix-test-key"

_NON_EXEMPT_ENDPOINTS = [
    ("GET",    "/roles"),
    ("POST",   "/chat"),
    ("POST",   "/memory/query"),
    ("POST",   "/memory/forget"),
    ("POST",   "/permission/_request"),
    ("POST",   "/permission/nonexistent/decide"),
    ("GET",    "/permission/nonexistent/wait"),
    ("GET",    "/rules"),
    ("POST",   "/rules"),
    ("DELETE", "/rules/nonexistent"),
    ("POST",   "/rules/evaluate"),
]

_EXEMPT_ENDPOINTS = [
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/v1/status"),
    ("GET", "/doctor"),
]


class TestAPIKeyMatrix:
    """Every non-exempt endpoint must return 401 when the key is absent."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: _KEY)
        monkeypatch.setattr("backend.main.get_api_key", lambda: _KEY)

    @pytest.mark.parametrize("method,path", _NON_EXEMPT_ENDPOINTS)
    def test_missing_key_returns_401(self, bare_client, method, path):
        resp = bare_client.request(method, path, json={})
        assert resp.status_code == 401, (
            f"{method} {path} returned {resp.status_code}, expected 401"
        )

    @pytest.mark.parametrize("method,path", _EXEMPT_ENDPOINTS)
    def test_exempt_path_accessible_without_key(self, bare_client, method, path):
        with (
            patch("backend.main.db.query_all", return_value=[[0]]),
            patch("backend.main.shutil.which", return_value=None),
        ):
            resp = bare_client.request(method, path)
        # Exempt paths must not 401 — other errors (503, 500) are acceptable
        assert resp.status_code != 401, (
            f"Exempt {method} {path} should never 401 (got {resp.status_code})"
        )


# ── /permission/{id}/decide unauthenticated (issue #3) ───────────────────────

class TestPermissionDecideUnauth:
    def test_unauthenticated_decide_returns_401(self, bare_client, fresh_broker, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: _KEY)
        monkeypatch.setattr("backend.main.get_api_key", lambda: _KEY)
        pr = fresh_broker.create("s1", "claude", "Bash", {})
        resp = bare_client.post(f"/permission/{pr.id}/decide", json={"behavior": "allow"})
        assert resp.status_code == 401

    def test_unauthenticated_permission_request_returns_401(self, bare_client, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: _KEY)
        monkeypatch.setattr("backend.main.get_api_key", lambda: _KEY)
        resp = bare_client.post("/permission/_request", json={
            "session_id": "s1", "agent": "claude", "tool_name": "Bash", "input": {},
        })
        assert resp.status_code == 401


# ── /memory/forget unauthenticated (issue #4) ────────────────────────────────

class TestMemoryForgetUnauth:
    def test_unauthenticated_forget_returns_401(self, bare_client, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: _KEY)
        monkeypatch.setattr("backend.main.get_api_key", lambda: _KEY)
        resp = bare_client.post("/memory/forget", json={"session_id": "s1"})
        assert resp.status_code == 401

    def test_authenticated_forget_succeeds(self, bare_client, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: _KEY)
        monkeypatch.setattr("backend.main.get_api_key", lambda: _KEY)
        with patch("backend.main.db.query"):
            resp = bare_client.post(
                "/memory/forget",
                json={"session_id": "s1"},
                headers={"X-LeadAgent-Key": _KEY},
            )
        assert resp.status_code == 200


# ── /rules CRUD unauthenticated (issue #7) ────────────────────────────────────

class TestRulesCRUDUnauth:
    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setattr("backend.security.get_api_key", lambda: _KEY)
        monkeypatch.setattr("backend.main.get_api_key", lambda: _KEY)

    def test_list_rules_unauth_returns_401(self, bare_client):
        resp = bare_client.get("/rules")
        assert resp.status_code == 401

    def test_create_rule_unauth_returns_401(self, bare_client):
        resp = bare_client.post("/rules", json={
            "tool_pattern": "Bash", "action": "allow",
        })
        assert resp.status_code == 401

    def test_delete_rule_unauth_returns_401(self, bare_client):
        resp = bare_client.delete("/rules/some-rule-id")
        assert resp.status_code == 401

    def test_evaluate_rule_unauth_returns_401(self, bare_client):
        resp = bare_client.post("/rules/evaluate", json={
            "tool_name": "Bash", "input": {},
        })
        assert resp.status_code == 401


# ── project_id validation on ChatRequest ─────────────────────────────────────

class TestProjectIDValidation:
    def test_valid_project_id_accepted(self, client):
        with (
            patch("backend.main.agent_router.check_memory", return_value=None),
            patch("backend.main.agent_router.route_multi", return_value=(["none"], {})),
        ):
            resp = client.post("/chat", json={"prompt": "hi", "project_id": "my-project_1"})
        assert resp.status_code == 200

    def test_invalid_project_id_rejected(self, client):
        resp = client.post("/chat", json={"prompt": "hi", "project_id": "bad/id?here"})
        assert resp.status_code == 422

    def test_project_id_too_long_rejected(self, client):
        resp = client.post("/chat", json={"prompt": "hi", "project_id": "a" * 65})
        assert resp.status_code == 422


# ── /memory/entities propagates project_id ───────────────────────────────────

class TestMemoryEntitiesProjectID:
    def test_project_id_passed_to_db(self, client, _mock_db):
        resp = client.post("/memory/entities", json={
            "name": "FastAPI", "type": "framework", "project_id": "proj-alpha"
        })
        assert resp.status_code == 200
        _mock_db.add_entity.assert_called_once_with(
            "FastAPI", "framework", "", source_project_id="proj-alpha"
        )

    def test_default_project_id_requires_promote_header(self, client, _mock_db):
        resp = client.post("/memory/entities", json={"name": "X", "type": "thing"})
        assert resp.status_code == 403

    def test_default_project_id_with_promote_header_succeeds(self, client, _mock_db):
        resp = client.post(
            "/memory/entities",
            json={"name": "X", "type": "thing"},
            headers={"X-LeadAgent-Promote": "1"},
        )
        assert resp.status_code == 200
        _mock_db.add_entity.assert_called_once_with("X", "thing", "", source_project_id="default")


# ── /memory/relationships propagates project_id ──────────────────────────────

class TestMemoryRelationshipsProjectID:
    def test_project_id_passed_to_db(self, client, _mock_db):
        resp = client.post("/memory/relationships", json={
            "source": "A", "target": "B", "type": "uses", "project_id": "proj-beta"
        })
        assert resp.status_code == 200
        _mock_db.add_relationship.assert_called_once_with(
            "A", "B", "uses", project_id="proj-beta"
        )

    def test_default_project_id_requires_promote_header(self, client, _mock_db):
        resp = client.post("/memory/relationships", json={
            "source": "A", "target": "B", "type": "uses"
        })
        assert resp.status_code == 403


# ── /v1/audit — memory shape normalisation ───────────────────────────────────

class TestAuditSession:
    def _audit(self, client, history_items):
        with (
            patch("backend.main.memory_client.search", return_value=history_items),
            patch("backend.main.db.query_all", return_value=[]),
            patch("backend.main._classify_task", return_value=("general", 1)),
        ):
            return client.get("/v1/audit/test-session")

    def test_standard_shape(self, client):
        items = [{"content": "User: hi\nAssistant: hello", "metadata": {"agent": "claude"}}]
        resp = self._audit(client, items)
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["agent"] == "claude"
        assert "hi" in data[0]["prompt_preview"]

    def test_observation_shape_narrative(self, client):
        items = [{"observation": {"narrative": "User: obs prompt\nAssistant: ok", "agent": "gemini"}}]
        resp = self._audit(client, items)
        assert resp.status_code == 200
        data = resp.json()
        assert "obs prompt" in data[0]["prompt_preview"]

    def test_observation_shape_title_fallback(self, client):
        items = [{"observation": {"title": "some title"}}]
        resp = self._audit(client, items)
        assert resp.status_code == 200
        assert "some title" in resp.json()[0]["prompt_preview"]

    def test_unknown_shape_does_not_crash(self, client):
        items = [{"unexpected_key": "value"}]
        resp = self._audit(client, items)
        assert resp.status_code == 200
        assert resp.json()[0]["prompt_preview"] == "..."

    def test_empty_history_returns_empty_list(self, client):
        resp = self._audit(client, [])
        assert resp.status_code == 200
        assert resp.json() == []
