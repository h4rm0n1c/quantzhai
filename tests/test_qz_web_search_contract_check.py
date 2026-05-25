"""
Tests for scripts/qz_web_search_contract_check.py

Covers the payload-aware SSE parser, the contract checker, and the
deterministic RuntimeStreamRuntime integration.

Key contract rules (issue #66):
- web_search items are response.output_item.added/done with item.type=web_search_call
- Detection MUST look at the data payload, NOT at event names
- Fake response.web_search_call.* events must not appear and must cause failure
- In deterministic mode, no web_search_call items → fail (proxy must convert the call)
- In live mode, no web_search_call items → no_search (model may decline tool use)
"""

import json
import sys
import pathlib
import unittest

SCRIPTS_DIR = pathlib.Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from qz_web_search_contract_check import (  # type: ignore
    parse_sse_events_with_payloads,
    check_contract,
    run_deterministic,
    _FakeStream,
    _FakeWebRuntime,
    _make_web_call_chunks,
    _make_final_message_chunks,
)

REPO_ROOT = str(pathlib.Path(__file__).parent.parent)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stream(*blocks):
    """Join SSE text blocks into a single stream string."""
    return "".join(blocks)


def _added_block(item_type, item_id="itm_1"):
    return (
        "event: response.output_item.added\n"
        f'data: {{"output_index":0,"item":{{"type":"{item_type}","id":"{item_id}","status":"in_progress"}}}}\n'
        "\n"
    )


def _done_block(item_type, item_id="itm_1"):
    return (
        "event: response.output_item.done\n"
        f'data: {{"output_index":0,"item":{{"type":"{item_type}","id":"{item_id}","status":"completed"}}}}\n'
        "\n"
    )


def _completed_block():
    return (
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}\n'
        "\n"
    )


def _fake_event_block(event_name):
    return (
        f"event: {event_name}\n"
        f'data: {{"type":"{event_name}"}}\n'
        "\n"
    )


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class ParseSSEEventsTests(unittest.TestCase):
    """Tests for parse_sse_events_with_payloads."""

    def test_returns_event_name_and_payload_tuple(self):
        stream = _stream(
            "event: response.output_item.added\n"
            'data: {"output_index":0,"item":{"type":"web_search_call"}}\n'
            "\n"
        )
        events = parse_sse_events_with_payloads(stream)
        self.assertEqual(len(events), 1)
        et, p = events[0]
        self.assertEqual(et, "response.output_item.added")
        self.assertIsInstance(p, dict)
        self.assertEqual(p["item"]["type"], "web_search_call")

    def test_event_name_does_not_contain_web_search_call(self):
        """Web search items arrive as output_item.added/done — never as response.web_search_call.*."""
        stream = _stream(
            _added_block("web_search_call"),
            _done_block("web_search_call"),
            _completed_block(),
        )
        events = parse_sse_events_with_payloads(stream)
        names = [et for et, _ in events]
        # Event names must be output_item.*, not web_search_call.*
        self.assertIn("response.output_item.added", names)
        self.assertIn("response.output_item.done", names)
        # No event name should contain "web_search_call"
        for name in names:
            if name:
                self.assertNotIn("web_search_call", name,
                                 f"Event name '{name}' must not contain 'web_search_call'")

    def test_done_sentinel_parsed_correctly(self):
        stream = "data: [DONE]\n\n"
        events = parse_sse_events_with_payloads(stream)
        self.assertEqual(len(events), 1)
        et, p = events[0]
        self.assertEqual(p, "[DONE]")

    def test_multiple_events_all_parsed(self):
        stream = _stream(
            _added_block("web_search_call"),
            "event: response.output_text.delta\n"
            'data: {"delta":"hello"}\n'
            "\n",
            _done_block("web_search_call"),
            _completed_block(),
        )
        events = parse_sse_events_with_payloads(stream)
        self.assertEqual(len(events), 4)

    def test_event_without_event_line_falls_back_to_payload_type(self):
        """When no 'event:' line is present, the event name falls back to payload['type']."""
        stream = (
            'data: {"type":"response.completed","response":{"status":"completed"}}\n'
            "\n"
        )
        events = parse_sse_events_with_payloads(stream)
        self.assertEqual(len(events), 1)
        et, _ = events[0]
        self.assertEqual(et, "response.completed")


# ---------------------------------------------------------------------------
# Contract checker tests
# ---------------------------------------------------------------------------

class CheckContractTests(unittest.TestCase):
    """Tests for check_contract — the Codex web_search contract enforcement."""

    def _events(self, stream_text):
        return parse_sse_events_with_payloads(stream_text)

    def test_correct_contract_passes(self):
        stream = _stream(
            _added_block("web_search_call"),
            _done_block("web_search_call"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "pass")

    def test_detects_web_search_call_via_item_type_not_event_name(self):
        """Contract checker must find web_search_call in payload.item.type,
        not by looking for the string in event names."""
        # Build events that pass but have no "web_search_call" in any event name
        stream = _stream(
            _added_block("web_search_call"),
            _done_block("web_search_call"),
            _completed_block(),
        )
        events = self._events(stream)
        # Confirm no event name contains "web_search_call"
        names = [et for et, _ in events]
        for name in names:
            if name:
                self.assertNotIn("web_search_call", name)
        # Contract still passes because check_contract looks at payload.item.type
        result = check_contract(events, mode="deterministic")
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["checks"]["output_item_added_with_web_search_call_item"])
        self.assertTrue(result["checks"]["output_item_done_with_web_search_call_item"])

    def test_fake_in_progress_event_causes_fail(self):
        stream = _stream(
            _added_block("web_search_call"),
            _fake_event_block("response.web_search_call.in_progress"),
            _done_block("web_search_call"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["fake_web_search_call_in_progress_absent"])

    def test_fake_searching_event_causes_fail(self):
        stream = _stream(
            _added_block("web_search_call"),
            _fake_event_block("response.web_search_call.searching"),
            _done_block("web_search_call"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["fake_web_search_call_searching_absent"])

    def test_fake_completed_event_causes_fail(self):
        stream = _stream(
            _added_block("web_search_call"),
            _fake_event_block("response.web_search_call.completed"),
            _done_block("web_search_call"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["fake_web_search_call_completed_absent"])

    def test_missing_output_item_added_causes_fail(self):
        stream = _stream(
            _done_block("web_search_call"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["output_item_added_with_web_search_call_item"])

    def test_missing_output_item_done_causes_fail(self):
        stream = _stream(
            _added_block("web_search_call"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["output_item_done_with_web_search_call_item"])

    def test_missing_response_completed_causes_fail(self):
        stream = _stream(
            _added_block("web_search_call"),
            _done_block("web_search_call"),
            # No response.completed
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["response_completed"])

    def test_no_web_search_items_in_deterministic_mode_is_fail(self):
        """In deterministic mode, no web_search_call item means the proxy failed to convert
        the function_call.  This must be fail, not no_search."""
        stream = _stream(
            _added_block("message"),
            _done_block("message"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertEqual(result["status"], "fail",
                         "deterministic mode: no web_search_call items → fail, not no_search")

    def test_no_web_search_items_in_live_mode_is_no_search(self):
        """In live mode, no web_search_call item means the model chose not to call web_search.
        This is not a contract failure — it is an opportunistic skip."""
        stream = _stream(
            _added_block("message"),
            _done_block("message"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="live")
        self.assertEqual(result["status"], "no_search",
                         "live mode: model did not call web_search → no_search, not fail")

    def test_message_item_does_not_count_as_web_search_call(self):
        stream = _stream(
            _added_block("message"),
            _done_block("message"),
            _completed_block(),
        )
        result = check_contract(self._events(stream), mode="deterministic")
        self.assertFalse(result["checks"]["output_item_added_with_web_search_call_item"])
        self.assertFalse(result["checks"]["output_item_done_with_web_search_call_item"])

    def test_all_fake_events_absent_flag_true_when_none_present(self):
        stream = _stream(
            _added_block("web_search_call"),
            _done_block("web_search_call"),
            _completed_block(),
        )
        result = check_contract(self._events(stream))
        self.assertTrue(result["checks"]["fake_web_search_call_in_progress_absent"])
        self.assertTrue(result["checks"]["fake_web_search_call_searching_absent"])
        self.assertTrue(result["checks"]["fake_web_search_call_completed_absent"])


# ---------------------------------------------------------------------------
# Deterministic integration test
# ---------------------------------------------------------------------------

class DeterministicContractTests(unittest.TestCase):
    """Integration tests for run_deterministic — exercises ResponsesStreamRuntime."""

    def test_deterministic_pass(self):
        result = run_deterministic(REPO_ROOT)
        self.assertEqual(result["status"], "pass",
                         f"deterministic contract check failed: {result}")

    def test_deterministic_mode_in_result(self):
        result = run_deterministic(REPO_ROOT)
        self.assertEqual(result.get("mode"), "deterministic")

    def test_deterministic_all_checks_true(self):
        result = run_deterministic(REPO_ROOT)
        checks = result.get("checks", {})
        for key, value in checks.items():
            self.assertTrue(value, f"deterministic check '{key}' is False")

    def test_fake_web_runtime_integration(self):
        """Verify that _FakeWebRuntime returns the correct structure for the runtime."""
        wrt = _FakeWebRuntime()
        call_item = {"call_id": "c1", "name": "web_search", "arguments": "{}"}
        public, output, sources = wrt.execute_web_search_call(call_item, {}, set())
        self.assertEqual(public["type"], "web_search_call")
        self.assertEqual(public["call_id"], "c1")
        self.assertEqual(output["type"], "function_call_output")
        self.assertIsInstance(sources, list)
        self.assertTrue(len(sources) > 0)


# ---------------------------------------------------------------------------
# FakeStream tests
# ---------------------------------------------------------------------------

class FakeStreamTests(unittest.TestCase):
    """Basic tests for _FakeStream helper."""

    def test_reads_all_lines(self):
        chunks = [b"event: foo\ndata: {}\n\n"]
        stream = _FakeStream(chunks)
        lines = []
        while True:
            line = stream.readline()
            if not line:
                break
            lines.append(line)
        self.assertEqual(len(lines), 3)

    def test_close_sets_closed(self):
        stream = _FakeStream([])
        self.assertFalse(stream.closed)
        stream.close()
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
