from backend.db import db
from backend.security import assert_read_only_cypher, is_blocked_entity, scrub_secrets

_MCP_DEFAULT_BLOCKED = (
    "MCP tools cannot write directly to project_id='default'. "
    "Use a project-scoped write and promote via the API with X-LeadAgent-Promote header."
)


def add_memory_entity(name: str, type: str, description: str = "", project_id: str = "default") -> str:
    if project_id == "default":
        return _MCP_DEFAULT_BLOCKED
    if is_blocked_entity(name):
        return f"Entity '{name}' rejected: name matches security blocklist."
    db.add_entity(name, type, description, source_project_id=project_id, source_agent="mcp")
    return f"Entity '{name}' added/updated."


def add_memory_relationship(source: str, target: str, rel_type: str, project_id: str = "default") -> str:
    if project_id == "default":
        return _MCP_DEFAULT_BLOCKED
    if is_blocked_entity(source) or is_blocked_entity(target):
        return f"Relationship rejected: source or target matches security blocklist."
    db.add_relationship(source, target, rel_type, project_id=project_id)
    return f"Relationship from '{source}' to '{target}' added."


def query_memory_graph(cypher: str) -> list:
    assert_read_only_cypher(cypher)
    return db.query_all(cypher)
