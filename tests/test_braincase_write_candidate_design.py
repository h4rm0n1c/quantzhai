"""Structural design tests for Slices H and H.1: braincase.write_candidate.

These tests validate the fixture files and design invariants.
The runtime implementation landed in Slice H.2 (proxy/qz_braincase_tools.py).
These tests continue to validate the design fixtures and doctrine.

Slice H invariants:
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

Slice H.1 invariants (polished doctrine):
- Forbidden status/visibility → REJECTION (ok=false, stored=false), not override.
- Defensive force of candidate/internal is a backstop, not an acceptance path.
- memory_domain authority is config/caller-owned, not BrainCaseDB-owned.
- HSM wording uses "configured memory_domain example" not hard-coded/built-in.
- Raw log/prompt in claim/summary → hard error, no storage, not warning.
- WriteCandidateResult is not RenderPacket-shaped; no raw StateRecord field.
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


# =============================================================================
# SLICE H.1 ADDITIONS: polished doctrine tests
# =============================================================================

class FixtureParseH1Tests(unittest.TestCase):
    """Parse all H.1 fixtures."""

    def test_forbidden_raw_log_in_claim_parses(self):
        data = _load("tool-input-forbidden-raw-log-in-claim.json")
        self.assertIsInstance(data, dict)

    def test_all_fixtures_present(self):
        expected = {
            "tool-input-valid.json",
            "result-valid.json",
            "tool-input-forbidden-active.json",
            "tool-input-forbidden-raw-prompt.json",
            "tool-input-forbidden-raw-log-in-claim.json",
        }
        actual = {p.name for p in _FIXTURE_DIR.glob("*.json")}
        for name in expected:
            self.assertIn(name, actual, f"Expected fixture {name!r} not found")


class RejectFirstPolicyTests(unittest.TestCase):
    """Reject-first: forbidden status/visibility are REJECTION not override."""

    def setUp(self):
        self.data = _load("tool-input-forbidden-active.json")

    def test_policy_key_says_reject_first(self):
        """Fixture must document reject-first policy, not override-and-warn."""
        policy = self.data.get("_policy", "")
        self.assertIn("reject", policy.lower(),
                      "_policy must say 'reject-first', not just 'override'")

    def test_rejection_reason_describes_rejection(self):
        """Rejection reason must describe a rejection, not a successful-write path."""
        reason = self.data.get("_rejection_reason", "")
        # Must NOT describe a write that proceeds
        self.assertNotIn("write still proceeds", reason.lower())
        # Must describe rejection / error
        self.assertTrue(
            "reject" in reason.lower() or "error" in reason.lower()
            or "ok=false" in reason.lower(),
            f"_rejection_reason should describe rejection/error, got: {reason}"
        )

    def test_expected_result_is_rejection_not_override(self):
        """ok=false means rejection. stored=false means nothing was written."""
        er = self.data["_expected_result"]
        self.assertFalse(er["ok"],
                         "Forbidden-active expected result must have ok=False (rejection)")
        self.assertFalse(er["stored"],
                         "Forbidden-active expected result must have stored=False (no write)")

    def test_defensive_backstop_still_reports_candidate_internal(self):
        """Defensive invariant: even on rejection, status/visibility are candidate/internal."""
        er = self.data["_expected_result"]
        self.assertEqual(er.get("status"), "candidate",
                         "Even rejected result reports status=candidate (defensive)")
        self.assertEqual(er.get("visibility"), "internal",
                         "Even rejected result reports visibility=internal (defensive)")

    def test_no_status_overridden_warning_name_in_fixture(self):
        """The permissive 'status_overridden_to_candidate' warning must not appear."""
        fixture_str = json.dumps(self.data)
        self.assertNotIn("status_overridden_to_candidate", fixture_str,
                         "Permissive override warning name must not appear in fixtures")
        self.assertNotIn("visibility_overridden_to_internal", fixture_str)


class MemoryDomainAuthorityTests(unittest.TestCase):
    """memory_domain authority is config/caller-owned, not BrainCaseDB-owned."""

    def test_valid_input_does_not_imply_db_registry(self):
        """Valid fixture memory_domain is a plain string — no registry implied."""
        data = _load("tool-input-valid.json")
        md = data.get("memory_domain", "")
        self.assertIsInstance(md, str)
        self.assertTrue(md.strip(), "memory_domain must be a non-empty string")

    def test_design_doc_memory_domain_section_exists(self):
        """Design doc must have a memory_domain authority section (docs test)."""
        doc = _REPO / "docs" / "braincase-memory-tool-api.md"
        text = doc.read_text()
        self.assertIn("memory_domain authority", text,
                      "Design doc must document memory_domain authority")
        self.assertIn("config/caller-owned", text,
                      "Design doc must say memory_domain is config/caller-owned")

    def test_design_doc_no_braincase_registry_claim(self):
        doc = _REPO / "docs" / "braincase-memory-tool-api.md"
        text = doc.read_text()
        # The doc should say BrainCaseDB has no registry
        self.assertIn("no memory_domain registry", text,
                      "Design doc must explicitly state BrainCaseDB has no registry")

    def test_scope_resolve_is_caller_policy_not_db_registry(self):
        """scope_resolve() checks caller-supplied domains, not a DB list."""
        from proxy.qz_braincase_write import scope_resolve
        # When no allowed_domains supplied, any non-empty domain is accepted
        rec = {"memory_domain": "any_configured_domain"}
        result = scope_resolve(rec)
        self.assertTrue(result["ok"],
                        "scope_resolve must accept any non-empty domain when no "
                        "allowed_domains are supplied — no DB registry check")


class HsmWordingTests(unittest.TestCase):
    """HSM is a configured example domain, not hard-coded or built-in."""

    def _doc_text(self):
        return (_REPO / "docs" / "braincase-memory-tool-api.md").read_text()

    def test_hsm_not_described_as_specific_memory_domain(self):
        """No load-bearing 'HSM is a specific memory_domain' statement in the doc."""
        text = self._doc_text()
        # The slice H.1 completion note quotes the OLD wording as an artifact —
        # that's acceptable. What must NOT exist is a live, un-quoted description
        # that still says "HSM is a specific memory_domain" outside a quoted context.
        # The HSM/LimbiCore mapping section is the authoritative place.
        # Check that the authoritative section uses the corrected phrasing.
        hsm_section_start = text.find("## HSM / LimbiCore")
        hsm_section_end = text.find("\n---", hsm_section_start)
        if hsm_section_start >= 0 and hsm_section_end > hsm_section_start:
            hsm_section = text[hsm_section_start:hsm_section_end]
            self.assertNotIn("HSM is a specific memory_domain", hsm_section,
                             "The HSM/LimbiCore mapping section must not say "
                             "'HSM is a specific memory_domain'")

    def test_hsm_described_as_configured_example(self):
        text = self._doc_text()
        self.assertTrue(
            "configured memory_domain" in text and "hsm" in text,
            "Design doc should describe HSM as a configured memory_domain example"
        )

    def test_braincase_db_does_not_hardcode_hsm(self):
        """BrainCaseDB source must not contain special-case HSM handling."""
        db_src = (_REPO / "proxy" / "qz_braincase_db.py").read_text()
        # Check for hard-coded HSM special-casing
        self.assertNotIn("\"hsm\"", db_src,
                         "qz_braincase_db.py must not hard-code 'hsm' as a special domain")


class RawLogInClaimFixtureTests(unittest.TestCase):
    """Raw log/prompt smuggled into claim is a hard error, not a warning."""

    def setUp(self):
        self.data = _load("tool-input-forbidden-raw-log-in-claim.json")

    def test_has_rejection_reason(self):
        self.assertIn("_rejection_reason", self.data)

    def test_policy_says_hard_error(self):
        policy = self.data.get("_policy", "")
        self.assertIn("hard error", policy.lower(),
                      "_policy must say hard error, not warning")
        # Policy should not say "warn" in a positive sense (allowing "Not a warning" is fine)
        lower = policy.lower()
        if "warn" in lower:
            # Allowed only if "warn" appears in a negation context
            self.assertTrue(
                "not a warn" in lower or "no warn" in lower,
                f"_policy contains 'warn' in a non-negation context: {policy}"
            )

    def test_expected_result_is_rejection(self):
        er = self.data.get("_expected_result", {})
        self.assertFalse(er.get("ok", True))
        self.assertFalse(er.get("stored", True))
        errors = er.get("errors", [])
        self.assertGreater(len(errors), 0,
                           "Expected errors list must be non-empty for raw-log-in-claim")

    def test_claim_contains_raw_log_marker(self):
        """Fixture documents what raw-log-in-claim looks like."""
        claim = self.data.get("claim", "")
        # Claim should contain obvious session/log/raw content
        self.assertTrue(
            "raw_request_body" in claim or
            "User:" in claim or
            "Assistant:" in claim or
            "[Turn" in claim,
            "Fixture claim should contain obvious raw log/session markers"
        )

    def test_expected_result_still_candidate_internal(self):
        er = self.data.get("_expected_result", {})
        self.assertEqual(er.get("status"), "candidate")
        self.assertEqual(er.get("visibility"), "internal")


class WriteCandidateResultBoundsTests(unittest.TestCase):
    """WriteCandidateResult must be bounded — not a RenderPacket, no raw dumps."""

    def setUp(self):
        self.data = _load("result-valid.json")

    def test_not_render_packet_schema(self):
        """Result must not have a RenderPacket schema field."""
        schema = self.data.get("schema", "")
        self.assertNotIn("render-packet", schema,
                         "WriteCandidateResult must not use RenderPacket schema")

    def test_no_rendered_text_field(self):
        """Result must not have rendered_text (RenderPacket field)."""
        self.assertNotIn("rendered_text", self.data,
                         "WriteCandidateResult must not contain rendered_text")

    def test_no_packet_id_field(self):
        """Result must not have packet_id (RenderPacket field)."""
        self.assertNotIn("packet_id", self.data,
                         "WriteCandidateResult must not contain packet_id")

    def test_no_raw_state_record_or_state_records(self):
        self.assertNotIn("record", self.data)
        self.assertNotIn("state_records", self.data)
        self.assertNotIn("raw_record", self.data)

    def test_no_source_blob_or_raw_source(self):
        self.assertNotIn("source_body", self.data)
        self.assertNotIn("raw_source", self.data)
        self.assertNotIn("raw_prompt", self.data)
        self.assertNotIn("raw_request_body", self.data)

    def test_dedup_hint_is_string_code(self):
        if "dedup_hint" in self.data:
            self.assertIsInstance(self.data["dedup_hint"], (str, type(None)),
                                  "dedup_hint must be a string code, not a raw DB dict")

    def test_conflict_hint_is_string_code(self):
        if "conflict_hint" in self.data:
            self.assertIsInstance(self.data["conflict_hint"], (str, type(None)))

    def test_result_has_bounded_required_fields_only(self):
        """Result should not contain unexpected raw-dump keys."""
        raw_dump_keys = {"claim", "summary", "tier", "record_type",
                         "memory_domain_raw", "source_refs_raw", "metadata_raw"}
        present = raw_dump_keys & set(self.data.keys())
        self.assertEqual(present, set(),
                         f"WriteCandidateResult must not echo raw input back: {present}")


if __name__ == "__main__":
    unittest.main()
