#!/usr/bin/env python3
"""Build qz.recovery.plan.v1 — read-only recovery feasibility planner.

Answers: "Would this recovery action be feasible right now, and why?"

Pure function. No I/O. No backend probes. No Docker calls. No telemetry.
No state mutation. No HTTP route.

This is slice 6 of #47: Add pure recovery planning helper.
The endpoint (POST /qz/recovery/plan) is slice 7 and not yet implemented.

Exposed by slice 7 via:
  POST /qz/recovery/plan  (dry-run only; not yet wired)
"""
from __future__ import annotations

from typing import Any

RECOVERY_PLAN_SCHEMA = "qz.recovery.plan.v1"

ALLOWED_RECOVERY_ACTIONS: frozenset[str] = frozenset({
    "refresh_catalog",
    "select_model",
    "reload_selected_model",
    "start_backend",
    "restart_backend",
    "clear_failure",
})

# Per-action static properties -----------------------------------------------

_REQUIRES_AUTHORITY: frozenset[str] = frozenset({
    "select_model",
    "reload_selected_model",
    "start_backend",
    "restart_backend",
    "clear_failure",
})

_REQUIRES_CONFIRMATION: frozenset[str] = frozenset({
    "select_model",
    "reload_selected_model",
    "start_backend",
    "restart_backend",
})

_WOULD_INTERRUPT: frozenset[str] = frozenset({
    "reload_selected_model",
    "restart_backend",
    # select_model does not interrupt active requests (no backend call)
})

_BACKOFF_APPLIES: frozenset[str] = frozenset({
    "select_model",
    "start_backend",
    "restart_backend",
    "reload_selected_model",
})

# State-changing actions blocked by an already-in-progress recovery
_BLOCKED_BY_IN_PROGRESS: frozenset[str] = frozenset({
    "select_model",
    "reload_selected_model",
    "start_backend",
    "restart_backend",
    "clear_failure",
})

_OPERATOR_WARNINGS: dict[str, str] = {
    "refresh_catalog":       "Refreshing the catalog does not restart the backend.",
    "select_model":          "Selecting a model may trigger model load depending on router behaviour.",
    "reload_selected_model": "Reloading the selected model may interrupt active requests.",
    "start_backend":         "Starting the backend requires local operator authority.",
    "restart_backend":       "Restarting the backend will interrupt active requests.",
    "clear_failure":         "Clearing failure state does not start or restart the backend.",
}


# Per-action feasibility logic ------------------------------------------------

def _plan_refresh_catalog(ss: dict[str, Any], notes: list[str]) -> dict[str, bool]:
    proxy_st   = ss.get("proxy_state", "unknown")
    catalog_st = ss.get("catalog_state", "unknown")

    blocked_by_state = False
    if proxy_st in ("starting", "initializing", "failed"):
        blocked_by_state = True
        notes.append(f"Proxy is not ready (proxy_state={proxy_st!r}); catalog refresh requires proxy to be ready.")
    elif catalog_st == "loading":
        blocked_by_state = True
        notes.append("Catalog is already loading; a refresh is already in progress.")
    return {"blocked_by_state": blocked_by_state}


def _plan_select_model(ss: dict[str, Any], model: str, notes: list[str]) -> dict[str, bool]:
    proxy_st   = ss.get("proxy_state", "unknown")
    catalog_st = ss.get("catalog_state", "unknown")

    blocked_by_missing_model = not model
    blocked_by_state = False

    if blocked_by_missing_model:
        notes.append("A model slug is required for select_model but none was provided.")
    if proxy_st != "ready":
        blocked_by_state = True
        notes.append(f"Proxy is not ready (proxy_state={proxy_st!r}); model selection requires proxy=ready.")
    elif catalog_st != "ready":
        blocked_by_state = True
        notes.append(f"Catalog is not ready (catalog_state={catalog_st!r}); model selection requires catalog=ready.")
    else:
        notes.append("Selecting a model may trigger model load if the model is not already loaded.")

    return {"blocked_by_state": blocked_by_state, "blocked_by_missing_model": blocked_by_missing_model}


def _plan_reload_selected_model(ss: dict[str, Any], force: bool, notes: list[str]) -> dict[str, bool]:
    backend_st = ss.get("backend_state", "unknown")

    blocked_by_state = False
    if backend_st not in ("healthy", "unhealthy"):
        blocked_by_state = True
        notes.append(
            f"Backend is not reachable (backend_state={backend_st!r}); "
            "reload_selected_model requires an accessible backend."
        )
    return {"blocked_by_state": blocked_by_state}


def _plan_start_backend(ss: dict[str, Any], notes: list[str]) -> dict[str, bool]:
    backend_st = ss.get("backend_state", "unknown")

    blocked_by_state = False
    if backend_st == "healthy":
        blocked_by_state = True
        notes.append("Backend is already reachable (backend_state=healthy); start_backend is not needed.")
    elif backend_st == "unhealthy":
        blocked_by_state = True
        notes.append(
            "Backend is reachable but unhealthy (backend_state=unhealthy); "
            "use restart_backend instead of start_backend."
        )
    elif backend_st != "unreachable":
        blocked_by_state = True
        notes.append(f"start_backend requires backend_state=unreachable, got {backend_st!r}.")
    return {"blocked_by_state": blocked_by_state}


def _plan_restart_backend(ss: dict[str, Any], force: bool, notes: list[str]) -> dict[str, bool]:
    backend_st = ss.get("backend_state", "unknown")
    model_st   = ss.get("model_state", "unknown")

    viable_backend = backend_st in ("unhealthy", "failed")
    viable_model   = model_st in ("mismatch", "failed")
    blocked_by_state = not (viable_backend or viable_model or force)

    if blocked_by_state:
        notes.append(
            f"restart_backend requires backend_state in (unhealthy, failed) or "
            f"model_state in (mismatch, failed), got backend_state={backend_st!r} "
            f"model_state={model_st!r}. Use force=true to override."
        )
    if force and not (viable_backend or viable_model):
        notes.append("force=true overrides state requirement for restart_backend.")
    return {"blocked_by_state": blocked_by_state}


def _plan_clear_failure(ss: dict[str, Any], notes: list[str]) -> dict[str, bool]:
    recovery_st = ss.get("recovery_state", "none")
    model_st    = ss.get("model_state", "unknown")
    backend_st  = ss.get("backend_state", "unknown")

    has_failure = (
        recovery_st in ("failed", "manual_required")
        or model_st == "failed"
        or backend_st == "failed"
    )
    blocked_by_state = not has_failure
    if blocked_by_state:
        notes.append(
            f"No failure state to clear "
            f"(recovery_state={recovery_st!r}, model_state={model_st!r}, "
            f"backend_state={backend_st!r})."
        )
    return {"blocked_by_state": blocked_by_state}


# Active request safety -------------------------------------------------------

def _apply_active_request_check(
    action: str,
    force: bool,
    active_requests: int | None,
    flags: dict[str, bool],
    notes: list[str],
) -> None:
    if action not in _WOULD_INTERRUPT:
        return
    if active_requests is None:
        notes.append(
            "active request count unavailable; "
            "implementation must require force=true before triggering interrupt actions."
        )
        return
    if active_requests > 0:
        if not force:
            flags["blocked_by_active_requests"] = True
            notes.append(
                f"{active_requests} active request(s) in flight; "
                "use force=true to override (may drop active requests)."
            )
        else:
            flags["requires_force"] = True
            notes.append(
                f"force=true overrides active request block ({active_requests} request(s) in flight); "
                "active requests may be interrupted."
            )


# Main builder ----------------------------------------------------------------

def build_recovery_plan(
    service_status: dict[str, Any],
    action: str,
    *,
    model: str = "",
    force: bool = False,
    authority_enabled: bool = False,
    local_request: bool = True,
    active_requests: int | None = None,
    backoff_active: bool = False,
    recovery_in_progress: bool = False,
) -> dict[str, Any]:
    """Build a qz.recovery.plan.v1 feasibility plan.

    Pure function. No I/O. No state mutation. Never raises.

    Args:
        service_status:      qz.service.status.v1 dict (from build_service_status).
        action:              Requested recovery action name.
        model:               Model slug (required for select_model).
        force:               Override active-request and state checks for interrupt actions.
        authority_enabled:   Whether QZ_RECOVERY_ACTIONS=1 (local operator authority).
        local_request:       Whether the request originates from loopback/local interface.
        active_requests:     Current in-flight request count, or None if not tracked.
        backoff_active:      Whether a per-action backoff clock is currently active.
        recovery_in_progress: Whether a recovery action is already running.

    Returns:
        A qz.recovery.plan.v1 dict. Always returns a valid dict; never raises.
    """
    # Coerce invalid service_status
    notes: list[str] = []
    if not isinstance(service_status, dict):
        notes.append("service_status was invalid or None; state-based checks degraded.")
        service_status = {}

    # Resolve state strings for action checks
    ss = service_status

    # Unknown action — return immediately
    if action not in ALLOWED_RECOVERY_ACTIONS:
        return {
            "schema": RECOVERY_PLAN_SCHEMA,
            "action": action,
            "feasible": False,
            "blocked_by_active_requests": False,
            "blocked_by_backoff": False,
            "blocked_by_authority": False,
            "blocked_by_state": True,
            "blocked_by_locality": False,
            "blocked_by_in_progress": False,
            "blocked_by_missing_model": False,
            "would_interrupt_requests": False,
            "requires_authority": False,
            "requires_confirmation": False,
            "requires_force": False,
            "operator_warning": "",
            "pre_status": ss,
            "notes": [f"Unknown action {action!r}; allowed: {sorted(ALLOWED_RECOVERY_ACTIONS)}."],
        }

    # Initialise flag map with static properties
    flags: dict[str, bool] = {
        "blocked_by_active_requests": False,
        "blocked_by_backoff":         False,
        "blocked_by_authority":       False,
        "blocked_by_state":           False,
        "blocked_by_locality":        False,
        "blocked_by_in_progress":     False,
        "blocked_by_missing_model":   False,
        "requires_force":             False,
    }

    # --- Authority check ---
    if action in _REQUIRES_AUTHORITY:
        if not authority_enabled:
            flags["blocked_by_authority"] = True
            notes.append(
                "QZ_RECOVERY_ACTIONS is not enabled; "
                f"{action!r} requires local operator authority (set QZ_RECOVERY_ACTIONS=1)."
            )
        if not local_request:
            flags["blocked_by_locality"] = True
            notes.append(
                f"{action!r} requires a local (loopback) request; "
                "remote clients cannot trigger state-changing recovery."
            )

    # --- Recovery in progress check ---
    if recovery_in_progress and action in _BLOCKED_BY_IN_PROGRESS:
        flags["blocked_by_in_progress"] = True
        notes.append(f"A recovery action is already in progress; {action!r} is blocked until it completes.")

    # --- Backoff check ---
    if backoff_active and action in _BACKOFF_APPLIES:
        flags["blocked_by_backoff"] = True
        notes.append(f"Backoff is active for {action!r}; retry after backoff expires.")

    # --- Per-action state checks ---
    if action == "refresh_catalog":
        flags.update(_plan_refresh_catalog(ss, notes))
    elif action == "select_model":
        action_flags = _plan_select_model(ss, model, notes)
        flags.update(action_flags)
    elif action == "reload_selected_model":
        flags.update(_plan_reload_selected_model(ss, force, notes))
    elif action == "start_backend":
        flags.update(_plan_start_backend(ss, notes))
    elif action == "restart_backend":
        flags.update(_plan_restart_backend(ss, force, notes))
    elif action == "clear_failure":
        flags.update(_plan_clear_failure(ss, notes))

    # --- Active request check (after per-action, so flags dict is populated) ---
    _apply_active_request_check(action, force, active_requests, flags, notes)

    # Derive feasible from all blocking flags
    blocking_flags = (
        "blocked_by_active_requests",
        "blocked_by_backoff",
        "blocked_by_authority",
        "blocked_by_state",
        "blocked_by_locality",
        "blocked_by_in_progress",
        "blocked_by_missing_model",
    )
    feasible = not any(flags[f] for f in blocking_flags)

    return {
        "schema": RECOVERY_PLAN_SCHEMA,
        "action": action,
        "feasible": feasible,
        "blocked_by_active_requests": flags["blocked_by_active_requests"],
        "blocked_by_backoff":         flags["blocked_by_backoff"],
        "blocked_by_authority":       flags["blocked_by_authority"],
        "blocked_by_state":           flags["blocked_by_state"],
        "blocked_by_locality":        flags["blocked_by_locality"],
        "blocked_by_in_progress":     flags["blocked_by_in_progress"],
        "blocked_by_missing_model":   flags["blocked_by_missing_model"],
        "would_interrupt_requests":   action in _WOULD_INTERRUPT,
        "requires_authority":         action in _REQUIRES_AUTHORITY,
        "requires_confirmation":      action in _REQUIRES_CONFIRMATION,
        "requires_force":             flags["requires_force"],
        "operator_warning":           _OPERATOR_WARNINGS.get(action, ""),
        "pre_status":                 ss,
        "notes":                      notes,
    }
