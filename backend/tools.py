from backend.db import db


def add_memory_entity(name: str, type: str, description: str = "") -> str:
    db.add_entity(name, type, description)
    return f"Entity '{name}' added/updated."


def add_memory_relationship(source: str, target: str, rel_type: str) -> str:
    db.add_relationship(source, target, rel_type)
    return f"Relationship from '{source}' to '{target}' added."


def query_memory_graph(cypher: str) -> list:
    return db.query_all(cypher)
