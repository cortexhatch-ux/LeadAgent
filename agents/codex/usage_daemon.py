#!/usr/bin/env python3
"""
Codex usage daemon — runs inside the leadagent-codex container.

Extend _parse() once the exact output format of the codex CLI is known.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

OUTPUT_FILE = Path("/app/leadagent-data/usage/codex.json")
INTERVAL    = int(os.environ.get("CODEX_USAGE_INTERVAL", "1800"))  # seconds
SESSION     = "la-codex-usage"
HARDCOPY    = Path("/tmp/la_codex_usage.txt")

STARTUP_WAIT = 6


def run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True)


def screen(*args: str) -> subprocess.CompletedProcess:
    return run("screen", *args)


def kill_session() -> None:
    screen("-S", SESSION, "-X", "quit")


def strip_ansi(s: str) -> str:
    s = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', s)
    s = re.sub(r'\x1b[()][A-Z0-9]', '', s)
    s = re.sub(r'\x1b.', '', s)
    s = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', s)
    return s.replace('\r\n', '\n').replace('\r', '\n')


def hardcopy() -> str:
    screen("-S", SESSION, "-X", "hardcopy", str(HARDCOPY))
    time.sleep(0.3)
    try:
        return strip_ansi(HARDCOPY.read_text(errors="replace"))
    except FileNotFoundError:
        return ""


def parse(text: str) -> dict:
    # TODO: update regexes once codex CLI output format is confirmed
    result = {}
    m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*(?:used|quota)', text, re.IGNORECASE)
    if m:
        result["quota_pct"] = float(m.group(1))
    model = re.search(r'model[:\s]+([^\n,]+)', text, re.IGNORECASE)
    if model:
        result["model"] = model.group(1).strip()
    return result


def scrape() -> dict:
    kill_session()
    r = run("screen", "-dmS", SESSION, "codex")
    if r.returncode != 0:
        print("[codex-usage-daemon] failed to start screen session", flush=True)
        return {}
    try:
        time.sleep(STARTUP_WAIT)
        return parse(hardcopy())
    finally:
        kill_session()


def write(data: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["provider"]    = "codex"
    data["captured_at"] = int(time.time())
    tmp = OUTPUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(OUTPUT_FILE)
    print(f"[codex-usage-daemon] wrote {data}", flush=True)


def main() -> None:
    print(f"[codex-usage-daemon] starting, interval={INTERVAL}s", flush=True)
    while True:
        try:
            data = scrape()
            if data:
                write(data)
            else:
                print("[codex-usage-daemon] scrape returned no data", flush=True)
        except Exception as e:
            print(f"[codex-usage-daemon] error: {e}", flush=True)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
