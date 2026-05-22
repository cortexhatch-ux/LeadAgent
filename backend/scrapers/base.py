from __future__ import annotations

from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """
    Contract every provider scraper must implement.

    scrape()        → dict of usage data (provider-specific keys)
    detect_model()  → str tier name (used to look up SYNC_INTERVALS), or None
    name            → provider key used in SCRAPERS registry and quota_manager
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def scrape(self) -> dict: ...

    def detect_model(self) -> str | None:
        """Return the active model tier (e.g. 'opus', 'gemini pro'). Override if supported."""
        return None
