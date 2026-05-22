#!/usr/bin/env bash
# LeadAgent Nuke Script - Total Environment Reset
set -e

cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"

# Colors
if [ -t 1 ]; then
    C_R='\033[0m'; C_B='\033[1m'; C_G='\033[32m'; C_Y='\033[33m'; C_RED='\033[31m'; C_C='\033[36m'
else
    C_R=''; C_B=''; C_G=''; C_Y=''; C_RED=''; C_C=''
fi

say() { printf "${C_B}${C_C}▸${C_R} %s\n" "$*"; }
err() { printf "${C_RED}✗${C_R} %s\n" "$*"; }
ok()  { printf "  ${C_G}✓${C_R} %s\n" "$*"; }

# Confirmation
if [[ "$*" != *"--force"* ]]; then
    printf "${C_RED}${C_B}⚠️  WARNING: This will completely destroy your LeadAgent environment!${C_R}\n"
    printf "   - All local memory (KuzuDB) will be deleted.\n"
    printf "   - All background processes will be killed.\n"
    printf "   - The Python virtual environment will be removed.\n"
    printf "   - The CLI binary will be deleted.\n\n"
    printf "Are you absolutely sure? [y/N]: "
    read -r confirm
    if [[ "$confirm" != [yY] ]]; then
        say "Nuke aborted."
        exit 0
    fi
fi

# ─── 1. Stop Processes ────────────────────────────────────────────────────────
say "Stopping all LeadAgent processes..."

# Docker
if command -v docker >/dev/null 2>&1 && [ -f "docker-compose.yml" ]; then
    if docker compose ps >/dev/null 2>&1; then
        docker compose down -v --remove-orphans >/dev/null 2>&1 || true
        ok "Docker containers and volumes removed"
    fi
fi

# Native (Surgical Kill)
PIDS=$(pgrep -f "LEADAGENT_TAG=true" || true)
if [ ! -z "$PIDS" ]; then
    for PID in $PIDS; do
        kill -9 $PID 2>/dev/null || true
    done
    ok "Native processes terminated"
fi

# macOS Launchd
if [ "$(uname -s)" = "Darwin" ]; then
    for label in "com.leadagent.daemon" "com.leadagent.watchdog" "com.leadagent.usage"; do
        plist="$HOME/Library/LaunchAgents/$label.plist"
        if [ -f "$plist" ]; then
            launchctl unload "$plist" 2>/dev/null || true
            rm "$plist"
            ok "macOS service $label removed"
        fi
    done
fi

# ─── 2. Clean Filesystem ──────────────────────────────────────────────────────
say "Cleaning filesystem..."

DIRS=("data" "leadagent-data" "leadagent" "cli/leadagent" "backend/__pycache__")
for dir in "${DIRS[@]}"; do
    if [ -e "$PROJECT_ROOT/$dir" ]; then
        rm -rf "$PROJECT_ROOT/$dir"
        ok "Removed $dir"
    fi
done

# Plists in root
rm -f com.leadagent.*.plist

# ─── 3. Shell Cleanup Instructions ──────────────────────────────────────────
echo
say "Nuke complete."
printf "${C_Y}Note:${C_R} To fully clean your shell profile, manually remove the 'leadagent' alias\n"
printf "      and PATH entries from your ${C_B}.zshrc${C_R} or ${C_B}.bashrc${C_R}.\n\n"
printf "${C_G}Environment reset to zero.${C_R}\n"
