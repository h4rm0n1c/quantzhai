import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_feedback import (
    FeedbackChannel,
    FeedbackVisibility,
    SignalDecision,
    render_advisory_output,
    render_coercion_error,
)
from proxy.qz_tools import synthesize_tool_error_result


class FeedbackEnumTests(unittest.TestCase):
    def test_feedback_visibility_has_expected_members(self):
        self.assertEqual(FeedbackVisibility.MODEL.value, "model")
        self.assertEqual(FeedbackVisibility.OPERATOR.value, "operator")
        self.assertEqual(FeedbackVisibility.BOTH.value, "both")

    def test_feedback_channel_has_expected_members(self):
        self.assertEqual(FeedbackChannel.FUNCTION_CALL_OUTPUT.value, "function_call_output")
        self.assertEqual(FeedbackChannel.TELEMETRY.value, "telemetry")
        self.assertEqual(FeedbackChannel.INSTRUCTIONS.value, "instructions")
        self.assertEqual(FeedbackChannel.TURN_HARNESS.value, "turn_harness")
        self.assertEqual(FeedbackChannel.FUTURE_STATE.value, "future_state")


class SignalDecisionTests(unittest.TestCase):
    def test_signal_decision_construction(self):
        sig = SignalDecision(
            event_type="tool_sandbox_denied",
            payload={"tool": "exec_command"},
        )
        self.assertEqual(sig.event_type, "tool_sandbox_denied")
        self.assertEqual(sig.payload, {"tool": "exec_command"})
        self.assertEqual(sig.visibility, FeedbackVisibility.OPERATOR)
        self.assertEqual(sig.channel, FeedbackChannel.TELEMETRY)
        self.assertEqual(sig.confidence, "")

    def test_signal_decision_with_explicit_fields(self):
        sig = SignalDecision(
            event_type="tool_coercion_error",
            payload={"tool": "apply_patch"},
            visibility=FeedbackVisibility.MODEL,
            channel=FeedbackChannel.FUNCTION_CALL_OUTPUT,
            confidence="high",
        )
        self.assertEqual(sig.visibility, FeedbackVisibility.MODEL)
        self.assertEqual(sig.channel, FeedbackChannel.FUNCTION_CALL_OUTPUT)
        self.assertEqual(sig.confidence, "high")

    def test_signal_decision_is_frozen(self):
        sig = SignalDecision(event_type="x", payload={})
        with self.assertRaises(Exception):  # frozen dataclass raises FrozenInstanceError
            sig.event_type = "y"

    def test_signal_decision_payload_is_dict(self):
        sig = SignalDecision(event_type="x", payload={"k": "v"})
        self.assertIsInstance(sig.payload, dict)


class RenderAdvisoryOutputTests(unittest.TestCase):
    def _call(self, call_id="call_abc"):
        return {"type": "function_call", "name": "exec_command", "call_id": call_id}

    def test_type_is_function_call_output(self):
        result = render_advisory_output(self._call(), "Note: already read this.")
        self.assertEqual(result["type"], "function_call_output")

    def test_call_id_preserved(self):
        result = render_advisory_output({"call_id": "orig_id"}, "Note: ...")
        self.assertEqual(result["call_id"], "orig_id")

    def test_output_is_plain_text_not_json_ok_false(self):
        """Advisory output is plain text, not an error JSON payload."""
        msg = "Note: you already read README.md earlier."
        result = render_advisory_output(self._call(), msg)
        self.assertEqual(result["output"], msg)
        # Must NOT be a JSON {"ok": false, ...} payload
        self.assertNotIn('"ok"', result["output"])

    def test_differs_from_coercion_error_in_output_format(self):
        """render_advisory_output and render_coercion_error produce different output formats."""
        call = self._call()
        msg = "test message"
        advisory = render_advisory_output(call, msg)
        error = render_coercion_error(call, msg)
        # Error wraps in JSON; advisory is plain text
        self.assertEqual(advisory["output"], msg)
        self.assertNotEqual(error["output"], msg)

    def test_fallback_call_id_when_none(self):
        result = render_advisory_output({}, "note")
        self.assertTrue(result["call_id"].startswith("advisory_"))


class RenderCoercionErrorTests(unittest.TestCase):
    def _call(self, call_id="call_abc"):
        return {"type": "function_call", "name": "apply_patch", "call_id": call_id}

    def test_output_shape_matches_synthesize_tool_error_result(self):
        """render_coercion_error must produce byte-for-byte identical output to synthesize_tool_error_result."""
        call = self._call("call_match")
        message = "test error message"
        new = render_coercion_error(call, message)
        old = synthesize_tool_error_result(call, message)
        self.assertEqual(new, old)

    def test_type_is_function_call_output(self):
        result = render_coercion_error(self._call(), "err")
        self.assertEqual(result["type"], "function_call_output")

    def test_call_id_preserved(self):
        result = render_coercion_error({"call_id": "my_call"}, "err")
        self.assertEqual(result["call_id"], "my_call")

    def test_id_field_used_when_call_id_absent(self):
        result = render_coercion_error({"id": "fc_id"}, "err")
        self.assertEqual(result["call_id"], "fc_id")

    def test_output_is_json_with_ok_false_and_error(self):
        result = render_coercion_error(self._call(), "bad args")
        parsed = json.loads(result["output"])
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"], "bad args")

    def test_fallback_call_id_when_none_available(self):
        result = render_coercion_error({}, "err")
        self.assertTrue(result["call_id"].startswith("err_"))

    def test_null_call_uses_fallback_call_id(self):
        result = render_coercion_error(None, "err")  # type: ignore[arg-type]
        self.assertTrue(result["call_id"].startswith("err_"))

    def test_non_ascii_error_message_encoded(self):
        result = render_coercion_error(self._call(), "错误")
        parsed = json.loads(result["output"])
        self.assertEqual(parsed["error"], "错误")

    def test_output_field_is_valid_json_string(self):
        result = render_coercion_error(self._call(), "any message")
        self.assertIsInstance(result["output"], str)
        parsed = json.loads(result["output"])
        self.assertIsInstance(parsed, dict)


if __name__ == "__main__":
    unittest.main()
