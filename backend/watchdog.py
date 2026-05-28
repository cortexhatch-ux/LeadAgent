#!/usr/bin/env python3
"""
LeadAgent Watchdog.

Native mode (launchd):  called every 30s via StartInterval, uses launchctl to restart.
Docker mode (detected automatically): runs in a loop via docker-compose, uses the
  Docker Unix socket API to restart the backend container — no docker CLI needed.
"""

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

try:
    from backend.indexer import process_file, INDEX_EXTENSIONS
    INDEXER_AVAILABLE = True
except ImportError:
    INDEXER_AVAILABLE = False

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "leadagent-data"
STATE_FILE = DATA_DIR / "watchdog_state.json"
LOG_FILE = DATA_DIR / "watchdog.log"
BACKEND_CONTAINER = "leadagent-backend"

# True when running inside a Docker container
IN_DOCKER = os.path.exists("/.dockerenv")

HEALTH_URL = (
    "http://backend:8000/health" if IN_DOCKER else "http://localhost:8000/health"
)
AGENTMEMORY_PORT = 3111
MAX_FAILURES = 3
DOCKER_SOCK = "/var/run/docker.sock"


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"consecutive_failures": 0}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def port_open(port: int, host: str = "localhost", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── Restart strategies ───────────────────────────────────────────────────────


def restart_via_docker_socket():
    """Restart the backend container via Docker Unix socket — no CLI needed."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(DOCKER_SOCK)
        request = (
            f"POST /containers/{BACKEND_CONTAINER}/restart HTTP/1.0\r\n"
            f"Host: localhost\r\n"
            f"\r\n"
        ).encode()
        sock.sendall(request)
        response = sock.recv(256).decode("utf-8", errors="ignore")
        sock.close()
        if " 204 " in response or " 200 " in response:
            log("Backend container restarted via Docker socket.")
            return True
        log(f"Docker socket restart got unexpected response: {response[:80]}")
        return False
    except Exception as e:
        log(f"Docker socket restart failed: {e}")
        return False


def restart_via_launchctl():
    """Restart the macOS launchd daemon."""
    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/com.leadagent.daemon"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        log("Daemon restarted via launchctl kickstart.")
    else:
        log(f"kickstart failed ({result.stderr.strip()}), trying stop/start...")
        subprocess.run(
            ["launchctl", "stop", "com.leadagent.daemon"], capture_output=True
        )
        time.sleep(3)
        subprocess.run(
            ["launchctl", "start", "com.leadagent.daemon"], capture_output=True
        )
        log("Daemon stop/start issued.")


def restart_daemon():
    if IN_DOCKER and os.path.exists(DOCKER_SOCK):
        restart_via_docker_socket()
    else:
        restart_via_launchctl()


# ── agentmemory (native-only — it manages its own Docker containers) ─────────


def revive_agentmemory():
    if IN_DOCKER:
        return  # agentmemory can't run inside Docker (it IS Docker)
    if not shutil.which("agentmemory"):
        return
    log("agentmemory port dark — attempting restart...")
    try:
        log_fh = open(DATA_DIR / "agentmemory.log", "a")
        env = os.environ.copy()
        env["LEADAGENT_TAG"] = "true"
        subprocess.Popen(
            ["agentmemory", "serve", "--port", str(AGENTMEMORY_PORT)],
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
            env=env,
        )
        log("agentmemory restarted.")
    except Exception as e:
        log(f"agentmemory restart failed: {e}")


# ── Health check ─────────────────────────────────────────────────────────────


def check_backend(state: dict) -> dict:
    import urllib.request
    import urllib.error

    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        status = data.get("status", "unknown")
        uptime = data.get("uptime_seconds", 0)
        entities = (
            data.get("components", {}).get("database", {}).get("entity_count", "?")
        )
        agents_up = [
            k
            for k, v in data.get("components", {}).get("agents", {}).items()
            if v.get("available")
        ]
        log(
            f"status={status}  uptime={uptime:.0f}s  "
            f"entities={entities}  agents={agents_up or 'none'}"
        )
        if status in ("ok", "degraded"):
            state["consecutive_failures"] = 0
        else:
            state["consecutive_failures"] += 1
            log(
                f"Backend reported error status (failure #{state['consecutive_failures']})"
            )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            state["consecutive_failures"] = 0
            log("Daemon reachable but /health missing — restart to pick up new code.")
        else:
            state["consecutive_failures"] += 1
            log(f"Backend HTTP {e.code} (failure #{state['consecutive_failures']})")
    except Exception as e:
        state["consecutive_failures"] += 1
        log(f"Backend unreachable — {e} (failure #{state['consecutive_failures']})")
    return state


def run_indexer(state: dict):
    """Scan for changed files and index them (Autonomous Indexer)."""
    if not INDEXER_AVAILABLE:
        return

    workspace = os.environ.get("LEADAGENT_WORKSPACE")
    if not workspace or not os.path.exists(workspace):
        return

    # Initialize hashes if missing
    if "file_hashes" not in state:
        state["file_hashes"] = {}

    log(f"Scanning workspace for changes: {workspace}")

    file_hashes = state["file_hashes"]
    indexed_count = 0

    # Simple walk-and-hash
    for root, dirs, files in os.walk(workspace):
        # Prune common ignore targets
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'venv', 'leadagent')]

        for f in files:
            file_path = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()

            if ext not in INDEX_EXTENSIONS:
                continue

            try:
                mtime = os.path.getmtime(file_path)
                if file_path not in file_hashes or mtime > file_hashes[file_path]:
                    log(f"Change detected in {f}, indexing...")
                    if process_file(file_path, project_id=workspace):
                        file_hashes[file_path] = mtime
                        indexed_count += 1
            except Exception as e:
                log(f"Failed to check/index {f}: {e}")

            if indexed_count >= 3: # Throttle to avoid overloading Ollama
                break

        if indexed_count >= 3:
            break

    if indexed_count > 0:
        log(f"Indexer batch complete: {indexed_count} files processed.")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()

    state = check_backend(state)

    if state["consecutive_failures"] >= MAX_FAILURES:
        log(
            f"*** {state['consecutive_failures']} consecutive failures — restarting ***"
        )
        restart_daemon()
        state["consecutive_failures"] = 0

    # ── Autonomous Indexer ──
    run_indexer(state)

    mem_host = "host.docker.internal" if IN_DOCKER else "localhost"
    if not IN_DOCKER and not port_open(AGENTMEMORY_PORT):
        revive_agentmemory()
    elif IN_DOCKER and not port_open(AGENTMEMORY_PORT, host=mem_host):
        log(f"agentmemory port dark on {mem_host}")

    save_state(state)


if __name__ == "__main__":
    main()
