"""Tests for proxy/qz_stream_watchdog.py — stream no-output watchdog (#40 slice 2).

All tests are deterministic — no wall-clock sleeps. Time is injected.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_stream_watchdog import (
    StreamWatchdogState,
    build_timeout_telemetry_payload,
    should_trigger_no_output_timeout,
)
from proxy.qz_stream_terminal import (
    STREAM_TERMINAL_SCHEMA,
    StreamObservation,
    classify_stream_terminal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(timeout_secs: float, started_at: float = 0.0, **kwargs) -> StreamWatchdogState:
    s = StreamWatchdogState(timeout_secs=timeout_secs, started_at=started_at)
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _obs(**kwargs) -> StreamObservation:
    return StreamObservation(**kwargs)


# ---------------------------------------------------------------------------
# 1. Disabled watchdog
# ---------------------------------------------------------------------------

class DisabledWatchdogTests(unittest.TestCase):
    def test_zero_timeout_never_triggers(self):
        state = _state(timeout_secs=0.0, started_at=0.0)
        state.mark_event(1.0)
        self.assertFalse(should_trigger_no_output_timeout(state, now=9999.0))

    def test_negative_timeout_never_triggers(self):
        state = _state(timeout_secs=-1.0, started_at=0.0)
        state.mark_event(1.0)
        self.assertFalse(should_trigger_no_output_timeout(state, now=9999.0))

    def test_disabled_property(self):
        self.assertTrue(_state(timeout_secs=0.0).disabled)
        self.assertTrue(_state(timeout_secs=-5.0).disabled)
        self.assertFalse(_state(timeout_secs=1.0).disabled)

    def test_already_triggered_does_not_re_trigger(self):
        state = _state(timeout_secs=10.0, started_at=0.0, triggered=True)
        state.mark_event(1.0)
        self.assertFalse(should_trigger_no_output_timeout(state, now=999.0))


# ---------------------------------------------------------------------------
# 2. Trigger: no output before deadline
# ---------------------------------------------------------------------------

class TriggerTests(unittest.TestCase):
    def test_triggers_when_elapsed_exceeds_timeout(self):
        state = _state(timeout_secs=5.0, started_at=0.0)
        state.mark_event(1.0)   # first event at t=1
        # t=6 → elapsed from first_event=5 → exactly at deadline → trigger
        self.assertTrue(should_trigger_no_output_timeout(state, now=6.0))

    def test_does_not_trigger_before_deadline(self):
        state = _state(timeout_secs=10.0, started_at=0.0)
        state.mark_event(0.0)
        self.assertFalse(should_trigger_no_output_timeout(state, now=9.9))

    def test_triggers_exactly_at_deadline(self):
        state = _state(timeout_secs=10.0, started_at=0.0)
        state.mark_event(0.0)
        self.assertTrue(should_trigger_no_output_timeout(state, now=10.0))

    def test_uses_first_event_as_reference_not_start(self):
        # started_at=0, first_event_at=5; timeout=10 → triggers at 15, not 10
        state = _state(timeout_secs=10.0, started_at=0.0)
        state.mark_event(5.0)
        self.assertFalse(should_trigger_no_output_timeout(state, now=14.9))
        self.assertTrue(should_trigger_no_output_timeout(state, now=15.0))

    def test_uses_started_at_when_no_event_seen(self):
        # No mark_event called; reference = started_at
        state = _state(timeout_secs=10.0, started_at=0.0)
        self.assertFalse(should_trigger_no_output_timeout(state, now=9.9))
        self.assertTrue(should_trigger_no_output_timeout(state, now=10.0))

    def test_reasoning_only_stream_triggers_when_no_visible_output(self):
        # Reasoning deltas arrived but no output_text → still no visible output
        # → watchdog fires if time exceeds deadline
        state = _state(timeout_secs=5.0, started_at=0.0)
        state.mark_event(0.0)
        # reasoning doesn't call mark_visible_output → no visible output
        self.assertTrue(should_trigger_no_output_timeout(state, now=5.0))


# ---------------------------------------------------------------------------
# 3. Suppression: visible output or terminal prevents trigger
# ---------------------------------------------------------------------------

class SuppressionTests(unittest.TestCase):
    def test_visible_output_prevents_trigger(self):
        state = _state(timeout_secs=5.0, started_at=0.0)
        state.mark_event(0.0)
        state.mark_visible_output(1.0)  # output at t=1
        # Even at t=100, timeout should NOT fire
        self.assertFalse(should_trigger_no_output_timeout(state, now=100.0))

    def test_terminal_prevents_trigger(self):
        state = _state(timeout_secs=5.0, started_at=0.0)
        state.mark_event(0.0)
        state.mark_terminal(2.0)       # terminal at t=2
        self.assertFalse(should_trigger_no_output_timeout(state, now=100.0))

    def test_mark_visible_output_only_once(self):
        state = _state(timeout_secs=5.0, started_at=0.0)
        state.mark_event(0.0)
        state.mark_visible_output(1.0)
        state.mark_visible_output(2.0)  # second call is no-op
        self.assertEqual(state.first_visible_output_at, 1.0)

    def test_mark_terminal_only_once(self):
        state = _state(timeout_secs=5.0, started_at=0.0)
        state.mark_event(0.0)
        state.mark_terminal(1.0)
        state.mark_terminal(2.0)
        self.assertEqual(state.first_terminal_at, 1.0)


# ---------------------------------------------------------------------------
# 4. elapsed_secs
# ---------------------------------------------------------------------------

class ElapsedSecsTests(unittest.TestCase):
    def test_elapsed_from_first_event(self):
        state = _state(timeout_secs=10.0, started_at=0.0)
        state.mark_event(5.0)
        self.assertAlmostEqual(state.elapsed_secs(now=7.0), 2.0)

    def test_elapsed_from_started_at_when_no_event(self):
        state = _state(timeout_secs=10.0, started_at=3.0)
        self.assertAlmostEqual(state.elapsed_secs(now=8.0), 5.0)

    def test_elapsed_clamped_at_zero(self):
        state = _state(timeout_secs=10.0, started_at=10.0)
        self.assertAlmostEqual(state.elapsed_secs(now=0.0), 0.0)


# ---------------------------------------------------------------------------
# 5. build_timeout_telemetry_payload
# ---------------------------------------------------------------------------

class BuildTimeoutTelemetryPayloadTests(unittest.TestCase):
    def _base(self):
        obs = _obs(saw_reasoning=True, output_timeout=True, request_id="req-1")
        terminal = classify_stream_terminal(obs)
        state = _state(timeout_secs=30.0, started_at=0.0)
        state.mark_event(1.0)
        return obs, terminal, state

    def test_classification_is_stream_no_output_timeout(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=31.0, request_id="req-1")
        self.assertEqual(payload["classification"], "stream_no_output_timeout")

    def test_signal_compatible_fields_present(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=31.0, request_id="req-1")
        for field in ("signal_id", "source", "channel", "visibility", "action"):
            self.assertIn(field, payload, f"missing signal field: {field}")
        self.assertEqual(payload["signal_id"], "stream.no_output_timeout")
        self.assertEqual(payload["source"], "qz_stream_watchdog")
        self.assertEqual(payload["channel"], "telemetry")
        self.assertEqual(payload["visibility"], "operator")
        self.assertEqual(payload["action"], "fallback")

    def test_timing_fields(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=31.0)
        self.assertEqual(payload["timeout_secs"], 30.0)
        self.assertAlmostEqual(payload["elapsed_secs"], 30.0, places=1)

    def test_request_id_propagated(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=5.0, request_id="my-req")
        self.assertEqual(payload["request_id"], "my-req")

    def test_observation_booleans_present(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=31.0)
        for field in (
            "saw_reasoning", "saw_output_text", "saw_tool_call",
            "saw_response_completed", "saw_done", "saw_error", "saw_compact_failed",
            "fallback_emitted", "repair_emitted",
        ):
            self.assertIn(field, payload, f"missing observation field: {field}")

    def test_recoverable_and_fallback_required(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=5.0)
        self.assertTrue(payload["recoverable"])
        self.assertTrue(payload["fallback_required"])

    def test_json_serialisable(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=31.0, request_id="r")
        json.dumps(payload)

    def test_schema_field_present(self):
        obs, terminal, state = self._base()
        payload = build_timeout_telemetry_payload(state, terminal, now=5.0)
        self.assertEqual(payload["schema"], STREAM_TERMINAL_SCHEMA)


# ---------------------------------------------------------------------------
# 6. Integration with classify_stream_terminal
# ---------------------------------------------------------------------------

class WatchdogClassificationIntegrationTests(unittest.TestCase):
    def test_timeout_obs_classifies_stream_no_output_timeout(self):
        obs = _obs(output_timeout=True)
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "stream_no_output_timeout")
        self.assertTrue(result["recoverable"])
        self.assertTrue(result["fallback_required"])

    def test_compact_failed_still_wins_over_non_timeout(self):
        # If compact_failed is set but output_timeout is not, compact_failed wins
        obs = _obs(saw_compact_failed=True)
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "compact_failed")

    def test_timeout_wins_over_compact_failed_when_timeout_set(self):
        # output_timeout has highest priority in classify_stream_terminal
        obs = _obs(output_timeout=True, saw_compact_failed=True)
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "stream_no_output_timeout")

    def test_reasoning_only_with_timeout_classifies_timeout(self):
        obs = _obs(saw_reasoning=True, output_timeout=True)
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "stream_no_output_timeout")

    def test_ok_stream_no_timeout_triggered(self):
        obs = _obs(saw_output_text=True, saw_done=True)
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "ok")
        self.assertFalse(result.get("output_timeout", False))


# ---------------------------------------------------------------------------
# 7. ResponsesStreamRuntime integration: watchdog fires on stream events
# ---------------------------------------------------------------------------

class WatchdogStreamRuntimeTests(unittest.TestCase):
    """Tests that watchdog integrates with the blocking stream read seam."""

    def _make_runtime(self, timeout_s: float, events: list, *, stall_after_eof=False, telemetry=None):
        from proxy.qz_responses_stream import ResponsesStreamRuntime

        # Collect written chunks
        written = []

        class FakeStream:
            def __init__(self, lines):
                self._lines = list(lines)
                self._idx = 0

            def readline(self):
                if self._idx >= len(self._lines):
                    if stall_after_eof:
                        raise TimeoutError("simulated upstream read timeout")
                    return b""
                line = self._lines[self._idx]
                self._idx += 1
                return line

            def close(self):
                pass

        def stream_opener(body):
            return FakeStream(events)

        runtime = ResponsesStreamRuntime(
            upstream="http://localhost:1",
            authorization="Bearer test",
            reasoning_stream_format="native",
            web_runtime=None,
            chunk_writer=written.append,
            stream_opener=stream_opener,
            capture_enabled=False,
            telemetry=telemetry,
            request_id="watchdog-test",
            stream_no_output_timeout_s=timeout_s,
        )
        return runtime, written

    def _no_output_hang_events(self):
        """Events that look like a stalled stream: only created/in_progress, no output."""
        import json
        lines = []
        events = [
            ("response.created",     {"response": {"id": "hang", "object": "response", "created_at": 0, "status": "in_progress", "model": "f", "output": []},"type": "response.created", "sequence_number": 1}),
            ("response.in_progress", {"response": {"id": "hang", "object": "response", "created_at": 0, "status": "in_progress", "model": "f", "output": []},"type": "response.in_progress", "sequence_number": 2}),
        ]
        for etype, payload in events:
            lines.append(f"event: {etype}\n".encode())
            lines.append(f"data: {json.dumps(payload)}\n".encode())
            lines.append(b"\n")
        return lines

    def test_disabled_watchdog_does_not_fire(self):
        """With timeout=0, no-output stream completes without timeout fallback."""
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        runtime, written = self._make_runtime(timeout_s=0.0, events=self._no_output_hang_events())
        result = runtime.run({"model": "fixture", "input": []}, "fixture")
        # Should complete (possibly empty) without timeout fallback
        combined = b"".join(written)
        # No timeout telemetry emitted (checked via result)
        terminal = result.get("stream_terminal")
        if terminal:
            self.assertNotEqual(terminal.get("classification"), "stream_no_output_timeout")

    def test_watchdog_fires_when_upstream_read_stalls_after_in_progress(self):
        """A read timeout after response.in_progress emits the timeout fallback."""
        from proxy.qz_telemetry import TelemetryBus

        telemetry = TelemetryBus()
        runtime, written = self._make_runtime(
            timeout_s=1.0,
            events=self._no_output_hang_events(),
            stall_after_eof=True,
            telemetry=telemetry,
        )

        result = runtime.run({"model": "fixture", "input": []}, "fixture")
        combined = b"".join(written)
        terminal = result.get("stream_terminal") or {}
        stream_completed = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_completed"
        ]
        terminal_classified = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_terminal_classified"
        ]

        self.assertTrue(result["fallback"])
        self.assertEqual(terminal.get("classification"), "stream_no_output_timeout")
        self.assertIn(b"no-output timeout", combined)
        self.assertIn(b"event: response.completed", combined)
        self.assertTrue(combined.endswith(b"data: [DONE]\n\n"))
        self.assertTrue(any(event.get("payload", {}).get("fallback") is True for event in stream_completed))
        self.assertEqual(len(terminal_classified), 1)

    def test_watchdog_timeout_path_reachable(self):
        """Verify the timeout fallback path produces the right structure.

        Tests by directly calling _emit_no_output_timeout_fallback via a
        pre-configured watchdog state, bypassing the full stream loop timing.
        """
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        from proxy.qz_stream_terminal import StreamObservation, classify_stream_terminal
        from proxy.qz_stream_watchdog import (
            StreamWatchdogState, build_timeout_telemetry_payload,
        )

        written = []
        runtime = ResponsesStreamRuntime(
            upstream="http://localhost:1",
            authorization="Bearer test",
            reasoning_stream_format="native",
            web_runtime=None,
            chunk_writer=written.append,
            capture_enabled=False,
            stream_no_output_timeout_s=10.0,
        )

        obs = StreamObservation(saw_reasoning=True, output_timeout=True, request_id="r1")
        watchdog_state = StreamWatchdogState(timeout_secs=10.0, started_at=0.0)
        watchdog_state.mark_event(1.0)
        terminal = classify_stream_terminal(obs)

        # Call the fallback method directly — verifies it runs without crashing
        items, seq = runtime._emit_no_output_timeout_fallback(
            requested_model="fixture",
            summary_started=set(),
            final_usage={},
            watchdog_state=watchdog_state,
            now=11.0,
            obs=obs,
            public_index=0,
            sequence=0,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "message")
        # Should have written SSE events
        combined = b"".join(written)
        self.assertIn(b"no visible output", combined)
        # Telemetry payload structure
        payload = build_timeout_telemetry_payload(watchdog_state, terminal, now=11.0, request_id="r1")
        self.assertEqual(payload["classification"], "stream_no_output_timeout")
        self.assertEqual(payload["signal_id"], "stream.no_output_timeout")

    def test_watchdog_does_not_fire_when_output_seen(self):
        """Watchdog must not fire when visible output is present."""
        import json
        lines = []
        events_data = [
            ("response.created",     {"response": {"id": "ok", "object": "response", "created_at": 0, "status": "in_progress", "model": "f", "output": []},"type": "response.created","sequence_number":1}),
            ("response.in_progress", {"response": {"id": "ok", "object": "response", "created_at": 0, "status": "in_progress", "model": "f", "output": []},"type": "response.in_progress","sequence_number":2}),
            ("response.output_item.added", {"output_index":0,"item":{"id":"msg_ok","type":"message","status":"in_progress","role":"assistant","content":[]},"type":"response.output_item.added","sequence_number":3}),
            ("response.output_text.delta", {"item_id":"msg_ok","output_index":0,"content_index":0,"delta":"hello","type":"response.output_text.delta","sequence_number":4}),
            ("response.output_item.done",  {"output_index":0,"item":{"id":"msg_ok","type":"message","status":"completed","role":"assistant","content":[{"type":"output_text","text":"hello","annotations":[]}]},"type":"response.output_item.done","sequence_number":5}),
            ("response.completed",  {"response":{"id":"ok","object":"response","created_at":0,"status":"completed","model":"f","output":[{"id":"msg_ok","type":"message","status":"completed","role":"assistant","content":[{"type":"output_text","text":"hello","annotations":[]}]}],"usage":{}},"type":"response.completed","sequence_number":6}),
        ]
        for etype, payload in events_data:
            lines.append(f"event: {etype}\n".encode())
            lines.append(f"data: {json.dumps(payload)}\n".encode())
            lines.append(b"\n")
        lines.append(b"data: [DONE]\n\n")

        runtime, written = self._make_runtime(timeout_s=10.0, events=lines)
        result = runtime.run({"model": "fixture", "input": []}, "fixture")
        terminal = result.get("stream_terminal")
        if terminal:
            self.assertNotEqual(terminal.get("classification"), "stream_no_output_timeout")

    def test_read_timeout_is_restored_after_visible_output(self):
        """A later would-be read timeout after visible output is not a no-output timeout."""
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        from proxy.qz_telemetry import TelemetryBus

        lines = []
        events_data = [
            ("response.created", {"response": {"id": "ok", "object": "response", "created_at": 0, "status": "in_progress", "model": "f", "output": []}, "type": "response.created", "sequence_number": 1}),
            ("response.in_progress", {"response": {"id": "ok", "object": "response", "created_at": 0, "status": "in_progress", "model": "f", "output": []}, "type": "response.in_progress", "sequence_number": 2}),
            ("response.output_text.delta", {"item_id": "msg_ok", "output_index": 0, "content_index": 0, "delta": "hello", "type": "response.output_text.delta", "sequence_number": 3}),
        ]
        for etype, payload in events_data:
            lines.append(f"event: {etype}\n".encode())
            lines.append(f"data: {json.dumps(payload)}\n".encode())
            lines.append(b"\n")

        class FakeStream:
            def __init__(self, stream_lines):
                self._lines = list(stream_lines)
                self._idx = 0
                self.timeout = None
                self.timeout_calls = []

            def gettimeout(self):
                return self.timeout

            def settimeout(self, timeout):
                self.timeout = timeout
                self.timeout_calls.append(timeout)

            def readline(self):
                if self._idx >= len(self._lines):
                    if self.timeout is not None:
                        raise TimeoutError("simulated read timeout after visible output")
                    return b""
                line = self._lines[self._idx]
                self._idx += 1
                return line

            def close(self):
                pass

        streams = []
        telemetry = TelemetryBus()

        def stream_opener(body):
            stream = FakeStream(lines)
            streams.append(stream)
            return stream

        written = []
        runtime = ResponsesStreamRuntime(
            upstream="http://localhost:1",
            authorization="Bearer test",
            reasoning_stream_format="native",
            web_runtime=None,
            chunk_writer=written.append,
            stream_opener=stream_opener,
            capture_enabled=False,
            telemetry=telemetry,
            request_id="watchdog-visible-output-test",
            stream_no_output_timeout_s=1.0,
        )

        result = runtime.run({"model": "fixture", "input": []}, "fixture")
        terminal = result.get("stream_terminal") or {}
        terminal_classified = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_terminal_classified"
        ]

        self.assertFalse(result["fallback"])
        self.assertNotEqual(terminal.get("classification"), "stream_no_output_timeout")
        self.assertFalse(any(
            event.get("payload", {}).get("classification") == "stream_no_output_timeout"
            for event in terminal_classified
        ))
        self.assertEqual(streams[0].timeout_calls, [1.0, None])


# ---------------------------------------------------------------------------
# 8. No infinite loop: watchdog fires once only
# ---------------------------------------------------------------------------

class NoInfiniteLoopTests(unittest.TestCase):
    def test_triggered_flag_prevents_re_trigger(self):
        state = _state(timeout_secs=1.0, started_at=0.0)
        state.mark_event(0.0)
        # Trigger fires
        self.assertTrue(should_trigger_no_output_timeout(state, now=1.0))
        # Mark triggered
        state.triggered = True
        # Does not fire again
        self.assertFalse(should_trigger_no_output_timeout(state, now=999.0))


# ---------------------------------------------------------------------------
# 9. JSON serialisable output
# ---------------------------------------------------------------------------

class SerialiseTests(unittest.TestCase):
    def test_all_scenario_payloads_serialisable(self):
        scenarios = [
            _obs(output_timeout=True),
            _obs(output_timeout=True, saw_reasoning=True),
            _obs(output_timeout=True, saw_compact_started=True),
            _obs(output_timeout=True, saw_error=True),
        ]
        for obs in scenarios:
            terminal = classify_stream_terminal(obs)
            state = _state(timeout_secs=10.0, started_at=0.0)
            state.mark_event(0.0)
            payload = build_timeout_telemetry_payload(state, terminal, now=10.0)
            json.dumps(payload)


if __name__ == "__main__":
    unittest.main()
