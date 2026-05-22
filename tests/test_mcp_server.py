"""Tests for backend/permission_mcp_server.py."""

import json
from unittest.mock import patch, MagicMock

import pytest
import backend.permission_mcp_server as mcp


@pytest.fixture(autouse=True)
def patch_send(monkeypatch):
    """Capture all _send calls and return the list."""
    sent = []
    monkeypatch.setattr(mcp, "_send", lambda obj: sent.append(obj))
    return sent


# ── initialize ────────────────────────────────────────────────────────────────

class TestInitialize:
    def test_returns_server_info(self, patch_send):
        mcp._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        result = patch_send[-1]["result"]
        assert result["serverInfo"]["name"] == "leadagent_perm"
        assert result["serverInfo"]["version"] == "1.0"

    def test_returns_protocol_version(self, patch_send):
        mcp._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert patch_send[-1]["result"]["protocolVersion"] == "2024-11-05"

    def test_id_echoed(self, patch_send):
        mcp._handle({"jsonrpc": "2.0", "id": 42, "method": "initialize", "params": {}})
        assert patch_send[-1]["id"] == 42

    def test_capabilities_include_tools(self, patch_send):
        mcp._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert "tools" in patch_send[-1]["result"]["capabilities"]


# ── notifications/initialized ─────────────────────────────────────────────────

class TestNotification:
    def test_no_response_emitted(self, patch_send):
        before = len(patch_send)
        mcp._handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert len(patch_send) == before


# ── tools/list ────────────────────────────────────────────────────────────────

class TestToolsList:
    def test_returns_ask_permission_tool(self, patch_send):
        mcp._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = patch_send[-1]["result"]["tools"]
        names = [t["name"] for t in tools]
        assert "ask_permission" in names

    def test_ask_permission_has_schema(self, patch_send):
        mcp._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = patch_send[-1]["result"]["tools"]
        ask = next(t for t in tools if t["name"] == "ask_permission")
        assert "inputSchema" in ask
        assert "tool_name" in ask["inputSchema"]["properties"]


# ── unknown method ────────────────────────────────────────────────────────────

class TestUnknownMethod:
    def test_returns_error_for_unknown_method(self, patch_send):
        mcp._handle({"jsonrpc": "2.0", "id": 3, "method": "foo/bar", "params": {}})
        assert "error" in patch_send[-1]
        assert patch_send[-1]["error"]["code"] == -32601

    def test_no_response_for_notification_without_id(self, patch_send):
        before = len(patch_send)
        mcp._handle({"jsonrpc": "2.0", "method": "foo/bar"})
        assert len(patch_send) == before


# ── unknown tool call ─────────────────────────────────────────────────────────

class TestUnknownTool:
    def test_returns_error_for_unknown_tool(self, patch_send):
        mcp._handle({
            "jsonrpc": "2.0", "id": 4,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        assert "error" in patch_send[-1]


# ── ask_permission ────────────────────────────────────────────────────────────

class TestAskPermission:
    def _call(self, args):
        mcp._handle({
            "jsonrpc": "2.0", "id": 10,
            "method": "tools/call",
            "params": {"name": "ask_permission", "arguments": args},
        })

    def test_allow_decision_returned(self, patch_send, monkeypatch):
        def fake_post(*args, **kwargs):
            r = MagicMock()
            r.json.return_value = {"id": "req-1"}
            r.raise_for_status = MagicMock()
            return r

        def fake_get(*args, **kwargs):
            r = MagicMock()
            r.json.return_value = {"behavior": "allow", "scope": "once", "updatedInput": {"command": "ls"}}
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", fake_post)
        monkeypatch.setattr("requests.get", fake_get)

        self._call({"tool_name": "Bash", "input": {"command": "ls"}})
        result = patch_send[-1]["result"]
        assert result["isError"] is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["behavior"] == "allow"
        assert payload["updatedInput"] == {"command": "ls"}

    def test_deny_decision_returned(self, patch_send, monkeypatch):
        def fake_post(*args, **kwargs):
            r = MagicMock()
            r.json.return_value = {"id": "req-2"}
            r.raise_for_status = MagicMock()
            return r

        def fake_get(*args, **kwargs):
            r = MagicMock()
            r.json.return_value = {"behavior": "deny", "scope": "once", "message": "Denied by user"}
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", fake_post)
        monkeypatch.setattr("requests.get", fake_get)

        self._call({"tool_name": "Bash", "input": {"command": "rm -rf /"}})
        payload = json.loads(patch_send[-1]["result"]["content"][0]["text"])
        assert payload["behavior"] == "deny"
        assert "Denied by user" in payload["message"]

    def test_network_error_defaults_to_deny(self, patch_send, monkeypatch):
        monkeypatch.setattr("requests.post", MagicMock(side_effect=Exception("connection refused")))
        self._call({"tool_name": "Bash", "input": {}})
        payload = json.loads(patch_send[-1]["result"]["content"][0]["text"])
        assert payload["behavior"] == "deny"

    def test_missing_tool_name_handled(self, patch_send, monkeypatch):
        def fake_post(*args, **kwargs):
            r = MagicMock()
            r.json.return_value = {"id": "req-3"}
            r.raise_for_status = MagicMock()
            return r

        def fake_get(*args, **kwargs):
            r = MagicMock()
            r.json.return_value = {"behavior": "deny"}
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", fake_post)
        monkeypatch.setattr("requests.get", fake_get)
        self._call({})  # no tool_name
        assert "result" in patch_send[-1]  # should not crash


# ── main loop ─────────────────────────────────────────────────────────────────

class TestMainLoop:
    def test_invalid_json_skipped(self, patch_send, monkeypatch):
        import io
        lines = "not json\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}\n"
        monkeypatch.setattr("sys.stdin", io.StringIO(lines))
        mcp.main()
        assert any("tools" in str(m) for m in patch_send)

    def test_empty_lines_skipped(self, patch_send, monkeypatch):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("\n\n\n"))
        mcp.main()
        assert len(patch_send) == 0
