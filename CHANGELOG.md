# Changelog

All notable changes to LeadAgent will be documented in this file.

## [0.7.0] - 2026-06-23

### Added
- **Interactive Knowledge Graph**: Node click panel shows source, type, connections, and a forget button. Double-click a node to remove it from memory. Toolbar with type-filter dropdown, text search, physics-freeze toggle, and refresh.
- **AgentMemory nodes in graph**: Episodic and semantic memories from `agentmemory` are now rendered alongside KuzuDB entity/concept/file nodes, auto-linked where entity names appear in memory content. Color-coded legend: indigo = entity, purple = concept, slate diamond = file, amber square = episodic, green square = semantic.
- **Dual-tier semantic memory writes**: Completed Q&A pairs are stored as `episodic`; debate consensus and distilled answers go to `semantic`. Both are retrieved on future prompts and visible in the graph.
- **Secure dashboard login**: Dashboard now requires an httpOnly session cookie (`la_session`, 8 h). First visit redirects to `/dashboard/login`; submitting the API key via form sets the cookie — the key never appears in a URL or JS-accessible storage.
- **Dashboard logout**: `POST /dashboard/logout` clears the cookie and redirects to the login page.
- **Force-global debate memory**: `leadagent debate --no-context` now queries cross-project semantic memory (global scope) instead of skipping memory entirely — gives broader context without local-project tunnel-vision.
- **`session_id` filtering on `/v1/history`**: History endpoint now filters by `session_id` when a non-default value is provided.
- **MCP `project_id` propagation**: `main_mcp_server.py` now includes `project_id` and `cwd` in every agent call payload.
- **Slack bot optional startup**: Slack container exits cleanly (exit 0, INFO log) when `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN` are unset. No more ERROR/WARNING spam on every restart.
- **`python-multipart`** added to `backend/requirements.txt` (required for FastAPI form parsing).

### Changed
- **GuardMiddleware rewritten** as a pure ASGI class, replacing `BaseHTTPMiddleware`. Fixes `KeyError: 'background'` crash on redirect responses in Starlette 1.3.1 / FastAPI 0.138.
- **`@app.on_event` replaced** with `asynccontextmanager` lifespan — eliminates FastAPI deprecation warnings.
- **Dashboard ROI card removed** — no real math was backing it; Agent Routing card now spans full width.
- **`router.py` print → structured logger**: `learn_from_prompt` errors now go through `logging.getLogger(__name__)` instead of bare `print()`.
- **Debate umpire ollama fallback**: Final umpire selection now skips Ollama when a non-Ollama debater is available, preventing a local model from synthesising a high-stakes consensus.
- **Slack bot `docker-compose.yml`**: `restart: "no"` — container will not loop-restart when tokens are absent.

### Fixed
- **`KeyError: 'background'`** raised by `BaseHTTPMiddleware` on redirect responses — fixed by pure ASGI rewrite.
- **CSS brace escaping** in `_LOGIN_HTML`: `.format()` call now uses doubled braces (`{{...}}`) for CSS rules.
- **Circular import** between `main.py` and `security.py`: session store moved entirely to `security.py`.
- **`_classify_task` AttributeError**: function now imported directly from `backend.router` and called at module level.
- **Ollama 1000% CPU during tests**: `conftest.py` autouse fixture mocks `OllamaAgent` globally — test suite time dropped from ~50 s to 0.2 s.
- **`audit_session` shape inconsistency**: `/v1/audit/session` now normalises both `observation.narrative` and `observation.title` fallback fields into a consistent shape.
- **`apiFetch is not defined`** (dashboard): Removed `apiFetch` wrapper; all dashboard fetches now use plain `fetch()` with the session cookie sent automatically.

## [0.6.0] - 2026-06-18

### Breaking Changes
- **Gemini CLI replaced by Antigravity (`agy`):** Google deprecated the `@google/gemini-cli` npm package and OAuth sign-in for individuals. LeadAgent now uses the `agy` CLI from [antigravity.google](https://antigravity.google). Run `curl -fsSL https://antigravity.google/cli/install.sh | bash` to install, then re-run onboarding.

### Changed
- **Docker:** `leadagent-gemini` container rebuilt with `agy` instead of `gemini`. Re-run `docker compose build gemini-agent` after upgrading.
- **Model ladder:** Updated fallback models to Gemini 3.5 Flash → Gemini 3.1 Pro (matching `agy`'s available models).
- **Auth check:** Gemini availability and auth detection now probe `agy --version` instead of `gemini --version`.

## [0.5.1] - 2026-06-14

### Added
- **Ollama Intelligence:** Automatic check for `llama3.2:3b` in onboarding.
- **Docker Parity:** Expanded container mapping for Codex, Grok, and Ollama in the setup wizard.
- **Graceful Fallbacks:** Debate engine now uses "Umpire Fallbacks" when an agent fails (auth/quota/model) to ensure debates always complete.

### Fixed
- **Codex Visibility:** Improved error reporting for Codex authentication. No more silent failures when logged out.
- **Grok Hardening:** Resolved git-related spawn errors in Docker by providing a proper workspace context.
- **CLI Versioning:** Correctly reporting v0.5.1 in `leadagent --version`.

## [0.5.0] - 2026-06-14

### Added
- **Multi-Agent Debates:** Run asynchronous debates between agents using `/debate`.
- **Grok Support:** Full integration for xAI's Grok CLI.
- **Codex Integration:** Support for OpenAI's Codex CLI with autonomous execution modes.
- **Observability Dashboard:** Real-time monitoring at `http://localhost:8000/dashboard`.

### Changed
- **Unified Installer:** `install.sh` now handles the entire stack setup.
- **Enhanced Routing:** Improved model affinity scoring for code vs. research tasks.

## [0.4.0] - 2026-06-10

### Added
- Initial support for Claude and Gemini CLI routing.
- Context-aware project indexing.
- Persistent memory via `agentmemory`.
