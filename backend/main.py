import os
import queue as _queue
import shutil
import socket
import threading
import time as _time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from backend.dashboard_html import DASHBOARD_HTML
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import json
import asyncio

from backend.models import Entity, Relationship
from backend.db import db
from backend.memory_client import memory_client
from backend.router import agent_router
from backend.agents import agent_factory, CLIAgent, is_installed_anywhere
from backend.roles import ROLE_DESCRIPTIONS
from backend.scraper import scraper
from backend.permissions import broker

_START_TIME = _time.time()

app = FastAPI(title="LeadAgent Daemon")


@app.on_event("startup")
async def _startup():
    db.start_janitor()


class ChatRequest(BaseModel):
    prompt: str
    task_type: str = "general"
    preferred_agent: str = None
    session_id: str = "default"
    cwd: str = "."
    parallel: bool = False


class DebateRequest(BaseModel):
    prompt: str
    rounds: int = 3
    agents: List[str] = None
    cwd: str = "."
    force: bool = False


class ProjectInitRequest(BaseModel):
    path: str
    agents: List[str] = ["claude", "gemini", "codex", "grok"]


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms/1000:.2f}s"
    return f"{ms:.0f}ms"


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "LeadAgent Daemon is running"}


@app.get("/health")
async def health():
    uptime = round(_time.time() - _START_TIME, 1)

    db_status = "ok"
    entity_count = 0
    try:
        rows = db.query_all("MATCH (e:Entity) RETURN count(e)")
        entity_count = int(rows[0][0]) if rows else 0
    except Exception:
        db_status = "error"

    mem_host = "host.docker.internal" if os.environ.get("LEADAGENT_DOCKER_MODE") else "localhost"
    mem_status = "ok" if _port_open(mem_host, 3111) else "unavailable"

    from backend.agents_catalog import enabled_agents, is_authenticated, AGENTS
    enabled = enabled_agents()
    agents = {}
    for name in AGENTS:
        installed = is_installed_anywhere(name)
        signed_in = is_authenticated(name) if installed else False
        is_enabled = name in enabled
        usable = installed and is_enabled and signed_in is not False
        agents[name] = {
            "installed": installed,
            "enabled":   is_enabled,
            "signed_in": signed_in,
            "available": usable,
        }

    overall = "ok"
    if db_status == "error":
        overall = "error"
    elif mem_status == "unavailable" or not any(a["available"] for a in agents.values()):
        overall = "degraded"

    return {
        "status": overall,
        "uptime_seconds": uptime,
        "components": {
            "database":       {"status": db_status, "entity_count": entity_count},
            "memory_service": {"status": mem_status},
            "agents": agents,
        },
    }


@app.post("/project/init")
async def init_project(request: ProjectInitRequest):
    scraper.start_watcher(request.path, request.agents)
    return {"status": "scanning"}


@app.post("/onboard")
async def onboard():
    """Non-interactive environment check — never prompts (would deadlock here)."""
    from backend.onboarding import onboarding
    asyncio.create_task(
        asyncio.to_thread(onboarding.check_and_fix_environment, interactive=False)
    )
    return {"status": "onboarding_started", "interactive": False}


@app.get("/v1/audit/{session_id}")
async def audit_session(session_id: str):
    """Reconstruct the causal narrative for a session (Consensus Round 4)."""
    # 1. Get recent Q&A for this session from episodic memory
    history = memory_client.search(f"session:{session_id}", limit=10)
    
    narrative = []
    for item in history:
        agent = item["metadata"].get("agent")
        prompt = item["content"].split("\nAssistant:")[0].replace("User: ", "")
        task_type, complexity = agent_router._classify_task(prompt)
        
        # 2. Query DB for the rationale used at the time
        affinity_rows = db.query_all(
            "MATCH (t:TaskType {name: $t})-[r:AFFINITY]->(a:AgentNode {name: $a}) RETURN r.score",
            {"t": task_type, "a": agent}
        )
        score = affinity_rows[0][0] if affinity_rows else 0.5
        
        # 3. Check for failures
        failure_rows = db.query_all(
            "MATCH (a:AgentNode {name: $a})-[r:FAILED_BECAUSE]->(e:ErrorType) RETURN e.name, r.count",
            {"a": agent}
        )
        failures = {r[0]: r[1] for r in failure_rows}
        
        narrative.append({
            "prompt_preview": prompt[:100] + "...",
            "agent": agent,
            "rationale": {
                "task_type": task_type,
                "complexity": complexity,
                "historical_affinity": score,
                "known_failure_risks": failures
            }
        })
    
    return narrative


@app.get("/v1/roi")
async def get_roi_metrics():
    """Aggregate success rates vs cost per agent."""
    agents = ["claude", "gemini", "codex", "grok"]
    results = {}
    
    for agent in agents:
        rows = db.query_all(
            "MATCH (a:AgentNode {name: $a})<-[r:AFFINITY]-(t:TaskType) RETURN sum(r.score), sum(r.count)",
            {"a": agent}
        )
        score_sum = rows[0][0] if rows and rows[0][0] is not None else 0
        total_uses = rows[0][1] if rows and rows[0][1] is not None else 0
        
        fail_rows = db.query_all(
            "MATCH (a:AgentNode {name: $a})-[r:FAILED_BECAUSE]->(e:ErrorType) RETURN sum(r.count)",
            {"a": agent}
        )
        total_fails = fail_rows[0][0] if fail_rows and fail_rows[0][0] is not None else 0
        
        results[agent] = {
            "success_rate": (total_uses - total_fails) / total_uses if total_uses > 0 else 1.0,
            "avg_affinity": score_sum / total_uses if total_uses > 0 else 0.5,
            "total_calls": total_uses,
            "failure_count": total_fails
        }
    
    return results


@app.get("/doctor")
async def doctor():
    """Single-shot diagnostic: every check the user could care about."""
    from backend.agents_catalog import AGENTS, AGENT_ORDER, is_authenticated

    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # Tools
    for tool in ("python3", "npm", "go", "agentmemory"):
        path = shutil.which(tool)
        add(f"tool:{tool}", bool(path), path or "not on PATH")

    # Backend pieces
    try:
        rows = db.query_all("MATCH (e:Entity) RETURN count(e)")
        add("graph_db", True, f"{int(rows[0][0]) if rows else 0} entities")
    except Exception as e:
        add("graph_db", False, str(e))

    add("memory_service", _port_open("localhost", 3111),
        "port 3111 " + ("open" if _port_open("localhost", 3111) else "closed"))

    # Per-agent install + auth
    for key in AGENT_ORDER:
        spec = AGENTS[key]
        installed = _is_installed(key)
        add(f"agent:{key}:installed", installed,
            spec.display if installed else f"missing — install: {spec.npm_pkg or 'pending CLI'}")
        if installed and spec.auth_check:
            authed = is_authenticated(key)
            add(f"agent:{key}:auth", authed is True,
                "signed in" if authed is True else "not signed in or unknown")

    failed = [c for c in checks if not c["ok"]]
    return {
        "status": "ok" if not failed else "issues",
        "summary": f"{len(checks) - len(failed)}/{len(checks)} checks passed",
        "checks": checks,
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/roles")
async def get_roles():
    return ROLE_DESCRIPTIONS



# ── v1 API (Unified Gateway) ──────────────────────────────────────────────────

@app.post("/v1/prompt")
async def v1_prompt(request: ChatRequest):
    """Standardized entry point for chat requests."""
    return await chat(request)


@app.get("/v1/status")
async def v1_status():
    """Consolidated health and quota status."""
    return await health()


@app.get("/v1/history")
async def v1_history(session_id: str = "default", limit: int = 10):
    """Retrieve episodic memory for a session."""
    # This is a placeholder for a more robust history retrieval
    return memory_client.search(f"session:{session_id}", limit=limit)


@app.get("/v1/snapshot/export")
async def export_snapshot(project_id: str = "default"):
    """Export a portable context snapshot for a project (Consensus Round 4)."""
    entities = [
        {"name": row[0], "type": row[1], "description": row[2], "confidence": row[3]}
        for row in db.query_all(
            "MATCH (e:Entity) WHERE e.project_id = $pid RETURN e.name, e.type, e.description, e.confidence",
            {"pid": project_id}
        )
    ]
    relationships = [
        {"source": row[0], "target": row[1], "type": row[2], "confidence": row[3]}
        for row in db.query_all(
            "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) WHERE a.project_id = $pid RETURN a.name, b.name, r.type, r.confidence",
            {"pid": project_id}
        )
    ]
    return {
        "project_id": project_id,
        "timestamp": _time.time(),
        "entities": entities,
        "relationships": relationships
    }


class _PermissionRequestBody(BaseModel):
    session_id: str
    agent: str
    tool_name: str
    input: dict = {}


class _PermissionDecision(BaseModel):
    behavior: str           # allow | deny | stop
    scope: str = "once"    # once | session
    message: Optional[str] = None
    updated_input: Optional[dict] = None


@app.post("/permission/_request")
async def permission_request(body: _PermissionRequestBody):
    pr = broker.create(body.session_id, body.agent, body.tool_name, body.input)
    return {"id": pr.id}


@app.get("/permission/{request_id}/wait")
async def permission_wait(request_id: str):
    decision = await asyncio.to_thread(broker.wait, request_id, 600.0)
    if decision is None:
        raise HTTPException(status_code=404, detail="Unknown permission request")
    return decision


@app.post("/permission/{request_id}/decide")
async def permission_decide(request_id: str, body: _PermissionDecision):
    ok = broker.decide(request_id, body.behavior, body.scope, body.message, body.updated_input)
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown or already decided request")
    return {"status": "ok"}


def _stream_agent(agent_name: str, full_prompt: str, request: "ChatRequest", perm_q, tag: str = "primary"):
    """
    Stream one agent's response. Yields text chunks + handles quota and logic errors.
    Returns (full_response, t_first, t3, t4) via a closure — caller collects via list.
    """
    from backend.models import ErrorType
    
    agent = agent_factory.get_agent(agent_name)
    t3 = _time.perf_counter()
    t_first_box = [None]
    response_chunks = []

    chunk_q: _queue.Queue = _queue.Queue()

    def _run_agent() -> None:
        try:
            for chunk in agent.execute_stream(full_prompt, request.cwd, session_id=request.session_id):
                chunk_q.put(("chunk", chunk))
        except Exception as exc:
            chunk_q.put(("error", exc))
        finally:
            chunk_q.put(("done", None))

    threading.Thread(target=_run_agent, daemon=True).start()

    agent_done = False
    while not agent_done:
        while perm_q is not None:
            try:
                pr = perm_q.get_nowait()
                yield f"__PERMISSION_REQUEST__:{json.dumps({'id': pr.id, 'tool_name': pr.tool_name, 'input': pr.input, 'agent': pr.agent})}\n"
            except _queue.Empty:
                break

        try:
            kind, val = chunk_q.get(timeout=0.05)
        except _queue.Empty:
            continue

        if kind == "done":
            agent_done = True

        elif kind == "error":
            e = val
            error_msg = str(e)
            
            # Categorize error (Causal Taxonomy)
            etype = ErrorType.UNKNOWN
            if "AGENT_TRANSIENT_ERROR" in error_msg:
                etype = ErrorType.TRANSIENT_CAPACITY
            elif "context length" in error_msg.lower() or "too many tokens" in error_msg.lower():
                etype = ErrorType.CONTEXT_OVERFLOW
            elif "syntax" in error_msg.lower() or "lint" in error_msg.lower():
                etype = ErrorType.LINTER_ERROR
            
            db.log_agent_failure(agent_name, etype.value)
            
            if etype == ErrorType.TRANSIENT_CAPACITY:
                yield f"\n⚠️  {agent_name} is temporarily overloaded (429 Capacity). Please try again in a few seconds.\n"
            else:
                yield f"\n❌ {agent_name} failed ({etype.value}): {e}\n"
                
                # Deterministic Rollback (Consensus Round 3)
                context_cache.prune_by_tag(request.session_id, tag)
                
                fallback = agent_router.get_fallback(agent_name, etype)
                if fallback:
                    yield f"🔄 Rollback & Fallback: Switching to {fallback}...\n"
                    # Re-run with the fallback agent
                    yield from _stream_agent(fallback, full_prompt, request, None, tag="fallback")
                else:
                    # Log failure to affinity
                    agent_router.update_outcome(request.task_type, agent_name, success=False)

        else:  # "chunk"
            chunk = val
            if t_first_box[0] is None:
                t_first_box[0] = _time.perf_counter()

            response_chunks.append(chunk)
            yield chunk

    t4 = _time.perf_counter()
    full_response = "".join(response_chunks)

    if full_response.strip() and not agent_done: # Success
         agent_router.update_outcome(request.task_type, agent_name, success=True)

    memory_client.store(
        content=f"User: {request.prompt}\nAssistant: {full_response[:500]}",
        metadata={"session_id": request.session_id, "agent": agent_name, "cwd": request.cwd},
        tier="episodic",
    )
    if full_response.strip():
        threading.Thread(
            target=agent_router.learn_from_prompt,
            args=(request.prompt, full_response, agent_name),
            daemon=True,
        ).start()

    yield f"\n__TIMING__:{json.dumps({'agent': agent_name, 'agent_start': _fmt_ms((t_first_box[0] - t3) * 1000) if t_first_box[0] else 'n/a', 'agent_total': _fmt_ms((t4 - t3) * 1000)})}"


@app.post("/chat")
async def chat(request: ChatRequest):
    t0 = _time.perf_counter()

    # STEP 1: Enrich prompt with graph + memory context (delta only for this session)
    # Default context is 'primary'
    memory_hit = agent_router.check_memory(request.prompt, request.session_id)
    t1 = _time.perf_counter()
    injected_context = ""
    if memory_hit:
        injected_context = (
            "=== CONTEXT FROM KNOWLEDGE GRAPH & MEMORY ===\n"
            f"{memory_hit}\n"
            "=== END CONTEXT ===\n\n"
        )

    # STEP 2: Route — may return multiple agents for fan-out requests
    agent_names = agent_router.route_multi(request.task_type, request.prompt, request.preferred_agent)
    t2 = _time.perf_counter()

    if agent_names == ["none"]:
        raise HTTPException(status_code=503, detail="No agents available (not installed or authenticated).")

    full_prompt = injected_context + request.prompt
    is_fanout = len(agent_names) > 1
    # Parallel if explicitly requested OR natural language signals it
    run_parallel = request.parallel or (is_fanout and agent_router.detect_parallel(request.prompt))

    def _sep(name: str) -> str:
        s = "━" * 60
        return f"\n{s}\n◆  {name.upper()}\n{s}\n"

    def cli_generator():
        if request.preferred_agent and agent_names[0] != request.preferred_agent and not is_fanout:
            yield f"⚠️  {request.preferred_agent} unavailable. Routing to {agent_names[0]}.\n"

        for agent_name in agent_names:
            yield f"↳ {agent_router.get_explanation(agent_name, request.prompt)}\n"

        perm_q = broker.session_queue(request.session_id)

        if is_fanout and run_parallel:
            # ── Parallel fan-out: run all agents simultaneously, stream in completion order
            result_buf: dict[str, str] = {}
            timing_buf: dict[str, str] = {}
            done_q: _queue.Queue = _queue.Queue()

            def _buffer_one(ag: str) -> None:
                chunks: list[str] = []
                t_json = ""
                # For parallel, we tag each branch uniquely
                for item in _stream_agent(ag, full_prompt, request, None, tag=f"branch_{ag}"):
                    if item.startswith("\n__TIMING__:"):
                        t_json = item
                    else:
                        chunks.append(item)
                result_buf[ag] = "".join(chunks)
                timing_buf[ag] = t_json
                done_q.put(ag)

            for ag in agent_names:
                threading.Thread(target=_buffer_one, args=(ag,), daemon=True).start()

            for _ in agent_names:
                ag = done_q.get()
                yield _sep(ag)
                yield result_buf[ag]
                if timing_buf[ag]:
                    yield timing_buf[ag]
                yield "\n"

        else:
            # ── Sequential fan-out (default)
            for i, agent_name in enumerate(agent_names):
                tag = "primary" if i == 0 else "critic"
                if is_fanout:
                    yield _sep(agent_name)
                yield from _stream_agent(agent_name, full_prompt, request, perm_q, tag=tag)
                if is_fanout and i < len(agent_names) - 1:
                    yield "\n"

        mode = "parallel" if (is_fanout and run_parallel) else "sequential"
        yield f"\n__TIMING__:{json.dumps({'agents': agent_names, 'mode': mode, 'memory': _fmt_ms((t1 - t0) * 1000), 'routing': _fmt_ms((t2 - t1) * 1000), 'total': _fmt_ms((_time.perf_counter() - t0) * 1000)})}"

    return StreamingResponse(cli_generator(), media_type="text/plain")


@app.post("/debate")
async def debate(request: DebateRequest):
    from backend.debate import run_debate

    def generator():
        yield from run_debate(
            prompt=request.prompt,
            rounds=request.rounds,
            cwd=request.cwd,
            agents=request.agents or None,
        )

    return StreamingResponse(generator(), media_type="text/plain")


# ── memory endpoints ──────────────────────────────────────────────────────────

@app.post("/memory/entities")
async def add_entity(entity: Entity):
    try:
        db.add_entity(entity.name, entity.type, entity.description or "")
        return {"status": "success", "entity": entity.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/relationships")
async def add_relationship(rel: Relationship):
    try:
        db.add_relationship(rel.source, rel.target, rel.type)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/graph")
async def get_graph():
    entities = [
        {"name": row[0], "type": row[1], "description": row[2]}
        for row in db.query_all("MATCH (e:Entity) RETURN e.name, e.type, e.description")
    ]
    relationships = [
        {"source": row[0], "target": row[1], "type": row[2]}
        for row in db.query_all("MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) RETURN a.name, b.name, r.type")
    ]
    return {"entities": entities, "relationships": relationships}


@app.post("/memory/query")
async def query_memory(query: dict):
    try:
        cypher = query.get("cypher")
        if not cypher:
            raise HTTPException(status_code=400, detail="Missing 'cypher' field")
        return {"result": db.query_all(cypher)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/session/clear")
async def clear_session(session_id: str = "default"):
    """Reset context cache for a session — next message gets full context again."""
    from backend.context_cache import context_cache
    context_cache.invalidate(session_id)
    return {"status": "cleared", "session_id": session_id}


@app.get("/session/stats")
async def session_stats(session_id: str = "default"):
    from backend.context_cache import context_cache
    return context_cache.stats(session_id)




if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
