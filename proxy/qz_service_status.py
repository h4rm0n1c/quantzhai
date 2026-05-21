#!/usr/bin/env python3
"""Build qz.service.status.v1 — canonical service/recovery status taxonomy.

This module is additive. It derives canonical enum values from an existing
/qz/control-plane payload (qz.control_plane.status.v1) and returns a new
qz.service.status.v1 dict that is embedded inside the control-plane response
as the "service_status" field. No existing fields are changed.

Canonical enums are defined in docs/backend-service-recovery-semantics.md.
This is slice 2 of #47: Normalize backend service status and recovery semantics.

Read-only — no state mutation, no recovery actions, no Docker calls.
"""
from __future__ import annotations

from typing import Any

SERVICE_STATUS_SCHEMA = "qz.service.status.v1"


def _proxy_state(cp: dict[str, Any]) -> str:
    """Derive proxy_state enum from control-plane payload."""
    init = cp.get("proxy_initialization") or {}
    if init.get("state") == "failed" or init.get("error"):
        return "failed"
    if not (cp.get("readiness") or {}).get("proxy_ready"):
        return "initializing"
    return "ready"


def _catalog_state(cp: dict[str, Any]) -> str:
    """Derive catalog_state enum from control-plane payload."""
    init = cp.get("proxy_initialization") or {}
    r = cp.get("readiness") or {}
    if init.get("state") == "failed":
        return "failed"
    if r.get("catalog_ready"):
        return "ready"
    if r.get("proxy_ready"):
        return "loading"  # proxy ready but catalog scan still running
    return "unknown"


def _backend_state(cp: dict[str, Any]) -> str:
    """Derive backend_state enum from control-plane payload."""
    r = cp.get("readiness") or {}
    b = cp.get("backend") or {}
    if not r.get("backend_reachable"):
        return "unreachable"
    health = b.get("health_status")
    if isinstance(health, int) and health != 200:
        return "unhealthy"
    return "healthy"


def _model_state(cp: dict[str, Any]) -> str:
    """Derive model_state enum from control-plane payload."""
    r = cp.get("readiness") or {}
    b = cp.get("backend") or {}
    m = cp.get("models") or {}

    if m.get("selected_model_ready") is True:
        return "loaded"
    if m.get("request_admission_state") in ("starting", "loading"):
        return "loading"
    if m.get("request_admission_state") == "failed":
        return "failed"

    # Context or identity mismatch requiring restart
    if b.get("restart_required"):
        return "mismatch"

    # No models configured at all
    if m.get("count", 0) == 0 and not m.get("selected"):
        return "none"

    # Backend not reachable — can't know model state
    if not r.get("backend_reachable"):
        return "unknown"

    # Explicit backend error
    if b.get("error"):
        return "failed"

    # Model confirmed loaded
    if r.get("backend_ready") and (b.get("loaded_model") or m.get("backend_loaded_model")):
        return "loaded"

    # Backend reachable but no model loaded
    if r.get("backend_reachable") and not r.get("backend_ready"):
        return "unloaded"

    return "unknown"


def _request_admission(
    proxy_st: str, catalog_st: str, backend_st: str, model_st: str,
    cp: dict[str, Any],
) -> str:
    """Derive request_admission enum — what /v1/responses would currently decide."""
    if proxy_st in ("starting", "initializing", "failed"):
        return "rejected_proxy_not_ready"
    if catalog_st in ("unknown", "loading", "failed"):
        return "rejected_proxy_not_ready"
    if (cp.get("models") or {}).get("count", 0) == 0:
        return "rejected_model_missing"
    if backend_st == "unreachable":
        return "rejected_backend_unavailable"
    if backend_st in ("unhealthy", "failed", "restarting"):
        return "rejected_backend_not_ready"
    if model_st in ("none", "unknown", "unloaded", "loading", "failed", "mismatch"):
        return "rejected_model_not_loaded"
    return "accepted"


def _recovery_state(proxy_st: str, catalog_st: str, backend_st: str, model_st: str) -> str:
    """Derive recovery_state enum."""
    if proxy_st == "failed":
        return "failed"
    if proxy_st in ("starting", "initializing") or catalog_st == "loading":
        return "in_progress"
    if backend_st in ("unreachable", "unhealthy"):
        return "available"
    if backend_st == "failed":
        return "available"
    if model_st == "loading":
        return "in_progress"
    if model_st in ("failed", "mismatch"):
        return "available"
    if model_st == "unloaded" and backend_st == "healthy":
        return "available"
    return "none"


def _operator_action(
    proxy_st: str, catalog_st: str, backend_st: str,
    model_st: str, recovery_st: str,
) -> str:
    """Derive the most relevant operator_action string."""
    if proxy_st in ("starting", "initializing") or catalog_st == "loading":
        return "remote_wait"
    if proxy_st == "failed":
        return "inspect_logs"
    if catalog_st == "failed":
        return "inspect_logs"
    if backend_st == "unreachable":
        return "start_backend"
    if backend_st in ("unhealthy", "failed"):
        return "restart_backend"
    if model_st in ("failed", "mismatch"):
        return "restart_backend"
    if model_st in ("none", "unloaded"):
        return "select_model"
    if model_st == "unknown":
        return "refresh_catalog"
    return ""


def _rrft(proxy_st: str, catalog_st: str, backend_st: str, model_st: str) -> tuple[bool, bool, bool]:
    """Return (recoverable, retryable, fatal) for the current state."""
    # Fatal: proxy failed with no recovery path
    if proxy_st == "failed":
        return False, False, True
    # All ready: nothing to recover
    if (proxy_st == "ready" and catalog_st == "ready"
            and backend_st == "healthy" and model_st == "loaded"):
        return False, False, False
    # Initializing/loading: wait and retry
    if proxy_st in ("starting", "initializing") or catalog_st == "loading" or model_st == "loading":
        return True, True, False
    # Everything else: recoverable but not auto-retryable
    return True, False, False


def build_service_status(cp: dict[str, Any]) -> dict[str, Any]:
    """Build a qz.service.status.v1 payload from an existing control-plane payload.

    Takes a qz.control_plane.status.v1 dict and returns a new qz.service.status.v1
    dict with canonical enum values. Does not probe the backend again.

    Args:
        cp: An existing /qz/control-plane response dict.

    Returns:
        A qz.service.status.v1 dict suitable for embedding in the control-plane
        response as "service_status".
    """
    if not isinstance(cp, dict):
        cp = {}

    proxy_st   = _proxy_state(cp)
    catalog_st = _catalog_state(cp)
    backend_st = _backend_state(cp)
    model_st   = _model_state(cp)
    admission  = _request_admission(proxy_st, catalog_st, backend_st, model_st, cp)
    recovery   = _recovery_state(proxy_st, catalog_st, backend_st, model_st)
    action     = _operator_action(proxy_st, catalog_st, backend_st, model_st, recovery)
    recoverable, retryable, fatal = _rrft(proxy_st, catalog_st, backend_st, model_st)

    # last_error: prefer backend.error, fall back to proxy_initialization.error
    b = cp.get("backend") or {}
    init = cp.get("proxy_initialization") or {}
    last_error = str(b.get("error") or init.get("error") or "")

    # operator_hints: carry forward existing proxy-owned hints, no duplication
    hints = list(cp.get("operator_hints") or [])

    return {
        "schema": SERVICE_STATUS_SCHEMA,
        "proxy_state": proxy_st,
        "catalog_state": catalog_st,
        "backend_state": backend_st,
        "model_state": model_st,
        "request_admission": admission,
        "recovery_state": recovery,
        "recoverable": recoverable,
        "retryable": retryable,
        "fatal": fatal,
        "last_error": last_error,
        "operator_action": action,
        "operator_hints": hints,
    }
