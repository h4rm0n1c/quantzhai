"""Structural validation tests for BrainCase schema and fixture files.

Validates that:
- All schema JSON files parse correctly.
- All fixture JSON files parse correctly.
- StateRecord fixtures have required fields and allowed enum values.
- SourceRef fixtures have required fields and allowed source_type values.
- RenderPacket fixtures have required fields.
- No fixture contains forbidden field names that imply automatic ingestion.
- memory_domain is always a plain string (no registry, no enum in schema).
- HSM fixture documents hsm as a configured example, not a built-in domain.
- Schemas do not define a memory_domain enum (config-owned invariant).
- RenderPackets are the only model-facing fixture type.
- Source refs do not embed raw content blobs.

Uses stdlib only. No jsonschema dependency.
"""

import json
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "docs" / "schemas" / "braincase"
FIXTURES_DIR = REPO_ROOT / "docs" / "fixtures" / "braincase"

# --- Allowed enum values drawn from state-record.schema.json ---

ALLOWED_TIERS = {
    "working_state",
    "session_state",
    "project_state",
    "semantic_memory",
    "procedural_memory",
    "episodic_memory",
    "artifact_memory",
    "perceptual_index",
    "preference_constraint_memory",
}

ALLOWED_RECORD_TYPES = {
    "constraint",
    "preference",
    "project_decision",
    "project_state",
    "procedure",
    "artifact_reference",
    "diagnostic",
    "open_question",
    "identity_note",
    "correction",
    "episode",
    "recent_topic",
}

ALLOWED_STATUS = {"active", "superseded", "retired", "candidate"}

ALLOWED_VISIBILITY = {"internal", "renderable", "never_model_visible"}

ALLOWED_RETENTION = {"ephemeral", "session", "project", "durable"}

ALLOWED_SOURCE_TYPES = {
    "user_instruction",
    "github_issue",
    "github_commit",
    "repo_file",
    "doc_file",
    "artifact",
    "capture",
    "manual_note",
}

STATE_RECORD_REQUIRED = {
    "record_id",
    "schema",
    "memory_domain",
    "tier",
    "record_type",
    "claim",
    "summary",
    "status",
    "visibility",
    "confidence",
    "importance",
    "retention",
    "created_at_ms",
    "updated_at_ms",
    "source_refs",
    "tags",
}

SOURCE_REF_REQUIRED = {"source_ref_id", "source_type", "summary", "locator"}

RENDER_PACKET_REQUIRED = {
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

# Fields that must never appear in any fixture — their presence implies
# automatic ingestion or raw content embedding.
FORBIDDEN_FIELDS = {
    "raw_prompt",
    "raw_request_body",
    "request_body",
    "prompt",
    "full_log",
    "telemetry_event",
    "stream_event",
    "raw_content",
    "full_body",
    "ingestion_source",
    "auto_ingested",
}


def _load_json(path: pathlib.Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _fixture_paths(subdir: str) -> list[pathlib.Path]:
    d = FIXTURES_DIR / subdir
    return sorted(d.glob("*.json")) if d.exists() else []


def _schema_paths() -> list[pathlib.Path]:
    return sorted(SCHEMAS_DIR.glob("*.schema.json")) if SCHEMAS_DIR.exists() else []


def _all_keys_recursive(obj, prefix="") -> set[str]:
    """Return every key at every depth of a nested dict/list structure."""
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys.update(_all_keys_recursive(v, prefix=k))
    elif isinstance(obj, list):
        for item in obj:
            keys.update(_all_keys_recursive(item, prefix=prefix))
    return keys


class SchemaParsingTests(unittest.TestCase):
    """All schema JSON files must parse without error."""

    def test_source_ref_schema_parses(self):
        path = SCHEMAS_DIR / "source-ref.schema.json"
        self.assertTrue(path.exists(), f"Missing: {path}")
        data = _load_json(path)
        self.assertIn("$schema", data)
        self.assertIn("$id", data)
        self.assertEqual(data["type"], "object")

    def test_state_record_schema_parses(self):
        path = SCHEMAS_DIR / "state-record.schema.json"
        self.assertTrue(path.exists(), f"Missing: {path}")
        data = _load_json(path)
        self.assertIn("$schema", data)
        self.assertIn("$id", data)
        self.assertEqual(data["type"], "object")

    def test_render_packet_schema_parses(self):
        path = SCHEMAS_DIR / "render-packet.schema.json"
        self.assertTrue(path.exists(), f"Missing: {path}")
        data = _load_json(path)
        self.assertIn("$schema", data)
        self.assertIn("$id", data)
        self.assertEqual(data["type"], "object")

    def test_all_schemas_have_required_fields(self):
        for path in _schema_paths():
            with self.subTest(schema=path.name):
                data = _load_json(path)
                self.assertIn("required", data, f"{path.name} missing 'required' list")
                self.assertIsInstance(data["required"], list)
                self.assertGreater(len(data["required"]), 0)

    def test_state_record_schema_memory_domain_has_no_enum(self):
        """memory_domain must be type string with no enum — it is config-owned."""
        path = SCHEMAS_DIR / "state-record.schema.json"
        data = _load_json(path)
        domain_prop = data["properties"]["memory_domain"]
        self.assertEqual(domain_prop["type"], "string",
                         "memory_domain must be plain string — no enum allowed")
        self.assertNotIn("enum", domain_prop,
                         "memory_domain must not have an enum — it is config-owned, not schema-defined")

    def test_render_packet_schema_memory_domain_has_no_enum(self):
        """memory_domain in RenderPacket schema must also be a plain string."""
        path = SCHEMAS_DIR / "render-packet.schema.json"
        data = _load_json(path)
        domain_prop = data["properties"]["memory_domain"]
        self.assertEqual(domain_prop["type"], "string")
        self.assertNotIn("enum", domain_prop,
                         "memory_domain must not have an enum in render-packet schema")

    def test_schemas_do_not_define_memory_domain_registry(self):
        """No schema should define a memory_domain registry or enumerate valid domains."""
        for path in _schema_paths():
            with self.subTest(schema=path.name):
                raw = path.read_text()
                self.assertNotIn("memory_domain_registry", raw)
                self.assertNotIn("grants_domain", raw)
                self.assertNotIn("authorizes_domain", raw)


class FixtureParsingTests(unittest.TestCase):
    """All fixture JSON files must parse without error."""

    def test_source_ref_fixtures_parse(self):
        paths = _fixture_paths("source-refs")
        self.assertGreater(len(paths), 0, "No source-ref fixtures found")
        for path in paths:
            with self.subTest(fixture=path.name):
                data = _load_json(path)
                self.assertIsInstance(data, dict)

    def test_state_record_fixtures_parse(self):
        paths = _fixture_paths("state-records")
        self.assertGreater(len(paths), 0, "No state-record fixtures found")
        for path in paths:
            with self.subTest(fixture=path.name):
                data = _load_json(path)
                self.assertIsInstance(data, dict)

    def test_render_packet_fixtures_parse(self):
        paths = _fixture_paths("render-packets")
        self.assertGreater(len(paths), 0, "No render-packet fixtures found")
        for path in paths:
            with self.subTest(fixture=path.name):
                data = _load_json(path)
                self.assertIsInstance(data, dict)


class StateRecordStructureTests(unittest.TestCase):
    """StateRecord fixtures must have correct structure and values."""

    def _records(self):
        return [(_load_json(p), p.name) for p in _fixture_paths("state-records")]

    def test_required_fields_present(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                missing = STATE_RECORD_REQUIRED - set(rec.keys())
                self.assertEqual(missing, set(), f"{name} missing fields: {missing}")

    def test_tier_values_allowed(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIn(rec["tier"], ALLOWED_TIERS,
                              f"{name} has unexpected tier: {rec['tier']}")

    def test_record_type_values_allowed(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIn(rec["record_type"], ALLOWED_RECORD_TYPES,
                              f"{name} has unexpected record_type: {rec['record_type']}")

    def test_status_values_allowed(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIn(rec["status"], ALLOWED_STATUS,
                              f"{name} has unexpected status: {rec['status']}")

    def test_visibility_values_allowed(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIn(rec["visibility"], ALLOWED_VISIBILITY,
                              f"{name} has unexpected visibility: {rec['visibility']}")

    def test_retention_values_allowed(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIn(rec["retention"], ALLOWED_RETENTION,
                              f"{name} has unexpected retention: {rec['retention']}")

    def test_memory_domain_is_string(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIsInstance(rec["memory_domain"], str,
                                      f"{name} memory_domain must be a string")
                self.assertGreater(len(rec["memory_domain"]), 0,
                                   f"{name} memory_domain must not be empty")

    def test_confidence_in_range(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertGreaterEqual(rec["confidence"], 0.0)
                self.assertLessEqual(rec["confidence"], 1.0)

    def test_importance_in_range(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertGreaterEqual(rec["importance"], 0.0)
                self.assertLessEqual(rec["importance"], 1.0)

    def test_source_refs_is_list(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIsInstance(rec["source_refs"], list,
                                      f"{name} source_refs must be a list")

    def test_tags_is_list(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertIsInstance(rec["tags"], list,
                                      f"{name} tags must be a list")

    def test_no_forbidden_fields(self):
        for rec, name in self._records():
            with self.subTest(fixture=name):
                all_keys = _all_keys_recursive(rec)
                bad = all_keys & FORBIDDEN_FIELDS
                self.assertEqual(bad, set(),
                                 f"{name} contains forbidden fields: {bad}")

    def test_visibility_not_directly_model_facing(self):
        """StateRecord visibility must not imply automatic model visibility.

        'internal' and 'never_model_visible' are clearly not model-facing.
        'renderable' is eligible for render but not automatically shown.
        No StateRecord should have a visibility value outside the allowed set
        (which is already tested), but this test also verifies that none of
        the fixtures default to a render-output mode.
        """
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertNotEqual(rec.get("visibility"), "prompt_prefix",
                                    f"{name} must not use prompt_prefix visibility")
                self.assertNotEqual(rec.get("visibility"), "model_visible",
                                    f"{name} must not be marked model_visible directly")

    def test_records_do_not_have_rendered_text(self):
        """StateRecords are not model-facing. Only RenderPackets have rendered_text."""
        for rec, name in self._records():
            with self.subTest(fixture=name):
                self.assertNotIn("rendered_text", rec,
                                 f"{name} must not have rendered_text — only RenderPackets do")


class SourceRefStructureTests(unittest.TestCase):
    """SourceRef fixtures must have correct structure."""

    def _refs(self):
        return [(_load_json(p), p.name) for p in _fixture_paths("source-refs")]

    def test_required_fields_present(self):
        for ref, name in self._refs():
            with self.subTest(fixture=name):
                missing = SOURCE_REF_REQUIRED - set(ref.keys())
                self.assertEqual(missing, set(), f"{name} missing fields: {missing}")

    def test_source_type_values_allowed(self):
        for ref, name in self._refs():
            with self.subTest(fixture=name):
                self.assertIn(ref["source_type"], ALLOWED_SOURCE_TYPES,
                              f"{name} has unexpected source_type: {ref['source_type']}")

    def test_no_raw_content_embedding(self):
        """Source refs must not embed raw request bodies, prompts, or full content blobs."""
        raw_content_keys = {"raw_content", "full_body", "raw_request_body", "raw_prompt",
                            "full_text", "content_blob", "request_body"}
        for ref, name in self._refs():
            with self.subTest(fixture=name):
                all_keys = _all_keys_recursive(ref)
                bad = all_keys & raw_content_keys
                self.assertEqual(bad, set(),
                                 f"{name} embeds raw content: {bad}")

    def test_summary_is_bounded(self):
        """Summary must be a non-empty string under 1000 chars (schema maxLength)."""
        for ref, name in self._refs():
            with self.subTest(fixture=name):
                self.assertIsInstance(ref["summary"], str)
                self.assertGreater(len(ref["summary"]), 0)
                self.assertLessEqual(len(ref["summary"]), 1000,
                                     f"{name} summary exceeds 1000 chars")

    def test_no_forbidden_fields(self):
        for ref, name in self._refs():
            with self.subTest(fixture=name):
                all_keys = _all_keys_recursive(ref)
                bad = all_keys & FORBIDDEN_FIELDS
                self.assertEqual(bad, set(),
                                 f"{name} contains forbidden fields: {bad}")


class RenderPacketStructureTests(unittest.TestCase):
    """RenderPacket fixtures must have correct structure."""

    def _packets(self):
        return [(_load_json(p), p.name) for p in _fixture_paths("render-packets")]

    def test_required_fields_present(self):
        for pkt, name in self._packets():
            with self.subTest(fixture=name):
                missing = RENDER_PACKET_REQUIRED - set(pkt.keys())
                self.assertEqual(missing, set(), f"{name} missing fields: {missing}")

    def test_rendered_text_is_string(self):
        for pkt, name in self._packets():
            with self.subTest(fixture=name):
                self.assertIsInstance(pkt["rendered_text"], str)
                self.assertGreater(len(pkt["rendered_text"]), 0)

    def test_source_record_ids_is_list(self):
        for pkt, name in self._packets():
            with self.subTest(fixture=name):
                self.assertIsInstance(pkt["source_record_ids"], list)

    def test_omitted_count_is_non_negative(self):
        for pkt, name in self._packets():
            with self.subTest(fixture=name):
                self.assertGreaterEqual(pkt["omitted_count"], 0)

    def test_budget_tokens_positive(self):
        for pkt, name in self._packets():
            with self.subTest(fixture=name):
                self.assertGreater(pkt["budget_tokens"], 0)

    def test_memory_domain_is_string(self):
        for pkt, name in self._packets():
            with self.subTest(fixture=name):
                self.assertIsInstance(pkt["memory_domain"], str)
                self.assertGreater(len(pkt["memory_domain"]), 0)

    def test_no_forbidden_fields(self):
        for pkt, name in self._packets():
            with self.subTest(fixture=name):
                all_keys = _all_keys_recursive(pkt)
                bad = all_keys & FORBIDDEN_FIELDS
                self.assertEqual(bad, set(),
                                 f"{name} contains forbidden fields: {bad}")


class DoctrineTests(unittest.TestCase):
    """Cross-cutting doctrine checks across all fixtures and schemas."""

    def _all_fixture_paths(self) -> list[pathlib.Path]:
        return (
            _fixture_paths("source-refs")
            + _fixture_paths("state-records")
            + _fixture_paths("render-packets")
        )

    def test_no_forbidden_fields_anywhere(self):
        """No fixture file anywhere should contain forbidden field names."""
        for path in self._all_fixture_paths():
            with self.subTest(fixture=path.name):
                data = _load_json(path)
                all_keys = _all_keys_recursive(data)
                bad = all_keys & FORBIDDEN_FIELDS
                self.assertEqual(bad, set(),
                                 f"{path.name} contains forbidden fields: {bad}")

    def test_no_memory_domain_registry_in_any_file(self):
        """No schema or fixture should claim to define a memory_domain registry."""
        all_paths = _schema_paths() + self._all_fixture_paths()
        for path in all_paths:
            with self.subTest(file=path.name):
                raw = path.read_text()
                self.assertNotIn("memory_domain_registry", raw,
                                 f"{path.name} must not reference a memory_domain registry")
                self.assertNotIn("grants_domain", raw)
                self.assertNotIn("authorizes_domain", raw)

    def test_render_packets_are_only_model_facing_type(self):
        """Only RenderPacket fixtures should have rendered_text."""
        for path in _fixture_paths("source-refs") + _fixture_paths("state-records"):
            with self.subTest(fixture=path.name):
                data = _load_json(path)
                self.assertNotIn("rendered_text", data,
                                 f"{path.name} must not have rendered_text — only RenderPackets do")

        for path in _fixture_paths("render-packets"):
            with self.subTest(fixture=path.name):
                data = _load_json(path)
                self.assertIn("rendered_text", data,
                              f"RenderPacket {path.name} must have rendered_text")

    def test_hsm_fixture_treats_domain_as_configured_value(self):
        """The HSM fixture must document hsm as a configured example, not a built-in domain."""
        hsm_fixtures = [
            p for p in _fixture_paths("state-records")
            if _load_json(p).get("memory_domain") == "hsm"
        ]
        self.assertGreater(len(hsm_fixtures), 0,
                           "Expected at least one fixture with memory_domain='hsm'")
        for path in hsm_fixtures:
            with self.subTest(fixture=path.name):
                data = _load_json(path)
                # The claim or summary must use language showing hsm is configured, not built-in
                text = (data.get("claim", "") + " " + data.get("summary", "")).lower()
                self.assertTrue(
                    "configured" in text or "config" in text,
                    f"{path.name}: HSM fixture must use 'configured' language in claim/summary"
                )
                # Must not say HSM is built-in or hard-coded
                self.assertNotIn("built-in", text,
                                 f"{path.name}: HSM fixture must not say 'built-in'")
                self.assertNotIn("hard-coded", text,
                                 f"{path.name}: HSM fixture must not say 'hard-coded'")
                # metadata should have the hsm_domain_note
                meta = data.get("metadata") or {}
                self.assertIn("hsm_domain_note", meta,
                              f"{path.name}: HSM fixture metadata must have hsm_domain_note")

    def test_state_record_visibility_defaults_not_model_visible(self):
        """At least one StateRecord fixture must use visibility='internal' as the common default."""
        records = [_load_json(p) for p in _fixture_paths("state-records")]
        internal_count = sum(1 for r in records if r.get("visibility") == "internal")
        self.assertGreater(internal_count, 0,
                           "Expected most StateRecord fixtures to use visibility='internal'")
        # Majority should be internal
        self.assertGreater(
            internal_count, len(records) // 2,
            "Most StateRecord fixtures should default to visibility='internal'"
        )

    def test_fixture_coverage_minimum_tiers(self):
        """Fixtures should cover at least the mandatory tier set from the task spec."""
        mandatory_tiers = {
            "project_state",
            "procedural_memory",
            "artifact_memory",
            "preference_constraint_memory",
            "episodic_memory",
            "working_state",
        }
        records = [_load_json(p) for p in _fixture_paths("state-records")]
        covered = {r["tier"] for r in records}
        missing = mandatory_tiers - covered
        self.assertEqual(missing, set(),
                         f"Missing fixture coverage for tiers: {missing}")

    def test_fixture_coverage_minimum_record_types(self):
        """Fixtures should cover at least the mandatory record type set from the task spec."""
        mandatory_types = {
            "constraint",
            "procedure",
            "artifact_reference",
            "episode",
            "recent_topic",
            "identity_note",
        }
        records = [_load_json(p) for p in _fixture_paths("state-records")]
        covered = {r["record_type"] for r in records}
        missing = mandatory_types - covered
        self.assertEqual(missing, set(),
                         f"Missing fixture coverage for record_types: {missing}")

    def test_no_sql_first_language_in_schemas(self):
        """Schemas must not imply SQL-first design."""
        for path in _schema_paths():
            with self.subTest(schema=path.name):
                raw = path.read_text().lower()
                self.assertNotIn("create table", raw,
                                 f"{path.name} must not contain SQL DDL")
                self.assertNotIn("insert into", raw,
                                 f"{path.name} must not contain SQL DML")


if __name__ == "__main__":
    unittest.main()
