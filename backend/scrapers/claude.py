"""
Claude usage scraper.

Primary: reads the JSON file written by agents/claude/usage_daemon.py.
Fallback: PTY-based scrape via pexpect (used when daemon file is absent/stale).
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
from backend.scrapers.file_reader import UsageFileReader
from backend.agents import is_installed_anywhere, _build_argv


class ClaudeScraper(BaseScraper):

    @property
    def name(self) -> str:
        return "claude"

    def detect_model(self) -> str | None:
        # Try daemon file first (free — no subprocess)
        data = UsageFileReader.read("claude")
        if data.get("model_tier"):
            return data["model_tier"]
        if data.get("model"):
            m = re.search(r'(opus|sonnet|haiku)', data["model"], re.IGNORECASE)
            if m:
                return m.group(1).lower()

        # Fallback: spawn claude --bare --print hi
        if not _PEXPECT_AVAILABLE or not is_installed_anywhere("claude"):
            return None
        try:
            argv = _build_argv("claude", ["--bare", "--print", "hi"], tty=True)
            child = pexpect.spawn(
                argv[0], args=argv[1:],
                timeout=8, encoding="utf-8", echo=False,
                dimensions=(10, 160),
            )
            try:
                child.expect(pexpect.EOF, timeout=8)
            except (pexpect.TIMEOUT, pexpect.EOF):
                pass
            raw = strip_ansi(child.before or "")
            try:
                child.terminate(force=True)
            except Exception:
                pass
            m = re.search(r"(Opus|Sonnet|Haiku)", raw, re.IGNORECASE)
            return m.group(1).lower() if m else None
        except Exception as e:
            print(f"[ClaudeScraper] detect_model fallback failed: {e}")
            return None

    def scrape(self) -> dict:
        # Primary: daemon file
        data = UsageFileReader.read("claude")
        if data:
            print("[ClaudeScraper] using daemon file")
            return data

        # Fallback: PTY scrape
        print("[ClaudeScraper] daemon file absent/stale — falling back to PTY")
        return self._pty_scrape()

    def _pty_scrape(self) -> dict:
        if not _PEXPECT_AVAILABLE or not is_installed_anywhere("claude"):
            return {}
        try:
            argv = _build_argv("claude", [], tty=True)
            child = pexpect.spawn(
                argv[0], args=argv[1:],
                timeout=30, encoding="utf-8", echo=False,
                dimensions=(80, 200),
            )
            time.sleep(3)
            for _ in range(3):
                child.send("\r")
                time.sleep(0.5)
            try:
                child.expect(r"(?m)^\s*❯\s*$", timeout=15)
            except (pexpect.TIMEOUT, pexpect.EOF):
                time.sleep(2)

            child.sendline("/usage")
            raw = ""
            for _ in range(20):
                time.sleep(1)
                try:
                    child.expect(pexpect.TIMEOUT, timeout=0.1)
                except (pexpect.TIMEOUT, pexpect.EOF):
                    pass
                raw = child.before or ""
                if re.search(r"\d+%", raw):
                    time.sleep(1)
                    try:
                        child.expect(pexpect.TIMEOUT, timeout=1)
                    except (pexpect.TIMEOUT, pexpect.EOF):
                        pass
                    raw = (child.before or "") + (child.after or "")
                    break
            child.terminate(force=True)
            return self._parse(strip_ansi(raw))
        except Exception as e:
            print(f"[ClaudeScraper] PTY scrape failed: {e}")
            return {}

    def _parse(self, text: str) -> dict:
        result = {}
        pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%\s*used", text)
        if pcts:
            result["session_pct"] = float(pcts[0])
        if len(pcts) > 1:
            result["weekly_pct"] = float(pcts[1])
        resets = re.findall(r"Resets\s+([^\n]+)", text)
        if resets:
            result["session_resets"] = resets[0].strip()
        if len(resets) > 1:
            result["weekly_resets"] = resets[1].strip()
        return result


register(ClaudeScraper())
