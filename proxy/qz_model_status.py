"""qz.model_status.v1 — single answer for configured vs selected vs loaded.

Used by:
- GET /qz/model/status
- POST /qz/model/select{,-and-restart}, /qz/model/reload (returns the same shape)
- /qz/control-plane (additive model fields derived from the same source state)

See ``docs/proxy-model-selection-authority.md`` §6.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from .qz_model_state import (
        ModelState,
        load_model_state,
        selected_identity_from_state,
    )
    from .qz_model_catalog import match_model
except ImportError:
    from qz_model_state import (
        ModelState,
        load_model_state,
        selected_identity_from_state,
    )
    from qz_model_catalog import match_model


QZ_MODEL_STATUS_SCHEMA = "qz.model_status.v1"


def _model_state_path(handler: Any) -> Path | None:
    raw = getattr(handler, "model_state_path", None)
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    env_path = os.environ.get("QZ_MODEL_STATE_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return None


def load_handler_model_state(handler: Any) -> ModelState:
    """Load qz.model_state.v1 using the handler's configured path; empty on miss."""
    path = _model_state_path(handler)
    if path is None:
        return ModelState()
    try:
        return load_model_state(path).state
    except Exception:
        return ModelState()


def identity_matches_loaded(
    selected_key: str,
    selected_backend_id: str,
    backend_loaded_model: str,
) -> bool:
    """True when the backend-loaded id matches either selected alias."""
    candidates = {x for x in (selected_key, selected_backend_id) if x}
    return bool(backend_loaded_model and backend_loaded_model in candidates)


def _resolve_catalog_entry(handler: Any, identity: str) -> dict | None:
    """Look up identity in the current catalog; return entry dict or None."""
    if not identity:
        return None
    try:
        catalog = handler._model_catalog()
    except Exception:
        return None
    entries = getattr(catalog, "entries", None) or []
    return match_model(entries, identity)


def _backend_loaded_model(handler: Any) -> str:
    """Probe the backend for the currently loaded model id; "" on any failure."""
    try:
        router = handler._model_router()
    except Exception:
        return ""
    try:
        backend_models = router.backend_models()
        loaded = router.loaded_backend_models(backend_models)
        if loaded:
            first = loaded[0]
            if isinstance(first, dict):
                model_id = first.get("id")
                if isinstance(model_id, str):
                    return model_id.strip()
    except Exception:
        return ""
    return ""


def _backend_manager_state(handler: Any) -> tuple[str, str]:
    """Return (phase, gpu_offload_state) from BackendManager snapshot, ("","") on miss."""
    mgr = getattr(handler, "backend_manager", None)
    if mgr is None or not callable(getattr(mgr, "snapshot", None)):
        return "", ""
    try:
        snap = mgr.snapshot()
    except Exception:
        return "", ""
    if not isinstance(snap, dict):
        return "", ""
    phase = snap.get("phase") or ""
    gpu = snap.get("gpu_offload_state") or ""
    return (phase if isinstance(phase, str) else ""), (gpu if isinstance(gpu, str) else "")


def _recommended_action(
    *,
    selected_identity: str,
    backend_loaded_model: str,
    selected_loaded_mismatch: bool,
    last_load_error_type: str | None,
) -> str | None:
    """Operator-actionable hint, or None when no action is needed."""
    if last_load_error_type == "insufficient_vram":
        return (
            "Selected model failed to fit VRAM/context. "
            "Pick a smaller model or reduce QZ_CONTEXT."
        )
    if not selected_identity:
        return "No selected model. POST /qz/model/select with {\"model\":\"<id>\"}."
    if selected_loaded_mismatch:
        return (
            "Selected model differs from loaded backend model. "
            "POST /qz/model/reload or /qz/model/select-and-restart."
        )
    if selected_identity and not backend_loaded_model:
        return (
            "No backend-loaded model yet. POST /qz/model/reload to load the "
            "selected model."
        )
    return None


def _recommended_recovery_action(state) -> str | None:
    """Hint for failure recovery — independent of the active-model hint."""
    if state.last_load_result != "failed":
        return None
    failed_id = state.failed_candidate_key or state.failed_candidate_backend_id or "<unknown>"
    last_good = state.last_good_key or state.last_good_backend_id
    rolled_back = bool(last_good and "rolled back from failed candidate" in (state.selected_reason or ""))

    if rolled_back:
        suffix = ""
        if state.last_load_error_type == "insufficient_vram":
            suffix = (
                " The candidate did not fit VRAM/context — pick a smaller "
                "model or reduce QZ_CONTEXT before retrying."
            )
        return (
            f"Active selection rolled back to last_good model {last_good}. "
            f"To retry the failed candidate ({failed_id}) explicitly, "
            "POST /qz/model/select-and-restart again."
            + suffix
        )
    if not last_good:
        if state.last_load_error_type == "insufficient_vram":
            return (
                f"Failed candidate {failed_id} did not fit VRAM/context and "
                "no last-good model is available. Pick a smaller model or "
                "reduce QZ_CONTEXT, then POST /qz/model/select-and-restart."
            )
        return (
            f"Failed candidate {failed_id} did not load and no last-good "
            "model is available. POST /qz/model/select-and-restart with a "
            "different model."
        )
    if state.last_load_error_type == "insufficient_vram":
        return (
            f"Failed candidate {failed_id} did not fit VRAM/context. "
            "Pick a smaller model or reduce QZ_CONTEXT, then "
            "POST /qz/model/select-and-restart."
        )
    return (
        f"Failed candidate {failed_id} did not load. Last-good available: "
        f"{last_good}. POST /qz/model/select-and-restart again to retry or "
        "with a different model."
    )


def build_model_status(handler: Any) -> dict[str, Any]:
    """Build the qz.model_status.v1 payload from the handler's runtime state."""
    state = load_handler_model_state(handler)

    selected_key = state.selected_key
    selected_backend_id = state.selected_backend_id
    selected_identity = selected_identity_from_state(state)

    configured_env_model = (os.environ.get("QZ_MODEL_KEY") or "").strip()

    catalog_entry = _resolve_catalog_entry(handler, selected_identity)
    model_visible = catalog_entry is not None
    profile_valid = True
    if catalog_entry is not None:
        profile_valid = catalog_entry.get("profile_valid", True) is not False

    backend_loaded_model = _backend_loaded_model(handler)

    selected_loaded_mismatch = bool(
        selected_identity
        and backend_loaded_model
        and not identity_matches_loaded(selected_key, selected_backend_id, backend_loaded_model)
    )

    backend_phase, backend_gpu_state = _backend_manager_state(handler)

    recommended_action = _recommended_action(
        selected_identity=selected_identity,
        backend_loaded_model=backend_loaded_model,
        selected_loaded_mismatch=selected_loaded_mismatch,
        last_load_error_type=state.last_load_error_type,
    )

    # Recovery surface: last-known-good vs. failed candidate, and a rollback
    # indicator derived from selected_reason (mark_load_failure writes a
    # canonical "rolled back from failed candidate <id>" reason).
    rollback_performed = bool(
        state.failed_candidate_key
        and state.selected_reason
        and "rolled back from failed candidate" in state.selected_reason
    )
    recovery_available = bool(state.last_good_key or state.last_good_backend_id)
    recommended_recovery_action = _recommended_recovery_action(state)

    return {
        "ok": True,
        "schema": QZ_MODEL_STATUS_SCHEMA,
        "configured_env_model": configured_env_model,
        "selected_key": selected_key,
        "selected_backend_id": selected_backend_id,
        "selected_label": state.selected_label,
        "selected_source": state.selected_source,
        "selected_at": state.selected_at,
        "backend_loaded_model": backend_loaded_model,
        "selected_loaded_mismatch": selected_loaded_mismatch,
        "model_visible": model_visible,
        "profile_valid": profile_valid,
        "backend_phase": backend_phase,
        "backend_gpu_state": backend_gpu_state,
        "last_load_result": state.last_load_result,
        "last_load_error": state.last_load_error,
        "last_load_error_type": state.last_load_error_type,
        "last_good_key": state.last_good_key,
        "last_good_backend_id": state.last_good_backend_id,
        "failed_candidate_key": state.failed_candidate_key,
        "failed_candidate_backend_id": state.failed_candidate_backend_id,
        "rollback_performed": rollback_performed,
        "recovery_available": recovery_available,
        "recommended_action": recommended_action,
        "recommended_recovery_action": recommended_recovery_action,
    }


def model_selection_hints(
    state: ModelState,
    configured_env_model: str,
    backend_loaded_model: str,
) -> list[str]:
    """Return additive operator hints derived from selection state.

    Used by /qz/control-plane to append model-selection-specific hints to the
    existing operator_hints list.
    """
    hints: list[str] = []
    selected_identity = selected_identity_from_state(state)

    if (
        configured_env_model
        and selected_identity
        and configured_env_model.strip().lower() != selected_identity.strip().lower()
        and configured_env_model.strip().lower() != (state.selected_backend_id or "").strip().lower()
    ):
        hints.append(
            "QZ_MODEL_KEY differs from persisted selection. QZ_MODEL_KEY is "
            "only a seed; the persisted proxy selection is active."
        )

    if (
        selected_identity
        and backend_loaded_model
        and not identity_matches_loaded(state.selected_key, state.selected_backend_id, backend_loaded_model)
    ):
        hints.append(
            "Selected model differs from loaded backend model. "
            "POST /qz/model/reload or /qz/model/select-and-restart to align."
        )

    if state.last_load_error_type == "insufficient_vram":
        hints.append(
            "Selected model failed to fit VRAM/context. "
            "Pick a smaller model or reduce QZ_CONTEXT."
        )

    # Failure recovery hints: rollback occurred OR failed candidate without
    # any last-good to fall back to.
    if state.last_load_result == "failed":
        failed_id = (
            state.failed_candidate_key or state.failed_candidate_backend_id or "<unknown>"
        )
        last_good = state.last_good_key or state.last_good_backend_id
        if last_good and "rolled back from failed candidate" in (state.selected_reason or ""):
            hints.append(
                f"Failed candidate {failed_id} did not load; active selection "
                f"rolled back to last-good {last_good}."
            )
        elif not last_good:
            hints.append(
                f"Failed candidate {failed_id} did not load and no last-good "
                "model is available. POST /qz/model/select-and-restart with a "
                "different model."
            )

    return hints
