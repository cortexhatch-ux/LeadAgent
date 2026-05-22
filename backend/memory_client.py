import requests
import json
import os

class AgentMemoryClient:
    def __init__(self, url: str = None):
        if url is None:
            host = "host.docker.internal" if os.environ.get("LEADAGENT_DOCKER_MODE") else "localhost"
            url = f"http://{host}:3111"
        self.url = url

    def store(self, content: str, metadata: dict = None, tier: str = "semantic"):
        """Stores a memory. Tiers: working, episodic, semantic, procedural."""
        try:
            response = requests.post(f"{self.url}/memories", json={
                "content": content,
                "metadata": metadata or {},
                "tier": tier
            }, timeout=2.0)
            return response.status_code == 201
        except Exception as e:
            # Silent fail for store to avoid blocking main flow
            return False

    def search(self, query: str, limit: int = 5):
        """Searches for relevant memories."""
        try:
            response = requests.get(f"{self.url}/search", params={
                "q": query,
                "limit": limit
            }, timeout=2.0)
            if response.status_code == 200:
                return response.json().get("results", [])
            return []
        except Exception as e:
            # Silent fail for search to avoid blocking main flow
            return []

memory_client = AgentMemoryClient()
