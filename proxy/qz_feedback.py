#!/usr/bin/env python3
"""Generic feedback/signal subsystem for QuantZhai.

This module provides the data types and render helpers shared across tool
coercion, native tool-output classification, and future runtime signals.

Current scope (Phase 0-3):
  - FeedbackVisibility: who can see a signal
  - FeedbackChannel: how it is surfaced
  - SignalDecision: a classified signal with metadata
  - render_coercion_error(): canonical model-visible error function_call_output

Deferred (later phases):
  - stream/runtime signals: empty-answer repair, reasoning-only, compaction watchdog
  - repeated-read advisory
  - model injection beyond coercion errors

See docs/signal-feedback-subsystem-plan.md.

This module has no imports from other proxy modules so it can be used as a
dependency by any proxy module without creating circular imports.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum


class FeedbackVisibility(Enum):
    """Who can see this signal.

    MODEL    — surfaced to the model via function_call_output, instructions, or harness.
    OPERATOR — surfaced to the operator via telemetry / qz-thoughts / status only.
    BOTH     — surfaced through both paths; use sparingly.
    """
    MODEL = "model"
    OPERATOR = "operator"
    BOTH = "both"


class FeedbackChannel(Enum):
    """How the signal is surfaced.

    FUNCTION_CALL_OUTPUT — injected as a model-visible tool result item.
    TELEMETRY            — emitted as a telemetry event (operator-visible).
    INSTRUCTIONS         — injected into body["instructions"].
    TURN_HARNESS         — injected into the newest user turn as harness text.
    FUTURE_STATE         — future: stored in SQLite / LimbiCore (not implemented).
    """
    FUNCTION_CALL_OUTPUT = "function_call_output"
    TELEMETRY = "telemetry"
    INSTRUCTIONS = "instructions"
    TURN_HARNESS = "turn_harness"
    FUTURE_STATE = "future_state"


@dataclass(frozen=True)
class SignalDecision:
    """A classified signal with visibility and channel metadata.

    The event_type and payload fields are compatible with the existing
    TelemetryBus.emit(event_type, payload) interface so callers can emit
    directly without conversion.

    visibility and channel are advisory for the caller:
      OPERATOR / TELEMETRY  — emit event only; do not inject into model context.
      MODEL / FUNCTION_CALL_OUTPUT — emit event and inject rendered result.
    """
    event_type: str
    payload: dict
    visibility: FeedbackVisibility = FeedbackVisibility.OPERATOR
    channel: FeedbackChannel = FeedbackChannel.TELEMETRY
    confidence: str = ""


def render_advisory_output(call: dict, message: str) -> dict:
    """Build a function_call_output item carrying an advisory message for the model.

    Used for signals like repeated-read advisories. The output field is plain text,
    not a JSON {"ok": false, ...} payload — this is feedback the model can use or
    ignore, not a failure report.

    The call_id matches the original tool call so the model can associate the
    advisory with the specific invocation.
    """
    call_id = (
        call.get("call_id") or call.get("id")
        if isinstance(call, dict) else None
    ) or f"advisory_{int(time.time())}"
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": message,
    }


def render_coercion_error(call: dict, message: str) -> dict:
    """Build a function_call_output item carrying a proxy/coercion error message.

    This is the canonical model-visible error format. The output field is a
    JSON-encoded {"ok": false, "error": "..."} dict — the same shape that
    proxy tools and coercion paths already use.

    Compatibility note: proxy/qz_tools.py keeps synthesize_tool_error_result()
    with the same implementation for callers that import from there.
    Both functions produce byte-for-byte identical output.
    """
    call_id = (
        call.get("call_id") or call.get("id")
        if isinstance(call, dict) else None
    ) or f"err_{int(time.time())}"
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps({"ok": False, "error": message}, ensure_ascii=False),
    }
