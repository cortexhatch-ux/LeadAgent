#!/usr/bin/env python3
"""
LeadAgent host-side usage daemon.

Runs on the macOS host (not inside Docker) and spawns CLI tools directly via
pexpect — the same approach that worked before Docker was introduced.
Writes JSON to <repo>/leadagent-data/usage/<provider>.json which the backend
container reads from the shared volume.
"""

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

try:
    import pexpect
except ImportError:
    print("[host-daemon] ERROR: pexpect not installed — run: pip3 install pexpect", flush=True)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
USAGE_DIR = REPO_ROOT / "leadagent-data" / "usage"

INTERVALS = {
    "claude": int(os.environ.get("CLAUDE_USAGE_INTERVAL", "600")),
    "gemini": int(os.environ.get("GEMINI_USAGE_INTERVAL", "300")),
    "codex":  int(os.environ.get("CODEX_USAGE_INTERVAL", "1800")),
}


def strip_ansi(s: str) -> str:
    s = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', s)
    s = re.sub(r'\x1b[()][A-Z0-9]', '', s)
    s = re.sub(r'\x1b.', '', s)
    s = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', s)
    return s.replace('\r\n', '\n').replace('\r', '\n')


def write_output(provider: str, data: dict) -> None:
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    data["provider"]    = provider
    data["captured_at"] = int(time.time())
    path = USAGE_DIR / f"{provider}.json"
    tmp  = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
    print(f"[{provider}] wrote: {data}", flush=True)


# ── claude ────────────────────────────────────────────────────────────────────

def scrape_claude() -> dict:
    if not shutil.which("claude"):
        print("[claude] CLI not found", flush=True)
        return {}
    try:
        child = pexpect.spawn(
            "claude", args=[],
            timeout=30, encoding="utf-8", echo=False,
            dimensions=(80, 200),
        )

        # Give TUI time to render, dismiss onboarding screens
        time.sleep(3)
        for _ in range(3):
            child.send("\r")
            time.sleep(0.5)

        # Wait for ❯ prompt (may have non-breaking space \xa0 after it)
        try:
            child.expect("❯", timeout=15)
        except (pexpect.TIMEOUT, pexpect.EOF):
            time.sleep(2)

        child.send("/usage\r")

        # Poll until quota % appears
        raw = ""
        for _ in range(30):
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
        return _parse_claude(strip_ansi(raw))
    except Exception as e:
        print(f"[claude] scrape failed: {e}", flush=True)
        return {}


def _parse_claude(text: str) -> dict:
    result = {}
    pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%\s*used", text, re.IGNORECASE)
    if pcts:
        result["session_pct"] = float(pcts[0])
    if len(pcts) > 1:
        result["weekly_pct"] = float(pcts[1])
    resets = re.findall(r"Rese[st]s?\s*([^\n]+)", text)
    if resets:
        result["session_resets"] = resets[0].strip()
    if len(resets) > 1:
        result["weekly_resets"] = resets[1].strip()
    m = re.search(r"(Opus|Sonnet|Haiku)", text, re.IGNORECASE)
    if m:
        result["model"] = m.group(1).lower()
    return result


# ── gemini ────────────────────────────────────────────────────────────────────

def scrape_gemini() -> dict:
    if not shutil.which("gemini"):
        print("[gemini] CLI not found", flush=True)
        return {}
    try:
        child = pexpect.spawn(
            "gemini", args=[],
            timeout=20, encoding="utf-8", echo=False,
            dimensions=(80, 200),
        )
        time.sleep(6)
        try:
            child.expect(pexpect.TIMEOUT, timeout=1)
        except pexpect.TIMEOUT:
            pass
        raw = child.before or ""
        child.terminate(force=True)
        return _parse_gemini(strip_ansi(raw))
    except Exception as e:
        print(f"[gemini] scrape failed: {e}", flush=True)
        return {}


def _parse_gemini(text: str) -> dict:
    result = {}
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*used", text)
    if m:
        result["quota_pct"] = float(m.group(1))
    model = re.search(
        r"no sandbox\s+(Auto\s*\([^)]+\)|Gemini[\w\s.]+?)\s+\d+%",
        text, re.IGNORECASE,
    )
    if model:
        result["model"] = model.group(1).strip()
    return result


# ── codex ─────────────────────────────────────────────────────────────────────

def scrape_codex() -> dict:
    if not shutil.which("codex"):
        print("[codex] CLI not found", flush=True)
        return {}
    try:
        child = pexpect.spawn(
            "codex", args=[],
            timeout=20, encoding="utf-8", echo=False,
            dimensions=(80, 200),
        )
        time.sleep(6)
        try:
            child.expect(pexpect.TIMEOUT, timeout=1)
        except pexpect.TIMEOUT:
            pass
        raw = child.before or ""
        child.terminate(force=True)
        return _parse_codex(strip_ansi(raw))
    except Exception as e:
        print(f"[codex] scrape failed: {e}", flush=True)
        return {}


def _parse_codex(text: str) -> dict:
    result = {}
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:used|quota)", text, re.IGNORECASE)
    if m:
        result["quota_pct"] = float(m.group(1))
    model = re.search(r"model[:\s]+([^\n,]+)", text, re.IGNORECASE)
    if model:
        result["model"] = model.group(1).strip()
    return result


# ── provider loop ─────────────────────────────────────────────────────────────

SCRAPERS = {
    "claude": scrape_claude,
    "gemini": scrape_gemini,
    "codex":  scrape_codex,
}


def provider_loop(provider: str) -> None:
    interval = INTERVALS[provider]
    print(f"[{provider}] starting, interval={interval}s", flush=True)
    while True:
        try:
            data = SCRAPERS[provider]()
            if data:
                write_output(provider, data)
            else:
                print(f"[{provider}] scrape returned no data", flush=True)
        except Exception as e:
            print(f"[{provider}] error: {e}", flush=True)
        time.sleep(interval)


def main() -> None:
    print(f"[host-daemon] starting — writing to {USAGE_DIR}", flush=True)
    threads = []
    for provider in SCRAPERS:
        t = threading.Thread(
            target=provider_loop,
            args=(provider,),
            daemon=True,
            name=f"usage-{provider}",
        )
        t.start()
        threads.append(t)
        time.sleep(2)  # stagger starts

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("[host-daemon] shutting down", flush=True)


if __name__ == "__main__":
    main()
