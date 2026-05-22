# LeadAgent: Universal CLI Orchestrator

LeadAgent is a local orchestration daemon designed for solo developers who want to leverage their existing AI subscriptions (Claude Pro, Gemini Advanced, etc.) directly from the terminal, without the overhead of per-token API costs.

**Note: LeadAgent is not a replacement for Aider, Cursor, or your existing dev tools.** It is a specialized "Global Brain" that sits at the OS level, providing a shared memory layer across all your projects.

## 🧠 Why LeadAgent?

Most AI orchestrators assume you want to pay for API tokens. LeadAgent assumes you already pay for Pro subscriptions. It works by routing your prompts through authorized CLI harnesses (like `claude` or `gemini`) and indexing the results into a local knowledge graph.

### Key Features
*   **Zero Marginal Cost:** Routes through your existing subscription CLIs instead of requiring API keys.
*   **Cross-Project Memory:** Uses a local [KuzuDB](http://kuzudb.github.io/docs) graph to remember architectural decisions, library preferences, and debugging history across different repositories.
*   **State-Aware Orchestration:** Intelligent routing based on task affinity (e.g., sending architecture reviews to Claude and research tasks to Gemini) and deterministic fallback DAGs for autonomous recovery.
*   **Privacy-First:** Your knowledge graph lives entirely on your machine. No proprietary code or session data is ever sent to a centralized "SaaS brain."
*   **Editor Agnostic:** Works alongside any IDE (Cursor, VS Code, Neovim) by operating at the terminal level and exposing an MCP (Model Context Protocol) interface.

## 🚀 Quick Start

LeadAgent consists of a **Python Backend** (the "Brain") and a **Go CLI** (the "Voice").

### 1. Prerequisites
Ensure you have Python 3.10+, Go 1.25+, and the AI provider CLIs (`claude` or `gemini`) installed.

### 2. Installation & Automated Setup
The unified installer handles prerequisite checks, venv creation, CLI building, background daemon startup, and the interactive onboarding wizard.

```bash
# Clone and install
git clone https://github.com/your-username/LeadAgent.git
cd LeadAgent
./install.sh
```

### 3. Running the Daemon & Onboarding (Optional / Restart)
Manual execution is only needed if you need to restart the services or re-run the configuration wizard.

LeadAgent uses a **Dual-Path** architecture—it adapts to your environment automatically:
*   **Mode A: Docker (Preferred)** - If Docker is running, it launches the containerized stack.
*   **Mode B: Native (Zero Containers)** - If Docker is stopped, it runs natively on your host.

```bash
# Start/Restart the backend (detects mode automatically)
./start_backend.sh

# Re-run onboarding wizard
leadagent --onboarding
```

### 4. Basic Usage
Once setup is complete, you can use `leadagent` from **any project directory** on your machine. It automatically discovers the local context and merges it into your global knowledge graph.

```bash
# General query (from any folder)
leadagent "Explain the architecture of this project"

# Force a specific agent for a coding task
leadagent "Refactor this module" --agent claude

# Run an adversarial debate
leadagent debate "Should we use Microservices or a Monolith for this MVP?"

# Advanced Debate Options
leadagent debate "Topic" --no-context  # Skip local project indexing
leadagent debate "Topic" --rounds 5    # Set the number of adversarial rounds
```

> **Pro Tip:** Add `alias leadagent='/path/to/LeadAgent/cli/leadagent'` to your `.zshrc` or `.bash_profile` to invoke the orchestrator globally.

## 🧠 Universal Context Discovery
LeadAgent isn't restricted to its own directory. When you run a command:
1.  **Auto-Discovery:** It identifies the nearest `.git` or project root.
2.  **Context Injection:** It snapshots the local file structure and relevant symbols.
3.  **Graph Indexing:** It incrementally builds a relationship map in KuzuDB, allowing you to ask questions like *"How did I solve that auth bug in my other project?"*

## 🐳 Docker Support

LeadAgent uses Docker Compose as its preferred environment. It orchestrates the backend and the various agent daemons (Claude, Gemini, etc.) into an isolated network.

### ⚠️ Troubleshooting: Docker Authentication
When authenticating agents inside Docker (via `leadagent --onboarding` or `docker exec`), you may encounter the following:
*   **Gemini Termination Issue:** The Gemini CLI can occasionally hang or fail to terminate the TTY session after a successful login. 
*   **The Fix:** If the terminal becomes unresponsive after login, you can safely **manually kill the terminal window or process**. The authentication state is persisted in the container's volume. LeadAgent's backend now includes robust process termination to prevent these hangs from affecting the orchestrated stack.
*   **Codex Autonomous Mode:** LeadAgent uses `codex exec --json` with the bypass flag. This is the most reliable way to receive structured events without manual sandbox prompts.

> **Note on Choice:** LeadAgent is "Docker-First but not Docker-Only." If you prefer a 100% native execution with zero containers, simply stop your Docker daemon and run `./start_backend.sh`. The onboarding wizard will then correctly guide you through local system setup instead of containerized setup.

## 🎭 Live Demo: The Orchestrator in Action

LeadAgent doesn't just route prompts; it forces models into adversarial collaboration to find the "ground truth." 

**Check out a real-world example of the State-Aware Debate Engine:**
👉 [**Ontological Debate: The Game of Thrones Blueprint Crisis**](DEBATE_EXAMPLE.md) — *Watch Claude, Gemini, and Codex argue about narrative structure and character causality.*

### ⚔️ The Power of Adversarial Collaboration

Most LLM interactions are "echo chambers"—the model tries to be helpful by agreeing with your premises. LeadAgent’s **Debate Engine** breaks this cycle:

*   **Bias Neutralization:** Forcing different models to critique each other flushes out model-specific biases.
*   **Hallucination Defense:** Adversaries are incentivized to call out hallucinations to "win" the round.
*   **The Umpire Protocol:** A neutral agent identifies logical fallacies and synthesizes a final **Consensus**.

## 🛠 How it Works

1.  **The Daemon:** A background process watches your filesystem and manages the knowledge graph.
2.  **The CLI:** A Go-based binary (`leadagent`) that serves as your primary interface.
3.  **The Graph:** Every interaction is parsed into entities and relationships, creating a persistent, decaying memory of your work.
4.  **The Router:** Dynamically selects the best agent for the job based on historical performance and current subscription quotas.

## ⚖️ Honest Position

LeadAgent is a **developer utility**, not a replacement for the underlying models or the editors you love. It is a "cat-and-mouse" tool that bridges the gap between web-based Pro subscriptions and the power of a programmatic CLI workflow. 

It is best suited for:
*   Solo developers managing multiple side projects.
*   Users who hit rate limits on single models and want intelligent failover.
*   Privacy-conscious developers who want a local, cross-project knowledge graph that they own entirely.

## 🏛️ Open Source Pedigree

LeadAgent is built on the shoulders of these incredible open-source projects:

### Backend (Python)
*   [**KuzuDB**](http://kuzudb.github.io/docs): The embeddable graph database that powers our cross-project memory.
*   [**AgentMemory**](https://github.com/rohitg00/agentmemory): Providing the optional semantic storage and indexing layer.
*   [**FastAPI**](https://fastapi.tiangolo.com/): Our high-performance routing gateway.
*   [**Pydantic**](https://docs.pydantic.dev/): Ensuring strict data integrity.
*   [**Pexpect**](https://pexpect.readthedocs.io/): Driving the terminal interaction for subscription CLIs.

### CLI (Go)
*   [**Glamour**](https://github.com/charmbracelet/glamour): Polished, ANSI-highlighted markdown.
*   [**Lip Gloss**](https://github.com/charmbracelet/lipgloss): High-contrast terminal UI style.
*   [**Chroma**](https://github.com/alecthomas/chroma): Universal syntax highlighting.

## 🌐 Related Ecosystem Projects

If LeadAgent’s philosophy resonates with you, check out:

*   [**OpenCode**](https://github.com/anomalyco/opencode): Fully open-source, terminal-native AI coding agent.
*   [**Mysti**](https://github.com/DeepMyst/Mysti): VS Code extension that brings multiple AI providers (Claude, Gemini, Codex, and more) together in collaborative brainstorm mode, letting agents debate and synthesize solutions instead of working in isolation.

## 🧹 Maintenance & Reset

If you need to completely wipe your LeadAgent environment and start fresh:
```bash
./nuke.sh
```

---
*LeadAgent is open-source under the [MIT License](LICENSE). We believe that orchestration should be neutral, auditable, and vendor-agnostic.*
