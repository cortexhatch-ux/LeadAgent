"""MCP Rules Engine — evaluates stored rules before escalating to the user.

Rule evaluation order:
  1. Rules are sorted by priority DESC, then created_at ASC (higher priority wins).
  2. For each rule, check: tool_pattern matches AND scope matches AND input_match matches.
  3. Return the action of the first matching rule: "allow" | "deny" | "ask".
  4. If no rule matches, return "ask" (fall through to user permission prompt).

tool_pattern matching:
  - Exact match:  "write_file"  matches only "write_file"
  - Glob prefix:  "bash*"       matches "bash", "bash_execute", etc.
  - Wildcard:     "*"           matches any tool

scope matching:
  - "global"           — applies to all agents and sessions
  - "agent:<name>"     — applies only when the given agent is running
  - "session:<id>"     — applies only to the given session

input_match:
  - JSON string of key:value pairs; ALL must be present in the tool input.
  - Empty string means no input filtering (match any input).
"""

import json
import fnmatch
from typing import Literal

from backend.db import db


Action = Literal["allow", "deny", "ask"]


def _tool_matches(pattern: str, tool_name: str) -> bool:
    return fnmatch.fnmatch(tool_name, pattern)


def _scope_matches(scope: str, agent: str, session_id: str) -> bool:
    if scope == "global":
        return True
    if scope.startswith("agent:"):
        return scope[6:] == agent
    if scope.startswith("session:"):
        return scope[8:] == session_id
    return False


def _input_matches(input_match_str: str, input_: dict) -> bool:
    if not input_match_str:
        return True
    try:
        required = json.loads(input_match_str)
        return all(input_.get(k) == v for k, v in required.items())
    except Exception:
        return True  # malformed rule — don't block


def evaluate(
    tool_name: str,
    input_: dict,
    agent: str,
    session_id: str,
) -> tuple[Action, str]:
    """Return (action, reason) for the first matching rule, or ("ask", "") if none match."""
    rows = db.list_rules()
    for row in rows:
        # row: id, tool_pattern, action, scope, reason, input_match, priority, created_at
        _, tool_pattern, action, scope, reason, input_match, *_ = row
        if (
            _tool_matches(tool_pattern, tool_name)
            and _scope_matches(scope, agent, session_id)
            and _input_matches(input_match, input_)
        ):
            return action, reason or ""
    return "ask", ""
