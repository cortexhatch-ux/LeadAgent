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

    # Issue #2: expansion phrases must trigger execute so Claude gets acceptEdits,
    # not dangerously-skip-permissions.
    def test_implement_suggestions_returns_execute(self):
        assert _detect_execute_mode("implement the suggestions") == "execute"

    def test_apply_recommendations_returns_execute(self):
        assert _detect_execute_mode("apply the recommendations") == "execute"

    def test_go_ahead_returns_execute(self):
        assert _detect_execute_mode("go ahead") == "execute"

    def test_proceed_returns_execute(self):
        assert _detect_execute_mode("proceed") == "execute"

    def test_do_it_returns_execute(self):
        assert _detect_execute_mode("do it") == "execute"

    def test_ship_it_returns_execute(self):
        assert _detect_execute_mode("ship it") == "execute"

    def test_make_the_changes_returns_execute(self):
        assert _detect_execute_mode("make the changes") == "execute"

    def test_apply_this_returns_execute(self):
        assert _detect_execute_mode("apply this") == "execute"


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

    def test_slm_low_complexity_returns_single_agent(self):
        """SLM recommending multiple agents should be trimmed to one for low complexity."""
        slm_result = {
            "task_type": "creative",
            "complexity": "low",
            "recommended_agents": ["claude", "gemini"],
            "mode": "plan",
        }
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], \
             patch("backend.router._classify_task_slm", return_value=slm_result), \
             patches[4]:
            agents, meta = AgentRouter().route_multi("creative", "write a short poem")
            assert len(agents) == 1, f"Expected 1 agent for low complexity, got {agents}"

    def test_slm_high_complexity_allows_fanout(self):
        """SLM recommending multiple agents should be kept when multi_agent_explicit=True."""
        slm_result = {
            "task_type": "deep_analysis",
            "complexity": "high",
            "recommended_agents": ["claude", "gemini"],
            "mode": "plan",
            "multi_agent_explicit": True,
        }
        patches = _mock_route_deps()
        with patches[0], patches[1], patches[2], \
             patch("backend.router._classify_task_slm", return_value=slm_result), \
             patches[4]:
            agents, _ = AgentRouter().route_multi("deep_analysis", "analyse the entire codebase architecture")
            assert len(agents) == 2, f"Expected 2 agents for high complexity, got {agents}"


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
            AgentRouter().learn_from_prompt("what is foo", "foo is bar", "claude", project_id="proj-err")

    def test_extracts_snake_case_entities(self):
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a, **kw: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "how does permission_broker work",
                "permission_broker manages the queue",
                "claude",
                project_id="proj-test",
            )
        names = [c[0] for c in calls]
        assert any("permission_broker" in n for n in names)

    def test_extracts_camel_case_entities(self):
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a, **kw: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "explain FastAPI routing",
                "FastAPI uses decorators for routing",
                "claude",
                project_id="proj-test",
            )
        names = [c[0] for c in calls]
        assert any("FastAPI" in n for n in names)

    def test_strips_conversation_history_prefix(self):
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a, **kw: calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "[Conversation so far]\nUser: hi\nAssistant: hello\nUser: explain snake_case_naming",
                "snake_case_naming is a convention",
                "gemini",
                project_id="proj-test",
            )
        names = [c[0] for c in calls]
        assert any("snake_case_naming" in n for n in names)

    def test_blocklist_filters_sensitive_entity_names(self):
        """Entity names matching the security blocklist must not be written to the brain."""
        calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a, **kw: calls.append(a[0])),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "the password is SuperSecret and the api_key is ABCD1234",
                "password=hunter2 api_key=sk-1234 SecureToken AdminPassword",
                "claude",
                project_id="proj-test",
            )
        # None of the blocked names should have been written
        blocked = [n for n in calls if any(kw in n.lower() for kw in ("password", "secret", "api_key", "token", "admin"))]
        assert blocked == [], f"Blocked names leaked to brain: {blocked}"

    def test_prompt_truncated_to_8000_chars(self):
        """Oversized prompts must be truncated before brain regex work."""
        huge = "x" * 20000
        entity_calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a, **kw: entity_calls.append(a)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            # Should not raise or hang
            AgentRouter().learn_from_prompt(huge, huge, "claude", project_id="proj-test")

    def test_default_project_id_skips_all_writes(self):
        """learn_from_prompt with project_id='default' must not write any entities."""
        with (
            patch("backend.router.db.add_entity") as mock_add_entity,
            patch("backend.router.db.add_question") as mock_add_question,
        ):
            AgentRouter().learn_from_prompt("explain FastAPI", "FastAPI is a framework", "claude")
        mock_add_entity.assert_not_called()
        mock_add_question.assert_not_called()

    def test_auto_extracted_flag_set(self):
        """learn_from_prompt must pass auto_extracted=True to db.add_entity."""
        kw_calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a, **kw: kw_calls.append(kw)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "explain FastAPI routing",
                "FastAPI uses decorators",
                "claude",
                project_id="myproject",
            )
        if kw_calls:
            assert all(kw.get("auto_extracted") is True for kw in kw_calls), kw_calls


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


# ── AgentRouter.learn_from_prompt — project_id propagation ───────────────────

class TestLearnFromPromptProjectID:
    def test_project_id_passed_to_add_entity(self):
        entity_calls = []
        with (
            patch("backend.router.db.add_entity", side_effect=lambda *a, **kw: entity_calls.append(kw)),
            patch("backend.router.db.add_question", return_value="q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt(
                "explain FastAPI routing",
                "FastAPI uses decorators",
                "claude",
                project_id="proj-x",
            )
        assert all(c.get("source_project_id") == "proj-x" for c in entity_calls), entity_calls

    def test_project_id_passed_to_add_question(self):
        q_calls = []
        with (
            patch("backend.router.db.add_entity"),
            patch("backend.router.db.add_question", side_effect=lambda *a, **kw: q_calls.append(kw) or "q1"),
            patch("backend.router.db.link_question_to_entity"),
        ):
            AgentRouter().learn_from_prompt("hi", "hello", "claude", project_id="proj-y")
        assert q_calls[0].get("source_project_id") == "proj-y"


# ── check_memory scoping ──────────────────────────────────────────────────────

class TestCheckMemoryScoping:
    def _make_router(self):
        return AgentRouter()

    def test_strict_scope_uses_pid_clause(self):
        queries = []
        with (
            patch("backend.router.db.query_all", side_effect=lambda q, p=None: queries.append((q, p)) or []),
            patch("backend.router.memory_client.search", return_value=[]),
            patch("backend.router.context_cache.filter_memory", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_entities", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_relations", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_qa", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.commit"),
        ):
            self._make_router().check_memory("FastAPI", project_id="proj-z", memory_scope="strict")
        # At least one query should contain strict project filter
        entity_query = next((q for q, p in queries if "Entity" in q and "project_id" in q), None)
        assert entity_query is not None
        assert "$pid" in entity_query
        # strict uses equality, not OR
        assert "OR" not in entity_query

    def test_shared_scope_includes_default(self):
        queries = []
        with (
            patch("backend.router.db.query_all", side_effect=lambda q, p=None: queries.append((q, p)) or []),
            patch("backend.router.memory_client.search", return_value=[]),
            patch("backend.router.context_cache.filter_memory", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_entities", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_relations", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_qa", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.commit"),
        ):
            self._make_router().check_memory("FastAPI", project_id="proj-z", memory_scope="shared")
        entity_query = next((q for q, p in queries if "Entity" in q and "project_id" in q), None)
        assert entity_query is not None
        assert "'default'" in entity_query

    def test_global_scope_has_no_pid_filter(self):
        queries = []
        with (
            patch("backend.router.db.query_all", side_effect=lambda q, p=None: queries.append((q, p)) or []),
            patch("backend.router.memory_client.search", return_value=[]),
            patch("backend.router.context_cache.filter_memory", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_entities", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_relations", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_qa", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.commit"),
        ):
            self._make_router().check_memory("FastAPI", project_id="proj-z", memory_scope="global")
        entity_query = next((q for q, p in queries if "Entity" in q), None)
        assert entity_query is not None
        assert "$pid" not in entity_query


# ── Adversarial cross-project isolation ───────────────────────────────────────

class TestAdversarialProjectIsolation:
    """Poisoned entity in project-A must not leak into project-B's check_memory."""

    def _make_router(self):
        with patch("backend.router.db.query_all", return_value=[]):
            return AgentRouter()

    def test_poisoned_entity_absent_in_other_project(self):
        """All parameterised queries must use proj-b's pid; no query may hard-code proj-a."""
        queries_params = []

        def capture(q, p=None):
            queries_params.append((q, p))
            # Return a fake entity row on the first (entity) query so QA/rel paths execute.
            if "Entity" in q and "project_id" in q and not queries_params[:-1]:
                return [("secret_key", "concept", "", "proj-b")]
            return []

        with (
            patch("backend.router.db.query_all", side_effect=capture),
            patch("backend.router.memory_client.search", return_value=[]),
            patch("backend.router.context_cache.filter_memory", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_entities", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_relations", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_qa", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.commit"),
        ):
            self._make_router().check_memory("secret_key", project_id="proj-b", memory_scope="strict")

        for q, p in queries_params:
            if p and "pid" in p:
                assert p["pid"] == "proj-b", f"query leaked wrong pid: {p}"
            if "project_id" in q and "$pid" in q:
                assert "'proj-a'" not in q, "query hard-coded proj-a into proj-b request"

    def test_shared_scope_confidence_gate_entity_only(self):
        """Entity shared-scope query gates on confidence; Question/File queries must not."""
        queries_params = []
        call_count = [0]

        def capture(q, p=None):
            call_count[0] += 1
            queries_params.append((q, p))
            # Return a fake entity on the first call so QA + relationship paths execute.
            if call_count[0] == 1:
                return [("foo", "concept", "", "proj-x")]
            return []

        with (
            patch("backend.router.db.query_all", side_effect=capture),
            patch("backend.router.memory_client.search", return_value=[]),
            patch("backend.router.context_cache.filter_memory", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_entities", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_relations", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_qa", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.commit"),
        ):
            self._make_router().check_memory("foo", project_id="proj-x", memory_scope="shared")

        entity_q = next((q for q, _ in queries_params if "Entity" in q and "$pid" in q), None)
        rel_q = next((q for q, _ in queries_params if "RELATED_TO" in q), None)
        qa_q = next((q for q, _ in queries_params if "Question" in q), None)
        file_q = next((q for q, _ in queries_params if ":File" in q), None)

        assert entity_q is not None
        assert "confidence" in entity_q, "Entity shared-scope query must gate on confidence"

        assert rel_q is not None, "Relationship query must execute when entity_names is non-empty"
        # Relationships between entities may legitimately filter on entity confidence,
        # but the raw RELATED_TO clause must not add an unrelated standalone confidence gate.
        assert "confidence" not in rel_q, "Relationship clause must not introduce a confidence filter"

        assert qa_q is not None, "QA query must execute when entity_names is non-empty"
        assert "confidence" not in qa_q, "Question nodes have no confidence field — must not appear in QA clause"

        assert file_q is not None, "File query must always execute"
        assert "confidence" not in file_q, "File nodes have no confidence field — must not appear in File clause"

    def test_e2e_cross_project_secret_not_in_context(self):
        """A poisoned entity (secret value) written to proj-A must not appear in proj-B's rendered context."""
        # Simulate DB returning proj-A entities when proj-B asks for memory.
        # This represents a worst-case DB misconfiguration — the context renderer
        # is the last line of defense and must filter them out.
        poisoned_secret = "SK_PROJ_A_SECRET_TOKEN"
        proj_a_entity = (poisoned_secret, "Token", "access key", "proj-a")

        def capture(q, p=None):
            # Return entity rows only for the entity-match query; other queries empty.
            if "MATCH (e:Entity)" in q:
                return [proj_a_entity]
            return []

        context_out = []

        def capture_context(session_id, snippets):
            context_out.extend(snippets)
            return snippets

        with (
            patch("backend.router.db.query_all", side_effect=capture),
            patch("backend.router.memory_client.search", return_value=[]),
            patch("backend.router.context_cache.filter_memory", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_entities", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_relations", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_qa", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.commit"),
        ):
            result = self._make_router().check_memory(poisoned_secret, project_id="proj-b", memory_scope="strict")

        # The context string (or None) must not contain the secret from proj-A
        context_str = result or ""
        assert poisoned_secret not in context_str, (
            f"Cross-project secret '{poisoned_secret}' leaked into proj-B context"
        )


# ── check_memory semantic content fallback shapes ─────────────────────────────

class TestCheckMemoryContentFallback:
    """check_memory must extract snippet text from several agentmemory result shapes."""

    def _check(self, semantic_results):
        from backend.router import AgentRouter
        router = AgentRouter()
        with (
            patch("backend.router.memory_client.search", return_value=semantic_results),
            patch("backend.router.db.query_all", return_value=[]),
            patch("backend.router.context_cache.filter_memory", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_entities", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_relations", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.filter_qa", side_effect=lambda s, i: i),
            patch("backend.router.context_cache.commit"),
        ):
            return router.check_memory("q", session_id="s")

    def test_standard_content_field(self):
        result = self._check([{"content": "hello world", "metadata": {}}])
        assert result and "hello world" in result

    def test_observation_narrative_fallback(self):
        result = self._check([{"observation": {"narrative": "from narrative"}}])
        assert result and "from narrative" in result

    def test_observation_title_fallback(self):
        result = self._check([{"observation": {"title": "from title"}}])
        assert result and "from title" in result

    def test_empty_results_returns_none(self):
        assert self._check([]) is None

    def test_unknown_shape_does_not_crash(self):
        result = self._check([{"unexpected": "value"}])
        # Should not raise; may return None or skip the item
        assert result is None or isinstance(result, str)
