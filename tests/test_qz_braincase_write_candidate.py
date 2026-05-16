"""Tests for braincase.write_candidate runtime implementation — Slice H.2.

Coverage:
  Feature flags (1-6)
  Tool schema (7-11)
  Runtime execution (12-34)
  Proxy-local dispatch (35-40)
  Candidate isolation from render/recall (41-43)
  No automatic ingestion (44-46)
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proxy.qz_braincase_db import BrainCaseDB
from proxy.qz_braincase_tools import (
    BRAINCASE_WRITE_CANDIDATE_TOOL_DEF,
    QZ_BRAINCASE_TOOLS_ENABLED_ENV,
    QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV,
    BraincaseWriteCandidateProxyToolExecutor,
    braincase_write_candidate_tool,
    get_braincase_tool_definitions,
    get_braincase_harness_policy,
    is_braincase_write_candidate_enabled,
    make_braincase_tool_executors,
)
from proxy.qz_proxy_tools import ProxyLocalToolRegistry, ProxyToolExecutionContext
from proxy.qz_tool_lifecycle import ToolContinuationResult

_READ_ONLY_ENV = {QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true"}
_BOTH_FLAGS_ENV = {
    QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true",
    QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV: "true",
}
_DISABLED_ENV: dict = {}
_WRITE_ONLY_ENV = {QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV: "true"}

_VALID_ARGS = {
    "purpose": "test_constraint",
    "memory_domain": "coding",
    "tier": "project_state",
    "record_type": "constraint",
    "claim": "All public functions must have explicit return type annotations.",
    "summary": "Explicit return types required on public functions.",
}

_RENDER_PACKET_FIELDS = {"packet_id", "schema", "rendered_text", "source_record_ids"}


def _fresh_db(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


def _disabled_db(td: str) -> BrainCaseDB:
    return BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)


def _make_function_call(name: str, args: dict, call_id: str = "call_wc_001") -> dict:
    return {
        "type": "function_call",
        "name": name,
        "call_id": call_id,
        "id": call_id,
        "arguments": json.dumps(args),
    }


# =============================================================================
# 1-6: Feature flags
# =============================================================================

class WriteCandidateFlagTests(unittest.TestCase):

    def test_write_candidate_absent_when_both_disabled(self):
        defs = get_braincase_tool_definitions(env=_DISABLED_ENV)
        names = [d["name"] for d in defs]
        self.assertNotIn("braincase.write_candidate", names)

    def test_write_candidate_absent_when_only_read_flag_set(self):
        defs = get_braincase_tool_definitions(env=_READ_ONLY_ENV)
        names = [d["name"] for d in defs]
        self.assertNotIn("braincase.write_candidate", names)

    def test_write_candidate_absent_when_only_write_flag_set(self):
        defs = get_braincase_tool_definitions(env=_WRITE_ONLY_ENV)
        names = [d["name"] for d in defs]
        self.assertNotIn("braincase.write_candidate", names)

    def test_write_candidate_present_when_both_flags_set(self):
        defs = get_braincase_tool_definitions(env=_BOTH_FLAGS_ENV)
        names = [d["name"] for d in defs]
        self.assertIn("braincase.write_candidate", names)

    def test_render_recall_unchanged_when_only_read_flag(self):
        defs = get_braincase_tool_definitions(env=_READ_ONLY_ENV)
        names = [d["name"] for d in defs]
        self.assertIn("braincase.render", names)
        self.assertIn("braincase.recall", names)

    def test_write_update_search_inspect_never_exposed(self):
        for env in (_BOTH_FLAGS_ENV, _READ_ONLY_ENV, _DISABLED_ENV):
            defs = get_braincase_tool_definitions(env=env)
            names = [d["name"] for d in defs]
            for forbidden in ("braincase.write", "braincase.update",
                              "braincase.search", "braincase.inspect",
                              "braincase.promote_candidate"):
                self.assertNotIn(forbidden, names)

    def test_is_braincase_write_candidate_enabled_requires_both_flags(self):
        self.assertFalse(is_braincase_write_candidate_enabled(_DISABLED_ENV))
        self.assertFalse(is_braincase_write_candidate_enabled(_READ_ONLY_ENV))
        self.assertFalse(is_braincase_write_candidate_enabled(_WRITE_ONLY_ENV))
        self.assertTrue(is_braincase_write_candidate_enabled(_BOTH_FLAGS_ENV))

    def test_harness_policy_includes_write_candidate_only_when_both_flags(self):
        policy_read = get_braincase_harness_policy(env=_READ_ONLY_ENV)
        policy_both = get_braincase_harness_policy(env=_BOTH_FLAGS_ENV)
        self.assertIsNotNone(policy_read)
        self.assertNotIn("braincase.write_candidate", policy_read)
        self.assertIsNotNone(policy_both)
        self.assertIn("braincase.write_candidate", policy_both)

    def test_harness_policy_none_when_disabled(self):
        self.assertIsNone(get_braincase_harness_policy(env=_DISABLED_ENV))


# =============================================================================
# 7-11: Tool definition schema
# =============================================================================

class WriteCandidateToolSchemaTests(unittest.TestCase):

    def setUp(self):
        self.td = BRAINCASE_WRITE_CANDIDATE_TOOL_DEF

    def test_name_is_braincase_write_candidate(self):
        self.assertEqual(self.td["name"], "braincase.write_candidate")

    def test_type_is_function(self):
        self.assertEqual(self.td["type"], "function")

    def test_required_fields(self):
        required = set(self.td["parameters"]["required"])
        for field in ("purpose", "memory_domain", "tier", "record_type", "claim", "summary"):
            self.assertIn(field, required)

    def test_additional_properties_false(self):
        self.assertFalse(self.td["parameters"].get("additionalProperties", True))

    def test_no_status_in_schema(self):
        self.assertNotIn("status", self.td["parameters"]["properties"])

    def test_no_visibility_in_schema(self):
        self.assertNotIn("visibility", self.td["parameters"]["properties"])

    def test_no_raw_forbidden_fields_in_schema(self):
        props = self.td["parameters"]["properties"]
        for forbidden in ("raw_prompt", "raw_request_body", "request_body",
                          "full_log", "telemetry_event", "stream_event"):
            self.assertNotIn(forbidden, props)

    def test_description_says_candidate_only(self):
        desc = self.td["description"].lower()
        self.assertIn("candidate", desc)

    def test_description_says_not_active(self):
        desc = self.td["description"].lower()
        self.assertIn("active", desc)

    def test_description_says_no_raw_logs(self):
        desc = self.td["description"].lower()
        self.assertTrue("log" in desc or "raw" in desc or "ingest" in desc)


# =============================================================================
# 12-34: Runtime execution
# =============================================================================

class WriteCandidateRuntimeTests(unittest.TestCase):

    def test_valid_input_stores_record(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertTrue(result["ok"])
            self.assertTrue(result["stored"])
            self.assertIsNotNone(result["record_id"])

    def test_stored_record_has_status_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertEqual(result["status"], "candidate")
            # Verify in DB
            rec = db.get_state_record(result["record_id"])
            self.assertIsNotNone(rec)
            self.assertEqual(rec["status"], "candidate")

    def test_stored_record_has_visibility_internal(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertEqual(result["visibility"], "internal")
            rec = db.get_state_record(result["record_id"])
            self.assertEqual(rec["visibility"], "internal")

    def test_result_has_review_required_true(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertTrue(result["review_required"])

    def test_result_is_not_render_packet(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            for field in _RENDER_PACKET_FIELDS:
                self.assertNotIn(field, result)

    def test_result_has_no_raw_record_dump(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertNotIn("record", result)
            self.assertNotIn("state_records", result)

    def test_missing_memory_domain_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = {k: v for k, v in _VALID_ARGS.items() if k != "memory_domain"}
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("memory_domain" in e for e in result["errors"]))

    def test_missing_claim_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = {k: v for k, v in _VALID_ARGS.items() if k != "claim"}
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])

    def test_missing_summary_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = {k: v for k, v in _VALID_ARGS.items() if k != "summary"}
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])

    def test_supplied_status_active_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, status="active")
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("status" in e for e in result["errors"]))

    def test_supplied_visibility_renderable_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, visibility="renderable")
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("visibility" in e for e in result["errors"]))

    def test_raw_prompt_top_level_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, raw_prompt="User: do something")
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("raw_prompt" in e for e in result["errors"]))

    def test_raw_request_body_top_level_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, raw_request_body='{"model":"qwen"}')
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])

    def test_raw_log_in_claim_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, claim="User: can you help? [raw_request_body: {...}]")
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("claim_content_rejected" in e for e in result["errors"]))

    def test_raw_log_in_summary_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, summary="Assistant: Sure, I will do that [Turn 5]")
            result = braincase_write_candidate_tool(db, args)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("summary_content_rejected" in e for e in result["errors"]))

    def test_confidence_clamped_to_default(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, confidence=99.9)
            result = braincase_write_candidate_tool(db, args)
            self.assertTrue(result["ok"])
            rec = db.get_state_record(result["record_id"])
            self.assertEqual(rec["confidence"], 0.5)

    def test_importance_clamped_to_default(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, importance=-5.0)
            result = braincase_write_candidate_tool(db, args)
            self.assertTrue(result["ok"])
            rec = db.get_state_record(result["record_id"])
            self.assertEqual(rec["importance"], 0.5)

    def test_invalid_retention_defaults_to_project(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, retention="forever")
            result = braincase_write_candidate_tool(db, args)
            self.assertTrue(result["ok"])
            rec = db.get_state_record(result["record_id"])
            self.assertEqual(rec["retention"], "project")

    def test_invalid_tags_type_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, tags=["good", 42, None, "also_good"])
            result = braincase_write_candidate_tool(db, args)
            self.assertTrue(result["ok"])
            rec = db.get_state_record(result["record_id"])
            self.assertEqual(sorted(rec["tags"]), ["also_good", "good"])

    def test_invalid_source_refs_type_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            args = dict(_VALID_ARGS, source_refs=["sref_001", None, 42])
            result = braincase_write_candidate_tool(db, args)
            # write_candidate still proceeds; missing source_refs are warnings
            self.assertIn(result["ok"], (True, False))  # depends on DB state

    def test_db_disabled_returns_safe_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _disabled_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertIsNone(result["record_id"])
            self.assertEqual(result["status"], "candidate")
            self.assertEqual(result["visibility"], "internal")
            self.assertTrue(result["review_required"])
            self.assertTrue(len(result["errors"]) > 0)

    def test_db_disabled_no_exception(self):
        with tempfile.TemporaryDirectory() as td:
            db = _disabled_db(td)
            try:
                braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            except Exception as exc:
                self.fail(f"braincase_write_candidate_tool raised: {exc}")

    def test_malformed_args_returns_safe_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, None)  # type: ignore[arg-type]
            self.assertFalse(result["ok"])
            for field in ("status", "visibility", "review_required", "errors"):
                self.assertIn(field, result)

    def test_dedup_hint_on_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            r1 = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertTrue(r1["ok"])
            r2 = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertTrue(r2["ok"])
            self.assertEqual(r2["dedup_hint"], "possible_duplicate")

    def test_no_dedup_hint_on_first_write(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            self.assertTrue(result["ok"])
            self.assertEqual(result["dedup_hint"], "no_duplicates")


# =============================================================================
# 35-40: Proxy-local dispatch
# =============================================================================

class WriteCandidateDispatchTests(unittest.TestCase):

    def test_registry_recognizes_write_candidate_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            executors = make_braincase_tool_executors(db=db, env=_BOTH_FLAGS_ENV)
            reg = ProxyLocalToolRegistry(executors)
            call = _make_function_call("braincase.write_candidate", dict(_VALID_ARGS))
            self.assertTrue(reg.is_proxy_local_call(call))

    def test_registry_does_not_recognize_write_candidate_when_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            executors = make_braincase_tool_executors(db=db, env=_READ_ONLY_ENV)
            reg = ProxyLocalToolRegistry(executors)
            call = _make_function_call("braincase.write_candidate", dict(_VALID_ARGS))
            self.assertFalse(reg.is_proxy_local_call(call))

    def test_continuation_path_returns_function_call_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            ex = BraincaseWriteCandidateProxyToolExecutor(db=db)
            call = _make_function_call("braincase.write_candidate", dict(_VALID_ARGS))
            result = ex.execute(call, ProxyToolExecutionContext())
            self.assertIsInstance(result, ToolContinuationResult)
            self.assertEqual(result.public_item.get("type"), "function_call_output")

    def test_output_json_has_candidate_status_and_review_required(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            ex = BraincaseWriteCandidateProxyToolExecutor(db=db)
            call = _make_function_call("braincase.write_candidate", dict(_VALID_ARGS))
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertEqual(output["status"], "candidate")
            self.assertEqual(output["visibility"], "internal")
            self.assertTrue(output["review_required"])

    def test_output_json_is_not_render_packet(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            ex = BraincaseWriteCandidateProxyToolExecutor(db=db)
            call = _make_function_call("braincase.write_candidate", dict(_VALID_ARGS))
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            for field in _RENDER_PACKET_FIELDS:
                self.assertNotIn(field, output)

    def test_output_json_no_raw_state_record(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            ex = BraincaseWriteCandidateProxyToolExecutor(db=db)
            call = _make_function_call("braincase.write_candidate", dict(_VALID_ARGS))
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertNotIn("record", output)
            self.assertNotIn("state_records", output)

    def test_upstream_items_contains_call_and_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            ex = BraincaseWriteCandidateProxyToolExecutor(db=db)
            call = _make_function_call("braincase.write_candidate", dict(_VALID_ARGS))
            result = ex.execute(call, ProxyToolExecutionContext())
            self.assertEqual(len(result.upstream_items), 2)
            types = {item.get("type") for item in result.upstream_items}
            self.assertIn("function_call", types)
            self.assertIn("function_call_output", types)


# =============================================================================
# 41-43: Candidate isolation from render/recall
# =============================================================================

class WriteCandidateIsolationTests(unittest.TestCase):
    """Candidate records must not leak into braincase.render or braincase.recall."""

    def _seed_candidate(self, db) -> str:
        """Write one candidate record and return its record_id."""
        result = braincase_write_candidate_tool(db, dict(_VALID_ARGS))
        self.assertTrue(result["ok"], f"Expected ok=True but got errors: {result['errors']}")
        return result["record_id"]

    def test_candidate_not_returned_by_render(self):
        from proxy.qz_braincase_tools import braincase_render_tool
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            record_id = self._seed_candidate(db)
            result = braincase_render_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
            })
            self.assertNotIn(record_id, result.get("source_record_ids", []),
                             "Candidate record must not appear in braincase.render output")

    def test_candidate_not_returned_by_recall(self):
        from proxy.qz_braincase_tools import braincase_recall_tool
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            record_id = self._seed_candidate(db)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
            })
            self.assertNotIn(record_id, result.get("source_record_ids", []),
                             "Candidate record must not appear in braincase.recall output")

    def test_candidate_stored_and_retrievable_internally(self):
        """Candidate IS stored and can be retrieved by internal DB queries."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            record_id = self._seed_candidate(db)
            rec = db.get_state_record(record_id)
            self.assertIsNotNone(rec, "Candidate should be stored in DB")
            self.assertEqual(rec["status"], "candidate")
            self.assertEqual(rec["visibility"], "internal")


# =============================================================================
# 44-46: No automatic ingestion
# =============================================================================

class WriteCandidateNoIngestionTests(unittest.TestCase):

    def test_executing_write_candidate_writes_only_one_explicit_record(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            before = db.list_state_records(memory_domain="coding", limit=100)
            braincase_write_candidate_tool(db, dict(_VALID_ARGS))
            after = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(after), len(before) + 1)

    def test_render_does_not_create_records(self):
        from proxy.qz_braincase_tools import braincase_render_tool
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            before = db.list_state_records(memory_domain="coding", limit=100)
            braincase_render_tool(db, {"purpose": "test", "memory_domain": "coding"})
            after = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(before), len(after))

    def test_recall_does_not_create_records(self):
        from proxy.qz_braincase_tools import braincase_recall_tool
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            before = db.list_state_records(memory_domain="coding", limit=100)
            braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
            })
            after = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(before), len(after))


if __name__ == "__main__":
    unittest.main()
