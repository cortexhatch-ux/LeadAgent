"""Tests for db-layer security guards in GraphDB (add_concept, add_relationship)."""

from unittest.mock import MagicMock, patch, call
import pytest


@pytest.fixture
def graph_db():
    """GraphDB instance with a fully mocked kuzu connection."""
    import backend.db as db_module
    gdb = object.__new__(db_module.GraphDB)
    gdb._lock = __import__("threading").Lock()
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    gdb.connection = conn
    return gdb


class TestAddConceptGuards:
    def test_blocklisted_name_is_rejected(self, graph_db):
        with patch("backend.db.is_blocked_entity", return_value=True):
            graph_db.add_concept("PASSWORD", project_id="default")
        graph_db.connection.execute.assert_not_called()

    def test_default_project_scrubs_name(self, graph_db):
        captured = {}
        def fake_scrub(text):
            captured["calls"] = captured.get("calls", []) + [text]
            return text
        with patch("backend.db.scrub_secrets", side_effect=fake_scrub):
            with patch("backend.db.is_blocked_entity", return_value=False):
                graph_db.add_concept("MyLib", "some desc", project_id="default")
        assert "MyLib" in captured["calls"]
        assert "some desc" in captured["calls"]

    def test_project_scoped_write_skips_scrub(self, graph_db):
        with patch("backend.db.scrub_secrets") as mock_scrub:
            with patch("backend.db.is_blocked_entity", return_value=False):
                graph_db.add_concept("MyLib", "desc", project_id="proj1")
        mock_scrub.assert_not_called()

    def test_update_is_scoped_to_project_id(self, graph_db):
        """On PK collision the MATCH UPDATE must use project_id to avoid cross-project mutation."""
        execute = graph_db.connection.execute
        execute.side_effect = [Exception("PK collision"), MagicMock()]
        with patch("backend.db.is_blocked_entity", return_value=False):
            graph_db.add_concept("MyLib", project_id="proj1")
        second_call_sql = execute.call_args_list[1][0][0]
        assert "$pid" in second_call_sql


class TestAddRelationshipGuards:
    def test_blocklisted_source_rejected(self, graph_db):
        with patch("backend.db.is_blocked_entity", side_effect=lambda n: n == "PASSWORD"):
            graph_db.add_relationship("PASSWORD", "B", "uses", project_id="myproject")
        graph_db.connection.execute.assert_not_called()

    def test_blocklisted_target_rejected(self, graph_db):
        with patch("backend.db.is_blocked_entity", side_effect=lambda n: n == "SECRET"):
            graph_db.add_relationship("A", "SECRET", "uses", project_id="myproject")
        graph_db.connection.execute.assert_not_called()

    def test_clean_write_proceeds(self, graph_db):
        with patch("backend.db.is_blocked_entity", return_value=False):
            graph_db.add_relationship("A", "B", "uses", project_id="myproject")
        graph_db.connection.execute.assert_called_once()

    def test_default_project_scrubs_rel_type(self, graph_db):
        captured = {}
        def fake_scrub(text):
            captured["rel_type"] = text
            return text
        with patch("backend.db.is_blocked_entity", return_value=False):
            with patch("backend.db.scrub_secrets", side_effect=fake_scrub):
                graph_db.add_relationship("A", "B", "uses", project_id="default")
        assert captured.get("rel_type") == "uses"

    def test_project_scoped_write_skips_rel_type_scrub(self, graph_db):
        with patch("backend.db.is_blocked_entity", return_value=False):
            with patch("backend.db.scrub_secrets") as mock_scrub:
                graph_db.add_relationship("A", "B", "uses", project_id="proj1")
        mock_scrub.assert_not_called()
