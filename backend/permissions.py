"""In-memory permission broker.

Mediates tool-call approvals between sub-agents (claude/codex/gemini/grok) and
the user. Sub-agents request permission via the stdio MCP server
(permission_mcp_server.py), which calls into the backend HTTP API; the user's
decision is delivered back via /permission/{id}/decide from the CLI.
"""

import queue
import threading
import time
import uuid
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class PermissionRequest:
    id: str
    session_id: str
    agent: str
    tool_name: str
    input: Dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    decision: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)


class PermissionBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, PermissionRequest] = {}
        self._session_queues: Dict[str, "queue.Queue[PermissionRequest]"] = {}
        self._allow_cache: Dict[Tuple[str, str, str, str], bool] = {}
        self._interrupts: set = set()

    # ── mid-run interrupt: force the next tool call to prompt the user ─────
    def set_interrupt(self, session_id: str) -> None:
        with self._lock:
            self._interrupts.add(session_id)

    def consume_interrupt(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._interrupts:
                self._interrupts.discard(session_id)
                return True
            return False

    # ── session-scoped queues (chat stream subscribes here) ────────────────
    def session_queue(self, session_id: str) -> "queue.Queue[PermissionRequest]":
        with self._lock:
            q = self._session_queues.get(session_id)
            if q is None:
                q = queue.Queue()
                self._session_queues[session_id] = q
            return q

    # ── allow-for-session cache ────────────────────────────────────────────
    @staticmethod
    def _fingerprint(tool_name: str, input_: Dict[str, Any]) -> str:
        """Coarse fingerprint so 'allow for session' applies to similar calls.

        For Bash-like tools, fingerprint by the first token of the command so
        e.g. allowing `gemini --help` permits subsequent `gemini ...` calls.
        For file-touching tools, fingerprint by the file path's directory.
        """
        if isinstance(input_, dict):
            cmd = input_.get("command")
            if isinstance(cmd, str) and cmd.strip():
                return cmd.strip().split()[0]
            fp = input_.get("file_path") or input_.get("path")
            if isinstance(fp, str) and fp:
                return fp.rsplit("/", 1)[0] or fp
        return ""

    def _is_high_risk(self, tool_name: str, input_: Dict[str, Any]) -> bool:
        """Identify commands that should NEVER be auto-approved via 'allow session'."""
        if tool_name.lower() == "bash":
            cmd = str(input_.get("command", "")).lower().strip()
            # sudo/root operations
            if "sudo " in cmd or cmd.startswith("sudo"):
                return True
            # Deletions
            if "rm " in cmd or cmd.startswith("rm"):
                return True
            # System modification risks (crontab, chmod, etc.)
            risky_bins = ("chmod", "chown", "crontab", "reboot", "shutdown")
            if any(cmd.startswith(bin) or f" {bin} " in cmd for bin in risky_bins):
                return True

            # Pip installs MUST use the LeadAgent venv
            if ("pip " in cmd or "pip3 " in cmd) and "install " in cmd:
                # If it's a bare 'pip install' or 'pip3 install' without the venv path, it's high risk
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                venv_path = os.path.join(project_root, "leadagent")
                if venv_path.lower() not in cmd:
                    return True
        return False

    def is_allowed(
        self, session_id: str, agent: str, tool_name: str, input_: Dict[str, Any]
    ) -> bool:
        # High-risk commands ALWAYS require a manual 'once' approval
        if self._is_high_risk(tool_name, input_):
            return False

        with self._lock:
            # 1. Check for a session-wide tool allow (highest precedence, agent-agnostic)
            if self._allow_cache.get((session_id, "*", tool_name, "*"), False):
                return True
            # 2. Check for an agent-specific tool allow
            if self._allow_cache.get((session_id, agent, tool_name, "*"), False):
                return True
            # 3. Check for the specific fingerprint (legacy/conservative)
            key = (session_id, agent, tool_name, self._fingerprint(tool_name, input_))
            return self._allow_cache.get(key, False)

    def remember_allow(
        self, session_id: str, agent: str, tool_name: str, input_: Dict[str, Any], scope: str = "session"
    ) -> None:
        with self._lock:
            if scope == "session":
                # Broad allow: this tool is trusted for ALL agents in this session
                self._allow_cache[(session_id, "*", tool_name, "*")] = True
            else:
                # Targeted allow: this specific command fingerprint for this agent
                key = (session_id, agent, tool_name, self._fingerprint(tool_name, input_))
                self._allow_cache[key] = True

    # ── request lifecycle ──────────────────────────────────────────────────
    def create(
        self, session_id: str, agent: str, tool_name: str, input_: Dict[str, Any]
    ) -> PermissionRequest:
        pr = PermissionRequest(
            id=uuid.uuid4().hex,
            session_id=session_id,
            agent=agent,
            tool_name=tool_name,
            input=input_,
        )
        with self._lock:
            self._pending[pr.id] = pr
        self.session_queue(session_id).put(pr)
        return pr

    def wait(self, request_id: str, timeout: float = 600.0) -> Optional[Dict[str, Any]]:
        with self._lock:
            pr = self._pending.get(request_id)
        if pr is None:
            return None
        pr.event.wait(timeout)
        return pr.decision

    def decide(
        self,
        request_id: str,
        behavior: str,
        scope: str = "once",
        message: Optional[str] = None,
        updated_input: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """behavior: 'allow' | 'deny' | 'stop'. scope: 'once' | 'session' | 'stop'."""
        with self._lock:
            pr = self._pending.get(request_id)
            if pr is None:
                return False
            pr.decision = {
                "behavior": behavior,
                "scope": scope,
                "message": message,
                "updatedInput": updated_input
                if updated_input is not None
                else pr.input,
            }
        if behavior == "allow" and scope == "session":
            self.remember_allow(pr.session_id, pr.agent, pr.tool_name, pr.input, scope="session")
        pr.event.set()
        return True

    def get(self, request_id: str) -> Optional[PermissionRequest]:
        with self._lock:
            return self._pending.get(request_id)


broker = PermissionBroker()
