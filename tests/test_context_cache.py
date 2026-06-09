"""Tests for backend/context_cache.py — ContextCache dedup logic."""

import time

import pytest

from backend.context_cache import ContextCache, _hsh
import backend.context_cache as cc_mod


@pytest.fixture
def cache():
    return ContextCache()


def test_filter_entities_initially_returns_all(cache):
    rows = [("A", "Class", "foo"), ("B", "Func", "bar")]
    assert cache.filter_entities("s1", rows) == rows


def test_commit_then_filter_entities_dedupes(cache):
    rows = [("A", "Class", "foo"), ("B", "Func", "bar")]
    cache.commit("s1", entities=rows)
    assert cache.filter_entities("s1", rows) == []
    # New entity still passes through
    new_row = ("C", "Class", "baz")
    assert cache.filter_entities("s1", rows + [new_row]) == [new_row]


def test_filter_relations_dedupes(cache):
    rel = ("A", "B", "Class", "uses", "USES")
    cache.commit("s1", relations=[rel])
    assert cache.filter_relations("s1", [rel]) == []
    other = ("A", "C", "Class", "uses", "USES")
    assert cache.filter_relations("s1", [rel, other]) == [other]


def test_filter_qa_dedupes_by_prompt(cache):
    rows = [("what is X?", "X is foo", "claude")]
    cache.commit("s1", qa_rows=rows)
    assert cache.filter_qa("s1", rows) == []
    new = ("what is Y?", "Y is bar", "gemini")
    assert cache.filter_qa("s1", rows + [new]) == [new]


def test_filter_memory_dedupes(cache):
    snippets = ["the sky is blue", "water is wet"]
    cache.commit("s1", memory_snippets=snippets)
    assert cache.filter_memory("s1", snippets) == []
    assert cache.filter_memory("s1", snippets + ["new fact"]) == ["new fact"]


def test_sessions_are_isolated(cache):
    rows = [("A", "Class", "")]
    cache.commit("s1", entities=rows)
    # s2 has not seen anything yet
    assert cache.filter_entities("s2", rows) == rows


def test_invalidate_clears_session(cache):
    rows = [("A", "Class", "")]
    cache.commit("s1", entities=rows)
    assert cache.filter_entities("s1", rows) == []
    cache.invalidate("s1")
    assert cache.filter_entities("s1", rows) == rows


def test_invalidate_unknown_session_noop(cache):
    cache.invalidate("never-seen")  # must not raise


def test_stats_inactive_for_unknown_session(cache):
    assert cache.stats("nope") == {"active": False}


def test_stats_active_after_commit(cache):
    cache.commit(
        "s1",
        entities=[("A", "Class", "")],
        relations=[("A", "B", "Class", "x", "USES")],
        qa_rows=[("q?", "a", "claude")],
        memory_snippets=["m1"],
    )
    s = cache.stats("s1")
    assert s["active"] is True
    assert s["injected_entities"] == 1
    assert s["injected_relations"] == 1
    assert s["injected_qa"] == 1
    assert s["injected_memory"] == 1
    assert s["idle_seconds"] >= 0


def test_prune_by_tag(cache):
    cache.commit("s1", entities=[("A", "C", "")], tag="t1")
    cache.commit("s1", entities=[("B", "C", "")], tag="t2")
    cache.prune_by_tag("s1", "t1")
    # A purged, B remains
    assert cache.filter_entities("s1", [("A", "C", ""), ("B", "C", "")]) == [
        ("A", "C", "")
    ]


def test_prune_by_tag_unknown_session_noop(cache):
    cache.prune_by_tag("nope", "anything")  # must not raise


def test_expire_removes_idle_sessions(cache, monkeypatch):
    cache.commit("old", entities=[("A", "C", "")])
    # Force last_active far in the past
    cache._sessions["old"].last_active = time.time() - cc_mod._SESSION_TTL - 10
    # commit on a different session triggers _expire
    cache.commit("new", entities=[("B", "C", "")])
    assert "old" not in cache._sessions
    assert "new" in cache._sessions


def test_hsh_stable_and_truncated():
    a = _hsh("hello world")
    b = _hsh("hello world")
    assert a == b
    # First 200 chars only matter
    long = "x" * 200
    assert _hsh(long) == _hsh(long + "tail-differs")
