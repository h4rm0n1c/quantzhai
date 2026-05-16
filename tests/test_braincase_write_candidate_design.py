"""Structural design tests for Slice H: braincase.write_candidate.

These tests validate the fixture files and design invariants.
They do NOT test any runtime implementation — the tool is not yet implemented.

Invariants this file protects:
- Valid tool input does NOT contain status or visibility fields.
- Valid tool input contains all required fields.
- Valid result always has status="candidate" and visibility="internal".
- Valid result always has review_required=True.
- Valid result has no raw StateRecord dump.
- Forbidden-active fixture is documented as rejected.
- Forbidden-raw-prompt fixture is documented as rejected.
- Existing render eligibility (status=active, visibility=renderable) excludes
  candidate records, so candidates never leak into braincase.render/recall.
- write/update/search/inspect remain unexposed.
"""
import json
import pathlib
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_FIXTURE_DIR = _REPO / "docs" / "fixtures" / "braincase" / "write-candidate"


def _load(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Fixture parsing
# ---------------------------------------------------------------------------

class FixtureParseTests(unittest.TestCase):

    def test_valid_input_parses(self):
        data = _load("tool-input-valid.json")
        self.assertIsInstance(data, dict)

    def test_valid_result_parses(self):
        data = _load("result-valid.json")
        self.assertIsInstance(data, dict)

    def test_forbidden_active_parses(self):
        data = _load("tool-input-forbidden-active.json")
        self.assertIsInstance(data, dict)

    def test_forbidden_raw_prompt_parses(self):
        data = _load("tool-input-forbidden-raw-prompt.json")
        self.assertIsInstance(data, dict)


# ---------------------------------------------------------------------------
# Valid tool input invariants
# ---------------------------------------------------------------------------

class ValidToolInputTests(unittest.TestCase):

    def setUp(self):
        self.data = _load("tool-input-valid.json")

    def test_required_purpose(self):
        self.assertIn("purpose", self.data)
        self.assertIsInstance(self.data["purpose"], str)
        self.assertTrue(self.data["purpose"].strip())

    def test_required_memory_domain(self):
        self.assertIn("memory_domain", self.data)
        self.assertIsInstance(self.data["memory_domain"], str)
        self.assertTrue(self.data["memory_domain"].strip())

    def test_required_tier(self):
        self.assertIn("tier", self.data)
        self.assertIsInstance(self.data["tier"], str)

    def test_required_record_type(self):
        self.assertIn("record_type", self.data)
        self.assertIsInstance(self.data["record_type"], str)

    def test_required_claim(self):
        self.assertIn("claim", self.data)
        self.assertIsInstance(self.data["claim"], str)
        self.assertGreater(len(self.data["claim"]), 0)

    def test_required_summary(self):
        self.assertIn("summary", self.data)
        self.assertIsInstance(self.data["summary"], str)
        self.assertGreater(len(self.data["summary"]), 0)

    def test_status_absent_from_valid_input(self):
        """status must not appear in valid tool input — forced by proxy."""
        self.assertNotIn("status", self.data,
                         "status must not be a valid tool input field; "
                         "it is forced to 'candidate' by the proxy executor")

    def test_visibility_absent_from_valid_input(self):
        """visibility must not appear in valid tool input — forced by proxy."""
        self.assertNotIn("visibility", self.data,
                         "visibility must not be a valid tool input field; "
                         "it is forced to 'internal' by the proxy executor")

    def test_no_forbidden_raw_fields_in_valid_input(self):
        forbidden = {"raw_prompt", "raw_request_body", "request_body",
                     "full_log", "telemetry_event", "stream_event"}
        present = forbidden & set(self.data.keys())
        self.assertEqual(present, set(),
                         f"Forbidden raw fields must not appear in valid input: {present}")

    def test_confidence_bounded(self):
        if "confidence" in self.data:
            c = self.data["confidence"]
            self.assertGreaterEqual(c, 0.0)
            self.assertLessEqual(c, 1.0)

    def test_importance_bounded(self):
        if "importance" in self.data:
            imp = self.data["importance"]
            self.assertGreaterEqual(imp, 0.0)
            self.assertLessEqual(imp, 1.0)

    def test_claim_not_raw_log(self):
        """claim must not contain raw request body markers."""
        claim = self.data.get("claim", "")
        self.assertNotIn("raw_prompt", claim.lower())
        self.assertNotIn("request_body", claim.lower())


# ---------------------------------------------------------------------------
# Valid WriteCandidateResult invariants
# ---------------------------------------------------------------------------

class ValidResultTests(unittest.TestCase):

    def setUp(self):
        self.data = _load("result-valid.json")

    def test_ok_is_true(self):
        self.assertTrue(self.data["ok"])

    def test_stored_is_true(self):
        self.assertTrue(self.data["stored"])

    def test_status_is_candidate(self):
        """Result must always report status='candidate'."""
        self.assertEqual(self.data["status"], "candidate",
                         "WriteCandidateResult must always have status='candidate'")

    def test_visibility_is_internal(self):
        """Result must always report visibility='internal'."""
        self.assertEqual(self.data["visibility"], "internal",
                         "WriteCandidateResult must always have visibility='internal'")

    def test_review_required_is_true(self):
        """Candidate records always require operator review before promotion."""
        self.assertTrue(self.data["review_required"],
                        "review_required must always be True for candidate writes")

    def test_record_id_present(self):
        self.assertIn("record_id", self.data)
        self.assertIsInstance(self.data["record_id"], str)

    def test_no_raw_state_record_dump(self):
        """Result must not contain a 'record' field (raw StateRecord)."""
        self.assertNotIn("record", self.data,
                         "Result must not dump the raw StateRecord")

    def test_no_source_blob(self):
        """Result must not contain raw source body blobs."""
        self.assertNotIn("source_body", self.data)
        self.assertNotIn("raw_source", self.data)

    def test_dedup_hint_is_code_not_dict(self):
        """dedup_hint should be a simple string code, not a raw DB dict."""
        if "dedup_hint" in self.data:
            self.assertIsInstance(self.data["dedup_hint"], (str, type(None)),
                                  "dedup_hint must be a string code, not a raw dict")

    def test_conflict_hint_is_code_not_dict(self):
        if "conflict_hint" in self.data:
            self.assertIsInstance(self.data["conflict_hint"], (str, type(None)))

    def test_errors_is_list(self):
        self.assertIsInstance(self.data.get("errors", []), list)

    def test_warnings_is_list(self):
        self.assertIsInstance(self.data.get("warnings", []), list)


# ---------------------------------------------------------------------------
# Forbidden-active fixture invariants
# ---------------------------------------------------------------------------

class ForbiddenActiveFixtureTests(unittest.TestCase):

    def setUp(self):
        self.data = _load("tool-input-forbidden-active.json")

    def test_has_rejection_reason(self):
        self.assertIn("_rejection_reason", self.data)

    def test_has_expected_result_with_errors(self):
        er = self.data.get("_expected_result", {})
        self.assertFalse(er.get("ok", True),
                         "Forbidden-active input must produce ok=False")
        self.assertFalse(er.get("stored", True))
        errors = er.get("errors", [])
        self.assertGreater(len(errors), 0)

    def test_expected_result_still_forces_candidate(self):
        """Even for rejected inputs, status is always candidate."""
        er = self.data.get("_expected_result", {})
        self.assertEqual(er.get("status"), "candidate")
        self.assertEqual(er.get("visibility"), "internal")

    def test_expected_result_review_required(self):
        er = self.data.get("_expected_result", {})
        self.assertTrue(er.get("review_required"))

    def test_forbidden_input_contains_status_active(self):
        """Fixture documents what the model is trying to do (bypass status)."""
        self.assertEqual(self.data.get("status"), "active")
        self.assertEqual(self.data.get("visibility"), "renderable")


# ---------------------------------------------------------------------------
# Forbidden-raw-prompt fixture invariants
# ---------------------------------------------------------------------------

class ForbiddenRawPromptFixtureTests(unittest.TestCase):

    def setUp(self):
        self.data = _load("tool-input-forbidden-raw-prompt.json")

    def test_has_rejection_reason(self):
        self.assertIn("_rejection_reason", self.data)

    def test_contains_raw_prompt_field(self):
        """Fixture documents the forbidden field pattern."""
        self.assertIn("raw_prompt", self.data)

    def test_expected_result_is_error(self):
        er = self.data.get("_expected_result", {})
        self.assertFalse(er.get("ok", True))
        errors = er.get("errors", [])
        self.assertTrue(any("raw_prompt" in e for e in errors),
                        "Expected error must mention raw_prompt")


# ---------------------------------------------------------------------------
# Candidate isolation from render/recall
# ---------------------------------------------------------------------------

class CandidateRenderIsolationTests(unittest.TestCase):
    """Verify that candidate records can't leak into render/recall."""

    def test_candidate_status_not_in_render_eligible_statuses(self):
        """eligible_for_render requires status='active'; candidate must be excluded."""
        from proxy.qz_braincase_render import eligible_for_render
        candidate_record = {
            "record_id": "rec_candidate_test",
            "memory_domain": "coding",
            "tier": "project_state",
            "status": "candidate",
            "visibility": "internal",
            "confidence": 0.9,
            "importance": 0.8,
        }
        ok, reason = eligible_for_render(candidate_record, memory_domain="coding")
        self.assertFalse(ok, "Candidate records must not be render-eligible")
        self.assertIn("status", reason, f"Rejection reason should mention status, got: {reason}")

    def test_internal_visibility_not_render_eligible(self):
        """Even if status were active, visibility=internal must exclude from render."""
        from proxy.qz_braincase_render import eligible_for_render
        internal_record = {
            "record_id": "rec_internal_test",
            "memory_domain": "coding",
            "tier": "project_state",
            "status": "active",
            "visibility": "internal",
        }
        ok, reason = eligible_for_render(internal_record, memory_domain="coding")
        self.assertFalse(ok, "Internal visibility records must not be render-eligible")
        self.assertIn("visibility", reason)

    def test_candidate_with_renderable_visibility_still_excluded_by_status(self):
        """Even if someone manually set visibility=renderable on a candidate, status blocks it."""
        from proxy.qz_braincase_render import eligible_for_render
        bad_record = {
            "record_id": "rec_bad_candidate",
            "memory_domain": "coding",
            "tier": "project_state",
            "status": "candidate",
            "visibility": "renderable",  # should never happen, but if it does
        }
        ok, reason = eligible_for_render(bad_record, memory_domain="coding")
        self.assertFalse(ok, "Candidate status must always block render, even if visibility=renderable")


# ---------------------------------------------------------------------------
# write/update/search/inspect remain unexposed
# ---------------------------------------------------------------------------

class UnexposedToolsTests(unittest.TestCase):

    def test_write_candidate_not_in_current_tool_definitions(self):
        """braincase.write_candidate is not yet in the production tool definitions."""
        import os
        from proxy.qz_braincase_tools import get_braincase_tool_definitions, QZ_BRAINCASE_TOOLS_ENABLED_ENV
        enabled_env = {QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true"}
        defs = get_braincase_tool_definitions(env=enabled_env)
        names = [d.get("name") for d in defs]
        self.assertNotIn("braincase.write_candidate", names,
                         "braincase.write_candidate is a Slice H design; "
                         "not yet in production tool definitions")

    def test_write_not_in_tool_definitions(self):
        from proxy.qz_braincase_tools import get_braincase_tool_definitions, QZ_BRAINCASE_TOOLS_ENABLED_ENV
        defs = get_braincase_tool_definitions(env={QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true"})
        names = [d.get("name") for d in defs]
        for forbidden in ("braincase.write", "braincase.update",
                          "braincase.search", "braincase.inspect"):
            self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
