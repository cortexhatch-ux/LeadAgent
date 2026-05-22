#!/usr/bin/env python3
"""
Claude usage daemon — runs inside the leadagent-claude container.

Reads ~/.claude/stats-cache.json (written by Claude Code itself) and
publishes a summary to the shared volume. No PTY, no screen sessions.

Runs every INTERVAL seconds and writes /app/leadagent-data/usage/claude.json.
"""

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

STATS_FILE  = Path("/root/.claude/stats-cache.json")
OUTPUT_FILE = Path("/app/leadagent-data/usage/claude.json")
INTERVAL    = int(os.environ.get("CLAUDE_USAGE_INTERVAL", "300"))  # 5 min

# Tokens in a day that we consider "100% session usage" for a Max subscriber.
# Claude Max is ~unlimited but we use a practical heavy-day ceiling for the %
# so the dashboard shows something meaningful. Adjust to taste.
SESSION_TOKEN_CEILING = 2_000_000


def read_stats() -> dict:
    try:
        return json.loads(STATS_FILE.read_text())
    except Exception as e:
        print(f"[claude-usage-daemon] cannot read stats-cache: {e}", flush=True)
        return {}


def detect_model(stats: dict) -> str | None:
    """Return the most-used model today, or from the last active day."""
    today = date.today().isoformat()
    for entry in reversed(stats.get("dailyModelTokens", [])):
        if entry["date"] == today or entry["date"] == (date.today() - timedelta(days=1)).isoformat():
            tokens = entry.get("tokensByModel", {})
            if tokens:
                return max(tokens, key=tokens.get)
    return None


def today_tokens(stats: dict) -> dict[str, int]:
    """Total output tokens per model for today."""
    today = date.today().isoformat()
    for entry in reversed(stats.get("dailyModelTokens", [])):
        if entry["date"] == today:
            return entry.get("tokensByModel", {})
    return {}


def week_tokens(stats: dict) -> int:
    """Total output tokens across all models for the last 7 days."""
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    total = 0
    for entry in stats.get("dailyModelTokens", []):
        if entry["date"] >= cutoff:
            total += sum(entry.get("tokensByModel", {}).values())
    return total


def today_activity(stats: dict) -> dict:
    today = date.today().isoformat()
    for entry in reversed(stats.get("dailyActivity", [])):
        if entry["date"] == today:
            return entry
    return {}


def build_output(stats: dict) -> dict:
    model_name = detect_model(stats)
    t_today    = today_tokens(stats)
    total_today = sum(t_today.values())
    session_pct = round(min(total_today / SESSION_TOKEN_CEILING * 100, 100), 1)

    activity = today_activity(stats)

    # Derive a tidy model tier name for SYNC_INTERVALS lookup
    tier = None
    if model_name:
        low = model_name.lower()
        for t in ("opus", "sonnet", "haiku"):
            if t in low:
                tier = t
                break

    return {
        "provider":      "claude",
        "model":         model_name,
        "model_tier":    tier,
        "session_pct":   session_pct,
        "today_tokens":  total_today,
        "week_tokens":   week_tokens(stats),
        "today_messages": activity.get("messageCount", 0),
        "today_sessions": activity.get("sessionCount", 0),
        "tokens_by_model": t_today,
        "captured_at":   int(time.time()),
    }


def write(data: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(OUTPUT_FILE)
    print(f"[claude-usage-daemon] wrote — model={data.get('model_tier')} "
          f"session={data.get('session_pct')}% today={data.get('today_tokens')} tokens",
          flush=True)


def main() -> None:
    print(f"[claude-usage-daemon] starting, interval={INTERVAL}s", flush=True)
    while True:
        try:
            stats = read_stats()
            if stats:
                write(build_output(stats))
            else:
                print("[claude-usage-daemon] stats-cache empty, skipping", flush=True)
        except Exception as e:
            print(f"[claude-usage-daemon] error: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
