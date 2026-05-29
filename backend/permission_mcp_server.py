"""Stdio JSON-RPC MCP server — exposes ask_permission to the claude subprocess.

Claude calls this server when --permission-prompt-tool is set.  The server
forwards each request to the LeadAgent backend HTTP API, blocks until the
user decides (via the CLI), and returns the decision to claude.
"""

import json
import os
import sys

import requests

BACKEND_URL = os.environ.get("LEADAGENT_BACKEND_URL", "http://localhost:8000")
SESSION_ID = os.environ.get("LEADAGENT_SESSION_ID", "default")
AGENT_NAME = os.environ.get("LEADAGENT_AGENT_NAME", "claude")

# Rules are evaluated via the backend HTTP API so this server stays stateless.
# The backend holds the DB connection and the rules engine.
_RULES_URL = f"{BACKEND_URL}/rules/evaluate"

_TOOLS = [
    {
        "name": "ask_permission",
        "description": "Request user permission before running a tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool being invoked",
                },
                "input": {
                    "type": "object",
                    "description": "Arguments the tool would receive",
                },
                "tool_use_id": {
                    "type": "string",
                    "description": "Opaque ID from the caller",
                },
            },
            "required": ["tool_name"],
        },
    }
]


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> None:
    method = msg.get("method", "")
    id_ = msg.get("id")

    if method == "initialize":
        _send(
            {
                "jsonrpc": "2.0",
                "id": id_,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "leadagent_perm", "version": "1.0"},
                },
            }
        )

    elif method == "notifications/initialized":
        pass  # notification — no response

    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": id_, "result": {"tools": _TOOLS}})

    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        if name == "ask_permission":
            _ask_permission(id_, args)
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": id_,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                }
            )

    elif id_ is not None:
        _send(
            {
                "jsonrpc": "2.0",
                "id": id_,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        )


def _ask_permission(id_: object, args: dict) -> None:
    tool_name = args.get("tool_name", "")
    input_ = args.get("input", {})

    # ── Rules layer: evaluate before asking the user ──────────────────────────
    try:
        rule_resp = requests.post(
            _RULES_URL,
            json={"tool_name": tool_name, "input": input_, "agent": AGENT_NAME, "session_id": SESSION_ID},
            timeout=5,
        )
        if rule_resp.ok:
            rule = rule_resp.json()
            action = rule.get("action", "ask")
            reason = rule.get("reason", "")
            if action == "allow":
                _send({
                    "jsonrpc": "2.0", "id": id_,
                    "result": {"content": [{"type": "text", "text": json.dumps({
                        "behavior": "allow", "updatedInput": input_,
                    })}], "isError": False},
                })
                return
            if action == "deny":
                _send({
                    "jsonrpc": "2.0", "id": id_,
                    "result": {"content": [{"type": "text", "text": json.dumps({
                        "behavior": "deny",
                        "message": reason or f"Blocked by MCP rule (tool: {tool_name}).",
                    })}], "isError": False},
                })
                return
            # action == "ask" — fall through to user prompt
    except Exception:
        pass  # rules service unavailable — fall through to user prompt
    # ─────────────────────────────────────────────────────────────────────────

    try:
        resp = requests.post(
            f"{BACKEND_URL}/permission/_request",
            json={
                "session_id": SESSION_ID,
                "agent": AGENT_NAME,
                "tool_name": tool_name,
                "input": input_,
            },
            timeout=5,
        )
        resp.raise_for_status()
        req_id = resp.json()["id"]

        # Block until the user decides (up to 10 minutes)
        wait_resp = requests.get(
            f"{BACKEND_URL}/permission/{req_id}/wait",
            timeout=610,
        )
        wait_resp.raise_for_status()
        decision = wait_resp.json()

    except Exception as exc:
        decision = {"behavior": "deny", "scope": "once", "message": str(exc)}

    behavior = decision.get("behavior", "deny")

    if behavior == "allow":
        payload = {
            "behavior": "allow",
            "updatedInput": decision.get("updatedInput", input_),
        }
    else:
        payload = {
            "behavior": "deny",
            "message": decision.get("message") or "User denied the request.",
        }

    _send(
        {
            "jsonrpc": "2.0",
            "id": id_,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": False,
            },
        }
    )


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        try:
            _handle(msg)
        except Exception:
            pass  # never crash the server


if __name__ == "__main__":
    main()
