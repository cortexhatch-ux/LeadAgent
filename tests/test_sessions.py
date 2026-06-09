"""Tests for backend/sessions.py — load_sessions / save_sessions."""

import json
import os
from unittest.mock import patch

import pytest

from backend import sessions


def test_load_sessions_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "SESSIONS_FILE", str(tmp_path / "nope.json"))
    assert sessions.load_sessions() == {}


def test_load_sessions_reads_existing_file(monkeypatch, tmp_path):
    p = tmp_path / "sessions.json"
    p.write_text(json.dumps({"sess-1": {"role": "coding"}}))
    monkeypatch.setattr(sessions, "SESSIONS_FILE", str(p))
    assert sessions.load_sessions() == {"sess-1": {"role": "coding"}}


def test_load_sessions_corrupt_json_returns_empty(monkeypatch, tmp_path, capsys):
    p = tmp_path / "sessions.json"
    p.write_text("not json {{{")
    monkeypatch.setattr(sessions, "SESSIONS_FILE", str(p))
    assert sessions.load_sessions() == {}
    out = capsys.readouterr().out
    assert "Load failed" in out


def test_save_sessions_roundtrip(monkeypatch, tmp_path):
    p = tmp_path / "sessions.json"
    monkeypatch.setattr(sessions, "SESSIONS_FILE", str(p))
    sessions.save_sessions({"a": 1, "b": [1, 2]})
    assert json.loads(p.read_text()) == {"a": 1, "b": [1, 2]}


def test_save_sessions_handles_io_error(monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_FILE", "/dev/null/cannot-write/here.json")
    # Should not raise
    sessions.save_sessions({"x": 1})
    out = capsys.readouterr().out
    assert "Save failed" in out
