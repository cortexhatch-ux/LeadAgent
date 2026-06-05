"""Single source of truth for subscription-CLI metadata.

Both the GUI setup wizard and the CLI onboarding flow read from here so we
don't drift definitions across files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

_CONFIG_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "leadagent-data", "config.json")
)


@dataclass(frozen=True)
class AgentSpec:
    key: str
    display: str
    vendor: str
    color: str
    npm_pkg: Optional[str] = None
    login_cmd: Optional[str] = None
    auth_check: Optional[list[str]] = None
    note: Optional[str] = None


_AUTH_OK_TOKENS = (
    "logged in",
    "logged-in",
    '"loggedin": true',
    "authenticated",
    "active subscription",
    "subscription: pro",
    "subscription: max",
    "claude pro",
    "sonnet",
    "opus",
    "haiku",
    "0.",
    "1.",
    "2.",  # Recognize version numbers (for non-interactive probes)
)
# "Logged in" alone isn't enough to prove a paid tier exists, but it's the
# stronger signal CLIs other than claude expose. We surface "signed in" in the
# UI; the user remains responsible for declaring which subscriptions they pay
# for via the wizard (stored in leadagent-data/config.json).


AGENTS: dict[str, AgentSpec] = {
    "claude": AgentSpec(
        key="claude",
        display="Claude Pro",
        vendor="Anthropic",
        color="#a78cf7",
        npm_pkg="@anthropic-ai/claude-code",
        login_cmd="claude auth login",
        auth_check=["claude", "auth", "status"],
    ),
    "gemini": AgentSpec(
        key="gemini",
        display="Gemini Advanced",
        vendor="Google",
        color="#5e9cf5",
        npm_pkg="@google/gemini-cli",
        login_cmd="gemini auth login",
        auth_check=["gemini", "--version"],
    ),
    "codex": AgentSpec(
        key="codex",
        display="OpenAI Codex",
        vendor="OpenAI",
        color="#5dba6e",
        npm_pkg="@openai/codex",
        login_cmd="codex login",
        auth_check=["codex", "login", "status"],
    ),
    "grok": AgentSpec(
        key="grok",
        display="Grok",
        vendor="xAI",
        color="#e8a840",
        npm_pkg=None,
        login_cmd=None,
        auth_check=None,
        note="CLI not yet released by xAI",
    ),
    "ollama": AgentSpec(
        key="ollama",
        display="Ollama (Local)",
        vendor="Ollama",
        color="#64748b",
        note="Local SLM runner. Requires Ollama to be running on port 11434.",
        auth_check=["ollama", "list"],
    ),
}

AGENT_ORDER = ("claude", "gemini", "codex", "grok", "ollama")


_EXTRA_BIN_DIRS = [
    os.path.expanduser("~/.leadagent/bin"),
    os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/.npm-global/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
]


def _which_extended(cmd: str) -> Optional[str]:
    # In Docker mode, we primarily care if the container is running.
    # We still check local paths for back-compat or when DOCKER_MODE=0.
    p = shutil.which(cmd)
    if p:
        return p
    for d in _EXTRA_BIN_DIRS:
        cand = os.path.join(d, cmd)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _container_running(agent_key: str) -> bool:
    """Check if the corresponding docker container is running."""
    container_map = {
        "claude": "leadagent-claude",
        "gemini": "leadagent-gemini",
        "codex": "leadagent-codex",
        "grok": "leadagent-grok",
    }
    container = container_map.get(agent_key)
    if not container or not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def is_installed(agent_key: str) -> bool:
    if agent_key == "ollama":
        # For Ollama, we prioritize the REST API check since it's a service.
        # Check both local and docker hosts.
        from backend.agents import OllamaAgent
        import requests
        agent = OllamaAgent()
        try:
            # Quick probe to the /api/tags or version endpoint
            resp = requests.get(f"{agent.url}/api/tags", timeout=0.5)
            if resp.status_code == 200:
                return True
        except Exception:
            pass

    if os.environ.get("LEADAGENT_DOCKER_MODE"):
        if _container_running(agent_key):
            return True
    return _which_extended(agent_key) is not None


def enabled_agents() -> set[str]:
    """Read user-declared agent list from data/config.json (set by the wizard)."""
    try:
        with open(_CONFIG_FILE) as fh:
            data = json.load(fh)
        agents = data.get("agents")
        if isinstance(agents, list) and agents:
            return {a for a in agents if a in AGENTS}
    except Exception:
        pass
    # No config yet → assume everything installed is enabled (back-compat).
    return {k for k in AGENT_ORDER if is_installed(k)}


def is_authenticated(agent_key: str) -> Optional[bool]:
    """Return True/False if we can check, None if no probe is defined."""
    spec = AGENTS.get(agent_key)
    if not spec or not spec.auth_check:
        return None
    if not is_installed(agent_key):
        return False

    # Build command: use docker exec if in Docker mode and container is up
    if os.environ.get("LEADAGENT_DOCKER_MODE") and _container_running(agent_key):
        container_map = {
            "claude": "leadagent-claude",
            "gemini": "leadagent-gemini",
            "codex": "leadagent-codex",
            "grok": "leadagent-grok",
        }
        cmd = ["docker", "exec", "-i", container_map[agent_key]] + spec.auth_check
    else:
        binary = _which_extended(agent_key) or agent_key
        cmd = [binary] + spec.auth_check[1:]

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        blob = (out.stdout + out.stderr).lower()
        return any(tok in blob for tok in _AUTH_OK_TOKENS)
    except Exception as e:
        if os.environ.get("LEADAGENT_DEBUG"):
            print(f"[is_authenticated] {agent_key} probe failed: {e}")
        return None
