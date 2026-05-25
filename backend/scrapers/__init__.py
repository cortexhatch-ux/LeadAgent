"""
Provider scraper registry.

Each scraper lives in its own module and registers itself here via `register()`.
UsageScraper / UsageMonitor iterate SCRAPERS to stay provider-agnostic.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.scrapers.base import BaseScraper

# Populated by each scraper module at import time.
SCRAPERS: dict[str, "BaseScraper"] = {}


def register(scraper: "BaseScraper") -> None:
    SCRAPERS[scraper.name] = scraper


def strip_ansi(s: str) -> str:
    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)
    s = re.sub(r"\x1b[()][A-Z0-9]", "", s)
    s = re.sub(r"\x1b.", "", s)
    s = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", s)
    return s.replace("\r\n", "\n").replace("\r", "\n")


# Sync intervals (seconds) keyed by the string returned by BaseScraper.detect_model()
SYNC_INTERVALS: dict[str | None, int] = {
    # Claude
    "opus": 2 * 60,
    "sonnet": 10 * 60,
    "haiku": 30 * 60,
    # Gemini
    "gemini ultra": 2 * 60,
    "gemini pro": 5 * 60,
    "gemini flash": 15 * 60,
    "gemini nano": 30 * 60,
    # Codex
    "codex": 30 * 60,
    # Fallback
    None: 30 * 60,
}


def _load_all() -> None:
    """Import every scraper module so they self-register."""
    from backend.scrapers import claude, gemini, codex  # noqa: F401


_load_all()
