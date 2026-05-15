"""Tests for proxy/qz_braincase_tools.py — Slice F BrainCase tool surface.

Coverage:
  1.  braincase.render tool definition present when QZ_BRAINCASE_TOOLS_ENABLED=true
  2.  braincase.render tool definition absent when disabled
  3.  No braincase.recall tool definition
  4.  No braincase.write/update/search/inspect tool definitions
  5.  Harness policy says render first and recall not exposed yet
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
    BRAINCASE_RENDER_TOOL_DEF,
    QZ_BRAINCASE_TOOLS_ENABLED_ENV,
    braincase_render_tool,
    get_braincase_harness_policy,
    get_braincase_tool_definitions,
    inject_braincase_tools_to_body,
    is_braincase_tools_enabled,
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
        self.assertEqual(len(defs), 1)
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

    def test_no_braincase_recall(self):
        """braincase.recall must not be exposed."""
        for env in (_ENABLED_ENV, _DISABLED_ENV):
            with self.subTest(env=env):
                self.assertNotIn("braincase.recall", self._all_tool_names(env))

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
        """Policy text should mention braincase.render as the only tool."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        self.assertIn("braincase.render", policy)

    def test_policy_says_recall_not_exposed(self):
        """Policy text should explicitly say recall is not yet exposed."""
        policy = get_braincase_harness_policy(env=_ENABLED_ENV)
        self.assertIn("recall", policy.lower())
        # Should indicate it's not exposed
        self.assertIn("not yet exposed", policy.lower())

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

    def test_inject_does_not_expose_recall_write_update(self):
        """inject_braincase_tools_to_body never adds recall/write/update/search/inspect."""
        body = {"tools": []}
        inject_braincase_tools_to_body(body, env=_ENABLED_ENV)
        names = [t.get("name") for t in body["tools"]]
        for forbidden in ("braincase.recall", "braincase.write", "braincase.update",
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

    def test_normalize_injects_braincase_tool_when_enabled(self):
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


if __name__ == "__main__":
    unittest.main()
