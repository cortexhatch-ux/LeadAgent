"""Tests for backend/security.py — GuardMiddleware, Cypher guard, rate limiter, secret scrubber."""

import pytest
from unittest.mock import MagicMock, patch

from backend.security import (
    assert_read_only_cypher,
    UnsafeCypherError,
    scrub_secrets,
    _check_rate_limit,
    _rate_buckets,
    _rate_lock,
)


# ── assert_read_only_cypher ───────────────────────────────────────────────────

class TestReadOnlyCypher:
    def test_match_returns_query_unchanged(self):
        q = "MATCH (e:Entity) RETURN e.name"
        assert assert_read_only_cypher(q) == q

    def test_empty_string_raises(self):
        with pytest.raises(UnsafeCypherError):
            assert_read_only_cypher("")

    def test_create_raises(self):
        with pytest.raises(UnsafeCypherError, match="CREATE"):
            assert_read_only_cypher("CREATE (n:Node {name: 'x'})")

    def test_delete_raises(self):
        with pytest.raises(UnsafeCypherError, match="DELETE"):
            assert_read_only_cypher("MATCH (n) DELETE n")

    def test_detach_delete_raises(self):
        with pytest.raises(UnsafeCypherError):
            assert_read_only_cypher("MATCH (n) DETACH DELETE n")

    def test_set_raises(self):
        with pytest.raises(UnsafeCypherError, match="SET"):
            assert_read_only_cypher("MATCH (n) SET n.name = 'bad'")

    def test_merge_raises(self):
        with pytest.raises(UnsafeCypherError, match="MERGE"):
            assert_read_only_cypher("MERGE (n:Node {name: 'x'})")

    def test_drop_raises(self):
        with pytest.raises(UnsafeCypherError, match="DROP"):
            assert_read_only_cypher("DROP TABLE Entity")

    def test_call_raises(self):
        with pytest.raises(UnsafeCypherError, match="CALL"):
            assert_read_only_cypher("CALL db.index.fulltext.createNodeIndex('idx', ['Entity'], ['name'])")

    def test_case_insensitive_write_raises(self):
        with pytest.raises(UnsafeCypherError):
            assert_read_only_cypher("match (n) delete n")

    def test_return_only_query_passes(self):
        q = "MATCH (e:Entity) WHERE e.name = $n RETURN e.name, e.type"
        assert assert_read_only_cypher(q) == q

    def test_where_and_order_pass(self):
        q = "MATCH (e:Entity) WHERE e.confidence > 0.5 RETURN e ORDER BY e.name LIMIT 10"
        assert assert_read_only_cypher(q) == q


# ── scrub_secrets ─────────────────────────────────────────────────────────────

class TestScrubSecrets:
    def test_openai_key_redacted(self):
        text = "Use sk-abcdefghijklmnopqrstuvwxyz012345 to authenticate"
        result = scrub_secrets(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in result
        assert "[REDACTED]" in result

    def test_anthropic_key_redacted(self):
        text = "API key: sk-ant-api03-longkeyvaluethatshouldmatch1234567890"
        result = scrub_secrets(text)
        assert "sk-ant-api03" not in result

    def test_google_api_key_redacted(self):
        text = "token=AIzaSyDummyKeyValue1234567890abcdefghijk"
        result = scrub_secrets(text)
        assert "AIzaSy" not in result

    def test_aws_access_key_redacted(self):
        text = "AKIAIOSFODNN7EXAMPLE is the AWS access key"
        result = scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_password_kvpair_redacted(self):
        text = "password=hunter2secure"
        result = scrub_secrets(text)
        assert "hunter2secure" not in result

    def test_token_kvpair_redacted(self):
        text = "token=eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9secretpart"
        result = scrub_secrets(text)
        assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9secretpart" not in result

    def test_clean_text_unchanged(self):
        text = "FastAPI uses decorators for routing in Python"
        result = scrub_secrets(text)
        assert result == text

    def test_ssh_private_key_header_redacted(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAA..."
        result = scrub_secrets(text)
        assert "BEGIN RSA PRIVATE KEY" not in result

    def test_empty_string_returns_empty(self):
        assert scrub_secrets("") == ""

    def test_git_sha_not_redacted(self):
        sha = "a3f2c1d4e5b6a7f8e9d0c1b2a3f4e5d6c7b8a9f0"
        text = f"Commit {sha} fixed the bug"
        result = scrub_secrets(text)
        assert sha in result, "bare git SHAs must not be redacted"

    def test_hex_in_key_value_is_redacted(self):
        text = "secret=a3f2c1d4e5b6a7f8e9d0c1b2a3f4e5d6c7b8a9f0"
        result = scrub_secrets(text)
        assert "a3f2c1d4e5b6a7f8e9d0c1b2a3f4e5d6c7b8a9f0" not in result


# ── Rate limiter ──────────────────────────────────────────────────────────────

class TestRateLimiter:
    def setup_method(self):
        with _rate_lock:
            _rate_buckets.clear()

    def test_first_request_allowed(self):
        assert _check_rate_limit("127.0.0.1", "/v1/chat") is True

    def test_requests_within_limit_allowed(self):
        for _ in range(10):
            result = _check_rate_limit("10.0.0.1", "/v1/chat")
        assert result is True

    def test_requests_beyond_limit_blocked(self):
        from backend.security import _RATE_LIMIT_RPM
        ip = "192.168.1.99"
        path = "/v1/test-limit"
        for _ in range(_RATE_LIMIT_RPM):
            _check_rate_limit(ip, path)
        assert _check_rate_limit(ip, path) is False

    def test_different_paths_have_separate_buckets(self):
        from backend.security import _RATE_LIMIT_RPM
        ip = "192.168.1.50"
        for _ in range(_RATE_LIMIT_RPM):
            _check_rate_limit(ip, "/path-a")
        # /path-b bucket is fresh
        assert _check_rate_limit(ip, "/path-b") is True
