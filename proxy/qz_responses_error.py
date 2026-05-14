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


def build_responses_error_payload(
    error: str,
    reason: str = "",
    requested_model: str = "",
    available_models: list[str] | None = None,
    proxy_initialization: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    operator_hint: str = "",
) -> dict[str, Any]:
    """Build a qz.responses.error.v1 payload for /v1/responses rejections.

    All fields are optional beyond `error`. Callers include only what is
    meaningfully available so clients get maximum signal without fabricated data.

    Args:
        error: Short machine-readable error label.
        reason: Human-readable elaboration.
        requested_model: The model ID the client requested, if known.
        available_models: List of currently visible model IDs, if cheap to obtain.
        proxy_initialization: Proxy startup snapshot from _initialization_payload().
        readiness: Dict with boolean readiness levels:
            proxy_ready, catalog_ready, model_visible, backend_ready.
        operator_hint: Actionable guidance for the operator/user that does not
            assume local Docker or backend access.
    """
    payload: dict[str, Any] = {
        "schema": QZ_RESPONSES_ERROR_SCHEMA,
        "error": error,
    }
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
    if operator_hint:
        payload["operator_hint"] = operator_hint
    return payload
