# LeadAgent: Universal CLI Orchestrator

A local orchestration daemon for solo developers who want to leverage existing AI subscriptions (Claude Pro, Gemini Advanced, etc.) directly from the terminal — without per-token API costs.

> **Not a replacement for Aider, Cursor, or your existing dev tools.** LeadAgent is a "Global Brain" that sits at the OS level, providing shared memory across all your projects.

## Quick Start

Requires Python 3.10+, Go 1.25+, and at least one AI provider CLI (`claude` or `gemini`).

```bash
git clone https://github.com/your-username/LeadAgent.git
cd LeadAgent
./install.sh
```

```bash
# From any project directory
leadagent "Explain the architecture of this project"
leadagent "Refactor this module" --agent claude
leadagent debate "Microservices vs Monolith for this MVP?"
```

> Add `alias leadagent='/path/to/LeadAgent/cli/leadagent'` to your `.zshrc` to invoke globally.

## Why LeadAgent?

- **Zero Marginal Cost** — routes through your existing subscription CLIs, no API keys needed
- **Cross-Project Memory** — local [KuzuDB](http://kuzudb.github.io/docs) graph remembers decisions and history across repos
- **State-Aware Routing** — task affinity scoring + deterministic fallback DAGs for autonomous recovery
- **Privacy-First** — your knowledge graph lives entirely on your machine
- **Editor Agnostic** — works alongside any IDE via the terminal and MCP interface

## Docs

- [Architecture & Flow](docs/architecture.md) — routing pipeline, MCP layer, context discovery, full diagram
- [MCP Rules](docs/mcp-rules.md) — structural tool enforcement: block, allow, or escalate before the agent ever sees a capability
- [Debate Engine](docs/debate.md) — GAN-style adversarial multi-agent debates with umpire synthesis
- [Docker Setup](docs/docker.md) — Docker vs native mode, auth troubleshooting
- [Open Source Credits](docs/credits.md) — dependencies and related projects

## Daemon Management

```bash
./start_backend.sh --daemon   # start in background
./start_backend.sh            # start in foreground (debug)
leadagent --onboarding        # re-run setup wizard
tail -f leadagent-data/daemon.log
```

## Reset

```bash
./nuke.sh   # wipe environment and start fresh
```

---
*Open-source under the [MIT License](LICENSE). Orchestration should be neutral, auditable, and vendor-agnostic.*
