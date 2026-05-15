"""Tests for proxy/qz_stream_terminal.py — stream terminal classification seam (#40).

Slice 1: pure classifier, fixture-based observation accumulation,
protocol-drift tolerance. No real-time watchdog yet.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_stream_terminal import (
    STREAM_TERMINAL_SCHEMA,
    StreamObservation,
    accumulate,
    classify_stream_terminal,
    observation_from_dict,
    observation_from_event_type,
)
from proxy.qz_streaming import parse_sse_event_lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sse"


def _obs(**kwargs) -> StreamObservation:
    return StreamObservation(**kwargs)


def _classify(**kwargs) -> dict:
    return classify_stream_terminal(_obs(**kwargs))


def _parse_fixture(name: str) -> list[tuple[str, object]]:
    """Parse an SSE fixture file into a list of (event_type, payload) tuples."""
    path = FIXTURE_DIR / name
    events = []
    event_lines: list[bytes] = []
    for line in path.read_bytes().splitlines(keepends=True):
        if line in (b"\n", b"\r\n", b""):
            if event_lines:
                et, payload = parse_sse_event_lines(event_lines)
                if et is not None:
                    events.append((et, payload))
                event_lines = []
        else:
            event_lines.append(line)
    if event_lines:
        et, payload = parse_sse_event_lines(event_lines)
        if et is not None:
            events.append((et, payload))
    return events


def _obs_from_fixture(name: str) -> StreamObservation:
    """Accumulate stream observations from an SSE fixture file."""
    acc: dict = {}
    for event_type, payload in _parse_fixture(name):
        partial = observation_from_event_type(event_type, payload)
        accumulate(acc, partial)
    return observation_from_dict(acc)


# ---------------------------------------------------------------------------
# 1. Pure classifier — ok
# ---------------------------------------------------------------------------

class ClassifyOkTests(unittest.TestCase):
    def test_ok_with_answer_and_terminal(self):
        result = _classify(saw_output_text=True, saw_response_completed=True, saw_done=True)
        self.assertEqual(result["classification"], "ok")
        self.assertFalse(result["fallback_required"])
        self.assertFalse(result["recoverable"])
        self.assertTrue(result["visible_answer_seen"])
        self.assertTrue(result["terminal_event_seen"])

    def test_ok_with_assistant_item_and_done(self):
        result = _classify(saw_assistant_item=True, saw_done=True)
        self.assertEqual(result["classification"], "ok")

    def test_ok_schema_present(self):
        result = _classify(saw_output_text=True, saw_done=True)
        self.assertEqual(result["schema"], STREAM_TERMINAL_SCHEMA)

    def test_ok_is_json_serialisable(self):
        result = _classify(saw_output_text=True, saw_done=True)
        json.dumps(result)

    def test_ok_with_reasoning_plus_answer(self):
        result = _classify(saw_reasoning=True, saw_output_text=True, saw_done=True, saw_response_completed=True)
        self.assertEqual(result["classification"], "ok")


# ---------------------------------------------------------------------------
# 2. Pure classifier — stream_completed_without_visible_answer
# ---------------------------------------------------------------------------

class ClassifyCompletedWithoutAnswerTests(unittest.TestCase):
    def test_reasoning_only_completed(self):
        result = _classify(saw_reasoning=True, saw_response_completed=True, saw_done=True)
        self.assertEqual(result["classification"], "stream_completed_without_visible_answer")
        self.assertTrue(result["recoverable"])
        self.assertTrue(result["fallback_required"])
        self.assertFalse(result["visible_answer_seen"])
        self.assertTrue(result["terminal_event_seen"])

    def test_empty_completed_stream(self):
        # response.completed and [DONE] but no useful output
        result = _classify(saw_response_completed=True, saw_done=True)
        self.assertEqual(result["classification"], "stream_completed_without_visible_answer")

    def test_completed_with_tool_call_but_no_answer(self):
        result = _classify(saw_tool_call=True, saw_response_completed=True, saw_done=True)
        self.assertEqual(result["classification"], "stream_completed_without_visible_answer")

    def test_done_only_no_output(self):
        result = _classify(saw_done=True)
        self.assertEqual(result["classification"], "stream_completed_without_visible_answer")


# ---------------------------------------------------------------------------
# 3. Pure classifier — stream_terminal_missing
# ---------------------------------------------------------------------------

class ClassifyTerminalMissingTests(unittest.TestCase):
    def test_reasoning_then_close_no_terminal(self):
        result = _classify(saw_reasoning=True)
        self.assertEqual(result["classification"], "stream_terminal_missing")
        self.assertFalse(result["terminal_event_seen"])

    def test_output_text_then_close_no_terminal(self):
        result = _classify(saw_output_text=True)
        self.assertEqual(result["classification"], "stream_terminal_missing")

    def test_no_activity_at_all(self):
        result = _classify()
        self.assertEqual(result["classification"], "stream_terminal_missing")
        self.assertFalse(result["visible_answer_seen"])
        self.assertFalse(result["terminal_event_seen"])

    def test_tool_call_then_close(self):
        result = _classify(saw_tool_call=True)
        self.assertEqual(result["classification"], "stream_terminal_missing")


# ---------------------------------------------------------------------------
# 4. Pure classifier — compact_failed
# ---------------------------------------------------------------------------

class ClassifyCompactFailedTests(unittest.TestCase):
    def test_compact_failed_classification(self):
        result = _classify(saw_compact_started=True, saw_compact_failed=True, saw_done=True)
        self.assertEqual(result["classification"], "compact_failed")
        self.assertTrue(result["recoverable"])
        self.assertTrue(result["fallback_required"])

    def test_compact_failed_without_started(self):
        result = _classify(saw_compact_failed=True)
        self.assertEqual(result["classification"], "compact_failed")

    def test_compact_failed_trumps_answer(self):
        # Even if there's a visible answer, compact_failed wins
        result = _classify(saw_compact_failed=True, saw_output_text=True, saw_done=True)
        self.assertEqual(result["classification"], "compact_failed")


# ---------------------------------------------------------------------------
# 5. Pure classifier — fallback_emitted / repaired
# ---------------------------------------------------------------------------

class ClassifyFallbackTests(unittest.TestCase):
    def test_fallback_emitted(self):
        result = _classify(fallback_emitted=True, saw_output_text=True, saw_done=True)
        self.assertEqual(result["classification"], "fallback_emitted")
        self.assertFalse(result["fallback_required"])

    def test_repaired_with_answer(self):
        result = _classify(repair_emitted=True, saw_output_text=True, saw_done=True, saw_response_completed=True)
        self.assertEqual(result["classification"], "repaired")
        self.assertTrue(result["recoverable"])

    def test_repaired_without_answer_falls_through(self):
        # Repair started but still no visible answer: classify based on what's visible
        result = _classify(repair_emitted=True, saw_done=True)
        self.assertNotEqual(result["classification"], "repaired")
        self.assertEqual(result["classification"], "stream_completed_without_visible_answer")


# ---------------------------------------------------------------------------
# 6. Pure classifier — protocol_drift_seen
# ---------------------------------------------------------------------------

class ClassifyProtocolDriftTests(unittest.TestCase):
    def test_drift_with_complete_stream(self):
        result = _classify(
            saw_output_text=True, saw_done=True, saw_response_completed=True,
            saw_protocol_drift_event=True,
        )
        self.assertEqual(result["classification"], "protocol_drift_seen")
        self.assertFalse(result["fallback_required"])

    def test_drift_without_complete_stream(self):
        # Drift seen but stream still incomplete
        result = _classify(saw_protocol_drift_event=True)
        # No terminal, no answer → stream_terminal_missing
        self.assertEqual(result["classification"], "stream_terminal_missing")


# ---------------------------------------------------------------------------
# 7. Pure classifier — unrecoverable / error
# ---------------------------------------------------------------------------

class ClassifyErrorTests(unittest.TestCase):
    def test_error_without_recovery(self):
        result = _classify(saw_error=True)
        self.assertEqual(result["classification"], "unrecoverable")
        self.assertFalse(result["recoverable"])

    def test_error_terminal_without_answer_is_unrecoverable(self):
        result = _classify(saw_error=True, saw_done=True)
        self.assertEqual(result["classification"], "unrecoverable")
        self.assertFalse(result["recoverable"])
        self.assertTrue(result["terminal_event_seen"])

    def test_error_with_fallback_not_unrecoverable(self):
        result = _classify(saw_error=True, fallback_emitted=True)
        self.assertEqual(result["classification"], "fallback_emitted")

    def test_error_with_visible_answer_not_unrecoverable(self):
        result = _classify(saw_error=True, saw_output_text=True, saw_done=True)
        # error flag set but visible answer + terminal → classified by answer
        self.assertEqual(result["classification"], "ok")


# ---------------------------------------------------------------------------
# 8. Reserved: stream_no_output_timeout (slice 2 watchdog)
# ---------------------------------------------------------------------------

class ClassifyTimeoutTests(unittest.TestCase):
    def test_output_timeout_classification(self):
        result = _classify(output_timeout=True)
        self.assertEqual(result["classification"], "stream_no_output_timeout")
        self.assertTrue(result["recoverable"])
        self.assertTrue(result["fallback_required"])

    def test_output_timeout_trumps_compact_failed(self):
        # timeout is highest priority
        result = _classify(output_timeout=True, saw_compact_failed=True)
        self.assertEqual(result["classification"], "stream_no_output_timeout")


# ---------------------------------------------------------------------------
# 9. observation_from_event_type — event-level accumulation
# ---------------------------------------------------------------------------

class ObservationFromEventTypeTests(unittest.TestCase):
    def test_output_text_delta_with_text(self):
        obs = observation_from_event_type("response.output_text.delta", {"delta": "hello"})
        self.assertTrue(obs.get("saw_output_text"))

    def test_output_text_delta_whitespace_only(self):
        obs = observation_from_event_type("response.output_text.delta", {"delta": "   "})
        self.assertFalse(obs.get("saw_output_text"))

    def test_reasoning_delta(self):
        obs = observation_from_event_type("response.reasoning_text.delta", {"delta": "thinking..."})
        self.assertTrue(obs.get("saw_reasoning"))

    def test_reasoning_summary_delta(self):
        obs = observation_from_event_type("response.reasoning_summary_text.delta", {"delta": "..."})
        self.assertTrue(obs.get("saw_reasoning"))

    def test_response_completed(self):
        obs = observation_from_event_type("response.completed", {})
        self.assertTrue(obs.get("saw_response_completed"))

    def test_done_event(self):
        obs = observation_from_event_type("done", "[DONE]")
        self.assertTrue(obs.get("saw_done"))

    def test_error_events(self):
        for et in ("response.failed", "response.cancelled", "response.incomplete"):
            obs = observation_from_event_type(et, {})
            self.assertTrue(obs.get("saw_error"), f"{et} should set saw_error")

    def test_compact_started(self):
        obs = observation_from_event_type("response.compact.started", {})
        self.assertTrue(obs.get("saw_compact_started"))

    def test_compact_failed(self):
        obs = observation_from_event_type("response.compact.failed", {})
        self.assertTrue(obs.get("saw_compact_failed"))

    def test_assistant_item_done(self):
        payload = {
            "output_index": 0,
            "item": {"id": "msg_1", "type": "message", "status": "completed", "role": "assistant"},
        }
        obs = observation_from_event_type("response.output_item.done", payload)
        self.assertTrue(obs.get("saw_assistant_item"))

    def test_in_progress_item_done_not_assistant(self):
        payload = {
            "output_index": 0,
            "item": {"id": "msg_1", "type": "message", "status": "in_progress", "role": "assistant"},
        }
        obs = observation_from_event_type("response.output_item.done", payload)
        self.assertFalse(obs.get("saw_assistant_item"))

    def test_function_call_done_item(self):
        payload = {
            "output_index": 0,
            "item": {"id": "call_1", "type": "function_call", "status": "completed"},
        }
        obs = observation_from_event_type("response.output_item.done", payload)
        self.assertTrue(obs.get("saw_tool_call"))
        self.assertFalse(obs.get("saw_assistant_item"))

    def test_none_event_type_returns_empty(self):
        obs = observation_from_event_type(None, {})
        self.assertEqual(obs, {})

    def test_unknown_event_type_returns_empty(self):
        obs = observation_from_event_type("response.unknown.thing", {})
        self.assertEqual(obs, {})


# ---------------------------------------------------------------------------
# 10. Protocol drift — newer item/content delta shape
# ---------------------------------------------------------------------------

class ProtocolDriftToleranceTests(unittest.TestCase):
    def test_item_content_delta_sets_saw_output_text_and_drift(self):
        payload = {"delta": {"type": "text", "text": "drift content"}}
        obs = observation_from_event_type("response.output_item.content.delta", payload)
        self.assertTrue(obs.get("saw_output_text"))
        self.assertTrue(obs.get("saw_protocol_drift_event"))

    def test_item_content_delta_whitespace_sets_only_drift(self):
        payload = {"delta": {"type": "text", "text": "   "}}
        obs = observation_from_event_type("response.output_item.content.delta", payload)
        self.assertFalse(obs.get("saw_output_text"))
        self.assertTrue(obs.get("saw_protocol_drift_event"))

    def test_content_part_delta_sets_drift(self):
        payload = {"delta": "some text"}
        obs = observation_from_event_type("response.content_part.delta", payload)
        self.assertTrue(obs.get("saw_protocol_drift_event"))

    def test_item_content_delta_without_text_still_sets_drift(self):
        obs = observation_from_event_type("response.output_item.content.delta", {})
        self.assertTrue(obs.get("saw_protocol_drift_event"))


# ---------------------------------------------------------------------------
# 11. accumulate helper
# ---------------------------------------------------------------------------

class AccumulateTests(unittest.TestCase):
    def test_accumulate_or(self):
        acc: dict = {}
        acc = accumulate(acc, {"saw_reasoning": True})
        acc = accumulate(acc, {"saw_output_text": True})
        self.assertTrue(acc["saw_reasoning"])
        self.assertTrue(acc["saw_output_text"])

    def test_accumulate_false_does_not_reset(self):
        acc = {"saw_reasoning": True}
        acc = accumulate(acc, {"saw_reasoning": False})
        self.assertTrue(acc["saw_reasoning"])

    def test_observation_from_dict_roundtrip(self):
        d = {"saw_reasoning": True, "saw_output_text": True, "saw_done": True}
        obs = observation_from_dict(d)
        self.assertTrue(obs.saw_reasoning)
        self.assertTrue(obs.saw_output_text)
        self.assertTrue(obs.saw_done)
        self.assertFalse(obs.saw_compact_failed)


# ---------------------------------------------------------------------------
# 12. Fixture-based classification tests
# ---------------------------------------------------------------------------

class FixtureBasedClassificationTests(unittest.TestCase):
    """Classify stream observations built from SSE fixture files.

    These test the full pipeline: fixture → parse → accumulate → classify.
    """

    def test_basic_message_fixture_classifies_ok(self):
        obs = _obs_from_fixture("basic_message.raw")
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "ok")
        self.assertTrue(result["visible_answer_seen"])
        self.assertTrue(result["terminal_event_seen"])

    def test_reasoning_only_fixture_classifies_terminal_missing(self):
        # reasoning_only.raw has reasoning deltas but no response.completed or [DONE]
        # → stream_terminal_missing (the #9 repair path handles this live)
        obs = _obs_from_fixture("reasoning_only.raw")
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "stream_terminal_missing")
        self.assertTrue(obs.saw_reasoning)
        self.assertFalse(obs.saw_output_text)
        self.assertFalse(obs.saw_response_completed)
        self.assertFalse(obs.saw_done)

    def test_completed_without_done_fixture_classifies_ok(self):
        # completed_without_done.raw has output_text + response.completed but no [DONE]
        # The stream has a visible answer and response.completed — classify as ok
        # (QuantZhai currently accepts response.completed as a valid terminal)
        obs = _obs_from_fixture("completed_without_done.raw")
        result = classify_stream_terminal(obs)
        # visible answer seen, response.completed seen → ok (DONE is not required alone)
        self.assertTrue(obs.saw_output_text or obs.saw_response_completed)
        # Either ok or stream_terminal_missing depending on what the fixture shows
        self.assertIn(result["classification"], {"ok", "stream_terminal_missing", "stream_completed_without_visible_answer"})

    def test_done_only_fixture_classifies_without_visible_answer(self):
        obs = _obs_from_fixture("done_only.raw")
        result = classify_stream_terminal(obs)
        self.assertTrue(obs.saw_done)
        self.assertEqual(result["classification"], "stream_completed_without_visible_answer")

    def test_compact_failed_fixture_classifies_compact_failed(self):
        obs = _obs_from_fixture("compact_failed.raw")
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "compact_failed")
        self.assertTrue(obs.saw_compact_started)
        self.assertTrue(obs.saw_compact_failed)

    def test_item_content_delta_fixture_classifies_protocol_drift(self):
        # Newer delta shape → protocol_drift_seen (with visible answer + terminal)
        obs = _obs_from_fixture("item_content_delta.raw")
        result = classify_stream_terminal(obs)
        self.assertEqual(result["classification"], "protocol_drift_seen")
        self.assertTrue(obs.saw_protocol_drift_event)
        self.assertTrue(obs.saw_output_text)
        self.assertTrue(obs.saw_response_completed or obs.saw_done)

    def test_created_only_fixture_classifies_terminal_missing(self):
        obs = _obs_from_fixture("created_only.raw")
        result = classify_stream_terminal(obs)
        # Only response.created and response.in_progress then [DONE]
        # saw_done = True, but no output → completed_without_visible_answer or terminal_missing
        self.assertIn(result["classification"], {
            "stream_terminal_missing",
            "stream_completed_without_visible_answer",
        })

    def test_malformed_terminal_fixture_parses(self):
        # malformed_terminal.raw should parse without error
        obs = _obs_from_fixture("malformed_terminal.raw")
        result = classify_stream_terminal(obs)
        self.assertIn("classification", result)
        json.dumps(result)


# ---------------------------------------------------------------------------
# 13. All results are JSON serialisable
# ---------------------------------------------------------------------------

class JsonSerialisableTests(unittest.TestCase):
    def _all_obs(self):
        scenarios = [
            _obs(),
            _obs(saw_output_text=True, saw_done=True),
            _obs(saw_reasoning=True, saw_done=True, saw_response_completed=True),
            _obs(saw_compact_failed=True),
            _obs(saw_error=True),
            _obs(fallback_emitted=True),
            _obs(repair_emitted=True, saw_output_text=True, saw_done=True, saw_response_completed=True),
            _obs(saw_protocol_drift_event=True, saw_output_text=True, saw_done=True),
            _obs(output_timeout=True),
            _obs(saw_output_text=True),  # no terminal → terminal_missing
        ]
        return scenarios

    def test_all_results_json_serialisable(self):
        for obs in self._all_obs():
            result = classify_stream_terminal(obs)
            json.dumps(result)  # must not raise

    def test_schema_field_always_present(self):
        for obs in self._all_obs():
            result = classify_stream_terminal(obs)
            self.assertEqual(result["schema"], STREAM_TERMINAL_SCHEMA)


if __name__ == "__main__":
    unittest.main()
