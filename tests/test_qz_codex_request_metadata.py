import json
import unittest

from proxy.qz_codex_metadata import (
    extract_codex_body_metadata,
    extract_codex_identity,
    extract_codex_request_context,
    parse_codex_window_id,
)


class CodexRequestMetadataTests(unittest.TestCase):
    def test_parse_codex_window_id_valid(self):
        tid, gen = parse_codex_window_id("thread-abc:42")
        self.assertEqual(tid, "thread-abc")
        self.assertEqual(gen, 42)

    def test_parse_codex_window_id_rsplit_thread_with_colon(self):
        # Handle thread IDs that might contain colons
        tid, gen = parse_codex_window_id("namespace:thread-abc:42")
        self.assertEqual(tid, "namespace:thread-abc")
        self.assertEqual(gen, 42)

    def test_parse_codex_window_id_invalid_missing_generation(self):
        tid, gen = parse_codex_window_id("thread-abc")
        self.assertIsNone(tid)
        self.assertIsNone(gen)

    def test_parse_codex_window_id_invalid_negative_generation(self):
        tid, gen = parse_codex_window_id("thread-abc:-1")
        self.assertIsNone(tid)
        self.assertIsNone(gen)

    def test_extract_identity_parses_window_thread_and_generation(self):
        identity = extract_codex_identity({
            "x-codex-window-id": "thread-abc:42",
        })
        self.assertEqual(identity.codex_window_thread_id, "thread-abc")
        self.assertEqual(identity.codex_window_generation, 42)
        self.assertFalse(identity.codex_window_parse_error)

    def test_extract_identity_window_thread_conflict_detected(self):
        identity = extract_codex_identity({
            "thread_id": "thread-A",
            "x-codex-window-id": "thread-B:42",
        })
        self.assertEqual(identity.client_thread_id, "thread-A")
        self.assertEqual(identity.codex_window_thread_id, "thread-B")
        self.assertTrue(identity.identity_conflict)
        self.assertTrue(any("differs from window_id thread" in note for note in identity.conflict_notes))

    def test_extract_identity_captures_turn_state_raw(self):
        identity = extract_codex_identity({
            "x-codex-turn-state": "base64-token-123",
        })
        self.assertEqual(identity.codex_turn_state_raw, "base64-token-123")

    def test_extract_identity_captures_installation_id_header(self):
        identity = extract_codex_identity({
            "x-codex-installation-id": "inst-abc",
        })
        self.assertEqual(identity.installation_id, "inst-abc")

    def test_extract_identity_captures_parent_thread_id(self):
        identity = extract_codex_identity({
            "x-codex-parent-thread-id": "parent-123",
        })
        self.assertEqual(identity.parent_thread_id, "parent-123")

    def test_extract_identity_captures_subagent_header(self):
        identity = extract_codex_identity({
            "x-openai-subagent": "generalist",
        })
        self.assertEqual(identity.subagent, "generalist")

    def test_extract_identity_captures_memgen_true(self):
        for val in ("true", "1", "yes", " TRUE "):
            with self.subTest(val=val):
                identity = extract_codex_identity({
                    "x-openai-memgen-request": val,
                })
                self.assertTrue(identity.is_memgen)

    def test_extract_identity_captures_memgen_false(self):
        for val in ("false", "0", "no", "anything-else", ""):
            with self.subTest(val=val):
                identity = extract_codex_identity({
                    "x-openai-memgen-request": val,
                })
                self.assertFalse(identity.is_memgen)

    def test_extract_body_metadata_extracts_previous_response_id(self):
        meta = extract_codex_body_metadata({"previous_response_id": "resp-123"})
        self.assertEqual(meta.previous_response_id, "resp-123")

    def test_extract_body_metadata_extracts_prompt_cache_key(self):
        meta = extract_codex_body_metadata({"prompt_cache_key": "cache-abc"})
        self.assertEqual(meta.prompt_cache_key, "cache-abc")

    def test_extract_body_metadata_extracts_reasoning_effort_summary(self):
        meta = extract_codex_body_metadata({
            "reasoning": {
                "effort": "high",
                "summary": "Thinking hard..."
            }
        })
        self.assertEqual(meta.reasoning_effort, "high")
        self.assertEqual(meta.reasoning_summary, "Thinking hard...")

    def test_extract_body_metadata_extracts_service_tier(self):
        meta = extract_codex_body_metadata({"service_tier": "pro"})
        self.assertEqual(meta.service_tier, "pro")

    def test_extract_body_metadata_extracts_client_metadata(self):
        meta = extract_codex_body_metadata({
            "client_metadata": {"cwd": "/tmp", "key": "val"}
        })
        self.assertEqual(meta.client_metadata, {"cwd": "/tmp", "key": "val"})

    def test_extract_body_metadata_extracts_installation_id_from_client_metadata(self):
        meta = extract_codex_body_metadata({
            "client_metadata": {"x-codex-installation-id": "inst-body-123"}
        })
        self.assertEqual(meta.client_installation_id_from_body, "inst-body-123")

    def test_extract_body_metadata_summarises_tools_without_full_schema(self):
        body = {
            "tools": [
                {"type": "function", "function": {"name": "read_file", "parameters": {}}},
                {"type": "function", "function": {"name": "write_file"}},
                {"type": "web_search"}
            ]
        }
        meta = extract_codex_body_metadata(body)
        self.assertEqual(meta.tools_count, 3)
        self.assertEqual(meta.tool_names, ["read_file", "write_file", "web_search"])

    def test_extract_body_metadata_detects_output_schema_without_storing_blob(self):
        body = {
            "text": {
                "format": "json_schema",
                "schema": {"type": "object"}
            }
        }
        meta = extract_codex_body_metadata(body)
        self.assertTrue(meta.has_output_schema)

    def test_extract_body_metadata_handles_malformed_body(self):
        self.assertIsNotNone(extract_codex_body_metadata(None))
        self.assertIsNotNone(extract_codex_body_metadata([]))
        self.assertIsNotNone(extract_codex_body_metadata("string"))

    def test_request_context_detects_installation_id_conflict(self):
        headers = {"x-codex-installation-id": "inst-A"}
        body = {"client_metadata": {"x-codex-installation-id": "inst-B"}}
        ctx = extract_codex_request_context(headers, body)
        self.assertTrue(ctx.identity.identity_conflict)
        self.assertTrue(any("installation_id header (inst-A) differs" in note for note in ctx.identity.conflict_notes))


if __name__ == "__main__":
    unittest.main()
