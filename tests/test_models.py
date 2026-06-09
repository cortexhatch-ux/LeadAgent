"""Tests for backend/models.py — pydantic schemas + ErrorType enum."""

import pytest
from pydantic import ValidationError

from backend.models import Entity, ErrorType, MemoryState, Relationship


def test_entity_minimal():
    e = Entity(name="Foo", type="Class")
    assert e.name == "Foo"
    assert e.type == "Class"
    assert e.description is None
    assert e.properties == {}


def test_entity_full():
    e = Entity(name="Foo", type="Class", description="d", properties={"k": 1})
    assert e.description == "d"
    assert e.properties == {"k": 1}


def test_entity_requires_name_and_type():
    with pytest.raises(ValidationError):
        Entity(name="x")
    with pytest.raises(ValidationError):
        Entity(type="x")


def test_relationship_minimal():
    r = Relationship(source="A", target="B", type="USES")
    assert r.source == "A"
    assert r.target == "B"
    assert r.type == "USES"
    assert r.description is None
    assert r.properties == {}


def test_relationship_requires_fields():
    with pytest.raises(ValidationError):
        Relationship(source="A", target="B")  # missing type


def test_memory_state_composes():
    ms = MemoryState(
        entities=[Entity(name="A", type="C")],
        relationships=[Relationship(source="A", target="B", type="USES")],
    )
    assert len(ms.entities) == 1
    assert len(ms.relationships) == 1


def test_memory_state_empty_lists():
    ms = MemoryState(entities=[], relationships=[])
    assert ms.entities == []
    assert ms.relationships == []


def test_error_type_is_str_enum():
    assert ErrorType.LINTER_ERROR == "LinterError"
    assert ErrorType.QUOTA_EXHAUSTED.value == "QuotaExhausted"
    # str-enum convention
    assert isinstance(ErrorType.UNKNOWN, str)


def test_error_type_all_members_present():
    expected = {
        "LINTER_ERROR", "TEST_TIMEOUT", "CONTEXT_OVERFLOW", "LOGIC_ERROR",
        "NETWORK_FAILURE", "QUOTA_EXHAUSTED", "TRANSIENT_CAPACITY", "UNKNOWN",
    }
    assert {m.name for m in ErrorType} == expected
