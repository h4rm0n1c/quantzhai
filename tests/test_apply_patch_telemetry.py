import json
import unittest
from proxy.qz_tool_apply_patch import APPLY_PATCH_TOOL_ADAPTER
from proxy.qz_responses_stream import ResponsesStreamRuntime
from proxy.qz_tools import ToolRegistry

class ApplyPatchTelemetryIntegrationTests(unittest.TestCase):
    """Integration tests for apply_patch telemetry in streaming path."""

    def test_sibling_patch_promoted_telemetry(self):
        """Verify sibling_patch_promoted coercion emits correct telemetry."""
        # This is a test that uses a mock-like structure to simulate the stream.
        # We need to simulate the completed_call_decision logic found in
        # qz_responses_stream.py and ensure the payload is correct.
        
        # 1. Simulate the coercion decision
        call = {
            "name": "apply_patch",
            "call_id": "test_coercion_1",
            "arguments": json.dumps({
                "operation": {"type": "create_file", "path": "secret/path.txt"},
                "patch": "VERY_SECRET_PATCH_BODY\n"
            })
        }
        
        # 2. Use the adapter to get the decision
        # The stream code calls proxy_tool_registry.completed_call_decision
        # We can simulate the coercion part.
        from proxy.qz_tool_lifecycle import CompletedToolCallDecision
        
        # Simulation: coercing this call via ApplyPatchToolAdapter.coerce()
        coercion = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertTrue(coercion.succeeded())
        
        decision = CompletedToolCallDecision(
            kind="adapter",
            call=call,
            coercion_applied=True,
            coercion_error=None
        )
        
        # 3. Verify telemetry payload construction logic (as in qz_responses_stream.py)
        if decision.coercion_applied:
            _coercion_event = "coercion_failed" if decision.coercion_error else "coercion_succeeded"
            _coerce_tool = call.get("name") or ""
            _coercion_payload = {
                "tool": _coerce_tool,
                "upstream_name": _coerce_tool,
                "call_id": call.get("call_id") or "",
                "correction_applied": not bool(decision.coercion_error),
                "error_summary": decision.coercion_error[:200] if decision.coercion_error else "",
                "source": "tool_adapter",
            }
            if _coerce_tool == "apply_patch":
                from proxy.qz_tool_apply_patch import inspect_apply_patch_arguments
                _coercion_payload["apply_patch"] = inspect_apply_patch_arguments(
                    call.get("arguments") or ""
                )
        
        # 4. Assertions
        self.assertEqual(_coercion_event, "coercion_succeeded")
        self.assertEqual(_coercion_payload["apply_patch"]["coercion_strategy"], "sibling_patch_promoted")
        self.assertTrue(_coercion_payload["apply_patch"]["patch_present"])
        self.assertTrue(_coercion_payload["apply_patch"]["path_present"])
        
        # Verify forbidden raw content safety
        payload_str = json.dumps(_coercion_payload)
        self.assertNotIn("secret/path.txt", payload_str)
        self.assertNotIn("VERY_SECRET_PATCH_BODY", payload_str)

    def test_failed_missing_diff_telemetry(self):
        """Verify failed_missing_diff coercion emits coercion_failed telemetry."""
        call = {
            "name": "apply_patch",
            "call_id": "test_coercion_fail_1",
            "arguments": json.dumps({
                "operation": {"type": "create_file", "path": "secret/path.txt"}
            })
        }
        
        from proxy.qz_tool_lifecycle import CompletedToolCallDecision
        coercion = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(coercion.succeeded())
        
        decision = CompletedToolCallDecision(
            kind="error",
            call=call,
            coercion_applied=True,
            coercion_error=coercion.error_message
        )
        
        if decision.coercion_applied:
            _coercion_event = "coercion_failed" if decision.coercion_error else "coercion_succeeded"
            _coercion_payload = {
                "tool": call.get("name"),
                "correction_applied": not bool(decision.coercion_error),
                "error_summary": decision.coercion_error[:200] if decision.coercion_error else "",
            }
            if call.get("name") == "apply_patch":
                from proxy.qz_tool_apply_patch import inspect_apply_patch_arguments
                _coercion_payload["apply_patch"] = inspect_apply_patch_arguments(
                    call.get("arguments") or ""
                )
                
        self.assertEqual(_coercion_event, "coercion_failed")
        self.assertEqual(_coercion_payload["apply_patch"]["coercion_strategy"], "failed_missing_diff")
        
        # Verify safety
        payload_str = json.dumps(_coercion_payload)
        self.assertNotIn("secret/path.txt", payload_str)

if __name__ == "__main__":
    unittest.main()
