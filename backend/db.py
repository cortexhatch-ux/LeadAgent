import kuzu
import os
import threading
import time
import uuid

from backend.security import scrub_secrets, is_blocked_entity


class GraphDB:
    def __init__(self, db_path: str = "leadagent-data/db"):
        parent_dir = os.path.dirname(db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        # Kuzu's default buffer pool is ~80% of system memory, which inside a
        # container means the whole Docker VM — large enough to get the daemon
        # picked by the kernel OOM killer under memory pressure.
        buffer_mb = int(os.environ.get("LEADAGENT_KUZU_BUFFER_MB", "1024"))
        self.db = kuzu.Database(db_path, buffer_pool_size=buffer_mb * 1024 * 1024)
        self.connection = kuzu.Connection(self.db)
        self._lock = threading.Lock()
        self._init_schema()

    def _try_create(self, stmt: str):
        try:
            self.connection.execute(stmt)
        except Exception:
            pass  # Table already exists

    def _try_alter(self, stmt: str):
        try:
            self.connection.execute(stmt)
        except Exception:
            pass

    def _migrate_entity_composite_pk(self):
        """Upgrade Entity table to use id=project_id:name as composite PK."""
        try:
            self.connection.execute("MATCH (e:Entity) RETURN e.id LIMIT 1")
            return  # Already on new schema
        except Exception as exc:
            if "id" not in str(exc).lower() and "property" not in str(exc).lower():
                return  # Unknown error — don't touch the schema
        print("[DB Migration] Upgrading Entity table to composite PK (project_id:name). Existing entity data will be cleared.")
        for stmt in ("DROP TABLE RELATED_TO", "DROP TABLE DEFINED_IN", "DROP TABLE ABOUT", "DROP TABLE Entity"):
            try:
                self.connection.execute(stmt)
            except Exception:
                pass
        for stmt in (
            "CREATE NODE TABLE Entity(id STRING, name STRING, type STRING, description STRING, "
            "project_id STRING, confidence DOUBLE, last_seen DOUBLE, error_sourced BOOLEAN, "
            "source_agent STRING, created_at DOUBLE, PRIMARY KEY (id))",
            "CREATE REL TABLE RELATED_TO(FROM Entity TO Entity, type STRING, confidence DOUBLE)",
            "CREATE REL TABLE DEFINED_IN(FROM Entity TO File)",
            "CREATE REL TABLE ABOUT(FROM Question TO Entity)",
        ):
            try:
                self.connection.execute(stmt)
            except Exception:
                pass
        print("[DB Migration] Entity table upgraded.")

    def _init_schema(self):
        # Node tables — added project_id, confidence, last_seen for hygiene
        # Entity uses a composite id = f"{project_id}:{name}" as PK to allow
        # the same entity name to exist in multiple projects without collision.
        self._try_create(
            "CREATE NODE TABLE Entity(id STRING, name STRING, type STRING, description STRING, "
            "project_id STRING, confidence DOUBLE, last_seen DOUBLE, error_sourced BOOLEAN, "
            "source_agent STRING, created_at DOUBLE, PRIMARY KEY (id))"
        )
        self._migrate_entity_composite_pk()
        self._try_create(
            "CREATE NODE TABLE Concept(name STRING, description STRING, "
            "project_id STRING, confidence DOUBLE, last_seen DOUBLE, PRIMARY KEY (name))"
        )
        self._try_create(
            "CREATE NODE TABLE Question(id STRING, prompt STRING, answer STRING, agent STRING, "
            "timestamp DOUBLE, project_id STRING, error_sourced BOOLEAN, PRIMARY KEY (id))"
        )
        self._try_create(
            "CREATE NODE TABLE File(path STRING, name STRING, extension STRING, "
            "project_id STRING, last_indexed DOUBLE, PRIMARY KEY (path))"
        )
        self._try_create(
            "CREATE NODE TABLE Folder(path STRING, name STRING, "
            "project_id STRING, last_indexed DOUBLE, PRIMARY KEY (path))"
        )

        # Phase 1: Affinity & Error Taxonomy
        self._try_create("CREATE NODE TABLE TaskType(name STRING, PRIMARY KEY (name))")
        self._try_create("CREATE NODE TABLE AgentNode(name STRING, PRIMARY KEY (name))")
        self._try_create(
            "CREATE NODE TABLE ErrorType(name STRING, description STRING, PRIMARY KEY (name))"
        )

        # Rel tables
        self._try_create(
            "CREATE REL TABLE RELATED_TO(FROM Entity TO Entity, type STRING, confidence DOUBLE)"
        )
        self._try_create("CREATE REL TABLE CONTAINS(FROM Folder TO Folder)")
        self._try_create("CREATE REL TABLE HAS_FILE(FROM Folder TO File)")
        self._try_create("CREATE REL TABLE DEFINED_IN(FROM Entity TO File)")
        self._try_create("CREATE REL TABLE MENTIONS(FROM Entity TO Concept)")
        self._try_create("CREATE REL TABLE ABOUT(FROM Question TO Entity)")
        self._try_create("CREATE REL TABLE DISCUSSES(FROM Question TO Concept)")

        # Phase 1 relationships
        self._try_create(
            "CREATE REL TABLE AFFINITY(FROM TaskType TO AgentNode, score DOUBLE, count INT64)"
        )
        self._try_create(
            "CREATE REL TABLE FAILED_BECAUSE(FROM AgentNode TO ErrorType, count INT64)"
        )

        # MCP Rules layer — evaluated before user permission prompts
        self._try_create(
            "CREATE NODE TABLE MCPRule("
            "id STRING, "
            "tool_pattern STRING, "
            "action STRING, "
            "scope STRING, "
            "reason STRING, "
            "input_match STRING, "
            "priority INT64, "
            "created_at DOUBLE, "
            "PRIMARY KEY (id))"
        )

        # Migrations
        self._try_alter("ALTER TABLE Entity ADD error_sourced BOOLEAN DEFAULT false")
        self._try_alter("ALTER TABLE Question ADD error_sourced BOOLEAN DEFAULT false")
        self._try_alter("ALTER TABLE Question ADD session_id STRING DEFAULT 'default'")
        self._try_alter("ALTER TABLE Entity ADD source_agent STRING DEFAULT ''")
        self._try_alter("ALTER TABLE Entity ADD created_at DOUBLE DEFAULT 0.0")

    # ── write methods (all lock-protected) ──────────────────────────────────

    def add_file(self, path: str, name: str, extension: str, project_id: str):
        with self._lock:
            try:
                self.connection.execute(
                    "CREATE (f:File {path: $path, name: $name, extension: $ext, "
                    "project_id: $pid, last_indexed: $now})",
                    {
                        "path": path,
                        "name": name,
                        "ext": extension,
                        "pid": project_id,
                        "now": time.time(),
                    },
                )
            except Exception:
                try:
                    self.connection.execute(
                        "MATCH (f:File {path: $path}) SET f.last_indexed = $now",
                        {"path": path, "now": time.time()},
                    )
                except Exception:
                    pass

    def add_folder(self, path: str, name: str, project_id: str):
        with self._lock:
            try:
                self.connection.execute(
                    "CREATE (f:Folder {path: $path, name: $name, "
                    "project_id: $pid, last_indexed: $now})",
                    {"path": path, "name": name, "pid": project_id, "now": time.time()},
                )
            except Exception:
                try:
                    self.connection.execute(
                        "MATCH (f:Folder {path: $path}) SET f.last_indexed = $now",
                        {"path": path, "now": time.time()},
                    )
                except Exception:
                    pass

    def link_filesystem(self, parent_path: str, child_path: str, is_file: bool):
        with self._lock:
            try:
                rel = "HAS_FILE" if is_file else "CONTAINS"
                target_table = "File" if is_file else "Folder"
                self.connection.execute(
                    f"MATCH (p:Folder {{path: $ppath}}), (c:{target_table} {{path: $cpath}}) "
                    f"MERGE (p)-[:{rel}]->(c)",
                    {"ppath": parent_path, "cpath": child_path},
                )
            except Exception as e:
                print(f"[link_filesystem] Error linking {parent_path} -> {child_path}: {e}")

    def add_entity(
        self,
        name: str,
        type: str,
        description: str = "",
        source_project_id: str = "default",
        error_sourced: bool = False,
        auto_extracted: bool = False,
        source_agent: str = "",
    ):
        # Centralized brain-write guard: block sensitive names and scrub text on all paths.
        if is_blocked_entity(name):
            return
        if source_project_id == "default":
            name = scrub_secrets(name)
            description = scrub_secrets(description)

        # Auto-extracted entities start at 0.5 and need 5 corroborations to reach 1.0.
        # Human-curated or indexer-written entities start at 1.0.
        initial_confidence = 0.5 if auto_extracted else 1.0
        now = time.time()
        entity_id = f"{source_project_id}:{name}"
        with self._lock:
            try:
                # Try update first — match on composite id to avoid cross-project collisions.
                res = self.connection.execute(
                    "MATCH (e:Entity {id: $eid}) "
                    "SET e.last_seen = $now, "
                    "e.confidence = CASE WHEN e.confidence < 1.0 THEN e.confidence + 0.1 ELSE 1.0 END, "
                    "e.error_sourced = $es "
                    "RETURN count(e)",
                    {"eid": entity_id, "now": now, "es": error_sourced},
                )
                if res.has_next() and res.get_next()[0] > 0:
                    return

                # If not found, create new
                self.connection.execute(
                    "CREATE (e:Entity {id: $eid, name: $name, type: $type, description: $description, "
                    "project_id: $pid, confidence: $conf, last_seen: $now, error_sourced: $es, "
                    "source_agent: $agent, created_at: $created})",
                    {
                        "eid": entity_id,
                        "name": name,
                        "type": type,
                        "description": description,
                        "pid": source_project_id,
                        "conf": initial_confidence,
                        "now": now,
                        "es": error_sourced,
                        "agent": source_agent,
                        "created": now,
                    },
                )
            except Exception as e:
                print(f"[add_entity] Error: {e}")

    def add_concept(
        self, name: str, description: str = "", project_id: str = "default"
    ):
        if is_blocked_entity(name):
            return
        if project_id == "default":
            name = scrub_secrets(name)
            description = scrub_secrets(description)
        with self._lock:
            try:
                self.connection.execute(
                    "CREATE (c:Concept {name: $name, description: $desc, "
                    "project_id: $pid, confidence: 1.0, last_seen: $now})",
                    {
                        "name": name,
                        "desc": description,
                        "pid": project_id,
                        "now": time.time(),
                    },
                )
            except Exception:
                try:
                    self.connection.execute(
                        "MATCH (c:Concept {name: $name, project_id: $pid}) SET c.last_seen = $now",
                        {"name": name, "pid": project_id, "now": time.time()},
                    )
                except Exception:
                    pass

    def add_relationship(
        self, source: str, target: str, rel_type: str, confidence: float = 1.0, project_id: str = "default"
    ):
        if is_blocked_entity(source) or is_blocked_entity(target):
            return
        if project_id == "default":
            rel_type = scrub_secrets(rel_type)
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (a:Entity {id: $source_id}), (b:Entity {id: $target_id}) "
                    "MERGE (a)-[r:RELATED_TO {type: $rel_type}]->(b) "
                    "SET r.confidence = $conf",
                    {
                        "source_id": f"{project_id}:{source}",
                        "target_id": f"{project_id}:{target}",
                        "rel_type": rel_type,
                        "conf": confidence,
                    },
                )
            except Exception as e:
                print(f"[add_relationship] Error linking {source} -> {target}: {e}")

    def link_entity_to_file(self, entity_name: str, file_path: str, project_id: str = "default"):
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (e:Entity {id: $eid}), (f:File {path: $fpath}) "
                    "MERGE (e)-[:DEFINED_IN]->(f)",
                    {"eid": f"{project_id}:{entity_name}", "fpath": file_path},
                )
            except Exception as e:
                print(f"[link_entity_to_file] Error linking {entity_name} -> {file_path}: {e}")

    def prune_file(self, file_path: str):
        """Remove a file and any entities that were ONLY defined in that file."""
        with self._lock:
            try:
                # Delete entities that are ONLY in this file
                self.connection.execute(
                    "MATCH (e:Entity)-[:DEFINED_IN]->(f:File {path: $path}) "
                    "OPTIONAL MATCH (e)-[other:DEFINED_IN]->(f2:File) WHERE f2.path <> $path "
                    "WITH e, count(other) as other_links "
                    "WHERE other_links = 0 "
                    "DELETE e",
                    {"path": file_path}
                )
                
                # Delete the file node itself
                self.connection.execute(
                    "MATCH (f:File {path: $path}) DELETE f",
                    {"path": file_path}
                )
            except Exception as e:
                print(f"[prune_file] Error: {e}")

    def add_question(
        self, prompt: str, answer: str, agent: str, source_project_id: str = "default", error_sourced: bool = False, session_id: str = "default"
    ) -> str:
        qid = str(uuid.uuid4())
        # Scrub secrets from global-brain writes to prevent cross-project leakage.
        if source_project_id == "default":
            prompt = scrub_secrets(prompt)
            answer = scrub_secrets(answer)
        with self._lock:
            try:
                self.connection.execute(
                    "CREATE (q:Question {id: $id, prompt: $prompt, answer: $answer, "
                    "agent: $agent, timestamp: $ts, project_id: $pid, error_sourced: $es, session_id: $sid})",
                    {
                        "id": qid,
                        "prompt": prompt[:500],
                        "answer": answer[:1000],
                        "agent": agent,
                        "ts": time.time(),
                        "pid": source_project_id,
                        "es": error_sourced,
                        "sid": session_id,
                    },
                )
            except Exception as e:
                print(f"[add_question] Error: {e}")
        return qid

    # ── Memory Hygiene (Consensus Round 5) ──────────────────────────────────

    def run_hygiene(self, ttl_days: int = 30):
        """Background janitor: decay confidence and prune stale nodes."""
        now = time.time()
        ttl_seconds = ttl_days * 86400

        with self._lock:
            try:
                # 1. Decay confidence for all entities not seen recently
                self.connection.execute(
                    "MATCH (e:Entity) WHERE $now - e.last_seen > 86400 "
                    "SET e.confidence = e.confidence * 0.9",
                    {"now": now},
                )

                # 2. Prune low confidence, very old nodes, or error-sourced nodes
                self.connection.execute(
                    "MATCH (e:Entity) WHERE e.confidence < 0.1 OR $now - e.last_seen > $ttl OR e.error_sourced = true "
                    "DELETE e",
                    {"now": now, "ttl": ttl_seconds},
                )
                
                # 3. Prune error-sourced questions too
                self.connection.execute(
                    "MATCH (q:Question) WHERE q.error_sourced = true DELETE q"
                )
            except Exception as e:
                print(f"[Hygiene] Error: {e}")

    def start_janitor(self):
        def _loop():
            while True:
                time.sleep(3600 * 24)  # Run daily
                self.run_hygiene()

        threading.Thread(target=_loop, daemon=True, name="MemoryJanitor").start()

    def link_question_to_entity(self, question_id: str, entity_name: str, project_id: str = "default"):
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (q:Question {id: $qid}), (e:Entity {id: $eid}) CREATE (q)-[:ABOUT]->(e)",
                    {"qid": question_id, "eid": f"{project_id}:{entity_name}"},
                )
            except Exception as e:
                print(f"[link_question_to_entity] Error: {e}")

    def link_question_to_concept(self, question_id: str, concept_name: str):
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (q:Question {id: $qid}), (c:Concept {name: $cname}) CREATE (q)-[:DISCUSSES]->(c)",
                    {"qid": question_id, "cname": concept_name},
                )
            except Exception as e:
                print(f"[link_question_to_concept] Error: {e}")

    # ── Phase 1: Affinity & Errors ──────────────────────────────────────────

    def update_affinity(self, task_type: str, agent: str, score_delta: float):
        with self._lock:
            try:
                # Ensure nodes exist
                self.connection.execute(
                    "MERGE (t:TaskType {name: $t})", {"t": task_type}
                )
                self.connection.execute("MERGE (a:AgentNode {name: $a})", {"a": agent})

                # Update affinity
                self.connection.execute(
                    "MATCH (t:TaskType {name: $t}), (a:AgentNode {name: $a}) "
                    "MERGE (t)-[r:AFFINITY]->(a) "
                    "ON CREATE SET r.score = 0.5, r.count = 1 "
                    "ON MATCH SET r.score = r.score + $delta, r.count = r.count + 1",
                    {"t": task_type, "a": agent, "delta": score_delta},
                )
            except Exception as e:
                print(f"[update_affinity] Error: {e}")

    def log_agent_failure(self, agent: str, error_type: str):
        with self._lock:
            try:
                self.connection.execute("MERGE (a:AgentNode {name: $a})", {"a": agent})
                self.connection.execute(
                    "MERGE (e:ErrorType {name: $e})", {"e": error_type}
                )

                self.connection.execute(
                    "MATCH (a:AgentNode {name: $a}), (e:ErrorType {name: $e}) "
                    "MERGE (a)-[r:FAILED_BECAUSE]->(e) "
                    "ON CREATE SET r.count = 1 "
                    "ON MATCH SET r.count = r.count + 1",
                    {"a": agent, "e": error_type},
                )
            except Exception as e:
                print(f"[log_agent_failure] Error: {e}")

    # ── MCP Rules CRUD ───────────────────────────────────────────────────────

    def add_rule(
        self,
        tool_pattern: str,
        action: str,
        scope: str = "global",
        reason: str = "",
        input_match: str = "",
        priority: int = 0,
    ) -> str:
        rule_id = uuid.uuid4().hex
        with self._lock:
            self.connection.execute(
                "CREATE (r:MCPRule {id: $id, tool_pattern: $tp, action: $act, "
                "scope: $sc, reason: $rs, input_match: $im, priority: $pr, created_at: $ts})",
                {
                    "id": rule_id,
                    "tp": tool_pattern,
                    "act": action,
                    "sc": scope,
                    "rs": reason,
                    "im": input_match,
                    "pr": priority,
                    "ts": time.time(),
                },
            )
        return rule_id

    def list_rules(self) -> list:
        return self.query_all(
            "MATCH (r:MCPRule) RETURN r.id, r.tool_pattern, r.action, r.scope, "
            "r.reason, r.input_match, r.priority, r.created_at "
            "ORDER BY r.priority DESC, r.created_at ASC"
        )

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (r:MCPRule {id: $id}) DELETE r", {"id": rule_id}
                )
                return True
            except Exception:
                return False

    # ── read methods ─────────────────────────────────────────────────────────

    def query(self, cypher: str, params: dict = None):
        """Returns a raw cursor. Holds the lock during execute to avoid racing
        the janitor / learning threads that write concurrently."""
        with self._lock:
            return self.connection.execute(cypher, params or {})

    def query_all(self, cypher: str, params: dict = None) -> list:
        """Thread-safe read: acquires lock, executes, returns all rows as a list."""
        with self._lock:
            result = self.connection.execute(cypher, params or {})
            rows = []
            while result.has_next():
                rows.append(result.get_next())
            return rows


db = GraphDB()
