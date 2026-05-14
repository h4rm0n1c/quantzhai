#!/usr/bin/env python3
"""Structured proxy-owned error payloads for /v1/responses rejections.

When /v1/responses cannot proceed (catalog not ready, model missing, backend
unreachable), the proxy returns a structured JSON payload so clients receive
actionable, proxy-owned diagnostics rather than vague error strings.

Remote qz-codex clients do not need Docker or llama.cpp access; this module
avoids any assumption about local infrastructure.

Schema: qz.responses.error.v1
"""
from typing import Any

QZ_RESPONSES_ERROR_SCHEMA = "qz.responses.error.v1"

# Old positional aliases that qz-codex now rejects. Detecting them in an error
# message lets clients know what went wrong without a separate hint path.
DEPRECATED_MODEL_ALIASES: frozenset[str] = frozenset(
    {"low", "medium", "high", "max", "caveman", "xhigh"}
)


def is_deprecated_alias(model: str) -> bool:
    """Return True if the model name is a deprecated positional alias."""
    return isinstance(model, str) and model.strip().lower() in DEPRECATED_MODEL_ALIASES


def normalize_error_code(error: str) -> str:
    """Derive a canonical snake_case error_code from a human-readable error label.

    Examples:
        "proxy not ready"        -> "proxy_not_ready"
        "model not found"        -> "model_not_found"
        "backend unavailable"    -> "backend_unavailable"
        "profile backend missing"-> "profile_backend_missing"
    """
    if not isinstance(error, str) or not error.strip():
        return ""
    return error.strip().lower().replace(" ", "_").replace("-", "_")


def build_responses_error_payload(
    error: str,
    reason: str = "",
    requested_model: str = "",
    available_models: list[str] | None = None,
    proxy_initialization: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    operator_hint: str = "",
    # Canonical service/recovery fields added in #47 slice 3.
    # All optional; existing callers that omit them continue to work unchanged.
    status_code: int | None = None,
    error_code: str = "",
    service_status: dict[str, Any] | None = None,
    recoverable: bool | None = None,
    retryable: bool | None = None,
    fatal: bool | None = None,
    operator_action: str = "",
) -> dict[str, Any]:
    """Build a qz.responses.error.v1 payload for /v1/responses rejections.

    All fields are optional beyond `error`. Callers include only what is
    meaningfully available so clients get maximum signal without fabricated data.

    Args:
        error: Short human-readable error label.
        reason: Human-readable elaboration.
        requested_model: The model ID the client requested, if known.
        available_models: List of currently visible model IDs, if cheap to obtain.
        proxy_initialization: Proxy startup snapshot from _initialization_payload().
        readiness: Dict with boolean readiness levels.
        operator_hint: Actionable guidance (remote-friendly, no Docker assumptions).

        # Canonical fields (#47 slice 3):
        status_code: HTTP status code that was / will be returned (int).
        error_code: Canonical snake_case error code; derived from `error` if empty.
        service_status: qz.service.status.v1 dict; recoverable/retryable/fatal/
            operator_action are mirrored from it when not explicitly supplied.
        recoverable: Whether the error is recoverable without operator intervention.
        retryable: Whether the client should retry (possibly after a wait).
        fatal: Whether this is an unrecoverable permanent failure.
        operator_action: Canonical operator action string (see taxonomy doc).
    """
    payload: dict[str, Any] = {
        "schema": QZ_RESPONSES_ERROR_SCHEMA,
        "error": error,
    }

    # Canonical error_code: use explicit value or derive from error string.
    _ec = error_code or normalize_error_code(error)
    if _ec:
        payload["error_code"] = _ec

    if status_code is not None:
        payload["status_code"] = status_code

    if reason:
        payload["reason"] = reason
    if requested_model:
        payload["requested_model"] = requested_model
        if is_deprecated_alias(requested_model):
            payload["alias_hint"] = (
                f"'{requested_model}' is a deprecated positional alias. "
                "Use a real model ID from /v1/models."
            )
    if available_models is not None:
        payload["available_models"] = sorted(available_models)
    if proxy_initialization is not None:
        payload["proxy_initialization"] = proxy_initialization
    if readiness is not None:
        payload["readiness"] = readiness

    # service_status: embed and mirror canonical recovery fields from it.
    if service_status is not None:
        payload["service_status"] = service_status
        _ss = service_status
        if recoverable is None:
            recoverable = _ss.get("recoverable")
        if retryable is None:
            retryable = _ss.get("retryable")
        if fatal is None:
            fatal = _ss.get("fatal")
        if not operator_action:
            operator_action = str(_ss.get("operator_action") or "")

    # Top-level canonical recovery fields (easy for clients that skip service_status).
    if recoverable is not None:
        payload["recoverable"] = recoverable
    if retryable is not None:
        payload["retryable"] = retryable
    if fatal is not None:
        payload["fatal"] = fatal
    if operator_action:
        payload["operator_action"] = operator_action

    if operator_hint:
        payload["operator_hint"] = operator_hint

    return payload
