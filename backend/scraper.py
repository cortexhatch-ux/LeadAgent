import os
import json
import subprocess
import hashlib
import time
import threading
from backend.db import db
from typing import List, Dict

class ContextScraper:
    def __init__(self):
        self.agent_targets = {
            "claude": [".claude/CLAUDE.md", "CLAUDE.md"],
            "gemini": ["GEMINI.md"],
            "codex": [".cursor/rules", ".aider.chat.history.md"]
        }
        self.file_hashes: Dict[str, str] = {}
        self._watch_threads: Dict[str, bool] = {}

    def auto_discovery(self, project_path: str):
        """Index the filesystem structure without LLM tokens (Consensus Phase 4 Extension)."""
        print(f"🗺️  Auto-discovering structure for {project_path}...")
        root_path = os.path.abspath(project_path)
        project_id = project_path # use path as ID
        
        # Add root folder
        db.add_folder(root_path, os.path.basename(root_path), project_id)

        count = 0
        for root, dirs, files in os.walk(root_path):
            # Prune hidden dirs and common ignore targets
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'venv', 'leadagent')]
            
            for d in dirs:
                dir_path = os.path.join(root, d)
                db.add_folder(dir_path, d, project_id)
                db.link_filesystem(root, dir_path, is_file=False)
                count += 1
                
            for f in files:
                if f.startswith('.'): continue
                file_path = os.path.join(root, f)
                ext = os.path.splitext(f)[1]
                db.add_file(file_path, f, ext, project_id)
                db.link_filesystem(root, file_path, is_file=True)
                count += 1
            
            if count > 1000: # safety break for massive un-ignored folders
                break
        
        print(f"✅ Indexed {count} filesystem nodes for {project_id}.")

    def _get_hash(self, file_path: str) -> str:
        try:
            with open(file_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except:
            return ""

    def scan_and_import(self, project_path: str, enabled_agents: List[str]):
        print(f"🔍 Scanning context for connected agents in {project_path}...")
        found_files = []
        
        for agent in enabled_agents:
            key = agent.lower().split()[0]
            targets = self.agent_targets.get(key, [])
            for target in targets:
                full_path = os.path.join(project_path, target)
                if os.path.exists(full_path):
                    found_files.append(full_path)

        # Only proceed if files have changed
        changed = False
        for f in found_files:
            h = self._get_hash(f)
            if h != self.file_hashes.get(f):
                self.file_hashes[f] = h
                changed = True
        
        if not changed:
            # print("ℹ️ No changes in context files.")
            return

        print(f"📄 Changes detected in context files. Starting ingestion...")

        combined_text = ""
        for f in found_files:
            try:
                with open(f, 'r', errors='ignore') as file:
                    combined_text += f"\n--- Source: {f} ---\n"
                    combined_text += file.read()[:5000] 
            except:
                continue

        if combined_text:
            self._ingest_to_graph_via_cli(combined_text, project_path)

    def start_watcher(self, project_path: str, enabled_agents: List[str]):
        if project_path in self._watch_threads:
            return
        
        def _loop():
            self._watch_threads[project_path] = True
            # Initial full discovery
            self.auto_discovery(project_path)
            
            discovery_counter = 0
            while True:
                try:
                    self.scan_and_import(project_path, enabled_agents)
                    
                    # Periodic re-discovery (every 10 minutes)
                    discovery_counter += 1
                    if discovery_counter >= 10:
                        self.auto_discovery(project_path)
                        discovery_counter = 0
                except Exception as e:
                    print(f"[Watcher] Error: {e}")
                time.sleep(60) # check every minute
        
        threading.Thread(target=_loop, daemon=True, name=f"Watcher-{project_path}").start()

    def _ingest_to_graph_via_cli(self, text: str, project_id: str):
        # Use the official 'gemini' CLI for extraction
        from backend.agents import _build_argv
        try:
            prompt = (
                "Extract key project entities (modules, technologies, rules, architectural patterns, previous discussion conclusions) "
                "and their relationships from the following project context. "
                "Format as a JSON object with 'entities' (list of {name, type, description}) and "
                "'relationships' (list of {source, target, type}). Only return raw JSON.\n\n"
                f"Context:\n{text}"
            )
            
            cmd = _build_argv("gemini", ["-p", prompt, "--skip-trust"])
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )
            
            output = process.stdout.strip()
            if "```" in output:
                output = output.replace("```json", "").replace("```", "").strip()
            
            data = json.loads(output)
            
            for e in data.get('entities', []):
                db.add_entity(e['name'], e['type'], e.get('description', ''), project_id=project_id)
            
            for r in data.get('relationships', []):
                db.add_relationship(r['source'], r['target'], r['type'])
                
            print(f"✅ Successfully updated {len(data.get('entities', []))} entities from context in {project_id}.")
        except Exception as e:
            print(f"⚠️ Context ingestion failed: {e}")

scraper = ContextScraper()
