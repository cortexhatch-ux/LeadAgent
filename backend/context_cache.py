"""
Session-scoped context cache.

Tracks which context blocks (entities, relationships, past Q&A, memory snippets)
have already been injected into a given session. Subsequent messages in the same
session only receive genuinely new context — reducing prompt size and avoiding
repeated context noise.
"""

import threading
import time
from dataclasses import dataclass, field


_SESSION_TTL = 2 * 3600  # expire idle sessions after 2 hours


@dataclass
class _SessionState:
    injected_entities: dict[str, str] = field(default_factory=dict)  # name -> tag
    injected_relations: dict[str, str] = field(default_factory=dict)  # "A->B" -> tag
    injected_qa_keys: dict[int, str] = field(default_factory=dict)  # hash -> tag
    injected_memory_keys: dict[int, str] = field(default_factory=dict)  # hash -> tag
    last_active: float = field(default_factory=time.time)

    def touch(self):
        self.last_active = time.time()


class ContextCache:
    """
    Thread-safe, session-scoped context deduplication.

    Usage:
        cache = ContextCache()

        # In check_memory — build blocks, then filter:
        new_entities = cache.filter_entities(session_id, all_entities)
        new_relations = cache.filter_relations(session_id, all_relations)
        ...
        cache.commit(session_id, new_entities, new_relations, ...)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionState] = {}

    def _get(self, session_id: str) -> _SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = _SessionState()
        s = self._sessions[session_id]
        s.touch()
        return s

    def _expire(self):
        now = time.time()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active > _SESSION_TTL
        ]
        for sid in expired:
            del self._sessions[sid]

    # ── filter methods (call before building context blocks) ─────────────────

    def filter_entities(self, session_id: str, rows: list) -> list:
        """rows: [(name, type, description), ...] — returns only unseen ones."""
        with self._lock:
            s = self._get(session_id)
            return [r for r in rows if r[0] not in s.injected_entities]

    def filter_relations(self, session_id: str, rows: list) -> list:
        """rows: [(src, target_name, target_type, desc, rel_type), ...] — returns only unseen."""
        with self._lock:
            s = self._get(session_id)
            return [r for r in rows if f"{r[0]}->{r[1]}" not in s.injected_relations]

    def filter_qa(self, session_id: str, rows: list) -> list:
        """rows: [(prompt, answer, agent), ...] — returns only unseen."""
        with self._lock:
            s = self._get(session_id)
            return [r for r in rows if _hsh(r[0]) not in s.injected_qa_keys]

    def filter_memory(self, session_id: str, snippets: list[str]) -> list[str]:
        """snippets: list of semantic memory strings — returns only unseen."""
        with self._lock:
            s = self._get(session_id)
            return [m for m in snippets if _hsh(m) not in s.injected_memory_keys]

    # ── commit (call after context was actually injected) ────────────────────

    def commit(
        self,
        session_id: str,
        entities: list = (),
        relations: list = (),
        qa_rows: list = (),
        memory_snippets: list = (),
        tag: str = "default",
    ):
        """Mark these items as injected so they're skipped next time."""
        with self._lock:
            s = self._get(session_id)
            for r in entities:
                s.injected_entities[r[0]] = tag
            for r in relations:
                s.injected_relations[f"{r[0]}->{r[1]}"] = tag
            for r in qa_rows:
                s.injected_qa_keys[_hsh(r[0])] = tag
            for m in memory_snippets:
                s.injected_memory_keys[_hsh(m)] = tag
            self._expire()

    def prune_by_tag(self, session_id: str, tag: str):
        """Remove all injected blocks associated with a specific tag."""
        with self._lock:
            if session_id not in self._sessions:
                return
            s = self._sessions[session_id]
            s.injected_entities = {
                k: v for k, v in s.injected_entities.items() if v != tag
            }
            s.injected_relations = {
                k: v for k, v in s.injected_relations.items() if v != tag
            }
            s.injected_qa_keys = {
                k: v for k, v in s.injected_qa_keys.items() if v != tag
            }
            s.injected_memory_keys = {
                k: v for k, v in s.injected_memory_keys.items() if v != tag
            }

    def invalidate(self, session_id: str):
        """Force a full re-injection on next message (e.g. after /clear)."""
        with self._lock:
            self._sessions.pop(session_id, None)

    def stats(self, session_id: str) -> dict:
        with self._lock:
            if session_id not in self._sessions:
                return {"active": False}
            s = self._sessions[session_id]
            return {
                "active": True,
                "injected_entities": len(s.injected_entities),
                "injected_relations": len(s.injected_relations),
                "injected_qa": len(s.injected_qa_keys),
                "injected_memory": len(s.injected_memory_keys),
                "idle_seconds": int(time.time() - s.last_active),
            }


def _hsh(text: str) -> int:
    """Stable hash for deduplication — not cryptographic, just fast."""
    return hash(text[:200])


context_cache = ContextCache()
