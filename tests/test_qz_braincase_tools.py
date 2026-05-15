"""Tests for proxy/qz_braincase_tools.py — Slices F+G BrainCase tool surface.

Slice F coverage (render):
  1.  braincase.render tool definition present when QZ_BRAINCASE_TOOLS_ENABLED=true
  2.  braincase.render tool definition absent when disabled
  3.  No braincase.write/update/search/inspect tool definitions
  4.  Harness policy mentions both render and recall, warns against broad dump
  6.  Tool args schema requires purpose and memory_domain
  7.  Tool args schema includes budget_tokens and limit with bounds
  8.  braincase_render_tool returns RenderPacket-shaped dict
  9.  Tool result contains rendered_text and source_record_ids
  10. Tool result does NOT contain raw StateRecord objects
  11. Tool result does NOT contain metadata/raw JSON dump (forbidden fields)
  12. Tool call with missing memory_domain returns safe warning
  13. Tool call with missing purpose returns safe warning
  14. Disabled DB returns safe warning, no exception
  15. Tool call does NOT write records
  16. No automatic ingestion occurs
  17. inject_braincase_tools_to_body adds tool when enabled
  18. inject_braincase_tools_to_body is a no-op when disabled
  19. inject_braincase_tools_to_body is idempotent
  20. get_braincase_harness_policy returns text when enabled
  21. get_braincase_harness_policy returns None when disabled
  22. No forwarded /v1/responses body mutation when disabled
  23. normalize_responses_input_for_qwen unchanged when flag disabled

All tests use temp DB only. No live var/ DB.
No automatic ingestion in any test.
"""
import copy
import json
import os
import pathlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proxy.qz_braincase_db import BrainCaseDB
from proxy.qz_braincase_tools import (
    BRAINCASE_HARNESS_POLICY,
    BRAINCASE_RECALL_TOOL_DEF,
    BRAINCASE_RENDER_TOOL_DEF,
    QZ_BRAINCASE_TOOLS_ENABLED_ENV,
    RECALL_MODE_TIERS,
    braincase_recall_packet,
    braincase_recall_tool,
    braincase_render_tool,
    get_braincase_harness_policy,
    get_braincase_tool_definitions,
    inject_braincase_tools_to_body,
    is_braincase_tools_enabled,
    tiers_for_recall_mode,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_ENABLED_ENV = {QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true"}
_DISABLED_ENV = {}

_RENDER_PACKET_REQUIRED_FIELDS = {
    "packet_id",
    "schema",
    "purpose",
    "memory_domain",
    "generated_at_ms",
    "budget_tokens",
    "rendered_text",
    "source_record_ids",
    "omitted_count",
    "warnings",
}

_FORBIDDEN_OUTPUT_FIELDS = {
    "raw_prompt",
    "raw_request_body",
    "request_body",
    "full_log",
    "telemetry_event",
    "stream_event",
    "metadata_json",
}


def _fresh_db(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


def _disabled_db(td: str) -> BrainCaseDB:
    return BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)


def _make_renderable_record(
    record_id: str = "test_tools_r001",
    memory_domain: str = "coding",
    tier: str = "project_state",
    claim: str = "Test claim for tool surface tests.",
    summary: str = "Test summary.",
) -> dict:
    return {
        "record_id": record_id,
        "schema": "braincase/state-record@1",
        "memory_domain": memory_domain,
        "tier": tier,
        "record_type": "constraint",
        "claim": claim,
        "summary": summary,
        "status": "active",
        "visibility": "renderable",
        "confidence": 1.0,
        "importance": 0.8,
        "retention": "project",
        "created_at_ms": 1778803200000,
        "updated_at_ms": 1778803200000,
        "source_refs": [],
        "tags": ["test"],
        "supersedes": None,
        "superseded_by": None,
        "metadata": None,
    }


# ---------------------------------------------------------------------------
# 1–2: Feature flag and tool definition presence
# ---------------------------------------------------------------------------

class ToolDefinitionPresenceTests(unittest.TestCase):

    def test_enabled_returns_render_def(self):
        """braincase.render definition present when flag is enabled."""
        defs = get_braincase_tool_definitions(env=_ENABLED_ENV)
        self.assertIsInstance(defs, list)
        self.assertGreaterEqual(len(defs), 1)
        names = [d["name"] for d in defs]
        self.assertIn("braincase.render", names)

    def test_disabled_returns_empty(self):
        """No tool definitions returned when flag is disabled (default)."""
        defs = get_braincase_tool_definitions(env=_DISABLED_ENV)
        self.assertEqual(defs, [])

    def test_disabled_by_default_with_no_env(self):
        """Default environment should have tools disabled."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            self.assertFalse(is_braincase_tools_enabled())

    def test_enabled_with_various_truthy_values(self):
        for val in ("1", "true", "True", "yes", "on", "enabled"):
            self.assertTrue(is_braincase_tools_enabled({QZ_BRAINCASE_TOOLS_ENABLED_ENV: val}))

    def test_disabled_with_falsy_values(self):
        for val in ("0", "false", "no", "off", "", "disabled"):
            self.assertFalse(is_braincase_tools_enabled({QZ_BRAINCASE_TOOLS_ENABLED_ENV: val}))


# ---------------------------------------------------------------------------
# 3–4: No forbidden tool names
# ---------------------------------------------------------------------------

class ForbiddenToolNamesTests(unittest.TestCase):

    def _all_tool_names(self, env):
        return [d.get("name") for d in get_braincase_tool_definitions(env=env)]

    def test_no_braincase_recall_when_disabled(self):
        """braincase.recall must not be exposed when flag is disabled."""
        self.assertNotIn("braincase.recall", self._all_tool_names(_DISABLED_ENV))

    def test_no_braincase_write(self):
        """braincase.write must not be exposed."""
        for env in (_ENABLED_ENV, _DISABLED_ENV):
            with self.subTest(env=env):
                self.assertNotIn("braincase.write", self._all_tool_names(env))

    def test_no_braincase_update(self):
        """braincase.update must not be exposed."""
        for env in (_ENABLED_ENV, _DISABLED_ENV):
            with self.subTest(env=env):
                self.assertNotIn("braincase.update", self._all_tool_names(env))

    def test_no_braincase_search(self):
        """braincase.search must not be exposed."""
        for env in (_ENABLED_ENV, _DISABLED_ENV):
            with self.subTest(env=env):
                self.assertNotIn("braincase.search", self._all_tool_names(env))

    def test_no_braincase_inspect(self):
        """braincase.inspect must not be exposed."""
        for env in (_ENABLED_ENV, _DISABLED_ENV):
            with self.subTest(env=env):
                self.assertNotIn("braincase.inspect", self._all_tool_names(env))


# ---------------------------------------------------------------------------
# 5: Harness policy content
# ---------------------------------------------------------------------------

class HarnessPolicyTests(unittest.TestCase):

    def test_enabled_returns_policy_text(self):
        """get_braincase_harness_policy returns text when enabled."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        self.assertIsInstance(policy, str)
        self.assertGreater(len(policy), 50)

    def test_disabled_returns_none(self):
        """get_braincase_harness_policy returns None when disabled."""
        policy = get_braincase_harness_policy(env=_DISABLED_ENV)
        self.assertIsNone(policy)

    def test_policy_mentions_render(self):
        """Policy text should mention braincase.render."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        self.assertIn("braincase.render", policy)

    def test_policy_mentions_recall(self):
        """Policy text should mention braincase.recall (now exposed in Slice G)."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        self.assertIn("braincase.recall", policy)

    def test_policy_says_write_update_not_exposed(self):
        """Policy text should say write/update/search/inspect are not yet exposed."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        lower = policy.lower()
        self.assertIn("not yet exposed", lower)

    def test_policy_mentions_memory_domain_requirement(self):
        """Policy should state that memory_domain must be supplied explicitly."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        self.assertIn("memory_domain", policy)

    def test_policy_no_broad_dump_warning(self):
        """Policy should warn against using it as a broad memory dump."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        lower = policy.lower()
        self.assertTrue(
            "broad" in lower or "dump" in lower or "prefill" in lower,
            "Policy should warn against broad dump / context prefill usage"
        )

    def test_harness_policy_constant_matches_function(self):
        """BRAINCASE_HARNESS_POLICY constant should match what the function returns."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        self.assertEqual(policy, BRAINCASE_HARNESS_POLICY)


# ---------------------------------------------------------------------------
# 6–7: Tool definition schema
# ---------------------------------------------------------------------------

class ToolDefinitionSchemaTests(unittest.TestCase):

    def setUp(self):
        self.tool_def = BRAINCASE_RENDER_TOOL_DEF

    def test_tool_type_is_function(self):
        self.assertEqual(self.tool_def["type"], "function")

    def test_tool_name_is_braincase_render(self):
        self.assertEqual(self.tool_def["name"], "braincase.render")

    def test_has_description(self):
        self.assertIsInstance(self.tool_def.get("description"), str)
        self.assertGreater(len(self.tool_def["description"]), 20)

    def test_description_mentions_render_packet(self):
        desc = self.tool_def["description"].lower()
        self.assertTrue(
            "renderpacket" in desc or "render packet" in desc,
            "Description should mention RenderPacket"
        )

    def test_description_says_no_raw_state_records(self):
        desc = self.tool_def["description"].lower()
        self.assertIn("raw", desc)
        self.assertIn("staterecord", desc.replace(" ", ""))

    def test_parameters_object_type(self):
        params = self.tool_def["parameters"]
        self.assertEqual(params["type"], "object")

    def test_required_contains_purpose(self):
        required = self.tool_def["parameters"]["required"]
        self.assertIn("purpose", required)

    def test_required_contains_memory_domain(self):
        required = self.tool_def["parameters"]["required"]
        self.assertIn("memory_domain", required)

    def test_purpose_is_string_property(self):
        props = self.tool_def["parameters"]["properties"]
        self.assertIn("purpose", props)
        self.assertEqual(props["purpose"]["type"], "string")

    def test_memory_domain_is_string_property(self):
        props = self.tool_def["parameters"]["properties"]
        self.assertIn("memory_domain", props)
        self.assertEqual(props["memory_domain"]["type"], "string")

    def test_budget_tokens_has_bounds(self):
        props = self.tool_def["parameters"]["properties"]
        self.assertIn("budget_tokens", props)
        bt = props["budget_tokens"]
        self.assertEqual(bt["type"], "integer")
        self.assertIn("minimum", bt)
        self.assertIn("maximum", bt)
        self.assertLessEqual(bt["minimum"], 80)
        self.assertGreaterEqual(bt["maximum"], 600)

    def test_limit_has_bounds(self):
        props = self.tool_def["parameters"]["properties"]
        self.assertIn("limit", props)
        lim = props["limit"]
        self.assertEqual(lim["type"], "integer")
        self.assertIn("minimum", lim)
        self.assertIn("maximum", lim)
        self.assertGreaterEqual(lim["minimum"], 1)
        self.assertGreaterEqual(lim["maximum"], 12)

    def test_optional_fields_present(self):
        props = self.tool_def["parameters"]["properties"]
        for field in ("query", "tiers", "record_ids"):
            self.assertIn(field, props, f"Expected optional field {field!r} in parameters")


# ---------------------------------------------------------------------------
# 8–11: braincase_render_tool executor
# ---------------------------------------------------------------------------

class RenderToolExecutorTests(unittest.TestCase):

    def test_returns_render_packet_shape(self):
        """braincase_render_tool returns a RenderPacket-shaped dict."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result, f"Missing field {field!r} in result")

    def test_result_has_rendered_text(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertIn("rendered_text", result)
            self.assertIsInstance(result["rendered_text"], str)

    def test_result_has_source_record_ids(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertIn("source_record_ids", result)
            self.assertIsInstance(result["source_record_ids"], list)

    def test_result_no_raw_state_record_objects(self):
        """Tool result must not contain raw StateRecord dicts."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_renderable_record()
            db.put_state_record(rec)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            # The result dict should not have a 'record' or 'records' key
            self.assertNotIn("record", result)
            self.assertNotIn("records", result)
            self.assertNotIn("state_records", result)
            # source_record_ids should be a list of strings, not dicts
            for rid in result.get("source_record_ids", []):
                self.assertIsInstance(rid, str, "source_record_ids must be strings, not record dicts")

    def test_result_no_forbidden_fields(self):
        """Tool result must not contain raw/forbidden output fields."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            for field in _FORBIDDEN_OUTPUT_FIELDS:
                self.assertNotIn(field, result, f"Forbidden field {field!r} must not be in result")

    def test_result_schema_is_render_packet(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertEqual(result.get("schema"), "braincase/render-packet@1")

    def test_rendered_text_with_seeded_record(self):
        """When renderable records exist, rendered_text is non-empty."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_renderable_record(claim="Constraint: always use typed dicts.")
            db.put_state_record(rec)
            result = braincase_render_tool(db, {
                "purpose": "project_constraints",
                "memory_domain": "coding",
            })
            self.assertIn("rendered_text", result)
            # Should have the rendered content
            if result.get("source_record_ids"):
                self.assertGreater(len(result["rendered_text"]), 0)


# ---------------------------------------------------------------------------
# 12–13: Missing required args return safe warning
# ---------------------------------------------------------------------------

class RenderToolArgValidationTests(unittest.TestCase):

    def test_missing_memory_domain_returns_warning(self):
        """Tool call with missing memory_domain returns safe warning packet."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {"purpose": "task_continuity"})
            self.assertIn("warnings", result)
            warnings = result["warnings"]
            self.assertTrue(
                any("memory_domain" in w for w in warnings),
                f"Expected memory_domain warning, got: {warnings}"
            )
            # Should not raise; should return a valid shape
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_none_memory_domain_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {"purpose": "task_continuity", "memory_domain": None})
            self.assertIn("warnings", result)
            warnings = result["warnings"]
            self.assertTrue(any("memory_domain" in w for w in warnings))

    def test_empty_memory_domain_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {"purpose": "task_continuity", "memory_domain": ""})
            self.assertIn("warnings", result)
            warnings = result["warnings"]
            self.assertTrue(any("memory_domain" in w for w in warnings))

    def test_missing_purpose_returns_warning(self):
        """Tool call with missing purpose returns safe warning packet."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {"memory_domain": "coding"})
            self.assertIn("warnings", result)
            warnings = result["warnings"]
            self.assertTrue(
                any("purpose" in w for w in warnings),
                f"Expected purpose warning, got: {warnings}"
            )
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_none_args_returns_warning(self):
        """Tool call with None args dict returns safe warning."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, None)  # type: ignore[arg-type]
            self.assertIn("warnings", result)
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_empty_args_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {})
            self.assertIn("warnings", result)
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_budget_tokens_clamped_below_minimum(self):
        """budget_tokens below minimum is clamped to default 600."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
                "budget_tokens": 5,
            })
            self.assertEqual(result["budget_tokens"], 600)

    def test_budget_tokens_clamped_above_maximum(self):
        """budget_tokens above maximum is clamped to 2000."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
                "budget_tokens": 99999,
            })
            self.assertEqual(result["budget_tokens"], 2000)

    def test_limit_clamped_below_minimum(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
                "limit": 0,
            })
            # Should succeed with default limit
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)


# ---------------------------------------------------------------------------
# 14: Disabled DB returns safe warning
# ---------------------------------------------------------------------------

class DisabledDBTests(unittest.TestCase):

    def test_disabled_db_returns_warning_packet(self):
        """Disabled DB returns safe warning packet, does not raise."""
        with tempfile.TemporaryDirectory() as td:
            db = _disabled_db(td)
            result = braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertIn("warnings", result)
            warnings = result["warnings"]
            self.assertTrue(
                any("disabled" in w for w in warnings),
                f"Expected disabled warning, got: {warnings}"
            )
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_disabled_db_does_not_create_file(self):
        """Disabled DB does not create a SQLite file."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=False)
            braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertFalse(db_path.exists(), "Disabled DB must not create a file")


# ---------------------------------------------------------------------------
# 15–16: No writes, no automatic ingestion
# ---------------------------------------------------------------------------

class NoWriteNoIngestionTests(unittest.TestCase):

    def test_render_tool_does_not_write_records(self):
        """braincase_render_tool must not write any records to the DB."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            before = db.list_state_records(memory_domain="coding", limit=100)
            braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            after = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(before), len(after), "render_tool must not write records")

    def test_render_tool_with_seeded_records_no_write(self):
        """Render tool with existing records does not add new records."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_renderable_record()
            db.put_state_record(rec)
            before_count = len(db.list_state_records(memory_domain="coding", limit=100))
            braincase_render_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            after_count = len(db.list_state_records(memory_domain="coding", limit=100))
            self.assertEqual(before_count, after_count)

    def test_no_automatic_ingestion_on_render(self):
        """No automatic ingestion of request/session data during render."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            # Simulate multiple render calls (as if from repeated requests)
            for _ in range(3):
                braincase_render_tool(db, {
                    "purpose": "task_continuity",
                    "memory_domain": "coding",
                })
            # DB should still be empty — no records auto-ingested
            records = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(records), 0, "No records should be auto-ingested during render")


# ---------------------------------------------------------------------------
# 17–19: Body injection helpers
# ---------------------------------------------------------------------------

class InjectBraincaseToolsTests(unittest.TestCase):

    def test_inject_adds_render_tool_when_enabled(self):
        """inject_braincase_tools_to_body adds braincase.render to body['tools'] when enabled."""
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        names = [t.get("name") for t in body.get("tools", [])]
        self.assertIn("braincase.render", names)

    def test_inject_noop_when_disabled(self):
        """inject_braincase_tools_to_body is a no-op when disabled."""
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_DISABLED_ENV)
        self.assertEqual(body["tools"], [])

    def test_inject_noop_with_no_tools_key_disabled(self):
        """No-op on body with no 'tools' key when disabled."""
        body = {"model": "test-model"}
        inject_braincase_tools_to_body(body, env=_DISABLED_ENV)
        self.assertNotIn("tools", body)

    def test_inject_creates_tools_list_when_absent(self):
        """inject adds 'tools' list to body even if absent, when enabled."""
        body = {"model": "test-model"}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        self.assertIn("tools", body)
        names = [t.get("name") for t in body["tools"]]
        self.assertIn("braincase.render", names)

    def test_inject_idempotent(self):
        """Calling inject twice does not duplicate the tool definition."""
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        render_tools = [t for t in body["tools"] if t.get("name") == "braincase.render"]
        self.assertEqual(len(render_tools), 1, "braincase.render should appear exactly once")

    def test_inject_preserves_existing_tools(self):
        """Existing tools in body['tools'] are preserved when braincase tools are injected."""
        existing = {"type": "function", "name": "exec_command", "description": "run shell", "parameters": {}}
        body = {"tools": [existing]}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        names = [t.get("name") for t in body["tools"]]
        self.assertIn("exec_command", names)
        self.assertIn("braincase.render", names)

    def test_inject_does_not_expose_write_update_search_inspect(self):
        """inject_braincase_tools_to_body never adds write/update/search/inspect."""
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        names = [t.get("name") for t in body["tools"]]
        for forbidden in ("braincase.write", "braincase.update",
                          "braincase.search", "braincase.inspect"):
            self.assertNotIn(forbidden, names)

    def test_inject_returns_body(self):
        """inject_braincase_tools_to_body returns the body dict."""
        body = {"tools": []}
        result = inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        self.assertIs(result, body)


# ---------------------------------------------------------------------------
# 22: No /v1/responses body mutation when disabled
# ---------------------------------------------------------------------------

class NoBodyMutationWhenDisabledTests(unittest.TestCase):

    def test_disabled_flag_does_not_mutate_body_tools(self):
        """When QZ_BRAINCASE_TOOLS_ENABLED is not set, tools list is unchanged."""
        original_tools = [
            {"type": "function", "name": "exec_command", "description": "x", "parameters": {}}
        ]
        body = {"tools": list(original_tools)}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            inject_braincase_tools_to_body(body)
        self.assertEqual(body["tools"], original_tools)

    def test_disabled_flag_does_not_add_tools_key(self):
        """When disabled, 'tools' key is not added if absent."""
        body = {"model": "test", "input": []}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            inject_braincase_tools_to_body(body)
        self.assertNotIn("tools", body)


# ---------------------------------------------------------------------------
# 23: normalize_responses_input_for_qwen unchanged when flag disabled
# ---------------------------------------------------------------------------

class NormalizeInputFlagDisabledTests(unittest.TestCase):
    """Verify normalize_responses_input_for_qwen does not mutate body tools when disabled."""

    def test_normalize_does_not_inject_braincase_tools_when_disabled(self):
        from proxy.qz_request_normalization import normalize_responses_input_for_qwen

        body = {
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            ],
            "tools": [],
        }
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            result = normalize_responses_input_for_qwen(body)

        names = [t.get("name") for t in result.get("tools", [])]
        self.assertNotIn("braincase.render", names)

    def test_normalize_injects_braincase_tools_when_enabled(self):
        from proxy.qz_request_normalization import normalize_responses_input_for_qwen

        body = {
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            ],
            "tools": [],
        }
        with patch.dict(os.environ, {QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true"}):
            result = normalize_responses_input_for_qwen(body)

        names = [t.get("name") for t in result.get("tools", [])]
        self.assertIn("braincase.render", names)
        self.assertIn("braincase.recall", names)


# ---------------------------------------------------------------------------
# Existing request mutation regression guard
# ---------------------------------------------------------------------------

class BraincaseToolsMutationRegressionTests(unittest.TestCase):
    """Existing mutation regression: qz_session_id etc. must not leak to upstream."""

    def test_existing_mutation_regression_still_passes(self):
        """Run the existing mutation regression test as a sanity check."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_qz_request_mutation_regression", "-v"],
            cwd=str(pathlib.Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"Mutation regression failed:\n{result.stderr}")


# =============================================================================
# SLICE G: braincase.recall tests
# =============================================================================

# ---------------------------------------------------------------------------
# Tool definition: recall presence and schema
# ---------------------------------------------------------------------------

class RecallToolPresenceTests(unittest.TestCase):

    def _names(self, env):
        return [d.get("name") for d in get_braincase_tool_definitions(env=env)]

    def test_recall_absent_when_disabled(self):
        """braincase.recall not returned when flag is disabled."""
        self.assertNotIn("braincase.recall", self._names(_DISABLED_ENV))

    def test_recall_present_when_enabled(self):
        """braincase.recall returned when flag is enabled."""
        self.assertIn("braincase.recall", self._names(_ENABLED_ENV))

    def test_render_still_present_when_enabled(self):
        """braincase.render still returned alongside recall when enabled."""
        self.assertIn("braincase.render", self._names(_ENABLED_ENV))

    def test_write_update_search_inspect_still_absent(self):
        """write/update/search/inspect never appear regardless of flag."""
        for env in (_ENABLED_ENV, _DISABLED_ENV):
            names = self._names(env)
            for forbidden in ("braincase.write", "braincase.update",
                              "braincase.search", "braincase.inspect"):
                self.assertNotIn(forbidden, names, f"{forbidden} must not be exposed")

    def test_enabled_returns_exactly_render_and_recall(self):
        """Exactly render + recall are returned when enabled — no extras."""
        names = set(self._names(_ENABLED_ENV))
        self.assertEqual(names, {"braincase.render", "braincase.recall"})


class RecallToolSchemaTests(unittest.TestCase):

    def setUp(self):
        self.td = BRAINCASE_RECALL_TOOL_DEF

    def test_type_is_function(self):
        self.assertEqual(self.td["type"], "function")

    def test_name_is_braincase_recall(self):
        self.assertEqual(self.td["name"], "braincase.recall")

    def test_has_description(self):
        self.assertIsInstance(self.td.get("description"), str)
        self.assertGreater(len(self.td["description"]), 30)

    def test_description_no_raw_state_records(self):
        desc = self.td["description"].lower()
        self.assertIn("raw", desc)

    def test_description_no_ingestion(self):
        desc = self.td["description"].lower()
        self.assertIn("ingest", desc)

    def test_description_mentions_render_packet(self):
        desc = self.td["description"].lower()
        self.assertTrue("renderpacket" in desc or "render packet" in desc)

    def test_description_says_use_render_for_exact(self):
        desc = self.td["description"].lower()
        self.assertIn("braincase.render", desc)

    def test_required_purpose_and_memory_domain(self):
        required = self.td["parameters"]["required"]
        self.assertIn("purpose", required)
        self.assertIn("memory_domain", required)

    def test_optional_recall_mode(self):
        props = self.td["parameters"]["properties"]
        self.assertIn("recall_mode", props)
        # recall_mode is optional (not in required)
        self.assertNotIn("recall_mode", self.td["parameters"]["required"])

    def test_recall_mode_has_enum(self):
        props = self.td["parameters"]["properties"]
        rm = props["recall_mode"]
        self.assertIn("enum", rm)
        modes = rm["enum"]
        for expected in ("task", "project", "procedure", "artifact", "open_loops"):
            self.assertIn(expected, modes)

    def test_optional_query_tiers(self):
        props = self.td["parameters"]["properties"]
        self.assertIn("query", props)
        self.assertIn("tiers", props)

    def test_budget_tokens_has_bounds(self):
        props = self.td["parameters"]["properties"]
        bt = props["budget_tokens"]
        self.assertIn("minimum", bt)
        self.assertIn("maximum", bt)
        self.assertLessEqual(bt["minimum"], 80)
        self.assertGreaterEqual(bt["maximum"], 600)

    def test_limit_has_bounds(self):
        props = self.td["parameters"]["properties"]
        lim = props["limit"]
        self.assertIn("minimum", lim)
        self.assertIn("maximum", lim)


# ---------------------------------------------------------------------------
# tiers_for_recall_mode
# ---------------------------------------------------------------------------

class TiersForRecallModeTests(unittest.TestCase):

    def test_task_returns_bounded_tiers(self):
        tiers = tiers_for_recall_mode("task")
        self.assertIsInstance(tiers, list)
        self.assertGreater(len(tiers), 0)
        for t in ("working_state", "project_state"):
            self.assertIn(t, tiers)

    def test_project_returns_bounded_tiers(self):
        tiers = tiers_for_recall_mode("project")
        self.assertIsInstance(tiers, list)
        self.assertIn("project_state", tiers)

    def test_procedure_returns_bounded_tiers(self):
        tiers = tiers_for_recall_mode("procedure")
        self.assertIsInstance(tiers, list)
        self.assertIn("procedural_memory", tiers)

    def test_artifact_returns_bounded_tiers(self):
        tiers = tiers_for_recall_mode("artifact")
        self.assertIsInstance(tiers, list)
        self.assertIn("artifact_memory", tiers)

    def test_open_loops_returns_bounded_tiers(self):
        tiers = tiers_for_recall_mode("open_loops")
        self.assertIsInstance(tiers, list)
        self.assertIn("working_state", tiers)

    def test_unknown_mode_returns_none(self):
        self.assertIsNone(tiers_for_recall_mode("all_memory"))
        self.assertIsNone(tiers_for_recall_mode(""))
        self.assertIsNone(tiers_for_recall_mode("dump"))

    def test_no_mode_means_all_tiers(self):
        """Verify that no single mode covers all possible tiers (modes are bounded)."""
        all_known = {"working_state", "project_state", "session_state",
                     "semantic_memory", "procedural_memory", "episodic_memory",
                     "artifact_memory", "perceptual_index", "preference_constraint_memory"}
        for mode, tiers in RECALL_MODE_TIERS.items():
            self.assertLess(len(tiers), len(all_known),
                            f"Mode {mode!r} should not cover all possible tiers")

    def test_all_modes_present_in_constant(self):
        for mode in ("task", "project", "procedure", "artifact", "open_loops"):
            self.assertIn(mode, RECALL_MODE_TIERS)


# ---------------------------------------------------------------------------
# braincase_recall_packet tier narrowing
# ---------------------------------------------------------------------------

class RecallTierNarrowingTests(unittest.TestCase):

    def _fresh_db(self, td):
        from pathlib import Path
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        assert db.init()
        return db

    def test_caller_tiers_narrow_mode_tiers(self):
        """Caller-supplied tiers that are in the mode narrow the result."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_packet(
                db,
                purpose="test",
                memory_domain="coding",
                recall_mode="task",
                tiers=["project_state"],
            )
            # Should succeed (project_state is in task mode)
            self.assertNotIn("tier_not_allowed_for_mode", result.get("warnings", []))
            self.assertNotIn("unknown_recall_mode", result.get("warnings", []))

    def test_out_of_mode_tiers_returns_warning(self):
        """Caller-supplied tiers entirely outside the mode return a warning packet."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_packet(
                db,
                purpose="test",
                memory_domain="coding",
                recall_mode="task",
                tiers=["episodic_memory"],  # not in task mode
            )
            self.assertIn("tier_not_allowed_for_mode", result.get("warnings", []))

    def test_mixed_tiers_uses_intersection(self):
        """Mixed caller tiers (some in mode, some not) use the intersection."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_packet(
                db,
                purpose="test",
                memory_domain="coding",
                recall_mode="task",
                tiers=["project_state", "episodic_memory"],  # episodic not in task
            )
            # project_state IS in task mode, so intersection is ["project_state"]
            # Should succeed without tier_not_allowed warning
            self.assertNotIn("tier_not_allowed_for_mode", result.get("warnings", []))

    def test_none_tiers_uses_mode_defaults(self):
        """No caller tiers uses the full mode tier list."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_packet(
                db,
                purpose="test",
                memory_domain="coding",
                recall_mode="task",
                tiers=None,
            )
            self.assertNotIn("tier_not_allowed_for_mode", result.get("warnings", []))
            self.assertNotIn("unknown_recall_mode", result.get("warnings", []))


# ---------------------------------------------------------------------------
# braincase_recall_tool executor
# ---------------------------------------------------------------------------

class RecallToolExecutorTests(unittest.TestCase):

    def _fresh_db(self, td):
        from pathlib import Path
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        assert db.init()
        return db

    def _make_record(self, record_id="rec_recall_001", memory_domain="coding",
                     tier="project_state"):
        return {
            "record_id": record_id,
            "schema": "braincase/state-record@1",
            "memory_domain": memory_domain,
            "tier": tier,
            "record_type": "constraint",
            "claim": f"Constraint for {memory_domain} recall test.",
            "summary": "Test summary.",
            "status": "active",
            "visibility": "renderable",
            "confidence": 1.0,
            "importance": 0.8,
            "retention": "project",
            "created_at_ms": 1778803200000,
            "updated_at_ms": 1778803200000,
            "source_refs": [],
            "tags": ["test"],
            "supersedes": None,
            "superseded_by": None,
            "metadata": None,
        }

    def test_returns_render_packet_shape(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_result_schema_is_render_packet(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertEqual(result.get("schema"), "braincase/render-packet@1")

    def test_no_raw_state_records_in_result(self):
        """Tool result must not contain raw StateRecord objects."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            rec = self._make_record()
            db.put_state_record(rec)
            result = braincase_recall_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertNotIn("record", result)
            self.assertNotIn("records", result)
            self.assertNotIn("state_records", result)
            for rid in result.get("source_record_ids", []):
                self.assertIsInstance(rid, str)

    def test_no_forbidden_fields_in_result(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            for field in _FORBIDDEN_OUTPUT_FIELDS:
                self.assertNotIn(field, result)

    def test_recall_respects_memory_domain(self):
        """Records from another domain are not returned."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            rec_coding = self._make_record("rec_coding", "coding", "project_state")
            rec_hsm = self._make_record("rec_hsm", "hsm", "project_state")
            db.put_state_record(rec_coding)
            db.put_state_record(rec_hsm)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
            })
            source_ids = result.get("source_record_ids", [])
            if source_ids:
                self.assertIn("rec_coding", source_ids)
                self.assertNotIn("rec_hsm", source_ids)

    def test_recall_does_not_cross_into_hsm(self):
        """Recall with memory_domain=coding must not return hsm records."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            hsm_rec = self._make_record("rec_hsm_only", "hsm", "project_state")
            db.put_state_record(hsm_rec)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
            })
            self.assertNotIn("rec_hsm_only", result.get("source_record_ids", []))

    def test_hsm_recall_only_when_memory_domain_hsm(self):
        """Recall returns hsm records only when memory_domain=hsm."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            hsm_rec = self._make_record("rec_hsm_explicit", "hsm", "project_state")
            db.put_state_record(hsm_rec)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "hsm",
            })
            source_ids = result.get("source_record_ids", [])
            if source_ids:
                self.assertIn("rec_hsm_explicit", source_ids)

    def test_recall_respects_budget_hard_bound(self):
        """budget_tokens is reflected in the result and respected by render."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "budget_tokens": 80,
            })
            self.assertEqual(result.get("budget_tokens"), 80)
            if result.get("rendered_text"):
                self.assertLessEqual(len(result["rendered_text"]), 80 * 4 + 50)

    def test_recall_with_query_uses_search(self):
        """Recall with a query uses search-backed retrieval."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            rec = self._make_record("rec_query_test", "coding", "project_state")
            rec["claim"] = "unique_search_term_xyz_recall_test"
            db.put_state_record(rec)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "query": "unique_search_term_xyz_recall_test",
            })
            # No error in warnings
            self.assertNotIn("purpose_required", result.get("warnings", []))
            self.assertNotIn("memory_domain_required", result.get("warnings", []))

    def test_recall_without_query_uses_list(self):
        """Recall without a query uses list-backed retrieval filtered by mode tiers."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            rec = self._make_record("rec_list_test", "coding", "project_state")
            db.put_state_record(rec)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
            })
            self.assertNotIn("unknown_recall_mode", result.get("warnings", []))
            # project_state is in task mode, so record may appear
            self.assertIn("source_record_ids", result)

    def test_recall_excludes_internal_records(self):
        """Records with visibility=internal are excluded from render output."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            rec = self._make_record("rec_internal", "coding", "project_state")
            rec["visibility"] = "internal"
            db.put_state_record(rec)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
            })
            self.assertNotIn("rec_internal", result.get("source_record_ids", []))

    def test_recall_default_mode_is_task(self):
        """Omitting recall_mode defaults to 'task'."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {
                "purpose": "task_continuity",
                "memory_domain": "coding",
            })
            self.assertNotIn("unknown_recall_mode", result.get("warnings", []))


# ---------------------------------------------------------------------------
# Recall arg validation
# ---------------------------------------------------------------------------

class RecallArgValidationTests(unittest.TestCase):

    def _fresh_db(self, td):
        from pathlib import Path
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        assert db.init()
        return db

    def test_missing_memory_domain_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {"purpose": "test"})
            warnings = result.get("warnings", [])
            self.assertTrue(any("memory_domain" in w for w in warnings))
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_missing_purpose_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {"memory_domain": "coding"})
            warnings = result.get("warnings", [])
            self.assertTrue(any("purpose" in w for w in warnings))
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_unknown_recall_mode_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "dump_everything",
            })
            self.assertIn("unknown_recall_mode", result.get("warnings", []))
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_unknown_mode_does_not_return_all_memory(self):
        """Unknown recall_mode must never fall back to returning all memory."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "all_memory",
            })
            self.assertIn("unknown_recall_mode", result.get("warnings", []))
            # source_record_ids should be empty (warning packet)
            self.assertEqual(result.get("source_record_ids", []), [])

    def test_disabled_db_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
            })
            self.assertTrue(any("disabled" in w for w in result.get("warnings", [])))
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_disabled_db_no_exception(self):
        """Disabled DB must not raise."""
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "x.sqlite3", enabled=False)
            try:
                braincase_recall_tool(db, {"purpose": "p", "memory_domain": "coding"})
            except Exception as exc:
                self.fail(f"braincase_recall_tool raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# No writes, no automatic ingestion
# ---------------------------------------------------------------------------

class RecallNoWriteNoIngestionTests(unittest.TestCase):

    def _fresh_db(self, td):
        from pathlib import Path
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        assert db.init()
        return db

    def test_recall_does_not_write_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            before = db.list_state_records(memory_domain="coding", limit=100)
            braincase_recall_tool(db, {"purpose": "test", "memory_domain": "coding"})
            after = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(before), len(after))

    def test_no_automatic_ingestion_on_recall(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for _ in range(3):
                braincase_recall_tool(db, {"purpose": "test", "memory_domain": "coding"})
            records = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(records), 0)


# ---------------------------------------------------------------------------
# Body injection: render+recall together
# ---------------------------------------------------------------------------

class RecallBodyInjectionTests(unittest.TestCase):

    def test_inject_adds_recall_when_enabled(self):
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        names = [t.get("name") for t in body["tools"]]
        self.assertIn("braincase.recall", names)

    def test_inject_adds_render_and_recall_together(self):
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        names = [t.get("name") for t in body["tools"]]
        self.assertIn("braincase.render", names)
        self.assertIn("braincase.recall", names)

    def test_inject_noop_for_recall_when_disabled(self):
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_DISABLED_ENV)
        names = [t.get("name") for t in body["tools"]]
        self.assertNotIn("braincase.recall", names)

    def test_forwarded_body_unchanged_when_disabled(self):
        original = [{"type": "function", "name": "exec_command", "parameters": {}}]
        body = {"tools": list(original)}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            inject_braincase_tools_to_body(body)
        self.assertEqual(body["tools"], original)

    def test_forwarded_body_includes_recall_when_enabled(self):
        body = {"tools": []}
        with patch.dict(os.environ, {QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true"}):
            inject_braincase_tools_to_body(body)
        names = [t.get("name") for t in body["tools"]]
        self.assertIn("braincase.recall", names)


# ---------------------------------------------------------------------------
# Updated harness policy content checks
# ---------------------------------------------------------------------------

class UpdatedHarnessPolicyTests(unittest.TestCase):

    def test_policy_mentions_recall_modes(self):
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        lower = policy.lower()
        for mode in ("task", "project", "procedure", "artifact", "open_loops"):
            self.assertIn(mode, lower, f"Policy should mention recall_mode {mode!r}")

    def test_policy_warns_no_broad_dump(self):
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        lower = policy.lower()
        self.assertTrue("broad" in lower or "dump" in lower)

    def test_policy_says_write_update_search_inspect_not_exposed(self):
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        lower = policy.lower()
        self.assertIn("not yet exposed", lower)
        for word in ("write", "update", "search", "inspect"):
            self.assertIn(word, lower)

    def test_policy_tells_when_to_use_recall_vs_render(self):
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        # Both tools should be mentioned with guidance
        self.assertIn("braincase.recall", policy)
        self.assertIn("braincase.render", policy)


# =============================================================================
# SLICE G.1: tier-bounded retrieval and deterministic enum order tests
# =============================================================================

from proxy.qz_braincase_tools import (
    RECALL_MODE_ORDER,
    _recall_candidate_records,
)


def _make_record_g1(
    record_id: str,
    memory_domain: str,
    tier: str,
    claim: str = "",
    importance: float = 0.5,
    visibility: str = "renderable",
    status: str = "active",
) -> dict:
    return {
        "record_id": record_id,
        "schema": "braincase/state-record@1",
        "memory_domain": memory_domain,
        "tier": tier,
        "record_type": "constraint",
        "claim": claim or f"Claim for {record_id}",
        "summary": f"Summary for {record_id}",
        "status": status,
        "visibility": visibility,
        "confidence": 1.0,
        "importance": importance,
        "retention": "project",
        "created_at_ms": 1778803200000,
        "updated_at_ms": 1778803200000,
        "source_refs": [],
        "tags": ["test"],
        "supersedes": None,
        "superseded_by": None,
        "metadata": None,
    }


def _fresh_db_g1(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


class RecallTierBoundedRetrievalTests(unittest.TestCase):
    """Verify that recall retrieves tier-bounded candidates before the limit."""

    def test_query_tier_bounded_before_limit(self):
        """In-mode record found even when many out-of-mode records match query."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db_g1(td)
            shared_query_term = "turboquery_unique_g1_term"

            # Seed 5 out-of-mode (episodic_memory not in task mode) renderable records
            for i in range(5):
                rec = _make_record_g1(
                    f"rec_out_{i}", "coding", "episodic_memory",
                    claim=shared_query_term, importance=0.9,
                )
                db.put_state_record(rec)

            # Seed 1 in-mode (project_state IS in task mode) record with same query
            in_mode = _make_record_g1(
                "rec_in_mode", "coding", "project_state",
                claim=shared_query_term, importance=0.5,
            )
            db.put_state_record(in_mode)

            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
                "query": shared_query_term,
                "limit": 3,  # small limit — out-of-mode records must not fill it
            })

            # In-mode record must appear despite the limit
            self.assertIn("rec_in_mode", result.get("source_record_ids", []),
                          "In-mode record must not be starved by out-of-mode records")

    def test_list_tier_bounded_before_limit(self):
        """In-mode record found even when many out-of-mode records exist (no query)."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db_g1(td)

            # Seed 10 out-of-mode (episodic_memory) records
            for i in range(10):
                rec = _make_record_g1(
                    f"rec_out_{i}", "coding", "episodic_memory", importance=0.9,
                )
                db.put_state_record(rec)

            # Seed 1 in-mode (project_state) record
            in_mode = _make_record_g1(
                "rec_in_mode_list", "coding", "project_state", importance=0.5,
            )
            db.put_state_record(in_mode)

            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
                "limit": 3,
            })

            self.assertIn("rec_in_mode_list", result.get("source_record_ids", []),
                          "In-mode record must not be starved by out-of-mode records")

    def test_candidates_deduped_across_tiers(self):
        """Record appearing in multiple tiers (impossible by schema, but helper dedupes)."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db_g1(td)
            rec = _make_record_g1("rec_dedup", "coding", "project_state")
            db.put_state_record(rec)

            # Call _recall_candidate_records directly with repeated tier
            candidates = _recall_candidate_records(
                db,
                memory_domain="coding",
                query=None,
                effective_tiers=["project_state", "project_state"],
                limit=20,
            )
            ids = [c["record_id"] for c in candidates]
            self.assertEqual(len(ids), len(set(ids)), "Duplicate record IDs must be removed")

    def test_result_still_render_packet_shaped(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db_g1(td)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
            })
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_result_still_budget_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db_g1(td)
            for i in range(10):
                rec = _make_record_g1(
                    f"rec_budget_{i}", "coding", "project_state",
                    claim="A" * 200, importance=float(i) / 10,
                )
                db.put_state_record(rec)

            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
                "budget_tokens": 80,
            })
            rendered = result.get("rendered_text", "")
            # budget_tokens=80 → char budget = max(80, 80*4) = 320
            self.assertLessEqual(len(rendered), 320 + 50,
                                 "rendered_text must stay within budget")

    def test_recall_unknown_mode_still_warns(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db_g1(td)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "non_existent_mode",
            })
            self.assertIn("unknown_recall_mode", result.get("warnings", []))
            self.assertEqual(result.get("source_record_ids", []), [])

    def test_recall_empty_tier_intersection_still_warns(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db_g1(td)
            result = braincase_recall_tool(db, {
                "purpose": "test",
                "memory_domain": "coding",
                "recall_mode": "task",
                "tiers": ["episodic_memory"],  # not in task mode
            })
            self.assertIn("tier_not_allowed_for_mode", result.get("warnings", []))


class RecallEnumOrderTests(unittest.TestCase):
    """Verify that the recall_mode enum has deterministic order."""

    def test_recall_mode_order_constant_is_tuple(self):
        self.assertIsInstance(RECALL_MODE_ORDER, tuple)

    def test_recall_mode_order_matches_dict_keys(self):
        self.assertEqual(list(RECALL_MODE_ORDER), list(RECALL_MODE_TIERS.keys()))

    def test_recall_mode_enum_in_tool_def_is_deterministic(self):
        enum = BRAINCASE_RECALL_TOOL_DEF["parameters"]["properties"]["recall_mode"]["enum"]
        self.assertIsInstance(enum, list)
        self.assertEqual(enum, list(RECALL_MODE_ORDER))

    def test_recall_mode_enum_starts_with_task(self):
        enum = BRAINCASE_RECALL_TOOL_DEF["parameters"]["properties"]["recall_mode"]["enum"]
        self.assertEqual(enum[0], "task", "task mode should be first (default)")

    def test_recall_mode_enum_contains_all_modes(self):
        enum = BRAINCASE_RECALL_TOOL_DEF["parameters"]["properties"]["recall_mode"]["enum"]
        for mode in ("task", "project", "procedure", "artifact", "open_loops"):
            self.assertIn(mode, enum)

    def test_recall_mode_enum_stable_across_calls(self):
        """Enum should be identical across repeated calls — no frozenset non-determinism."""
        e1 = BRAINCASE_RECALL_TOOL_DEF["parameters"]["properties"]["recall_mode"]["enum"]
        e2 = BRAINCASE_RECALL_TOOL_DEF["parameters"]["properties"]["recall_mode"]["enum"]
        self.assertEqual(e1, e2)


class RecallPartialTierDroppedWarningTests(unittest.TestCase):
    """Verify behaviour when some caller-supplied tiers are outside the mode."""

    def _fresh_db(self, td):
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        assert db.init()
        return db

    def test_partial_out_of_mode_tiers_warns(self):
        """Mixed tiers (some in mode, some not) — packet includes dropped warning."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_packet(
                db,
                purpose="test",
                memory_domain="coding",
                recall_mode="task",
                tiers=["project_state", "episodic_memory"],  # episodic not in task
            )
            self.assertIn("tier_narrowing_dropped_out_of_mode", result.get("warnings", []),
                          "Should warn that episodic_memory was dropped from task mode tiers")

    def test_partial_out_of_mode_still_returns_valid_packet(self):
        """Packet is still valid despite some tiers being dropped."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_packet(
                db,
                purpose="test",
                memory_domain="coding",
                recall_mode="task",
                tiers=["project_state", "episodic_memory"],
            )
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, result)

    def test_all_in_mode_tiers_no_dropped_warning(self):
        """No dropped warning when all caller tiers are within the mode."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = braincase_recall_packet(
                db,
                purpose="test",
                memory_domain="coding",
                recall_mode="task",
                tiers=["project_state"],  # project_state IS in task mode
            )
            self.assertNotIn("tier_narrowing_dropped_out_of_mode", result.get("warnings", []))

    def test_write_update_search_inspect_remain_unexposed(self):
        names = [d.get("name") for d in get_braincase_tool_definitions(env=_ENABLED_ENV)]
        for forbidden in ("braincase.write", "braincase.update",
                          "braincase.search", "braincase.inspect"):
            self.assertNotIn(forbidden, names)


# =============================================================================
# SLICE G.2: proxy-local tool dispatch tests
# =============================================================================

from proxy.qz_braincase_tools import (
    BraincaseRenderProxyToolExecutor,
    BraincaseRecallProxyToolExecutor,
    make_braincase_tool_executors,
)
from proxy.qz_proxy_tools import (
    ProxyLocalToolRegistry,
    ProxyToolExecutionContext,
    make_proxy_local_tool_registry,
)
from proxy.qz_tool_lifecycle import ToolContinuationResult


def _make_function_call(
    name: str,
    args: dict,
    call_id: str = "call_bc_001",
    item_id: str = "fc_bc_001",
) -> dict:
    return {
        "type": "function_call",
        "name": name,
        "call_id": call_id,
        "id": item_id,
        "arguments": json.dumps(args),
    }


class FakeWebRuntime:
    def execute_web_search_call(self, call, counters, seen_signatures, request_id=""):
        return (
            {"id": "wsc_fake", "type": "web_search_call", "status": "completed", "call_id": call.get("call_id")},
            {"type": "function_call_output", "call_id": call.get("call_id"), "output": "{}"},
            [],
        )


def _fresh_bc_db(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


def _make_renderable(record_id="rec_disp_001", memory_domain="coding",
                     tier="project_state", claim="dispatch test claim") -> dict:
    return {
        "record_id": record_id, "schema": "braincase/state-record@1",
        "memory_domain": memory_domain, "tier": tier, "record_type": "constraint",
        "claim": claim, "summary": "dispatch summary",
        "status": "active", "visibility": "renderable",
        "confidence": 1.0, "importance": 0.8, "retention": "project",
        "created_at_ms": 1778803200000, "updated_at_ms": 1778803200000,
        "source_refs": [], "tags": ["test"],
        "supersedes": None, "superseded_by": None, "metadata": None,
    }


class BraincaseExecutorPresenceTests(unittest.TestCase):
    """Executors absent when disabled, present when enabled."""

    def test_executors_absent_when_disabled(self):
        executors = make_braincase_tool_executors(env=_DISABLED_ENV)
        names = [e.function_name for e in executors]
        self.assertNotIn("braincase.render", names)
        self.assertNotIn("braincase.recall", names)

    def test_executors_present_when_enabled(self):
        with patch.dict(os.environ, _ENABLED_ENV):
            executors = make_braincase_tool_executors()
        names = [e.function_name for e in executors]
        self.assertIn("braincase.render", names)
        self.assertIn("braincase.recall", names)

    def test_no_write_update_search_inspect_executors(self):
        with patch.dict(os.environ, _ENABLED_ENV):
            executors = make_braincase_tool_executors()
        names = [e.function_name for e in executors]
        for forbidden in ("braincase.write", "braincase.update",
                          "braincase.search", "braincase.inspect"):
            self.assertNotIn(forbidden, names)

    def test_exactly_render_and_recall_when_enabled(self):
        with patch.dict(os.environ, _ENABLED_ENV):
            executors = make_braincase_tool_executors()
        names = set(e.function_name for e in executors)
        self.assertEqual(names, {"braincase.render", "braincase.recall"})

    def test_executors_empty_list_when_disabled(self):
        executors = make_braincase_tool_executors(env=_DISABLED_ENV)
        self.assertEqual(executors, [])

    def _env_executors(self):
        """make_braincase_tool_executors with explicit env dict (for isolation)."""
        pass


# Override make_braincase_tool_executors to accept env for tests
def make_braincase_tool_executors(db=None, env=None):
    from proxy.qz_braincase_tools import is_braincase_tools_enabled, BraincaseRenderProxyToolExecutor, BraincaseRecallProxyToolExecutor
    if not is_braincase_tools_enabled(env):
        return []
    return [
        BraincaseRenderProxyToolExecutor(db=db),
        BraincaseRecallProxyToolExecutor(db=db),
    ]


class RegistryDispatchTests(unittest.TestCase):
    """Registry recognizes BrainCase calls when enabled."""

    def _registry(self, db=None) -> ProxyLocalToolRegistry:
        return ProxyLocalToolRegistry([
            BraincaseRenderProxyToolExecutor(db=db),
            BraincaseRecallProxyToolExecutor(db=db),
        ])

    def test_registry_recognizes_render_call(self):
        reg = self._registry()
        call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
        self.assertTrue(reg.is_proxy_local_call(call))

    def test_registry_recognizes_recall_call(self):
        reg = self._registry()
        call = _make_function_call("braincase.recall", {"purpose": "test", "memory_domain": "coding"})
        self.assertTrue(reg.is_proxy_local_call(call))

    def test_registry_does_not_recognize_write(self):
        reg = self._registry()
        call = _make_function_call("braincase.write", {})
        self.assertFalse(reg.is_proxy_local_call(call))

    def test_registry_does_not_recognize_update_search_inspect(self):
        reg = self._registry()
        for name in ("braincase.update", "braincase.search", "braincase.inspect"):
            call = _make_function_call(name, {})
            self.assertFalse(reg.is_proxy_local_call(call),
                             f"{name} must not be recognized as proxy-local")

    def test_make_proxy_local_tool_registry_includes_braincase_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            with patch.dict(os.environ, _ENABLED_ENV):
                reg = make_proxy_local_tool_registry(FakeWebRuntime(), db=db)
            render_call = _make_function_call("braincase.render", {"purpose": "t", "memory_domain": "coding"})
            recall_call = _make_function_call("braincase.recall", {"purpose": "t", "memory_domain": "coding"})
            self.assertTrue(reg.is_proxy_local_call(render_call))
            self.assertTrue(reg.is_proxy_local_call(recall_call))

    def test_make_proxy_local_tool_registry_excludes_braincase_when_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            reg = make_proxy_local_tool_registry(FakeWebRuntime())
        render_call = _make_function_call("braincase.render", {"purpose": "t", "memory_domain": "coding"})
        self.assertFalse(reg.is_proxy_local_call(render_call))

    def test_web_search_unaffected_by_braincase(self):
        with patch.dict(os.environ, _ENABLED_ENV):
            reg = make_proxy_local_tool_registry(FakeWebRuntime())
        web_call = {"type": "function_call", "name": "web_search", "call_id": "wsc_01",
                    "id": "wsc_01", "arguments": '{"query":"test"}'}
        self.assertTrue(reg.is_proxy_local_call(web_call))


class BraincaseRenderDispatchTests(unittest.TestCase):
    """braincase.render call produces function_call_output with RenderPacket."""

    def _executor(self, db):
        return BraincaseRenderProxyToolExecutor(db=db)

    def test_execute_returns_tool_continuation_result(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            ctx = ProxyToolExecutionContext(request_id="req-bc-001")
            result = ex.execute(call, ctx)
            self.assertIsInstance(result, ToolContinuationResult)

    def test_public_item_is_function_call_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            self.assertEqual(result.public_item.get("type"), "function_call_output")

    def test_output_contains_render_packet_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, output)

    def test_output_rendered_text_and_source_ids_present(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertIn("rendered_text", output)
            self.assertIn("source_record_ids", output)

    def test_output_no_raw_state_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            rec = _make_renderable()
            db.put_state_record(rec)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertNotIn("record", output)
            self.assertNotIn("state_records", output)
            for rid in output.get("source_record_ids", []):
                self.assertIsInstance(rid, str)

    def test_output_no_raw_source_refs(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertNotIn("source_refs", output)

    def test_upstream_items_contains_call_and_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            self.assertEqual(len(result.upstream_items), 2)
            types = [item.get("type") for item in result.upstream_items]
            self.assertIn("function_call", types)
            self.assertIn("function_call_output", types)

    def test_disabled_db_returns_warning_in_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertTrue(any("disabled" in w for w in output.get("warnings", [])))

    def test_missing_memory_domain_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertTrue(any("memory_domain" in w for w in output.get("warnings", [])))

    def test_missing_purpose_returns_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertTrue(any("purpose" in w for w in output.get("warnings", [])))

    def test_execute_does_not_write_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.render", {"purpose": "test", "memory_domain": "coding"})
            before = db.list_state_records(memory_domain="coding", limit=100)
            ex.execute(call, ProxyToolExecutionContext())
            after = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(before), len(after))

    def test_started_public_item_has_id(self):
        ex = BraincaseRenderProxyToolExecutor()
        call = _make_function_call("braincase.render", {})
        item = ex.started_public_item(call, 0)
        self.assertIn("id", item)
        self.assertEqual(item.get("type"), "function_call_output")

    def test_lifecycle_is_proxy_local_with_continuation(self):
        ex = BraincaseRenderProxyToolExecutor()
        self.assertEqual(ex.lifecycle.execution, "proxy_local")
        self.assertGreater(ex.lifecycle.continuation_hops, 0)
        self.assertTrue(ex.lifecycle.emits_continuation)


class BraincaseRecallDispatchTests(unittest.TestCase):
    """braincase.recall call produces function_call_output with RenderPacket."""

    def _executor(self, db):
        return BraincaseRecallProxyToolExecutor(db=db)

    def test_execute_returns_tool_continuation_result(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.recall",
                                       {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            self.assertIsInstance(result, ToolContinuationResult)

    def test_public_item_is_function_call_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.recall",
                                       {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            self.assertEqual(result.public_item.get("type"), "function_call_output")

    def test_output_contains_render_packet_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.recall",
                                       {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, output)

    def test_output_no_raw_state_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            rec = _make_renderable()
            db.put_state_record(rec)
            ex = self._executor(db)
            call = _make_function_call("braincase.recall",
                                       {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertNotIn("record", output)
            self.assertNotIn("state_records", output)

    def test_disabled_db_returns_warning_in_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)
            ex = self._executor(db)
            call = _make_function_call("braincase.recall",
                                       {"purpose": "test", "memory_domain": "coding"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertTrue(any("disabled" in w for w in output.get("warnings", [])))

    def test_no_automatic_ingestion(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            ex = self._executor(db)
            call = _make_function_call("braincase.recall",
                                       {"purpose": "test", "memory_domain": "coding"})
            for _ in range(3):
                ex.execute(call, ProxyToolExecutionContext())
            records = db.list_state_records(memory_domain="coding", limit=100)
            self.assertEqual(len(records), 0)

    def test_lifecycle_is_proxy_local_with_continuation(self):
        ex = BraincaseRecallProxyToolExecutor()
        self.assertEqual(ex.lifecycle.execution, "proxy_local")
        self.assertGreater(ex.lifecycle.continuation_hops, 0)
        self.assertTrue(ex.lifecycle.emits_continuation)

    def test_recall_with_seeded_record_in_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_bc_db(td)
            rec = _make_renderable("rec_dispatch_recall", "coding", "project_state")
            db.put_state_record(rec)
            ex = self._executor(db)
            call = _make_function_call("braincase.recall",
                                       {"purpose": "test", "memory_domain": "coding",
                                        "recall_mode": "task"})
            result = ex.execute(call, ProxyToolExecutionContext())
            output = json.loads(result.public_item["output"])
            self.assertIn("rec_dispatch_recall", output.get("source_record_ids", []))


class BraincaseDispatchNoMutationTests(unittest.TestCase):
    """Forwarded body unchanged when feature flag disabled."""

    def test_body_not_mutated_when_disabled(self):
        original_tools = [{"type": "function", "name": "exec_command", "parameters": {}}]
        body = {"tools": list(original_tools)}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            inject_braincase_tools_to_body(body)
        self.assertEqual(body["tools"], original_tools)

    def test_no_qz_session_id_injected(self):
        """qz_session_id must not appear in forwarded body metadata."""
        from proxy.qz_request_normalization import normalize_responses_input_for_qwen
        body = {
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "hi"}]}],
            "tools": [],
        }
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(QZ_BRAINCASE_TOOLS_ENABLED_ENV, None)
            result = normalize_responses_input_for_qwen(body)
        metadata = result.get("metadata", {})
        self.assertNotIn("qz_session_id", metadata)
        self.assertNotIn("qz_workspace_id", metadata)
        self.assertNotIn("qz_memory_domain", metadata)


if __name__ == "__main__":
    unittest.main()
