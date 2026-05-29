# Architecture & Flow

LeadAgent is a structural mediator between your intent and the tools required to fulfill it — not just a prompt router.

```mermaid
graph TD
    CLI[Go CLI] -->|Prompt + Context| Router[Python Backend Router]

    subgraph Memory Layer
        AM[AgentMemory Service\nport 3111\nworking · episodic · semantic · procedural]
        DB[(KuzuDB\nEntities · Relationships\nAffinity · MCPRules)]
    end

    Router -->|Semantic search before routing| AM
    Router -->|Query task affinity + graph entities| DB
    Router -->|SLM Classification| Ollama[Ollama\nLocal LLM]
    Ollama -->|task_type, complexity, mode| Router
    Router -->|Selects Agent + Mode| MCP[MCP Permission Server]
    MCP -->|Injects Permitted Tool Schemas| AgentIface[Universal Agent Interface]

    subgraph Agents
        AgentIface -->|execute| Claude[Claude CLI]
        AgentIface -->|execute| Gemini[Gemini CLI]
        AgentIface -->|execute| Grok[Grok CLI]
        AgentIface -->|execute| Codex[Codex CLI]
    end

    AgentIface -->|Tool Calls| Tools[Universal Tool Pool\nmain_mcp_server]
    MCP -->|Validates / Blocks via Rules Engine| Tools
    Tools -.->|read_file, run_sql, write_file...| System[File System]

    Router -->|Debate Mode| Debate[Debate Engine\nGAN-style Multi-Agent]
    Debate -->|Parallel Rounds| AgentIface

    AgentIface -->|Store Q+A episodic tier| AM
    AgentIface -->|Store entities + affinity scores| DB
    Debate -->|Store synthesis episodic tier| AM

    Dashboard[Dashboard\nlocalhost:8000] -->|Live Routing + Tool Logs + Rules| Router
```

## The Agent Router

LeadAgent uses a multi-tier routing system (`backend/router.py`) to pick the best model — but it is never a black box:

- **Explicit Control:** Force a specific agent via `--agent claude` or natural language (`"Ask Gemini to summarize..."`).
- **Auto-Routing:** When no agent is specified, the system classifies the task using local regex heuristics or a zero-cost Ollama pass, then queries KuzuDB for **Affinity** — matching the task to the model that has historically performed best on similar prompts.
- **Observability:** The real-time dashboard (`http://localhost:8000/dashboard`) shows exactly which agent was selected and why (e.g., `[claude] matched: complexity=high, task=coding`).

## The MCP Rules Layer

Every tool call passes through the rules engine **before** the user is ever prompted. The evaluation chain is:

```
Agent tool call
    └─► Rules Engine (backend/rules.py)
            ├─ ALLOW  → tool executes immediately, no prompt
            ├─ DENY   → tool blocked, reason returned to agent
            └─ ASK    → forwarded to user permission prompt
```

Rules are stored in KuzuDB (`MCPRule` nodes) and evaluated in priority order. Each rule has:

| Field | Description |
|---|---|
| `tool_pattern` | Exact name or glob — `"write_file"`, `"bash*"`, `"*"` |
| `action` | `allow` / `deny` / `ask` |
| `scope` | `global`, `agent:<name>`, `session:<id>` |
| `input_match` | JSON key:value pairs that must all match the tool input |
| `priority` | Higher wins; ties broken by creation order |
| `reason` | Shown to the agent when denied |

**Manage rules via the API:**
```bash
# List all rules
curl http://localhost:8000/rules

# Deny all shell execution globally
curl -X POST http://localhost:8000/rules \
  -H 'Content-Type: application/json' \
  -d '{"tool_pattern":"bash*","action":"deny","scope":"global","reason":"Shell blocked by policy","priority":100}'

# Allow file reads without prompting
curl -X POST http://localhost:8000/rules \
  -d '{"tool_pattern":"read_file","action":"allow","scope":"global","priority":10}'

# Delete a rule
curl -X DELETE http://localhost:8000/rules/<id>
```

**Key properties:**
- **Bypass resistance** — enforcement runs in Python outside the LLM's reasoning loop; prompt engineering cannot override it.
- **Agent-agnostic** — the same rule blocks Claude, Gemini, and Grok equally.
- **Fail-open to user** — if the rules service is unavailable, the call falls through to the user permission prompt rather than silently allowing or blocking.

→ See [MCP Rules](mcp-rules.md) for the full guide: rule fields, example sets, and why prompt instructions are not a substitute.

## The Memory Layer

LeadAgent uses two complementary stores that serve different roles:

| | AgentMemory (port 3111) | KuzuDB |
|---|---|---|
| **What it stores** | Raw Q&A content, episodic history, semantic snippets | Entities, relationships, affinity scores, MCP rules |
| **How it's queried** | Semantic similarity search against prompt text | Cypher graph queries, exact entity matching |
| **When it's written** | After every successful agent response; after every debate synthesis | During context indexing; after routing decisions |
| **Role in routing** | Injects relevant past conversations as background context | Injects matched entities + selects agent by affinity score |

**Memory tiers** (AgentMemory):
- `working` — current session scratchpad
- `episodic` — completed Q&A pairs and debate syntheses
- `semantic` — distilled facts extracted from responses
- `procedural` — recurring patterns and learned workflows

Both stores feed `check_memory()` in the router before every prompt is dispatched. The result is injected as background context — the agent sees relevant history without you repeating yourself across sessions or projects.

## Universal Context Discovery

When you run `leadagent` from any directory:

1. **Auto-Discovery:** Identifies the nearest `.git` or project root.
2. **Context Injection:** Snapshots local file structure and relevant symbols.
3. **Graph Indexing:** Incrementally builds a relationship map in KuzuDB, so you can ask *"How did I solve that auth bug in my other project?"*
