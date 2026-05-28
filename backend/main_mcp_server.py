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

BACKEND_URL = os.environ.get("LEADAGENT_BACKEND_URL", "http://localhost:8000")
SESSION_ID = os.environ.get("LEADAGENT_SESSION_ID", "default")

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
                "session_id": f"{SESSION_ID}:subagent" # scoped session
            },
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
        _send({"jsonrpc": "2.0", "id": id_, "result": {"tools": _TOOLS}})

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
                resp = requests.post(f"{BACKEND_URL}/memory/query", json={"cypher": cypher}, timeout=10)
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
                resp = requests.get(f"{BACKEND_URL}/v1/history", params={"session_id": SESSION_ID, "limit": limit}, timeout=10)
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
