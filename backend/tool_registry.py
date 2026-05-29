"""Tool registry — canonical list of known tools across all agents.

On backend startup, any tool not yet in KuzuDB is seeded with action="ask".
This means every tool call goes to the user by default until explicitly
set to allow or deny via the rules API.

Tool names follow each agent's own naming convention so the rules engine
can match them directly from the tool_name field in ask_permission calls.
"""

from backend.db import db

# ── Known tools per agent ─────────────────────────────────────────────────────

CLAUDE_TOOLS = [
    ("Bash",        "Execute shell commands"),
    ("Read",        "Read files from the filesystem"),
    ("Write",       "Write or overwrite files"),
    ("Edit",        "Make targeted edits to files"),
    ("Glob",        "Find files by pattern"),
    ("Grep",        "Search file contents with regex"),
    ("LS",          "List directory contents"),
    ("WebFetch",    "Fetch content from a URL"),
    ("WebSearch",   "Search the web"),
    ("TodoRead",    "Read the todo list"),
    ("TodoWrite",   "Write to the todo list"),
]

GEMINI_TOOLS = [
    ("run_shell_command", "Execute shell commands"),
    ("write_file",        "Write or overwrite files"),
    ("read_file",         "Read files from the filesystem"),
    ("replace",           "Make targeted edits to files"),
    ("glob",              "Find files by pattern"),
    ("find_files",        "Find files by name or pattern"),
    ("web_search",        "Search the web"),
    ("web_fetch",         "Fetch content from a URL"),
]

# LeadAgent's own MCP tools (main_mcp_server.py)
LEADAGENT_TOOLS = [
    ("parallel_agent_call", "Run multiple prompts in parallel across agents"),
    ("memory_query",        "Run a Cypher query against the knowledge graph"),
    ("semantic_search",     "Search past discussions and project memories"),
]

ALL_TOOLS: list[tuple[str, str]] = CLAUDE_TOOLS + GEMINI_TOOLS + LEADAGENT_TOOLS


def seed_default_rules() -> int:
    """Insert a default ask rule for every tool not yet registered.
    Returns the number of new rules added."""
    existing = {row[1] for row in db.list_rules()}  # row[1] = tool_pattern
    added = 0
    for tool_name, reason in ALL_TOOLS:
        if tool_name not in existing:
            db.add_rule(
                tool_pattern=tool_name,
                action="ask",
                scope="global",
                reason=reason,
                priority=0,
            )
            added += 1
    if added:
        print(f"[tool_registry] Seeded {added} default tool rules.")
    return added
