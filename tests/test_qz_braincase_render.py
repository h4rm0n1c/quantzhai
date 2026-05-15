"""Tests for proxy/qz_braincase_render.py — Slice E render packet builder.

Covers: render_pack, braincase_render_packet, eligible_for_render,
render_record_line, render_budget_chars, make_render_packet_id.

All Slice A fixtures use visibility="internal". Tests that need renderable
records create in-memory copies with visibility="renderable" — fixture files
are never modified.

No model-facing tool wiring is tested here (deferred to Slice F).
No automatic ingestion occurs.
"""
import copy
import json
import pathlib
import tempfile
import time
import unittest
from pathlib import Path

from proxy.qz_braincase_db import BrainCaseDB
from proxy.qz_braincase_render import (
    _FOOTER_SIZE_ESTIMATE,
    _FORBIDDEN_OUTPUT_FIELDS,
    _SCHEMA,
    braincase_render_packet,
    eligible_for_render,
    make_render_packet_id,
    render_budget_chars,
    render_pack,
    render_record_line,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BC_FIXTURES = _REPO_ROOT / "docs" / "fixtures" / "braincase"

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


def _load_state_records() -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted((_BC_FIXTURES / "state-records").glob("*.json"))
    ]


def _load_source_refs() -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted((_BC_FIXTURES / "source-refs").glob("*.json"))
    ]


def _make_renderable(record: dict, **overrides) -> dict:
    """Return a copy of a StateRecord with visibility='renderable'.
    Fixture files are never modified — only in-memory copies are used in tests.
    """
    rec = copy.deepcopy(record)
    rec["visibility"] = "renderable"
    rec.update(overrides)
    return rec


def _fresh_db(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


def _disabled_db(td: str) -> BrainCaseDB:
    return BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)


def _make_simple_record(
    record_id: str = "test_render_001",
    memory_domain: str = "coding",
    tier: str = "project_state",
    record_type: str = "constraint",
    claim: str = "Test claim for render tests.",
    summary: str = "Test summary.",
    importance: float = 0.8,
    visibility: str = "renderable",
    status: str = "active",
    tags: list | None = None,
    **kwargs,
) -> dict:
    return {
        "record_id": record_id,
        "schema": "braincase/state-record@1",
        "memory_domain": memory_domain,
        "tier": tier,
        "record_type": record_type,
        "claim": claim,
        "summary": summary,
        "status": status,
        "visibility": visibility,
        "confidence": 1.0,
        "importance": importance,
        "retention": "project",
        "created_at_ms": 1778803200000,
        "updated_at_ms": 1778803200000,
        "source_refs": [],
        "tags": tags if tags is not None else ["test"],
        "supersedes": None,
        "superseded_by": None,
        "metadata": None,
        **kwargs,
    }


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

class RenderBudgetCharsTests(unittest.TestCase):

    def test_standard_budget(self):
        self.assertEqual(render_budget_chars(100), 400)

    def test_minimum_floor(self):
        # Very tiny budget should not go below 80
        self.assertEqual(render_budget_chars(1), 80)
        self.assertEqual(render_budget_chars(0), 80)

    def test_large_budget(self):
        self.assertEqual(render_budget_chars(600), 2400)


class MakeRenderPacketIdTests(unittest.TestCase):

    def test_returns_string(self):
        pid = make_render_packet_id(1000, "task_continuity", "coding")
        self.assertIsInstance(pid, str)
        self.assertTrue(pid.startswith("pkt_"))

    def test_includes_timestamp(self):
        pid = make_render_packet_id(9999, "purpose", "coding")
        self.assertIn("9999", pid)


class EligibleForRenderTests(unittest.TestCase):

    def _rec(self, **kw) -> dict:
        return _make_simple_record(**kw)

    def test_renderable_active_matches_domain(self):
        ok, reason = eligible_for_render(self._rec(), "coding")
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_internal_excluded(self):
        ok, reason = eligible_for_render(self._rec(visibility="internal"), "coding")
        self.assertFalse(ok)
        self.assertIn("internal", reason)

    def test_never_model_visible_excluded(self):
        ok, reason = eligible_for_render(self._rec(visibility="never_model_visible"), "coding")
        self.assertFalse(ok)

    def test_wrong_status_excluded(self):
        for status in ("superseded", "retired", "candidate"):
            with self.subTest(status=status):
                ok, reason = eligible_for_render(self._rec(status=status), "coding")
                self.assertFalse(ok)

    def test_domain_mismatch_excluded(self):
        ok, reason = eligible_for_render(self._rec(memory_domain="hsm"), "coding")
        self.assertFalse(ok)

    def test_tier_filter_excludes_nonmatching(self):
        ok, reason = eligible_for_render(
            self._rec(tier="project_state"),
            "coding",
            tiers=["procedural_memory"],
        )
        self.assertFalse(ok)

    def test_tier_filter_passes_matching(self):
        ok, _ = eligible_for_render(
            self._rec(tier="project_state"),
            "coding",
            tiers=["project_state", "procedural_memory"],
        )
        self.assertTrue(ok)

    def test_no_tier_filter_accepts_any_tier(self):
        ok, _ = eligible_for_render(self._rec(tier="episodic_memory"), "coding", tiers=None)
        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# render_pack tests
# ---------------------------------------------------------------------------

class RenderPackTests(unittest.TestCase):

    def _two_renderable(self, domain: str = "coding") -> list[dict]:
        return [
            _make_simple_record("r1", memory_domain=domain, importance=0.9, claim="Claim A."),
            _make_simple_record("r2", memory_domain=domain, importance=0.6, claim="Claim B."),
        ]

    # 1. creates RenderPacket-shaped dict
    def test_creates_render_packet_shaped_dict(self):
        pkt = render_pack([], purpose="test", memory_domain="coding")
        for field in _RENDER_PACKET_REQUIRED_FIELDS:
            self.assertIn(field, pkt, f"Missing field: {field}")

    # 2. schema field is correct
    def test_schema_field_is_render_packet_schema(self):
        pkt = render_pack([], purpose="test", memory_domain="coding")
        self.assertEqual(pkt["schema"], "braincase/render-packet@1")

    # 3. enforces memory_domain equality
    def test_enforces_memory_domain_equality(self):
        records = [
            _make_simple_record("r_coding", memory_domain="coding"),
            _make_simple_record("r_hsm", memory_domain="hsm"),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        self.assertIn("r_coding", pkt["source_record_ids"])
        self.assertNotIn("r_hsm", pkt["source_record_ids"])

    # 4. visibility=internal excluded
    def test_visibility_internal_excluded(self):
        records = [
            _make_simple_record("r_internal", visibility="internal"),
            _make_simple_record("r_renderable", visibility="renderable"),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        self.assertNotIn("r_internal", pkt["source_record_ids"])
        self.assertIn("r_renderable", pkt["source_record_ids"])

    # 5. visibility=never_model_visible excluded
    def test_visibility_never_model_visible_excluded(self):
        records = [_make_simple_record("r_nmv", visibility="never_model_visible")]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        self.assertNotIn("r_nmv", pkt["source_record_ids"])

    # 6. status!=active excluded
    def test_status_superseded_excluded(self):
        pkt = render_pack(
            [_make_simple_record("r_sup", status="superseded")],
            purpose="test", memory_domain="coding",
        )
        self.assertNotIn("r_sup", pkt["source_record_ids"])

    def test_status_retired_excluded(self):
        pkt = render_pack(
            [_make_simple_record("r_ret", status="retired")],
            purpose="test", memory_domain="coding",
        )
        self.assertNotIn("r_ret", pkt["source_record_ids"])

    def test_status_candidate_excluded(self):
        pkt = render_pack(
            [_make_simple_record("r_can", status="candidate")],
            purpose="test", memory_domain="coding",
        )
        self.assertNotIn("r_can", pkt["source_record_ids"])

    # 7. tier filter works
    def test_tier_filter_excludes_nonmatching(self):
        records = [
            _make_simple_record("r_proj", tier="project_state"),
            _make_simple_record("r_proc", tier="procedural_memory"),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          tiers=["project_state"])
        self.assertIn("r_proj", pkt["source_record_ids"])
        self.assertNotIn("r_proc", pkt["source_record_ids"])

    # 8. ranks by importance desc, then updated_at_ms desc
    def test_ranks_by_importance_desc(self):
        records = [
            _make_simple_record("r_low", importance=0.3, claim="Low importance."),
            _make_simple_record("r_high", importance=0.9, claim="High importance."),
            _make_simple_record("r_mid", importance=0.6, claim="Mid importance."),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        ids = pkt["source_record_ids"]
        self.assertEqual(ids.index("r_high"), 0)
        self.assertEqual(ids.index("r_mid"), 1)
        self.assertEqual(ids.index("r_low"), 2)

    def test_tiebreak_by_updated_at_ms(self):
        records = [
            _make_simple_record("r_old", importance=0.8, updated_at_ms=1000),
            _make_simple_record("r_new", importance=0.8, updated_at_ms=2000),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        ids = pkt["source_record_ids"]
        self.assertEqual(ids[0], "r_new")  # newer wins tiebreak

    # 9. budget enforcement increments omitted_count
    def test_budget_enforces_omitted_count(self):
        # budget_tokens=5 -> 20 chars — too small for two records; at least one gets omitted
        records = [
            _make_simple_record("r1", importance=0.9, claim="First claim is quite long and takes chars."),
            _make_simple_record("r2", importance=0.6, claim="Second claim also fairly long."),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding", budget_tokens=5)
        # At least one record omitted when budget is tiny
        self.assertGreaterEqual(pkt["omitted_count"], 1)

    # 10. budget_exhausted warning added when records omitted
    def test_budget_exhausted_warning_added(self):
        records = [
            _make_simple_record("r1", claim="A" * 300),
            _make_simple_record("r2", claim="B" * 300),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding", budget_tokens=5)
        # Should have budget_exhausted warning if anything was omitted
        if pkt["omitted_count"] > 0:
            self.assertIn("budget_exhausted", pkt["warnings"])

    def test_no_budget_warning_when_all_fit(self):
        records = [_make_simple_record("r1", claim="Short.")]
        pkt = render_pack(records, purpose="test", memory_domain="coding", budget_tokens=600)
        self.assertNotIn("budget_exhausted", pkt["warnings"])

    # 11. rendered_text includes record_id source markers
    def test_rendered_text_includes_record_id(self):
        records = [_make_simple_record("unique_record_xyz", claim="Test claim.")]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        self.assertIn("unique_record_xyz", pkt["rendered_text"])

    # 12. rendered_text includes tier and record_type
    def test_rendered_text_includes_tier_record_type(self):
        records = [_make_simple_record("r1", tier="project_state", record_type="constraint")]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        self.assertIn("project_state", pkt["rendered_text"])
        self.assertIn("constraint", pkt["rendered_text"])

    # 13. rendered_text does not include raw metadata JSON
    def test_rendered_text_no_raw_metadata(self):
        rec = _make_simple_record("r1", metadata={"secret_key": "secret_value"})
        pkt = render_pack([rec], purpose="test", memory_domain="coding")
        self.assertNotIn("secret_key", pkt["rendered_text"])
        self.assertNotIn("secret_value", pkt["rendered_text"])
        self.assertNotIn("metadata_json", pkt["rendered_text"])

    # 14. rendered_text does not include forbidden raw field names
    def test_rendered_text_no_forbidden_fields(self):
        pkt = render_pack(
            [_make_simple_record("r1")],
            purpose="test", memory_domain="coding",
        )
        for field in _FORBIDDEN_OUTPUT_FIELDS:
            self.assertNotIn(field, pkt["rendered_text"],
                             f"Forbidden field '{field}' found in rendered_text")

    def test_empty_records_returns_valid_packet(self):
        pkt = render_pack([], purpose="test", memory_domain="coding")
        self.assertIn("packet_id", pkt)
        self.assertEqual(pkt["source_record_ids"], [])
        self.assertEqual(pkt["omitted_count"], 0)

    def test_packet_cites_source_ids_not_dump(self):
        """source_record_ids should be record IDs, not full StateRecord dicts."""
        records = [_make_simple_record("r1")]
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        for sid in pkt["source_record_ids"]:
            self.assertIsInstance(sid, str)


# ---------------------------------------------------------------------------
# braincase_render_packet (DB-backed) tests
# ---------------------------------------------------------------------------

class BraincaseRenderPacketTests(unittest.TestCase):

    def _populate_db(self, db: BrainCaseDB, records: list[dict]) -> None:
        for rec in records:
            db.put_state_record(rec)

    # 15. with record_ids inspects selected records
    def test_with_record_ids_inspects_selected_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            r1 = _make_simple_record("r1_insp", claim="Record one.")
            r2 = _make_simple_record("r2_insp", claim="Record two.")
            self._populate_db(db, [r1, r2])
            pkt = braincase_render_packet(
                db, purpose="test", memory_domain="coding",
                record_ids=["r1_insp"],
            )
            # r1 is renderable and should appear; r2 was not requested
            self.assertIn("r1_insp", pkt["source_record_ids"])
            self.assertNotIn("r2_insp", pkt["source_record_ids"])
            db.close()

    # 16. with query searches records
    def test_with_query_searches_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            r1 = _make_simple_record("r1_search", claim="Unique phrase xyzzy for search.")
            self._populate_db(db, [r1])
            pkt = braincase_render_packet(
                db, purpose="test", memory_domain="coding",
                query="xyzzy for search",
            )
            self.assertIn("r1_search", pkt["source_record_ids"])
            db.close()

    # 17. with no query lists records by memory_domain
    def test_no_query_lists_records_by_domain(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            r_coding = _make_simple_record("r_coding_list", memory_domain="coding")
            r_hsm = _make_simple_record("r_hsm_list", memory_domain="hsm")
            self._populate_db(db, [r_coding, r_hsm])
            pkt = braincase_render_packet(
                db, purpose="test", memory_domain="coding",
            )
            self.assertIn("r_coding_list", pkt["source_record_ids"])
            self.assertNotIn("r_hsm_list", pkt["source_record_ids"])
            db.close()

    # 18. missing memory_domain returns warning packet
    def test_missing_memory_domain_returns_warning_packet(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            pkt = braincase_render_packet(db, purpose="test", memory_domain=None)
            self.assertIn("memory_domain_required", pkt["warnings"])
            self.assertEqual(pkt["source_record_ids"], [])
            db.close()

    def test_empty_memory_domain_returns_warning_packet(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            pkt = braincase_render_packet(db, purpose="test", memory_domain="")
            self.assertIn("memory_domain_required", pkt["warnings"])
            db.close()

    # 19. disabled DB returns empty packet with warning, no exception
    def test_disabled_db_returns_empty_packet_no_exception(self):
        with tempfile.TemporaryDirectory() as td:
            db = _disabled_db(td)
            pkt = braincase_render_packet(db, purpose="test", memory_domain="coding")
            self.assertIn("braincase_db_disabled", pkt["warnings"])
            self.assertEqual(pkt["source_record_ids"], [])
            self.assertEqual(pkt["omitted_count"], 0)
            # All required fields still present
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, pkt)

    # 20. cross-domain records excluded
    def test_cross_domain_records_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            r_coding = _make_simple_record("r_cross_coding", memory_domain="coding")
            r_other = _make_simple_record("r_cross_other", memory_domain="roleplay")
            self._populate_db(db, [r_coding, r_other])
            pkt = braincase_render_packet(db, purpose="test", memory_domain="coding")
            self.assertNotIn("r_cross_other", pkt["source_record_ids"])
            db.close()

    # 21. HSM configured-domain records render when memory_domain="hsm"
    def test_hsm_renders_when_domain_matches(self):
        records = _load_state_records()
        hsm_rec = next(r for r in records if r.get("memory_domain") == "hsm")
        renderable_hsm = _make_renderable(hsm_rec)
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            db.put_state_record(renderable_hsm)
            pkt = braincase_render_packet(db, purpose="hsm_test", memory_domain="hsm")
            self.assertIn(hsm_rec["record_id"], pkt["source_record_ids"])
            db.close()

    def test_hsm_not_rendered_for_coding_domain(self):
        records = _load_state_records()
        hsm_rec = _make_renderable(
            next(r for r in records if r.get("memory_domain") == "hsm")
        )
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            db.put_state_record(hsm_rec)
            pkt = braincase_render_packet(db, purpose="test", memory_domain="coding")
            self.assertNotIn(hsm_rec["record_id"], pkt["source_record_ids"])
            db.close()

    # 22. RenderPacket fixture-style fields present
    def test_render_packet_fixture_fields_present(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            pkt = braincase_render_packet(db, purpose="test", memory_domain="coding")
            for field in _RENDER_PACKET_REQUIRED_FIELDS:
                self.assertIn(field, pkt)
            self.assertIsInstance(pkt["source_record_ids"], list)
            self.assertIsInstance(pkt["warnings"], list)
            self.assertIsInstance(pkt["omitted_count"], int)
            self.assertIsInstance(pkt["rendered_text"], str)
            db.close()

    # 23. no model-facing tool or HTTP route
    def test_no_model_facing_tool_or_http_route(self):
        """Structural: verify render module has no HTTP framework imports or route registrations."""
        import proxy.qz_braincase_render as render_mod
        import inspect
        src = inspect.getsource(render_mod)
        # No web framework imports
        self.assertNotIn("from flask", src)
        self.assertNotIn("import flask", src)
        self.assertNotIn("from aiohttp", src)
        self.assertNotIn("import aiohttp", src)
        self.assertNotIn("@app.route", src)
        # No prompt injection
        self.assertNotIn("system_prompt", src)
        self.assertNotIn("inject_prompt", src)


# ---------------------------------------------------------------------------
# Fixture-based round-trip tests
# ---------------------------------------------------------------------------

class FixtureRoundTripTests(unittest.TestCase):

    def test_fixture_records_render_when_made_renderable(self):
        """Slice A fixture records are render-eligible when visibility is set to renderable."""
        records = _load_state_records()
        # Make coding fixtures renderable
        renderable = [
            _make_renderable(r) for r in records if r.get("memory_domain") == "coding"
        ]
        pkt = render_pack(renderable, purpose="fixture_test", memory_domain="coding")
        self.assertGreater(len(pkt["source_record_ids"]), 0)
        self.assertEqual(pkt["schema"], "braincase/render-packet@1")

    def test_fixture_render_packet_matches_fixture(self):
        """The existing render packet fixture should conform to required field set."""
        fixture_path = _REPO_ROOT / "docs" / "fixtures" / "braincase" / "render-packets" / "packet_project_constraints.json"
        pkt = json.loads(fixture_path.read_text())
        for field in _RENDER_PACKET_REQUIRED_FIELDS:
            self.assertIn(field, pkt, f"Fixture missing field: {field}")
        self.assertEqual(pkt["schema"], "braincase/render-packet@1")

    def test_internal_fixtures_are_not_rendered_by_default(self):
        """All Slice A fixtures have visibility='internal' and must not be rendered."""
        records = _load_state_records()
        pkt = render_pack(records, purpose="test", memory_domain="coding")
        # All coding fixtures are internal — none should be in source_record_ids
        self.assertEqual(pkt["source_record_ids"], [])
        self.assertEqual(pkt["omitted_count"], 0)

    def test_render_record_line_does_not_include_forbidden_fields(self):
        """render_record_line must not reference raw field names in output."""
        records = _load_state_records()
        for rec in records:
            line = render_record_line(rec)
            for field in _FORBIDDEN_OUTPUT_FIELDS:
                self.assertNotIn(field, line,
                                 f"Forbidden field '{field}' in render_record_line output")


# ---------------------------------------------------------------------------
# render_record_line unit tests
# ---------------------------------------------------------------------------

class RenderRecordLineTests(unittest.TestCase):

    def test_includes_tier_and_record_type(self):
        rec = _make_simple_record("r1", tier="project_state", record_type="constraint",
                                  claim="A claim.")
        line = render_record_line(rec)
        self.assertIn("project_state", line)
        self.assertIn("constraint", line)

    def test_includes_claim(self):
        rec = _make_simple_record("r1", claim="Specific test claim text.")
        line = render_record_line(rec)
        self.assertIn("Specific test claim text.", line)

    def test_includes_record_id(self):
        rec = _make_simple_record("my_record_id_123")
        line = render_record_line(rec)
        self.assertIn("my_record_id_123", line)

    def test_truncates_very_long_claim(self):
        rec = _make_simple_record("r1", claim="x" * 300)
        line = render_record_line(rec)
        # Claim should be truncated; total line shouldn't be enormous
        self.assertLessEqual(len(line), 800)
        self.assertIn("...", line)

    def test_does_not_include_metadata(self):
        rec = _make_simple_record("r1", metadata={"internal_key": "internal_value"})
        line = render_record_line(rec)
        self.assertNotIn("internal_key", line)
        self.assertNotIn("internal_value", line)


# ---------------------------------------------------------------------------
# Slice E.1 tests: hard budget enforcement
# ---------------------------------------------------------------------------

class HardBudgetTests(unittest.TestCase):
    """Tests for the hard-bounded RenderPacket budget added in Slice E.1."""

    def _two_renderable(self, claim1: str = "Claim A.", claim2: str = "Claim B.") -> list[dict]:
        return [
            _make_simple_record("r1", importance=0.9, claim=claim1),
            _make_simple_record("r2", importance=0.6, claim=claim2),
        ]

    # 1. render_pack never exceeds render_budget_chars()
    def test_rendered_text_never_exceeds_budget(self):
        for budget_tokens in (1, 5, 20, 100, 600):
            chars = render_budget_chars(budget_tokens)
            records = self._two_renderable(
                claim1="A" * 400,
                claim2="B" * 400,
            )
            pkt = render_pack(records, purpose="test", memory_domain="coding",
                              budget_tokens=budget_tokens)
            self.assertLessEqual(
                len(pkt["rendered_text"]), chars,
                f"Budget {chars} chars exceeded at budget_tokens={budget_tokens}: "
                f"got {len(pkt['rendered_text'])} chars",
            )

    def test_rendered_text_bounded_with_short_records(self):
        for budget_tokens in (5, 50, 200):
            chars = render_budget_chars(budget_tokens)
            records = [_make_simple_record(f"r{i}", claim=f"Short claim {i}.") for i in range(5)]
            pkt = render_pack(records, purpose="test", memory_domain="coding",
                              budget_tokens=budget_tokens)
            self.assertLessEqual(len(pkt["rendered_text"]), chars)

    # 2. first eligible long record is truncated or omitted, never over budget
    def test_first_long_record_truncated_or_omitted_not_over_budget(self):
        long_claim = "X" * 2000
        records = [_make_simple_record("r_long", claim=long_claim)]
        for budget_tokens in (1, 5, 100):
            chars = render_budget_chars(budget_tokens)
            pkt = render_pack(records, purpose="test", memory_domain="coding",
                              budget_tokens=budget_tokens)
            self.assertLessEqual(len(pkt["rendered_text"]), chars,
                                 f"First record violated budget at {budget_tokens} tokens")
            # Either included (truncated) or omitted — both are valid
            # What is not valid: included AND over budget

    def test_large_budget_includes_long_record(self):
        """With a generous budget, a long record is included (possibly truncated to 200 chars)."""
        long_claim = "Y" * 500
        records = [_make_simple_record("r_large", claim=long_claim)]
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=600)
        self.assertIn("r_large", pkt["source_record_ids"])
        self.assertLessEqual(len(pkt["rendered_text"]), render_budget_chars(600))

    # 3. source_record_ids includes only actually rendered records
    def test_source_ids_only_includes_rendered_records(self):
        records = [
            _make_simple_record("r1", claim="Short fits."),
            _make_simple_record("r2", claim="Z" * 2000, importance=0.1),
        ]
        # Budget large enough for r1 but should not include r2 if r2 is omitted
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=600)
        for rid in pkt["source_record_ids"]:
            self.assertIn(rid, pkt["rendered_text"],
                          f"source_record_id '{rid}' not found in rendered_text")

    def test_source_ids_empty_when_nothing_rendered(self):
        # Extremely tiny budget that can't fit any record
        records = [_make_simple_record("r1", claim="A" * 2000)]
        pkt = render_pack(records, purpose="t", memory_domain="c",
                          budget_tokens=1)  # 80 chars; header alone ~32 chars, tight
        # Either r1 is in source_ids and rendered_text, or neither
        for rid in pkt["source_record_ids"]:
            self.assertIn(rid, pkt["rendered_text"])

    # 4. omitted_count counts eligible records omitted by budget
    def test_omitted_count_correct_when_budget_tight(self):
        records = self._two_renderable(claim1="A" * 2000, claim2="B" * 2000)
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=5)  # 80 chars — very tight
        # Both records have 2000-char claims; expect both omitted
        self.assertEqual(pkt["omitted_count"], 2)
        self.assertEqual(pkt["source_record_ids"], [])

    def test_omitted_count_accurate_for_partial_fit(self):
        """One record fits, one is omitted due to insufficient remaining budget.

        budget_tokens=40 (160 chars) is tight enough that after r_fits is included
        (~55 chars of body), the remaining budget is too small for even the minimal
        form of r_omit (~45 chars for classification + Source line).
        """
        records = [
            _make_simple_record("r_fits", importance=0.9, claim="Short."),
            _make_simple_record("r_omit", importance=0.1, claim="Q" * 2000),
        ]
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=40)  # 160 chars — tight after first record
        self.assertIn("r_fits", pkt["source_record_ids"])
        self.assertNotIn("r_omit", pkt["source_record_ids"])
        self.assertEqual(pkt["omitted_count"], 1)

    # 5. budget_exhausted warning when records omitted
    def test_budget_exhausted_warning_when_omitted(self):
        records = self._two_renderable(claim1="A" * 2000, claim2="B" * 2000)
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=5)
        self.assertGreater(pkt["omitted_count"], 0)
        self.assertIn("budget_exhausted", pkt["warnings"])

    def test_no_budget_exhausted_when_all_fit(self):
        records = [_make_simple_record("r1", claim="Short claim.")]
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=600)
        self.assertNotIn("budget_exhausted", pkt["warnings"])

    # 6. record_truncated warning when truncation occurs
    def test_record_truncated_warning_when_claim_is_long(self):
        """A very long claim triggers the budget-constrained truncation path.

        budget_tokens=50 (200 chars) is tight enough that the default 200-char
        claim pre-truncation still produces a line (~247 chars with tier+source)
        that exceeds max_line_chars (~122 chars), forcing the budget-constrained
        render_record_line(max_chars=...) path, which adds record_truncated.
        """
        records = [_make_simple_record("r_trunc", claim="W" * 2000)]
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=50)  # 200 chars — tight enough to trigger truncation path
        chars = render_budget_chars(50)
        self.assertLessEqual(len(pkt["rendered_text"]), chars)
        if pkt["source_record_ids"]:
            # Record was included via truncation path
            self.assertIn("record_truncated", pkt["warnings"])
            self.assertIn("r_trunc", pkt["rendered_text"])

    def test_no_record_truncated_warning_for_short_claims(self):
        records = [_make_simple_record("r1", claim="A short claim.")]
        pkt = render_pack(records, purpose="test", memory_domain="coding",
                          budget_tokens=600)
        self.assertNotIn("record_truncated", pkt["warnings"])

    # 7. tiny budget returns valid RenderPacket with bounded rendered_text
    def test_tiny_budget_returns_bounded_text(self):
        records = [_make_simple_record("r1", claim="This is a claim.")]
        pkt = render_pack(records, purpose="t", memory_domain="c",
                          budget_tokens=1)  # 80 chars minimum
        chars = render_budget_chars(1)
        self.assertLessEqual(len(pkt["rendered_text"]), chars)
        # Must still have all required fields
        for field in _RENDER_PACKET_REQUIRED_FIELDS:
            self.assertIn(field, pkt)

    def test_zero_eligible_records_renders_header_footer_within_budget(self):
        pkt = render_pack([], purpose="test", memory_domain="coding",
                          budget_tokens=1)
        self.assertLessEqual(len(pkt["rendered_text"]), render_budget_chars(1))

    # 8. no forbidden raw fields after truncation path
    def test_no_forbidden_fields_in_truncated_output(self):
        # Even if a record had a forbidden field name in its claim (unusual but defensive)
        rec = _make_simple_record("r1", claim="raw_prompt: some claim text here " * 10)
        pkt = render_pack([rec], purpose="test", memory_domain="coding",
                          budget_tokens=5)
        # Whether included or omitted, the rendered_text must never contain forbidden key patterns
        for field in _FORBIDDEN_OUTPUT_FIELDS - {"raw_prompt"}:  # raw_prompt can appear as text
            self.assertNotIn(field, pkt["rendered_text"])

    # 9. braincase_render_packet obeys budget for all retrieval modes
    def test_braincase_render_packet_obeys_budget_list_mode(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            for i in range(5):
                db.put_state_record(_make_simple_record(f"r{i}", claim="Z" * 300))
            for budget_tokens in (5, 50, 200):
                chars = render_budget_chars(budget_tokens)
                pkt = braincase_render_packet(db, purpose="test",
                                              memory_domain="coding",
                                              budget_tokens=budget_tokens)
                self.assertLessEqual(len(pkt["rendered_text"]), chars,
                                     f"Budget violated at {budget_tokens} tokens")
            db.close()

    def test_braincase_render_packet_obeys_budget_query_mode(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            db.put_state_record(_make_simple_record("r_q", claim="unique_search_xyzzy " * 50))
            chars = render_budget_chars(10)
            pkt = braincase_render_packet(db, purpose="test",
                                          memory_domain="coding",
                                          query="unique_search_xyzzy",
                                          budget_tokens=10)
            self.assertLessEqual(len(pkt["rendered_text"]), chars)
            db.close()

    def test_braincase_render_packet_obeys_budget_record_ids_mode(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            db.put_state_record(_make_simple_record("r_id", claim="K" * 500))
            chars = render_budget_chars(5)
            pkt = braincase_render_packet(db, purpose="test",
                                          memory_domain="coding",
                                          record_ids=["r_id"],
                                          budget_tokens=5)
            self.assertLessEqual(len(pkt["rendered_text"]), chars)
            db.close()


# ---------------------------------------------------------------------------
# render_record_line max_chars tests (Slice E.1)
# ---------------------------------------------------------------------------

class RenderRecordLineMaxCharsTests(unittest.TestCase):

    def _rec(self, claim: str = "Test claim.", **kw) -> dict:
        return _make_simple_record("r1", claim=claim, **kw)

    def test_no_max_chars_returns_string(self):
        result = render_record_line(self._rec())
        self.assertIsInstance(result, str)

    def test_generous_max_chars_returns_full_line(self):
        rec = self._rec(claim="Short.")
        result = render_record_line(rec, max_chars=500)
        self.assertIsNotNone(result)
        self.assertIn("Short.", result)

    def test_tiny_max_chars_returns_none_when_minimal_cant_fit(self):
        rec = self._rec()
        # Max 1 char — even minimal form can't fit
        result = render_record_line(rec, max_chars=1)
        self.assertIsNone(result)

    def test_max_chars_result_within_limit(self):
        rec = self._rec(claim="A" * 500)
        for limit in (50, 100, 200):
            result = render_record_line(rec, max_chars=limit)
            if result is not None:
                self.assertLessEqual(len(result), limit,
                                     f"render_record_line exceeded max_chars={limit}")

    def test_truncated_result_preserves_record_id(self):
        rec = _make_simple_record("my_unique_id", claim="B" * 500)
        result = render_record_line(rec, max_chars=80)
        if result is not None:
            self.assertIn("my_unique_id", result)


if __name__ == "__main__":
    unittest.main()
