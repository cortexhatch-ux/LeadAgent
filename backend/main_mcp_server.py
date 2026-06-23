"""Main LeadAgent MCP server — exposes advanced tools to the orchestrator.

Tools:
- parallel_agent_call: Run multiple prompts in parallel across different agents.
- memory_query: Direct Cypher query to the knowledge graph.
- semantic_search: Search episodic and semantic memory.
"""

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

port = 8000 if os.environ.get("LEADAGENT_DOCKER_MODE") else 8001
BACKEND_URL = os.environ.get("LEADAGENT_BACKEND_URL", f"http://localhost:{port}")
SESSION_ID = os.environ.get("LEADAGENT_SESSION_ID", "default")
PROJECT_ID = os.environ.get("LEADAGENT_PROJECT_ID", "default")
_CWD = os.environ.get("LEADAGENT_WORKSPACE", ".")
_API_KEY = os.environ.get("LEADAGENT_API_KEY", "")
_AUTH_HEADERS = {"X-LeadAgent-Key": _API_KEY} if _API_KEY else {}

_TOOLS = [
    {
        "name": "parallel_agent_call",
        "description": "Execute multiple prompts in parallel using different agents. "
                       "Use this to decompose a complex task into independent sub-tasks "
                       "or to gather perspectives from multiple models simultaneously.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "agent": {"type": "string", "description": "Preferred agent (claude, gemini, codex, grok, ollama)"},
                            "task_type": {"type": "string", "default": "general"}
                        },
                        "required": ["prompt"]
                    }
                }
            },
            "required": ["calls"]
        },
    },
    {
        "name": "memory_query",
        "description": "Run a Cypher query against the LeadAgent knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cypher": {"type": "string", "description": "KuzuDB compatible Cypher query"}
            },
            "required": ["cypher"]
        }
    },
    {
        "name": "semantic_search",
        "description": "Search for relevant past discussions and project memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        }
    }
]

def _evaluate_rule(tool_name: str) -> str:
    """Return the rules-engine action for this tool (allow/deny/ask).
    Falls back to 'ask' if the backend is unreachable."""
    try:
        resp = requests.post(
            f"{BACKEND_URL}/rules/evaluate",
            json={"tool_name": tool_name, "input": {}, "agent": "mcp", "session_id": SESSION_ID},
            headers=_AUTH_HEADERS,
            timeout=2,
        )
        if resp.ok:
            return resp.json().get("action", "ask")
    except Exception:
        pass
    return "ask"


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def _call_agent(call: dict) -> str:
    try:
        resp = requests.post(
            f"{BACKEND_URL}/v1/prompt",
            json={
                "prompt": call["prompt"],
                "preferred_agent": call.get("agent"),
                "task_type": call.get("task_type", "general"),
                "session_id": f"{SESSION_ID}:subagent",
                "project_id": PROJECT_ID,
                "cwd": _CWD,
            },
            headers=_AUTH_HEADERS,
            timeout=120,
            stream=True
        )
        resp.raise_for_status()
        chunks = []
        for chunk in resp.iter_lines(decode_unicode=True):
            if chunk and not chunk.startswith("__TIMING__"):
                chunks.append(chunk)
        return "".join(chunks)
    except Exception as e:
        return f"Error calling agent: {e}"

def _handle(msg: dict) -> None:
    method = msg.get("method", "")
    id_ = msg.get("id")

    if method == "initialize":
        _send({
            "jsonrpc": "2.0",
            "id": id_,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "leadagent_main", "version": "1.0"},
            },
        })

    elif method == "tools/list":
        # Filter tools through rules before handing schemas to the agent.
        # Any tool with a "deny" rule at this scope is stripped entirely —
        # the agent never knows it exists.
        visible = []
        for tool in _TOOLS:
            action = _evaluate_rule(tool["name"])
            if action != "deny":
                visible.append(tool)
        _send({"jsonrpc": "2.0", "id": id_, "result": {"tools": visible}})

    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})

        if name == "parallel_agent_call":
            calls = args.get("calls", [])
            with ThreadPoolExecutor(max_workers=min(len(calls), 5)) as pool:
                results = list(pool.map(_call_agent, calls))
            
            output = []
            for i, res in enumerate(results):
                agent = calls[i].get("agent", "auto")
                output.append(f"--- Result from {agent} ---\n{res}\n")
            
            _send({
                "jsonrpc": "2.0",
                "id": id_,
                "result": {
                    "content": [{"type": "text", "text": "\n".join(output)}],
                    "isError": False
                }
            })

        elif name == "memory_query":
            cypher = args.get("cypher", "")
            try:
                resp = requests.post(f"{BACKEND_URL}/memory/query", json={"cypher": cypher}, headers=_AUTH_HEADERS, timeout=10)
                resp.raise_for_status()
                res = resp.json()["result"]
                _send({
                    "jsonrpc": "2.0",
                    "id": id_,
                    "result": {"content": [{"type": "text", "text": json.dumps(res)}], "isError": False}
                })
            except Exception as e:
                _send({
                    "jsonrpc": "2.0",
                    "id": id_,
                    "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}
                })

        elif name == "semantic_search":
            query = args.get("query", "")
            limit = args.get("limit", 5)
            try:
                resp = requests.get(f"{BACKEND_URL}/v1/history", params={"session_id": SESSION_ID, "limit": limit}, headers=_AUTH_HEADERS, timeout=10)
                resp.raise_for_status()
                res = resp.json()
                _send({
                    "jsonrpc": "2.0",
                    "id": id_,
                    "result": {"content": [{"type": "text", "text": json.dumps(res)}], "isError": False}
                })
            except Exception as e:
                _send({
                    "jsonrpc": "2.0",
                    "id": id_,
                    "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}
                })

def main():
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
            _handle(msg)
        except Exception:
            pass

if __name__ == "__main__":
    main()
