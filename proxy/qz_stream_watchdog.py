#!/usr/bin/env python3
"""Bounded stream watchdog helpers for #40.

Pure logic — no I/O, no threads, no sleeps.
Accepts injected time values so tests remain deterministic.

Configuration:
  QZ_STREAM_NO_OUTPUT_TIMEOUT_S — seconds before no-output timeout fires.
  Default 0 = disabled. Set to a positive value (e.g. 120) to enable.
  QZ_STREAM_TERMINAL_TIMEOUT_S — seconds after first visible output before
  terminal-after-output timeout fires. Default 0 = disabled.
  Consistent with QZ_REASONING_ONLY_TIMEOUT_S and QZ_PRIVATE_TOOL_CALL_TIMEOUT_S
  defaults in qz_responses_stream.py.

Signal compatibility:
  build_timeout_telemetry_payload() returns fields shaped for future #42
  SignalDecision wrapping without structural changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# ≤0 = disabled.  Must be explicitly enabled by operator.
# Warning: the router sends SSE events only after the first decoded token.
# For 256K context on slow models, prompt processing can take 60-180s —
# setting this too low triggers false-positive timeouts on legitimate requests.
# The zombie-slot fix (C++ log-thread EOF handler + proxy dead-child recovery)
# handles silent child deaths without a timeout, so the default is disabled.
STREAM_NO_OUTPUT_TIMEOUT_S: float = float(
    os.environ.get("QZ_STREAM_NO_OUTPUT_TIMEOUT_S", "0")
)

STREAM_TERMINAL_TIMEOUT_S: float = float(
    os.environ.get("QZ_STREAM_TERMINAL_TIMEOUT_S", "0")
)


@dataclass
class StreamWatchdogState:
    """Per-hop mutable watchdog state. One instance per upstream hop.

    Callers update the mark_* methods as stream events arrive.
    No threads, no background workers.
    """

    timeout_secs: float          # ≤0 means no-output watchdog is disabled
    started_at: float            # time() when the upstream connection was opened
    terminal_timeout_secs: float = 0.0  # ≤0 means terminal watchdog is disabled

    first_event_at: float | None = None
    first_visible_output_at: float | None = None  # output_text or public assistant item
    first_terminal_at: float | None = None        # response.completed / [DONE] forwarded
    triggered: bool = False                        # True once timeout fires

    @property
    def disabled(self) -> bool:
        return self.timeout_secs <= 0

    def mark_event(self, now: float) -> None:
        """Record that any upstream event has arrived."""
        if self.first_event_at is None:
            self.first_event_at = now

    def mark_visible_output(self, now: float) -> None:
        """Record that visible output (output_text or public item) was seen."""
        if self.first_visible_output_at is None:
            self.first_visible_output_at = now

    def mark_terminal(self, now: float) -> None:
        """Record that a terminal event (response.completed / [DONE]) was forwarded."""
        if self.first_terminal_at is None:
            self.first_terminal_at = now

    def elapsed_secs(self, now: float) -> float:
        """Elapsed seconds since first upstream event, or since stream start."""
        reference = self.first_event_at if self.first_event_at is not None else self.started_at
        return max(0.0, now - reference)

    def terminal_elapsed_secs(self, now: float) -> float:
        """Elapsed seconds since first visible output."""
        if self.first_visible_output_at is None:
            return 0.0
        return max(0.0, now - self.first_visible_output_at)


def should_trigger_no_output_timeout(state: StreamWatchdogState, now: float) -> bool:
    """Return True if the no-output watchdog should fire now.

    Definition: fires when timeout_secs > 0 and no visible output and no
    terminal event and elapsed >= timeout_secs.

    Does NOT fire if:
    - disabled (timeout_secs ≤ 0)
    - already triggered
    - visible output has been seen (output_text or public assistant item)
    - terminal event has been forwarded (response.completed or [DONE])
    """
    if state.disabled or state.triggered:
        return False
    if state.first_visible_output_at is not None:
        return False
    if state.first_terminal_at is not None:
        return False
    return state.elapsed_secs(now) >= state.timeout_secs


def should_trigger_terminal_timeout(state: StreamWatchdogState, now: float) -> bool:
    """Return True if terminal-after-output watchdog should fire now.

    Definition: fires when terminal_timeout_secs > 0, visible output has been
    seen, no terminal event has been forwarded, and elapsed time since first
    visible output is >= terminal_timeout_secs.

    This is intentionally separate from should_trigger_no_output_timeout:
    - no-output timeout means no visible answer ever appeared
    - terminal timeout means visible output appeared but the terminal event did
      not arrive before the deadline
    """
    if state.terminal_timeout_secs <= 0 or state.triggered:
        return False
    if state.first_visible_output_at is None:
        return False
    if state.first_terminal_at is not None:
        return False
    return state.terminal_elapsed_secs(now) >= state.terminal_timeout_secs


def stream_timeout_kind(state: StreamWatchdogState, now: float) -> str | None:
    """Return which timeout kind should fire now, or None if neither should.

    Pure helper — no I/O, no state mutation, no action side effects.
    Returns "no_output", "terminal", or None.
    No-output has priority over terminal to preserve the inline check order.
    """
    if should_trigger_no_output_timeout(state, now):
        return "no_output"
    if should_trigger_terminal_timeout(state, now):
        return "terminal"
    return None


def build_timeout_telemetry_payload(
    state: StreamWatchdogState,
    terminal: dict,
    now: float,
    request_id: str = "",
) -> dict:
    """Build a signal-compatible telemetry payload for stream_no_output_timeout.

    Shaped to be wrappable by a future #42 SignalDecision without structural
    changes — includes signal_id, source, channel, visibility, action.

    Args:
        state:      current watchdog state (has timing data)
        terminal:   result of classify_stream_terminal(obs) already computed
        now:        current time (injected for testability)
        request_id: request id from caller (overrides obs if present)
    """
    return {
        # Core classification
        "schema":                 terminal.get("schema", "qz.stream.terminal.v1"),
        "classification":         "stream_no_output_timeout",
        # Signal-compatible fields (future #42 SignalDecision wrapping)
        "signal_id":              "stream.no_output_timeout",
        "source":                 "qz_stream_watchdog",
        "channel":                "telemetry",
        "visibility":             "operator",
        "action":                 "fallback",
        # Timing
        "timeout_secs":           state.timeout_secs,
        "elapsed_secs":           round(state.elapsed_secs(now), 3),
        # Context
        "request_id":             request_id or terminal.get("request_id", ""),
        # Classification outcome
        "recoverable":            terminal.get("recoverable", True),
        "fallback_required":      terminal.get("fallback_required", True),
        "visible_answer_seen":    terminal.get("visible_answer_seen", False),
        "terminal_event_seen":    terminal.get("terminal_event_seen", False),
        # Observation booleans
        "saw_reasoning":          terminal.get("saw_reasoning", False),
        "saw_output_text":        terminal.get("saw_output_text", False),
        "saw_tool_call":          terminal.get("saw_tool_call", False),
        "saw_response_completed": terminal.get("saw_response_completed", False),
        "saw_done":               terminal.get("saw_done", False),
        "saw_error":              terminal.get("saw_error", False),
        "saw_compact_failed":     terminal.get("saw_compact_failed", False),
        "fallback_emitted":       terminal.get("fallback_emitted", False),
        "repair_emitted":         terminal.get("repair_emitted", False),
        "notes":                  list(terminal.get("notes", [])),
    }


def build_terminal_timeout_telemetry_payload(
    state: StreamWatchdogState,
    terminal: dict,
    now: float,
    request_id: str = "",
) -> dict:
    """Build signal-compatible telemetry for stream_terminal_timeout."""
    return {
        # Core classification
        "schema":                 terminal.get("schema", "qz.stream.terminal.v1"),
        "classification":         "stream_terminal_timeout",
        # Signal-compatible fields (future #42 SignalDecision wrapping)
        "signal_id":              "stream.terminal_timeout",
        "source":                 "qz_stream_watchdog",
        "channel":                "telemetry",
        "visibility":             "operator",
        "action":                 "terminal_fallback",
        # Timing
        "timeout_secs":           state.terminal_timeout_secs,
        "elapsed_secs":           round(state.terminal_elapsed_secs(now), 3),
        # Context
        "request_id":             request_id or terminal.get("request_id", ""),
        # Classification outcome
        "recoverable":            terminal.get("recoverable", True),
        "fallback_required":      terminal.get("fallback_required", True),
        "visible_answer_seen":    terminal.get("visible_answer_seen", True),
        "terminal_event_seen":    terminal.get("terminal_event_seen", False),
        # Observation booleans
        "saw_reasoning":          terminal.get("saw_reasoning", False),
        "saw_output_text":        terminal.get("saw_output_text", False),
        "saw_assistant_item":     terminal.get("saw_assistant_item", False),
        "saw_tool_call":          terminal.get("saw_tool_call", False),
        "saw_response_completed": terminal.get("saw_response_completed", False),
        "saw_done":               terminal.get("saw_done", False),
        "saw_error":              terminal.get("saw_error", False),
        "saw_compact_failed":     terminal.get("saw_compact_failed", False),
        "fallback_emitted":       terminal.get("fallback_emitted", False),
        "repair_emitted":         terminal.get("repair_emitted", False),
        "notes":                  list(terminal.get("notes", [])),
    }
