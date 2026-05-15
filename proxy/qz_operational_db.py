#!/usr/bin/env python3
"""Optional SQLite storage substrate for QuantZhai state/memory experiments.

Slice 1 only owns DB availability and schema metadata. "Operational" here means
non-model-visible storage plumbing for future parser-boundary scoping facts. It
is not a runtime telemetry warehouse, recovery-state store, config authority, or
memory_domain registry.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

QZ_OPERATIONAL_DB_SCHEMA_VERSION = 1
QZ_STATE_DB_ENABLED_ENV = "QZ_STATE_DB_ENABLED"
QZ_STATE_DB_PATH_ENV = "QZ_STATE_DB_PATH"


def default_db_path(root: str | Path | None = None) -> Path:
    root = Path(
        root if root is not None else os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1])
    )
    return root / "var" / "qz-state.sqlite3"


def _env_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


class OperationalDB:
    """Small non-fatal wrapper around the optional SQLite storage substrate."""

    def __init__(self, path: str | Path | None = None, enabled: bool = False):
        self.path = Path(path) if path is not None else default_db_path()
        self.enabled = bool(enabled)
        self.available = False
        self.last_error: str | None = None
        self._conn: sqlite3.Connection | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "OperationalDB":
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
                self._conn = sqlite3.connect(
                    str(self.path),
                    timeout=1.0,
                    check_same_thread=False,
                )
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

    def _initialize_schema(self) -> None:
        assert self._conn is not None
        now_ms = int(time.time() * 1000)
        self._conn.execute(f"PRAGMA user_version = {QZ_OPERATIONAL_DB_SCHEMA_VERSION}")
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
            (str(QZ_OPERATIONAL_DB_SCHEMA_VERSION), now_ms),
        )
        self._conn.commit()

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
