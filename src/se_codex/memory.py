"""Small, auditable memory gateway for local Codex agents.

The store provides governance and provenance for normal callers.  It is not a
security sandbox: a process that can directly rewrite this SQLite database can
bypass every check in this module.  A protected deployment must enforce the
role boundary outside this process (for example with a separate OS identity).
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePath
import re
import sqlite3
import threading
from typing import Any, Iterator
from uuid import uuid4


SCHEMA_VERSION = 1
VALID_ROLES = {"operator", "worker"}
VALID_SOURCE_KINDS = {"external", "self_observed", "user_stated", "tool"}
VALID_EFFECT_STATES = {
    "applied",
    "failed",
    "uncertain",
    "compensated",
    "manual_required",
}
CAUSAL_RELATIONS = {
    "used",
    "recalled",
    "derived",
    "produced",
    "artifact_used",
    "effect_planned",
    "compensates",
}


class MemoryStoreError(ValueError):
    """A public gateway error with a stable adapter-facing code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _required_text(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MemoryStoreError("invalid_input", f"{name} must be a non-empty string")
    if "\x00" in value:
        raise MemoryStoreError("invalid_input", f"{name} contains a NUL byte")
    return value


def _optional_text(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise MemoryStoreError("invalid_input", f"{name} must be a non-empty string or null")
    return value


def _normalize_expiration(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MemoryStoreError("invalid_input", "expires_at must be an ISO-8601 string or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MemoryStoreError("invalid_input", "expires_at must be an ISO-8601 string") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _looks_sensitive(content: str) -> bool:
    """Reject only obvious credential material; this is not a secret scanner."""
    if re.search(r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----", content):
        return True
    return bool(
        re.search(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{20,}",
            content,
        )
    )


class MemoryStore:
    """SQLite-backed memory gateway with one fixed role and scope."""

    def __init__(
        self,
        db_path: str,
        *,
        role: str = "operator",
        scope: str = "default",
        max_memories: int = 1_000,
        max_content_chars: int = 50_000,
        max_recall: int = 3,
        max_recall_chars: int = 12_000,
    ) -> None:
        if role not in VALID_ROLES:
            raise MemoryStoreError("unknown_role", f"unknown role: {role!r}")
        if not isinstance(scope, str) or not scope.strip():
            raise MemoryStoreError("invalid_scope", "scope must be a non-empty string")
        limits = (max_memories, max_content_chars, max_recall, max_recall_chars)
        if any(type(value) is not int or value < 1 for value in limits):
            raise MemoryStoreError("invalid_input", "capacity limits must be positive")

        self.db_path = str(db_path)
        self.role = role
        self.scope = scope
        self.max_memories = max_memories
        self.max_content_chars = max_content_chars
        self.max_recall = max_recall
        self.max_recall_chars = max_recall_chars
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=10,
                isolation_level=None,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                self._conn.close()
                raise MemoryStoreError("unsupported_schema", "database schema is not supported")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 10000")
            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._initialize()
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as error:
            raise MemoryStoreError("storage_error", "unable to initialize memory storage") from error

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS store_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY, scope TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scopes (
                scope TEXT PRIMARY KEY,
                epoch INTEGER NOT NULL DEFAULT 0,
                frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                kind TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'released')),
                closure_complete INTEGER NOT NULL DEFAULT 0 CHECK (closure_complete IN (0, 1)),
                created_at TEXT NOT NULL,
                closed_at TEXT,
                release_evidence TEXT
            );

            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                kind TEXT NOT NULL,
                locator TEXT NOT NULL,
                source_version TEXT,
                raw_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                source_id TEXT REFERENCES sources(source_id),
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('candidate', 'active', 'quarantined', 'revoked', 'superseded')),
                expires_at TEXT,
                invalidation_condition TEXT,
                policy_version TEXT,
                created_run_id TEXT,
                supersedes_revision INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (memory_id, revision)
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                thread_id TEXT,
                model TEXT,
                effort TEXT,
                status TEXT NOT NULL,
                memory_epoch INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS recalls (
                receipt_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                memory_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                delivered_sha256 TEXT NOT NULL,
                query_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (memory_id, revision) REFERENCES memories(memory_id, revision)
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                kind TEXT NOT NULL,
                locator TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('active', 'needs_review')),
                producing_run_id TEXT NOT NULL REFERENCES runs(run_id),
                created_at TEXT NOT NULL,
                PRIMARY KEY (artifact_id, revision)
            );

            CREATE TABLE IF NOT EXISTS effects (
                effect_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                tool TEXT NOT NULL,
                target TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL,
                external_reference TEXT,
                result_sha256 TEXT,
                compensation_kind TEXT,
                compensates_effect_id TEXT REFERENCES effects(effect_id),
                impacted INTEGER NOT NULL DEFAULT 0 CHECK (impacted IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (scope, idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                parent_kind TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                parent_revision INTEGER,
                child_kind TEXT NOT NULL,
                child_id TEXT NOT NULL,
                child_revision INTEGER,
                relation TEXT NOT NULL,
                run_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS safety_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                incident_id TEXT,
                event_type TEXT NOT NULL,
                object_kind TEXT NOT NULL,
                object_id TEXT NOT NULL,
                revision INTEGER,
                reason TEXT,
                evidence TEXT,
                path_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS restore_plans (
                plan_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL REFERENCES scopes(scope),
                base_event_seq INTEGER NOT NULL,
                base_epoch INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                diff_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('ready', 'applied')),
                result_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memories_scope_state
                ON memories(scope, state, expires_at);
            CREATE INDEX IF NOT EXISTS idx_edges_parent
                ON edges(scope, parent_kind, parent_id, parent_revision);
            CREATE INDEX IF NOT EXISTS idx_events_incident
                ON safety_events(scope, incident_id, seq);
            """
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO scopes(scope, epoch, frozen) VALUES (?, 0, 0)",
            (self.scope,),
        )
        self._conn.execute("INSERT OR IGNORE INTO store_metadata VALUES ('store_id', ?)", (_new_id('store'),))
        self.store_id = self._conn.execute("SELECT value FROM store_metadata WHERE key = 'store_id'").fetchone()[0]

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def execute(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one named operation through the role/scope checked gateway."""
        if not isinstance(operation, str) or operation not in self._operations():
            raise MemoryStoreError("unknown_operation", f"unknown operation: {operation!r}")
        if not isinstance(arguments, dict):
            raise MemoryStoreError("invalid_input", "arguments must be an object")
        if arguments.get("scope") != self.scope:
            raise MemoryStoreError("scope_mismatch", "operation scope does not match store scope")

        try:
            data = self._operations()[operation](arguments)
        except MemoryStoreError:
            raise
        except sqlite3.Error as error:
            raise MemoryStoreError("storage_error", "memory storage operation failed") from error
        return {"ok": True, "data": data}

    def _operations(self):
        return {
            'status': self._status,
            "source-propose": self._source_propose,
            "candidate-list": self._candidate_list,
            "activate": self._activate,
            "run-start": self._run_start,
            "recall": self._recall,
            "source-use": self._source_use,
            "artifact-record": self._artifact_record,
            "artifact-use": self._artifact_use,
            "memory-derive": self._memory_derive,
            "quarantine": self._quarantine,
            "revoke": self._revoke,
            "policy-revoke": self._policy_revoke,
            "incident-report": self._incident_report,
            "snapshot": self._snapshot,
            "restore-plan": self._restore_plan,
            "restore": self._restore,
            "effect-plan": self._effect_plan,
            "effect-record": self._effect_record,
            "scope-release": self._scope_release,
        }

    def _operator_only(self) -> None:
        if self.role != "operator":
            raise MemoryStoreError("permission_denied", "operation requires operator role")

    def _status(self, arguments):
        self._operator_only()
        with self._transaction() as conn:
            scope = dict(self._scope_row(conn))
            scope['counts'] = {row['state']: row['count'] for row in conn.execute('SELECT state, COUNT(*) AS count FROM memories WHERE scope = ? GROUP BY state', (self.scope,))}
            scope['open_incidents'] = [dict(row) for row in conn.execute("SELECT incident_id, kind, closure_complete, created_at FROM incidents WHERE scope = ? AND status = 'open'", (self.scope,))]
            scope['event_seq'] = conn.execute('SELECT COALESCE(MAX(seq), 0) FROM safety_events WHERE scope = ?', (self.scope,)).fetchone()[0]
            scope['storage_mode'] = 'governance; direct database access must be restricted by the host'
        return scope

    def _scope_row(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM scopes WHERE scope = ?", (self.scope,)).fetchone()
        if row is None:
            raise MemoryStoreError("invalid_scope", "scope is not initialized")
        return row

    def _require_unfrozen(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = self._scope_row(conn)
        if row["frozen"]:
            raise MemoryStoreError("scope_frozen", "scope is frozen by an open incident")
        return row

    def _require_current_run(self, conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        run = conn.execute(
            "SELECT * FROM runs WHERE run_id = ? AND scope = ?", (run_id, self.scope)
        ).fetchone()
        if run is None:
            raise MemoryStoreError("not_found", "run was not found in this scope")
        scope = self._require_unfrozen(conn)
        if run["memory_epoch"] != scope["epoch"]:
            raise MemoryStoreError("stale_epoch", "run uses an old memory epoch")
        if run["status"] != "running":
            raise MemoryStoreError("invalid_state", "run is not active")
        return run

    def _check_content(self, content: str) -> None:
        if len(content) > self.max_content_chars:
            raise MemoryStoreError("capacity_exceeded", "content exceeds configured limit")
        if _looks_sensitive(content):
            raise MemoryStoreError("sensitive_content", "obvious credential material is not accepted")

    def _check_memory_capacity(self, conn: sqlite3.Connection, additions: int = 1) -> None:
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE scope = ?", (self.scope,)
        ).fetchone()[0]
        if count + additions > self.max_memories:
            raise MemoryStoreError("capacity_exceeded", "memory capacity has been reached")

    def _event(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        object_kind: str,
        object_id: str,
        *,
        revision: int | None = None,
        incident_id: str | None = None,
        reason: str | None = None,
        evidence: str | None = None,
        path: list[dict[str, Any]] | None = None,
    ) -> int:
        cursor = conn.execute(
            """INSERT INTO safety_events
               (scope, incident_id, event_type, object_kind, object_id, revision,
                reason, evidence, path_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.scope,
                incident_id,
                event_type,
                object_kind,
                object_id,
                revision,
                reason,
                evidence,
                _canonical(path) if path is not None else None,
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def _add_edge(
        self,
        conn: sqlite3.Connection,
        parent_kind: str,
        parent_id: str,
        parent_revision: int | None,
        child_kind: str,
        child_id: str,
        child_revision: int | None,
        relation: str,
        run_id: str | None = None,
    ) -> str:
        existing = conn.execute(
            """SELECT edge_id FROM edges
               WHERE scope = ? AND parent_kind = ? AND parent_id = ?
                 AND parent_revision IS ? AND child_kind = ? AND child_id = ?
                 AND child_revision IS ? AND relation = ? AND run_id IS ?""",
            (
                self.scope,
                parent_kind,
                parent_id,
                parent_revision,
                child_kind,
                child_id,
                child_revision,
                relation,
                run_id,
            ),
        ).fetchone()
        if existing:
            return existing["edge_id"]
        edge_id = _new_id("edge")
        conn.execute(
            """INSERT INTO edges
               (edge_id, scope, parent_kind, parent_id, parent_revision,
                child_kind, child_id, child_revision, relation, run_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edge_id,
                self.scope,
                parent_kind,
                parent_id,
                parent_revision,
                child_kind,
                child_id,
                child_revision,
                relation,
                run_id,
                _now(),
            ),
        )
        return edge_id

    def _source_propose(self, arguments: dict[str, Any]) -> dict[str, Any]:
        source_kind = _required_text(arguments, "source_kind")
        if source_kind not in VALID_SOURCE_KINDS:
            raise MemoryStoreError("invalid_input", "unsupported source_kind")
        locator = _required_text(arguments, "locator")
        content = _required_text(arguments, "content")
        self._check_content(content)
        source_version = _optional_text(arguments, "source_version")
        expires_at = _normalize_expiration(arguments.get("expires_at"))
        invalidation = _optional_text(arguments, "invalidation_condition")

        with self._transaction() as conn:
            self._require_unfrozen(conn)
            self._check_memory_capacity(conn)
            source_id = _new_id("src")
            memory_id = _new_id("mem")
            created_at = _now()
            content_hash = _digest(content)
            conn.execute(
                """INSERT INTO sources
                   (source_id, scope, kind, locator, source_version, raw_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_id,
                    self.scope,
                    source_kind,
                    locator,
                    source_version,
                    content_hash,
                    created_at,
                ),
            )
            conn.execute(
                """INSERT INTO memories
                   (memory_id, revision, scope, source_id, content, content_sha256,
                    state, expires_at, invalidation_condition, created_at)
                   VALUES (?, 1, ?, ?, ?, ?, 'candidate', ?, ?, ?)""",
                (
                    memory_id,
                    self.scope,
                    source_id,
                    content,
                    content_hash,
                    expires_at,
                    invalidation,
                    created_at,
                ),
            )
            self._add_edge(conn, "source", source_id, None, "memory", memory_id, 1, "proposed")
            self._event(conn, "proposed", "memory", memory_id, revision=1)
        return {
            "source_id": source_id,
            "memory_id": memory_id,
            "revision": 1,
            "state": "candidate",
            "content_sha256": content_hash,
        }

    def _candidate_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        limit, offset = arguments.get('limit', 20), arguments.get('offset', 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise MemoryStoreError('invalid_input', 'limit must be 1..100 and offset must be non-negative')
        with self._transaction() as conn:
            rows = conn.execute(
                """SELECT m.*, s.kind AS source_kind, s.locator, s.source_version
                   FROM memories m LEFT JOIN sources s ON s.source_id = m.source_id
                   WHERE m.scope = ? AND m.state IN ('candidate', 'quarantined')
                   ORDER BY m.created_at, m.memory_id, m.revision LIMIT ? OFFSET ?""",
                (self.scope, limit, offset),
            ).fetchall()
            candidates = []
            for row in rows:
                if _digest(row["content"]) != row["content_sha256"]:
                    raise MemoryStoreError("storage_integrity", "memory content hash mismatch")
                candidates.append(
                    {
                        "memory_id": row["memory_id"],
                        "revision": row["revision"],
                        "state": row["state"],
                        "content": row["content"],
                        "content_sha256": row["content_sha256"],
                        "source_id": row["source_id"],
                        "source_kind": row["source_kind"],
                        "locator": row["locator"],
                        "source_version": row["source_version"],
                    }
                )
        return {"candidates": candidates}

    def _get_memory(
        self, conn: sqlite3.Connection, memory_id: str, revision: int
    ) -> sqlite3.Row:
        row = conn.execute(
            """SELECT * FROM memories
               WHERE scope = ? AND memory_id = ? AND revision = ?""",
            (self.scope, memory_id, revision),
        ).fetchone()
        if row is None:
            raise MemoryStoreError("not_found", "memory revision was not found in this scope")
        if _digest(row["content"]) != row["content_sha256"]:
            raise MemoryStoreError("storage_integrity", "memory content hash mismatch")
        return row

    def _activate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        memory_id = _required_text(arguments, "memory_id")
        revision = self._revision_argument(arguments)
        evidence = _required_text(arguments, "evidence")
        policy_version = _required_text(arguments, "policy_version")

        with self._transaction() as conn:
            scope = self._require_unfrozen(conn)
            memory = self._get_memory(conn, memory_id, revision)
            if conn.execute("SELECT 1 FROM safety_events WHERE scope = ? AND object_kind = 'policy' AND object_id = ? AND event_type = 'revoked'", (self.scope, policy_version)).fetchone():
                raise MemoryStoreError("revoked_policy", "policy version was revoked")
            if memory['created_run_id']:
                run = conn.execute("SELECT status FROM runs WHERE run_id = ? AND scope = ?", (memory['created_run_id'], self.scope)).fetchone()
                if run is None or run['status'] == 'impacted':
                    raise MemoryStoreError("unsafe_provenance", "derive a new candidate in a clean run")
            for edge in conn.execute("SELECT * FROM edges WHERE scope = ? AND child_kind = 'memory' AND child_id = ? AND child_revision = ? AND relation = 'derived'", (self.scope, memory_id, revision)):
                if edge['parent_kind'] in ('memory', 'artifact'):
                    self._require_safe_parent(conn, edge['parent_kind'], edge['parent_id'], edge['parent_revision'])
            for edge in conn.execute("SELECT parent_id FROM edges WHERE scope = ? AND child_kind = 'memory' AND child_id = ? AND child_revision = ? AND parent_kind = 'source' AND relation = 'used'", (self.scope, memory_id, revision)):
                self._require_safe_source(conn, edge['parent_id'])
            if memory["state"] == "revoked":
                raise MemoryStoreError("revoked", "revoked memory cannot be reactivated")
            if memory["state"] == "superseded":
                raise MemoryStoreError("invalid_state", "superseded memory cannot be reactivated")
            if memory["expires_at"] and memory["expires_at"] <= _now():
                raise MemoryStoreError("expired", "expired memory cannot be activated")
            if memory["state"] == "active":
                if memory["policy_version"] != policy_version:
                    raise MemoryStoreError("conflict", "memory is active under another policy version")
                return {
                    "memory_id": memory_id,
                    "revision": revision,
                    "state": "active",
                    "memory_epoch": scope["epoch"],
                    "idempotent": True,
                }
            conn.execute(
                """UPDATE memories SET state = 'active', policy_version = ?
                   WHERE memory_id = ? AND revision = ?""",
                (policy_version, memory_id, revision),
            )
            if memory['supersedes_revision']:
                conn.execute("UPDATE memories SET state = 'superseded' WHERE memory_id = ? AND revision = ? AND state = 'active'", (memory_id, memory['supersedes_revision']))
            new_epoch = scope["epoch"] + 1
            conn.execute("UPDATE scopes SET epoch = ? WHERE scope = ?", (new_epoch, self.scope))
            event_type = "revalidated" if memory["state"] == "quarantined" else "activated"
            event_seq = self._event(
                conn,
                event_type,
                "memory",
                memory_id,
                revision=revision,
                evidence=evidence,
                reason=f"policy:{policy_version}",
            )
        return {
            "memory_id": memory_id,
            "revision": revision,
            "state": "active",
            "memory_epoch": new_epoch,
            "event_seq": event_seq,
        }

    def _run_start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        thread_id = _optional_text(arguments, "thread_id")
        model = _optional_text(arguments, "model")
        effort = _optional_text(arguments, "effort")
        with self._transaction() as conn:
            scope = self._require_unfrozen(conn)
            run_id = _new_id("run")
            conn.execute(
                """INSERT INTO runs
                   (run_id, scope, thread_id, model, effort, status, memory_epoch, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
                (run_id, self.scope, thread_id, model, effort, scope["epoch"], _now()),
            )
        return {"run_id": run_id, "memory_epoch": scope["epoch"], "status": "running"}

    def _recall(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_text(arguments, "run_id")
        query = _required_text(arguments, "query")
        requested_limit = arguments.get("limit", self.max_recall)
        if not isinstance(requested_limit, int) or isinstance(requested_limit, bool) or requested_limit < 1:
            raise MemoryStoreError("invalid_input", "limit must be a positive integer")
        limit = min(requested_limit, self.max_recall)
        tokens = sorted(set(re.findall(r"\w+", query.casefold())))

        with self._transaction() as conn:
            run = self._require_current_run(conn, run_id)
            rows = conn.execute(
                """SELECT m.*, s.kind AS source_kind, s.locator, s.source_version
                   FROM memories m LEFT JOIN sources s ON s.source_id = m.source_id
                   WHERE m.scope = ? AND m.state = 'active'
                     AND (m.expires_at IS NULL OR m.expires_at > ?)
                     AND NOT EXISTS (
                         SELECT 1 FROM safety_events e
                         WHERE e.scope = m.scope AND e.object_kind = 'memory'
                           AND e.object_id = m.memory_id AND e.revision = m.revision
                           AND (e.event_type = 'revoked' OR (e.event_type = 'quarantined'
                           AND NOT EXISTS (
                               SELECT 1 FROM safety_events r
                               WHERE r.scope = e.scope AND r.object_kind = e.object_kind
                                 AND r.object_id = e.object_id AND r.revision = e.revision
                                 AND r.event_type = 'revalidated' AND r.seq > e.seq
                           )))
                     )""",
                (self.scope, _now()),
            ).fetchall()
            ranked: list[tuple[int, sqlite3.Row]] = []
            for row in rows:
                if _digest(row["content"]) != row["content_sha256"]:
                    raise MemoryStoreError("storage_integrity", "memory content hash mismatch")
                haystack = row["content"].casefold()
                score = sum(haystack.count(token) for token in tokens)
                if score:
                    ranked.append((score, row))
            ranked.sort(key=lambda item: (-item[0], item[1]["memory_id"], item[1]["revision"]))

            memories: list[dict[str, Any]] = []
            receipt_ids: list[str] = []
            delivered_chars = 0
            for score, row in ranked:
                if len(memories) >= limit:
                    break
                if delivered_chars + len(row['content']) > self.max_recall_chars:
                    continue
                delivered_chars += len(row['content'])
                receipt_id = _new_id("recall")
                conn.execute(
                    """INSERT INTO recalls
                       (receipt_id, scope, run_id, memory_id, revision,
                        delivered_sha256, query_sha256, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        receipt_id,
                        self.scope,
                        run_id,
                        row["memory_id"],
                        row["revision"],
                        row["content_sha256"],
                        _digest(query),
                        _now(),
                    ),
                )
                self._add_edge(
                    conn,
                    "memory",
                    row["memory_id"],
                    row["revision"],
                    "run",
                    run_id,
                    None,
                    "recalled",
                    run_id,
                )
                receipt_ids.append(receipt_id)
                memories.append(
                    {
                        "memory_id": row["memory_id"],
                        "revision": row["revision"],
                        "content": row["content"],
                        "content_sha256": row["content_sha256"],
                        "source": {
                            "source_id": row["source_id"],
                            "kind": row["source_kind"],
                            "locator": row["locator"],
                            "version": row["source_version"],
                        },
                        "score": score,
                    }
                )
        return {
            "run_id": run_id,
            "memory_epoch": run["memory_epoch"],
            "memories": memories,
            "receipt_ids": receipt_ids,
        }

    def _source_use(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_text(arguments, "run_id")
        source_id = _required_text(arguments, "source_id")
        relation = _required_text(arguments, "relation")
        if relation not in {"observed", "used"}:
            raise MemoryStoreError("invalid_input", "relation must be observed or used")
        with self._transaction() as conn:
            self._require_current_run(conn, run_id)
            source = conn.execute(
                "SELECT 1 FROM sources WHERE source_id = ? AND scope = ?",
                (source_id, self.scope),
            ).fetchone()
            if source is None:
                raise MemoryStoreError("not_found", "source was not found in this scope")
            self._require_safe_source(conn, source_id)
            edge_id = self._add_edge(
                conn, "source", source_id, None, "run", run_id, None, relation, run_id
            )
        return {"edge_id": edge_id, "relation": relation}

    def _artifact_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_text(arguments, "run_id")
        kind = _required_text(arguments, "kind")
        locator = _required_text(arguments, "locator")
        content = _required_text(arguments, "content")
        if kind in {"file", "path", "workspace"} and ".." in PurePath(locator).parts:
            raise MemoryStoreError("path_traversal", "artifact locator contains parent traversal")
        artifact_id_arg = arguments.get("artifact_id")
        if artifact_id_arg is not None and (
            not isinstance(artifact_id_arg, str) or not artifact_id_arg.strip()
        ):
            raise MemoryStoreError("invalid_input", "artifact_id must be a non-empty string")
        parent_memories = self._memory_refs(arguments.get("parent_memories", []))
        parent_artifacts = self._artifact_refs(arguments.get("parent_artifacts", []))

        with self._transaction() as conn:
            self._require_current_run(conn, run_id)
            artifact_id = artifact_id_arg or _new_id("artifact")
            last = conn.execute(
                "SELECT MAX(revision) FROM artifacts WHERE artifact_id = ? AND scope = ?",
                (artifact_id, self.scope),
            ).fetchone()[0]
            revision = int(last or 0) + 1
            conn.execute(
                """INSERT INTO artifacts
                   (artifact_id, revision, scope, kind, locator, content_sha256,
                    state, producing_run_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (artifact_id, revision, self.scope, kind, locator, _digest(content), run_id, _now()),
            )
            self._add_edge(
                conn, "run", run_id, None, "artifact", artifact_id, revision, "produced", run_id
            )
            for memory_id, memory_revision in parent_memories:
                self._require_safe_parent(conn, 'memory', memory_id, memory_revision)
                self._add_edge(
                    conn,
                    "memory",
                    memory_id,
                    memory_revision,
                    "artifact",
                    artifact_id,
                    revision,
                    "derived",
                    run_id,
                )
            for parent_id, parent_revision in parent_artifacts:
                self._require_safe_parent(conn, 'artifact', parent_id, parent_revision)
                self._add_edge(
                    conn,
                    "artifact",
                    parent_id,
                    parent_revision,
                    "artifact",
                    artifact_id,
                    revision,
                    "derived",
                    run_id,
                )
        return {
            "artifact_id": artifact_id,
            "revision": revision,
            "content_sha256": _digest(content),
            "state": "active",
        }

    def _artifact_use(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_text(arguments, "run_id")
        artifact_id = _required_text(arguments, "artifact_id")
        revision = self._revision_argument(arguments)
        with self._transaction() as conn:
            self._require_current_run(conn, run_id)
            artifact = self._get_artifact(conn, artifact_id, revision)
            if artifact["state"] != "active":
                raise MemoryStoreError("quarantined", "artifact requires review")
            edge_id = self._add_edge(
                conn,
                "artifact",
                artifact_id,
                revision,
                "run",
                run_id,
                None,
                "artifact_used",
                run_id,
            )
        return {"edge_id": edge_id}

    def _memory_derive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_text(arguments, "run_id")
        content = _required_text(arguments, "content")
        self._check_content(content)
        parent_memories = self._memory_refs(arguments.get("parent_memories", []))
        parent_artifacts = self._artifact_refs(arguments.get("parent_artifacts", []))
        source_ids = arguments.get("source_ids", [])
        if not isinstance(source_ids, list) or any(
            not isinstance(item, str) or not item.strip() for item in source_ids
        ):
            raise MemoryStoreError("invalid_input", "source_ids must be a list of strings")
        if not parent_memories and not parent_artifacts and not source_ids:
            raise MemoryStoreError("missing_provenance", "derived memory needs at least one parent")
        expires_at = _normalize_expiration(arguments.get("expires_at"))
        invalidation = _optional_text(arguments, "invalidation_condition")
        memory_id_arg = arguments.get("memory_id")
        supersedes = arguments.get("supersedes_revision")
        if supersedes is not None and (
            not isinstance(supersedes, int) or isinstance(supersedes, bool) or supersedes < 1
        ):
            raise MemoryStoreError("invalid_input", "supersedes_revision must be a positive integer")

        with self._transaction() as conn:
            self._require_current_run(conn, run_id)
            self._check_memory_capacity(conn)
            if memory_id_arg is None:
                memory_id = _new_id("mem")
                revision = 1
                if supersedes is not None:
                    raise MemoryStoreError("invalid_input", "new memory cannot supersede an unknown revision")
            else:
                if not isinstance(memory_id_arg, str) or not memory_id_arg.strip():
                    raise MemoryStoreError("invalid_input", "memory_id must be a non-empty string")
                memory_id = memory_id_arg
                last = conn.execute(
                    "SELECT MAX(revision) FROM memories WHERE memory_id = ? AND scope = ?",
                    (memory_id, self.scope),
                ).fetchone()[0]
                if last is None:
                    raise MemoryStoreError("not_found", "memory_id was not found in this scope")
                revision = int(last) + 1
                if supersedes is None:
                    supersedes = int(last)
                self._get_memory(conn, memory_id, supersedes)
                # Candidates have no authority to replace an approved revision.
            content_hash = _digest(content)
            conn.execute(
                """INSERT INTO memories
                   (memory_id, revision, scope, content, content_sha256, state,
                    expires_at, invalidation_condition, created_run_id,
                    supersedes_revision, created_at)
                   VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    revision,
                    self.scope,
                    content,
                    content_hash,
                    expires_at,
                    invalidation,
                    run_id,
                    supersedes,
                    _now(),
                ),
            )
            self._add_edge(
                conn, "run", run_id, None, "memory", memory_id, revision, "derived", run_id
            )
            for parent_id, parent_revision in parent_memories:
                self._require_safe_parent(conn, 'memory', parent_id, parent_revision)
                self._add_edge(
                    conn,
                    "memory",
                    parent_id,
                    parent_revision,
                    "memory",
                    memory_id,
                    revision,
                    "derived",
                    run_id,
                )
            for parent_id, parent_revision in parent_artifacts:
                self._require_safe_parent(conn, 'artifact', parent_id, parent_revision)
                self._add_edge(
                    conn,
                    "artifact",
                    parent_id,
                    parent_revision,
                    "memory",
                    memory_id,
                    revision,
                    "derived",
                    run_id,
                )
            for source_id in source_ids:
                source = conn.execute(
                    "SELECT 1 FROM sources WHERE source_id = ? AND scope = ?",
                    (source_id, self.scope),
                ).fetchone()
                if source is None:
                    raise MemoryStoreError("not_found", "source was not found in this scope")
                self._require_safe_source(conn, source_id)
                self._add_edge(
                    conn,
                    "source",
                    source_id,
                    None,
                    "memory",
                    memory_id,
                    revision,
                    "used",
                    run_id,
                )
            self._event(conn, "proposed", "memory", memory_id, revision=revision)
        return {
            "memory_id": memory_id,
            "revision": revision,
            "state": "candidate",
            "content_sha256": content_hash,
            "supersedes_revision": supersedes,
        }

    def _quarantine(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._contain(arguments, terminal=False)

    def _revoke(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        return self._contain(arguments, terminal=True)

    def _policy_revoke(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        policy = _required_text(arguments, 'policy_version')
        reason = _required_text(arguments, 'reason')
        incident_id = _new_id('incident')
        with self._transaction() as conn:
            self._start_incident(conn, incident_id, 'policy_revocation')
            self._event(conn, 'revoked', 'policy', policy, incident_id=incident_id, reason=reason)
            rows = conn.execute("SELECT memory_id, revision FROM memories WHERE scope = ? AND policy_version = ?", (self.scope, policy)).fetchall()
            for row in rows:
                causal, exposed = self._impact_paths(conn, row['memory_id'], row['revision'])
                self._apply_impacts(conn, incident_id, reason, causal, exposed, seed=None)
            conn.execute("UPDATE scopes SET frozen = 1, epoch = epoch + 1 WHERE scope = ?", (self.scope,))
            conn.execute("UPDATE incidents SET closure_complete = 1 WHERE incident_id = ?", (incident_id,))
            self._event(conn, 'closure_complete', 'incident', incident_id, incident_id=incident_id)
        return self._incident_report({'incident_id': incident_id})

    def _contain(self, arguments: dict[str, Any], *, terminal: bool) -> dict[str, Any]:
        memory_id = _required_text(arguments, "memory_id")
        revision = self._revision_argument(arguments)
        reason = _required_text(arguments, "reason")
        incident_id = arguments.get("incident_id") or _new_id("incident")
        if not isinstance(incident_id, str) or not incident_id.strip():
            raise MemoryStoreError("invalid_input", "incident_id must be a non-empty string")

        with self._transaction() as conn:
            memory = conn.execute('SELECT * FROM memories WHERE scope = ? AND memory_id = ? AND revision = ?', (self.scope, memory_id, revision)).fetchone()
            if memory is None:
                raise MemoryStoreError('not_found', 'memory revision was not found')
            incident = self._start_incident(conn, incident_id, "revocation" if terminal else "quarantine")
            same_seed = conn.execute("SELECT 1 FROM safety_events WHERE scope = ? AND incident_id = ? AND object_kind = 'memory' AND object_id = ? AND revision = ? AND event_type = ?", (self.scope, incident_id, memory_id, revision, 'revoked' if terminal else 'quarantined')).fetchone()
            if incident['closure_complete'] and same_seed:
                return {'incident_id': incident_id, 'memory_epoch': self._scope_row(conn)['epoch'], 'scope_frozen': True, 'idempotent': True}
            existing_terminal = memory["state"] == "revoked"
            if terminal and not existing_terminal:
                conn.execute(
                    "UPDATE memories SET state = 'revoked' WHERE memory_id = ? AND revision = ?",
                    (memory_id, revision),
                )
                self._event(
                    conn,
                    "revoked",
                    "memory",
                    memory_id,
                    revision=revision,
                    incident_id=incident_id,
                    reason=reason,
                    path=[self._node_dict(("memory", memory_id, revision))],
                )
            elif not terminal and memory["state"] not in {"quarantined", "revoked"}:
                conn.execute(
                    "UPDATE memories SET state = 'quarantined' WHERE memory_id = ? AND revision = ?",
                    (memory_id, revision),
                )
                self._event(
                    conn,
                    "quarantined",
                    "memory",
                    memory_id,
                    revision=revision,
                    incident_id=incident_id,
                    reason=reason,
                    path=[self._node_dict(("memory", memory_id, revision))],
                )

            causal_paths, exposed_paths = self._impact_paths(conn, memory_id, revision)
            self._apply_impacts(
                conn,
                incident_id,
                reason,
                causal_paths,
                exposed_paths,
                seed=("memory", memory_id, revision),
            )
            conn.execute(
                "UPDATE incidents SET closure_complete = 1 WHERE incident_id = ?", (incident_id,)
            )
            scope = self._scope_row(conn)
            new_epoch = scope["epoch"] + 1
            conn.execute(
                "UPDATE scopes SET epoch = ?, frozen = 1 WHERE scope = ?",
                (new_epoch, self.scope),
            )
            closure_event = self._event(
                conn,
                "closure_complete",
                "incident",
                incident_id,
                incident_id=incident_id,
                reason=reason,
            )
        report = self._incident_report({"scope": self.scope, "incident_id": incident_id})
        report.update(
            {
                "memory_epoch": new_epoch,
                "closure_event_seq": closure_event,
                "idempotent": bool(existing_terminal and terminal and incident["closure_complete"]),
            }
        )
        return report

    def _start_incident(
        self, conn: sqlite3.Connection, incident_id: str, kind: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if row:
            if row["scope"] != self.scope:
                raise MemoryStoreError("scope_mismatch", "incident belongs to another scope")
            if row["status"] != "open":
                raise MemoryStoreError("invalid_state", "incident is already released")
            return row
        conn.execute(
            """INSERT INTO incidents
               (incident_id, scope, kind, status, closure_complete, created_at)
               VALUES (?, ?, ?, 'open', 0, ?)""",
            (incident_id, self.scope, kind, _now()),
        )
        return conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()

    def _impact_paths(
        self, conn: sqlite3.Connection, memory_id: str, revision: int
    ) -> tuple[dict[tuple[str, str, int | None], list[tuple[str, str, int | None]]], dict]:
        edges = conn.execute("SELECT * FROM edges WHERE scope = ?", (self.scope,)).fetchall()
        adjacency: dict[tuple[str, str, int | None], list[tuple[str, str, int | None]]] = {}
        for edge in edges:
            if edge["relation"] not in CAUSAL_RELATIONS:
                continue
            parent = (edge["parent_kind"], edge["parent_id"], edge["parent_revision"])
            child = (edge["child_kind"], edge["child_id"], edge["child_revision"])
            adjacency.setdefault(parent, []).append(child)

        seed = ("memory", memory_id, revision)
        causal_starts = [seed]
        exposure_starts: list[tuple[str, str, int | None]] = []
        source_ids = {
            row["source_id"]
            for row in conn.execute(
                """SELECT source_id FROM memories
                   WHERE memory_id = ? AND revision = ? AND scope = ? AND source_id IS NOT NULL""",
                (memory_id, revision, self.scope),
            )
        }
        source_ids.update(
            edge["parent_id"]
            for edge in edges
            if edge["parent_kind"] == "source"
            and edge["child_kind"] == "memory"
            and edge["child_id"] == memory_id
            and edge["child_revision"] == revision
        )
        for edge in edges:
            if edge["parent_kind"] != "source" or edge["parent_id"] not in source_ids:
                continue
            child = (edge["child_kind"], edge["child_id"], edge["child_revision"])
            if edge["relation"] == "used":
                causal_starts.append(child)
            elif edge["relation"] == "observed" and child[0] == "run":
                exposure_starts.append(child)

        causal = self._walk(adjacency, causal_starts)
        exposed = self._walk(adjacency, exposure_starts)
        for node in list(exposed):
            if node in causal:
                del exposed[node]
        return causal, exposed

    @staticmethod
    def _walk(adjacency, starts):
        paths = {node: [node] for node in starts}
        queue = deque(starts)
        while queue:
            current = queue.popleft()
            for child in adjacency.get(current, []):
                if child in paths:
                    continue
                paths[child] = paths[current] + [child]
                queue.append(child)
        return paths

    @staticmethod
    def _node_dict(node: tuple[str, str, int | None]) -> dict[str, Any]:
        return {"kind": node[0], "id": node[1], "revision": node[2]}

    def _apply_impacts(
        self,
        conn: sqlite3.Connection,
        incident_id: str,
        reason: str,
        causal_paths,
        exposed_paths,
        *,
        seed,
    ) -> None:
        for classification, paths in (("causal", causal_paths), ("exposed", exposed_paths)):
            for node, path in paths.items():
                if node == seed:
                    continue
                kind, object_id, revision = node
                if kind == "memory":
                    current = conn.execute(
                        "SELECT state FROM memories WHERE memory_id = ? AND revision = ? AND scope = ?",
                        (object_id, revision, self.scope),
                    ).fetchone()
                    if current and current["state"] not in {"revoked", "quarantined"}:
                        conn.execute(
                            "UPDATE memories SET state = 'quarantined' WHERE memory_id = ? AND revision = ?",
                            (object_id, revision),
                        )
                        self._event(conn, 'quarantined', 'memory', object_id, revision=revision, incident_id=incident_id, reason=reason)
                elif kind == "artifact":
                    conn.execute(
                        "UPDATE artifacts SET state = 'needs_review' WHERE artifact_id = ? AND revision = ? AND scope = ?",
                        (object_id, revision, self.scope),
                    )
                elif kind == "run":
                    conn.execute(
                        "UPDATE runs SET status = 'impacted' WHERE run_id = ? AND scope = ?",
                        (object_id, self.scope),
                    )
                elif kind == "effect":
                    conn.execute(
                        "UPDATE effects SET impacted = 1 WHERE effect_id = ? AND scope = ?",
                        (object_id, self.scope),
                    )
                else:
                    continue
                self._event(
                    conn,
                    f"{classification}_impact",
                    kind,
                    object_id,
                    revision=revision,
                    incident_id=incident_id,
                    reason=reason,
                    path=[self._node_dict(item) for item in path],
                )

    def _incident_report(self, arguments: dict[str, Any]) -> dict[str, Any]:
        incident_id = _required_text(arguments, "incident_id")
        with self._transaction() as conn:
            incident = conn.execute(
                "SELECT * FROM incidents WHERE incident_id = ? AND scope = ?",
                (incident_id, self.scope),
            ).fetchone()
            if incident is None:
                raise MemoryStoreError("not_found", "incident was not found in this scope")
            rows = conn.execute(
                """SELECT seq, event_type, object_kind, object_id, revision, reason, path_json, created_at
                   FROM safety_events WHERE scope = ? AND incident_id = ? ORDER BY seq""",
                (self.scope, incident_id),
            ).fetchall()
            events = []
            causal = []
            exposed = []
            for row in rows:
                item = {
                    "seq": row["seq"],
                    "event_type": row["event_type"],
                    "object": {
                        "kind": row["object_kind"],
                        "id": row["object_id"],
                        "revision": row["revision"],
                    },
                    "reason": row["reason"],
                    "path": json.loads(row["path_json"]) if row["path_json"] else None,
                    "created_at": row["created_at"],
                }
                events.append(item)
                if row["event_type"] == "causal_impact":
                    causal.append(item)
                elif row["event_type"] == "exposed_impact":
                    exposed.append(item)
            scope = self._scope_row(conn)
            effects = [dict(row) for row in conn.execute("SELECT effect_id, tool, target, status, external_reference, compensation_kind, compensates_effect_id FROM effects WHERE scope = ? AND effect_id IN (SELECT object_id FROM safety_events WHERE scope = ? AND incident_id = ? AND object_kind = 'effect')", (self.scope, self.scope, incident_id))]
        # Deliberately no source, memory, or artifact content in this report.
        return {
            "incident_id": incident_id,
            "status": incident["status"],
            "closure_complete": bool(incident["closure_complete"]),
            "scope_frozen": bool(scope["frozen"]),
            "memory_epoch": scope["epoch"],
            "causal": causal,
            "exposed": exposed,
            "events": events,
            "effects": effects,
            "unknown": "operations that bypassed this gateway are not observable",
        }

    def _snapshot(self, _: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        with self._transaction() as conn:
            scope = self._scope_row(conn)
            memories = [
                dict(row)
                for row in conn.execute(
                    """SELECT memory_id, revision, scope, source_id, content,
                              content_sha256, state, expires_at, invalidation_condition,
                              policy_version, created_run_id, supersedes_revision, created_at
                       FROM memories
                       WHERE scope = ? AND state IN ('active', 'superseded')
                       ORDER BY memory_id, revision""",
                    (self.scope,),
                )
            ]
            for memory in memories:
                if _digest(memory["content"]) != memory["content_sha256"]:
                    raise MemoryStoreError("storage_integrity", "memory content hash mismatch")
            source_ids = sorted({m["source_id"] for m in memories if m["source_id"]})
            sources = []
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                sources = [
                    dict(row)
                    for row in conn.execute(
                        f"""SELECT source_id, scope, kind, locator, source_version,
                                   raw_sha256, created_at
                            FROM sources WHERE scope = ? AND source_id IN ({placeholders})
                            ORDER BY source_id""",
                        (self.scope, *source_ids),
                    )
                ]
            allowed_memories = {(m["memory_id"], m["revision"]) for m in memories}
            edges = []
            for row in conn.execute("SELECT * FROM edges WHERE scope = ?", (self.scope,)):
                parent_ok = row["parent_kind"] == "source" and row["parent_id"] in source_ids
                parent_ok = parent_ok or (
                    row["parent_kind"] == "memory"
                    and (row["parent_id"], row["parent_revision"]) in allowed_memories
                )
                child_ok = row["child_kind"] == "memory" and (
                    row["child_id"], row["child_revision"]
                ) in allowed_memories
                if parent_ok and child_ok:
                    edges.append(
                        {
                            key: row[key]
                            for key in (
                                "edge_id",
                                "scope",
                                "parent_kind",
                                "parent_id",
                                "parent_revision",
                                "child_kind",
                                "child_id",
                                "child_revision",
                                "relation",
                                "run_id",
                                "created_at",
                            )
                        }
                    )
            payload = {"sources": sources, "memories": memories, "edges": edges}
            event_seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM safety_events WHERE scope = ?", (self.scope,)
            ).fetchone()[0]
            policy_versions = sorted(
                {memory["policy_version"] for memory in memories if memory["policy_version"]}
            )
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "store_id": self.store_id,
                "snapshot_id": _new_id('snapshot'),
                "scope": self.scope,
                "event_seq": event_seq,
                "memory_epoch": scope["epoch"],
                "policy_versions": policy_versions,
                "created_at": _now(),
                "payload_sha256": _digest(_canonical(payload)),
            }
            conn.execute("INSERT INTO snapshots VALUES (?, ?, ?)", (manifest['snapshot_id'], self.scope, _digest(_canonical({'manifest': manifest, 'payload': payload}))))
        return {"manifest": manifest, "payload": payload}

    def _restore_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        snapshot = arguments.get("snapshot")
        payload, manifest = self._validate_snapshot(snapshot)
        with self._transaction() as conn:
            scope = self._scope_row(conn)
            current_seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM safety_events WHERE scope = ?", (self.scope,)
            ).fetchone()[0]
            revoked = {
                (row["object_id"], row["revision"])
                for row in conn.execute(
                    """SELECT object_id, revision FROM safety_events
                       WHERE scope = ? AND event_type = 'revoked' AND object_kind = 'memory'""",
                    (self.scope,),
                )
            }
            revoked_policies = {
                row["object_id"]
                for row in conn.execute(
                    """SELECT object_id FROM safety_events
                       WHERE scope = ? AND event_type = 'revoked' AND object_kind = 'policy'""",
                    (self.scope,),
                )
            }
            added = []
            skipped_revoked = []
            conflicts = []
            for memory in payload["memories"]:
                key = (memory["memory_id"], memory["revision"])
                if key in revoked or memory.get("policy_version") in revoked_policies:
                    skipped_revoked.append({"memory_id": key[0], "revision": key[1]})
                    continue
                existing = conn.execute(
                    """SELECT content, content_sha256 FROM memories
                       WHERE scope = ? AND memory_id = ? AND revision = ?""",
                    (self.scope, key[0], key[1]),
                ).fetchone()
                if existing is None:
                    added.append({"memory_id": key[0], "revision": key[1]})
                elif existing["content_sha256"] != memory["content_sha256"] or _digest(existing['content']) != existing['content_sha256']:
                    conflicts.append({"memory_id": key[0], "revision": key[1]})
            if len(added) + conn.execute(
                "SELECT COUNT(*) FROM memories WHERE scope = ?", (self.scope,)
            ).fetchone()[0] > self.max_memories:
                raise MemoryStoreError("capacity_exceeded", "snapshot would exceed memory capacity")
            diff = {
                "add": added,
                "retire": [dict(row) for row in conn.execute("SELECT memory_id, revision FROM memories WHERE scope = ? AND state IN ('active', 'candidate')", (self.scope,)) if (row['memory_id'], row['revision']) not in {(m['memory_id'], m['revision']) for m in payload['memories']}],
                "skip_revoked": skipped_revoked,
                "conflicts": conflicts,
                "snapshot_event_seq": manifest["event_seq"],
                "current_event_seq": current_seq,
            }
            plan_id = _new_id("restore")
            snapshot_json = _canonical(snapshot)
            conn.execute(
                """INSERT INTO restore_plans
                   (plan_id, scope, base_event_seq, base_epoch, snapshot_json,
                    snapshot_sha256, diff_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?)""",
                (
                    plan_id,
                    self.scope,
                    current_seq,
                    scope["epoch"],
                    snapshot_json,
                    _digest(snapshot_json),
                    _canonical(diff),
                    _now(),
                ),
            )
        return {"plan_id": plan_id, "diff": diff, "base_epoch": scope["epoch"]}

    def _restore(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        plan_id = _required_text(arguments, "plan_id")
        with self._transaction() as conn:
            plan = conn.execute(
                "SELECT * FROM restore_plans WHERE plan_id = ? AND scope = ?",
                (plan_id, self.scope),
            ).fetchone()
            if plan is None:
                raise MemoryStoreError("not_found", "restore plan was not found in this scope")
            if plan["status"] == "applied":
                result = json.loads(plan["result_json"])
                result["idempotent"] = True
                return result
            if _digest(plan["snapshot_json"]) != plan["snapshot_sha256"]:
                raise MemoryStoreError("storage_integrity", "stored restore plan hash mismatch")
            scope = self._scope_row(conn)
            current_seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM safety_events WHERE scope = ?", (self.scope,)
            ).fetchone()[0]
            if current_seq != plan["base_event_seq"] or scope["epoch"] != plan["base_epoch"]:
                raise MemoryStoreError("restore_conflict", "scope changed after restore plan was created")
            snapshot = json.loads(plan["snapshot_json"])
            payload, _manifest = self._validate_snapshot(snapshot)
            diff = json.loads(plan["diff_json"])
            if diff["conflicts"]:
                raise MemoryStoreError("restore_conflict", "snapshot has immutable revision conflicts")

            incident_id = _new_id("incident")
            self._start_incident(conn, incident_id, "restore")
            new_epoch = scope["epoch"] + 1
            conn.execute(
                "UPDATE scopes SET frozen = 1, epoch = ? WHERE scope = ?",
                (new_epoch, self.scope),
            )
            self._event(
                conn,
                "restore_started",
                "restore_plan",
                plan_id,
                incident_id=incident_id,
            )

            revoked = {
                (row["object_id"], row["revision"])
                for row in conn.execute(
                    """SELECT object_id, revision FROM safety_events
                       WHERE scope = ? AND event_type = 'revoked' AND object_kind = 'memory'""",
                    (self.scope,),
                )
            }
            for item in diff['retire']:
                conn.execute("UPDATE memories SET state = 'superseded' WHERE scope = ? AND memory_id = ? AND revision = ? AND state IN ('active', 'candidate')", (self.scope, item['memory_id'], item['revision']))
            quarantined = {
                (row["object_id"], row["revision"])
                for row in conn.execute(
                    """SELECT object_id, revision FROM safety_events e
                       WHERE e.scope = ? AND e.event_type = 'quarantined'
                         AND e.object_kind = 'memory'
                         AND NOT EXISTS (
                             SELECT 1 FROM safety_events r
                             WHERE r.scope = e.scope AND r.object_kind = e.object_kind
                               AND r.object_id = e.object_id AND r.revision = e.revision
                               AND r.event_type = 'revalidated' AND r.seq > e.seq
                         )""",
                    (self.scope,),
                )
            }
            revoked_policies = {
                row["object_id"]
                for row in conn.execute(
                    """SELECT object_id FROM safety_events
                       WHERE scope = ? AND event_type = 'revoked' AND object_kind = 'policy'""",
                    (self.scope,),
                )
            }
            for source in payload["sources"]:
                existing = conn.execute(
                    "SELECT * FROM sources WHERE source_id = ?", (source["source_id"],)
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """INSERT INTO sources
                           (source_id, scope, kind, locator, source_version, raw_sha256, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            source["source_id"],
                            self.scope,
                            source["kind"],
                            source["locator"],
                            source.get("source_version"),
                            source["raw_sha256"],
                            source["created_at"],
                        ),
                    )
                elif existing["scope"] != self.scope or existing["raw_sha256"] != source["raw_sha256"]:
                    raise MemoryStoreError("restore_conflict", "source identity conflicts with live data")

            restored = []
            skipped = []
            for memory in payload["memories"]:
                key = (memory["memory_id"], memory["revision"])
                if key in revoked or memory.get("policy_version") in revoked_policies:
                    skipped.append({"memory_id": key[0], "revision": key[1], "reason": "revoked"})
                    continue
                existing = conn.execute(
                    "SELECT * FROM memories WHERE memory_id = ? AND revision = ?",
                    key,
                ).fetchone()
                if existing:
                    if existing['scope'] != self.scope or existing["content_sha256"] != memory["content_sha256"] or _digest(existing['content']) != existing['content_sha256']:
                        raise MemoryStoreError("restore_conflict", "immutable memory revision conflicts")
                    if existing['state'] not in ('revoked', 'quarantined') and key not in quarantined:
                        conn.execute("UPDATE memories SET state = ? WHERE scope = ? AND memory_id = ? AND revision = ?", (memory['state'], self.scope, *key))
                    continue
                state = "quarantined" if key in quarantined else memory["state"]
                conn.execute(
                    """INSERT INTO memories
                       (memory_id, revision, scope, source_id, content, content_sha256,
                        state, expires_at, invalidation_condition, policy_version,
                        created_run_id, supersedes_revision, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        memory["memory_id"],
                        memory["revision"],
                        self.scope,
                        memory.get("source_id"),
                        memory["content"],
                        memory["content_sha256"],
                        state,
                        memory.get("expires_at"),
                        memory.get("invalidation_condition"),
                        memory.get("policy_version"),
                        memory.get('created_run_id'),
                        memory.get("supersedes_revision"),
                        memory["created_at"],
                    ),
                )
                restored.append({"memory_id": key[0], "revision": key[1], "state": state})

            for edge in payload["edges"]:
                self._add_edge(
                    conn,
                    edge["parent_kind"],
                    edge["parent_id"],
                    edge.get("parent_revision"),
                    edge["child_kind"],
                    edge["child_id"],
                    edge.get("child_revision"),
                    edge["relation"],
                    None,
                )
            conn.execute(
                "UPDATE incidents SET closure_complete = 1 WHERE incident_id = ?", (incident_id,)
            )
            event_seq = self._event(
                conn,
                "restore_complete",
                "restore_plan",
                plan_id,
                incident_id=incident_id,
            )
            result = {
                "plan_id": plan_id,
                "incident_id": incident_id,
                "memory_epoch": new_epoch,
                "event_seq": event_seq,
                "restored": restored,
                "skipped": skipped,
                "scope_frozen": True,
                "index_rebuild_required": False,
                "index_strategy": "live_sql",
                "idempotent": False,
            }
            conn.execute(
                "UPDATE restore_plans SET status = 'applied', result_json = ? WHERE plan_id = ?",
                (_canonical(result), plan_id),
            )
        return result

    def _effect_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_text(arguments, "run_id")
        tool = _required_text(arguments, "tool")
        target = _required_text(arguments, "target")
        idempotency_key = _required_text(arguments, "idempotency_key")
        compensation_kind = _optional_text(arguments, "compensation_kind")
        compensates = _optional_text(arguments, "compensates_effect_id")
        with self._transaction() as conn:
            self._require_current_run(conn, run_id)
            existing = conn.execute(
                "SELECT * FROM effects WHERE scope = ? AND idempotency_key = ?",
                (self.scope, idempotency_key),
            ).fetchone()
            if existing:
                if (existing["run_id"], existing["tool"], existing["target"], existing['compensation_kind'], existing['compensates_effect_id']) != (run_id, tool, target, compensation_kind, compensates):
                    raise MemoryStoreError("idempotency_conflict", "idempotency key has other arguments")
                return {
                    "effect_id": existing["effect_id"],
                    "status": existing["status"],
                    "idempotent": True,
                    "executed": False,
                }
            if compensates:
                original = conn.execute(
                    "SELECT 1 FROM effects WHERE effect_id = ? AND scope = ?",
                    (compensates, self.scope),
                ).fetchone()
                if original is None:
                    raise MemoryStoreError("not_found", "compensated effect was not found")
            effect_id = _new_id("effect")
            created_at = _now()
            conn.execute(
                """INSERT INTO effects
                   (effect_id, scope, run_id, tool, target, idempotency_key, status,
                    compensation_kind, compensates_effect_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?)""",
                (
                    effect_id,
                    self.scope,
                    run_id,
                    tool,
                    target,
                    idempotency_key,
                    compensation_kind,
                    compensates,
                    created_at,
                    created_at,
                ),
            )
            self._add_edge(
                conn, "run", run_id, None, "effect", effect_id, None, "effect_planned", run_id
            )
            if compensates:
                self._add_edge(
                    conn,
                    "effect",
                    compensates,
                    None,
                    "effect",
                    effect_id,
                    None,
                    "compensates",
                    run_id,
                )
        return {"effect_id": effect_id, "status": "planned", "executed": False, "idempotent": False}

    def _effect_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        effect_id = _required_text(arguments, "effect_id")
        status = _required_text(arguments, "status")
        if status not in VALID_EFFECT_STATES:
            raise MemoryStoreError("invalid_input", "unsupported effect status")
        external_reference = _optional_text(arguments, "external_reference")
        result_sha256 = _optional_text(arguments, "result_sha256")
        compensates = _optional_text(arguments, "compensates_effect_id")
        if result_sha256 and not re.fullmatch(r'[0-9a-f]{64}', result_sha256):
            raise MemoryStoreError('invalid_input', 'result_sha256 must be a SHA-256 hex digest')
        if status == 'compensated':
            self._operator_only()
            _required_text(arguments, 'evidence')
        with self._transaction() as conn:
            effect = conn.execute(
                "SELECT * FROM effects WHERE effect_id = ? AND scope = ?",
                (effect_id, self.scope),
            ).fetchone()
            if effect is None:
                raise MemoryStoreError("not_found", "effect was not found in this scope")
            if effect["status"] == status:
                if (external_reference and effect['external_reference'] != external_reference) or (result_sha256 and effect['result_sha256'] != result_sha256):
                    raise MemoryStoreError('idempotency_conflict', 'recorded effect has different evidence')
                return {"effect_id": effect_id, "status": status, "idempotent": True}
            allowed = {
                "planned": VALID_EFFECT_STATES - {'compensated'},
                "uncertain": {"applied", "failed", "manual_required", "compensated"},
                "applied": {'manual_required', 'compensated'},
                "manual_required": {'applied', 'failed', 'compensated'},
            }
            if status not in allowed.get(effect["status"], set()):
                raise MemoryStoreError("invalid_state", "effect status transition is not allowed")
            if status == 'compensated':
                compensation_id = _required_text(arguments, 'compensation_effect_id')
                verified = conn.execute("SELECT 1 FROM effects WHERE scope = ? AND effect_id = ? AND compensates_effect_id = ? AND status = 'applied'", (self.scope, compensation_id, effect_id)).fetchone()
                if not verified:
                    raise MemoryStoreError('unverified_compensation', 'an applied compensation effect is required')
            if compensates:
                if compensates != effect['compensates_effect_id']:
                    raise MemoryStoreError('conflict', 'compensation relation is fixed when planned')
                original = conn.execute(
                    "SELECT 1 FROM effects WHERE effect_id = ? AND scope = ?",
                    (compensates, self.scope),
                ).fetchone()
                if original is None:
                    raise MemoryStoreError("not_found", "compensated effect was not found")
                self._add_edge(
                    conn,
                    "effect",
                    compensates,
                    None,
                    "effect",
                    effect_id,
                    None,
                    "compensates",
                    effect["run_id"],
                )
            conn.execute(
                """UPDATE effects SET status = ?, external_reference = COALESCE(?, external_reference),
                   result_sha256 = COALESCE(?, result_sha256),
                   compensates_effect_id = COALESCE(?, compensates_effect_id), updated_at = ?
                   WHERE effect_id = ?""",
                (status, external_reference, result_sha256, compensates, _now(), effect_id),
            )
            self._event(conn, 'effect_recorded', 'effect', effect_id, reason=status, evidence=arguments.get('evidence'))
        return {"effect_id": effect_id, "status": status, "idempotent": False, "executed": False}

    def _scope_release(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._operator_only()
        incident_id = _required_text(arguments, "incident_id")
        evidence = _required_text(arguments, "evidence")
        with self._transaction() as conn:
            incident = conn.execute(
                "SELECT * FROM incidents WHERE incident_id = ? AND scope = ?",
                (incident_id, self.scope),
            ).fetchone()
            if incident is None:
                raise MemoryStoreError("not_found", "incident was not found in this scope")
            if not incident["closure_complete"]:
                raise MemoryStoreError("closure_incomplete", "incident closure is not complete")
            if incident["status"] == "released":
                scope = self._scope_row(conn)
                return {
                    "incident_id": incident_id,
                    "scope_frozen": bool(scope["frozen"]),
                    "memory_epoch": scope["epoch"],
                    "idempotent": True,
                }
            conn.execute(
                """UPDATE incidents SET status = 'released', closed_at = ?, release_evidence = ?
                   WHERE incident_id = ?""",
                (_now(), evidence, incident_id),
            )
            open_count = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE scope = ? AND status = 'open'", (self.scope,)
            ).fetchone()[0]
            scope = self._scope_row(conn)
            new_epoch = scope["epoch"] + 1
            conn.execute(
                "UPDATE scopes SET frozen = ?, epoch = ? WHERE scope = ?",
                (1 if open_count else 0, new_epoch, self.scope),
            )
            self._event(
                conn,
                "scope_released",
                "incident",
                incident_id,
                incident_id=incident_id,
                evidence=evidence,
            )
        return {
            "incident_id": incident_id,
            "scope_frozen": bool(open_count),
            "memory_epoch": new_epoch,
            "idempotent": False,
        }

    def _require_safe_source(self, conn, source_id):
        unsafe = conn.execute("SELECT 1 FROM memories m WHERE m.scope = ? AND m.state IN ('revoked', 'quarantined') AND (m.source_id = ? OR EXISTS (SELECT 1 FROM edges e WHERE e.scope = m.scope AND e.parent_kind = 'source' AND e.parent_id = ? AND e.child_kind = 'memory' AND e.child_id = m.memory_id AND e.child_revision = m.revision))", (self.scope, source_id, source_id)).fetchone()
        if unsafe:
            raise MemoryStoreError('unsafe_provenance', 'source is linked to contained memory')

    def _require_safe_parent(self, conn, kind, object_id, revision):
        row = self._get_memory(conn, object_id, revision) if kind == 'memory' else self._get_artifact(conn, object_id, revision)
        allowed = ('active', 'superseded') if kind == 'memory' else ('active',)
        if row['state'] not in allowed:
            raise MemoryStoreError('unsafe_provenance', 'parent is not eligible for use')
        if kind == 'memory' and row['expires_at'] and row['expires_at'] <= _now():
            raise MemoryStoreError('expired', 'parent memory has expired')
        return row

    def _get_artifact(
        self, conn: sqlite3.Connection, artifact_id: str, revision: int
    ) -> sqlite3.Row:
        row = conn.execute(
            """SELECT * FROM artifacts
               WHERE scope = ? AND artifact_id = ? AND revision = ?""",
            (self.scope, artifact_id, revision),
        ).fetchone()
        if row is None:
            raise MemoryStoreError("not_found", "artifact revision was not found in this scope")
        return row

    @staticmethod
    def _revision_argument(arguments: dict[str, Any]) -> int:
        value = arguments.get("revision")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise MemoryStoreError("invalid_input", "revision must be a positive integer")
        return value

    @staticmethod
    def _refs(value: Any, name: str) -> list[tuple[str, int]]:
        if not isinstance(value, list):
            raise MemoryStoreError("invalid_input", f"{name} must be a list")
        refs = []
        for item in value:
            if not isinstance(item, dict):
                raise MemoryStoreError("invalid_input", f"{name} entries must be objects")
            object_id = item.get("id")
            revision = item.get("revision")
            if not isinstance(object_id, str) or not object_id.strip():
                raise MemoryStoreError("invalid_input", f"{name} id must be a string")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise MemoryStoreError("invalid_input", f"{name} revision must be positive")
            refs.append((object_id, revision))
        return refs

    def _memory_refs(self, value: Any) -> list[tuple[str, int]]:
        return self._refs(value, "parent_memories")

    def _artifact_refs(self, value: Any) -> list[tuple[str, int]]:
        return self._refs(value, "parent_artifacts")

    def _validate_snapshot(self, snapshot: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(snapshot, dict):
            raise MemoryStoreError("invalid_snapshot", "snapshot must be an object")
        manifest = snapshot.get("manifest")
        payload = snapshot.get("payload")
        if not isinstance(manifest, dict) or not isinstance(payload, dict):
            raise MemoryStoreError("invalid_snapshot", "snapshot needs manifest and payload objects")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise MemoryStoreError("unsupported_schema", "snapshot schema version is not supported")
        if manifest.get("scope") != self.scope:
            raise MemoryStoreError("scope_mismatch", "snapshot belongs to another scope")
        if manifest.get('store_id') != self.store_id:
            raise MemoryStoreError('foreign_snapshot', 'snapshot requires its original store and safety journal')
        snapshot_id = manifest.get('snapshot_id')
        if not isinstance(snapshot_id, str):
            raise MemoryStoreError('invalid_snapshot', 'snapshot_id must be a string')
        with self._lock:
            registered = self._conn.execute('SELECT snapshot_sha256 FROM snapshots WHERE snapshot_id = ? AND scope = ?', (snapshot_id, self.scope)).fetchone()
        if not registered or registered[0] != _digest(_canonical(snapshot)):
            raise MemoryStoreError('snapshot_corrupt', 'snapshot does not match the registered checkpoint')
        if set(payload) != {"sources", "memories", "edges"} or any(
            not isinstance(payload[name], list) for name in payload
        ):
            raise MemoryStoreError("invalid_snapshot", "snapshot payload shape is invalid")
        if _digest(_canonical(payload)) != manifest.get("payload_sha256"):
            raise MemoryStoreError("snapshot_corrupt", "snapshot payload hash mismatch")

        seen = set()
        source_ids = set()
        for source in payload["sources"]:
            if not isinstance(source, dict) or source.get("scope") != self.scope:
                raise MemoryStoreError("invalid_snapshot", "snapshot source is invalid")
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or source_id in source_ids:
                raise MemoryStoreError("invalid_snapshot", "snapshot source identity is invalid")
            source_ids.add(source_id)
        for memory in payload["memories"]:
            if not isinstance(memory, dict) or memory.get("scope") != self.scope:
                raise MemoryStoreError("invalid_snapshot", "snapshot memory is invalid")
            key = (memory.get("memory_id"), memory.get("revision"))
            if not isinstance(key[0], str) or not isinstance(key[1], int) or key in seen:
                raise MemoryStoreError("invalid_snapshot", "snapshot memory identity is invalid")
            seen.add(key)
            content = memory.get("content")
            if not isinstance(content, str) or _digest(content) != memory.get("content_sha256"):
                raise MemoryStoreError("snapshot_corrupt", "snapshot memory hash mismatch")
            if memory.get("state") not in {"active", "superseded"}:
                raise MemoryStoreError("invalid_snapshot", "snapshot contains an ineligible memory state")
            if memory.get("source_id") is not None and memory["source_id"] not in source_ids:
                raise MemoryStoreError("invalid_snapshot", "snapshot memory source is missing")
        if any(not isinstance(edge, dict) or edge.get("scope") != self.scope for edge in payload["edges"]):
            raise MemoryStoreError("invalid_snapshot", "snapshot edge is invalid")
        return payload, manifest
