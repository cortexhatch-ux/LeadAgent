"""
Gemini usage scraper — PTY-based until the daemon approach is validated.
"""

from __future__ import annotations

import re
import time

try:
    import pexpect

    _PEXPECT_AVAILABLE = True
except ImportError:
    _PEXPECT_AVAILABLE = False

from backend.scrapers.base import BaseScraper
from backend.scrapers import strip_ansi, register
from backend.agents import is_installed_anywhere, _build_argv


class GeminiScraper(BaseScraper):
    @property
    def name(self) -> str:
        return "gemini"

    def scrape(self) -> dict:
        if not _PEXPECT_AVAILABLE or not is_installed_anywhere("gemini"):
            return {}
        try:
            argv = _build_argv("gemini", [], tty=True)
            child = pexpect.spawn(
                argv[0],
                args=argv[1:],
                timeout=20,
                encoding="utf-8",
                echo=False,
                dimensions=(80, 200),
            )
            time.sleep(6)
            try:
                child.expect(pexpect.TIMEOUT, timeout=1)
            except pexpect.TIMEOUT:
                pass
            raw = child.before or ""
            child.terminate(force=True)
            return self._parse(strip_ansi(raw))
        except Exception as e:
            print(f"[GeminiScraper] scrape failed: {e}")
            return {}

    def _parse(self, text: str) -> dict:
        result = {}
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*used", text)
        if m:
            result["quota_pct"] = float(m.group(1))
        model = re.search(
            r"no sandbox\s+(Auto\s*\([^)]+\)|Gemini[\w\s.]+?)\s+\d+%",
            text,
            re.IGNORECASE,
        )
        if model:
            result["model"] = model.group(1).strip()
        return result

    def model_key(self, model_name: str | None) -> str | None:
        if not model_name:
            return None
        low = model_name.lower()
        for key in ("ultra", "pro", "flash", "nano"):
            if key in low:
                return f"gemini {key}"
        return None


register(GeminiScraper())
