"""Tests for backend/tools.py MCP security guards."""

from unittest.mock import MagicMock, patch

import pytest
import backend.tools as tools
from backend.tools import _MCP_DEFAULT_BLOCKED


@pytest.fixture(autouse=True)
def mock_db(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(tools, "db", db)
    return db


class TestAddMemoryEntityGuard:
    def test_default_project_blocked(self, mock_db):
        result = tools.add_memory_entity("Python", "lang", project_id="default")
        assert result == _MCP_DEFAULT_BLOCKED
        mock_db.add_entity.assert_not_called()

    def test_omitted_project_id_defaults_to_default_and_blocks(self, mock_db):
        result = tools.add_memory_entity("Python", "lang")
        assert result == _MCP_DEFAULT_BLOCKED
        mock_db.add_entity.assert_not_called()

    def test_project_scoped_write_succeeds(self, mock_db):
        result = tools.add_memory_entity("Python", "lang", project_id="myproject")
        assert "Python" in result
        mock_db.add_entity.assert_called_once_with("Python", "lang", "", source_project_id="myproject", source_agent="mcp")

    def test_blocklisted_entity_rejected(self, mock_db):
        with patch("backend.tools.is_blocked_entity", return_value=True):
            result = tools.add_memory_entity("Password", "secret", project_id="myproject")
        assert "rejected" in result.lower()
        mock_db.add_entity.assert_not_called()


class TestAddMemoryRelationshipGuard:
    def test_default_project_blocked(self, mock_db):
        result = tools.add_memory_relationship("A", "B", "uses", project_id="default")
        assert result == _MCP_DEFAULT_BLOCKED
        mock_db.add_relationship.assert_not_called()

    def test_omitted_project_id_defaults_to_default_and_blocks(self, mock_db):
        result = tools.add_memory_relationship("A", "B", "uses")
        assert result == _MCP_DEFAULT_BLOCKED
        mock_db.add_relationship.assert_not_called()

    def test_project_scoped_write_succeeds(self, mock_db):
        result = tools.add_memory_relationship("A", "B", "uses", project_id="myproject")
        assert "A" in result
        mock_db.add_relationship.assert_called_once_with("A", "B", "uses", project_id="myproject")

    def test_blocklisted_source_rejected(self, mock_db):
        with patch("backend.tools.is_blocked_entity", side_effect=lambda name: name == "A"):
            result = tools.add_memory_relationship("A", "B", "uses", project_id="myproject")
        assert "rejected" in result.lower()
        mock_db.add_relationship.assert_not_called()

    def test_blocklisted_target_rejected(self, mock_db):
        with patch("backend.tools.is_blocked_entity", side_effect=lambda name: name == "B"):
            result = tools.add_memory_relationship("A", "B", "uses", project_id="myproject")
        assert "rejected" in result.lower()
        mock_db.add_relationship.assert_not_called()
