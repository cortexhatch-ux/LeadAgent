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
from dotenv import load_dotenv

load_dotenv()

from backend.models import Entity, Relationship, ErrorType
from backend.db import db
from backend.memory_client import memory_client
from backend.context_cache import context_cache
from backend.router import agent_router
from backend.agents import agent_factory, is_installed_anywhere
from backend.quota import quota_manager

from backend.roles import ROLE_DESCRIPTIONS
from backend.scraper import scraper
from backend.permissions import broker

_START_TIME = _time.time()

app = FastAPI(title="LeadAgent Daemon")

from backend.security import GuardMiddleware

# Reject LAN peers and cross-origin browser requests (CSRF / DNS rebinding).
app.add_middleware(GuardMiddleware)


@app.on_event("startup")
async def _startup():
    db.start_janitor()
    from backend.tool_registry import seed_default_rules
    seed_default_rules()


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
        return f"{ms / 1000:.2f}s"
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
    db_error = None
    entity_count = 0
    try:
        rows = db.query_all("MATCH (e:Entity) RETURN count(e)")
        entity_count = int(rows[0][0]) if rows else 0
    except Exception as e:
        db_status = "error"
        db_error = str(e)

    # Try both common hosts for the memory service
    mem_status = "unavailable"
    mem_detail = "Checked localhost and host.docker.internal on port 3111"
    for host in ("localhost", "host.docker.internal", "127.0.0.1"):
        if _port_open(host, 3111):
            mem_status = "ok"
            mem_detail = f"Connected to {host}:3111"
            break

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
            "enabled": is_enabled,
            "signed_in": signed_in,
            "available": usable,
        }

    overall = "ok"
    if db_status == "error":
        overall = "error"
    elif mem_status == "unavailable" or not any(
        a["available"] for a in agents.values()
    ):
        overall = "degraded"

    return {
        "status": overall,
        "uptime_seconds": uptime,
        "components": {
            "database": {
                "status": db_status,
                "entity_count": entity_count,
                "error": db_error,
            },
            "memory_service": {"status": mem_status, "detail": mem_detail},
            "agents": agents,
        },
        "quotas": {k: v.model_dump() for k, v in quota_manager.state.items()},
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
            {"t": task_type, "a": agent},
        )
        score = affinity_rows[0][0] if affinity_rows else 0.5

        # 3. Check for failures
        failure_rows = db.query_all(
            "MATCH (a:AgentNode {name: $a})-[r:FAILED_BECAUSE]->(e:ErrorType) RETURN e.name, r.count",
            {"a": agent},
        )
        failures = {r[0]: r[1] for r in failure_rows}

        narrative.append(
            {
                "prompt_preview": prompt[:100] + "...",
                "agent": agent,
                "rationale": {
                    "task_type": task_type,
                    "complexity": complexity,
                    "historical_affinity": score,
                    "known_failure_risks": failures,
                },
            }
        )

    return narrative


@app.get("/v1/roi")
async def get_roi_metrics():
    """Aggregate success rates vs cost per agent."""
    agents = ["claude", "gemini", "codex", "grok"]
    results = {}

    for agent in agents:
        rows = db.query_all(
            "MATCH (a:AgentNode {name: $a})<-[r:AFFINITY]-(t:TaskType) RETURN sum(r.score), sum(r.count)",
            {"a": agent},
        )
        score_sum = rows[0][0] if rows and rows[0][0] is not None else 0
        total_uses = rows[0][1] if rows and rows[0][1] is not None else 0

        fail_rows = db.query_all(
            "MATCH (a:AgentNode {name: $a})-[r:FAILED_BECAUSE]->(e:ErrorType) RETURN sum(r.count)",
            {"a": agent},
        )
        total_fails = (
            fail_rows[0][0] if fail_rows and fail_rows[0][0] is not None else 0
        )

        results[agent] = {
            "success_rate": float(total_uses - total_fails) / float(total_uses)
            if total_uses > 0
            else 1.0,
            "avg_affinity": float(score_sum) / float(total_uses) if total_uses > 0 else 0.5,
            "total_calls": int(total_uses),
            "failure_count": int(total_fails),
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

    add(
        "memory_service",
        _port_open("localhost", 3111),
        "port 3111 " + ("open" if _port_open("localhost", 3111) else "closed"),
    )

    # Per-agent install + auth
    for key in AGENT_ORDER:
        spec = AGENTS[key]
        installed = is_installed_anywhere(key)
        add(
            f"agent:{key}:installed",
            installed,
            spec.display
            if installed
            else f"missing — install: {spec.npm_pkg or 'pending CLI'}",
        )
        if installed and spec.auth_check:
            authed = is_authenticated(key)
            add(
                f"agent:{key}:auth",
                authed is True,
                "signed in" if authed is True else "not signed in or unknown",
            )

    failed = [c for c in checks if not c["ok"]]
    return {
        "status": "ok" if not failed else "issues",
        "summary": f"{len(checks) - len(failed)}/{len(checks)} checks passed",
        "checks": checks,
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/v1/roi")
async def get_roi():
    """Calculate Return on Investment (success rates) for each agent."""
    try:
        # Match all affinity relationships and average the scores per agent
        rows = db.query_all(
            "MATCH (t:TaskType)-[r:AFFINITY]->(a:AgentNode) "
            "RETURN a.name, avg(r.score), sum(r.count)"
        )
        # Normalize score (0-1) assuming base affinity is around 0.5
        # This is a heuristic for visualization
        roi = {}
        for name, avg_score, total_count in rows:
            success_rate = max(0.1, min(1.0, (float(avg_score) + 1.0) / 2.0))
            roi[name] = {
                "success_rate": success_rate,
                "total_tasks": int(total_count)
            }
        return roi
    except Exception:
        return {}


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
    """Retrieve recent activity from KuzuDB Question nodes."""
    try:
        from backend.router import AgentRouter
        rows = db.query_all(
            "MATCH (q:Question) RETURN q.id, q.prompt, q.answer, q.agent, q.timestamp "
            "ORDER BY q.timestamp DESC LIMIT $limit",
            {"limit": limit},
        )
        return [
            {
                "content": f"User: {AgentRouter._extract_user_prompt(r[1])}",
                "metadata": {
                    "agent": r[3] or "unknown",
                    "session_id": session_id,
                    "answer_preview": (r[2] or "")[:120],
                    "timestamp": r[4],
                },
            }
            for r in rows
        ]
    except Exception:
        return []


@app.get("/v1/snapshot/export")
async def export_snapshot(project_id: str = "default"):
    """Export a portable context snapshot for a project (Consensus Round 4)."""
    entities = [
        {"name": row[0], "type": row[1], "description": row[2], "confidence": row[3]}
        for row in db.query_all(
            "MATCH (e:Entity) WHERE e.project_id = $pid RETURN e.name, e.type, e.description, e.confidence",
            {"pid": project_id},
        )
    ]
    relationships = [
        {"source": row[0], "target": row[1], "type": row[2], "confidence": row[3]}
        for row in db.query_all(
            "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) WHERE a.project_id = $pid RETURN a.name, b.name, r.type, r.confidence",
            {"pid": project_id},
        )
    ]
    return {
        "project_id": project_id,
        "timestamp": _time.time(),
        "entities": entities,
        "relationships": relationships,
    }


class _PermissionRequestBody(BaseModel):
    session_id: str
    agent: str
    tool_name: str
    input: dict = {}


class _PermissionDecision(BaseModel):
    behavior: str  # allow | deny | stop
    scope: str = "once"  # once | session
    message: Optional[str] = None
    updated_input: Optional[dict] = None


@app.post("/permission/_request")
async def permission_request(body: _PermissionRequestBody):
    if broker.consume_interrupt(body.session_id):
        pr = broker.create(body.session_id, body.agent, body.tool_name, body.input)
        return {"id": pr.id}
    if broker.is_allowed(body.session_id, body.agent, body.tool_name, body.input):
        return {"id": None, "allowed": True}
    pr = broker.create(body.session_id, body.agent, body.tool_name, body.input)
    return {"id": pr.id}


@app.post("/permission/interrupt/{session_id}")
async def permission_interrupt(session_id: str):
    broker.set_interrupt(session_id)
    broker.set_interrupt(f"{session_id}:subagent")
    return {"status": "ok"}


@app.get("/permission/{request_id}/wait")
async def permission_wait(request_id: str):
    decision = await asyncio.to_thread(broker.wait, request_id, 600.0)
    if decision is None:
        raise HTTPException(status_code=404, detail="Unknown permission request")
    return decision


@app.post("/permission/{request_id}/decide")
async def permission_decide(request_id: str, body: _PermissionDecision):
    ok = broker.decide(
        request_id, body.behavior, body.scope, body.message, body.updated_input
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail="Unknown or already decided request"
        )
    return {"status": "ok"}


def _stream_agent(
    agent_name: str,
    full_prompt: str,
    request: "ChatRequest",
    perm_q,
    tag: str = "primary",
    mode: str = "plan",
):
    """
    Stream one agent's response. Yields text chunks + handles quota and logic errors.
    Returns (full_response, t_first, t3, t4) via a closure — caller collects via list.
    """

    agent = agent_factory.get_agent(agent_name)
    t3 = _time.perf_counter()
    t_first_box = [None]
    response_chunks = []

    agent_done = False
    agent_success = True
    chunk_q: _queue.Queue = _queue.Queue()

    def _run_agent() -> None:
        try:
            for chunk in agent.execute_stream(
                full_prompt, request.cwd, session_id=request.session_id, mode=mode
            ):
                chunk_q.put(("chunk", chunk))
        except Exception as exc:
            chunk_q.put(("error", exc))
        finally:
            chunk_q.put(("done", None))

    threading.Thread(target=_run_agent, daemon=True).start()

    agent_done = False
    last_heartbeat = _time.perf_counter()
    heartbeat_interval = 8   # seconds between status pings when agent is silent
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
            now = _time.perf_counter()
            elapsed = int(now - t3)
            if now - last_heartbeat >= heartbeat_interval:
                last_heartbeat = now
                if elapsed < 15:
                    msg = f"{agent_name} thinking... ({elapsed}s)"
                elif elapsed < 30:
                    msg = f"{agent_name} still working... ({elapsed}s)"
                elif elapsed < 60:
                    msg = f"{agent_name} taking longer than usual... ({elapsed}s)"
                else:
                    msg = f"{agent_name} very slow response — may be rate-limited ({elapsed}s)"
                yield f"__STATUS__:{msg}\n"
            continue

        if kind == "done":
            agent_done = True

        elif kind == "error":
            agent_success = False
            e = val
            error_msg = str(e)

            # Categorize error (Causal Taxonomy)
            etype = ErrorType.UNKNOWN
            if "AGENT_TRANSIENT_ERROR" in error_msg:
                etype = ErrorType.TRANSIENT_CAPACITY
            elif (
                "context length" in error_msg.lower()
                or "too many tokens" in error_msg.lower()
            ):
                etype = ErrorType.CONTEXT_OVERFLOW
            elif "syntax" in error_msg.lower() or "lint" in error_msg.lower():
                etype = ErrorType.LINTER_ERROR

            db.log_agent_failure(agent_name, etype.value)

            context_cache.prune_by_tag(request.session_id, tag)
            fallback = agent_router.get_fallback(agent_name, etype)

            if etype == ErrorType.TRANSIENT_CAPACITY:
                if fallback:
                    yield f"__STATUS__:{agent_name} quota exhausted — switching to {fallback}...\n"
                    yield from _stream_agent(
                        fallback, full_prompt, request, None, tag="fallback", mode=mode
                    )
                else:
                    yield f"\n⚠️  {agent_name} quota exhausted and no fallback available. Try again later.\n"
                    agent_router.update_outcome(request.task_type, agent_name, success=False)
            else:
                yield f"\n❌ {agent_name} failed ({etype.value}): {e}\n"

                if fallback:
                    yield f"🔄 Rollback & Fallback: Switching to {fallback}...\n"
                    yield from _stream_agent(
                        fallback, full_prompt, request, None, tag="fallback", mode=mode
                    )
                else:
                    agent_router.update_outcome(
                        request.task_type, agent_name, success=False
                    )

        else:  # "chunk"
            chunk = val
            if t_first_box[0] is None:
                t_first_box[0] = _time.perf_counter()

            # Status markers (tool activity, fallback notices) are UI-only —
            # keep them out of the stored answer and memory.
            if not chunk.startswith("__STATUS__:"):
                response_chunks.append(chunk)
            yield chunk

    t4 = _time.perf_counter()
    full_response = "".join(response_chunks).strip()

    if agent_success and full_response:  # Success
        agent_router.update_outcome(request.task_type, agent_name, success=True)

    if full_response:
        memory_client.store(
            content=f"User: {request.prompt}\nAssistant: {full_response[:500]}",
            metadata={
                "session_id": request.session_id,
                "agent": agent_name,
                "cwd": request.cwd,
            },
            tier="episodic",
        )
        threading.Thread(
            target=agent_router.learn_from_prompt,
            args=(request.prompt, full_response, agent_name),
            daemon=True,
        ).start()

    yield f"\n__TIMING__:{json.dumps({'agent': agent_name, 'agent_start': _fmt_ms((t_first_box[0] - t3) * 1000) if t_first_box[0] else 'n/a', 'agent_total': _fmt_ms((t4 - t3) * 1000)})}"


@app.post("/chat")
async def chat(request: ChatRequest):

    def _sep(name: str) -> str:
        s = "━" * 60
        return f"\n{s}\n◆  {name.upper()}\n{s}\n"

    def _status(msg: str) -> str:
        return f"__STATUS__:{msg}\n"

    def cli_generator():
        # ── Step 1: memory lookup ──────────────────────────────────────────
        yield _status("Checking context memory...")
        t0 = _time.perf_counter()
        memory_hit = agent_router.check_memory(request.prompt, request.session_id)
        t1 = _time.perf_counter()
        injected_context = ""
        if memory_hit:
            injected_context = (
                "=== BACKGROUND CONTEXT (for reference only — do NOT act on this unless the user's task below explicitly requires it) ===\n"
                f"{memory_hit}\n"
                "=== END BACKGROUND CONTEXT ===\n\n"
            )

        # ── Step 2: routing ───────────────────────────────────────────────
        yield _status("Routing to best agent...")
        agent_names, metadata = agent_router.route_multi(
            request.task_type, request.prompt, request.preferred_agent
        )
        t2 = _time.perf_counter()

        if agent_names == ["none"]:
            yield "❌ No agents available (not installed or authenticated).\n"
            return

        mode = metadata.get("mode", "plan")
        reasoning = metadata.get("internal_reasoning")

        full_prompt = injected_context + f"=== YOUR TASK ===\n{request.prompt}\n=== END TASK ==="
        if reasoning:
            full_prompt = f"=== INTERNAL REASONING (DO NOT SHOW TO USER) ===\n{reasoning}\n=== END REASONING ===\n\n" + full_prompt

        is_fanout = len(agent_names) > 1
        run_parallel = request.parallel or (
            is_fanout and agent_router.detect_parallel(request.prompt)
        )

        if (
            request.preferred_agent
            and agent_names[0] != request.preferred_agent
            and not is_fanout
        ):
            yield f"⚠️  {request.preferred_agent} unavailable. Routing to {agent_names[0]}.\n"

        for agent_name in agent_names:
            yield f"↳ {agent_router.get_explanation(agent_name, request.prompt)}\n"

        perm_q = broker.session_queue(request.session_id)
        result_buf: dict[str, str] = {}
        timing_buf: dict[str, str] = {}

        if is_fanout and run_parallel:
            # ── Parallel fan-out: run all agents simultaneously, stream in completion order
            done_q: _queue.Queue = _queue.Queue()

            def _buffer_one(ag: str) -> None:
                chunks: list[str] = []
                t_json = ""
                # For parallel, we tag each branch uniquely
                for item in _stream_agent(
                    ag, full_prompt, request, None, tag=f"branch_{ag}", mode=mode
                ):
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

        elif is_fanout:
            # ── Sequential fan-out
            for i, agent_name in enumerate(agent_names):
                tag = "primary" if i == 0 else "critic"
                yield _sep(agent_name)
                chunks = []
                for item in _stream_agent(
                    agent_name, full_prompt, request, perm_q, tag=tag, mode=mode
                ):
                    if not item.startswith("\n__TIMING__:"):
                        yield item
                        chunks.append(item)
                    else:
                        timing_buf[agent_name] = item
                result_buf[agent_name] = "".join(chunks)
                if i < len(agent_names) - 1:
                    yield "\n"
        else:
            # ── Single agent
            yield from _stream_agent(
                agent_names[0], full_prompt, request, perm_q, tag="primary", mode=mode
            )

        # ── Phase 1: Umpire Synthesis ──
        if is_fanout and len(result_buf) > 1:
            yield f"\n{'━' * 60}\n⚖️  UMPIRE SYNTHESIS\n{'━' * 60}\n"
            synthesis_prompt = (
                "You are the LeadAgent Umpire. Read the following different agent responses to the user prompt "
                "and provide a single, unified, and cohesive final answer. Resolve any contradictions and "
                "remove redundant information.\n\n"
                f"Original Prompt: {request.prompt}\n\n"
            )
            for ag, res in result_buf.items():
                synthesis_prompt += f"--- Result from {ag.upper()} ---\n{res}\n\n"

            # Use a high-reasoning agent for synthesis
            synth_agent = "claude" if "claude" in agent_names else agent_names[0]
            yield from _stream_agent(
                synth_agent, synthesis_prompt, request, None, tag="synthesis", mode=mode
            )

        run_mode = "parallel" if (is_fanout and run_parallel) else "sequential"
        yield f"\n__TIMING__:{json.dumps({'agents': agent_names, 'mode': run_mode, 'memory': _fmt_ms((t1 - t0) * 1000), 'routing': _fmt_ms((t2 - t1) * 1000), 'total': _fmt_ms((_time.perf_counter() - t0) * 1000)})}"

    return StreamingResponse(cli_generator(), media_type="text/plain")


@app.get("/v1/audit/{session_id}")
async def get_audit(session_id: str):
    """Retrieve routing rationale for a given session."""
    try:
        # Search the knowledge graph for questions in this session
        # This is a bit complex without session_id in the Question node (added in earlier steps)
        rows = db.query_all(
            "MATCH (q:Question) WHERE q.project_id CONTAINS $sid RETURN q.prompt, q.agent, q.id",
            {"sid": session_id}
        )
        # For simplicity, returning mock rationale if logic is missing
        # In a real implementation, we'd log routing decisions to a separate table
        return [{"rationale": {"task_type": "auto", "complexity": "medium", "historical_affinity": 0.8, "known_failure_risks": {}}}]
    except Exception:
        return []


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
        for row in db.query_all(
            "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) RETURN a.name, b.name, r.type"
        )
    ]
    return {"entities": entities, "relationships": relationships}


@app.get("/memory/graph/d3")
async def get_graph_d3():
    """Format graph for D3/vis.js (Phase 2)."""
    entities = db.query_all("MATCH (e:Entity) RETURN e.name, e.type")
    files = db.query_all("MATCH (f:File) RETURN f.path, f.name")

    nodes = []
    # Add Entity nodes
    for name, etype in entities:
        nodes.append({"id": name, "label": name, "group": "entity", "type": etype})

    # Add File nodes
    for path, name in files:
        nodes.append({"id": path, "label": name, "group": "file", "title": path})

    edges = []
    # RELATED_TO edges
    rel_rows = db.query_all("MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) RETURN a.name, b.name, r.type")
    for src, tgt, rtype in rel_rows:
        edges.append({"from": src, "to": tgt, "label": rtype})

    # FS edges
    fs_rows = db.query_all("MATCH (a)-[r:CONTAINS]->(b) RETURN a.path, b.path")
    for src, tgt in fs_rows:
        if src and tgt:
            edges.append({"from": src, "to": tgt, "dashes": True, "color": "gray"})

    return {"nodes": nodes, "edges": edges}


@app.post("/memory/query")
async def query_memory(query: dict):
    from backend.security import assert_read_only_cypher, UnsafeCypherError

    cypher = query.get("cypher")
    if not cypher:
        raise HTTPException(status_code=400, detail="Missing 'cypher' field")
    try:
        assert_read_only_cypher(cypher)
    except UnsafeCypherError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
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


# ── MCP Rules layer ───────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    tool_pattern: str
    action: str  # "allow" | "deny" | "ask"
    scope: str = "global"
    reason: str = ""
    input_match: str = ""  # JSON string of required key:value pairs
    priority: int = 0


class RuleEvaluateRequest(BaseModel):
    tool_name: str
    input: dict = {}
    agent: str = "claude"
    session_id: str = "default"


@app.get("/rules")
async def list_rules():
    rows = db.list_rules()
    return [
        {
            "id": r[0], "tool_pattern": r[1], "action": r[2], "scope": r[3],
            "reason": r[4], "input_match": r[5], "priority": r[6], "created_at": r[7],
        }
        for r in rows
    ]


@app.post("/rules")
async def create_rule(body: RuleCreate):
    if body.action not in ("allow", "deny", "ask"):
        raise HTTPException(status_code=400, detail="action must be allow, deny, or ask")
    rule_id = db.add_rule(
        tool_pattern=body.tool_pattern,
        action=body.action,
        scope=body.scope,
        reason=body.reason,
        input_match=body.input_match,
        priority=body.priority,
    )
    return {"id": rule_id, "status": "created"}


@app.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    ok = db.delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"id": rule_id, "status": "deleted"}


@app.post("/rules/evaluate")
async def evaluate_rule(body: RuleEvaluateRequest):
    from backend.rules import evaluate
    action, reason = evaluate(body.tool_name, body.input, body.agent, body.session_id)
    return {"action": action, "reason": reason}


if __name__ == "__main__":
    # Default to loopback so the daemon is not reachable from the LAN.
    # In Docker mode the backend must listen on all interfaces so sibling
    # containers can reach it; the published port should still be bound to
    # 127.0.0.1 on the host (see docker-compose.yml).
    default_host = "0.0.0.0" if os.environ.get("LEADAGENT_DOCKER_MODE") else "127.0.0.1"
    bind_host = os.environ.get("LEADAGENT_BIND_HOST", default_host)
    uvicorn.run(app, host=bind_host, port=8000)
