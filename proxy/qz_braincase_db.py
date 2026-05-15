#!/usr/bin/env python3
"""Optional SQLite state/memory storage substrate for QuantZhai experiments.

BrainCaseDB is the low-level storage case — not a policy layer.

Slice 1 added DB availability and schema metadata plumbing.
Slice 2 adds explicit StateRecord and SourceRef storage for Slice A fixture-shaped records.

Hard rules:
- BrainCaseDB does not auto-ingest observed runtime/request data.
- Callers must use an explicit memory/state write path.
- memory_domain is the configured value supplied by the caller; BrainCaseDB stores
  it as-is and must not infer, create, normalize, or grant domain values.
- BrainCaseDB is not a telemetry warehouse, request log, or memory_domain registry.

Forbidden fields (rejected by put_* methods if present in input dicts):
  raw_prompt, raw_request_body, request_body, full_log, telemetry_event, stream_event
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

QZ_BRAINCASE_DB_SCHEMA_VERSION = 2
QZ_STATE_DB_ENABLED_ENV = "QZ_STATE_DB_ENABLED"
QZ_STATE_DB_PATH_ENV = "QZ_STATE_DB_PATH"

_FORBIDDEN_RECORD_FIELDS = frozenset({
    "raw_prompt",
    "raw_request_body",
    "request_body",
    "full_log",
    "telemetry_event",
    "stream_event",
})


def default_db_path(root: str | Path | None = None) -> Path:
    root = Path(
        root if root is not None else os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1])
    )
    return root / "var" / "qz-state.sqlite3"


def _env_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


class BrainCaseDB:
    """Optional non-fatal wrapper around the SQLite state/memory storage substrate.

    All public methods return False/None/[] rather than raising on error.
    Failures are recorded in last_error.
    """

    def __init__(self, path: str | Path | None = None, enabled: bool = False):
        self.path = Path(path) if path is not None else default_db_path()
        self.enabled = bool(enabled)
        self.available = False
        self.last_error: str | None = None
        self._conn: sqlite3.Connection | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "BrainCaseDB":
        source = os.environ if env is None else env
        path = source.get(QZ_STATE_DB_PATH_ENV)
        enabled = _env_enabled(source.get(QZ_STATE_DB_ENABLED_ENV))
        return cls(path=path or default_db_path(source.get("QZ_ROOT")), enabled=enabled)

    def init(self) -> bool:
        """Open and initialize the DB if enabled.

        Returns availability. All failures are captured in last_error and never
        raised to callers because this substrate is optional enrichment.
        """
        if not self.enabled:
            self.available = False
            self.last_error = None
            return False

        try:
            if self._conn is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(
                    str(self.path),
                    timeout=1.0,
                    check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                self._conn = conn
            self._initialize_schema()
            self.available = True
            self.last_error = None
            return True
        except Exception as exc:
            self.available = False
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._close_connection()
            return False

    def close(self) -> None:
        self._close_connection()
        self.available = False

    def health(self) -> dict[str, Any]:
        schema_version: int | None = None
        if self.enabled and self._conn is not None:
            try:
                schema_version = self._read_user_version()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
        return {
            "enabled": self.enabled,
            "path": str(self.path),
            "available": self.available,
            "schema_version": schema_version,
            "last_error": self.last_error,
        }

    def execute_write(self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> bool:
        """Run a write statement without letting sqlite failures escape."""
        if not self.enabled:
            return False
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return False

        try:
            assert self._conn is not None
            if params is None:
                self._conn.execute(sql)
            else:
                self._conn.execute(sql, params)
            self._conn.commit()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    write = execute_write

    # ------------------------------------------------------------------
    # Slice B: SourceRef storage
    # ------------------------------------------------------------------

    def put_source_ref(self, source_ref: dict) -> bool:
        """Store a SourceRef. Returns False on disabled DB or error.

        Does not mutate the input dict. Forbidden fields cause rejection.
        memory_domain is not applicable to SourceRefs; no domain check here.
        """
        if not self.enabled:
            return False
        forbidden = _FORBIDDEN_RECORD_FIELDS & source_ref.keys()
        if forbidden:
            self.last_error = f"Forbidden fields in source_ref: {sorted(forbidden)}"
            return False
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return False
        try:
            assert self._conn is not None
            meta = source_ref.get("metadata")
            self._conn.execute(
                """
                INSERT OR REPLACE INTO qz_braincase_source_refs(
                    source_ref_id, source_type, title, summary, locator,
                    content_hash, captured_at_ms, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_ref["source_ref_id"],
                    source_ref["source_type"],
                    source_ref.get("title"),
                    source_ref["summary"],
                    source_ref["locator"],
                    source_ref.get("content_hash"),
                    source_ref.get("captured_at_ms"),
                    json.dumps(meta) if meta is not None else None,
                ),
            )
            self._conn.commit()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def get_source_ref(self, source_ref_id: str) -> dict | None:
        """Fetch a SourceRef by ID. Returns None on disabled DB, not found, or error."""
        if not self.enabled:
            return None
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return None
        try:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM qz_braincase_source_refs WHERE source_ref_id = ?",
                (source_ref_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_source_ref(row)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    # ------------------------------------------------------------------
    # Slice B: StateRecord storage
    # ------------------------------------------------------------------

    def put_state_record(self, record: dict) -> bool:
        """Store a StateRecord. Returns False on disabled DB, forbidden fields, or error.

        Does not mutate the input dict.
        memory_domain is stored exactly as supplied — never inferred or normalized.
        source_refs entries are stored in qz_braincase_record_sources.
        """
        if not self.enabled:
            return False
        forbidden = _FORBIDDEN_RECORD_FIELDS & record.keys()
        if forbidden:
            self.last_error = f"Forbidden fields in state record: {sorted(forbidden)}"
            return False
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return False
        try:
            assert self._conn is not None
            record_id = record["record_id"]
            source_refs = list(record.get("source_refs") or [])
            tags = record.get("tags") or []
            meta = record.get("metadata")

            self._conn.execute(
                """
                INSERT OR REPLACE INTO qz_braincase_state_records(
                    record_id, record_schema, memory_domain, tier, record_type,
                    claim, summary, status, visibility, confidence, importance,
                    retention, created_at_ms, updated_at_ms, tags_json,
                    supersedes, superseded_by, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    record["schema"],
                    record["memory_domain"],
                    record["tier"],
                    record["record_type"],
                    record["claim"],
                    record["summary"],
                    record["status"],
                    record["visibility"],
                    float(record["confidence"]),
                    float(record["importance"]),
                    record["retention"],
                    int(record["created_at_ms"]),
                    int(record["updated_at_ms"]),
                    json.dumps(tags),
                    record.get("supersedes"),
                    record.get("superseded_by"),
                    json.dumps(meta) if meta is not None else None,
                ),
            )
            self._conn.execute(
                "DELETE FROM qz_braincase_record_sources WHERE record_id = ?",
                (record_id,),
            )
            if source_refs:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO qz_braincase_record_sources(record_id, source_ref_id) VALUES (?, ?)",
                    [(record_id, sref_id) for sref_id in source_refs],
                )
            self._conn.commit()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def get_state_record(self, record_id: str) -> dict | None:
        """Fetch a StateRecord by ID. Returns None on disabled DB, not found, or error."""
        if not self.enabled:
            return None
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return None
        try:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM qz_braincase_state_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                return None
            source_ref_ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT source_ref_id FROM qz_braincase_record_sources WHERE record_id = ?",
                    (record_id,),
                ).fetchall()
            ]
            return self._row_to_state_record(row, source_ref_ids)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def list_state_records(
        self,
        memory_domain: str | None = None,
        tier: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return stored StateRecords, optionally filtered.

        Returns [] on disabled DB or error. memory_domain is matched exactly —
        never inferred or expanded. No cross-domain access.
        """
        if not self.enabled:
            return []
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return []
        try:
            assert self._conn is not None
            clauses: list[str] = []
            params: list[Any] = []
            if memory_domain is not None:
                clauses.append("memory_domain = ?")
                params.append(memory_domain)
            if tier is not None:
                clauses.append("tier = ?")
                params.append(tier)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = self._conn.execute(
                f"SELECT * FROM qz_braincase_state_records {where} ORDER BY created_at_ms DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            results = []
            for row in rows:
                rid = row["record_id"]
                sref_ids = [
                    r[0]
                    for r in self._conn.execute(
                        "SELECT source_ref_id FROM qz_braincase_record_sources WHERE record_id = ?",
                        (rid,),
                    ).fetchall()
                ]
                results.append(self._row_to_state_record(row, sref_ids))
            return results
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []

    def retire_state_record(
        self,
        record_id: str,
        reason: str,
        now_ms: int | None = None,
    ) -> bool:
        """Mark a StateRecord as retired and record a revision.

        Returns False on disabled DB, record not found, or error.
        """
        if not self.enabled:
            return False
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return False
        try:
            assert self._conn is not None
            ts = now_ms if now_ms is not None else int(time.time() * 1000)
            old = self.get_state_record(record_id)
            if old is None:
                self.last_error = f"Record not found: {record_id}"
                return False
            prev_json = json.dumps(old)
            self._conn.execute(
                """
                INSERT INTO qz_braincase_record_revisions(
                    record_id, operation, previous_record_json, new_record_json, reason, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (record_id, "retire", prev_json, None, reason, ts),
            )
            self._conn.execute(
                "UPDATE qz_braincase_state_records SET status = 'retired', updated_at_ms = ? WHERE record_id = ?",
                (ts, record_id),
            )
            self._conn.commit()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def supersede_state_record(
        self,
        old_record_id: str,
        new_record: dict,
        reason: str,
        now_ms: int | None = None,
    ) -> bool:
        """Replace old_record_id with new_record, recording a supersession revision.

        new_record must have a different record_id from old_record_id.
        Stores new_record (with supersedes=old_record_id) and marks the old record superseded.
        Returns False on disabled DB, record not found, forbidden fields, or error.
        Does not mutate new_record.
        """
        if not self.enabled:
            return False
        forbidden = _FORBIDDEN_RECORD_FIELDS & new_record.keys()
        if forbidden:
            self.last_error = f"Forbidden fields in superseding record: {sorted(forbidden)}"
            return False
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return False
        try:
            assert self._conn is not None
            ts = now_ms if now_ms is not None else int(time.time() * 1000)
            old = self.get_state_record(old_record_id)
            if old is None:
                self.last_error = f"Record not found: {old_record_id}"
                return False
            new_record_id = new_record["record_id"]

            # Store new record with supersedes link (do not mutate new_record)
            linked = dict(new_record)
            linked["supersedes"] = old_record_id
            if not self.put_state_record(linked):
                return False

            # Record revision for old record
            prev_json = json.dumps(old)
            self._conn.execute(
                """
                INSERT INTO qz_braincase_record_revisions(
                    record_id, operation, previous_record_json, new_record_json, reason, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (old_record_id, "supersede", prev_json, None, reason, ts),
            )
            # Mark old record superseded
            self._conn.execute(
                "UPDATE qz_braincase_state_records SET status = 'superseded', superseded_by = ?, updated_at_ms = ? WHERE record_id = ?",
                (new_record_id, ts, old_record_id),
            )
            self._conn.commit()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _initialize_schema(self) -> None:
        assert self._conn is not None
        now_ms = int(time.time() * 1000)
        self._conn.execute(f"PRAGMA user_version = {QZ_BRAINCASE_DB_SCHEMA_VERSION}")
        # Slice 1: schema metadata
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qz_schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            INSERT INTO qz_schema_metadata(key, value, updated_at_ms)
            VALUES('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at_ms = excluded.updated_at_ms
            """,
            (str(QZ_BRAINCASE_DB_SCHEMA_VERSION), now_ms),
        )
        # Slice 2: source refs
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qz_braincase_source_refs (
                source_ref_id TEXT PRIMARY KEY,
                source_type    TEXT NOT NULL,
                title          TEXT,
                summary        TEXT NOT NULL,
                locator        TEXT NOT NULL,
                content_hash   TEXT,
                captured_at_ms INTEGER,
                metadata_json  TEXT
            )
            """
        )
        # Slice 2: state records
        # record_schema stores the schema field ("braincase/state-record@1" etc.)
        # memory_domain stored as-is; no enum constraint — config-owned.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qz_braincase_state_records (
                record_id      TEXT PRIMARY KEY,
                record_schema  TEXT NOT NULL,
                memory_domain  TEXT NOT NULL,
                tier           TEXT NOT NULL,
                record_type    TEXT NOT NULL,
                claim          TEXT NOT NULL,
                summary        TEXT NOT NULL,
                status         TEXT NOT NULL,
                visibility     TEXT NOT NULL,
                confidence     REAL NOT NULL,
                importance     REAL NOT NULL,
                retention      TEXT NOT NULL,
                created_at_ms  INTEGER NOT NULL,
                updated_at_ms  INTEGER NOT NULL,
                tags_json      TEXT NOT NULL,
                supersedes     TEXT,
                superseded_by  TEXT,
                metadata_json  TEXT
            )
            """
        )
        # Slice 2: record <-> source ref join table
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qz_braincase_record_sources (
                record_id     TEXT NOT NULL,
                source_ref_id TEXT NOT NULL,
                PRIMARY KEY (record_id, source_ref_id)
            )
            """
        )
        # Slice 2: revision log (retire, supersede, correct operations)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qz_braincase_record_revisions (
                revision_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id            TEXT NOT NULL,
                operation            TEXT NOT NULL,
                previous_record_json TEXT,
                new_record_json      TEXT,
                reason               TEXT,
                created_at_ms        INTEGER NOT NULL
            )
            """
        )
        # Slice 2: record links (supersedes chains, derived_from, etc.)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qz_braincase_record_links (
                source_record_id TEXT NOT NULL,
                target_record_id TEXT NOT NULL,
                link_type        TEXT NOT NULL,
                metadata_json    TEXT,
                PRIMARY KEY (source_record_id, target_record_id, link_type)
            )
            """
        )
        self._conn.commit()

    def _row_to_source_ref(self, row: sqlite3.Row) -> dict:
        meta_json = row["metadata_json"]
        return {
            "source_ref_id": row["source_ref_id"],
            "source_type": row["source_type"],
            "title": row["title"],
            "summary": row["summary"],
            "locator": row["locator"],
            "content_hash": row["content_hash"],
            "captured_at_ms": row["captured_at_ms"],
            "metadata": json.loads(meta_json) if meta_json is not None else None,
        }

    def _row_to_state_record(self, row: sqlite3.Row, source_ref_ids: list[str]) -> dict:
        tags_json = row["tags_json"]
        meta_json = row["metadata_json"]
        return {
            "record_id": row["record_id"],
            "schema": row["record_schema"],
            "memory_domain": row["memory_domain"],
            "tier": row["tier"],
            "record_type": row["record_type"],
            "claim": row["claim"],
            "summary": row["summary"],
            "status": row["status"],
            "visibility": row["visibility"],
            "confidence": row["confidence"],
            "importance": row["importance"],
            "retention": row["retention"],
            "created_at_ms": row["created_at_ms"],
            "updated_at_ms": row["updated_at_ms"],
            "source_refs": source_ref_ids,
            "tags": json.loads(tags_json) if tags_json else [],
            "supersedes": row["supersedes"],
            "superseded_by": row["superseded_by"],
            "metadata": json.loads(meta_json) if meta_json is not None else None,
        }

    def _read_user_version(self) -> int:
        assert self._conn is not None
        row = self._conn.execute("PRAGMA user_version").fetchone()
        if not row:
            return 0
        return int(row[0])

    def _close_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
