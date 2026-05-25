import re

_EXHAUSTED_SIGNALS = [
    "hit your limit",
    "quota exceeded",
    "usage limit",
    "limit reached",
    "you've reached",
    "you have reached",
]

_TRANSIENT_SIGNALS = [
    "rate limit",
    "too many requests",
    "429",
    "no capacity",
    "capacity_exhausted",
]

_WEEKLY_SIGNALS = ["weekly", "week"]
_DAILY_SIGNALS = ["daily", "day", "hour", "today"]


def extract_usage_hint(text: str) -> dict:
    """
    Scan CLI output for quota/rate-limit signals.

    Returns any subset of:
      exhausted        bool
      transient        bool
      limit_type       str  ("daily" or "weekly")
      daily_pct        float  (0-100)
      weekly_pct       float  (0-100)
      reset_in_seconds int
    """
    result = {}
    lower = text.lower()

    if any(sig in lower for sig in _EXHAUSTED_SIGNALS):
        result["exhausted"] = True
        # Determine whether it's a daily or weekly limit
        if any(s in lower for s in _WEEKLY_SIGNALS):
            result["limit_type"] = "weekly"
        else:
            result["limit_type"] = "daily"

    if any(sig in lower for sig in _TRANSIENT_SIGNALS):
        result["transient"] = True

    # "55% of your weekly limit" / "55% weekly"
    weekly = re.search(r"(\d+(?:\.\d+)?)\s*%[^.\n]{0,80}week", text, re.IGNORECASE)
    daily = re.search(
        r"(\d+(?:\.\d+)?)\s*%[^.\n]{0,80}(?:day|daily|hour)", text, re.IGNORECASE
    )
    if weekly:
        result["weekly_pct"] = float(weekly.group(1))
    if daily:
        result["daily_pct"] = float(daily.group(1))

    # "12 messages remaining out of 50"
    msg = re.search(
        r"(\d+)\s+messages?\s+remaining(?:\s+out\s+of\s+(\d+))?", text, re.IGNORECASE
    )
    if msg and "daily_pct" not in result:
        remaining = int(msg.group(1))
        total = int(msg.group(2)) if msg.group(2) else None
        if total and total > 0:
            result["daily_pct"] = round((total - remaining) / total * 100, 1)

    # "resets in 3h 22m" / "resets in 45 minutes" / "resets in 2 hours"
    reset = re.search(
        r"reset[s]?\s+in\s+(?:(\d+)\s*h(?:ours?)?\s*)?(?:(\d+)\s*m(?:inutes?)?)?",
        text,
        re.IGNORECASE,
    )
    if reset and (reset.group(1) or reset.group(2)):
        hours = int(reset.group(1) or 0)
        mins = int(reset.group(2) or 0)
        secs = hours * 3600 + mins * 60
        if secs > 0:
            result["reset_in_seconds"] = secs

    return result
