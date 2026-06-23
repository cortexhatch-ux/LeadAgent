"""Autonomous Indexer Logic.
Handles proactive code analysis using local Ollama.
"""

import os
import json
import re
import requests
from backend.db import db
from typing import List, Dict

# Extensions we care about for proactive indexing
INDEX_EXTENSIONS = {'.py', '.js', '.ts', '.go', '.rs', '.java', '.cpp', '.h', '.html', '.css', '.md', '.json', '.yaml', '.yml'}

def get_ollama_url():
    """Determine Ollama URL based on environment."""
    if os.environ.get("LEADAGENT_DOCKER_MODE"):
        return os.environ.get("OLLAMA_HOST", "http://ollama:11434")
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434")

def extract_entities_via_ollama(file_path: str, content: str) -> Dict:
    """Use Ollama to extract entities and relationships from a single file."""
    url = f"{get_ollama_url()}/api/generate"
    
    prompt = f"""
Analyze the following source code file and extract key architectural entities and their relationships.
Entities: Classes, main functions, core data structures, external dependencies, and architectural patterns.
Relationships: inheritance, usage, composition, or data flow.

Format as a JSON object with:
{{
  "entities": [ {{"name": "...", "type": "...", "description": "..."}} ],
  "relationships": [ {{"source": "...", "target": "...", "type": "..."}} ]
}}

Respond ONLY with raw JSON.

File Path: {file_path}
Content Snippet:
{content[:3000]}
"""
    try:
        response = requests.post(url, json={
            "model": os.environ.get("LEADAGENT_OLLAMA_MODEL", "llama3.2:3b"),
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }, timeout=60)
        response.raise_for_status()
        data = response.json()
        return json.loads(data.get("response", "{}"))
    except Exception as e:
        print(f"[Indexer] Ollama extraction failed for {file_path}: {e}")
        return {"entities": [], "relationships": []}

def process_file(file_path: str, project_id: str = "default"):
    """Index a single file into the graph."""
    try:
        with open(file_path, 'r', errors='ignore') as f:
            content = f.read()
        
        # 1. Update basic file nodes
        name = os.path.basename(file_path)
        ext = os.path.splitext(file_path)[1]
        db.add_file(file_path, name, ext, project_id)
        
        # 2. Extract deep context via Ollama
        data = extract_entities_via_ollama(file_path, content)
        
        # 3. Insert into Graph
        from backend.security import scrub_secrets, is_blocked_entity
        for e in data.get("entities", []):
            name = e["name"]
            if is_blocked_entity(name):
                continue
            description = scrub_secrets(e.get("description", ""))
            db.add_entity(
                name,
                e["type"],
                description,
                source_project_id=project_id,
                auto_extracted=True,
                source_agent="indexer",
            )
            db.link_entity_to_file(e["name"], file_path, project_id=project_id)
        
        for r in data.get("relationships", []):
            db.add_relationship(r["source"], r["target"], r["type"], project_id=project_id)
            
        print(f"✅ Proactively indexed {file_path} ({len(data.get('entities', []))} entities)")
        return True
    except Exception as e:
        print(f"[Indexer] Failed to process {file_path}: {e}")
        return False
