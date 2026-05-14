#!/usr/bin/env python3
"""In-memory recovery attempt and backoff state tracker.

Tracks per-action failure counts, backoff schedules, and in-progress state.
In-memory only. Non-durable — does not survive proxy restart.

This is slice 8 of #47: Add in-memory recovery backoff state.

Used by:
  POST /qz/recovery/plan  — reports real backoff_active / recovery_in_progress
  POST /qz/recovery/trigger  — (slice 9) enforces backoff, marks started/failed/completed
  GET /qz/recovery/status  — exposes runtime_state snapshot

Durable crash-loop protection requires Phase 1 SQLite (#2) and is not implemented here.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

RECOVERY_STATE_SCHEMA = "qz.recovery.runtime_state.v1"

DEFAULT_BACKOFF_SECS: list[int] = [30, 120, 300]
DEFAULT_MAX_ATTEMPTS: int = 3


def parse_backoff_schedule(s: str) -> list[int]:
    """Parse a comma-separated backoff schedule string into a list of positive ints.

    Returns DEFAULT_BACKOFF_SECS on any parse error, empty input, or non-positive value.

    Examples:
        parse_backoff_schedule("30,120,300")  ->  [30, 120, 300]
        parse_backoff_schedule("60")          ->  [60]
        parse_backoff_schedule("bad")         ->  [30, 120, 300]
        parse_backoff_schedule("")            ->  [30, 120, 300]
        parse_backoff_schedule("30,-1,300")   ->  [30, 120, 300]
    """
    try:
        parts = [int(x.strip()) for x in str(s).split(",") if x.strip()]
        if not parts or any(p <= 0 for p in parts):
            return list(DEFAULT_BACKOFF_SECS)
        return parts
    except Exception:
        return list(DEFAULT_BACKOFF_SECS)


class RecoveryRuntimeState:
    """In-memory per-action recovery attempt and backoff tracker.

    Thread-safe. All public methods acquire the internal lock.
    Non-durable: state is lost when the proxy process restarts.

    Backoff policy (default):
        attempt 1 failure  ->  30 s backoff
        attempt 2 failure  ->  120 s backoff
        attempt 3 failure  ->  300 s backoff
        attempt 4+ failure ->  manual_required (blocked until clear() is called)

    Override via constructor args or env at singleton creation time.
    """

    def __init__(
        self,
        backoff_schedule: list[int] | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._backoff_schedule: list[int] = (
            backoff_schedule if backoff_schedule is not None
            else list(DEFAULT_BACKOFF_SECS)
        )
        self._max_attempts: int = (
            max_attempts if max_attempts is not None
            else DEFAULT_MAX_ATTEMPTS
        )
        # Progress state
        self._in_progress: bool = False
        self._current_action: str = ""
        self._current_request_id: str = ""
        # History (most recent action, regardless of outcome)
        self._last_action: str = ""
        self._last_started_at: float | None = None
        self._last_finished_at: float | None = None
        self._last_error: str = ""
        # Per-action tracking
        self._attempts: dict[str, int] = {}
        self._backoff_until: dict[str, float] = {}    # epoch; 0.0 = no active backoff
        self._manual_required: dict[str, bool] = {}   # True when attempts > max_attempts
        self._last_action_errors: dict[str, str] = {}

    # ---- Status queries --------------------------------------------------

    def is_backoff_active(self, action: str, now: float | None = None) -> bool:
        """Return True if action is blocked by backoff or manual_required."""
        t = now if now is not None else time.time()
        with self._lock:
            if self._manual_required.get(action, False):
                return True
            return t < self._backoff_until.get(action, 0.0)

    def is_recovery_in_progress(self) -> bool:
        """Return True if any recovery action is currently marked in-progress."""
        with self._lock:
            return self._in_progress

    # ---- State mutators (wired by slice 9 trigger endpoint) --------------

    def mark_started(
        self,
        action: str,
        request_id: str = "",
        now: float | None = None,
    ) -> None:
        """Mark a recovery action as started (in-flight)."""
        t = now if now is not None else time.time()
        with self._lock:
            self._in_progress = True
            self._current_action = action
            self._current_request_id = request_id
            self._last_action = action
            self._last_started_at = t
            self._last_finished_at = None
            self._last_error = ""

    def mark_completed(self, action: str, now: float | None = None) -> None:
        """Mark a recovery action as successfully completed.

        Resets attempt count and backoff for the action on success.
        """
        t = now if now is not None else time.time()
        with self._lock:
            self._in_progress = False
            self._current_action = ""
            self._current_request_id = ""
            self._last_finished_at = t
            self._last_error = ""
            # Reset per-action state on success
            self._attempts[action] = 0
            self._backoff_until[action] = 0.0
            self._manual_required.pop(action, None)
            self._last_action_errors.pop(action, None)

    def mark_failed(
        self,
        action: str,
        error: str,
        now: float | None = None,
    ) -> None:
        """Mark a recovery action as failed.

        Increments attempt count and sets per-action backoff.
        When attempt count exceeds max_attempts, sets manual_required=True,
        which blocks further automatic attempts until clear() is called.
        """
        t = now if now is not None else time.time()
        with self._lock:
            self._in_progress = False
            self._current_action = ""
            self._current_request_id = ""
            self._last_finished_at = t
            self._last_error = error

            count = self._attempts.get(action, 0) + 1
            self._attempts[action] = count
            self._last_action_errors[action] = error

            if count > self._max_attempts:
                # Too many failures — require manual intervention.
                # is_backoff_active() returns True via manual_required flag.
                self._manual_required[action] = True
                self._backoff_until[action] = 0.0
            else:
                # Apply time-based backoff; clamp index to schedule length
                idx = min(count - 1, len(self._backoff_schedule) - 1)
                self._backoff_until[action] = t + self._backoff_schedule[idx]

    def clear(self, action: str | None = None) -> None:
        """Clear recovery state.

        If action is given, clears only that action's attempt/backoff/manual state.
        If action is None, clears all state including any in-progress flag.
        """
        with self._lock:
            if action is None:
                self._in_progress = False
                self._current_action = ""
                self._current_request_id = ""
                self._last_error = ""
                self._attempts.clear()
                self._backoff_until.clear()
                self._manual_required.clear()
                self._last_action_errors.clear()
            else:
                self._attempts.pop(action, None)
                self._backoff_until.pop(action, None)
                self._manual_required.pop(action, None)
                self._last_action_errors.pop(action, None)
                if self._current_action == action:
                    self._in_progress = False
                    self._current_action = ""
                    self._current_request_id = ""

    # ---- Snapshot --------------------------------------------------------

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of current in-memory state."""
        t = now if now is not None else time.time()
        with self._lock:
            all_actions = set(
                list(self._attempts)
                + list(self._backoff_until)
                + list(self._manual_required)
            )
            attempts: dict[str, Any] = {}
            for act in sorted(all_actions):
                count = self._attempts.get(act, 0)
                bu = self._backoff_until.get(act, 0.0)
                mr = self._manual_required.get(act, False)
                backoff_active = mr or (t < bu)
                attempts[act] = {
                    "count": count,
                    # backoff_until is None when manual_required (no time-based expiry)
                    "backoff_until": (bu if (bu > 0.0 and not mr) else None),
                    "backoff_active": backoff_active,
                    "manual_required": mr,
                    "last_error": self._last_action_errors.get(act, ""),
                }
            return {
                "schema": RECOVERY_STATE_SCHEMA,
                "in_progress": self._in_progress,
                "current_action": self._current_action,
                "current_request_id": self._current_request_id,
                "last_recovery_action": self._last_action,
                "last_recovery_started_at": self._last_started_at,
                "last_recovery_finished_at": self._last_finished_at,
                "last_recovery_error": self._last_error,
                "attempts": attempts,
            }


def _make_default_recovery_state() -> RecoveryRuntimeState:
    """Build RECOVERY_STATE from environment config at import time."""
    sched_raw = os.environ.get("QZ_RECOVERY_BACKOFF_SECS", "").strip()
    backoff = (
        parse_backoff_schedule(sched_raw)
        if sched_raw
        else list(DEFAULT_BACKOFF_SECS)
    )
    max_att = DEFAULT_MAX_ATTEMPTS
    try:
        v = int(os.environ.get("QZ_RECOVERY_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)))
        if v >= 1:
            max_att = v
    except Exception:
        pass
    return RecoveryRuntimeState(backoff_schedule=backoff, max_attempts=max_att)


# Process-global in-memory singleton.
# Non-durable: does not survive proxy restart.
# Durable crash-loop protection requires #2 SQLite.
RECOVERY_STATE: RecoveryRuntimeState = _make_default_recovery_state()
