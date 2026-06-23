#!/bin/bash
set -e
cd "$(dirname "$0")"

DAEMON_MODE=false
FORCE_NATIVE=false
SERVICES=()   # empty = restart everything (original behaviour)

for arg in "$@"; do
    case "$arg" in
        --daemon|-d)    DAEMON_MODE=true ;;
        --native|-n)    FORCE_NATIVE=true ;;
        backend|agentmemory|ollama) SERVICES+=("$arg") ;;
        --help|-h)
            echo "Usage: $0 [--daemon] [--native] [service ...]"
            echo ""
            echo "  No service args   — full restart (default)"
            echo "  backend           — restart only the FastAPI backend"
            echo "  agentmemory       — restart only the agentmemory server"
            echo "  ollama            — restart only the Ollama server"
            echo ""
            echo "Examples:"
            echo "  $0                        # full restart"
            echo "  $0 backend                # restart just the backend"
            echo "  $0 --daemon backend       # restart backend in background"
            echo "  $0 agentmemory ollama     # restart agentmemory + ollama"
            exit 0
            ;;
        *) echo "⚠️  Unknown argument: $arg (ignored)" ;;
    esac
done

# Helper: should we touch this service?
wants() { [ ${#SERVICES[@]} -eq 0 ] || printf '%s\n' "${SERVICES[@]}" | grep -qx "$1"; }

# ── Guard: auto-install on first run ─────────────────────────────────────────
if [ ! -f ".installed" ]; then
    echo "⚙️  LeadAgent is not installed yet — running setup first..."
    MODE_FLAG=""
    if [ "$FORCE_NATIVE" = true ]; then MODE_FLAG="--native"; fi
    ./install.sh $MODE_FLAG
    exit $?
fi

# Ensure runtime directories exist
mkdir -p leadagent-data data

# ── Build Go CLI if sources are newer than the binary ────────────────────────
CLI_BIN="cli/leadagent"
CLI_SRC="cli/main.go"
if command -v go &>/dev/null; then
    if [ ! -f "$CLI_BIN" ] || [ "$CLI_SRC" -nt "$CLI_BIN" ] || [ "cli/go.mod" -nt "$CLI_BIN" ]; then
        echo "🔨 Rebuilding leadagent CLI..."
        (cd cli && go build -o leadagent . && echo "   ✓ CLI built: cli/leadagent")
    fi
else
    echo "⚠️  go not found — skipping CLI build (install Go to enable auto-build)"
fi

# ── Kill / stop selected services ────────────────────────────────────────────

stop_native_service() {
    local port=$1 label=$2
    # Kill by tag first
    local PIDS
    PIDS=$(pgrep -f "LEADAGENT_TAG=true" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        for PID in $PIDS; do
            if ps -p "$PID" -o args= 2>/dev/null | grep -q "$label"; then
                echo "   Terminating $label process $PID..."
                kill -9 "$PID" 2>/dev/null || true
            fi
        done
    fi
    # Port sweep
    local PORT_PIDS
    PORT_PIDS=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$PORT_PIDS" ]; then
        for PID in $PORT_PIDS; do
            if ps -p "$PID" -o args= 2>/dev/null | grep -q "LEADAGENT_TAG=true"; then
                echo "   Killing tagged process $PID on port $port..."
                kill -9 "$PID" 2>/dev/null || true
            fi
        done
    fi
}

if wants backend; then
    if [ "$FORCE_NATIVE" = true ] || ! docker info &>/dev/null; then
        echo "🧹 Stopping native backend (port 8001)..."
        stop_native_service 8001 "main.py"
    else
        echo "🧹 Stopping Docker backend (port 8000)..."
        stop_native_service 8000 "main.py"
    fi
fi
if wants agentmemory; then
    echo "🧹 Stopping agentmemory (port 3111)..."
    stop_native_service 3111 "agentmemory"
fi
if wants ollama; then
    echo "🧹 Stopping ollama (port 11434)..."
    stop_native_service 11434 "ollama"
fi

# Full-restart also cleans up anything else tagged as ours
if [ ${#SERVICES[@]} -eq 0 ]; then
    echo "🧹 Cleaning up all LeadAgent processes..."
    # Kill native iii/agentmemory processes that hold ports 3111/3112
    pgrep -x iii 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    PIDS=$(pgrep -f "LEADAGENT_TAG=true" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        for PID in $PIDS; do
            echo "   Terminating tagged LeadAgent process $PID..."
            kill -9 "$PID" 2>/dev/null || true
        done
    fi
fi

sleep 1

export LEADAGENT_TAG=true

# Load environment variables from .env if present
if [ -f "backend/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source backend/.env
    set +a
fi

# Load workspace path from config.json
CONFIG_FILE="leadagent-data/config.json"
WORKSPACE=""
if [ -f "$CONFIG_FILE" ]; then
    SAVED_PATH=$(grep -o '"projects_dir": "[^"]*' "$CONFIG_FILE" | cut -d'"' -f4)
    if [ -n "$SAVED_PATH" ]; then
        WORKSPACE="$SAVED_PATH"
    fi
fi
# Fall back to env var only if explicitly set by the user — never default to $HOME,
# which would mirror the entire home directory before onboarding has run.
if [ -z "$WORKSPACE" ] && [ -n "$LEADAGENT_WORKSPACE" ]; then
    WORKSPACE="$LEADAGENT_WORKSPACE"
fi
export LEADAGENT_WORKSPACE="$WORKSPACE"

# ── Service launchers ─────────────────────────────────────────────────────────

_ensure_ollama_model() {
    local model="${LEADAGENT_OLLAMA_MODEL:-llama3.2:3b}"
    if ollama list 2>/dev/null | grep -q "^${model} "; then
        return 0
    fi
    echo "   Pulling Ollama model '${model}' in background (first-time setup)..."
    ollama pull "${model}" &>leadagent-data/ollama-pull.log &
}

start_ollama() {
    if command -v ollama &>/dev/null; then
        if ! nc -z localhost 11434 2>/dev/null; then
            echo "   Starting Ollama server..."
            env LEADAGENT_TAG=true ollama serve &>leadagent-data/ollama.log &
            sleep 2
        else
            echo "   Ollama already running on port 11434."
        fi
        _ensure_ollama_model
    else
        echo "   Ollama not found on PATH. If it's the Mac App, please open it manually."
    fi
}

# ── Docker mode ───────────────────────────────────────────────────────────────
_docker_ready() {
    local i=0
    while [ $i -lt 15 ]; do
        docker info &>/dev/null && return 0
        printf "   Waiting for Docker daemon... (%ds)\n" "$((i*2))"
        sleep 2
        i=$((i+1))
    done
    return 1
}
if [ "$FORCE_NATIVE" = false ] && docker info &>/dev/null; then
    _docker_ready || { echo "⚠️  Docker daemon not ready after 30s — falling through to native mode."; }
    echo "🐳 Docker is running — starting LeadAgent via Docker Compose..."
    if [ -n "$LEADAGENT_WORKSPACE" ]; then
        echo "   Workspace mirrored: $LEADAGENT_WORKSPACE"
    else
        echo "   Workspace: not set (configure via onboarding)"
    fi

    if [ ${#SERVICES[@]} -eq 0 ]; then
        if ! nc -z localhost 11434 2>/dev/null; then
            echo "   Ollama not detected natively, will use Docker container..."
        fi
        # Tear down first so stale containers don't block port binding
        docker compose down --remove-orphans 2>/dev/null || true
        docker compose up -d --build
        # Pull configured Ollama model if not already present (background, non-blocking)
        _OLLAMA_MODEL="${LEADAGENT_OLLAMA_MODEL:-llama3.2:3b}"
        if ! docker exec leadagent-ollama ollama list 2>/dev/null | grep -q "^${_OLLAMA_MODEL} "; then
            echo "   Pulling Ollama model '${_OLLAMA_MODEL}' in background..."
            docker exec leadagent-ollama ollama pull "${_OLLAMA_MODEL}" &>leadagent-data/ollama-pull.log &
        fi
    else
        COMPOSE_SERVICES=()
        for svc in "${SERVICES[@]}"; do
            case "$svc" in
                backend) COMPOSE_SERVICES+=("backend") ;;
                ollama)  COMPOSE_SERVICES+=("ollama") ;;
            esac
        done
        if [ ${#COMPOSE_SERVICES[@]} -gt 0 ]; then
            echo "   Restarting docker compose services: ${COMPOSE_SERVICES[*]}"
            docker compose up -d --build "${COMPOSE_SERVICES[@]}"
        fi
    fi

    echo ""
    echo "🚀 LeadAgent is running."
    echo "   Logs:    docker compose logs -f backend"
    echo "   Health:  leadagent health"
    echo "   Stop:    docker compose down"
    exit 0
fi

# ── Native fallback ───────────────────────────────────────────────────────────
if [ "$FORCE_NATIVE" = true ]; then
    echo "🖥️  Native mode — starting without Docker..."
    # Tear down any running Docker stack so ports don't conflict
    if docker info &>/dev/null 2>&1; then
        docker compose down --remove-orphans 2>/dev/null || true
    fi
else
    echo "⚠️  Docker not available — starting natively..."
fi
export PYTHONPATH=$PYTHONPATH:.
export PATH="$HOME/.leadagent/bin:$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if wants ollama; then start_ollama; fi

if wants backend; then
    # Find venv python
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
        nohup env LEADAGENT_TAG=true "$PYTHON" backend/main.py LEADAGENT_TAG=true > leadagent-data/daemon.log 2>&1 &
        
        # Start Slack Bot if tokens are present
        if grep -q "SLACK_BOT_TOKEN=xoxb-" leadagent-data/config.json 2>/dev/null || [ -n "$SLACK_BOT_TOKEN" ]; then
             echo "🚀 Launching Slack Bot daemon..."
             nohup env LEADAGENT_TAG=true "$PYTHON" backend/slack_bot.py > leadagent-data/slack_bot.log 2>&1 &
        fi
    else
        # In non-daemon mode, we run the bot in the background but keep the backend in foreground
        if [ -n "$SLACK_BOT_TOKEN" ]; then
             echo "🚀 Launching Slack Bot daemon..."
             nohup env LEADAGENT_TAG=true "$PYTHON" backend/slack_bot.py > leadagent-data/slack_bot.log 2>&1 &
        fi
        exec env LEADAGENT_TAG=true "$PYTHON" backend/main.py LEADAGENT_TAG=true
    fi
fi
