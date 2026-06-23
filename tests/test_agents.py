"""Tests for backend/agents.py — CLIAgent, AgentFactory, _execute_claude_stream."""

import json
import os
from unittest.mock import patch, MagicMock

import pytest

from backend.agents import (
    CLIAgent,
    AgentFactory,
    agent_factory,
    _PROJECT_ROOT,
    _safe_cwd,
)


@pytest.fixture(autouse=True)
def _mock_installed(monkeypatch):
    monkeypatch.setattr("backend.agents.is_installed_anywhere", lambda *a, **kw: True)


# ── _safe_cwd ─────────────────────────────────────────────────────────────────

class TestSafeCwd:
    def test_project_root_allowed(self):
        assert _safe_cwd(_PROJECT_ROOT) == os.path.realpath(_PROJECT_ROOT)

    def test_subdir_of_project_allowed(self):
        sub = os.path.join(_PROJECT_ROOT, "backend")
        assert _safe_cwd(sub) == os.path.realpath(sub)

    def test_home_rejected(self):
        # $HOME is no longer an allowed root (issue #6 hardening)
        home = os.path.expanduser("~")
        assert _safe_cwd(home) is None

    def test_system_dir_rejected(self):
        # /etc exists but is outside every allowed root
        assert _safe_cwd("/etc") is None

    def test_nonexistent_dir_rejected(self):
        assert _safe_cwd("/nonexistent/path/xyz") is None

    def test_empty_falls_back_to_none(self):
        assert _safe_cwd("") is None

    def test_extra_root_via_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LEADAGENT_ALLOWED_CWD", str(tmp_path))
        assert _safe_cwd(str(tmp_path)) == os.path.realpath(str(tmp_path))


# ── _PROJECT_ROOT ─────────────────────────────────────────────────────────────

class TestProjectRoot:
    def test_is_a_directory(self):
        assert os.path.isdir(_PROJECT_ROOT)

    def test_contains_backend(self):
        assert os.path.isdir(os.path.join(_PROJECT_ROOT, "backend"))


# ── AgentFactory ──────────────────────────────────────────────────────────────

class TestAgentFactory:
    def test_returns_cli_agent_for_claude(self):
        agent = agent_factory.get_agent("claude")
        assert isinstance(agent, CLIAgent)
        assert agent.command == "claude"

    def test_returns_cli_agent_for_gemini(self):
        agent = agent_factory.get_agent("gemini")
        assert agent.command == "agy"

    def test_returns_cli_agent_for_codex(self):
        agent = agent_factory.get_agent("codex")
        assert agent.command == "codex"

    def test_raises_for_unknown_agent(self):
        with pytest.raises(ValueError, match="Unknown agent"):
            agent_factory.get_agent("skynet")


# ── execute_stream — non-claude agents ───────────────────────────────────────

class TestExecuteStreamNonClaude:
    def _fake_process(self, lines, returncode=0):
        proc = MagicMock()
        proc.stdout = iter(lines)
        proc.returncode = returncode
        proc.stdin = MagicMock()
        proc.wait = MagicMock()
        return proc

    def test_gemini_yields_text_lines(self):
        agent = CLIAgent("gemini")
        proc = self._fake_process(["Hello\n", "World\n"])
        with patch("subprocess.Popen", return_value=proc):
            chunks = list(agent.execute_stream("say hello", session_id="s1"))
        assert any("Hello" in c for c in chunks)
        assert any("World" in c for c in chunks)

    def test_suppressed_prefixes_filtered(self):
        agent = CLIAgent("gemini")
        proc = self._fake_process([
            "Warning: 256-color support not detected\n",
            "Actual response\n",
        ])
        with patch("subprocess.Popen", return_value=proc):
            chunks = list(agent.execute_stream("hi", session_id="s1"))
        assert not any("256-color" in c for c in chunks)
        assert any("Actual response" in c for c in chunks)

    def test_ansi_codes_stripped(self):
        agent = CLIAgent("gemini")
        proc = self._fake_process(["\x1b[32mGreen text\x1b[0m\n"])
        with patch("subprocess.Popen", return_value=proc):
            chunks = list(agent.execute_stream("hi", session_id="s1"))
        assert any("Green text" in c for c in chunks)
        assert not any("\x1b" in c for c in chunks)

    def test_nonzero_returncode_raises(self):
        # returncode=1 is allowed; returncode=2 (unexpected) should raise for grok (standard path)
        agent = CLIAgent("grok")
        proc = self._fake_process([], returncode=2)
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(Exception):
                list(agent.execute_stream("hi", session_id="s1"))

    def test_returncode_one_with_result_event_does_not_raise(self):
        # returncode=1 is tolerated when the CLI emitted a success result event
        agent = CLIAgent("grok")
        result_line = json.dumps({"type": "end", "stopReason": "EndTurn", "is_error": False}) + "\n"
        proc = self._fake_process([result_line], returncode=1)
        with patch("subprocess.Popen", return_value=proc):
            list(agent.execute_stream("hi", session_id="s1"))

    def test_returncode_one_no_result_raises(self):
        # returncode=1 with no result event is a crash — should raise
        agent = CLIAgent("grok")
        proc = self._fake_process([], returncode=1)
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(Exception, match="exit code 1"):
                list(agent.execute_stream("hi", session_id="s1"))

    def test_codex_returncode_now_checked(self):
        # codex uses _execute_jsonl_stream which now has a returncode check
        agent = CLIAgent("codex")
        proc = self._fake_process([], returncode=2)
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(Exception, match="failed with exit code 2"):
                list(agent.execute_stream("hi", session_id="s1"))

    def test_grok_streaming_parsing(self):
        agent = CLIAgent("grok")
        events = [
            json.dumps({"type": "thought", "data": "Reasoning..."}) + "\n",
            json.dumps({"type": "text", "data": "Hello "}) + "\n",
            json.dumps({"type": "text", "data": "world!"}) + "\n",
            json.dumps({"type": "end", "stopReason": "EndTurn"}) + "\n",
        ]
        proc = self._fake_process(events, returncode=0)
        with patch("subprocess.Popen", return_value=proc):
            chunks = list(agent.execute_stream("hi", session_id="s1"))
        assert chunks == ["Hello ", "world!"]

    def test_grok_error_handling(self):
        agent = CLIAgent("grok")
        events = [
            json.dumps({"type": "end", "stopReason": "Error", "error": "Internal Fail"}) + "\n",
        ]
        proc = self._fake_process(events, returncode=0)
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(Exception, match="Internal Fail"):
                list(agent.execute_stream("hi", session_id="s1"))

    def test_zero_returncode_no_exception(self):
        agent = CLIAgent("gemini")
        proc = self._fake_process(["ok\n"], returncode=0)
        with patch("subprocess.Popen", return_value=proc):
            list(agent.execute_stream("hi", session_id="s1"))

    def test_codex_streaming_parsing(self):
        agent = CLIAgent("codex")
        events = [
            json.dumps({"type": "turn.started"}) + "\n",
            json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": "ls"}}) + "\n",
            json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}}) + "\n",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}}) + "\n",
            json.dumps({"type": "turn.completed"}) + "\n",
        ]
        proc = self._fake_process(events, returncode=0)
        with patch("subprocess.Popen", return_value=proc):
            chunks = list(agent.execute_stream("hi", session_id="s1"))
        
        # Should include status update and final text
        assert "__STATUS__:codex → ls" in chunks[0]
        assert "Done" in chunks[1]

    def test_codex_fallback_parsing(self):
        # Ensure generic JSONL agents still work with recursive search
        agent = CLIAgent("codex")
        events = [
            json.dumps({"random": "event", "text": "fallback"}) + "\n",
        ]
        proc = self._fake_process(events, returncode=0)
        with patch("subprocess.Popen", return_value=proc):
            chunks = list(agent.execute_stream("hi", session_id="s1"))
        assert chunks == ["fallback"]

    def test_large_prompt_uses_stdin(self):
        agent = CLIAgent("codex")
        big_prompt = "x" * 200_000
        proc = self._fake_process(["response\n"])
        with patch("subprocess.Popen", return_value=proc) as mock_popen:
            list(agent.execute_stream(big_prompt, session_id="s1"))
        call_args = mock_popen.call_args
        # stdin=PIPE when prompt exceeds ARG_MAX
        assert call_args.kwargs.get("stdin") is not None or \
               call_args[1].get("stdin") is not None


# ── _execute_claude_stream ────────────────────────────────────────────────────

class TestExecuteClaudeStream:
    def _fake_claude_process(self, events, returncode=0):
        lines = [json.dumps(e) + "\n" for e in events]
        proc = MagicMock()
        proc.stdout = iter(lines)
        proc.returncode = returncode
        proc.stdin = MagicMock()
        proc.wait = MagicMock()
        return proc

    def test_extracts_text_from_assistant_event(self):
        agent = CLIAgent("claude")
        events = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello world"}]}},
            {"type": "result", "subtype": "success"},
        ]
        proc = self._fake_claude_process(events)
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink"),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/fake.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            chunks = list(agent.execute_stream("hello", session_id="s1"))
        assert any("Hello world" in c for c in chunks)

    def test_extracts_streaming_text_delta(self):
        agent = CLIAgent("claude")
        events = [
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello "}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "world"}},
        ]
        proc = self._fake_claude_process(events)
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink"),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/fake.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            chunks = list(agent.execute_stream("hello", session_id="s1"))
        assert "".join(chunks) == "Hello world"

    def test_skips_non_json_lines(self):
        agent = CLIAgent("claude")
        proc = MagicMock()
        proc.stdout = iter(["not json\n", '{"type":"result","subtype":"success"}\n'])
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.wait = MagicMock()
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink"),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/fake.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            chunks = list(agent.execute_stream("hello", session_id="s1"))
        assert chunks == []  # no text content emitted

    def test_result_error_with_rate_limit_raises_transient(self):
        # "rate limit" in the error message triggers AGENT_TRANSIENT_ERROR
        agent = CLIAgent("claude")
        events = [
            {"type": "result", "subtype": "error", "result": "You have hit a rate limit."},
        ]
        proc = self._fake_claude_process(events)
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink"),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/fake.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(Exception, match="AGENT_TRANSIENT_ERROR"):
                list(agent.execute_stream("hello", session_id="s1"))

    def test_result_error_non_quota_raises_agent_error(self):
        # Non-quota errors raise a generic Agent error, not AGENT_TRANSIENT_ERROR
        agent = CLIAgent("claude")
        events = [
            {"type": "result", "subtype": "error", "result": "You've hit your usage limit."},
        ]
        proc = self._fake_claude_process(events)
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink"),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/fake.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(Exception, match="Agent error"):
                list(agent.execute_stream("hello", session_id="s1"))

    def test_mcp_config_written_with_session_id(self):
        agent = CLIAgent("claude")
        proc = self._fake_claude_process([])
        written_content = {}

        class FakeTmp:
            name = "/tmp/fake_mcp.json"
            def write(self, s):
                written_content["data"] = s
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile", return_value=FakeTmp()),
            patch("json.dump", side_effect=lambda obj, fh: written_content.update(obj)),
            patch("os.unlink"),
        ):
            list(agent.execute_stream("hello", session_id="my-session"))

        server_cfg = written_content.get("mcpServers", {}).get("leadagent_perm", {})
        assert server_cfg.get("env", {}).get("LEADAGENT_SESSION_ID") == "my-session"

    def test_temp_file_cleaned_up_on_success(self):
        agent = CLIAgent("claude")
        proc = self._fake_claude_process([])
        deleted = []

        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink", side_effect=lambda p: deleted.append(p)),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/cleanup_test.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            list(agent.execute_stream("hello", session_id="s1"))

        assert "/tmp/cleanup_test.json" in deleted

    def test_temp_file_cleaned_up_on_exception(self):
        agent = CLIAgent("claude")
        proc = MagicMock()
        proc.stdout = iter(['{"type":"result","subtype":"error","result":"quota exhausted"}\n'])
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.wait = MagicMock()
        deleted = []

        with (
            patch("subprocess.Popen", return_value=proc),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink", side_effect=lambda p: deleted.append(p)),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/cleanup_exc.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(Exception):
                list(agent.execute_stream("hello", session_id="s1"))

        assert "/tmp/cleanup_exc.json" in deleted

    # Issue #2: execute mode must produce --permission-mode acceptEdits for Claude,
    # never --dangerously-skip-permissions or any variant of it.
    def test_execute_mode_uses_accept_edits_not_dangerous_flag(self):
        agent = CLIAgent("claude")
        proc = self._fake_claude_process([])
        captured_args = []

        with (
            patch("subprocess.Popen", side_effect=lambda args, **kw: (
                captured_args.extend(args), proc)[1]),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink"),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/mode_test.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            list(agent.execute_stream("go ahead", session_id="s1", mode="execute"))

        args_str = " ".join(str(a) for a in captured_args)
        assert "acceptEdits" in args_str, (
            "execute mode must pass --permission-mode acceptEdits to claude"
        )
        assert "dangerously" not in args_str.lower(), (
            "claude must never receive a dangerously-skip-permissions flag"
        )

    def test_plan_mode_uses_default_permission_mode(self):
        agent = CLIAgent("claude")
        proc = self._fake_claude_process([])
        captured_args = []

        with (
            patch("subprocess.Popen", side_effect=lambda args, **kw: (
                captured_args.extend(args), proc)[1]),
            patch("tempfile.NamedTemporaryFile") as mock_tmp,
            patch("os.unlink"),
        ):
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/mode_plan.json"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            list(agent.execute_stream("explain this", session_id="s1", mode="plan"))

        args_str = " ".join(str(a) for a in captured_args)
        assert "acceptEdits" not in args_str, (
            "plan mode must not pass acceptEdits"
        )
        assert "dangerously" not in args_str.lower(), (
            "plan mode must never receive a dangerously-skip-permissions flag"
        )
