#!/bin/bash
set -e
cd "$(dirname "$0")"

DAEMON_MODE=false
FORCE_NATIVE=false
for arg in "$@"; do
    if [ "$arg" == "--daemon" ] || [ "$arg" == "-d" ]; then
        DAEMON_MODE=true
    fi
    if [ "$arg" == "--native" ] || [ "$arg" == "-n" ]; then
        FORCE_NATIVE=true
    fi
done

# Ensure runtime directories exist
mkdir -p leadagent-data data

echo "🧹 Cleaning up existing LeadAgent processes..."

# 1. Surgical kill: Target only processes tagged with LEADAGENT_TAG=true
PIDS=$(pgrep -f "LEADAGENT_TAG=true" || true)
if [ ! -z "$PIDS" ]; then
    for PID in $PIDS; do
        echo "   Terminating tagged LeadAgent process $PID..."
        kill -9 $PID 2>/dev/null || true
    done
fi

# 2. Safety sweep for ports (8000 for FastAPI, 3111 for AgentMemory)
# Only kill if the process on that port is tagged as ours
for port in 8000 3111; do
    PIDS=$(lsof -ti :$port || true)
    if [ ! -z "$PIDS" ]; then
        for PID in $PIDS; do
            # Check if the process command line contains our tag
            if ps -p $PID -o args= 2>/dev/null | grep -q "LEADAGENT_TAG=true"; then
                echo "   Killing tagged LeadAgent process $PID holding port $port..."
                kill -9 $PID 2>/dev/null || true
            fi
        done
    fi
done

# Allow a moment for ports to be released
sleep 1

# Export the tag for all subsequent commands in this script
export LEADAGENT_TAG=true

# Load workspace path from config.json (Consensus Round 6)
CONFIG_FILE="leadagent-data/config.json"
# Discover local workspace (default to home directory)
WORKSPACE="${LEADAGENT_WORKSPACE:-$HOME}"
if [ -f "$CONFIG_FILE" ]; then
    SAVED_PATH=$(grep -o '"projects_dir": "[^"]*' "$CONFIG_FILE" | cut -d'"' -f4)
    if [ ! -z "$SAVED_PATH" ]; then
        WORKSPACE="$SAVED_PATH"
    fi
fi
export LEADAGENT_WORKSPACE="$WORKSPACE"

# ── Docker mode (preferred — no system file writes required) ─────────────────
if [ "$FORCE_NATIVE" = false ] && docker info &>/dev/null; then
    echo "🐳 Docker is running — starting LeadAgent via Docker Compose..."
    echo "   Workspace mirrored: $LEADAGENT_WORKSPACE"

    # agentmemory runs natively (it manages its own Docker containers)
    if command -v agentmemory &>/dev/null; then
        if ! nc -z localhost 3111 2>/dev/null; then
            echo "   Starting agentmemory server..."
            # Launch with tag
            env LEADAGENT_TAG=true agentmemory serve --port 3111 --storage leadagent-data/memory &>leadagent-data/agentmemory.log &
        else
            echo "   agentmemory already running on port 3111."
        fi
    else
        echo "   agentmemory not found — context memory will be limited."
    fi

    docker compose up -d --build
    echo ""
    echo "🚀 LeadAgent is running."
    echo "   Logs:    docker compose logs -f backend"
    echo "   Health:  leadagent health"
    echo "   Stop:    docker compose down"
    exit 0
fi

# ── Native fallback (launchd, no Docker) ─────────────────────────────────────
echo "⚠️  Docker not available — starting natively..."
export PYTHONPATH=$PYTHONPATH:.
# Carry the user's full PATH so the backend can find CLIs (claude, gemini, etc.)
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if command -v agentmemory &>/dev/null; then
    echo "   Starting agentmemory server..."
    # Launch with tag
    env LEADAGENT_TAG=true agentmemory serve --port 3111 --storage leadagent-data/memory &>leadagent-data/agentmemory.log &
else
    echo "   agentmemory not found. Context memory will be limited."
fi

# Find the venv python — prefer ./leadagent, then legacy ./venv, fall back to system
PYTHON=""
for p in ./leadagent/bin/python3 ./leadagent/bin/python ./venv/bin/python3 ./venv/bin/python python3 python; do
    if [ -x "$p" ] || command -v "$p" &>/dev/null; then
        PYTHON="$p"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Error: no Python found. Run ./install.sh to create the leadagent venv."
    exit 1
fi

echo "🚀 Launching FastAPI daemon..."
if [ "$DAEMON_MODE" = true ]; then
    echo "   Running in background. Logs: leadagent-data/daemon.log"
    # Pass tag as an argument so it shows up in ps/pgrep on all platforms (e.g. Darwin)
    nohup env LEADAGENT_TAG=true "$PYTHON" backend/main.py LEADAGENT_TAG=true > leadagent-data/daemon.log 2>&1 &
else
    # Pass tag as an argument so it shows up in ps/pgrep on all platforms (e.g. Darwin)
    exec env LEADAGENT_TAG=true "$PYTHON" backend/main.py LEADAGENT_TAG=true
fi
