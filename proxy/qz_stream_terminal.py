#!/usr/bin/env python3
"""Stream terminal classification seam for #40.

Classifies stream outcomes from collected boolean observations.
Pure logic — no I/O, no timers, no threads, no state mutation.

Source/confidence vocabulary follows docs/patterns/provenance-telemetry.md.

Slice 1: classification and fixture coverage.
Slice 2 (future): real-time watchdog / timeout trigger.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STREAM_TERMINAL_SCHEMA = "qz.stream.terminal.v1"


@dataclass
class StreamObservation:
    """Boolean summary of stream events collected during one upstream hop.

    All fields default to False/0/"". Callers populate what they observed.
    """

    # --- event presence flags ---
    saw_reasoning: bool = False          # any reasoning_text / reasoning_summary delta
    saw_output_text: bool = False        # any non-whitespace output_text.delta
    saw_assistant_item: bool = False     # valid public non-tool item done
    saw_tool_call: bool = False          # completed function_call
    saw_response_completed: bool = False # response.completed event forwarded
    saw_done: bool = False               # [DONE] token forwarded
    saw_error: bool = False              # response.failed / response.cancelled
    saw_compact_started: bool = False    # compact-handoff started signal
    saw_compact_failed: bool = False     # compact task failed / session stuck
    saw_protocol_drift_event: bool = False  # unknown/newer item-delta event accepted

    # --- outcome flags ---
    fallback_emitted: bool = False       # proxy emitted a synthetic fallback message
    repair_emitted: bool = False         # empty-answer repair hop triggered

    # --- timeout flag (reserved; set by watchdog in slice 2) ---
    output_timeout: bool = False         # stream produced no output within deadline

    # --- context ---
    request_id: str = ""


def classify_stream_terminal(obs: StreamObservation) -> dict:
    """Return a qz.stream.terminal.v1 classification dict.

    Rules (in priority order):

    output_timeout            → stream_no_output_timeout  (watchdog-triggered)
    compact_failed            → compact_failed
    error + no recovery       → unrecoverable
    fallback_emitted          → fallback_emitted
    repair + answer + terminal → repaired
    answer + terminal + drift → protocol_drift_seen
    answer + terminal         → ok
    terminal + no answer      → stream_completed_without_visible_answer
    activity + no terminal    → stream_terminal_missing
    nothing                   → stream_terminal_missing

    Never raises. Returns a JSON-serialisable dict.
    """
    visible_answer = obs.saw_output_text or obs.saw_assistant_item
    terminal_seen = obs.saw_response_completed or obs.saw_done

    # --- timeout (reserved for slice 2 watchdog) ---
    if obs.output_timeout:
        return _result(
            "stream_no_output_timeout",
            recoverable=True,
            fallback_required=True,
            visible_answer_seen=visible_answer,
            terminal_event_seen=terminal_seen,
            obs=obs,
            notes=["stream produced no output before timeout deadline; watchdog-triggered"],
        )

    # --- compact failure ---
    if obs.saw_compact_failed:
        return _result(
            "compact_failed",
            recoverable=True,
            fallback_required=True,
            visible_answer_seen=visible_answer,
            terminal_event_seen=terminal_seen,
            obs=obs,
            notes=["compact task failed; session may be stuck"],
        )

    # --- error with no recovery path ---
    if obs.saw_error and not terminal_seen and not visible_answer and not obs.fallback_emitted:
        return _result(
            "unrecoverable",
            recoverable=False,
            fallback_required=True,
            visible_answer_seen=False,
            terminal_event_seen=False,
            obs=obs,
            notes=["error event received without terminal event or visible answer"],
        )

    # --- fallback explicitly emitted by proxy ---
    if obs.fallback_emitted:
        return _result(
            "fallback_emitted",
            recoverable=True,
            fallback_required=False,
            visible_answer_seen=visible_answer,
            terminal_event_seen=terminal_seen,
            obs=obs,
            notes=["proxy emitted fallback message"],
        )

    # --- repair hop completed successfully ---
    if obs.repair_emitted and visible_answer and terminal_seen:
        return _result(
            "repaired",
            recoverable=True,
            fallback_required=False,
            visible_answer_seen=True,
            terminal_event_seen=True,
            obs=obs,
        )

    # --- visible answer and terminal event ---
    if visible_answer and terminal_seen:
        if obs.saw_protocol_drift_event:
            return _result(
                "protocol_drift_seen",
                recoverable=True,
                fallback_required=False,
                visible_answer_seen=True,
                terminal_event_seen=True,
                obs=obs,
                notes=["protocol drift event observed but stream completed normally"],
            )
        return _result(
            "ok",
            recoverable=False,
            fallback_required=False,
            visible_answer_seen=True,
            terminal_event_seen=True,
            obs=obs,
        )

    # --- terminal seen but no visible answer ---
    if terminal_seen and not visible_answer:
        return _result(
            "stream_completed_without_visible_answer",
            recoverable=True,
            fallback_required=True,
            visible_answer_seen=False,
            terminal_event_seen=True,
            obs=obs,
            notes=["stream completed without user-visible output_text or public item"],
        )

    # --- had some activity but no terminal event ---
    any_activity = obs.saw_reasoning or visible_answer or obs.saw_tool_call or obs.saw_compact_started
    if any_activity:
        return _result(
            "stream_terminal_missing",
            recoverable=True,
            fallback_required=True,
            visible_answer_seen=visible_answer,
            terminal_event_seen=False,
            obs=obs,
            notes=["stream had activity but no terminal event before close"],
        )

    # --- nothing at all ---
    return _result(
        "stream_terminal_missing",
        recoverable=True,
        fallback_required=True,
        visible_answer_seen=False,
        terminal_event_seen=False,
        obs=obs,
        notes=["no useful stream activity observed before close"],
    )


def _result(
    classification: str,
    *,
    recoverable: bool,
    fallback_required: bool,
    visible_answer_seen: bool,
    terminal_event_seen: bool,
    obs: StreamObservation,
    notes: list | None = None,
) -> dict:
    return {
        "schema":              STREAM_TERMINAL_SCHEMA,
        "classification":      classification,
        "recoverable":         recoverable,
        "fallback_required":   fallback_required,
        "visible_answer_seen": visible_answer_seen,
        "terminal_event_seen": terminal_event_seen,
        "saw_reasoning":       obs.saw_reasoning,
        "saw_output_text":     obs.saw_output_text,
        "saw_tool_call":       obs.saw_tool_call,
        "saw_response_completed": obs.saw_response_completed,
        "saw_done":            obs.saw_done,
        "saw_error":           obs.saw_error,
        "saw_compact_failed":  obs.saw_compact_failed,
        "fallback_emitted":    obs.fallback_emitted,
        "repair_emitted":      obs.repair_emitted,
        "request_id":          obs.request_id,
        "notes":               list(notes or []),
    }


def observation_from_event_type(event_type: str | None, payload) -> dict:
    """Return a partial-observation dict for a single SSE event.

    Used by callers that accumulate observations event-by-event.
    Returns a dict of (field_name, bool) pairs to OR into an accumulator.

    Supports both legacy and newer item/content delta shapes:
    - legacy: response.output_text.delta (current Responses API)
    - newer:  response.output_item.content.delta (protocol drift variant)
    """
    obs: dict[str, bool] = {}

    if event_type is None:
        return obs

    # Reasoning events
    if event_type in {
        "response.reasoning_text.delta",
        "response.reasoning_summary_text.delta",
    }:
        obs["saw_reasoning"] = True

    # Output text — legacy delta shape
    if event_type == "response.output_text.delta":
        delta = (payload or {}).get("delta") if isinstance(payload, dict) else None
        if isinstance(delta, str) and delta.strip():
            obs["saw_output_text"] = True

    # Newer item/content delta shape (protocol drift tolerance)
    if event_type in {
        "response.output_item.content.delta",
        "response.content_part.delta",
    }:
        delta = (payload or {}).get("delta") if isinstance(payload, dict) else None
        text = (delta or {}).get("text") if isinstance(delta, dict) else delta
        if isinstance(text, str) and text.strip():
            obs["saw_output_text"] = True
        obs["saw_protocol_drift_event"] = True

    # Valid public item done (assistant/non-tool)
    if event_type == "response.output_item.done" and isinstance(payload, dict):
        item = payload.get("item") or {}
        if (
            isinstance(item, dict)
            and item.get("type") == "message"
            and item.get("status") not in {"in_progress", "incomplete"}
        ):
            obs["saw_assistant_item"] = True

    # Function call
    if event_type in {
        "response.function_call_arguments.done",
        "response.output_item.done",
    } and isinstance(payload, dict):
        item = payload.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "function_call":
            obs["saw_tool_call"] = True

    # Terminal events
    if event_type == "response.completed":
        obs["saw_response_completed"] = True
    if event_type in {"response.failed", "response.cancelled", "response.incomplete"}:
        obs["saw_error"] = True
    if event_type == "done":
        obs["saw_done"] = True

    # Compact signals — synthetic or future upstream events
    if event_type in {
        "response.compact.started",
        "session.compact.started",
    }:
        obs["saw_compact_started"] = True
    if event_type in {
        "response.compact.failed",
        "session.compact.failed",
        "session.compact.error",
    }:
        obs["saw_compact_failed"] = True

    return obs


def accumulate(acc: dict, partial: dict) -> dict:
    """OR a partial observation dict into an accumulator."""
    for key, val in partial.items():
        if val:
            acc[key] = True
    return acc


def observation_from_dict(d: dict) -> StreamObservation:
    """Construct a StreamObservation from an accumulated dict."""
    return StreamObservation(
        saw_reasoning=bool(d.get("saw_reasoning")),
        saw_output_text=bool(d.get("saw_output_text")),
        saw_assistant_item=bool(d.get("saw_assistant_item")),
        saw_tool_call=bool(d.get("saw_tool_call")),
        saw_response_completed=bool(d.get("saw_response_completed")),
        saw_done=bool(d.get("saw_done")),
        saw_error=bool(d.get("saw_error")),
        saw_compact_started=bool(d.get("saw_compact_started")),
        saw_compact_failed=bool(d.get("saw_compact_failed")),
        saw_protocol_drift_event=bool(d.get("saw_protocol_drift_event")),
        fallback_emitted=bool(d.get("fallback_emitted")),
        repair_emitted=bool(d.get("repair_emitted")),
        output_timeout=bool(d.get("output_timeout")),
        request_id=str(d.get("request_id") or ""),
    )
