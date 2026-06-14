"""Tests for backend/agents_catalog.py — AGENTS, AGENT_ORDER, enabled_agents, is_installed, is_authenticated."""

import json
import os
from unittest.mock import patch, MagicMock

import pytest

from backend import agents_catalog
from backend.agents_catalog import (
    AGENTS,
    AGENT_ORDER,
    AgentSpec,
    enabled_agents,
    is_installed,
    is_authenticated,
    _which_extended,
    _container_running,
)


# ── Catalog constants ────────────────────────────────────────────────────────

class TestCatalogConstants:
    def test_agent_order_includes_all_agents(self):
        assert set(AGENT_ORDER) >= {"claude", "gemini", "codex", "grok", "ollama"}

    def test_all_specs_in_AGENTS(self):
        for key in ("claude", "gemini", "codex", "grok", "ollama"):
            assert key in AGENTS
            assert isinstance(AGENTS[key], AgentSpec)

    def test_claude_spec_has_npm_package(self):
        assert AGENTS["claude"].npm_pkg == "@anthropic-ai/claude-code"

    def test_grok_has_npm_package(self):
        assert AGENTS["grok"].npm_pkg == "@xai-official/grok"


# ── _which_extended ──────────────────────────────────────────────────────────

class TestWhichExtended:
    def test_returns_shutil_which_when_found(self):
        with patch("backend.agents_catalog.shutil.which", return_value="/usr/local/bin/foo"):
            assert _which_extended("foo") == "/usr/local/bin/foo"

    def test_falls_back_to_extra_dirs(self, tmp_path):
        fake_bin = tmp_path / "mybin"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        with (
            patch("backend.agents_catalog.shutil.which", return_value=None),
            patch("backend.agents_catalog._EXTRA_BIN_DIRS", [str(tmp_path)]),
        ):
            assert _which_extended("mybin") == str(fake_bin)

    def test_returns_none_when_not_found(self):
        with (
            patch("backend.agents_catalog.shutil.which", return_value=None),
            patch("backend.agents_catalog._EXTRA_BIN_DIRS", []),
        ):
            assert _which_extended("nonesuch") is None


# ── _container_running ───────────────────────────────────────────────────────

class TestContainerRunning:
    def test_returns_false_when_no_docker(self):
        with patch("backend.agents_catalog.shutil.which", return_value=None):
            assert _container_running("claude") is False

    def test_returns_false_for_unknown_key(self):
        # unknown key → no container mapped → False
        assert _container_running("nosuchagent") is False

    def test_true_when_inspect_says_running(self):
        proc = MagicMock()
        proc.stdout = "true\n"
        with (
            patch("backend.agents_catalog.shutil.which", return_value="/usr/bin/docker"),
            patch("backend.agents_catalog.subprocess.run", return_value=proc),
        ):
            assert _container_running("claude") is True

    def test_false_when_inspect_errors(self):
        with (
            patch("backend.agents_catalog.shutil.which", return_value="/usr/bin/docker"),
            patch("backend.agents_catalog.subprocess.run", side_effect=Exception("boom")),
        ):
            assert _container_running("claude") is False


# ── enabled_agents ───────────────────────────────────────────────────────────

class TestEnabledAgents:
    def test_reads_from_config_file(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"agents": ["claude", "gemini"]}))
        with patch("backend.agents_catalog._CONFIG_FILE", str(cfg)):
            result = enabled_agents()
        assert result == {"claude", "gemini"}

    def test_ignores_unknown_agents_in_config(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"agents": ["claude", "skynet"]}))
        with patch("backend.agents_catalog._CONFIG_FILE", str(cfg)):
            result = enabled_agents()
        assert result == {"claude"}

    def test_fallback_to_installed_when_no_config(self, tmp_path):
        # Point to a nonexistent file
        bad = str(tmp_path / "nope.json")
        with (
            patch("backend.agents_catalog._CONFIG_FILE", bad),
            patch("backend.agents_catalog.is_installed", side_effect=lambda k: k == "claude"),
        ):
            result = enabled_agents()
        assert "claude" in result
        assert "gemini" not in result

    def test_fallback_when_config_has_empty_agents_list(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"agents": []}))
        with (
            patch("backend.agents_catalog._CONFIG_FILE", str(cfg)),
            patch("backend.agents_catalog.is_installed", side_effect=lambda k: k == "gemini"),
        ):
            result = enabled_agents()
        assert result == {"gemini"}


# ── is_installed ─────────────────────────────────────────────────────────────

class TestIsInstalled:
    def test_returns_true_when_binary_found(self):
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("backend.agents_catalog._which_extended", return_value="/usr/bin/claude"),
        ):
            os.environ.pop("LEADAGENT_DOCKER_MODE", None)
            assert is_installed("claude") is True

    def test_returns_false_when_not_found(self):
        with (
            patch("backend.agents_catalog._which_extended", return_value=None),
        ):
            os.environ.pop("LEADAGENT_DOCKER_MODE", None)
            assert is_installed("claude") is False

    def test_docker_mode_uses_container_check(self):
        with (
            patch.dict(os.environ, {"LEADAGENT_DOCKER_MODE": "1"}),
            patch("backend.agents_catalog._container_running", return_value=True),
            patch("backend.agents_catalog._which_extended", return_value=None),
        ):
            assert is_installed("claude") is True


# ── is_authenticated ─────────────────────────────────────────────────────────

class TestIsAuthenticated:
    def test_unknown_agent_returns_none(self):
        assert is_authenticated("skynet") is None

    def test_false_when_not_installed(self):
        with patch("backend.agents_catalog.is_installed", return_value=False):
            assert is_authenticated("claude") is False

    def test_true_when_probe_contains_auth_token(self):
        proc = MagicMock()
        proc.stdout = "Logged in as foo"
        proc.stderr = ""
        with (
            patch("backend.agents_catalog.is_installed", return_value=True),
            patch("backend.agents_catalog._which_extended", return_value="/usr/bin/claude"),
            patch("backend.agents_catalog.subprocess.run", return_value=proc),
        ):
            os.environ.pop("LEADAGENT_DOCKER_MODE", None)
            assert is_authenticated("claude") is True

    def test_false_when_probe_has_no_auth_token(self):
        proc = MagicMock()
        proc.stdout = "Please log in"
        proc.stderr = ""
        with (
            patch("backend.agents_catalog.is_installed", return_value=True),
            patch("backend.agents_catalog._which_extended", return_value="/usr/bin/claude"),
            patch("backend.agents_catalog.subprocess.run", return_value=proc),
        ):
            os.environ.pop("LEADAGENT_DOCKER_MODE", None)
            assert is_authenticated("claude") is False

    def test_none_when_probe_raises(self):
        with (
            patch("backend.agents_catalog.is_installed", return_value=True),
            patch("backend.agents_catalog._which_extended", return_value="/usr/bin/claude"),
            patch("backend.agents_catalog.subprocess.run", side_effect=Exception("timeout")),
        ):
            os.environ.pop("LEADAGENT_DOCKER_MODE", None)
            assert is_authenticated("claude") is None
