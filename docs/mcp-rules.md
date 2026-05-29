# MCP Rules — Structural Tool Enforcement

## The core idea

Most AI safety instructions are written as text: a `CLAUDE.md` file, a system prompt, a "don't do X" rule. These are **behavioral** — you're asking the LLM to remember and comply. LeadAgent's MCP rules layer is **structural** — it removes capabilities before the agent ever sees them.

> *CLAUDE.md rules are like a sign on the door that says "don't run in the halls."  
> MCP-layer enforcement is removing the legs from the chair — the agent literally cannot run because the capability was never handed to it.*

## Behavioral vs structural enforcement

|  | Prompt / CLAUDE.md rules | MCP-layer enforcement |
|---|---|---|
| **Enforcement point** | Inside the LLM (behavioral) | Outside the LLM (structural) |
| **Bypassable by prompt?** | Yes | No |
| **Bypassable by alias/script?** | Yes | No |
| **Adds context tokens?** | Yes | No |
| **Works across all agents?** | Only if they read the file | Yes, regardless of agent |

## How it works in LeadAgent

Every tool call an agent attempts passes through the rules engine **before** the tool schema is acted on:

```
Agent tool call
    └─► Rules Engine  (backend/rules.py + KuzuDB)
            ├─ ALLOW  → executes immediately, no prompt
            ├─ DENY   → blocked; reason returned to the agent
            └─ ASK    → forwarded to the user permission prompt
```

Rules are stored as `MCPRule` nodes in KuzuDB and evaluated in priority order. The first matching rule wins.

## Rule fields

| Field | Type | Description |
|---|---|---|
| `tool_pattern` | string | Exact name or glob — `"write_file"`, `"bash*"`, `"*"` |
| `action` | `allow` / `deny` / `ask` | What to do when the rule matches |
| `scope` | string | `global`, `agent:<name>`, `session:<id>` |
| `input_match` | JSON string | Key:value pairs that must all be present in the tool input |
| `priority` | integer | Higher wins; ties broken by creation order |
| `reason` | string | Message returned to the agent when denied |

## Managing rules

```bash
# List all active rules
curl http://localhost:8000/rules

# Block all shell execution globally (highest priority)
curl -X POST http://localhost:8000/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "tool_pattern": "bash*",
    "action": "deny",
    "scope": "global",
    "reason": "Shell execution is disabled by policy.",
    "priority": 100
  }'

# Allow file reads silently — no user prompt ever
curl -X POST http://localhost:8000/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "tool_pattern": "read_file",
    "action": "allow",
    "scope": "global",
    "priority": 10
  }'

# Block database writes for a specific agent only
curl -X POST http://localhost:8000/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "tool_pattern": "run_sql",
    "action": "deny",
    "scope": "agent:gemini",
    "input_match": "{\"query\": \"DROP\"}",
    "reason": "DROP statements are not permitted.",
    "priority": 50
  }'

# Delete a rule by ID
curl -X DELETE http://localhost:8000/rules/<rule_id>
```

## Example rule set for a production project

```bash
# 1. Never allow shell commands without asking
curl -X POST http://localhost:8000/rules \
  -d '{"tool_pattern":"bash*","action":"ask","scope":"global","priority":90}'

# 2. Read-only file access is always fine
curl -X POST http://localhost:8000/rules \
  -d '{"tool_pattern":"read_file","action":"allow","scope":"global","priority":10}'

# 3. No destructive DB operations ever
curl -X POST http://localhost:8000/rules \
  -d '{"tool_pattern":"run_sql","action":"deny","scope":"global","reason":"Destructive SQL blocked. Use a migration file instead.","priority":80}'

# 4. During a debug session, allow everything for Claude specifically
curl -X POST http://localhost:8000/rules \
  -d '{"tool_pattern":"*","action":"allow","scope":"agent:claude","priority":5}'
```

## What not to do

Don't write prompt instructions as a substitute for rules. These patterns are fragile:

```
# ❌ Fragile — the LLM may comply or may not, depending on context
"Never run DROP TABLE commands."
"Always ask before writing files."
"Do not execute shell commands."
```

These belong in `CLAUDE.md` as a reminder, not as your actual enforcement mechanism. Write a rule instead — it works whether the agent is Claude, Gemini, Grok, or any future model LeadAgent supports.

## Failure behaviour

If the rules service is unreachable (e.g. DB error at startup), tool calls fall through to the **user permission prompt** rather than silently allowing or silently blocking. The system fails safe to human oversight, not to open access.
