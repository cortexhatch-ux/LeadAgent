"""Tests for backend/router.py — _classify_task and AgentRouter.route."""

import contextlib

import pytest
from unittest.mock import patch, MagicMock

from backend.router import _classify_task, AgentRouter


# ── _classify_task ────────────────────────────────────────────────────────────

class TestClassifyTask:
    def test_coding_prompt(self):
        assert _classify_task("Write a Python function to sort a list") == "coding"

    def test_research_prompt(self):
        assert _classify_task("What is the history of the internet?") == "research"

    def test_deep_analysis_prompt(self):
        assert _classify_task("Review this code for security vulnerabilities and performance") == "deep_analysis"

    def test_long_context_prompt(self):
        assert _classify_task("Summarize this entire document for me") == "long_context"

    def test_creative_prompt(self):
        assert _classify_task("Write a blog post about machine learning") == "creative"

    def test_logic_prompt(self):
        assert _classify_task("Solve this math equation: x^2 + 3x - 4 = 0") == "logic"

    def test_general_fallback(self):
        assert _classify_task("hello") == "general"

    def test_empty_string_fallback(self):
        assert _classify_task("") == "general"

    def test_case_insensitive(self):
        assert _classify_task("DEBUG this CODE") == "coding"

    def test_most_frequent_wins(self):
        # More coding keywords than research
        result = _classify_task("Write a function to implement an algorithm, fix the bug, refactor the class")
        assert result == "coding"


# ── AgentRouter.route ─────────────────────────────────────────────────────────

@contextlib.contextmanager
def _make_router(available=None, enabled=None, authenticated=None):
    """Yield an AgentRouter with mocked dependencies."""
    router = AgentRouter()
    if available is None:
        available = ["claude", "gemini", "codex", "grok"]
    if enabled is None:
        enabled = {"claude", "gemini", "codex", "grok"}
    if authenticated is None:
        authenticated = {}

    with (
        patch("backend.router.quota_manager.get_available_agents", return_value=available),
        patch("backend.router._cli_available", side_effect=lambda a: a in available),
        patch("backend.router.enabled_agents", return_value=enabled),
        patch("backend.router.is_authenticated", side_effect=lambda a: authenticated.get(a, None)),
    ):
        yield router


@pytest.fixture
def router():
    with _make_router() as r:
        yield r


class TestRoute:
    def test_preferred_agent_returned_when_available(self):
        with _make_router() as router:
            result = router.route("general", "hello", preferred_agent="gemini")
            assert result == "gemini"

    def test_preferred_agent_ignored_when_unavailable(self):
        with _make_router(available=["claude"]) as router:
            result = router.route("coding", "fix the bug", preferred_agent="gemini")
            assert result == "claude"

    def test_returns_none_when_no_agents_available(self):
        with _make_router(available=[]) as router:
            result = router.route("coding", "fix the bug")
            assert result == "none"

    def test_coding_task_prefers_claude(self):
        with _make_router() as router:
            result = router.route("coding", "")
            assert result == "claude"

    def test_research_task_prefers_gemini(self):
        with _make_router() as router:
            result = router.route("research", "")
            assert result == "gemini"

    def test_general_with_coding_prompt_classifies_and_routes(self):
        with _make_router() as router:
            result = router.route("general", "Write a Python function")
            assert result == "claude"

    def test_falls_back_to_first_available(self):
        with _make_router(available=["grok"]) as router:
            result = router.route("coding", "")
            assert result == "grok"  # grok doesn't do coding but it's all that's available

    def test_disabled_agent_excluded(self):
        with _make_router(enabled={"gemini"}) as router:
            result = router.route("coding", "")
            assert result == "gemini"  # claude disabled

    def test_unauthenticated_agent_excluded(self):
        with _make_router(authenticated={"claude": False}) as router:
            result = router.route("coding", "")
            assert result != "claude"

    def test_unknown_auth_agent_included(self):
        with _make_router(authenticated={"claude": None}) as router:
            result = router.route("coding", "")
            assert result == "claude"  # None = unknown, treated as available


# ── AgentRouter.learn_from_prompt ─────────────────────────────────────────────

class TestLearnFromPrompt:
    def test_does_not_raise_on_db_error(self):
        router = AgentRouter()
        with patch("backend.router.db") as mock_db:
            mock_db.query_all.side_effect = Exception("db error")
            mock_db.add_entity.side_effect = Exception("db error")
            mock_db.add_question.side_effect = Exception("db error")
            # Should swallow the exception
            router.learn_from_prompt("what is foo", "foo is bar", "claude")

    def test_extracts_snake_case_entities(self):
        router = AgentRouter()
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            router.learn_from_prompt(
                "how does permission_broker work",
                "permission_broker manages the queue",
                "claude",
            )
        names = [c[0] for c in calls]
        assert any("permission_broker" in n for n in names)

    def test_extracts_camel_case_entities(self):
        router = AgentRouter()
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            router.learn_from_prompt(
                "explain FastAPI routing",
                "FastAPI uses decorators for routing",
                "claude",
            )
        names = [c[0] for c in calls]
        assert any("FastAPI" in n for n in names)
