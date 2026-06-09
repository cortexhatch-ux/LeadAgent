"""Tests for backend/tool_registry.py — seed_default_rules."""

from unittest.mock import MagicMock

from backend import tool_registry
from backend.tool_registry import (
    ALL_TOOLS,
    CLAUDE_TOOLS,
    GEMINI_TOOLS,
    LEADAGENT_TOOLS,
    seed_default_rules,
)


def test_all_tools_is_union():
    assert ALL_TOOLS == CLAUDE_TOOLS + GEMINI_TOOLS + LEADAGENT_TOOLS


def test_known_claude_tools_present():
    names = {n for n, _ in CLAUDE_TOOLS}
    for t in ("Bash", "Read", "Write", "Edit", "Grep"):
        assert t in names


def test_known_gemini_tools_present():
    names = {n for n, _ in GEMINI_TOOLS}
    assert "run_shell_command" in names
    assert "write_file" in names


def test_seed_default_rules_inserts_when_empty(monkeypatch):
    fake_db = MagicMock()
    fake_db.list_rules.return_value = []
    fake_db.add_rule.return_value = None
    monkeypatch.setattr(tool_registry, "db", fake_db)

    added = seed_default_rules()
    assert added == len(ALL_TOOLS)
    assert fake_db.add_rule.call_count == len(ALL_TOOLS)

    # All inserts use action="ask" / scope="global"
    for call in fake_db.add_rule.call_args_list:
        kwargs = call.kwargs
        assert kwargs["action"] == "ask"
        assert kwargs["scope"] == "global"
        assert kwargs["priority"] == 0


def test_seed_default_rules_skips_existing(monkeypatch):
    fake_db = MagicMock()
    # list_rules returns (id, tool_pattern, ...) tuples — index [1] is the pattern
    fake_db.list_rules.return_value = [
        (1, name, "ask", "global", "x", 0) for name, _ in ALL_TOOLS
    ]
    monkeypatch.setattr(tool_registry, "db", fake_db)

    added = seed_default_rules()
    assert added == 0
    fake_db.add_rule.assert_not_called()


def test_seed_default_rules_partial(monkeypatch):
    fake_db = MagicMock()
    fake_db.list_rules.return_value = [(1, "Bash", "ask", "global", "x", 0)]
    monkeypatch.setattr(tool_registry, "db", fake_db)

    added = seed_default_rules()
    assert added == len(ALL_TOOLS) - 1
    # Bash must not be inserted
    inserted = {c.kwargs["tool_pattern"] for c in fake_db.add_rule.call_args_list}
    assert "Bash" not in inserted
