import inspect
import json
import unittest

from proxy.qz_proxy_tools import make_proxy_local_tool_registry
from proxy.qz_responses_stream import (
    ResponsesStreamRuntime,
    build_tool_coercion_telemetry_payload,
)
from proxy.qz_tool_lifecycle import CompletedToolCallDecision


class FakeWebRuntime:
    def execute_web_search_call(self, call, counters, seen_signatures, request_id=""):
        raise AssertionError("web_search execution is not used by these tests")


class ApplyPatchTelemetryIntegrationTests(unittest.TestCase):
    """Tests for the production coercion telemetry helper used by streaming."""

    def _registry(self):
        return make_proxy_local_tool_registry(FakeWebRuntime())

    def test_sibling_patch_promoted_payload(self):
        call = {
            "type": "function_call",
            "name": "apply_patch",
            "call_id": "test_call",
            "arguments": json.dumps({
                "operation": {"type": "create_file", "path": "secret/path.txt"},
                "patch": "VERY_SECRET_PATCH_BODY\n",
            }),
        }

        decision = self._registry().completed_call_decision(call)
        self.assertTrue(decision.coercion_applied)
        self.assertFalse(decision.coercion_error)

        built = build_tool_coercion_telemetry_payload(call, decision)
        self.assertIsNotNone(built)
        event_type, payload = built

        self.assertEqual(event_type, "coercion_succeeded")
        self.assertEqual(payload["tool"], "apply_patch")
        self.assertEqual(payload["source"], "tool_adapter")
        self.assertTrue(payload["correction_applied"])
        self.assertEqual(payload["apply_patch"]["coercion_strategy"], "sibling_patch_promoted")
        self.assertTrue(payload["apply_patch"]["patch_present"])
        self.assertTrue(payload["apply_patch"]["path_present"])

        payload_json = json.dumps(payload)
        self.assertNotIn("secret/path.txt", payload_json)
        self.assertNotIn("VERY_SECRET_PATCH_BODY", payload_json)

    def test_failed_missing_diff_payload(self):
        call = {
            "type": "function_call",
            "name": "apply_patch",
            "call_id": "test_call",
            "arguments": json.dumps({
                "operation": {"type": "create_file", "path": "secret/path.txt"},
            }),
        }

        decision = self._registry().completed_call_decision(call)
        self.assertTrue(decision.coercion_applied)
        self.assertTrue(decision.coercion_error)

        built = build_tool_coercion_telemetry_payload(call, decision)
        self.assertIsNotNone(built)
        event_type, payload = built

        self.assertEqual(event_type, "coercion_failed")
        self.assertFalse(payload["correction_applied"])
        self.assertTrue(payload["error_summary"])
        self.assertEqual(payload["apply_patch"]["coercion_strategy"], "failed_missing_diff")

        payload_json = json.dumps(payload)
        self.assertNotIn("secret/path.txt", payload_json)

    def test_no_coercion_means_no_telemetry(self):
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "test_call",
            "arguments": "{}",
        }
        decision = CompletedToolCallDecision(kind="public", call=call)

        self.assertIsNone(build_tool_coercion_telemetry_payload(call, decision))

    def test_non_apply_patch_coercion_has_no_apply_patch_metadata(self):
        call = {
            "type": "function_call",
            "name": "web_search",
            "call_id": "test_call",
            "arguments": "{}",
        }
        decision = CompletedToolCallDecision(
            kind="proxy_local",
            call=call,
            coercion_applied=True,
        )

        built = build_tool_coercion_telemetry_payload(call, decision)
        self.assertIsNotNone(built)
        event_type, payload = built

        self.assertEqual(event_type, "coercion_succeeded")
        self.assertNotIn("apply_patch", payload)

    def test_runtime_call_site_uses_helper(self):
        source = inspect.getsource(ResponsesStreamRuntime.run)

        self.assertIn("build_tool_coercion_telemetry_payload", source)
        self.assertNotIn("inspect_apply_patch_arguments", source)


if __name__ == "__main__":
    unittest.main()
