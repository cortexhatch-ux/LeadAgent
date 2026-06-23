"""Tests for backend/watchdog.py — agentmemory health check and restart logic."""
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.watchdog import (
    agentmemory_http_ok,
    check_agentmemory,
    MAX_AGENTMEMORY_FAILURES,
    DOCKER_SOCK,
)


def _mock_livez(status=200, raises=None):
    if raises:
        return patch("urllib.request.urlopen", side_effect=raises)
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(status=status)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("urllib.request.urlopen", return_value=cm)


def test_agentmemory_http_ok_returns_true_on_200():
    with _mock_livez(200):
        assert agentmemory_http_ok() is True


def test_agentmemory_http_ok_returns_false_on_500():
    with _mock_livez(500):
        assert agentmemory_http_ok() is False


def test_agentmemory_http_ok_returns_false_on_exception():
    with _mock_livez(raises=OSError("refused")):
        assert agentmemory_http_ok() is False


def test_check_agentmemory_resets_failures_when_healthy():
    state = {"agentmemory_failures": 2}
    with patch("backend.watchdog.agentmemory_http_ok", return_value=True), \
         patch("os.path.exists", return_value=True):
        result = check_agentmemory(state)
    assert result["agentmemory_failures"] == 0


def test_check_agentmemory_increments_on_failure():
    with patch("backend.watchdog.agentmemory_http_ok", return_value=False), \
         patch("os.path.exists", return_value=True), \
         patch("backend.watchdog.restart_iii_engine_via_docker_socket", return_value=True):
        state = check_agentmemory({})
    assert state["agentmemory_failures"] == 1


def test_check_agentmemory_restarts_after_max_failures():
    state = {"agentmemory_failures": MAX_AGENTMEMORY_FAILURES - 1}
    with patch("backend.watchdog.agentmemory_http_ok", return_value=False), \
         patch("os.path.exists", return_value=True), \
         patch("backend.watchdog.restart_iii_engine_via_docker_socket", return_value=True) as mock_restart:
        result = check_agentmemory(state)
    mock_restart.assert_called_once()
    assert result["agentmemory_failures"] == 0


def test_check_agentmemory_skips_restart_below_threshold():
    state = {"agentmemory_failures": 0}
    with patch("backend.watchdog.agentmemory_http_ok", return_value=False), \
         patch("os.path.exists", return_value=True), \
         patch("backend.watchdog.restart_iii_engine_via_docker_socket") as mock_restart:
        check_agentmemory(state)
    mock_restart.assert_not_called()
