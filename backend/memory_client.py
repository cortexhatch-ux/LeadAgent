import requests
import os


class AgentMemoryClient:
    def __init__(self, url: str = None):
        if url is None:
            host = (
                "host.docker.internal"
                if os.environ.get("LEADAGENT_DOCKER_MODE")
                else "localhost"
            )
            url = f"http://{host}:3111"
        self.url = url

    def store(self, content: str, metadata: dict = None, tier: str = "semantic"):
        """Stores a memory. Tiers: working, episodic, semantic, procedural."""
        try:
            response = requests.post(
                f"{self.url}/memories",
                json={"content": content, "metadata": metadata or {}, "tier": tier},
                timeout=2.0,
            )
            if response.status_code != 201:
                print(f"[MemoryClient] store failed with status {response.status_code}: {response.text}")
            return response.status_code == 201
        except requests.exceptions.Timeout:
            print("[MemoryClient] store timed out (transient)")
            return False
        except Exception as e:
            print(f"[MemoryClient] store fatal error: {e}")
            return False

    def search(self, query: str, limit: int = 5):
        """Searches for relevant memories."""
        try:
            response = requests.get(
                f"{self.url}/search", params={"q": query, "limit": limit}, timeout=2.0
            )
            if response.status_code == 200:
                return response.json().get("results", [])
            print(f"[MemoryClient] search failed with status {response.status_code}: {response.text}")
            return []
        except requests.exceptions.Timeout:
            print("[MemoryClient] search timed out (transient)")
            return []
        except Exception as e:
            print(f"[MemoryClient] search fatal error: {e}")
            return []


memory_client = AgentMemoryClient()
