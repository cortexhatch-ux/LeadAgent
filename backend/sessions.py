import json
import os

SESSIONS_FILE = "leadagent-data/sessions.json"


def load_sessions() -> dict:
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"[Sessions] Load failed, starting fresh: {e}")
        return {}


def save_sessions(sessions: dict) -> None:
    try:
        with open(SESSIONS_FILE, "w") as f:
            json.dump(sessions, f)
    except Exception as e:
        print(f"[Sessions] Save failed: {e}")
