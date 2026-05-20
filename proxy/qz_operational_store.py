#!/usr/bin/env python3
"""Lightweight SQLite store for QuantZhai runtime events and operational facts.

Phase 1 scope: schema_meta, runtime_events, runtime_facts.

NOT BrainCaseDB.  Not model-visible memory.  Not recovery policy.
Nothing here is ever injected into forwarded request bodies or rendered to
the LLM.  This is internal diagnostics/control-plane state only.

Enable:   QZ_OPERATIONAL_DB_ENABLED=1
Path:     QZ_OPERATIONAL_DB_PATH  (overrides all)
Default:  $QZ_VAR_DIR/state/operational.sqlite3
          or $QZ_ROOT/var/state/operational.sqlite3
          or <repo>/var/state/operational.sqlite3
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

QZ_OPERATIONAL_DB_ENABLED_ENV = "QZ_OPERATIONAL_DB_ENABLED"
QZ_OPERATIONAL_DB_PATH_ENV = "QZ_OPERATIONAL_DB_PATH"


def _is_enabled(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _default_db_path(env: dict[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    var_dir_raw = source.get("QZ_VAR_DIR")
    if var_dir_raw and var_dir_raw.strip():
        var_dir = Path(var_dir_raw.strip()).expanduser()
    else:
        qz_root_raw = source.get("QZ_ROOT")
        if qz_root_raw and qz_root_raw.strip():
            var_dir = Path(qz_root_raw.strip()).expanduser() / "var"
        else:
            var_dir = Path(__file__).resolve().parents[1] / "var"
    return var_dir / "state" / "operational.sqlite3"


def _resolve_db_path(env: dict[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    explicit = source.get(QZ_OPERATIONAL_DB_PATH_ENV)
    if explicit and explicit.strip():
        return Path(explicit.strip()).expanduser()
    return _default_db_path(env)


_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS runtime_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms        INTEGER NOT NULL,
    event_type   TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runtime_events_ts   ON runtime_events(ts_ms);
CREATE INDEX IF NOT EXISTS idx_runtime_events_type ON runtime_events(event_type);

CREATE TABLE IF NOT EXISTS runtime_facts (
    key           TEXT    PRIMARY KEY,
    value_json    TEXT    NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    provenance    TEXT
);
"""


class OperationalStore:
    """Lightweight SQLite store for QuantZhai runtime events and operational facts.

    Disabled mode is a complete no-op: no file is created and all methods
    return empty/None without raising.  All DB errors in enabled mode are
    caught; they never propagate to callers.

    Usage::

        store = OperationalStore.from_env()
        store.init()                                  # idempotent; non-fatal
        store.record_startup_event("proxy_started", {"pid": os.getpid()})
        store.record_runtime_fact("last_model", {"slug": "qwen"})
        fact = store.get_runtime_fact("last_model")   # {"slug": "qwen"}
        events = store.recent_events(limit=10)
        store.close()
    """

    def __init__(self, path: Path, enabled: bool = False):
        self.path = path
        self.enabled = bool(enabled)
        self.available = False
        self.last_error: str | None = None
        self._conn: sqlite3.Connection | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "OperationalStore":
        """Construct from environment variables (or a dict override)."""
        source = os.environ if env is None else env
        enabled = _is_enabled(source.get(QZ_OPERATIONAL_DB_ENABLED_ENV))
        path = _resolve_db_path(env)
        return cls(path=path, enabled=enabled)

    def init(self) -> bool:
        """Open and initialise the DB.  Idempotent.  Non-fatal.

        Returns True when the store is available for use, False otherwise.
        When disabled, returns False immediately without touching the filesystem.
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
            self._apply_schema()
            self.available = True
            self.last_error = None
            return True
        except Exception as exc:
            self.available = False
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._close_conn()
            return False

    def close(self) -> None:
        self._close_conn()
        self.available = False

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    def record_startup_event(
        self,
        phase: str,
        payload: dict[str, Any] | None = None,
        source: str = "launcher",
    ) -> None:
        """Record a startup lifecycle event (replaces qz-write-runtime-state)."""
        self._safe_execute(
            "INSERT INTO runtime_events (ts_ms, event_type, source, payload_json)"
            " VALUES (?, ?, ?, ?)",
            (
                int(time.time() * 1000),
                str(phase),
                str(source),
                json.dumps(payload or {}, ensure_ascii=False),
            ),
            commit=True,
        )

    def record_runtime_fact(
        self,
        key: str,
        value: dict[str, Any],
        provenance: str = "proxy",
    ) -> None:
        """Upsert a key-value operational fact."""
        self._safe_execute(
            "INSERT INTO runtime_facts (key, value_json, updated_at_ms, provenance)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET"
            "   value_json    = excluded.value_json,"
            "   updated_at_ms = excluded.updated_at_ms,"
            "   provenance    = excluded.provenance",
            (
                str(key),
                json.dumps(value, ensure_ascii=False),
                int(time.time() * 1000),
                str(provenance),
            ),
            commit=True,
        )

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def get_runtime_fact(self, key: str) -> dict[str, Any] | None:
        """Return the stored fact for *key*, or None if absent or on error."""
        if not self.enabled or not self.available or self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT value_json FROM runtime_facts WHERE key = ?", (str(key),)
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["value_json"])
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def recent_events(
        self,
        event_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return recent runtime events, newest first.

        Optionally filter by *event_type*.  Returns [] on error or when disabled.
        """
        if not self.enabled or not self.available or self._conn is None:
            return []
        try:
            if event_type is not None:
                rows = self._conn.execute(
                    "SELECT id, ts_ms, event_type, source, payload_json"
                    " FROM runtime_events"
                    " WHERE event_type = ?"
                    " ORDER BY ts_ms DESC, id DESC"
                    " LIMIT ?",
                    (str(event_type), int(limit)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, ts_ms, event_type, source, payload_json"
                    " FROM runtime_events"
                    " ORDER BY ts_ms DESC, id DESC"
                    " LIMIT ?",
                    (int(limit),),
                ).fetchall()
            result = []
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
                except Exception:
                    payload = {}
                result.append({
                    "id": row["id"],
                    "ts_ms": row["ts_ms"],
                    "event_type": row["event_type"],
                    "source": row["source"],
                    "payload": payload,
                })
            return result
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []

    def health(self) -> dict[str, Any]:
        """Return a health/diagnostics dict."""
        schema_version: int | None = None
        if self.enabled and self._conn is not None:
            try:
                row = self._conn.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row:
                    schema_version = int(row["value"])
            except Exception:
                pass
        return {
            "enabled": self.enabled,
            "path": str(self.path),
            "available": self.available,
            "schema_version": schema_version,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_DDL)
        now_ms = int(time.time() * 1000)
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES (?, ?)",
            ("created_at_ms", str(now_ms)),
        )
        self._conn.commit()

    def _safe_execute(
        self,
        sql: str,
        params: tuple[Any, ...],
        *,
        commit: bool = False,
    ) -> bool:
        if not self.enabled or not self.available:
            return False
        if self._conn is None:
            return False
        try:
            self._conn.execute(sql, params)
            if commit:
                self._conn.commit()
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            try:
                self._conn.rollback()
            except Exception:
                pass
            return False

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
