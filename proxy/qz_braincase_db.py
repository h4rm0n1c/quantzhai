#!/usr/bin/env python3
"""Optional SQLite state/memory storage substrate for QuantZhai experiments.

BrainCaseDB is the low-level storage case — not a policy layer.

Slice 1: DB availability and schema metadata plumbing.
Slice 2: Explicit StateRecord and SourceRef storage for Slice A fixture-shaped records.
Slice 3:   Internal search (exact/fts/tag) and inspect helpers over stored records.
           No model-facing tools. No automatic ingestion. No runtime integration.
Slice 3.1: FTS reindex/backfill helper (rebuild_fts_index).
           Indexes already-stored StateRecords only. No automatic ingestion.

Hard rules:
- BrainCaseDB does not auto-ingest observed runtime/request data.
- Callers must use an explicit memory/state write path.
- memory_domain is the configured value supplied by the caller; BrainCaseDB stores
  it as-is and must not infer, create, normalize, or grant domain values.
- BrainCaseDB is not a telemetry warehouse, request log, or memory_domain registry.
- search/inspect helpers are internal plumbing — not model-facing tools.

Forbidden fields (rejected by put_* methods if present in input dicts):
  raw_prompt, raw_request_body, request_body, full_log, telemetry_event, stream_event
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

QZ_BRAINCASE_DB_SCHEMA_VERSION = 4
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

# Patterns that indicate explicit / path-like / issue-like queries, routing to exact mode.
_EXACT_PATTERNS = re.compile(r'#\d+|^[./]')


def default_db_path(root: str | Path | None = None) -> Path:
    root = Path(
        root if root is not None else os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1])
    )
    return root / "var" / "state" / "braincase.sqlite3"


def _env_enabled(value: str | None) -> bool:
    # Default enabled when not explicitly set — BrainCase DB is always on.
    # QZ_STATE_DB_ENABLED=0/false/no can still disable explicitly if needed.
    if value is None:
        return True
    stripped = value.strip().lower()
    if stripped in {"0", "false", "no", "off", "disabled"}:
        return False
    return True


class BrainCaseDB:
    """Optional non-fatal wrapper around the SQLite state/memory storage substrate.

    All public methods return False/None/[] rather than raising on error.
    Failures are recorded in last_error.

    search/inspect methods are internal helpers — not model-facing tools.
    They must not produce RenderPackets or inject content into prompts.
    """

    def __init__(self, path: str | Path | None = None, enabled: bool = False):
        self.path = Path(path) if path is not None else default_db_path()
        self.enabled = bool(enabled)
        self.available = False
        self.last_error: str | None = None
        self._conn: sqlite3.Connection | None = None
        self._fts_available: bool = False

    @property
    def fts_available(self) -> bool:
        return self._fts_available

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
            # Slice C.1: backfill FTS if state_records has rows and FTS index is empty.
            if self._fts_available:
                self._maybe_backfill_fts_index()
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
            "fts_available": self._fts_available,
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
        FTS index is updated if available.
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
            # Sync FTS index if available (best-effort; failure does not undo the record write)
            if self._fts_available:
                try:
                    tags_text = " ".join(str(t) for t in tags)
                    self._sync_fts_for_record(record_id, record["claim"], record["summary"], tags_text)
                except Exception:
                    self._fts_available = False
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def record_access(self, record_ids: list[str]) -> None:
        """Update last_accessed_at_ms and increment access_count for each record.

        Called by render and recall tools after fetching records for the model.
        Best-effort: never raises, silently no-ops on disabled DB or error.
        """
        if not record_ids or not self.enabled or self._conn is None or not self.available:
            return
        now_ms = int(time.time() * 1000)
        try:
            self._conn.executemany(
                """
                UPDATE qz_braincase_state_records
                SET last_accessed_at_ms = ?,
                    access_count = COALESCE(access_count, 0) + 1
                WHERE record_id = ?
                """,
                [(now_ms, rid) for rid in record_ids if isinstance(rid, str)],
            )
            self._conn.commit()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

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
            return self._rows_to_records(rows)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []

    def list_state_records_by_status(
        self,
        *,
        status: str,
        memory_domain: str | None = None,
        tier: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return stored StateRecords filtered by exact status match.

        Applies the status filter in the SQL query so active/retired records
        cannot starve candidate records that happen to be older.

        Returns [] on disabled DB or error. memory_domain and tier are matched
        exactly if supplied. No cross-domain access.
        Not model-facing — operator/internal review plumbing only.
        """
        if not self.enabled:
            return []
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return []
        try:
            assert self._conn is not None
            clauses: list[str] = ["status = ?"]
            params: list[Any] = [status]
            if memory_domain is not None:
                clauses.append("memory_domain = ?")
                params.append(memory_domain)
            if tier is not None:
                clauses.append("tier = ?")
                params.append(tier)
            where = "WHERE " + " AND ".join(clauses)
            rows = self._conn.execute(
                f"SELECT * FROM qz_braincase_state_records {where} "
                f"ORDER BY created_at_ms DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return self._rows_to_records(rows)
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

    def promote_state_record(
        self,
        record_id: str,
        *,
        new_status: str,
        new_visibility: str,
        reason: str,
        now_ms: int | None = None,
    ) -> bool:
        """Update a candidate StateRecord's status and visibility, recording a promotion revision.

        This is a mechanical DB primitive. Policy enforcement (candidate check,
        redaction, dedup, conflict) is the caller's responsibility (qz_braincase_review.py).

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
            new_snapshot = dict(old, status=new_status, visibility=new_visibility,
                                updated_at_ms=ts)
            new_json = json.dumps(new_snapshot)
            self._conn.execute(
                """
                INSERT INTO qz_braincase_record_revisions(
                    record_id, operation, previous_record_json, new_record_json, reason, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (record_id, "promote", prev_json, new_json, reason, ts),
            )
            self._conn.execute(
                """
                UPDATE qz_braincase_state_records
                SET status = ?, visibility = ?, updated_at_ms = ?
                WHERE record_id = ?
                """,
                (new_status, new_visibility, ts, record_id),
            )
            self._conn.commit()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def patch_state_record(
        self,
        record_id: str,
        *,
        reason: str,
        tier: str | None = None,
        importance: float | None = None,
        tags: list[str] | None = None,
        now_ms: int | None = None,
    ) -> bool:
        """Update mutable fields (tier, importance, tags) on a StateRecord.

        All changes are recorded in the revision log.
        Returns False on disabled DB, record not found, or nothing to patch.
        """
        if not self.enabled:
            return False
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return False
        if tier is None and importance is None and tags is None:
            return False
        try:
            assert self._conn is not None
            ts = now_ms if now_ms is not None else int(time.time() * 1000)
            old = self.get_state_record(record_id)
            if old is None:
                self.last_error = f"Record not found: {record_id}"
                return False
            prev_json = json.dumps(old)
            sets: list[str] = ["updated_at_ms = ?"]
            params: list = [ts]
            new = dict(old, updated_at_ms=ts)
            if tier is not None:
                sets.append("tier = ?"); params.append(tier); new["tier"] = tier
            if importance is not None:
                sets.append("importance = ?"); params.append(float(importance)); new["importance"] = importance
            if tags is not None:
                tags_json = json.dumps(tags)
                sets.append("tags_json = ?"); params.append(tags_json); new["tags"] = tags
            params.append(record_id)
            self._conn.execute(
                f"UPDATE qz_braincase_state_records SET {', '.join(sets)} WHERE record_id = ?",
                params,
            )
            self._conn.execute(
                """
                INSERT INTO qz_braincase_record_revisions(
                    record_id, operation, previous_record_json, new_record_json, reason, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (record_id, "patch", prev_json, json.dumps(new), reason, ts),
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
    # Slice C: query planning
    # ------------------------------------------------------------------

    def query_plan(self, query: str, *, mode: str = "auto") -> dict:
        """Choose search mode for a given query string.

        Returns {"mode": "exact"|"fts"|"tag", "query": str}.
        Does not perform any DB operations.

        Routing rules (mode="auto"):
          tag:<name>         -> tag mode
          "quoted string"    -> exact mode (strips outer quotes)
          #<number>          -> exact mode (issue/PR reference)
          path/like/query    -> exact mode (contains '/' without http prefix)
          ./relative/path    -> exact mode
          otherwise          -> fts if available, else exact
        """
        if mode == "exact":
            return {"mode": "exact", "query": query}
        if mode == "tag":
            tag = query[4:].strip() if query.startswith("tag:") else query
            return {"mode": "tag", "query": tag}
        if mode == "fts":
            effective = "fts" if self._fts_available else "exact"
            return {"mode": effective, "query": query}

        # mode == "auto"
        if query.startswith("tag:"):
            return {"mode": "tag", "query": query[4:].strip()}
        if query.startswith('"') and query.endswith('"') and len(query) > 1:
            return {"mode": "exact", "query": query[1:-1]}
        if _EXACT_PATTERNS.search(query):
            return {"mode": "exact", "query": query}
        if "/" in query and not query.startswith("http"):
            return {"mode": "exact", "query": query}
        if self._fts_available:
            return {"mode": "fts", "query": query}
        return {"mode": "exact", "query": query}

    # ------------------------------------------------------------------
    # Slice C: search helpers
    # ------------------------------------------------------------------

    def search_state_records(
        self,
        query: str,
        *,
        memory_domain: str | None = None,
        tier: str | None = None,
        mode: str = "auto",
        limit: int = 10,
    ) -> list[dict]:
        """Search stored StateRecords using exact, FTS, or tag mode.

        Returns [] on disabled DB or error.
        memory_domain is matched exactly — never inferred or expanded.
        No cross-domain access occurs automatically.

        This is an internal search helper, not a model-facing tool.
        Results are plain StateRecord dicts. No RenderPacket is produced.
        """
        if not self.enabled:
            return []
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return []
        try:
            assert self._conn is not None
            plan = self.query_plan(query, mode=mode)
            effective_mode = plan["mode"]
            effective_query = plan["query"]
            if effective_mode == "tag":
                return self._search_by_tag(effective_query, memory_domain, tier, limit)
            if effective_mode == "fts" and self._fts_available:
                return self._search_fts(effective_query, memory_domain, tier, limit)
            return self._search_exact(effective_query, memory_domain, tier, limit)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []

    # ------------------------------------------------------------------
    # Slice C: inspect helper
    # ------------------------------------------------------------------

    def inspect_state_records(
        self,
        record_ids: list[str],
        *,
        include_source_refs: bool = True,
    ) -> list[dict]:
        """Fetch selected records plus their SourceRefs, preserving requested order.

        Returns [] on disabled DB or error.
        Unknown IDs return an entry with record=None and error="not found".
        Never crosses memory_domain automatically.
        Never produces model-facing rendered text or RenderPackets.

        Each result entry shape:
          {"record_id": str, "record": dict | None, "source_refs": list, "error": str | None}
        """
        if not self.enabled:
            return []
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return []
        try:
            assert self._conn is not None
            results = []
            for rid in record_ids:
                rec = self.get_state_record(rid)
                if rec is None:
                    results.append({
                        "record_id": rid,
                        "record": None,
                        "source_refs": [],
                        "error": "not found",
                    })
                    continue
                source_refs: list[dict] = []
                if include_source_refs:
                    for sref_id in rec.get("source_refs") or []:
                        sref = self.get_source_ref(sref_id)
                        if sref is not None:
                            source_refs.append(sref)
                results.append({
                    "record_id": rid,
                    "record": rec,
                    "source_refs": source_refs,
                    "error": None,
                })
            return results
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []

    # ------------------------------------------------------------------
    # Slice C.1: FTS reindex / backfill
    # ------------------------------------------------------------------

    def rebuild_fts_index(self) -> bool:
        """Rebuild the FTS index from all stored StateRecords.

        Clears qz_braincase_state_records_fts and repopulates it from
        qz_braincase_state_records. Only indexes already-stored records —
        this is not automatic ingestion and does not create new records.

        Returns False on disabled DB, unavailable DB, FTS5 unavailable, or error.
        Idempotent: safe to call multiple times.
        """
        if not self.enabled:
            return False
        if not self._fts_available:
            return False
        if self._conn is None or not self.available:
            if self._conn is None and not self.init():
                return False
        try:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT record_id, claim, summary, tags_json FROM qz_braincase_state_records"
            ).fetchall()
            self._conn.execute("DELETE FROM qz_braincase_state_records_fts")
            if rows:
                self._conn.executemany(
                    "INSERT INTO qz_braincase_state_records_fts(record_id, claim, summary, tags) VALUES (?, ?, ?, ?)",
                    [
                        (
                            row["record_id"],
                            row["claim"],
                            row["summary"],
                            " ".join(json.loads(row["tags_json"])) if row["tags_json"] else "",
                        )
                        for row in rows
                    ],
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

    def _sync_fts_for_record(
        self, record_id: str, claim: str, summary: str, tags_text: str
    ) -> None:
        """Insert or replace one record's entry in the FTS index.

        Does not catch exceptions — caller is responsible for error handling.
        Does not check FTS availability — caller must guard with self._fts_available.
        """
        assert self._conn is not None
        self._conn.execute(
            "DELETE FROM qz_braincase_state_records_fts WHERE record_id = ?",
            (record_id,),
        )
        self._conn.execute(
            "INSERT INTO qz_braincase_state_records_fts(record_id, claim, summary, tags) VALUES (?, ?, ?, ?)",
            (record_id, claim, summary, tags_text),
        )
        self._conn.commit()

    def _maybe_backfill_fts_index(self) -> None:
        """Auto-backfill FTS if state_records has rows but FTS index is empty.

        Called from init() after FTS is confirmed available. Best-effort: failures
        are silently swallowed so they cannot break init(). This is not ingestion —
        it indexes already-stored StateRecords only.
        """
        if not self._fts_available or self._conn is None:
            return
        try:
            state_count = self._conn.execute(
                "SELECT COUNT(*) FROM qz_braincase_state_records"
            ).fetchone()[0]
            fts_count = self._conn.execute(
                "SELECT COUNT(*) FROM qz_braincase_state_records_fts"
            ).fetchone()[0]
            if state_count > 0 and fts_count == 0:
                self.rebuild_fts_index()
        except Exception:
            pass

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
        # Schema v4 migration: temporal access tracking columns.
        # ALTER TABLE IF NOT EXISTS is not supported in older SQLite; catch
        # OperationalError if the column already exists (idempotent migration).
        for _col_sql in [
            "ALTER TABLE qz_braincase_state_records ADD COLUMN last_accessed_at_ms INTEGER",
            "ALTER TABLE qz_braincase_state_records ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                self._conn.execute(_col_sql)
            except Exception:
                pass  # column already exists — safe to ignore
        self._conn.commit()
        # Slice 3: optional FTS5 virtual table for claim/summary/tags search.
        # Degrades gracefully if FTS5 is not available in this SQLite build.
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS qz_braincase_state_records_fts
                USING fts5(record_id UNINDEXED, claim, summary, tags)
                """
            )
            # Verify the table is queryable
            self._conn.execute("SELECT record_id FROM qz_braincase_state_records_fts LIMIT 0")
            self._conn.commit()
            self._fts_available = True
        except Exception:
            self._fts_available = False

    def _search_exact(
        self,
        query: str,
        memory_domain: str | None,
        tier: str | None,
        limit: int,
    ) -> list[dict]:
        assert self._conn is not None
        text_pat = f"%{query}%"
        tag_pat = f'%"{query}"%'
        clauses: list[str] = ["(claim LIKE ? OR summary LIKE ? OR tags_json LIKE ?)"]
        params: list[Any] = [text_pat, text_pat, tag_pat]
        if memory_domain is not None:
            clauses.append("memory_domain = ?")
            params.append(memory_domain)
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier)
        where = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"SELECT * FROM qz_braincase_state_records {where} ORDER BY importance DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return self._rows_to_records(rows)

    def _search_fts(
        self,
        query: str,
        memory_domain: str | None,
        tier: str | None,
        limit: int,
    ) -> list[dict]:
        assert self._conn is not None
        try:
            fts_rows = self._conn.execute(
                "SELECT record_id FROM qz_braincase_state_records_fts WHERE qz_braincase_state_records_fts MATCH ?",
                (query,),
            ).fetchall()
        except Exception:
            # FTS query failed (e.g. bad query syntax); fall back to exact
            return self._search_exact(query, memory_domain, tier, limit)
        if not fts_rows:
            return []
        fts_ids = [r[0] for r in fts_rows]
        placeholders = ",".join("?" * len(fts_ids))
        clauses: list[str] = [f"record_id IN ({placeholders})"]
        params: list[Any] = list(fts_ids)
        if memory_domain is not None:
            clauses.append("memory_domain = ?")
            params.append(memory_domain)
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier)
        where = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"SELECT * FROM qz_braincase_state_records {where} ORDER BY importance DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return self._rows_to_records(rows)

    def _search_by_tag(
        self,
        tag: str,
        memory_domain: str | None,
        tier: str | None,
        limit: int,
    ) -> list[dict]:
        assert self._conn is not None
        # Match exact JSON-encoded tag value: "tag" appears with surrounding quotes in the JSON array.
        tag_pat = f'%"{tag}"%'
        clauses: list[str] = ["tags_json LIKE ?"]
        params: list[Any] = [tag_pat]
        if memory_domain is not None:
            clauses.append("memory_domain = ?")
            params.append(memory_domain)
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier)
        where = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"SELECT * FROM qz_braincase_state_records {where} ORDER BY importance DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return self._rows_to_records(rows)

    def _rows_to_records(self, rows: list) -> list[dict]:
        assert self._conn is not None
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
