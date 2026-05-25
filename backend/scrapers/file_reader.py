"""
Reads usage JSON files written by the per-container usage daemons.

File location: <leadagent-data>/usage/<provider>.json
In Docker mode the shared volume maps to /app/leadagent-data.
In local mode it falls back to the project root.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Age (seconds) beyond which a file is considered stale and ignored
STALE_AFTER = int(os.environ.get("USAGE_STALE_AFTER", str(2 * 3600)))  # 2 hours


def _usage_dir() -> Path:
    base = os.environ.get("LEADAGENT_DATA_DIR")
    if base:
        return Path(base) / "usage"
    # Docker: /app/leadagent-data/usage
    docker_path = Path("/app/leadagent-data/usage")
    if docker_path.exists():
        return docker_path
    # Local: repo root / usage
    return Path(__file__).resolve().parents[3] / "usage"


class UsageFileReader:
    @staticmethod
    def read(provider: str) -> dict:
        """
        Read and return the latest usage data for provider.
        Returns {} if the file doesn't exist or is stale.
        """
        path = _usage_dir() / f"{provider}.json"
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except Exception as e:
            print(f"[UsageFileReader] {provider}: read error: {e}")
            return {}

        age = time.time() - data.get("captured_at", 0)
        if age > STALE_AFTER:
            print(
                f"[UsageFileReader] {provider}: data is {age / 3600:.1f}h old — ignoring"
            )
            return {}

        return data

    @staticmethod
    def age(provider: str) -> float | None:
        """Return seconds since the file was last written, or None if absent."""
        path = _usage_dir() / f"{provider}.json"
        try:
            data = json.loads(path.read_text())
            return time.time() - data.get("captured_at", 0)
        except Exception:
            return None
