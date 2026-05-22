# LeadAgent: Universal Orchestrator

LeadAgent is designed to be a "Global Brain" that you can invoke from any project directory.

## 1. Quick Setup
Run the unified installer. This handles environment setup, CLI building, background daemon startup, and launches the onboarding wizard:

```bash
./install.sh [--native | --docker]
```

## 2. Global Usage
Once installed, use the `leadagent` command from **any folder**:

```bash
# General chat
leadagent "Explain this codebase"

# Force a specific agent
leadagent "Refactor this module" --agent claude

# Re-run onboarding
leadagent --onboarding
```

## 3. Maintenance & Cleanup
- **Restart Backend:** `./start_backend.sh --daemon`
- **Total Reset:** Run `./nuke.sh` to wipe all data and processes.

## 4. Advanced Features
- **Dashboard:** Access the real-time observability dashboard at `http://localhost:8000/dashboard`. Monitor quotas, agent health, and activity.
- **REST API:** Unified gateway at `http://localhost:8000/v1/prompt`. Standardized MCP-to-model translation.
- **Memory Hygiene:** Automatic confidence-weighted decay and pruning.
- **Proactive Indexing:** Background file watcher that feeds the knowledge graph incrementally.

## 5. How it works across projects
- **Context:** LeadAgent automatically picks up the context of the directory you are currently in.
- **Memory:** All knowledge from all projects is stored in the central Graph DB within your installation directory (e.g., `./data/db`).
- **Subscriptions:** It leverages your `claude` CLI login and your Google OAuth token globally.

## 5. Maintenance
- **Update CLI:** If you change the Go code, run `go build -o leadagent main.go` in the `cli/` folder.
- **Update Backend:** Simply restart the Python daemon.
