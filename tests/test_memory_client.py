"""Tests for backend/memory_client.py — AgentMemoryClient HTTP wrapper."""

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.memory_client import AgentMemoryClient


def _resp(status, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body or {}
    r.text = text
    return r


def test_default_url_localhost(monkeypatch):
    monkeypatch.delenv("LEADAGENT_DOCKER_MODE", raising=False)
    c = AgentMemoryClient()
    assert c.url == "http://localhost:3111"


def test_default_url_docker(monkeypatch):
    monkeypatch.setenv("LEADAGENT_DOCKER_MODE", "1")
    c = AgentMemoryClient()
    assert c.url == "http://host.docker.internal:3111"


def test_explicit_url_overrides():
    c = AgentMemoryClient(url="http://example.com:9999")
    assert c.url == "http://example.com:9999"


def test_store_success():
    c = AgentMemoryClient(url="http://x")
    with patch("backend.memory_client.requests.post", return_value=_resp(201)) as p:
        assert c.store("hello", {"k": "v"}, tier="episodic") is True
        p.assert_called_once()
        kwargs = p.call_args.kwargs
        assert kwargs["json"] == {
            "content": "hello",
            "metadata": {"k": "v"},
            "tier": "episodic",
        }
        assert kwargs["timeout"] == 2.0


def test_store_default_metadata():
    c = AgentMemoryClient(url="http://x")
    with patch("backend.memory_client.requests.post", return_value=_resp(201)) as p:
        c.store("hi")
        assert p.call_args.kwargs["json"]["metadata"] == {}
        assert p.call_args.kwargs["json"]["tier"] == "semantic"


def test_store_non_201_returns_false(capsys):
    c = AgentMemoryClient(url="http://x")
    with patch("backend.memory_client.requests.post", return_value=_resp(500, text="boom")):
        assert c.store("x") is False
    assert "store failed" in capsys.readouterr().out


def test_store_timeout_returns_false(capsys):
    c = AgentMemoryClient(url="http://x")
    with patch(
        "backend.memory_client.requests.post",
        side_effect=requests.exceptions.Timeout(),
    ):
        assert c.store("x") is False
    assert "timed out" in capsys.readouterr().out


def test_store_generic_exception_returns_false(capsys):
    c = AgentMemoryClient(url="http://x")
    with patch("backend.memory_client.requests.post", side_effect=RuntimeError("nope")):
        assert c.store("x") is False
    assert "fatal error" in capsys.readouterr().out


def test_search_success_returns_results():
    c = AgentMemoryClient(url="http://x")
    body = {"results": [{"content": "a"}, {"content": "b"}]}
    with patch("backend.memory_client.requests.get", return_value=_resp(200, body)) as g:
        out = c.search("foo", limit=10)
        assert out == body["results"]
        assert g.call_args.kwargs["params"] == {"q": "foo", "limit": 10}
        assert g.call_args.kwargs["timeout"] == 2.0


def test_search_missing_results_key_returns_empty():
    c = AgentMemoryClient(url="http://x")
    with patch("backend.memory_client.requests.get", return_value=_resp(200, {})):
        assert c.search("foo") == []


def test_search_non_200_returns_empty(capsys):
    c = AgentMemoryClient(url="http://x")
    with patch("backend.memory_client.requests.get", return_value=_resp(404, text="nope")):
        assert c.search("foo") == []
    assert "search failed" in capsys.readouterr().out


def test_search_timeout_returns_empty(capsys):
    c = AgentMemoryClient(url="http://x")
    with patch(
        "backend.memory_client.requests.get",
        side_effect=requests.exceptions.Timeout(),
    ):
        assert c.search("foo") == []
    assert "timed out" in capsys.readouterr().out


def test_search_generic_exception_returns_empty(capsys):
    c = AgentMemoryClient(url="http://x")
    with patch("backend.memory_client.requests.get", side_effect=RuntimeError("boom")):
        assert c.search("foo") == []
    assert "fatal error" in capsys.readouterr().out
