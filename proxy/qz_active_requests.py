#!/usr/bin/env python3
"""In-memory active request tracker.

Tracks /v1/responses requests currently in-flight through the proxy.
Used as a read-only safety signal for recovery planning:
  - restart/reload actions should not fire while active_requests > 0
  - planning returns blocked_by_active_requests=True when count > 0 and force=False

In-memory only. Thread-safe. Non-durable.
Never raises from begin() or finish() — calling code does not need try/except.

This is slice 10a of #47.
"""
from __future__ import annotations

import threading
import time
from typing import Any

ACTIVE_REQUESTS_SCHEMA = "qz.active_requests.v1"


class ActiveRequestTracker:
    """In-memory thread-safe tracker for in-flight proxy requests.

    All public methods are guaranteed non-raising.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, dict[str, Any]] = {}

    def begin(
        self,
        request_id: str,
        route: str = "",
        model: str = "",
        started_at: float | None = None,
    ) -> None:
        """Register a request as in-flight. Safe to call multiple times for the same id."""
        if not request_id:
            return
        t = started_at if started_at is not None else time.time()
        try:
            with self._lock:
                self._requests[request_id] = {
                    "request_id": request_id,
                    "route": route,
                    "model": model,
                    "started_at": t,
                }
        except Exception:
            pass

    def finish(self, request_id: str, finished_at: float | None = None) -> None:
        """Deregister a request. Safe to call for unknown or already-finished ids."""
        if not request_id:
            return
        try:
            with self._lock:
                self._requests.pop(request_id, None)
        except Exception:
            pass

    def count(self) -> int:
        """Return the current number of in-flight requests. Always >= 0."""
        try:
            with self._lock:
                return len(self._requests)
        except Exception:
            return 0

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of current in-flight requests."""
        t = now if now is not None else time.time()
        try:
            with self._lock:
                requests = [
                    {
                        "request_id": r["request_id"],
                        "route": r.get("route", ""),
                        "model": r.get("model", ""),
                        "started_at": r.get("started_at"),
                        "age_secs": round(t - r["started_at"], 3)
                        if isinstance(r.get("started_at"), (int, float))
                        else None,
                    }
                    for r in self._requests.values()
                ]
            return {
                "schema": ACTIVE_REQUESTS_SCHEMA,
                "count": len(requests),
                "requests": requests,
            }
        except Exception:
            return {"schema": ACTIVE_REQUESTS_SCHEMA, "count": 0, "requests": []}


# Process-global in-memory singleton. Non-durable.
ACTIVE_REQUESTS: ActiveRequestTracker = ActiveRequestTracker()
