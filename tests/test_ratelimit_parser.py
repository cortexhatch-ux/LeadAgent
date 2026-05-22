"""Tests for backend/ratelimit_parser.py — extract_usage_hint."""

import pytest
from backend.ratelimit_parser import extract_usage_hint


class TestExhaustedSignals:
    def test_hit_your_limit(self):
        h = extract_usage_hint("You've hit your limit for today.")
        assert h["exhausted"] is True

    def test_quota_exceeded(self):
        h = extract_usage_hint("Quota exceeded. Please try again later.")
        assert h["exhausted"] is True

    def test_usage_limit(self):
        h = extract_usage_hint("Usage limit reached.")
        assert h["exhausted"] is True

    def test_rate_limit_is_transient(self):
        h = extract_usage_hint("Rate limit: too many requests.")
        assert h.get("transient") is True
        assert "exhausted" not in h

    def test_too_many_requests_is_transient(self):
        h = extract_usage_hint("429 Too Many Requests")
        assert h.get("transient") is True
        assert "exhausted" not in h

    def test_capacity_is_transient(self):
        h = extract_usage_hint("No capacity available for model gemini-3.1-pro-preview on the server")
        assert h.get("transient") is True
        assert "exhausted" not in h

    def test_normal_output_not_exhausted(self):
        h = extract_usage_hint("Here is your answer: 42")
        assert "exhausted" not in h
        assert "transient" not in h

    def test_empty_string(self):
        h = extract_usage_hint("")
        assert h == {}


class TestLimitType:
    def test_weekly_limit(self):
        h = extract_usage_hint("You've reached your weekly usage limit.")
        assert h["limit_type"] == "weekly"

    def test_daily_limit(self):
        h = extract_usage_hint("You've reached your daily usage limit.")
        assert h["limit_type"] == "daily"

    def test_no_qualifier_defaults_to_daily(self):
        h = extract_usage_hint("You've hit your limit.")
        assert h["limit_type"] == "daily"

    def test_week_keyword(self):
        h = extract_usage_hint("Limit reached for this week.")
        assert h["limit_type"] == "weekly"


class TestUsagePercentage:
    def test_weekly_pct(self):
        h = extract_usage_hint("You've used 55% of your weekly limit.")
        assert h["weekly_pct"] == 55.0

    def test_daily_pct(self):
        h = extract_usage_hint("You've used 72% of your daily budget.")
        assert h["daily_pct"] == 72.0

    def test_fractional_pct(self):
        h = extract_usage_hint("Used 33.5% of weekly quota.")
        assert h["weekly_pct"] == 33.5

    def test_messages_remaining_computes_pct(self):
        h = extract_usage_hint("10 messages remaining out of 50")
        assert h["daily_pct"] == pytest.approx(80.0)

    def test_messages_remaining_no_total_ignored(self):
        h = extract_usage_hint("5 messages remaining")
        assert "daily_pct" not in h


class TestResetIn:
    def test_hours_and_minutes(self):
        h = extract_usage_hint("Resets in 3h 22m.")
        assert h["reset_in_seconds"] == 3 * 3600 + 22 * 60

    def test_hours_only(self):
        h = extract_usage_hint("resets in 2 hours")
        assert h["reset_in_seconds"] == 7200

    def test_minutes_only(self):
        h = extract_usage_hint("Resets in 45m")
        assert h["reset_in_seconds"] == 45 * 60

    def test_no_reset_info(self):
        h = extract_usage_hint("Please try again later.")
        assert "reset_in_seconds" not in h

    def test_zero_not_emitted(self):
        h = extract_usage_hint("Resets in 0h 0m")
        assert "reset_in_seconds" not in h


class TestCombined:
    def test_full_limit_message(self):
        text = "Weekly limit reached. Resets in 1h 30m. Used 100% of weekly quota."
        h = extract_usage_hint(text)
        assert h["exhausted"] is True
        assert h["limit_type"] == "weekly"
        assert h["reset_in_seconds"] == 5400
        assert h["weekly_pct"] == 100.0
