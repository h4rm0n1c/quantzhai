#!/usr/bin/env python3
"""Proxy-owned control-plane status for GET /qz/control-plane.

This module exposes a single client-friendly summary of proxy, catalog, model,
and backend readiness. It is designed for:
- Remote qz-codex clients that are not on the llama.cpp / Docker host.
- qz-doctor, qz-top, qz-wait-ready, and smoke scripts.
- Any future monitoring client that should not parse multiple endpoints.

The endpoint is safe when the backend is down: it always returns JSON.
It never assumes local Docker or infrastructure access.
It delegates backend probing to ModelRouter (which uses a short control-plane
timeout, currently ~0.75 s) and reuses existing proxy state.

Schema: qz.control_plane.status.v1
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from .qz_paths import codex_model_catalog_path as _codex_model_catalog_path
    from .qz_service_status import build_service_status
    from .qz_recovery_status import build_recovery_status
    from .qz_recovery_state import RECOVERY_STATE
    from .qz_active_requests import ACTIVE_REQUESTS
    from .qz_recovery_jobs import RECOVERY_JOBS
    from .qz_vram_snapshot import get_cached_vram_snapshot
    from .qz_model_status import (
        load_handler_model_state,
        identity_matches_loaded,
        model_selection_hints,
    )
    from .qz_model_catalog import match_model
except ImportError:
    from qz_paths import codex_model_catalog_path as _codex_model_catalog_path
    from qz_service_status import build_service_status
    from qz_recovery_status import build_recovery_status
    from qz_recovery_state import RECOVERY_STATE
    from qz_active_requests import ACTIVE_REQUESTS
    from qz_recovery_jobs import RECOVERY_JOBS
    from qz_vram_snapshot import get_cached_vram_snapshot
    from qz_model_status import (
        load_handler_model_state,
        identity_matches_loaded,
        model_selection_hints,
    )
    from qz_model_catalog import match_model
from typing import Any

QZ_CONTROL_PLANE_SCHEMA = "qz.control_plane.status.v1"


def _codex_catalog_info() -> dict[str, Any]:
    """Return the Codex catalog artifact path and existence state.

    Path comes from qz_paths (QZ_VAR_DIR / generated / codex), not CODEX_HOME.
    CODEX_HOME is qz-codex client-local state after #58 (#56 Slice D).
    """
    catalog_path = _codex_model_catalog_path()
    return {
        "path": str(catalog_path),
        "exists": catalog_path.is_file(),
        "updated_by_proxy": None,  # updated each time proxy refreshes; not persisted yet
    }


def _overall_status(readiness: dict[str, Any]) -> str:
    """Derive a single status label from the readiness dict."""
    if not readiness.get("proxy_ready"):
        return "initializing"
    if not readiness.get("catalog_ready"):
        return "initializing"
    if not readiness.get("backend_reachable"):
        return "backend_unavailable"
    if not readiness.get("backend_ready"):
        return "model_not_loaded"
    return "ready"


def _operator_hints(
    readiness: dict[str, Any],
    model_count: int,
    backend_error: str | None,
) -> list[str]:
    hints = []
    if not readiness.get("proxy_ready"):
        hints.append(
            "Proxy is initializing. Wait for proxy_ready == true before connecting. "
            "Check /health for readiness detail."
        )
    if readiness.get("proxy_ready") and not readiness.get("catalog_ready"):
        hints.append(
            "Model catalog is still loading. Wait for catalog_ready == true "
            "or call POST /qz/models/refresh."
        )
    if readiness.get("catalog_ready") and model_count == 0:
        hints.append(
            "Catalog is ready but no models are visible. "
            "Check that var/models/ contains *.gguf files."
        )
    if readiness.get("catalog_ready") and not readiness.get("backend_reachable"):
        hints.append(
            "Proxy is ready but the llama.cpp backend is unreachable. "
            "Check /qz/status for backend health. "
            "Start the stack with scripts/qz-up."
        )
    if readiness.get("backend_reachable") and not readiness.get("backend_ready"):
        hints.append(
            "Backend process is reachable but no model is loaded yet. "
            "A model will load on the first /v1/responses request."
        )
    if backend_error:
        hints.append(f"Backend probe error: {backend_error}")
    hints.append(
        "Remote qz-codex clients only need a reachable QuantZhai proxy URL — "
        "no local Docker or llama.cpp access required."
    )
    return hints


def build_control_plane_status(handler: Any) -> dict[str, Any]:
    """Build the qz.control_plane.status.v1 payload.

    Probes proxy initialization, model catalog, and backend (via the existing
    short-timeout control-plane path) and assembles a single client-friendly
    summary. Safe when backend is down.
    """
    # --- proxy initialization ---
    initialization: dict[str, Any] = {}
    try:
        init_fn = getattr(handler, "_initialization_payload", None)
        if callable(init_fn):
            initialization = init_fn() or {}
    except Exception:
        pass

    proxy_ready = bool(initialization.get("ready"))
    catalog_ready = bool(initialization.get("catalog_ready"))

    # --- model catalog ---
    model_ids: list[str] = []
    selected_id = ""
    selected_backend_id = ""

    if catalog_ready:
        try:
            catalog = handler._model_catalog()
            for e in catalog.entries:
                if not isinstance(e, dict):
                    continue
                if e.get("profile_valid", True) is False:
                    continue
                mid = e.get("key") or e.get("stem") or e.get("backend_id") or ""
                if mid:
                    model_ids.append(mid)
            sel = getattr(catalog, "selected", None)
            if isinstance(sel, dict):
                selected_id = sel.get("key") or sel.get("stem") or sel.get("backend_id") or ""
                selected_backend_id = sel.get("backend_id") or ""
        except Exception:
            pass

    # --- backend status (via ModelRouter, short probe timeout) ---
    backend_reachable = False
    backend_ready = False
    health_status: int | None = None
    loaded_model = ""
    loaded_count = 0
    restart_required = False
    backend_error: str | None = None

    if proxy_ready:
        try:
            router = handler._model_router()
            summary = router.status_summary("GET /qz/control-plane")
            health_status = summary.get("health_status")
            backend_reachable = isinstance(health_status, int) and health_status != 0
            backend_ready = bool(summary.get("ready", False))
            loaded_model = summary.get("loaded_model") or ""
            loaded_count = int(summary.get("loaded_count") or 0)
            restart_required = bool(summary.get("restart_required", False))
        except Exception as exc:
            backend_error = str(exc)

    # --- codex catalog artifact ---
    codex_catalog = _codex_catalog_info()

    # --- readiness map ---
    models_visible = len(model_ids) > 0
    readiness: dict[str, Any] = {
        "proxy_http": True,       # we're handling the request, so HTTP is up
        "proxy_ready": proxy_ready,
        "catalog_ready": catalog_ready,
        "models_visible": models_visible,
        "backend_reachable": backend_reachable,
        "backend_ready": backend_ready,
        "codex_catalog_ready": codex_catalog.get("exists", False),
    }

    overall = _overall_status(readiness)
    ok = proxy_ready and catalog_ready  # backend is optional; ok = proxy+catalog

    # --- proxy-owned model selection state (qz.model_state.v1) ---
    selection_state = load_handler_model_state(handler)
    selected_identity_from_state = (
        selection_state.selected_key or selection_state.selected_backend_id
    )
    configured_env_model = (os.environ.get("QZ_MODEL_KEY") or "").strip()
    selected_loaded_mismatch = bool(
        selected_identity_from_state
        and loaded_model
        and not identity_matches_loaded(
            selection_state.selected_key,
            selection_state.selected_backend_id,
            loaded_model,
        )
    )
    state_model_visible = False
    state_profile_valid = True
    if selected_identity_from_state and catalog_ready:
        try:
            catalog = handler._model_catalog()
            entry = match_model(catalog.entries, selected_identity_from_state)
            if entry is not None:
                state_model_visible = True
                state_profile_valid = entry.get("profile_valid", True) is not False
        except Exception:
            pass

    # Backend manager mode + derived model switch state.
    bm_snap = {}
    bm = getattr(handler, "backend_manager", None)
    if bm is not None and callable(getattr(bm, "snapshot", None)):
        try:
            _snap = bm.snapshot()
            if isinstance(_snap, dict):
                bm_snap = _snap
        except Exception:
            pass
    backend_model_mode = (bm_snap.get("backend_model_mode") or "direct").strip() or "direct"
    bm_phase = bm_snap.get("phase") or ""
    try:
        from .qz_model_status import _derive_model_switch_state
    except ImportError:
        from qz_model_status import _derive_model_switch_state
    model_switch_state, active_load_operation = _derive_model_switch_state(
        backend_phase=bm_phase,
        backend_model_mode=backend_model_mode,
        state=selection_state,
        selected_loaded_mismatch=selected_loaded_mismatch,
    )

    payload: dict[str, Any] = {
        "schema": QZ_CONTROL_PLANE_SCHEMA,
        "ok": ok,
        "status": overall,
        "readiness": readiness,
        "proxy_initialization": initialization,
        "models": {
            "count": len(model_ids),
            "ids": sorted(model_ids),
            "selected": selected_id,
            "selected_backend_id": selected_backend_id,
            # Slice C — proxy-owned selection authority surface.
            "configured_env_model":      configured_env_model,
            "selected_source":           selection_state.selected_source,
            "selected_at":               selection_state.selected_at,
            "backend_loaded_model":      loaded_model,
            "selected_loaded_mismatch":  selected_loaded_mismatch,
            "selection_reason":          selection_state.selected_reason,
            "model_visible":             state_model_visible,
            "profile_valid":             state_profile_valid,
            "restart_required":          restart_required,
            # Post-cleanup polish — direct vs router mode + switch state.
            "backend_model_mode":          backend_model_mode,
            "launch_model_key":            bm_snap.get("launch_model_key", ""),
            "launch_model_backend_id":     bm_snap.get("launch_model_backend_id", ""),
            "launch_model_path_basename":  bm_snap.get("launch_model_path_basename", ""),
            "model_switch_state":          model_switch_state,
            "active_load_operation":       active_load_operation,
            "last_good_key":               selection_state.last_good_key,
            "last_good_backend_id":        selection_state.last_good_backend_id,
            "failed_candidate_key":        selection_state.failed_candidate_key,
            "failed_candidate_backend_id": selection_state.failed_candidate_backend_id,
        },
        "backend": {
            "reachable": backend_reachable,
            "ready": backend_ready,
            "health_status": health_status,
            "loaded_model": loaded_model,
            "loaded_count": loaded_count,
            "restart_required": restart_required,
            "error": backend_error,
            # Slice C — selection-failure surface (populated by Slice E).
            "model_load_failed":        selection_state.last_load_result == "failed",
            "load_error":               selection_state.last_load_error,
            "load_error_type":          selection_state.last_load_error_type,
            "insufficient_vram":        selection_state.last_load_error_type == "insufficient_vram",
            "selected_model_failed":    selection_state.last_load_result == "failed",
        },
        "codex_catalog": codex_catalog,
        "operator_hints": _operator_hints(readiness, len(model_ids), backend_error),
    }
    # Append model-selection-specific operator hints.
    payload["operator_hints"].extend(
        model_selection_hints(
            selection_state,
            configured_env_model,
            loaded_model,
            backend_model_mode=backend_model_mode,
            model_switch_state=model_switch_state,
        )
    )
    # Additive: canonical service/recovery status derived from this payload.
    # Does not change existing fields; safe when backend is down.
    payload["service_status"] = build_service_status(payload)
    # Additive: read-only recovery summary with in-memory runtime state snapshot.
    try:
        runtime_snapshot = RECOVERY_STATE.snapshot()
    except Exception:
        runtime_snapshot = None
    try:
        ar_snapshot = ACTIVE_REQUESTS.snapshot()
    except Exception:
        ar_snapshot = None
    try:
        jobs_snapshot = RECOVERY_JOBS.snapshot()
    except Exception:
        jobs_snapshot = None
    payload["recovery"] = build_recovery_status(
        payload["service_status"],
        runtime_state=runtime_snapshot,
        active_requests=ar_snapshot,
        recovery_jobs=jobs_snapshot,
    )
    # Additive: backend manager lifecycle state (safe snapshot only).
    try:
        mgr = getattr(handler, "backend_manager", None)
        if mgr is not None and callable(getattr(mgr, "snapshot", None)):
            snap = mgr.snapshot()
            if isinstance(snap, dict):
                payload["backend_manager"] = snap
    except Exception:
        pass
    # Additive: proxy-owned VRAM snapshot (TTL-cached to avoid per-request nvidia-smi).
    try:
        payload["vram"] = get_cached_vram_snapshot(handler=handler)
    except Exception:
        pass
    return payload
