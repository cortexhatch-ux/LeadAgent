# LeadAgent Security Review & Hardening

This document records the security review of LeadAgent, the threat model for its
intended deployment, and the concrete fixes applied. It covers every issue that
was identified, including the ones that are mitigated by configuration rather
than code.

## Threat model

LeadAgent runs **locally on an individual user's desktop**. There is no
multi-tenant server and no anonymous internet attacker in the normal case. That
removes some risk but leaves three realistic threat sources:

1. **Other machines on the same LAN** — if the daemon binds a routable
   interface, a peer on a shared/office/café network can reach the API.
2. **The user's own web browser** — any site the user visits can issue
   `fetch("http://localhost:8000/...")` (CSRF), or use DNS rebinding to point
   its own hostname at `127.0.0.1` and drive the agents. Binding to localhost
   does **not** stop this, because the browser runs locally.
3. **The agents themselves** — LeadAgent's whole purpose is to run coding
   agents that execute code. The moment an agent ingests hostile content (a
   poisoned README, a web page, a Slack `/debate` topic from a coworker), prompt
   injection can make it act. The risk is not "an agent runs code" — that is the
   product — but the **blast radius** when it runs attacker-influenced code.

The fixes below are prioritized accordingly.

---

## Findings and fixes

### 1. Arbitrary Cypher execution via `/memory/query` — **Critical → Fixed**

`POST /memory/query` ran any Cypher string verbatim (`db.query_all(cypher)`),
re-exposed through the `memory_query` MCP tool and the Go CLI. A browser page or
a prompt-injected agent could run `MATCH (n) DETACH DELETE n` to wipe the graph
or exfiltrate every stored record.

**Fix:** `backend/security.py::assert_read_only_cypher` rejects any query
containing a write/DDL keyword (`CREATE`, `DELETE`, `DETACH`, `SET`, `MERGE`,
`REMOVE`, `DROP`, `ALTER`, `ATTACH`, `COPY`, `LOAD`, `INSTALL`, `EXPORT`,
`IMPORT`, `CALL`). Enforced in `backend/main.py` (`/memory/query` now returns
`403` on a write) and in `backend/tools.py::query_memory_graph`. Reads are
unaffected.

### 2. No origin/host validation; LAN + browser reachable — **Critical → Fixed**

The API had no authentication and bound `0.0.0.0`, exposing every endpoint —
including `/chat` and `/debate`, which spawn agents — to LAN peers and to the
browser.

**Fix:** `backend/security.py::GuardMiddleware` (wired in `backend/main.py`):

- **Host allow-list** — the `Host` header must resolve to a loopback name
  (`localhost`, `127.0.0.1`, `::1`). A LAN peer connects via the machine's
  IP/hostname (rejected, `421`); a DNS-rebinding page carries the attacker's
  hostname in `Host` (rejected).
- **Origin check** — a cross-origin `Origin` header is rejected (`403`),
  defeating browser CSRF. The native CLI and MCP servers send no `Origin`, so
  they are unaffected.
- Docker mode adds the compose service names (`leadagent-backend`, `backend`)
  to the allow-list; extra hosts can be added with `LEADAGENT_ALLOWED_HOSTS`.

The default uvicorn bind was also changed to `127.0.0.1` for native runs
(`LEADAGENT_DOCKER_MODE` keeps `0.0.0.0` so sibling containers can reach it),
overridable via `LEADAGENT_BIND_HOST`.

### 3. Agents run with sandboxing/approvals disabled — **Critical → Mitigated**

Codex was always invoked with `--dangerously-bypass-approvals-and-sandbox`
(full host + network access, no approval), regardless of plan/execute mode.
Combined with prompt injection this is a host-takeover primitive.

**Fix:** `backend/agents.py` now defaults Codex to a workspace-scoped sandbox
(`--sandbox workspace-write --ask-for-approval never`) — it can edit files in
its working directory but cannot touch the wider host or network. The fully
unsandboxed mode is opt-in via `LEADAGENT_ALLOW_UNSANDBOXED=1`.

**Residual / by design:** Gemini's `execute` mode still maps to
`--approval-mode yolo`. This only triggers in explicit *execute* mode (not the
default *plan* mode used by debates), and it is the documented intent of execute
mode. Treat any untrusted input handed to an execute-mode agent as privileged.

### 4. Docker socket mounted into agent + Slack containers — **Critical → Fixed**

`/var/run/docker.sock` was mounted into the claude, gemini, and slack-bot
containers. Any agent able to run a docker command could start a privileged
container mounting the host root filesystem — a trivial escape to host root.

**Fix:** `docker-compose.yml` removes the socket mount from all agent containers
and the Slack bot. It remains only on `backend` and `watchdog`, which are the
orchestrator components that legitimately manage containers by design.

### 5. Attacker-controlled working directory — **High → Fixed**

`ChatRequest.cwd` flowed straight into `subprocess.Popen(cwd=...)`, so a caller
could point an agent at any directory on the host.

**Fix:** `backend/agents.py::_safe_cwd` resolves the requested directory and
honors it only if it sits within an allow-listed root (project root, the user's
home, the temp dir, `/app/leadagent-data`, `LEADAGENT_WORKSPACE`, or paths in
`LEADAGENT_ALLOWED_CWD`). Anything outside falls back to the daemon's own cwd.

### 6. Host credentials mounted read-write into containers — **High → Partially fixed**

`~/.claude`, `~/.gemini`, `~/.config/gcloud`, `~/.config/google-chrome` were
mounted into containers, letting a compromised agent read or overwrite host
tokens.

**Fix:** the Slack bot's credential mounts are now read-only (it never refreshes
tokens). The agent containers keep read-write mounts because the CLIs refresh
OAuth tokens in place; making them read-only breaks re-auth. **Recommendation:**
use dedicated, scoped credentials for LeadAgent rather than your primary ones,
and prefer per-agent auth dirs over sharing the host's.

### 7. Slack prompt injection / no authorization — **Medium → Documented**

Any user who can `@mention` the bot or run `/debate` drives the agent fleet with
verbatim text, and exception detail is echoed back to the channel.

**Status:** This is an authorization-scope concern, not remote RCE. If the bot
lives in a shared workspace, restrict it to a private channel or an allow-list
of users. With fixes #3 and #4 in place, the blast radius of an injected Slack
prompt is now a workspace-scoped sandbox rather than host root.

### 8. `db.query()` bypassed the connection lock — **Low → Fixed**

`GraphDB.query()` executed without `self._lock`, unlike `query_all()`, allowing
races with the janitor/learning threads.

**Fix:** `backend/db.py::query` now acquires the lock for the `execute` call.

---

### 9. Dashboard API key exposed in query params / sessionStorage — **Medium → Fixed**

The dashboard previously required the API key to be passed as a URL query parameter (`?key=...`) or stored in `sessionStorage` — both accessible from JS and visible in browser history.

**Fix:** The dashboard now uses an **httpOnly session cookie** (`la_session`).

Flow:
1. `GET /dashboard` redirects unauthenticated users to `GET /dashboard/login`.
2. `POST /dashboard/login` accepts the API key via a form field (not URL), validates it with a constant-time compare, and sets an `httpOnly; SameSite=Strict` cookie containing a 32-byte random session token.
3. `GuardMiddleware` accepts the cookie as equivalent to the `X-LeadAgent-Key` header. JS code in the dashboard never sees the key or the token.
4. `/dashboard/login` and `/dashboard/logout` are listed in `_AUTH_SELF_PATHS` so the guard does not block the login page itself.

Cookie attributes:
- `httponly=True` — JS `document.cookie` cannot read it.
- `samesite="strict"` — browser will not attach it to cross-site requests, blocking CSRF.
- `max_age=28800` (8 hours) — session expires automatically.

### 10. `BaseHTTPMiddleware` incompatibility with redirect responses — **Low → Fixed**

Starlette 1.3.1 / FastAPI 0.138 raised `KeyError: 'background'` when `BaseHTTPMiddleware` processed redirect responses — because redirect scopes lack a `background` key that the middleware assumed.

**Fix:** `GuardMiddleware` was rewritten as a **pure ASGI class** (`__call__(scope, receive, send)`) that bypasses `BaseHTTPMiddleware` entirely. It constructs `Request` from the raw ASGI scope, performs all checks (host, origin, key/cookie, rate limit) and either short-circuits with a JSON response or delegates to the inner app. This is forward-compatible with future Starlette versions.

---

## What is intentionally *not* changed

- **Routing Codex/Gemini through the MCP permission broker** (as Claude is).
  This is a larger change; the sandbox default in #3 is the pragmatic interim.

## Configuration knobs added

| Env var | Effect | Default |
| --- | --- | --- |
| `LEADAGENT_BIND_HOST` | uvicorn bind address | `127.0.0.1` (native), `0.0.0.0` (docker) |
| `LEADAGENT_ALLOWED_HOSTS` | extra allowed `Host`/`Origin` names (comma list) | empty |
| `LEADAGENT_ALLOWED_CWD` | extra allowed agent working-dir roots (`:`-separated) | empty |
| `LEADAGENT_ALLOW_UNSANDBOXED` | restore Codex full host/network access | unset (sandboxed) |
| `LEADAGENT_DOCKER_MODE` | use `host.docker.internal` for AgentMemory; expand host allow-list | unset |

## Verification

All changes are covered by `pytest`:

- `tests/test_api.py::TestGuardMiddleware` — loopback allowed; LAN host,
  rebinding hostname, and cross-origin requests rejected.
- `tests/test_api.py::TestMemoryQueryGuard` — reads allowed; write/DDL Cypher
  rejected with `403`.
- `tests/test_agents.py::TestSafeCwd` — allowed roots honored, system/unknown
  dirs rejected.

Full suite: **332 passed** (316 pre-existing + 16 new).

Tests added in v0.7.0:
- `tests/test_api.py::TestAuditSession` — 5 tests for `/v1/audit/session` shape normalisation (standard, observation narrative, title fallback, unknown shape, empty history).
- `tests/test_router.py::TestCheckMemoryContentFallback` — 5 tests for `check_memory` content-field normalisation across AgentMemory response shapes.
