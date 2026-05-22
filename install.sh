#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"
NPM_PREFIX="$HOME/.leadagent"
VENV="$PROJECT_ROOT/leadagent"

# Parse arguments
MODE=""
for arg in "$@"; do
    case $arg in
        --native) MODE="native" ;;
        --docker) MODE="docker" ;;
    esac
done

# Colors (only when interactive)
if [ -t 1 ]; then
    C_R='\033[0m'; C_B='\033[1m'; C_G='\033[32m'; C_Y='\033[33m'; C_C='\033[36m'; C_RED='\033[31m'
else
    C_R=''; C_B=''; C_G=''; C_Y=''; C_C=''; C_RED=''
fi

say()  { printf "${C_B}${C_C}▸${C_R} %s\n" "$*"; }
ok()   { printf "  ${C_G}✓${C_R} %s\n" "$*"; }
warn() { printf "  ${C_Y}!${C_R} %s\n" "$*"; }
err()  { printf "  ${C_RED}✗${C_R} %s\n" "$*"; }

printf "${C_B}🚀  Setting up LeadAgent${C_R}\n"
printf "    project root: %s\n\n" "$PROJECT_ROOT"

if [ -z "$MODE" ]; then
    say "Choose your installation mode:"
    echo "  1) Native (Zero containers, uses host-side CLIs)"
    echo "  2) Docker (Isolated stack, uses containerized CLIs)"
    printf "  Selection [1/2]: "
    read -r choice
    case $choice in
        1) MODE="native" ;;
        2) MODE="docker" ;;
        *) err "Invalid selection"; exit 1 ;;
    esac
    echo
fi

# ─── 0. Preflight ────────────────────────────────────────────────────────────
say "Checking required tools"
missing=()
for tool in python3 npm go; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool: $(command -v "$tool")"
    else
        err "$tool: not found"
        missing+=("$tool")
    fi
done

if [ "$MODE" = "docker" ]; then
    if ! docker info >/dev/null 2>&1; then
        err "Docker is required for --docker mode but it is not running."
        exit 1
    fi
    ok "docker: running"
fi

if [ ${#missing[@]} -gt 0 ]; then
    echo
    err "Missing required tools: ${missing[*]}"
    echo "  Install them first, then re-run ./install.sh"
    exit 1
fi

# ─── 1. Data dirs ────────────────────────────────────────────────────────────
mkdir -p "$PROJECT_ROOT/data"
mkdir -p "$PROJECT_ROOT/leadagent-data"

# ─── 2. Python venv (needed for setup_wizard and agentmemory) ────────────────
say "Setting up Python venv at ./leadagent"
needs_create=1
if [ -x "$VENV/bin/python3" ]; then
    if "$VENV/bin/python3" -c "import sys" >/dev/null 2>&1; then
        if head -1 "$VENV/bin/pip" 2>/dev/null | grep -q "$VENV"; then
            needs_create=0
        fi
    fi
fi

if [ "$needs_create" = "1" ]; then
    if [ -d "$PROJECT_ROOT/venv" ] && [ ! -d "$VENV" ]; then
        mv "$PROJECT_ROOT/venv" "$VENV"
        ok "migrated legacy ./venv -> ./leadagent"
    fi
    python3 -m venv --upgrade "$VENV"
    ok "venv ready at ./leadagent"
else
    ok "venv already present"
fi

"$VENV/bin/python3" -m pip install -q --upgrade pip
"$VENV/bin/python3" -m pip install -q -r "$PROJECT_ROOT/backend/requirements.txt"
ok "Python dependencies installed"

# ─── 3. Mode-specific setup ──────────────────────────────────────────────────
if [ "$MODE" = "native" ]; then
    say "Installing AI CLIs into $NPM_PREFIX"
    mkdir -p "$NPM_PREFIX"
    for pkg in "@anthropic-ai/claude-code" "@google/gemini-cli" "@openai/codex"; do
        printf "  installing %s ..." "$pkg"
        if npm install -g --silent --prefix "$NPM_PREFIX" "$pkg" >/dev/null 2>&1; then
            printf " ${C_G}done${C_R}\n"
        else
            printf " ${C_Y}skipped${C_R}\n"
        fi
    done
fi

# ─── 4. Go CLI ───────────────────────────────────────────────────────────────
say "Building LeadAgent CLI"
( cd "$PROJECT_ROOT/cli" && go build -o leadagent main.go )
ok "CLI built at cli/leadagent"

# ─── 5. Shell rc ─────────────────────────────────────────────────────────────
case "${SHELL##*/}" in
    zsh)  SHELL_RC="$HOME/.zshrc" ;;
    bash) SHELL_RC="$HOME/.bashrc" ;;
    *)    SHELL_RC="$HOME/.profile" ;;
esac

say "Updating $SHELL_RC"
if ! grep -q "alias leadagent=" "$SHELL_RC" 2>/dev/null; then
    {
        echo ""
        echo "# LeadAgent"
        echo "alias leadagent='$PROJECT_ROOT/cli/leadagent'"
    } >> "$SHELL_RC"
    ok "added 'leadagent' alias"
else
    ok "alias already present"
fi
if ! grep -q "\.leadagent/bin" "$SHELL_RC" 2>/dev/null; then
    echo "export PATH=\"\$HOME/.leadagent/bin:\$PATH\"" >> "$SHELL_RC"
    ok "added \$HOME/.leadagent/bin to PATH"
else
    ok "PATH entry already present"
fi

# ─── 6. Background services (Native macOS only) ──────────────────────────────
if [ "$MODE" = "native" ] && [ "$(uname -s)" = "Darwin" ]; then
    say "Installing macOS launchd services"

    PLIST_SRC="$PROJECT_ROOT/com.leadagent.daemon.plist"
    PLIST_DST="$HOME/Library/LaunchAgents/com.leadagent.daemon.plist"
    cat > "$PLIST_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.leadagent.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_ROOT/start_backend.sh</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT_ROOT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key><string>$PROJECT_ROOT</string>
        <key>PATH</key><string>$HOME/.leadagent/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>$PROJECT_ROOT/data/daemon.log</string>
    <key>StandardErrorPath</key><string>$PROJECT_ROOT/data/daemon.log</string>
</dict>
</plist>
PLIST

    WATCHDOG_SRC="$PROJECT_ROOT/com.leadagent.watchdog.plist"
    WATCHDOG_DST="$HOME/Library/LaunchAgents/com.leadagent.watchdog.plist"
    cat > "$WATCHDOG_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.leadagent.watchdog</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/python3</string>
        <string>$PROJECT_ROOT/backend/watchdog.py</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT_ROOT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key><string>$PROJECT_ROOT</string>
        <key>PATH</key><string>$HOME/.leadagent/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StartInterval</key><integer>30</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$PROJECT_ROOT/data/watchdog.log</string>
    <key>StandardErrorPath</key><string>$PROJECT_ROOT/data/watchdog.log</string>
</dict>
</plist>
PLIST

    mkdir -p "$HOME/Library/LaunchAgents"
    USAGE_SRC="$PROJECT_ROOT/com.leadagent.usage.plist"
    USAGE_DST="$HOME/Library/LaunchAgents/com.leadagent.usage.plist"
    # Ensure usage plist exists or skip
    if [ -f "$USAGE_SRC" ]; then
        cp "$USAGE_SRC" "$USAGE_DST"
    fi

    for svc in "com.leadagent.daemon:$PLIST_SRC:$PLIST_DST" "com.leadagent.watchdog:$WATCHDOG_SRC:$WATCHDOG_DST"; do
        IFS=":" read -r label src dst <<<"$svc"
        if launchctl list | grep -q "$label"; then
            launchctl unload "$dst" 2>/dev/null || true
        fi
        cp "$src" "$dst"
        launchctl load -w "$dst"
        ok "$label loaded"
    done
fi

# ─── 7. Finalize ─────────────────────────────────────────────────────────────
echo
say "Starting LeadAgent backend daemon..."
START_FLAGS="--daemon"
if [ "$MODE" = "native" ]; then
    START_FLAGS="$START_FLAGS --native"
fi
./start_backend.sh $START_FLAGS

say "Launching onboarding wizard..."
# Wait for backend to be ready
sleep 2
./cli/leadagent --onboarding

echo
printf "${C_B}🎉  Setup complete!${C_R}\n"
printf "${C_C}────────────────────────────────────────────────${C_R}\n"
echo "  The backend is running in the background."
echo "  Reload your shell:  source $SHELL_RC"
echo "  Use 'leadagent' from any project folder."
echo
echo "Useful commands:"
echo "  leadagent doctor          — full environment check"
echo "  leadagent health          — backend status"
echo "  tail -f data/daemon.log   — backend logs"
printf "${C_C}────────────────────────────────────────────────${C_R}\n"
