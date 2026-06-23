"""Tests for backend/indexer.py — Ollama-driven file indexing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend import indexer


def test_get_ollama_url_local(monkeypatch):
    monkeypatch.delenv("LEADAGENT_DOCKER_MODE", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert indexer.get_ollama_url() == "http://localhost:11434"


def test_get_ollama_url_docker(monkeypatch):
    monkeypatch.setenv("LEADAGENT_DOCKER_MODE", "1")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert indexer.get_ollama_url() == "http://ollama:11434"


def test_get_ollama_url_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://my-host:9000")
    assert indexer.get_ollama_url() == "http://my-host:9000"


def test_extract_entities_via_ollama_success():
    payload = {
        "entities": [{"name": "Foo", "type": "Class", "description": "d"}],
        "relationships": [{"source": "Foo", "target": "Bar", "type": "USES"}],
    }
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": json.dumps(payload)}
    fake_resp.raise_for_status.return_value = None

    with patch("backend.indexer.requests.post", return_value=fake_resp) as p:
        out = indexer.extract_entities_via_ollama("foo.py", "print('hi')")
        assert out == payload
        body = p.call_args.kwargs["json"]
        assert body["model"] == "llama3.2:3b"
        assert body["stream"] is False
        assert body["format"] == "json"
        assert "foo.py" in body["prompt"]


def test_extract_entities_via_ollama_truncates_long_content():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": "{}"}
    fake_resp.raise_for_status.return_value = None

    with patch("backend.indexer.requests.post", return_value=fake_resp) as p:
        long_content = "x" * 10000
        indexer.extract_entities_via_ollama("big.py", long_content)
        # Prompt should contain only first 3000 chars of content
        prompt = p.call_args.kwargs["json"]["prompt"]
        assert "x" * 3000 in prompt
        assert "x" * 3001 not in prompt


def test_extract_entities_via_ollama_request_failure_returns_empty(capsys):
    with patch("backend.indexer.requests.post", side_effect=RuntimeError("net")):
        out = indexer.extract_entities_via_ollama("x.py", "code")
        assert out == {"entities": [], "relationships": []}
    assert "Ollama extraction failed" in capsys.readouterr().out


def test_extract_entities_via_ollama_bad_json_returns_empty(capsys):
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": "not valid json {{"}
    fake_resp.raise_for_status.return_value = None
    with patch("backend.indexer.requests.post", return_value=fake_resp):
        out = indexer.extract_entities_via_ollama("x.py", "code")
        assert out == {"entities": [], "relationships": []}


def test_process_file_success(tmp_path, monkeypatch):
    f = tmp_path / "hello.py"
    f.write_text("def hi(): pass")

    fake_db = MagicMock()
    monkeypatch.setattr(indexer, "db", fake_db)

    extracted = {
        "entities": [
            {"name": "hi", "type": "Function", "description": "greets"},
            {"name": "X", "type": "Class"},  # missing description
        ],
        "relationships": [{"source": "hi", "target": "X", "type": "USES"}],
    }
    with patch.object(indexer, "extract_entities_via_ollama", return_value=extracted):
        ok = indexer.process_file(str(f), project_id="proj1")

    assert ok is True
    fake_db.add_file.assert_called_once_with(str(f), "hello.py", ".py", "proj1")
    assert fake_db.add_entity.call_count == 2
    fake_db.add_entity.assert_any_call("hi", "Function", "greets", source_project_id="proj1", auto_extracted=True, source_agent="indexer")
    fake_db.add_entity.assert_any_call("X", "Class", "", source_project_id="proj1", auto_extracted=True, source_agent="indexer")
    fake_db.add_relationship.assert_called_once_with("hi", "X", "USES", project_id="proj1")


def test_process_file_missing_file_returns_false(tmp_path, monkeypatch, capsys):
    fake_db = MagicMock()
    monkeypatch.setattr(indexer, "db", fake_db)
    ok = indexer.process_file(str(tmp_path / "nope.py"))
    assert ok is False
    assert "Failed to process" in capsys.readouterr().out


def test_process_file_handles_empty_extraction(tmp_path, monkeypatch):
    f = tmp_path / "e.py"
    f.write_text("# nothing")
    fake_db = MagicMock()
    monkeypatch.setattr(indexer, "db", fake_db)
    with patch.object(
        indexer, "extract_entities_via_ollama",
        return_value={"entities": [], "relationships": []},
    ):
        ok = indexer.process_file(str(f))
    assert ok is True
    fake_db.add_file.assert_called_once()
    fake_db.add_entity.assert_not_called()
    fake_db.add_relationship.assert_not_called()


def test_index_extensions_contains_common_langs():
    for ext in (".py", ".js", ".ts", ".go", ".md", ".json"):
        assert ext in indexer.INDEX_EXTENSIONS
