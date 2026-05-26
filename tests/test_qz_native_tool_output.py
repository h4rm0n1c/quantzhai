import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_native_tool_output import (
    _build_call_id_to_name,
    _parse_exit_code,
    _safe_output_preview,
    classify_native_tool_outputs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fco(call_id, output):
    """Build a minimal function_call_output item."""
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _fc(call_id, name, arguments="{}"):
    """Build a minimal function_call item."""
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


# Real sandbox output envelope observed from Codex 0.130 (2026-05-14 capture)
_READONLY_FS_OUTPUT = (
    "Chunk ID: 0c3a9b\n"
    "Wall time: 0.0000 seconds\n"
    "Process exited with code 1\n"
    "Original token count: 16\n"
    "Output:\n"
    "/bin/bash: line 1: /etc/qz-denied-test: Read-only file system\n"
)

_CONN_REFUSED_OUTPUT = (
    "Chunk ID: ab12cd\n"
    "Wall time: 0.0010 seconds\n"
    "Process exited with code 1\n"
    "Output:\n"
    "curl: (7) Failed to connect to 127.0.0.1 port 18180: Connection refused\n"
)

_REQUEST_PERMISSIONS_GRANTED_OUTPUT = json.dumps({
    "permissions": {
        "network": {"enabled": True},
        "file_system": None,
    },
    "scope": "turn",
    "strict_auto_review": False,
})

_REQUEST_PERMISSIONS_EMPTY_OUTPUT = json.dumps({
    "permissions": {
        "network": None,
        "file_system": None,
    },
    "scope": "turn",
})


# ---------------------------------------------------------------------------
# _parse_exit_code
# ---------------------------------------------------------------------------

class ParseExitCodeTests(unittest.TestCase):
    def test_parses_exit_code_from_codex_envelope(self):
        self.assertEqual(_parse_exit_code(_READONLY_FS_OUTPUT), 1)

    def test_parses_exit_code_zero(self):
        self.assertEqual(_parse_exit_code("Process exited with code 0\nOutput:\nok\n"), 0)

    def test_parses_large_exit_code(self):
        self.assertEqual(_parse_exit_code("Process exited with code 127\nOutput:\n"), 127)

    def test_returns_none_when_no_exit_code(self):
        self.assertIsNone(_parse_exit_code("some output without envelope"))

    def test_returns_none_for_empty_string(self):
        self.assertIsNone(_parse_exit_code(""))

    def test_case_insensitive(self):
        self.assertEqual(_parse_exit_code("process exited with code 2"), 2)


# ---------------------------------------------------------------------------
# _safe_output_preview
# ---------------------------------------------------------------------------

class SafeOutputPreviewTests(unittest.TestCase):
    def test_truncates_string_at_limit(self):
        result = _safe_output_preview("x" * 300, 200)
        self.assertEqual(len(result), 200)

    def test_short_string_unchanged(self):
        self.assertEqual(_safe_output_preview("hello", 200), "hello")

    def test_none_returns_empty(self):
        self.assertEqual(_safe_output_preview(None, 200), "")

    def test_int_converted_to_string(self):
        result = _safe_output_preview(42, 200)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "42")

    def test_list_converted_to_string(self):
        result = _safe_output_preview(["a", "b"], 200)
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# _build_call_id_to_name
# ---------------------------------------------------------------------------

class BuildCallIdToNameTests(unittest.TestCase):
    def test_maps_call_id_to_tool_name(self):
        items = [_fc("call_abc", "exec_command")]
        mapping = _build_call_id_to_name(items)
        self.assertEqual(mapping["call_abc"], "exec_command")

    def test_ignores_non_function_call_items(self):
        items = [_fco("call_abc", "output"), _fc("call_abc", "exec_command")]
        mapping = _build_call_id_to_name(items)
        self.assertIn("call_abc", mapping)

    def test_empty_input_returns_empty_map(self):
        self.assertEqual(_build_call_id_to_name([]), {})

    def test_non_dict_items_ignored(self):
        mapping = _build_call_id_to_name([None, "string", 42])
        self.assertEqual(mapping, {})


# ---------------------------------------------------------------------------
# classify_native_tool_outputs
# ---------------------------------------------------------------------------

class ClassifyNativeToolOutputsTests(unittest.TestCase):

    # --- sandbox readonly filesystem ---

    def test_readonly_fs_emits_tool_sandbox_denied(self):
        items = [_fco("call_1", _READONLY_FS_OUTPUT)]
        results = classify_native_tool_outputs(items)
        self.assertEqual(len(results), 1)
        event_type, payload = results[0]
        self.assertEqual(event_type, "tool_sandbox_denied")
        self.assertEqual(payload["classifier"], "sandbox_denied_readonly_fs")
        self.assertEqual(payload["matched_string"], "Read-only file system")
        self.assertEqual(payload["confidence"], "high")

    def test_readonly_fs_exit_code_parsed(self):
        items = [_fco("call_1", _READONLY_FS_OUTPUT)]
        _, payload = classify_native_tool_outputs(items)[0]
        self.assertEqual(payload["exit_code"], 1)

    def test_readonly_fs_output_preview_bounded(self):
        long_output = ("x" * 300) + "Read-only file system" + ("y" * 300)
        items = [_fco("call_1", long_output)]
        _, payload = classify_native_tool_outputs(items)[0]
        self.assertIsInstance(payload["output_preview"], str)
        self.assertLessEqual(len(payload["output_preview"]), 200)

    # --- connection refused ---

    def test_connection_refused_emits_tool_connection_failed(self):
        items = [_fco("call_2", _CONN_REFUSED_OUTPUT)]
        results = classify_native_tool_outputs(items)
        self.assertEqual(len(results), 1)
        event_type, payload = results[0]
        self.assertEqual(event_type, "tool_connection_failed")
        self.assertEqual(payload["classifier"], "native_tool_connection_refused")
        self.assertIn("Connection refused", payload["matched_string"])

    def test_lowercase_connection_refused_also_classified(self):
        output = "Process exited with code 1\nOutput:\ncurl: connection refused\n"
        items = [_fco("call_3", output)]
        results = classify_native_tool_outputs(items)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "tool_connection_failed")

    # --- tool name recovery ---

    def test_tool_name_recovered_from_matching_function_call(self):
        items = [
            _fc("call_exec", "exec_command"),
            _fco("call_exec", _READONLY_FS_OUTPUT),
        ]
        _, payload = classify_native_tool_outputs(items)[0]
        self.assertEqual(payload["tool"], "exec_command")
        self.assertEqual(payload["call_id"], "call_exec")

    def test_unknown_tool_when_no_matching_function_call(self):
        items = [_fco("call_orphan", _READONLY_FS_OUTPUT)]
        _, payload = classify_native_tool_outputs(items)[0]
        self.assertEqual(payload["tool"], "unknown")

    # --- request_permissions outcome JSON ---

    def test_request_permissions_granted_fixture_classified(self):
        items = [
            _fc("call_perm", "request_permissions"),
            _fco("call_perm", _REQUEST_PERMISSIONS_GRANTED_OUTPUT),
        ]
        results = classify_native_tool_outputs(items)
        self.assertEqual(len(results), 1)
        event_type, payload = results[0]
        self.assertEqual(event_type, "request_permissions_outcome")
        self.assertEqual(payload["classifier"], "request_permissions_granted")
        self.assertEqual(payload["outcome"], "granted")
        self.assertEqual(payload["scope"], "turn")
        self.assertFalse(payload["strict_auto_review"])
        self.assertEqual(payload["permission_summary"], {"network": True, "file_system": False})
        self.assertEqual(payload["confidence"], "high")

    def test_request_permissions_empty_fixture_classified_as_denied_or_unavailable(self):
        items = [
            _fc("call_perm", "request_permissions"),
            _fco("call_perm", _REQUEST_PERMISSIONS_EMPTY_OUTPUT),
        ]
        results = classify_native_tool_outputs(items)
        self.assertEqual(len(results), 1)
        event_type, payload = results[0]
        self.assertEqual(event_type, "request_permissions_outcome")
        self.assertEqual(payload["classifier"], "request_permissions_denied_or_unavailable")
        self.assertEqual(payload["outcome"], "denied_or_unavailable")
        self.assertEqual(payload["permission_summary"], {"network": False, "file_system": False})
        self.assertLessEqual(len(payload["output_preview"]), 200)

    def test_request_permissions_json_requires_matching_tool_name(self):
        items = [
            _fc("call_perm", "exec_command"),
            _fco("call_perm", _REQUEST_PERMISSIONS_EMPTY_OUTPUT),
        ]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_request_permissions_malformed_json_not_classified(self):
        items = [
            _fc("call_perm", "request_permissions"),
            _fco("call_perm", '{"permissions":{}'),
        ]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_request_permissions_json_requires_source_backed_response_shape(self):
        items = [
            _fc("call_perm", "request_permissions"),
            _fco("call_perm", json.dumps({"permissions": {}, "ok": False})),
        ]
        self.assertEqual(classify_native_tool_outputs(items), [])

    # --- NOT classified ---

    def test_plain_permission_denied_not_classified(self):
        output = "Process exited with code 1\nOutput:\nbash: permission denied\n"
        items = [_fco("call_perm", output)]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_process_exited_code_1_alone_not_classified(self):
        output = "Process exited with code 1\nOutput:\n"
        items = [_fco("call_exit", output)]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_arbitrary_denied_text_not_classified(self):
        output = "denied denied unavailable sandbox"
        items = [_fco("call_denied_words", output)]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_exit_code_zero_success_not_classified(self):
        output = "Process exited with code 0\nOutput:\nhello\n"
        items = [_fco("call_ok", output)]
        self.assertEqual(classify_native_tool_outputs(items), [])

    # --- safety / robustness ---

    def test_empty_input_returns_empty(self):
        self.assertEqual(classify_native_tool_outputs([]), [])

    def test_non_list_input_returns_empty(self):
        self.assertEqual(classify_native_tool_outputs(None), [])
        self.assertEqual(classify_native_tool_outputs({}), [])

    def test_malformed_item_ignored(self):
        items = [None, "string", 42, {"type": "function_call_output"}]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_non_string_output_ignored(self):
        items = [{"type": "function_call_output", "call_id": "c", "output": ["list"]}]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_none_output_ignored(self):
        items = [{"type": "function_call_output", "call_id": "c", "output": None}]
        self.assertEqual(classify_native_tool_outputs(items), [])

    def test_non_function_call_output_items_skipped(self):
        items = [
            {"type": "message", "role": "user", "content": "Read-only file system"},
            {"type": "function_call", "name": "exec_command", "call_id": "c"},
        ]
        self.assertEqual(classify_native_tool_outputs(items), [])

    # --- body mutation guard ---

    def test_input_items_not_mutated_after_classification(self):
        item = {"type": "function_call_output", "call_id": "call_ro", "output": _READONLY_FS_OUTPUT}
        items = [item]
        original_item = copy.deepcopy(item)
        classify_native_tool_outputs(items)
        self.assertEqual(item, original_item)

    def test_input_list_not_mutated(self):
        items = [_fco("c1", _READONLY_FS_OUTPUT), _fco("c2", _CONN_REFUSED_OUTPUT)]
        original_len = len(items)
        classify_native_tool_outputs(items)
        self.assertEqual(len(items), original_len)

    # --- multiple items ---

    def test_multiple_items_each_classified_independently(self):
        items = [
            _fco("call_a", _READONLY_FS_OUTPUT),
            _fco("call_b", _CONN_REFUSED_OUTPUT),
        ]
        results = classify_native_tool_outputs(items)
        self.assertEqual(len(results), 2)
        event_types = {r[0] for r in results}
        self.assertIn("tool_sandbox_denied", event_types)
        self.assertIn("tool_connection_failed", event_types)

    def test_payload_fields_present(self):
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        _, payload = classify_native_tool_outputs(items)[0]
        for field in ("call_id", "tool", "classifier", "matched_string",
                      "exit_code", "output_preview", "confidence"):
            self.assertIn(field, payload, f"missing field: {field}")

    def test_payload_strings_are_strings(self):
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        _, payload = classify_native_tool_outputs(items)[0]
        for field in ("call_id", "tool", "classifier", "matched_string", "output_preview", "confidence"):
            self.assertIsInstance(payload[field], str, f"field '{field}' should be str")


class ObserverOrderRegressionTests(unittest.TestCase):
    """Prove the observer must run on raw input, not normalized input.

    After normalize_responses_input_for_qwen / _microcompact_old_tool_results,
    function_call_output items may be rewritten into assistant message items
    (e.g. by microcompaction when conversation history is long). Classification
    on the normalized items would miss the signal.

    These tests document the wiring requirement: classify_native_tool_outputs()
    must be called on the raw incoming body["input"] BEFORE any normalization.
    """

    def test_classifier_fires_on_raw_function_call_output(self):
        """Raw function_call_output with sandbox denial is classified."""
        raw_items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        results = classify_native_tool_outputs(raw_items)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "tool_sandbox_denied")

    def test_classifier_misses_signal_when_item_rewritten_to_message(self):
        """After microcompaction, function_call_output becomes an assistant message.

        Classification on the compacted form returns nothing — proving that the
        observer must run BEFORE normalization to catch sandbox denials.
        """
        # This simulates what _microcompact_old_tool_results produces from a
        # function_call_output that has been compacted into a placeholder message.
        compacted_item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Tool exec_command: FAILED — Read-only file system"}],
        }
        results = classify_native_tool_outputs([compacted_item])
        # Not a function_call_output → no event — proves classifier needs raw items
        self.assertEqual(results, [])

    def test_classifier_misses_signal_when_output_converted_to_input_text(self):
        """Input-text message form (another normalization output) is not classified."""
        normalized_item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Read-only file system"}],
        }
        results = classify_native_tool_outputs([normalized_item])
        self.assertEqual(results, [])

    def test_plain_permission_denied_not_classified_on_raw_input(self):
        """Plain permission denied in raw function_call_output is NOT classified."""
        raw_items = [_fco("call_perm", "Process exited with code 1\nOutput:\nbash: permission denied\n")]
        self.assertEqual(classify_native_tool_outputs(raw_items), [])

    def test_exit_code_alone_not_classified_on_raw_input(self):
        """Exit code 1 alone in raw function_call_output is NOT classified."""
        raw_items = [_fco("call_exit", "Process exited with code 1\nOutput:\n")]
        self.assertEqual(classify_native_tool_outputs(raw_items), [])

    def test_connection_refused_raw_input_classified(self):
        """Connection refused in raw function_call_output IS classified."""
        raw_items = [_fco("call_conn", _CONN_REFUSED_OUTPUT)]
        results = classify_native_tool_outputs(raw_items)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "tool_connection_failed")


class SignalWrapperTests(unittest.TestCase):
    """Tests for classify_native_tool_output_signals() — the SignalDecision wrapper."""

    def test_returns_signal_decision_objects(self):
        from proxy.qz_feedback import SignalDecision
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        results = classify_native_tool_output_signals(items)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], SignalDecision)

    def test_visibility_is_operator(self):
        from proxy.qz_feedback import FeedbackVisibility, SignalDecision
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        results = classify_native_tool_output_signals(items)
        self.assertEqual(results[0].visibility, FeedbackVisibility.OPERATOR)

    def test_channel_is_telemetry(self):
        from proxy.qz_feedback import FeedbackChannel, SignalDecision
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        results = classify_native_tool_output_signals(items)
        self.assertEqual(results[0].channel, FeedbackChannel.TELEMETRY)

    def test_event_type_matches_tuple_form(self):
        from proxy.qz_native_tool_output import (
            classify_native_tool_output_signals,
            classify_native_tool_outputs,
        )
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        signals = classify_native_tool_output_signals(items)
        tuples = classify_native_tool_outputs(items)
        self.assertEqual(signals[0].event_type, tuples[0][0])

    def test_payload_matches_tuple_form(self):
        from proxy.qz_native_tool_output import (
            classify_native_tool_output_signals,
            classify_native_tool_outputs,
        )
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        signals = classify_native_tool_output_signals(items)
        tuples = classify_native_tool_outputs(items)
        self.assertEqual(signals[0].payload, tuples[0][1])

    def test_confidence_carried_from_payload(self):
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        items = [_fco("call_ro", _READONLY_FS_OUTPUT)]
        results = classify_native_tool_output_signals(items)
        self.assertEqual(results[0].confidence, "high")

    def test_empty_input_returns_empty(self):
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        self.assertEqual(classify_native_tool_output_signals([]), [])

    def test_both_classifiers_wrapped(self):
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        items = [
            _fco("call_a", _READONLY_FS_OUTPUT),
            _fco("call_b", _CONN_REFUSED_OUTPUT),
        ]
        results = classify_native_tool_output_signals(items)
        self.assertEqual(len(results), 2)
        event_types = {r.event_type for r in results}
        self.assertIn("tool_sandbox_denied", event_types)
        self.assertIn("tool_connection_failed", event_types)

    def test_request_permissions_outcome_wrapped(self):
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        items = [
            _fc("call_perm", "request_permissions"),
            _fco("call_perm", _REQUEST_PERMISSIONS_EMPTY_OUTPUT),
        ]
        results = classify_native_tool_output_signals(items)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].event_type, "request_permissions_outcome")
        self.assertEqual(results[0].payload["classifier"], "request_permissions_denied_or_unavailable")

    def test_connection_failed_confidence_is_medium(self):
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        items = [_fco("call_b", _CONN_REFUSED_OUTPUT)]
        results = classify_native_tool_output_signals(items)
        self.assertEqual(results[0].event_type, "tool_connection_failed")
        self.assertEqual(results[0].confidence, "medium")

    def test_classify_signals_preserves_readonly_check(self):
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        # No mutation of input
        item = _fco("call_ro", _READONLY_FS_OUTPUT)
        original_output = item["output"]
        classify_native_tool_output_signals([item])
        self.assertEqual(item["output"], original_output)


if __name__ == "__main__":
    unittest.main()
