"""
Usage scraper orchestrator.

Provider-specific work is done by per-container daemons (agents/*/usage_daemon.py).
This module reads their output files and keeps quota_manager in sync.
"""

from __future__ import annotations

import threading
import time

import backend.scrapers as _scraper_pkg  # noqa: F401 — triggers _load_all()
from backend.scrapers import SCRAPERS, SYNC_INTERVALS
from backend.scrapers.gemini import GeminiScraper


# ── per-provider sync ─────────────────────────────────────────────────────────


def sync_provider(name: str, quota_manager) -> dict:
    scraper = SCRAPERS.get(name)
    if not scraper:
        return {}
    try:
        data = scraper.scrape()
        if data:
            if name == "claude":
                quota_manager.sync_real_usage(
                    "claude",
                    data.get("session_pct"),
                    data.get("weekly_pct"),
                )
            else:
                quota_manager.sync_real_usage(name, data.get("quota_pct"), None)
            print(f"[UsageScraper] {name} synced: {data}")
        return data
    except Exception as e:
        print(f"[UsageScraper] {name} sync failed: {e}")
        return {}


# Convenience aliases kept for existing callers in main.py
def sync_claude_usage(quota_manager) -> dict:
    return sync_provider("claude", quota_manager)


def sync_gemini_usage(quota_manager) -> dict:
    return sync_provider("gemini", quota_manager)


def sync_codex_usage(quota_manager) -> dict:
    return sync_provider("codex", quota_manager)


# ── adaptive monitor ──────────────────────────────────────────────────────────


class UsageMonitor:
    """
    Background thread that polls provider usage files and syncs quota_manager.
    Sync interval adapts to the active model tier (faster for premium models).
    """

    def __init__(self, quota_manager):
        self._qm = quota_manager
        self._stop = threading.Event()
        self._thread = None
        self._models: dict[str, str | None] = {name: None for name in SCRAPERS}
        self.last_sync_at: float = 0
        self.sync_interval: int = SYNC_INTERVALS[None]

    # ── backwards-compat properties ───────────────────────────────────────────

    @property
    def claude_model(self) -> str | None:
        return self._models.get("claude")

    @property
    def gemini_model(self) -> str | None:
        return self._models.get("gemini")

    @property
    def current_model(self) -> str | None:
        return self.claude_model

    # ── public API ────────────────────────────────────────────────────────────

    def start(self):
        try:
            self._initial_report()
        except Exception as e:
            print(f"[UsageMonitor] initial report failed (non-fatal): {e}")
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="UsageMonitor"
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def status(self) -> dict:
        due_in = max(0, int(self.last_sync_at + self.sync_interval - time.time()))
        return {
            "models": dict(self._models),
            "claude_model": self.claude_model,
            "gemini_model": self.gemini_model,
            "sync_interval_s": self.sync_interval,
            "next_sync_in_s": due_in,
            "last_sync_at": self.last_sync_at or None,
        }

    # ── internals ─────────────────────────────────────────────────────────────

    def _interval_for(self, name: str, model: str | None) -> int:
        if name == "gemini":
            gs = SCRAPERS.get("gemini")
            if isinstance(gs, GeminiScraper):
                model = gs.model_key(model)
        return SYNC_INTERVALS.get(model, SYNC_INTERVALS[None])

    def _compute_interval(self) -> int:
        return min(
            self._interval_for(name, model) for name, model in self._models.items()
        )

    def _dominant_provider(self) -> str:
        best_name, best_iv = None, SYNC_INTERVALS[None] + 1
        for name, model in self._models.items():
            iv = self._interval_for(name, model)
            if iv < best_iv:
                best_iv, best_name = iv, name
        return f"{best_name}/{self._models.get(best_name)}"

    def _sync_all(self) -> dict[str, dict]:
        all_data = {}
        for name in SCRAPERS:
            data = sync_provider(name, self._qm)
            all_data[name] = data
            # Gemini model comes from scrape data
            if name == "gemini" and data.get("model"):
                self._models["gemini"] = data["model"]
            # Claude model comes from detect_model (fast file read)
            if name == "claude":
                model = SCRAPERS["claude"].detect_model()
                if model:
                    self._models["claude"] = model
        return all_data

    def _initial_report(self):
        print("[UsageMonitor] starting — reading usage files from provider daemons...")
        all_data = self._sync_all()
        self.sync_interval = self._compute_interval()
        self.last_sync_at = time.time()

        lines = ["[UsageMonitor] ┌─ startup usage report ──────────────────"]
        for name, data in all_data.items():
            model = self._models.get(name) or "unknown"
            if name == "claude":
                s = data.get("session_pct", "?")
                w = data.get("weekly_pct", "?")
                r = data.get("session_resets", "")
                lines.append(
                    f"[UsageMonitor] │  Claude : {model}  session {s}%  weekly {w}%  {r}"
                )
            else:
                q = data.get("quota_pct", "?")
                lines.append(
                    f"[UsageMonitor] │  {name.capitalize()} : {model}  quota {q}%"
                )
        lines.append(
            f"[UsageMonitor] │  Sync interval: every {self.sync_interval // 60}min"
            f"  (driven by: {self._dominant_provider()})"
        )
        lines.append("[UsageMonitor] └─────────────────────────────────────────")
        print("\n".join(lines))

    def _loop(self):
        while not self._stop.is_set():
            self._stop.wait(timeout=60)
            if self._stop.is_set():
                break
            try:
                if time.time() - self.last_sync_at >= self.sync_interval:
                    print(
                        f"[UsageMonitor] syncing — {self._dominant_provider()} "
                        f"interval={self.sync_interval // 60}min"
                    )
                    self._sync_all()
                    self.sync_interval = self._compute_interval()
                    self.last_sync_at = time.time()
            except Exception as e:
                print(f"[UsageMonitor] loop error: {e}")


usage_monitor: UsageMonitor | None = None


def start_monitor(quota_manager) -> UsageMonitor:
    global usage_monitor
    usage_monitor = UsageMonitor(quota_manager)
    usage_monitor.start()
    return usage_monitor
