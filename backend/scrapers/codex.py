"""
Codex usage scraper — PTY-based stub until CLI output format is confirmed.
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


class CodexScraper(BaseScraper):
    @property
    def name(self) -> str:
        return "codex"

    def scrape(self) -> dict:
        if not _PEXPECT_AVAILABLE or not is_installed_anywhere("codex"):
            return {}
        try:
            argv = _build_argv("codex", [], tty=True)
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
            print(f"[CodexScraper] scrape failed: {e}")
            return {}

    def _parse(self, text: str) -> dict:
        # TODO: update once codex CLI output format is confirmed
        result = {}
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:used|quota)", text, re.IGNORECASE)
        if m:
            result["quota_pct"] = float(m.group(1))
        model = re.search(r"model[:\s]+([^\n,]+)", text, re.IGNORECASE)
        if model:
            result["model"] = model.group(1).strip()
        return result


register(CodexScraper())
