"""Tests for backend/roles.py — system-prompt lookup table."""

import pytest

from backend import roles
from backend.roles import ROLES, ROLE_DESCRIPTIONS, get_system_prompt


def test_all_roles_have_descriptions():
    assert set(ROLES) == set(ROLE_DESCRIPTIONS)


@pytest.mark.parametrize("role", list(ROLES.keys()))
def test_get_system_prompt_known_role(role):
    p = get_system_prompt(role)
    assert isinstance(p, str)
    assert p == ROLES[role]
    assert len(p) > 20


def test_get_system_prompt_unknown_falls_back_to_general():
    assert get_system_prompt("does-not-exist") == ROLES["general"]


def test_get_system_prompt_empty_falls_back_to_general():
    assert get_system_prompt("") == ROLES["general"]


def test_general_role_present():
    assert "general" in ROLES
    assert "LeadAgent" in ROLES["general"]


def test_descriptions_are_strings():
    for k, v in ROLE_DESCRIPTIONS.items():
        assert isinstance(v, str)
        assert v
