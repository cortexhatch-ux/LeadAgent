import kuzu
import os
import threading
import time
import uuid


class GraphDB:
    def __init__(self, db_path: str = "leadagent-data/db"):
        parent_dir = os.path.dirname(db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        self.db = kuzu.Database(db_path)
        self.connection = kuzu.Connection(self.db)
        self._lock = threading.Lock()
        self._init_schema()

    def _try_create(self, stmt: str):
        try:
            self.connection.execute(stmt)
        except Exception:
            pass  # Table already exists

    def _init_schema(self):
        # Node tables — added project_id, confidence, last_seen for hygiene
        self._try_create(
            "CREATE NODE TABLE Entity(name STRING, type STRING, description STRING, "
            "project_id STRING, confidence DOUBLE, last_seen DOUBLE, PRIMARY KEY (name))"
        )
        self._try_create(
            "CREATE NODE TABLE Concept(name STRING, description STRING, "
            "project_id STRING, confidence DOUBLE, last_seen DOUBLE, PRIMARY KEY (name))"
        )
        self._try_create(
            "CREATE NODE TABLE Question(id STRING, prompt STRING, answer STRING, agent STRING, "
            "timestamp DOUBLE, project_id STRING, PRIMARY KEY (id))"
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
            except Exception:
                pass

    def add_entity(
        self, name: str, type: str, description: str = "", project_id: str = "default"
    ):
        now = time.time()
        with self._lock:
            try:
                # Try update first. If MATCH succeeds, SET will run.
                res = self.connection.execute(
                    "MATCH (e:Entity {name: $name}) "
                    "SET e.last_seen = $now, e.confidence = COALESCE(e.confidence, 0.9) + 0.1 "
                    "RETURN count(e)",
                    {"name": name, "now": now},
                )
                if res.has_next() and res.get_next()[0] > 0:
                    return

                # If not found, create new
                self.connection.execute(
                    "CREATE (e:Entity {name: $name, type: $type, description: $description, "
                    "project_id: $pid, confidence: 1.0, last_seen: $now})",
                    {
                        "name": name,
                        "type": type,
                        "description": description,
                        "pid": project_id,
                        "now": now,
                    },
                )
            except Exception as e:
                print(f"[add_entity] Error: {e}")

    def add_concept(
        self, name: str, description: str = "", project_id: str = "default"
    ):
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
                        "MATCH (c:Concept {name: $name}) SET c.last_seen = $now",
                        {"name": name, "now": time.time()},
                    )
                except Exception:
                    pass

    def add_relationship(
        self, source: str, target: str, rel_type: str, confidence: float = 1.0
    ):
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (a:Entity {name: $source}), (b:Entity {name: $target}) "
                    "MERGE (a)-[r:RELATED_TO {type: $rel_type}]->(b) "
                    "SET r.confidence = $conf",
                    {
                        "source": source,
                        "target": target,
                        "rel_type": rel_type,
                        "conf": confidence,
                    },
                )
            except Exception:
                pass

    def add_question(
        self, prompt: str, answer: str, agent: str, project_id: str = "default"
    ) -> str:
        qid = str(uuid.uuid4())
        with self._lock:
            try:
                self.connection.execute(
                    "CREATE (q:Question {id: $id, prompt: $prompt, answer: $answer, "
                    "agent: $agent, timestamp: $ts, project_id: $pid})",
                    {
                        "id": qid,
                        "prompt": prompt[:500],
                        "answer": answer[:1000],
                        "agent": agent,
                        "ts": time.time(),
                        "pid": project_id,
                    },
                )
            except Exception:
                # Fallback for old schema
                self.connection.execute(
                    "CREATE (q:Question {id: $id, prompt: $prompt, answer: $answer, agent: $agent, timestamp: $ts})",
                    {
                        "id": qid,
                        "prompt": prompt[:500],
                        "answer": answer[:1000],
                        "agent": agent,
                        "ts": time.time(),
                    },
                )
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

                # 2. Prune low confidence or very old nodes
                self.connection.execute(
                    "MATCH (e:Entity) WHERE e.confidence < 0.1 OR $now - e.last_seen > $ttl "
                    "DELETE e",
                    {"now": now, "ttl": ttl_seconds},
                )
            except Exception as e:
                print(f"[Hygiene] Error: {e}")

    def start_janitor(self):
        def _loop():
            while True:
                time.sleep(3600 * 24)  # Run daily
                self.run_hygiene()

        threading.Thread(target=_loop, daemon=True, name="MemoryJanitor").start()

    def link_question_to_entity(self, question_id: str, entity_name: str):
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (q:Question {id: $qid}), (e:Entity {name: $ename}) CREATE (q)-[:ABOUT]->(e)",
                    {"qid": question_id, "ename": entity_name},
                )
            except Exception:
                pass

    def link_question_to_concept(self, question_id: str, concept_name: str):
        with self._lock:
            try:
                self.connection.execute(
                    "MATCH (q:Question {id: $qid}), (c:Concept {name: $cname}) CREATE (q)-[:DISCUSSES]->(c)",
                    {"qid": question_id, "cname": concept_name},
                )
            except Exception:
                pass

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

    # ── read methods ─────────────────────────────────────────────────────────

    def query(self, cypher: str, params: dict = None):
        """Returns a raw cursor. Only use when no background thread can write concurrently."""
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
