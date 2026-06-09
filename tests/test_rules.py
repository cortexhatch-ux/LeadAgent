"""Tests for backend/rules.py — rule evaluation, glob matching, scope filtering."""

from unittest.mock import patch

import pytest

from backend.rules import (
    _tool_matches,
    _scope_matches,
    _input_matches,
    evaluate,
)


# ── _tool_matches ────────────────────────────────────────────────────────────

class TestToolMatches:
    def test_exact_match(self):
        assert _tool_matches("write_file", "write_file") is True

    def test_exact_no_match(self):
        assert _tool_matches("write_file", "read_file") is False

    def test_glob_prefix(self):
        assert _tool_matches("bash*", "bash_execute") is True
        assert _tool_matches("bash*", "bash") is True

    def test_glob_no_match(self):
        assert _tool_matches("bash*", "fish") is False

    def test_wildcard_matches_anything(self):
        assert _tool_matches("*", "anything") is True
        assert _tool_matches("*", "") is True


# ── _scope_matches ───────────────────────────────────────────────────────────

class TestScopeMatches:
    def test_global_always_matches(self):
        assert _scope_matches("global", "claude", "s1") is True
        assert _scope_matches("global", "gemini", "s2") is True

    def test_agent_scope_match(self):
        assert _scope_matches("agent:claude", "claude", "s1") is True

    def test_agent_scope_no_match(self):
        assert _scope_matches("agent:claude", "gemini", "s1") is False

    def test_session_scope_match(self):
        assert _scope_matches("session:s1", "claude", "s1") is True

    def test_session_scope_no_match(self):
        assert _scope_matches("session:s1", "claude", "s2") is False

    def test_unknown_scope_no_match(self):
        assert _scope_matches("malformed", "claude", "s1") is False


# ── _input_matches ───────────────────────────────────────────────────────────

class TestInputMatches:
    def test_empty_string_matches_anything(self):
        assert _input_matches("", {"foo": "bar"}) is True

    def test_subset_match(self):
        assert _input_matches('{"command": "ls"}', {"command": "ls", "cwd": "/tmp"}) is True

    def test_missing_key_no_match(self):
        assert _input_matches('{"command": "ls"}', {"cwd": "/tmp"}) is False

    def test_value_mismatch_no_match(self):
        assert _input_matches('{"command": "ls"}', {"command": "rm"}) is False

    def test_malformed_json_treated_as_match(self):
        # Per the docstring: "malformed rule — don't block"
        assert _input_matches("not json", {"command": "ls"}) is True


# ── evaluate ─────────────────────────────────────────────────────────────────

def _row(tool_pattern, action, scope="global", reason="", input_match="", priority=0):
    # Schema: id, tool_pattern, action, scope, reason, input_match, priority, created_at
    return (1, tool_pattern, action, scope, reason, input_match, priority, "2024-01-01")


class TestEvaluate:
    def test_no_rules_returns_ask(self):
        with patch("backend.rules.db.list_rules", return_value=[]):
            action, reason = evaluate("Bash", {}, "claude", "s1")
        assert action == "ask"
        assert reason == ""

    def test_first_matching_rule_wins(self):
        rows = [
            _row("Bash", "allow", reason="trusted"),
            _row("Bash", "deny", reason="blocked"),
        ]
        with patch("backend.rules.db.list_rules", return_value=rows):
            action, reason = evaluate("Bash", {}, "claude", "s1")
        assert action == "allow"
        assert reason == "trusted"

    def test_wildcard_matches(self):
        rows = [_row("*", "deny", reason="catch-all")]
        with patch("backend.rules.db.list_rules", return_value=rows):
            action, _ = evaluate("anything", {}, "claude", "s1")
        assert action == "deny"

    def test_scope_filter_skips_nonmatching(self):
        rows = [
            _row("Bash", "allow", scope="agent:gemini"),
            _row("Bash", "deny", scope="global"),
        ]
        with patch("backend.rules.db.list_rules", return_value=rows):
            action, _ = evaluate("Bash", {}, "claude", "s1")
        assert action == "deny"  # first rule filtered out by scope

    def test_input_filter_required(self):
        rows = [
            _row("Bash", "allow", input_match='{"command": "ls"}'),
        ]
        with patch("backend.rules.db.list_rules", return_value=rows):
            action_ls, _ = evaluate("Bash", {"command": "ls"}, "claude", "s1")
            action_rm, _ = evaluate("Bash", {"command": "rm"}, "claude", "s1")
        assert action_ls == "allow"
        assert action_rm == "ask"

    def test_session_scope_matches(self):
        rows = [_row("Bash", "allow", scope="session:abc")]
        with patch("backend.rules.db.list_rules", return_value=rows):
            yes, _ = evaluate("Bash", {}, "claude", "abc")
            no, _ = evaluate("Bash", {}, "claude", "xyz")
        assert yes == "allow"
        assert no == "ask"
