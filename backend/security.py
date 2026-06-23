"""Local-host hardening for the LeadAgent backend.

LeadAgent runs on a single user's desktop, but the FastAPI app still faces two
realistic attackers even with no internet exposure:

1. **Other machines on the same LAN** — if the daemon binds a routable
   interface, a coworker / café network peer can reach the API.
2. **The user's own browser** — any website the user visits can issue
   ``fetch("http://localhost:8000/...")`` (CSRF) or use DNS rebinding to point
   its own hostname at 127.0.0.1 and drive the agents.
3. **Rogue local processes** — any process on the same machine that can
   reach localhost can drive agents or tamper with memory without authenticating.

``GuardMiddleware`` closes all three:

* **Host allow-list** — the request's ``Host`` header must resolve to a
  loopback name. A LAN peer connects via the machine's IP/hostname (rejected);
  a DNS-rebinding page carries the attacker's hostname in ``Host`` (rejected).
* **Origin check** — browsers attach an ``Origin`` header to cross-site
  requests. Anything not loopback is rejected.
* **API key check** — ``X-LeadAgent-Key`` is required for ALL non-exempt
  requests (browser and non-browser alike). Exempt paths: ``/``, ``/health``,
  ``/v1/status``, ``/doctor``.  The key is a 64-hex-char secret stored in
  ``leadagent-data/config.json`` (mode 0600).  Internal callers (CLI, MCP
  servers, Slack bot) read the key from the same file or from the
  ``LEADAGENT_API_KEY`` env var injected at spawn time.

Allowed hosts can be extended with ``LEADAGENT_ALLOWED_HOSTS`` (comma list) —
used in Docker mode where containers reach the backend as
``leadagent-backend``.
"""
from __future__ import annotations

import collections
import json
import os
import re
import secrets
import threading
import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# ── API key management ────────────────────────────────────────────────────────

_CONFIG_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "leadagent-data", "config.json")
)


def _load_config() -> dict:
    if os.path.exists(_CONFIG_FILE):
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    return {}


def _save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
    with open(_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(_CONFIG_FILE, 0o600)


def ensure_api_key() -> str:
    """Return the API key, generating and persisting one if absent."""
    cfg = _load_config()
    key = cfg.get("api_key")
    if not key:
        key = secrets.token_hex(32)
        cfg["api_key"] = key
        _save_config(cfg)
        print(f"[security] Generated new LEADAGENT_API_KEY — stored in {_CONFIG_FILE}")
    return key


def get_api_key() -> str | None:
    """Return the stored API key, or None if not yet configured."""
    return _load_config().get("api_key")


def auth_headers() -> dict[str, str]:
    """Return the X-LeadAgent-Key header dict for internal HTTP callers."""
    key = get_api_key()
    if key:
        return {"X-LeadAgent-Key": key}
    return {}


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def allowed_hosts() -> set[str]:
    """Loopback names plus anything in LEADAGENT_ALLOWED_HOSTS / docker backend."""
    hosts = set(_LOOPBACK_HOSTS)
    extra = os.environ.get("LEADAGENT_ALLOWED_HOSTS", "")
    for h in extra.split(","):
        h = h.strip().lower()
        if h:
            hosts.add(h)
    if os.environ.get("LEADAGENT_DOCKER_MODE"):
        # Containers address the backend by its compose service name.
        hosts.add("leadagent-backend")
        hosts.add("backend")
    return hosts


def _hostname(value: str) -> str:
    """Strip the port from a Host/Origin authority and lower-case it."""
    value = value.strip().lower()
    # Drop scheme if present (Origin header form: http://host:port)
    if "://" in value:
        value = value.split("://", 1)[1]
    # IPv6 literal: [::1]:8000 -> [::1]
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.split(":", 1)[0]


# ── Read-only Cypher guard ────────────────────────────────────────────────────
#
# The /memory/query endpoint (and the memory_query MCP tool) used to run any
# Cypher string verbatim, so a single request could `MATCH (n) DETACH DELETE n`
# the whole graph or rewrite stored data. Callers only ever need reads, so we
# reject any statement that contains a write / DDL keyword.

_CYPHER_WRITE_KEYWORDS = (
    "create", "delete", "detach", "set", "merge", "remove", "drop",
    "alter", "attach", "copy", "load", "install", "export", "import",
    "call",  # procedure calls can mutate or escape the read sandbox
)

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(_CYPHER_WRITE_KEYWORDS) + r")\b", re.IGNORECASE
)


class UnsafeCypherError(ValueError):
    """Raised when a Cypher query contains a write / DDL clause."""


def assert_read_only_cypher(cypher: str) -> str:
    """Return the query unchanged if it is read-only, else raise.

    Conservative: a write keyword appearing anywhere (including inside string
    literals) is rejected. Read queries never need these words, so the false
    positive cost is negligible next to the data-loss risk.
    """
    if not isinstance(cypher, str) or not cypher.strip():
        raise UnsafeCypherError("Empty query")
    match = _KEYWORD_RE.search(cypher)
    if match:
        raise UnsafeCypherError(
            f"Write/DDL clause '{match.group(1).upper()}' is not allowed; "
            "/memory/query is read-only."
        )
    return cypher


# ── Secret / PII scrubber ─────────────────────────────────────────────────────
#
# Runs on text before it is stored in the global brain (project_id == "default").
# Redacts common machine-readable secrets so they cannot propagate across projects.
# These patterns are conservative: they match high-entropy structured secrets, not
# plain English. False positives are fine — a redacted label never leaks a real key.

_SECRET_PATTERNS = [
    # OpenAI / Anthropic / Google API keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    # AWS access key / secret
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)aws[_\-\s]?(secret|access)[_\-\s]?key[_\-\s]*[:=]\s*[A-Za-z0-9/+=]{20,}"),
    # Generic high-entropy bearer / token values in key=value context
    re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key|auth_token|bearer)\s*[:=]\s*[^\s,;\"']{8,}"),
    # SSH private key header
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    # Hex secrets only in key=value context (avoids false-positives on git SHAs, content hashes)
    re.compile(r"(?i)(key|secret|token|hash|digest|password|passwd|auth)\s*[:=]\s*[0-9a-fA-F]{32,}"),
]

# ── Entity name blocklist (shared across all write paths) ─────────────────────
#
# Moved here from router.py so debate.py, indexer.py, tools.py, and the API
# endpoint all use the same guard without duplicating the pattern.

ENTITY_BLOCKLIST = re.compile(
    r"(?i)^(password|passwd|passphrase|secret|token|apikey|api_key|authkey|auth_key"
    r"|credential|private_key|privkey|access_key|session_key|session_token"
    r"|refresh_token|bearer|jwt|oauth|client_secret|client_id"
    r"|admin|root|sudo|superuser|masterkey|master_key|sysadmin"
    r"|ssn|creditcard|credit_card|cvv|pin|bank_account|iban|routing_number"
    r"|malicious|inject|exploit|payload|backdoor|shellcode|execcode"
    r"|dropper|ransomware|keylogger|rootkit|trojan|exfil|c2|cnc"
    r"|env_file|dotenv|\.env|ssh_key|id_rsa|id_ed25519|known_hosts)\w*$"
    r"|(password|secret|token|api_key|apikey|credential|private_key"
    r"|access_key|auth_key|session_key|session_token|bearer|jwt"
    r"|client_secret|refresh_token)",
)


def is_blocked_entity(name: str) -> bool:
    """Return True if the entity name matches the blocklist."""
    return bool(ENTITY_BLOCKLIST.search(name))


def scrub_secrets(text: str) -> str:
    """Return text with machine-readable secrets replaced by [REDACTED].

    Called before writing to the global brain so secrets extracted from one
    project's context cannot leak to other projects via the shared memory pool.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# ── Simple token-bucket rate limiter ─────────────────────────────────────────
#
# Protects write/inference endpoints against runaway callers on the same
# machine. The budget is intentionally generous (60 req/min per IP) — enough
# for any interactive use while blocking tight loops from a compromised process.

_RATE_LIMIT_RPM = int(os.environ.get("LEADAGENT_RATE_LIMIT_RPM", "60"))
_RATE_WINDOW = 60  # seconds

# {(client_ip, path): deque of request timestamps}
# Keyed per-path so a runaway caller on one endpoint doesn't starve others.
# All loopback traffic shares the same IP, so per-path bucketing is essential.
_rate_buckets: dict[tuple[str, str], collections.deque] = {}
_rate_lock = threading.Lock()

# Endpoints that are exempt from rate limiting (health/status only)
_RATE_EXEMPT_PATHS = {"/", "/health", "/v1/status", "/doctor"}
# Paths that handle their own auth (session cookie login/logout)
_AUTH_SELF_PATHS = {"/dashboard", "/dashboard/login", "/dashboard/logout"}

# ── Dashboard session store (httpOnly cookie auth) ────────────────────────────
dashboard_sessions: set[str] = set()
SESSION_COOKIE = "la_session"
SESSION_MAX_AGE = 8 * 60 * 60  # 8 hours


def valid_dashboard_session(cookies: dict) -> bool:
    token = cookies.get(SESSION_COOKIE, "")
    return bool(token and token in dashboard_sessions)


def _check_rate_limit(client_ip: str, path: str = "") -> bool:
    """Return True if the request is within the rate limit, False if exceeded."""
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.setdefault((client_ip, path), collections.deque())
        # Drop timestamps outside the window
        while bucket and now - bucket[0] > _RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_RPM:
            return False
        bucket.append(now)
    return True


class GuardMiddleware:
    """Pure ASGI middleware: reject non-loopback hosts, cross-origin requests, bad keys, rate-exceeded callers."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        allowed = allowed_hosts()
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path

        host_header = request.headers.get("host", "")
        if host_header and _hostname(host_header) not in allowed:
            print(f"[guard] 421 host-not-allowed ip={client_ip} host={host_header!r} path={path}")
            resp = JSONResponse({"detail": "Host not allowed"}, status_code=421)
            await resp(scope, receive, send)
            return

        origin = request.headers.get("origin")
        if origin and _hostname(origin) not in allowed:
            print(f"[guard] 403 cross-origin ip={client_ip} origin={origin!r} path={path}")
            resp = JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
            await resp(scope, receive, send)
            return

        if path not in _RATE_EXEMPT_PATHS and path not in _AUTH_SELF_PATHS:
            stored_key = get_api_key()
            if stored_key:
                if valid_dashboard_session(request.cookies):
                    key_ok = True
                else:
                    provided = request.headers.get("x-leadagent-key", "")
                    try:
                        key_ok = secrets.compare_digest(stored_key, provided)
                    except (TypeError, ValueError):
                        key_ok = False
                if not key_ok:
                    print(f"[guard] 401 bad-api-key ip={client_ip} path={path}")
                    resp = JSONResponse({"detail": "Missing or invalid X-LeadAgent-Key"}, status_code=401)
                    await resp(scope, receive, send)
                    return

        if path not in _RATE_EXEMPT_PATHS:
            if not _check_rate_limit(client_ip, path):
                print(f"[guard] 429 rate-limited ip={client_ip} path={path} limit={_RATE_LIMIT_RPM}rpm")
                resp = JSONResponse({"detail": f"Rate limit exceeded ({_RATE_LIMIT_RPM} req/min)"}, status_code=429)
                await resp(scope, receive, send)
                return

        await self.app(scope, receive, send)
