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
        """Stores a memory."""
        metadata = metadata or {}
        if metadata.get("project_id") == "default":
            from backend.security import scrub_secrets
            content = scrub_secrets(content)

        try:
            response = requests.post(
                f"{self.url}/agentmemory/remember",
                json={"content": content, "metadata": {**metadata, "tier": tier}},
                timeout=5.0,
            )
            if not response.ok:
                print(f"[MemoryClient] store failed with status {response.status_code}: {response.text}")
            return response.ok
        except requests.exceptions.Timeout:
            print("[MemoryClient] store timed out (transient)")
            return False
        except Exception as e:
            print(f"[MemoryClient] store fatal error: {e}")
            return False

    def search(self, query: str, limit: int = 5, project_id: str = None, strict: bool = False):
        """Searches for relevant memories."""
        try:
            body: dict = {"query": query, "limit": limit}
            # Always send the server-side filter when a project is known so the
            # server can prune before returning. Client-side re-filter below is
            # defence-in-depth in case the server ignores the filter param.
            if project_id and project_id != "default":
                body["filter"] = {"project_id": project_id}
            response = requests.post(
                f"{self.url}/agentmemory/search",
                json=body,
                timeout=5.0,
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                # Defence-in-depth: re-filter client-side regardless of server behaviour.
                if project_id and project_id != "default":
                    allowed = {project_id} if strict else {project_id, "default"}
                    results = [
                        r for r in results
                        if r.get("metadata", {}).get("project_id") in allowed
                    ]
                return results
            print(f"[MemoryClient] search failed with status {response.status_code}: {response.text}")
            return []
        except requests.exceptions.Timeout:
            print("[MemoryClient] search timed out (transient)")
            return []
        except Exception as e:
            print(f"[MemoryClient] search fatal error: {e}")
            return []


memory_client = AgentMemoryClient()
