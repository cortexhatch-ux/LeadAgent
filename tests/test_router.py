"""Tests for backend/router.py — _classify_task, _detect_execute_mode, AgentRouter."""

import pytest
from unittest.mock import patch, MagicMock

from backend.router import (
    _classify_task,
    _detect_execute_mode,
    AgentRouter,
)
from backend.models import ErrorType


# ── _classify_task ─────────────────────────────────────────────────────────────

class TestClassifyTask:
    def test_coding_prompt(self):
        task, _ = _classify_task("Write a Python function to sort a list")
        assert task == "coding"

    def test_research_prompt(self):
        task, _ = _classify_task("What is the history of the internet?")
        assert task == "research"

    def test_deep_analysis_prompt(self):
        task, _ = _classify_task("Review this code for security vulnerabilities and performance")
        assert task == "deep_analysis"

    def test_long_context_prompt(self):
        task, _ = _classify_task("Summarize this entire document for me")
        assert task == "long_context"

    def test_creative_prompt(self):
        task, _ = _classify_task("Write a blog post about machine learning")
        assert task == "creative"

    def test_logic_prompt(self):
        task, _ = _classify_task("Solve this math equation: x^2 + 3x - 4 = 0")
        assert task == "logic"

    def test_general_fallback(self):
        task, _ = _classify_task("hello")
        assert task == "general"

    def test_empty_string_fallback(self):
        task, _ = _classify_task("")
        assert task == "general"

    def test_case_insensitive(self):
        task, _ = _classify_task("DEBUG this CODE")
        assert task == "coding"

    def test_most_frequent_wins(self):
        task, _ = _classify_task("Write a function to implement an algorithm, fix the bug, refactor the class")
        assert task == "coding"

    def test_complexity_low(self):
        _, complexity = _classify_task("hello")
        assert complexity == "low"

    def test_complexity_medium(self):
        _, complexity = _classify_task("write a function to implement an algorithm and debug the code")
        assert complexity == "medium"

    def test_complexity_high(self):
        prompt = "code code code code code code function function function function function function debug"
        _, complexity = _classify_task(prompt)
        assert complexity == "high"


# ── _detect_execute_mode ───────────────────────────────────────────────────────

class TestDetectExecuteMode:
    def test_file_edit_returns_execute(self):
        assert _detect_execute_mode("edit the README.md file") == "execute"

    def test_git_push_returns_execute(self):
        assert _detect_execute_mode("push the changes to main") == "execute"

    def test_run_server_returns_execute(self):
        assert _detect_execute_mode("run the backend server") == "execute"

    def test_question_returns_plan(self):
        assert _detect_execute_mode("what is the capital of France?") == "plan"

    def test_explain_returns_plan(self):
        assert _detect_execute_mode("explain how async works in Python") == "plan"

    def test_empty_returns_plan(self):
        assert _detect_execute_mode("") == "plan"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_route_deps(available=None, enabled=None, authenticated=None):
    """Return a stack of patches for route-related dependencies."""
    if available is None:
        available = ["claude", "gemini", "codex", "grok"]
    if enabled is None:
        enabled = set(available)
    if authenticated is None:
        authenticated = {}

    return [
        patch("backend.router._cli_available", side_effect=lambda a: a in available),
        patch("backend.router.enabled_agents", return_value=enabled),
        patch("backend.router.is_authenticated", side_effect=lambda a: authenticated.get(a, None)),
        patch("backend.router._classify_task_slm", return_value=None),  # skip Ollama
        patch("backend.router.db.query_all", return_value=[]),           # skip KuzuDB affinity
    ]


# ── AgentRouter.route ──────────────────────────────────────────────────────────

class TestRoute:
    def test_preferred_agent_returned_when_available(self):
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("general", "hello", preferred_agent="gemini")
            assert result == "gemini"

    def test_preferred_agent_ignored_when_unavailable(self):
        patches = _mock_route_deps(available=["claude"])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("coding", "fix the bug", preferred_agent="gemini")
            assert result == "claude"

    def test_returns_none_when_no_agents_available(self):
        patches = _mock_route_deps(available=[], enabled=set())
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("coding", "fix the bug")
            assert result == "none"

    def test_coding_task_prefers_claude(self):
        # Exclude codex so claude is the only coding-capable agent
        patches = _mock_route_deps(available=["claude", "gemini", "grok"], enabled={"claude", "gemini", "grok"})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("coding", "")
            assert result == "claude"

    def test_research_task_routes_to_capable_agent(self):
        # Both gemini and grok handle research; either is a valid result
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("research", "")
            assert result in ("gemini", "grok")

    def test_general_with_coding_prompt_classifies_and_routes(self):
        # Exclude codex so claude is the only coding-capable agent
        patches = _mock_route_deps(available=["claude", "gemini", "grok"], enabled={"claude", "gemini", "grok"})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("general", "Write a Python function")
            assert result == "claude"

    def test_falls_back_to_first_available(self):
        patches = _mock_route_deps(available=["grok"], enabled={"grok"})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("coding", "")
            assert result == "grok"

    def test_disabled_agent_excluded(self):
        patches = _mock_route_deps(available=["claude", "gemini"], enabled={"gemini"})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("coding", "")
            assert result == "gemini"

    def test_unauthenticated_agent_excluded(self):
        patches = _mock_route_deps(
            available=["claude", "gemini"],
            authenticated={"claude": False},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("coding", "")
            assert result != "claude"

    def test_unknown_auth_agent_included(self):
        # None = unknown auth → agent should still be included
        patches = _mock_route_deps(
            available=["claude", "gemini", "grok"],
            enabled={"claude", "gemini", "grok"},
            authenticated={"claude": None},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("coding", "")
            assert result == "claude"

    def test_explicit_agent_name_in_prompt_routes_to_it(self):
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = AgentRouter().route("general", "use gemini to explain this")
            assert result == "gemini"


# ── AgentRouter.route_multi ────────────────────────────────────────────────────

class TestRouteMulti:
    def test_fan_out_on_both_keyword(self):
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            agents, _ = AgentRouter().route_multi("general", "ask both claude and gemini")
            assert "claude" in agents
            assert "gemini" in agents

    def test_returns_metadata_with_mode(self):
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            _, meta = AgentRouter().route_multi("coding", "edit the main.py file")
            assert meta.get("mode") == "execute"

    def test_single_agent_list_for_non_fanout(self):
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            agents, _ = AgentRouter().route_multi("coding", "")
            assert isinstance(agents, list)
            assert len(agents) >= 1


# ── AgentRouter.get_fallback ───────────────────────────────────────────────────

class TestGetFallback:
    def test_context_overflow_falls_back_to_gemini(self):
        with (
            patch("backend.router.enabled_agents", return_value={"gemini"}),
            patch("backend.router.is_authenticated", return_value=None),
        ):
            result = AgentRouter().get_fallback("claude", ErrorType.CONTEXT_OVERFLOW)
            assert result == "gemini"

    def test_fallback_excluded_when_not_enabled(self):
        with (
            patch("backend.router.enabled_agents", return_value={"claude"}),
            patch("backend.router.is_authenticated", return_value=None),
        ):
            result = AgentRouter().get_fallback("claude", ErrorType.CONTEXT_OVERFLOW)
            assert result is None

    def test_no_fallback_for_unknown_pair(self):
        with (
            patch("backend.router.enabled_agents", return_value={"claude", "gemini"}),
            patch("backend.router.is_authenticated", return_value=None),
        ):
            result = AgentRouter().get_fallback("ollama", ErrorType.CONTEXT_OVERFLOW)
            assert result is None

    def test_transient_capacity_claude_to_gemini(self):
        with (
            patch("backend.router.enabled_agents", return_value={"gemini"}),
            patch("backend.router.is_authenticated", return_value=None),
        ):
            result = AgentRouter().get_fallback("claude", ErrorType.TRANSIENT_CAPACITY)
            assert result == "gemini"


# ── AgentRouter.detect_parallel ───────────────────────────────────────────────

class TestDetectParallel:
    def test_parallel_keyword(self):
        assert AgentRouter().detect_parallel("run these in parallel") is True

    def test_simultaneously_keyword(self):
        assert AgentRouter().detect_parallel("do both simultaneously") is True

    def test_no_parallel_keywords(self):
        assert AgentRouter().detect_parallel("just fix the bug") is False


# ── AgentRouter.learn_from_prompt ─────────────────────────────────────────────

class TestLearnFromPrompt:
    def test_does_not_raise_on_db_error(self):
        with patch("backend.router.db") as mock_db:
            mock_db.add_question.side_effect = Exception("db error")
            AgentRouter().learn_from_prompt("what is foo", "foo is bar", "claude")

    def test_extracts_snake_case_entities(self):
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "how does permission_broker work",
                "permission_broker manages the queue",
                "claude",
            )
        names = [c[0] for c in calls]
        assert any("permission_broker" in n for n in names)

    def test_extracts_camel_case_entities(self):
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "explain FastAPI routing",
                "FastAPI uses decorators for routing",
                "claude",
            )
        names = [c[0] for c in calls]
        assert any("FastAPI" in n for n in names)

    def test_strips_conversation_history_prefix(self):
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "[Conversation so far]\nUser: hi\nAssistant: hello\nUser: explain snake_case_naming",
                "snake_case_naming is a convention",
                "gemini",
            )
        names = [c[0] for c in calls]
        assert any("snake_case_naming" in n for n in names)


# ── AgentRouter._infer_agents_from_prompt ─────────────────────────────────────

class TestInferAgentsFromPrompt:
    def setup_method(self):
        self.router = AgentRouter()

    def test_single_named_agent(self):
        assert self.router._infer_agents_from_prompt("use claude for this") == ["claude"]

    def test_fanout_both_agents(self):
        result = self.router._infer_agents_from_prompt("compare claude and gemini")
        assert "claude" in result
        assert "gemini" in result

    def test_no_agent_named(self):
        assert self.router._infer_agents_from_prompt("fix the bug") == []

    def test_leading_agent_name(self):
        assert self.router._infer_agents_from_prompt("gemini, explain this") == ["gemini"]
