"""Test configuration — mock heavy dependencies before any backend modules import them.

KuzuDB acquires an exclusive file lock, so we stub it out entirely.
QuotaManager writes to disk; tests get a tmp-path instance.
"""

import sys
from unittest.mock import MagicMock, patch

# ── stub kuzu before any backend module imports it ────────────────────────────
# This must run before collection so db.py never touches the real lock file.
_kuzu_stub = MagicMock()
_kuzu_stub.Database.return_value = MagicMock()
_kuzu_stub.Connection.return_value = MagicMock()
sys.modules.setdefault("kuzu", _kuzu_stub)

# ── stub agentmemory client (network call at import) ─────────────────────────
_mem_stub = MagicMock()
sys.modules.setdefault("agentmemory", _mem_stub)

import pytest


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Replace the module-level db singleton with a safe mock."""
    mock_db = MagicMock()
    mock_db.query_all.return_value = []
    mock_db.add_entity.return_value = None
    mock_db.add_question.return_value = "q-stub"
    mock_db.link_question_to_entity.return_value = None
    monkeypatch.setattr("backend.db.db", mock_db, raising=False)
    # Also patch it wherever it's imported directly
    for mod_name in list(sys.modules):
        if mod_name.startswith("backend."):
            mod = sys.modules[mod_name]
            if hasattr(mod, "db") and mod.db is not mock_db:
                try:
                    monkeypatch.setattr(mod, "db", mock_db, raising=False)
                except Exception:
                    pass
    return mock_db


@pytest.fixture(autouse=True)
def _mock_memory_client(monkeypatch):
    """Replace the memory_client singleton with a safe mock."""
    mock_mc = MagicMock()
    mock_mc.search.return_value = []
    mock_mc.store.return_value = None
    monkeypatch.setattr("backend.memory_client.memory_client", mock_mc, raising=False)
    for mod_name in list(sys.modules):
        if mod_name.startswith("backend."):
            mod = sys.modules[mod_name]
            if hasattr(mod, "memory_client") and mod.memory_client is not mock_mc:
                try:
                    monkeypatch.setattr(mod, "memory_client", mock_mc, raising=False)
                except Exception:
                    pass
    return mock_mc


@pytest.fixture(autouse=True)
def _mock_context_cache(monkeypatch):
    """Replace context_cache with a mock that passes everything through."""
    mock_cc = MagicMock()
    mock_cc.filter_memory.side_effect = lambda session_id, items: items
    mock_cc.filter_entities.side_effect = lambda session_id, rows: rows
    mock_cc.filter_relations.side_effect = lambda session_id, rows: rows
    mock_cc.filter_qa.side_effect = lambda session_id, rows: rows
    mock_cc.commit.return_value = None
    mock_cc.invalidate.return_value = None
    mock_cc.stats.return_value = {}
    monkeypatch.setattr("backend.context_cache.context_cache", mock_cc, raising=False)
    for mod_name in list(sys.modules):
        if mod_name.startswith("backend."):
            mod = sys.modules[mod_name]
            if hasattr(mod, "context_cache") and mod.context_cache is not mock_cc:
                try:
                    monkeypatch.setattr(mod, "context_cache", mock_cc, raising=False)
                except Exception:
                    pass
    return mock_cc
