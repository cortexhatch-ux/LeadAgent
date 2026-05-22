from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from enum import Enum

class Entity(BaseModel):
    name: str
    type: str
    description: Optional[str] = None
    properties: Dict[str, Any] = {}

class Relationship(BaseModel):
    source: str
    target: str
    type: str
    description: Optional[str] = None
    properties: Dict[str, Any] = {}

class MemoryState(BaseModel):
    entities: List[Entity]
    relationships: List[Relationship]

class ErrorType(str, Enum):
    LINTER_ERROR = "LinterError"
    TEST_TIMEOUT = "TestTimeout"
    CONTEXT_OVERFLOW = "ContextOverflow"
    LOGIC_ERROR = "LogicError"
    NETWORK_FAILURE = "NetworkFailure"
    QUOTA_EXHAUSTED = "QuotaExhausted"
    TRANSIENT_CAPACITY = "TransientCapacity"
    UNKNOWN = "Unknown"
