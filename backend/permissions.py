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

    def is_allowed(self, session_id: str, agent: str, tool_name: str, input_: Dict[str, Any]) -> bool:
        key = (session_id, agent, tool_name, self._fingerprint(tool_name, input_))
        with self._lock:
            return self._allow_cache.get(key, False)

    def remember_allow(self, session_id: str, agent: str, tool_name: str, input_: Dict[str, Any]) -> None:
        key = (session_id, agent, tool_name, self._fingerprint(tool_name, input_))
        with self._lock:
            self._allow_cache[key] = True

    # ── request lifecycle ──────────────────────────────────────────────────
    def create(self, session_id: str, agent: str, tool_name: str, input_: Dict[str, Any]) -> PermissionRequest:
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
                "updatedInput": updated_input if updated_input is not None else pr.input,
            }
        if behavior == "allow" and scope == "session":
            self.remember_allow(pr.session_id, pr.agent, pr.tool_name, pr.input)
        pr.event.set()
        return True

    def get(self, request_id: str) -> Optional[PermissionRequest]:
        with self._lock:
            return self._pending.get(request_id)


broker = PermissionBroker()
