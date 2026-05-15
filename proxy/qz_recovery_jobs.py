#!/usr/bin/env python3
"""In-memory recovery job store for async recovery actions.

Tracks in-flight and recently completed/failed async recovery jobs.
In-memory only. Thread-safe. Non-durable.

This is #50: Add async recovery job model.

Exposed via:
  GET /qz/recovery/jobs/<request_id>
  GET /qz/recovery/status  (jobs snapshot embedded)
"""
from __future__ import annotations

import threading
import time
from typing import Any

RECOVERY_JOB_SCHEMA  = "qz.recovery.job.v1"
RECOVERY_JOBS_SCHEMA = "qz.recovery.jobs.v1"

_DEFAULT_MAX_JOBS = 50


class RecoveryJobStore:
    """In-memory store for async recovery job state.

    Thread-safe. Keeps at most max_jobs recent entries; oldest are pruned.
    """

    def __init__(self, max_jobs: int = _DEFAULT_MAX_JOBS) -> None:
        self._lock    = threading.Lock()
        self._max     = max_jobs
        self._jobs:   dict[str, dict[str, Any]] = {}
        self._order:  list[str] = []    # insertion order, oldest first

    # ---- Mutators --------------------------------------------------------

    def create(
        self,
        request_id: str,
        action: str,
        pre_status: dict[str, Any] | None = None,
        operator_warning: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Register a new async job in 'queued' state and return a copy."""
        t = now if now is not None else time.time()
        job: dict[str, Any] = {
            "schema":           RECOVERY_JOB_SCHEMA,
            "request_id":       request_id,
            "action":           action,
            "state":            "queued",
            "accepted":         True,
            "async":            True,
            "started_at":       t,
            "finished_at":      None,
            "error":            "",
            "result":           None,
            "pre_status":       pre_status,
            "post_status":      None,
            "telemetry_event":  "",
            "operator_warning": operator_warning,
        }
        with self._lock:
            self._jobs[request_id] = job
            self._order.append(request_id)
            while len(self._order) > self._max:
                old_id = self._order.pop(0)
                self._jobs.pop(old_id, None)
        return dict(job)

    def mark_running(self, request_id: str, now: float | None = None) -> None:
        """Transition job to 'running'."""
        with self._lock:
            job = self._jobs.get(request_id)
            if job is not None:
                job["state"] = "running"

    def mark_completed(
        self,
        request_id: str,
        result: Any = None,
        post_status: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> None:
        """Transition job to 'completed'."""
        t = now if now is not None else time.time()
        with self._lock:
            job = self._jobs.get(request_id)
            if job is not None:
                job["state"]           = "completed"
                job["finished_at"]     = t
                job["error"]           = ""
                job["result"]          = result
                job["post_status"]     = post_status
                job["telemetry_event"] = "recovery_action_completed"

    def mark_failed(
        self,
        request_id: str,
        error: str = "",
        post_status: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> None:
        """Transition job to 'failed'."""
        t = now if now is not None else time.time()
        with self._lock:
            job = self._jobs.get(request_id)
            if job is not None:
                job["state"]           = "failed"
                job["finished_at"]     = t
                job["error"]           = error
                job["post_status"]     = post_status
                job["telemetry_event"] = "recovery_action_failed"

    # ---- Queries ---------------------------------------------------------

    def get(self, request_id: str) -> dict[str, Any] | None:
        """Return a copy of the job dict, or None if not found."""
        with self._lock:
            job = self._jobs.get(request_id)
            return dict(job) if job is not None else None

    def snapshot(self, limit: int = 10, now: float | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the most recent jobs."""
        with self._lock:
            recent = list(reversed(self._order[-limit:]))
            jobs = [dict(self._jobs[rid]) for rid in recent if rid in self._jobs]
        return {
            "schema": RECOVERY_JOBS_SCHEMA,
            "count":  len(jobs),
            "jobs":   jobs,
        }


# Process-global in-memory singleton. Non-durable.
RECOVERY_JOBS: RecoveryJobStore = RecoveryJobStore()
