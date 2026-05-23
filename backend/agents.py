import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

from backend.agents_catalog import AGENTS

_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
_PROJECT_ROOT = str(Path(__file__).parent.parent.absolute())

_CLI_MAP = {
    "claude": "claude",
    "gemini": "gemini",
    "codex":  "codex",
    "grok":   "grok",
}

_CONTAINER_MAP = {
    "claude": "leadagent-claude",
    "gemini": "leadagent-gemini",
    "codex":  "leadagent-codex",
    "grok":   "leadagent-grok",
}


def _container_running(container: str) -> bool:
    """Check if a named Docker container is running."""
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True, text=True, timeout=2
        )
        return res.stdout.strip() == "true"
    except Exception:
        return False


def is_installed_anywhere(cli: str) -> bool:
    """Check if CLI is installed locally OR if its container is running in Docker mode."""
    if os.environ.get("LEADAGENT_DOCKER_MODE"):
        container = _CONTAINER_MAP.get(cli)
        if container:
            return _container_running(container)
    
    # Native check
    if shutil.which(cli):
        return True
    
    # Check extra common paths (copied from main.py for consistency)
    extra_paths = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/.leadagent/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    return any(os.path.isfile(os.path.join(d, cli)) for d in extra_paths)


def _build_argv(cli: str, args: list[str], cwd: str = ".", tty: bool = False) -> list[str]:
    """
    Return the command argv to run `cli args`.
    Uses `docker exec` when Docker mode is active and the container is up,
    otherwise falls back to the local CLI.

    tty=True adds -t (needed when the caller provides a PTY, e.g. pexpect).
    Leave tty=False for subprocess.Popen calls (no outer TTY).
    """
    if os.environ.get("LEADAGENT_DOCKER_MODE") and shutil.which("docker"):
        container = _CONTAINER_MAP.get(cli)
        if container and _container_running(container):
            # Pass working directory to the container so it sees the same files.
            abs_cwd = cwd
            if not os.path.isabs(cwd):
                abs_cwd = os.path.join("/app/leadagent-data", cwd)
            # Host temp paths (macOS /var/folders) aren't mounted in containers.
            _MOUNTED_PREFIXES = [
                "/app/leadagent-data",
                os.path.expanduser("~")
            ]
            workspace = os.environ.get("LEADAGENT_WORKSPACE")
            if workspace:
                _MOUNTED_PREFIXES.append(workspace)

            if not any(abs_cwd.startswith(p) for p in _MOUNTED_PREFIXES):
                abs_cwd = "/tmp"
            flags = ["-it"] if tty else ["-i"]
            return ["docker", "exec"] + flags + ["-w", abs_cwd, container, cli] + args
    return [cli] + args

# Prompts larger than this are passed via stdin to avoid OS ARG_MAX limits
_ARG_MAX = 100_000

_SUPPRESS_PREFIXES = (
    "Warning: 256-color support not detected",
    "Ripgrep is not available",
    "Error executing tool",
    "[LocalAgentExecutor]",
    "Blocked call:",
    "is not available to this agent",
    "(node:",
    "DeprecationWarning:",
    "[DEP",
)


class CLIAgent:
    def __init__(self, command: str):
        self.command = command

    def execute_stream(self, prompt: str, cwd: str = ".", session_id: str = "default", simple: bool = False) -> Generator[str, None, None]:
        use_stdin = len(prompt) > _ARG_MAX
        stdin_data = None

        if self.command == "claude" and not simple:
            yield from self._execute_claude_stream(prompt, cwd, session_id, use_stdin)
            return

        if self.command == "gemini":
            if use_stdin:
                command_args = _build_argv(self.command, ["--skip-trust", "--approval-mode", "plan"], cwd=cwd)
                stdin_data = prompt
            else:
                command_args = _build_argv(self.command, ["-p", prompt, "--skip-trust", "--approval-mode", "plan"], cwd=cwd)
        elif self.command == "codex":
            # Use 'exec --json' for robust non-interactive parsing.
            flags = ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox"]
            
            # Use positional arguments for small prompts (more reliable, avoids stdin noise).
            # Fall back to stdin only for very large prompts.
            if len(prompt) < _ARG_MAX:
                command_args = _build_argv(self.command, flags + [prompt], cwd=cwd)
                stdin_data = None
            else:
                command_args = _build_argv(self.command, flags, cwd=cwd)
                stdin_data = prompt
            
            yield from self._execute_jsonl_stream(command_args, stdin_data, cwd)
            return
        elif self.command == "grok":
            if use_stdin:
                command_args = _build_argv(self.command, ["--print"], cwd=cwd)
                stdin_data = prompt
            else:
                command_args = _build_argv(self.command, ["--print", prompt], cwd=cwd)
        else:
            # any future --print-compatible CLI
            if use_stdin:
                command_args = _build_argv(self.command, ["--print"], cwd=cwd)
                stdin_data = prompt
            else:
                command_args = _build_argv(self.command, ["--print", prompt], cwd=cwd)

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")

        local_cwd = cwd if os.path.isdir(cwd) else None
        process = subprocess.Popen(
            command_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if use_stdin else None,
            text=True,
            bufsize=0,
            cwd=local_cwd,
            env=env,
        )

        try:
            if use_stdin and stdin_data:
                process.stdin.write(stdin_data)
                process.stdin.close()

            full_output = []
            for raw_line in process.stdout:
                line = _ANSI_RE.sub('', raw_line)
                full_output.append(line)
                stripped = line.strip()
                if stripped and not any(stripped.startswith(p) for p in _SUPPRESS_PREFIXES):
                    yield line

            process.wait()
        finally:
            if process.poll() is None:
                process.terminate()

        if process.returncode != 0 and process.returncode != 1:
            raise Exception(f"Agent failed with exit code {process.returncode}")

    def _execute_jsonl_stream(
        self, command_args: list[str], stdin_data: str | None, cwd: str
    ) -> Generator[str, None, None]:
        """Generic JSONL event stream parser (used by Codex and others)."""
        def find_text(obj: Any) -> Generator[str, None, None]:
            """Recursively find any 'text' or 'content' fields in the JSON event."""
            if isinstance(obj, dict):
                # 1. Check for known text fields
                if "text" in obj and isinstance(obj["text"], str):
                    yield obj["text"]
                elif "content" in obj and isinstance(obj["content"], str):
                    yield obj["content"]
                # 2. Check for Claude-style text deltas
                elif obj.get("type") == "text_delta" and "text" in obj:
                    yield obj["text"]
                # 3. Recurse into all other fields
                else:
                    for val in obj.values():
                        yield from find_text(val)
            elif isinstance(obj, list):
                for item in obj:
                    yield from find_text(item)

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")

        local_cwd = cwd if os.path.isdir(cwd) else None
        # Only pipe stdin if we actually have data to send. 
        # Otherwise, some CLIs (like codex) might hang waiting for input.
        process = subprocess.Popen(
            command_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, # Silence CLI noise (like "Reading from stdin...")
            stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
            text=True,
            bufsize=0,
            cwd=local_cwd,
            env=env,
        )

        try:
            if stdin_data:
                process.stdin.write(stdin_data)
                process.stdin.close()

            for raw_line in process.stdout:
                raw = raw_line.rstrip("\n")
                if not raw:
                    continue
                
                # Check if it's JSONL. If not (e.g. a startup warning), skip it.
                if not raw.startswith("{"):
                    # Still check for suppression list even for non-JSON lines
                    stripped = raw.strip()
                    if stripped and not any(stripped.startswith(p) for p in _SUPPRESS_PREFIXES):
                        yield raw_line
                    continue

                try:
                    event = json.loads(raw)
                    yield from find_text(event)
                except json.JSONDecodeError:
                    continue

            process.wait()
        finally:
            if process.poll() is None:
                process.terminate()

    def _execute_claude_stream(
        self, prompt: str, cwd: str, session_id: str, use_stdin: bool
    ) -> Generator[str, None, None]:
        """Run claude with stream-json output and MCP permission tool."""
        mcp_cfg = {
            "mcpServers": {
                "leadagent_perm": {
                    "command": sys.executable,
                    "args": ["-m", "backend.permission_mcp_server"],
                    "env": {
                        "LEADAGENT_SESSION_ID": session_id,
                        "LEADAGENT_BACKEND_URL": "http://localhost:8000",
                        "LEADAGENT_AGENT_NAME": "claude",
                        "PYTHONPATH": _PROJECT_ROOT,
                    },
                }
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump(mcp_cfg, fh)
            mcp_path = fh.name

        stream_flags = [
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", mcp_path,
            "--permission-prompt-tool", "mcp__leadagent_perm__ask_permission",
        ]

        if use_stdin:
            command_args = _build_argv("claude", stream_flags)
            stdin_data: str | None = prompt
        else:
            command_args = _build_argv("claude", ["-p", prompt] + stream_flags)
            stdin_data = None

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")

        local_cwd = cwd if os.path.isdir(cwd) else None
        process = subprocess.Popen(
            command_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # keep stderr separate from JSON stdout
            stdin=subprocess.PIPE if use_stdin else None,
            text=True,
            bufsize=0,
            cwd=local_cwd,
            env=env,
        )

        try:
            if use_stdin and stdin_data:
                process.stdin.write(stdin_data)
                process.stdin.close()

            for raw_line in process.stdout:
                raw = raw_line.rstrip("\n")
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")
                if etype == "assistant":
                    # Full or partial assistant message
                    for item in event.get("message", {}).get("content", []):
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            if text:
                                yield text
                elif etype == "content_block_delta":
                    # Streaming token delta
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield text
                elif etype == "result" and event.get("subtype") == "error":
                    err = str(event.get("result", "")).lower()
                    if "rate limit" in err or "overloaded" in err or "529" in err:
                        raise Exception("AGENT_TRANSIENT_ERROR")
                    raise Exception(f"Agent error: {err}")

        finally:
            if process.poll() is None:
                process.terminate()
            process.wait()
            if mcp_path:
                try:
                    os.unlink(mcp_path)
                except OSError:
                    pass

        if process.returncode not in (0, None, 1):
            raise Exception(f"Agent failed with exit code {process.returncode}")


class AgentFactory:
    @staticmethod
    def get_agent(agent_name: str) -> CLIAgent:
        if agent_name in _CLI_MAP:
            return CLIAgent(_CLI_MAP[agent_name])
        raise ValueError(f"Unknown agent: {agent_name}")


agent_factory = AgentFactory()
