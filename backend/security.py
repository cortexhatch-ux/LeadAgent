"""Local-host hardening for the LeadAgent backend.

LeadAgent runs on a single user's desktop, but the FastAPI app still faces two
realistic attackers even with no internet exposure:

1. **Other machines on the same LAN** — if the daemon binds a routable
   interface, a coworker / café network peer can reach the API.
2. **The user's own browser** — any website the user visits can issue
   ``fetch("http://localhost:8000/...")`` (CSRF) or use DNS rebinding to point
   its own hostname at 127.0.0.1 and drive the agents.

``GuardMiddleware`` closes both without breaking the local CLI / MCP callers:

* **Host allow-list** — the request's ``Host`` header must resolve to a
  loopback name. A LAN peer connects via the machine's IP/hostname (rejected);
  a DNS-rebinding page carries the attacker's hostname in ``Host`` (rejected).
* **Origin check** — browsers attach an ``Origin`` header to cross-site
  requests. Anything not loopback is rejected. The native CLI and the MCP
  servers send no ``Origin``, so they are unaffected.

Allowed hosts can be extended with ``LEADAGENT_ALLOWED_HOSTS`` (comma list) —
used in Docker mode where containers reach the backend as
``leadagent-backend``.
"""

import os
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


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


class GuardMiddleware(BaseHTTPMiddleware):
    """Reject non-loopback Host headers and cross-origin browser requests."""

    async def dispatch(self, request: Request, call_next):
        allowed = allowed_hosts()

        host_header = request.headers.get("host", "")
        if host_header and _hostname(host_header) not in allowed:
            return JSONResponse(
                {"detail": "Host not allowed"}, status_code=421
            )

        origin = request.headers.get("origin")
        if origin and _hostname(origin) not in allowed:
            return JSONResponse(
                {"detail": "Cross-origin request rejected"}, status_code=403
            )

        return await call_next(request)
