import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any


_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_PROJECT_ROOT = str(Path(__file__).parent.parent.absolute())

# Gemini model fallback ladder — tried in order when quota is exhausted
_GEMINI_MODEL_LADDER = [
    None,                    # default (no -m flag) = Gemini 2.5 Pro
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

_CLI_MAP = {
    "claude": "claude",
    "gemini": "gemini",
    "codex": "codex",
    "grok": "grok",
}

_CONTAINER_MAP = {
    "claude": "leadagent-claude",
    "gemini": "leadagent-gemini",
    "codex": "leadagent-codex",
    "grok": "leadagent-grok",
}


def _container_running(container: str) -> bool:
    """Check if a named Docker container is running."""
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return res.stdout.strip() == "true"
    except Exception:
        return False


def is_installed_anywhere(cli: str) -> bool:
    """Check if CLI is installed locally OR if its container is running in Docker mode."""
    if cli == "ollama":
        from backend.agents import OllamaAgent
        import requests
        agent = OllamaAgent()
        try:
            resp = requests.get(f"{agent.url}/api/tags", timeout=0.5)
            if resp.status_code == 200:
                return True
        except Exception:
            pass

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


def _build_argv(
    cli: str, args: list[str], cwd: str = ".", tty: bool = False
) -> list[str]:
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
            # Translate host repo path → container mount point
            if abs_cwd == _PROJECT_ROOT or abs_cwd.startswith(_PROJECT_ROOT + "/"):
                abs_cwd = "/app/leadagent-data" + abs_cwd[len(_PROJECT_ROOT):]
            # Host temp paths (macOS /var/folders) aren't mounted in containers.
            _MOUNTED_PREFIXES = ["/app/leadagent-data", os.path.expanduser("~")]
            workspace = os.environ.get("LEADAGENT_WORKSPACE")
            if workspace:
                _MOUNTED_PREFIXES.append(workspace)

            if not any(abs_cwd.startswith(p) for p in _MOUNTED_PREFIXES):
                abs_cwd = "/tmp"
            flags = ["-it"] if tty else ["-i"]
            return ["docker", "exec"] + flags + ["-w", abs_cwd, container, cli] + args
    return [cli] + args


def _allowed_cwd_roots() -> list[str]:
    """Directories an agent is permitted to run inside."""
    roots = [
        _PROJECT_ROOT,
        os.path.expanduser("~"),
        tempfile.gettempdir(),
        "/app/leadagent-data",
    ]
    workspace = os.environ.get("LEADAGENT_WORKSPACE")
    if workspace:
        roots.append(workspace)
    extra = os.environ.get("LEADAGENT_ALLOWED_CWD", "")
    roots.extend(p for p in extra.split(":") if p)
    return [os.path.realpath(r) for r in roots]


def _tool_status_hint(tool_input: Any) -> str:
    """Short human-readable hint for a tool_use event (file path, command, …)."""
    if isinstance(tool_input, dict):
        for key in ("file_path", "path", "command", "pattern", "url", "query"):
            val = tool_input.get(key)
            if isinstance(val, str) and val:
                if len(val) > 60:
                    val = val[:57] + "..."
                return f"({val})"
    return ""


def _safe_cwd(cwd: str) -> str | None:
    """Resolve `cwd` and return it only if it sits inside an allowed root.

    Caller-supplied working directories flow straight into subprocess spawns,
    so an unchecked absolute/`..` path could point an agent at any directory on
    the host. Anything outside the allow-list falls back to None (the daemon's
    own cwd) instead of being honored.
    """
    if not cwd:
        return None
    try:
        resolved = os.path.realpath(cwd)
    except (OSError, ValueError):
        return None
    if not os.path.isdir(resolved):
        return None
    for root in _allowed_cwd_roots():
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    print(f"[security] rejected cwd outside allowed roots: {cwd}")
    return None


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
    "┌─",
    "│",
    "at ",
    "[Routing]",
)


class CLIAgent:
    def __init__(self, command: str):
        self.command = command

    def _build_popen_args(self, prompt: str, cwd: str, mode: str, gemini_model: str | None = None) -> tuple[list[str], str | None, bool]:
        """Return (args, stdin_data, use_stdin)."""
        use_stdin = len(prompt) > _ARG_MAX
        stdin_data = prompt if use_stdin else None

        if self.command == "gemini":
            # Map LeadAgent internal modes to Gemini's --approval-mode values:
            #   "execute" → "yolo"    (auto-approve all, structural rules still enforce)
            #   "plan"    → "default" (LeadAgent "plan" = ask before acting, NOT Gemini read-only)
            #   anything else → "default"
            gemini_mode = {"execute": "yolo"}.get(mode, "default")
            flags = ["--skip-trust", "--approval-mode", gemini_mode]
            if gemini_model:
                flags = ["-m", gemini_model] + flags
            if not use_stdin:
                flags = ["-p", prompt] + flags
            return _build_argv(self.command, flags, cwd=cwd), stdin_data, use_stdin

        if self.command == "claude":
            flags = ["-p"]
            if not use_stdin:
                flags = ["-p", prompt]
            return _build_argv(self.command, flags, cwd=cwd), stdin_data, use_stdin

        if self.command == "grok" or self.command not in ("claude", "gemini", "codex"):
            flags = ["--print"]
            if not use_stdin:
                flags = ["--print", prompt]
            return _build_argv(self.command, flags, cwd=cwd), stdin_data, use_stdin
            
        return [], None, False

    def execute_stream(
        self,
        prompt: str,
        cwd: str = ".",
        session_id: str = "default",
        simple: bool = False,
        mode: str = "plan",
    ) -> Generator[str, None, None]:
        if self.command == "ollama":
            # Ollama uses a REST API, not Popen
            from backend.agents import OllamaAgent
            yield from OllamaAgent().execute_stream(prompt, cwd, session_id, simple)
            return

        if self.command == "claude" and not simple:
            yield from self._execute_claude_stream(prompt, cwd, session_id, len(prompt) > _ARG_MAX, mode)
            return

        if self.command == "codex":
            # By default run Codex in a workspace-scoped sandbox with no
            # interactive approvals — it can edit files in its cwd but cannot
            # touch the wider host or network. The fully-unsandboxed mode
            # (host + network access, no approvals) is opt-in because, combined
            # with prompt injection, it is a host-takeover primitive.
            if os.environ.get("LEADAGENT_ALLOW_UNSANDBOXED"):
                flags = ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox"]
            else:
                flags = [
                    "exec", "--json",
                    "--sandbox", "workspace-write",
                    "--ask-for-approval", "never",
                ]
            use_stdin = len(prompt) > _ARG_MAX
            if not use_stdin:
                flags.append(prompt)
            command_args = _build_argv(self.command, flags, cwd=cwd)
            yield from self._execute_jsonl_stream(command_args, prompt if use_stdin else None, cwd)
            return

        if self.command == "gemini":
            yield from self._execute_gemini_with_fallback(prompt, cwd, mode)
            return

        command_args, stdin_data, use_stdin = self._build_popen_args(prompt, cwd, mode)
        if not command_args:
             raise ValueError(f"Unsupported agent command: {self.command}")

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")

        local_cwd = _safe_cwd(cwd)
        process = subprocess.Popen(
            command_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if use_stdin else None,
            text=True,
            bufsize=0,
            cwd=local_cwd,
            env=env,
            start_new_session=True,
        )

        def _log_stderr():
            for line in process.stderr:
                if line.strip():
                    print(f"[{self.command} CLI Stderr]: {line.strip()}")

        threading.Thread(target=_log_stderr, daemon=True).start()

        try:
            if use_stdin and stdin_data:
                process.stdin.write(stdin_data)
                process.stdin.close()

            for raw_line in process.stdout:
                line = _ANSI_RE.sub("", raw_line)
                stripped = line.strip()
                if not stripped:
                    continue
                if any(stripped.startswith(p) for p in _SUPPRESS_PREFIXES):
                    continue
                # Detect quota exhaustion lines
                low = stripped.lower()
                if (
                    "exhausted your capacity" in low
                    or "quota will reset" in low
                    or ("attempt" in low and "failed" in low and "retrying" in low)
                ):
                    process.terminate()
                    raise Exception("AGENT_TRANSIENT_ERROR")
                yield line

            process.wait()
        finally:
            if process.poll() is None:
                process.terminate()

        if process.returncode != 0 and process.returncode != 1:
            raise Exception(f"Agent failed with exit code {process.returncode}")

    def _execute_gemini_with_fallback(
        self, prompt: str, cwd: str, mode: str
    ) -> Generator[str, None, None]:
        """Run Gemini, walking down _GEMINI_MODEL_LADDER on quota exhaustion."""
        for i, model in enumerate(_GEMINI_MODEL_LADDER):
            command_args, stdin_data, use_stdin = self._build_popen_args(prompt, cwd, mode, gemini_model=model)
            env = os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            env.setdefault("COLORTERM", "truecolor")
            local_cwd = _safe_cwd(cwd)
            process = subprocess.Popen(
                command_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if use_stdin else None,
                text=True,
                bufsize=0,
                cwd=local_cwd,
                env=env,
                start_new_session=True,
            )

            def _log_stderr():
                for line in process.stderr:
                    if line.strip():
                        print(f"[Gemini CLI Stderr]: {line.strip()}")

            threading.Thread(target=_log_stderr, daemon=True).start()

            quota_hit = False
            try:
                if use_stdin and stdin_data:
                    process.stdin.write(stdin_data)
                    process.stdin.close()
                for raw_line in process.stdout:
                    line = _ANSI_RE.sub("", raw_line)
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if any(stripped.startswith(p) for p in _SUPPRESS_PREFIXES):
                        continue
                    low = stripped.lower()
                    if (
                        "exhausted your capacity" in low
                        or "quota will reset" in low
                        or ("attempt" in low and "failed" in low and "retrying" in low)
                    ):
                        quota_hit = True
                        process.terminate()
                        break
                    yield line
                if not quota_hit:
                    process.wait()
                    if process.returncode != 0 and process.returncode != 1:
                        raise Exception(f"Gemini failed with exit code {process.returncode}")
                    return
            finally:
                if process.poll() is None:
                    process.terminate()

            # Quota hit — try next model in ladder
            next_model = _GEMINI_MODEL_LADDER[i + 1] if i + 1 < len(_GEMINI_MODEL_LADDER) else None
            if next_model is None and i + 1 >= len(_GEMINI_MODEL_LADDER):
                raise Exception("AGENT_TRANSIENT_ERROR")
            label = next_model or "default model"
            yield f"__STATUS__:Gemini quota exhausted — retrying with {label}...\n"

        raise Exception("AGENT_TRANSIENT_ERROR")

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

        local_cwd = _safe_cwd(cwd)
        # Only pipe stdin if we actually have data to send.
        # Otherwise, some CLIs (like codex) might hang waiting for input.
        process = subprocess.Popen(
            command_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # Silence CLI noise (like "Reading from stdin...")
            stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
            text=True,
            bufsize=0,
            cwd=local_cwd,
            env=env,
            start_new_session=True,
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
                    if stripped and not any(
                        stripped.startswith(p) for p in _SUPPRESS_PREFIXES
                    ):
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
        self, prompt: str, cwd: str, session_id: str, use_stdin: bool, mode: str = "plan"
    ) -> Generator[str, None, None]:
        """Run claude with stream-json output and MCP permission tool."""
        backend_url = "http://leadagent-backend:8000" if os.environ.get("LEADAGENT_DOCKER_MODE") else "http://localhost:8000"
        
        # Use python3 as the command in containers, sys.executable locally
        python_cmd = "python3" if os.environ.get("LEADAGENT_DOCKER_MODE") else sys.executable

        mcp_cfg = {
            "mcpServers": {
                "leadagent_perm": {
                    "command": python_cmd,
                    "args": ["-m", "backend.permission_mcp_server"],
                    "env": {
                        "LEADAGENT_SESSION_ID": session_id,
                        "LEADAGENT_BACKEND_URL": backend_url,
                        "LEADAGENT_AGENT_NAME": "claude",
                        "PYTHONPATH": "/app/leadagent-data" if os.environ.get("LEADAGENT_DOCKER_MODE") else _PROJECT_ROOT,
                    },
                },
                "leadagent_main": {
                    "command": python_cmd,
                    "args": ["-m", "backend.main_mcp_server"],
                    "env": {
                        "LEADAGENT_SESSION_ID": session_id,
                        "LEADAGENT_BACKEND_URL": backend_url,
                        "PYTHONPATH": "/app/leadagent-data" if os.environ.get("LEADAGENT_DOCKER_MODE") else _PROJECT_ROOT,
                    },
                },
                "docker": {
                    "command": "npx",
                    "args": ["-y", "@docker/mcp-server"],
                }
            }
        }
        # In Docker mode, write to the shared volume so the agent container can read it
        temp_dir = _PROJECT_ROOT if os.environ.get("LEADAGENT_DOCKER_MODE") else None
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=temp_dir) as fh:
            json.dump(mcp_cfg, fh)
            mcp_path = fh.name

        stream_flags = [
            "--output-format",
            "stream-json",
            "--verbose",
            "--mcp-config",
            mcp_path,
            # Only load LeadAgent's MCP servers — without this, claude also
            # loads the user's personal connectors (Gmail, Drive, …) from the
            # mounted ~/.claude config, exposing them to a headless agent.
            "--strict-mcp-config",
        ]

        # Map LeadAgent internal modes to claude's --permission-mode:
        #   "execute" → "acceptEdits" (file edits auto-approved; other tools
        #               still route through the leadagent_perm prompt tool)
        #   "plan"    → "default"     (LeadAgent "plan" = ask before acting —
        #               every tool call asks via the MCP prompt tool)
        claude_mode = {"execute": "acceptEdits"}.get(mode, "default")
        stream_flags += ["--permission-mode", claude_mode]

        stream_flags += [
            "--permission-prompt-tool",
            "mcp__leadagent_perm__ask_permission",
        ]

        if use_stdin:
            command_args = _build_argv("claude", stream_flags, cwd=cwd)
            stdin_data: str | None = prompt
        else:
            command_args = _build_argv("claude", ["-p", prompt] + stream_flags, cwd=cwd)
            stdin_data = None

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")

        local_cwd = _safe_cwd(cwd)
        process = subprocess.Popen(
            command_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # Capture stderr for diagnostics
            stdin=subprocess.PIPE if use_stdin else None,
            text=True,
            bufsize=0,
            cwd=local_cwd,
            env=env,
            start_new_session=True,
        )

        stderr_tail: list[str] = []

        def _log_stderr():
            for line in process.stderr:
                if line.strip():
                    print(f"[Claude CLI Stderr]: {line.strip()}")
                    stderr_tail.append(line.strip())
                    del stderr_tail[:-20]

        threading.Thread(target=_log_stderr, daemon=True).start()

        saw_result = False
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
                    # Non-JSON output (like startup info or warnings)
                    if raw.strip():
                        print(f"[Claude CLI]: {raw}")
                    continue

                etype = event.get("type")
                if etype == "assistant":
                    # Full or partial assistant message
                    for item in event.get("message", {}).get("content", []):
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "text":
                            text = item.get("text", "")
                            if text:
                                yield text
                        elif item.get("type") == "tool_use":
                            name = item.get("name", "tool")
                            hint = _tool_status_hint(item.get("input"))
                            yield f"__STATUS__:claude → {name}{hint}\n"
                elif etype == "content_block_delta":
                    # Streaming token delta
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield text
                elif etype == "result":
                    saw_result = True
                    subtype = event.get("subtype", "")
                    # The CLI emits several error subtypes (error,
                    # error_during_execution, error_max_turns, …) — treat
                    # anything that isn't an explicit success as a failure.
                    if event.get("is_error") or subtype != "success":
                        err = str(
                            event.get("result") or event.get("error") or subtype
                        )
                        detail = f"{subtype}: {err}" if subtype and subtype != err else err
                        if stderr_tail:
                            detail += " | stderr: " + " / ".join(stderr_tail[-5:])
                        print(f"[Claude CLI Error]: {detail}")
                        low = detail.lower()
                        if "rate limit" in low or "overloaded" in low or "529" in low:
                            raise Exception("AGENT_TRANSIENT_ERROR")
                        raise Exception(f"Agent error: {detail}")

        except Exception as e:
            if str(e) != "AGENT_TRANSIENT_ERROR":
                print(f"[_execute_claude_stream] Exception: {e}")
            raise e
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait()
            if mcp_path:
                try:
                    os.unlink(mcp_path)
                except OSError:
                    pass

        # Exit code 1 is only acceptable when the CLI delivered a result event;
        # a bare exit 1 with no result is a crash that used to die silently.
        if process.returncode not in (0, None) and not (
            process.returncode == 1 and saw_result
        ):
            detail = f"Agent failed with exit code {process.returncode}"
            if stderr_tail:
                detail += ". Stderr: " + " / ".join(stderr_tail[-5:])
            low = detail.lower()
            if "rate limit" in low or "overloaded" in low or "529" in low:
                raise Exception("AGENT_TRANSIENT_ERROR")
            raise Exception(detail)


import requests

class OllamaAgent:
    """Agent that talks to a local Ollama server."""
    def __init__(self, model: str = ""):
        self.model = model or os.environ.get("LEADAGENT_OLLAMA_MODEL", "llama3")
        default_host = "http://ollama:11434" if os.environ.get("LEADAGENT_DOCKER_MODE") else "http://localhost:11434"
        self.url = os.environ.get("OLLAMA_HOST", default_host)

    def execute_stream(
        self,
        prompt: str,
        cwd: str = ".",
        session_id: str = "default",
        simple: bool = False,
        mode: str = "plan",
    ) -> Generator[str, None, None]:
        try:
            response = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": True,
                },
                stream=True,
                timeout=(10, 120),
            )
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    yield data.get("response", "")
                    if data.get("done"):
                        break
        except requests.exceptions.HTTPError as he:
            if he.response.status_code == 404:
                yield f"\n[Ollama Error]: Model '{self.model}' not found. Run 'ollama pull {self.model}' to install it.\n"
            else:
                yield f"\n[Ollama Error]: HTTP {he.response.status_code} - {he.response.text}\n"
        except Exception as e:
            yield f"\n[Ollama Error]: {e}\n"

class AgentFactory:
    @staticmethod
    def get_agent(agent_name: str) -> Any:
        if agent_name == "ollama":
            return OllamaAgent()
        if agent_name in _CLI_MAP:
            return CLIAgent(_CLI_MAP[agent_name])
        raise ValueError(f"Unknown agent: {agent_name}")


agent_factory = AgentFactory()
