#!/usr/bin/env python3
"""Build qz.recovery.status.v1 — read-only recovery summary.

Derives a recovery-focused status summary from an existing qz.service.status.v1
payload (itself derived from /qz/control-plane).

This is slice 4 of #47: Add read-only /qz/recovery/status endpoint.

Read-only. No I/O. No backend probes. No Docker calls. No state mutation.
No recovery actions are triggered — this module only classifies current state
and suggests operator actions for a human or future automated path.

Exposed via:
  GET /qz/recovery/status  — standalone recovery summary
  (optionally) "recovery" field inside /qz/control-plane
"""
from __future__ import annotations

from typing import Any

RECOVERY_STATUS_SCHEMA = "qz.recovery.status.v1"

# Maps service_status.operator_action -> (remote_client_action, local_operator_action)
_ACTION_MAP: dict[str, tuple[str, str]] = {
    "remote_wait":         ("wait",              "monitor"),
    "start_backend":       ("contact_operator",  "start_backend"),
    "restart_backend":     ("contact_operator",  "restart_backend"),
    "select_model":        ("choose_valid_model", "select_model"),
    "refresh_catalog":     ("retry_after_refresh", "refresh_catalog"),
    "inspect_logs":        ("contact_operator",  "inspect_logs"),
    "manual_intervention": ("contact_operator",  "inspect_logs"),
}

_SUMMARY_MAP: dict[str, str] = {
    "remote_wait":         "Startup or initialization is in progress; wait for readiness.",
    "start_backend":       "Backend is unreachable; local operator should start the backend.",
    "restart_backend":     "Backend or model state requires a restart.",
    "select_model":        "Requested/selected model is not currently loadable.",
    "refresh_catalog":     "Catalog or model visibility should be refreshed.",
    "inspect_logs":        "Manual inspection of proxy logs is required.",
    "manual_intervention": "Manual intervention is required.",
    "":                    "No recovery action required.",
}


def build_recovery_status(service_status: dict[str, Any]) -> dict[str, Any]:
    """Build a qz.recovery.status.v1 payload from a qz.service.status.v1 dict.

    Args:
        service_status: A qz.service.status.v1 dict (from build_service_status()).
                        If None or invalid, returns a safe degraded payload.

    Returns:
        A qz.recovery.status.v1 dict. Always returns a valid dict; never raises.
    """
    if not isinstance(service_status, dict):
        return {
            "schema": RECOVERY_STATUS_SCHEMA,
            "ok": False,
            "state": "unknown",
            "recoverable": False,
            "retryable": False,
            "fatal": False,
            "operator_action": "",
            "last_error": "",
            "remote_client_action": "contact_operator",
            "local_operator_action": "inspect_logs",
            "summary": "Recovery status unavailable; service_status not provided.",
            "service_status": None,
            "operator_hints": [],
        }

    recovery_state = str(service_status.get("recovery_state") or "none")
    recoverable    = bool(service_status.get("recoverable", False))
    retryable      = bool(service_status.get("retryable", False))
    fatal          = bool(service_status.get("fatal", False))
    op_action      = str(service_status.get("operator_action") or "")
    last_error     = str(service_status.get("last_error") or "")
    hints          = list(service_status.get("operator_hints") or [])

    # ok: true when no problem or when recovery is already in progress
    ok = recovery_state in ("none", "in_progress") and not fatal

    # remote/local action derived from operator_action
    remote_action, local_action = _ACTION_MAP.get(op_action, ("contact_operator", "inspect_logs") if op_action else ("", ""))

    # summary
    if fatal:
        summary = "Fatal error; manual intervention is required. Contact a local operator."
    else:
        summary = _SUMMARY_MAP.get(op_action, f"Recovery state: {recovery_state}.")

    # Ensure hints are remote-friendly
    if not hints and op_action and op_action != "remote_wait":
        # Add a default remote-friendly hint only when no proxy-owned hints exist
        if remote_action == "contact_operator":
            hints = ["Contact the local operator for backend/proxy recovery assistance."]

    return {
        "schema": RECOVERY_STATUS_SCHEMA,
        "ok": ok,
        "state": recovery_state,
        "recoverable": recoverable,
        "retryable": retryable,
        "fatal": fatal,
        "operator_action": op_action,
        "last_error": last_error,
        "remote_client_action": remote_action,
        "local_operator_action": local_action,
        "summary": summary,
        "service_status": service_status,
        "operator_hints": hints,
    }
