"""Tests for proxy/qz_model_state.py — Slice B (qz.model_state.v1)."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proxy.qz_model_state import (
    SCHEMA,
    SELECTED_SOURCES,
    ModelState,
    ModelStateResult,
    load_model_state,
    mark_load_failure,
    mark_load_success,
    selected_identity_from_state,
    state_from_selection,
    update_load_observation,
    write_model_state,
)


# ---------------------------------------------------------------------------
# load_model_state
# ---------------------------------------------------------------------------

class LoadModelStateTests(unittest.TestCase):

    def _tmp_path(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory()

    def test_missing_file_returns_empty_state_no_warning(self):
        with self._tmp_path() as tmp:
            result = load_model_state(Path(tmp) / "model-state.json")
        self.assertIsInstance(result, ModelStateResult)
        self.assertFalse(result.state.has_selection())
        self.assertEqual(result.warnings, [])
        self.assertFalse(result.migrated)

    def test_corrupt_json_returns_empty_state_with_warning(self):
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            path.write_text("{not json", encoding="utf-8")
            result = load_model_state(path)
        self.assertFalse(result.state.has_selection())
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("invalid JSON", result.warnings[0])

    def test_non_object_json_returns_empty_state_with_warning(self):
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            result = load_model_state(path)
        self.assertFalse(result.state.has_selection())
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("not a JSON object", result.warnings[0])

    def test_v1_file_round_trips(self):
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            payload = {
                "schema": SCHEMA,
                "selected_key": "kuato.gguf",
                "selected_backend_id": "kuato",
                "selected_label": "Kuato",
                "selected_source": "operator",
                "selected_at": "2026-05-21T10:00:00Z",
                "selected_reason": "POST /qz/model/select",
                "runtime_context_length": 262144,
                "last_load_result": "loaded",
                "last_load_error": None,
                "last_load_error_type": None,
                "last_loaded_model": "kuato",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = load_model_state(path)
        self.assertFalse(result.migrated)
        self.assertEqual(result.state.schema, SCHEMA)
        self.assertEqual(result.state.selected_key, "kuato.gguf")
        self.assertEqual(result.state.selected_backend_id, "kuato")
        self.assertEqual(result.state.selected_source, "operator")
        self.assertEqual(result.state.runtime_context_length, 262144)
        self.assertEqual(result.state.last_loaded_model, "kuato")

    # --- Migration ---

    def test_pre_v1_with_selected_key_migrates(self):
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            path.write_text(json.dumps({
                "selected_key": "kuato.gguf",
                "selected_backend_id": "kuato",
                "selected_label": "Kuato",
                "selected_path": "/models/kuato.gguf",
                "selected_reason": "previous select",
                "source": "resolve_model_selection",
                "updated_at": 1700000000.0,
            }), encoding="utf-8")
            result = load_model_state(path)
        self.assertTrue(result.migrated)
        self.assertEqual(result.state.schema, SCHEMA)
        self.assertEqual(result.state.selected_key, "kuato.gguf")
        self.assertEqual(result.state.selected_backend_id, "kuato")
        self.assertEqual(result.state.selected_source, "migration")
        self.assertRegex(
            result.state.selected_at,
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("migrated", result.warnings[0])

    def test_pre_v1_with_only_selected_backend_id_migrates(self):
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            path.write_text(json.dumps({
                "selected_backend_id": "kuato",
            }), encoding="utf-8")
            result = load_model_state(path)
        self.assertTrue(result.migrated)
        self.assertEqual(result.state.selected_backend_id, "kuato")
        self.assertEqual(result.state.selected_source, "migration")

    def test_pre_v1_with_only_loaded_model_is_not_selected(self):
        """Hard rule: loaded_model alone is observation, never authority."""
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            path.write_text(json.dumps({
                "loaded_model": "stale-kuato",
                "updated_at": 1700000000.0,
            }), encoding="utf-8")
            result = load_model_state(path)
        self.assertFalse(result.migrated)
        self.assertFalse(result.state.has_selection())
        self.assertEqual(result.state.selected_key, "")
        self.assertEqual(result.state.selected_backend_id, "")
        # Warning must mention loaded_model dropped as authority
        self.assertTrue(any("loaded_model" in w for w in result.warnings))

    def test_pre_v1_empty_object_returns_empty_no_warning(self):
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            path.write_text("{}", encoding="utf-8")
            result = load_model_state(path)
        self.assertFalse(result.migrated)
        self.assertFalse(result.state.has_selection())
        self.assertEqual(result.warnings, [])

    def test_pre_v1_loaded_model_carries_to_last_loaded_model(self):
        """Migration moves loaded_model to last_loaded_model (observation)."""
        with self._tmp_path() as tmp:
            path = Path(tmp) / "model-state.json"
            path.write_text(json.dumps({
                "selected_key": "kuato.gguf",
                "loaded_model": "kuato",
            }), encoding="utf-8")
            result = load_model_state(path)
        self.assertTrue(result.migrated)
        self.assertEqual(result.state.last_loaded_model, "kuato")
        self.assertEqual(result.state.selected_key, "kuato.gguf")


# ---------------------------------------------------------------------------
# selected_identity_from_state
# ---------------------------------------------------------------------------

class SelectedIdentityFromStateTests(unittest.TestCase):

    def test_prefers_selected_key(self):
        state = ModelState(selected_key="kuato.gguf", selected_backend_id="kuato")
        self.assertEqual(selected_identity_from_state(state), "kuato.gguf")

    def test_falls_back_to_selected_backend_id(self):
        state = ModelState(selected_key="", selected_backend_id="kuato")
        self.assertEqual(selected_identity_from_state(state), "kuato")

    def test_empty_state_returns_empty(self):
        self.assertEqual(selected_identity_from_state(ModelState()), "")

    def test_last_loaded_model_does_not_count(self):
        """Observation fields are not selection identity."""
        state = ModelState(last_loaded_model="stale-kuato")
        self.assertEqual(selected_identity_from_state(state), "")


# ---------------------------------------------------------------------------
# write_model_state — atomicity and schema
# ---------------------------------------------------------------------------

class WriteModelStateTests(unittest.TestCase):

    def test_write_includes_schema_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-state.json"
            write_model_state(ModelState(selected_key="kuato.gguf"), path)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], SCHEMA)
        self.assertEqual(payload["selected_key"], "kuato.gguf")

    def test_write_forces_schema_even_if_state_field_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-state.json"
            state = ModelState(schema="qz.model_state.v0", selected_key="kuato.gguf")
            write_model_state(state, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], SCHEMA)

    def test_write_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deep" / "model-state.json"
            write_model_state(ModelState(selected_key="kuato.gguf"), path)
            self.assertTrue(path.is_file())

    def test_write_is_atomic_via_temp_replace(self):
        """No partial-file window: temp file is created then renamed.

        We can't easily simulate a crash mid-write, but we can verify that
        the temp file does not exist after a successful write and that the
        final file is complete JSON.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-state.json"
            write_model_state(ModelState(selected_key="kuato.gguf"), path)
            # No leftover *.tmp files in the directory
            leftovers = [p for p in Path(tmp).iterdir() if p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])
            # Final file is parseable
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")

    def test_write_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-state.json"
            original = ModelState(
                selected_key="kuato.gguf",
                selected_backend_id="kuato",
                selected_label="Kuato",
                selected_source="operator",
                selected_at="2026-05-21T10:00:00Z",
                selected_reason="POST /qz/model/select",
                runtime_context_length=262144,
                last_load_result="loaded",
                last_load_error=None,
                last_load_error_type=None,
                last_loaded_model="kuato",
            )
            write_model_state(original, path)
            result = load_model_state(path)
        self.assertEqual(result.state, original)
        self.assertFalse(result.migrated)


# ---------------------------------------------------------------------------
# state_from_selection
# ---------------------------------------------------------------------------

class StateFromSelectionTests(unittest.TestCase):

    ENTRY = {
        "slug": "kuato.gguf",
        "key": "kuato.gguf",
        "backend_id": "kuato",
        "label": "Kuato Q4",
    }

    def test_records_provenance_and_timestamp(self):
        state = state_from_selection(self.ENTRY, source="operator", reason="select")
        self.assertEqual(state.selected_source, "operator")
        self.assertEqual(state.selected_reason, "select")
        self.assertRegex(
            state.selected_at,
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )

    def test_picks_slug_as_key(self):
        state = state_from_selection(self.ENTRY, source="operator", reason="")
        self.assertEqual(state.selected_key, "kuato.gguf")
        self.assertEqual(state.selected_backend_id, "kuato")
        self.assertEqual(state.selected_label, "Kuato Q4")

    def test_backend_id_falls_back_to_key(self):
        entry = {"slug": "kuato.gguf"}
        state = state_from_selection(entry, source="operator", reason="")
        self.assertEqual(state.selected_backend_id, "kuato.gguf")

    def test_runtime_context_length_recorded(self):
        state = state_from_selection(
            self.ENTRY, source="operator", reason="", runtime_context_length=131072
        )
        self.assertEqual(state.runtime_context_length, 131072)

    def test_observation_fields_reset(self):
        state = state_from_selection(self.ENTRY, source="operator", reason="")
        self.assertEqual(state.last_load_result, "")
        self.assertIsNone(state.last_load_error)
        self.assertIsNone(state.last_load_error_type)
        self.assertEqual(state.last_loaded_model, "")

    def test_invalid_source_raises(self):
        with self.assertRaises(ValueError):
            state_from_selection(self.ENTRY, source="bogus", reason="")

    def test_each_canonical_source_accepted(self):
        for source in SELECTED_SOURCES:
            state = state_from_selection(self.ENTRY, source=source, reason="")
            self.assertEqual(state.selected_source, source)


# ---------------------------------------------------------------------------
# update_load_observation
# ---------------------------------------------------------------------------

class UpdateLoadObservationTests(unittest.TestCase):

    BASE = ModelState(
        selected_key="kuato.gguf",
        selected_backend_id="kuato",
        selected_label="Kuato",
        selected_source="operator",
        selected_at="2026-05-21T10:00:00Z",
        selected_reason="POST /qz/model/select",
        runtime_context_length=262144,
    )

    def test_records_loaded_model(self):
        new_state = update_load_observation(self.BASE, loaded_model="kuato")
        self.assertEqual(new_state.last_loaded_model, "kuato")

    def test_records_result_error_and_type(self):
        new_state = update_load_observation(
            self.BASE,
            result="failed",
            error="cudaMalloc failed",
            error_type="insufficient_vram",
        )
        self.assertEqual(new_state.last_load_result, "failed")
        self.assertEqual(new_state.last_load_error, "cudaMalloc failed")
        self.assertEqual(new_state.last_load_error_type, "insufficient_vram")

    def test_does_not_change_selection_authority(self):
        new_state = update_load_observation(
            self.BASE,
            loaded_model="totally-different",
            result="failed",
            error="boom",
            error_type="insufficient_vram",
        )
        self.assertEqual(new_state.selected_key, self.BASE.selected_key)
        self.assertEqual(new_state.selected_backend_id, self.BASE.selected_backend_id)
        self.assertEqual(new_state.selected_source, self.BASE.selected_source)
        self.assertEqual(new_state.selected_at, self.BASE.selected_at)
        self.assertEqual(new_state.selected_reason, self.BASE.selected_reason)
        self.assertEqual(new_state.runtime_context_length, self.BASE.runtime_context_length)

    def test_returns_new_state_does_not_mutate_input(self):
        before = ModelState(selected_key="kuato.gguf", last_loaded_model="old")
        after = update_load_observation(before, loaded_model="new")
        self.assertEqual(before.last_loaded_model, "old")
        self.assertEqual(after.last_loaded_model, "new")

    def test_none_keeps_existing_fields(self):
        base = ModelState(
            selected_key="kuato.gguf",
            last_load_result="loaded",
            last_loaded_model="kuato",
        )
        after = update_load_observation(base, error="some new note")
        self.assertEqual(after.last_load_result, "loaded")
        self.assertEqual(after.last_loaded_model, "kuato")
        self.assertEqual(after.last_load_error, "some new note")


# ---------------------------------------------------------------------------
# Regression: load_last_selected_model no longer reads loaded_model
# ---------------------------------------------------------------------------

class CatalogReaderRegressionTests(unittest.TestCase):
    """Confirms qz_model_catalog.load_last_selected_model() uses qz.model_state.v1."""

    def test_loaded_model_only_returns_empty_identity(self):
        from proxy.qz_model_catalog import load_last_selected_model

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "var").mkdir(parents=True)
            state_path = root / "var" / "model-state.json"
            state_path.write_text(json.dumps({"loaded_model": "stale-kuato"}), encoding="utf-8")
            with patch.dict(os.environ, {"QZ_MODEL_STATE_PATH": str(state_path)}, clear=False):
                identity = load_last_selected_model(root)
        self.assertEqual(identity, "")

    def test_selected_key_returned(self):
        from proxy.qz_model_catalog import load_last_selected_model

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "var").mkdir(parents=True)
            state_path = root / "var" / "model-state.json"
            state_path.write_text(json.dumps({"selected_key": "kuato.gguf"}), encoding="utf-8")
            with patch.dict(os.environ, {"QZ_MODEL_STATE_PATH": str(state_path)}, clear=False):
                identity = load_last_selected_model(root)
        self.assertEqual(identity, "kuato.gguf")

    def test_selected_backend_id_returned_when_no_key(self):
        from proxy.qz_model_catalog import load_last_selected_model

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "var").mkdir(parents=True)
            state_path = root / "var" / "model-state.json"
            state_path.write_text(json.dumps({"selected_backend_id": "kuato"}), encoding="utf-8")
            with patch.dict(os.environ, {"QZ_MODEL_STATE_PATH": str(state_path)}, clear=False):
                identity = load_last_selected_model(root)
        self.assertEqual(identity, "kuato")


# ---------------------------------------------------------------------------
# Post-Slice F: failed-model recovery helpers
# ---------------------------------------------------------------------------

class MarkLoadSuccessTests(unittest.TestCase):

    def test_clears_previous_error_and_records_last_good(self):
        before = ModelState(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            selected_label="Kuato",
            selected_source="operator",
            last_load_result="failed",
            last_load_error="cudaMalloc",
            last_load_error_type="insufficient_vram",
            failed_candidate_key="old-too-large",
        )
        after = mark_load_success(before, loaded_model="kuato")
        self.assertEqual(after.last_load_result, "loaded")
        self.assertIsNone(after.last_load_error)
        self.assertIsNone(after.last_load_error_type)
        self.assertEqual(after.last_loaded_model, "kuato")
        self.assertEqual(after.last_good_key, "kuato.gguf")
        self.assertEqual(after.last_good_backend_id, "kuato")
        self.assertEqual(after.last_good_label, "Kuato")
        self.assertEqual(after.last_good_source, "operator")
        self.assertRegex(after.last_good_loaded_at, r"^\d{4}-")
        # failed_candidate_* cleared
        self.assertEqual(after.failed_candidate_key, "")
        self.assertEqual(after.failed_candidate_backend_id, "")

    def test_no_selection_does_not_invent_last_good(self):
        before = ModelState()
        after = mark_load_success(before, loaded_model="x")
        self.assertEqual(after.last_good_key, "")
        self.assertEqual(after.last_good_backend_id, "")

    def test_does_not_mutate_input(self):
        before = ModelState(selected_key="a", selected_backend_id="a", last_load_result="failed")
        _ = mark_load_success(before, loaded_model="a")
        self.assertEqual(before.last_load_result, "failed")
        self.assertEqual(before.last_good_key, "")


class MarkLoadFailureTests(unittest.TestCase):

    def test_records_failed_candidate(self):
        before = ModelState(
            selected_key="too-large.gguf",
            selected_backend_id="too-large",
            selected_label="Too Large",
        )
        after, rollback = mark_load_failure(
            before,
            error="cudaMalloc failed: out of memory",
            error_type="insufficient_vram",
            rollback_to_last_good=False,
        )
        self.assertFalse(rollback)
        self.assertEqual(after.failed_candidate_key, "too-large.gguf")
        self.assertEqual(after.failed_candidate_backend_id, "too-large")
        self.assertEqual(after.failed_candidate_label, "Too Large")
        self.assertRegex(after.failed_candidate_at, r"^\d{4}-")
        self.assertEqual(after.last_load_result, "failed")
        self.assertEqual(after.last_load_error, "cudaMalloc failed: out of memory")
        self.assertEqual(after.last_load_error_type, "insufficient_vram")
        # No rollback → selected_* unchanged
        self.assertEqual(after.selected_key, "too-large.gguf")
        self.assertEqual(after.selected_backend_id, "too-large")

    def test_rollback_to_last_good_when_available(self):
        before = ModelState(
            selected_key="too-large.gguf",
            selected_backend_id="too-large",
            selected_label="Too Large",
            selected_source="operator",
            last_good_key="kuato.gguf",
            last_good_backend_id="kuato",
            last_good_label="Kuato",
            last_good_source="operator",
        )
        after, rollback = mark_load_failure(
            before,
            error="cudaMalloc failed",
            error_type="insufficient_vram",
            rollback_to_last_good=True,
        )
        self.assertTrue(rollback)
        self.assertEqual(after.selected_key, "kuato.gguf")
        self.assertEqual(after.selected_backend_id, "kuato")
        self.assertEqual(after.selected_label, "Kuato")
        self.assertEqual(after.selected_source, "operator")
        self.assertIn("rolled back from failed candidate too-large.gguf", after.selected_reason)
        # failed_candidate captures the model that failed
        self.assertEqual(after.failed_candidate_key, "too-large.gguf")
        # last_good_* preserved
        self.assertEqual(after.last_good_key, "kuato.gguf")

    def test_rollback_uses_fallback_source_when_last_good_source_unknown(self):
        before = ModelState(
            selected_key="too-large.gguf",
            selected_backend_id="too-large",
            last_good_key="kuato.gguf",
            last_good_backend_id="kuato",
            last_good_source="",  # unknown
        )
        after, rollback = mark_load_failure(
            before,
            error="boom",
            error_type="unknown",
            rollback_to_last_good=True,
        )
        self.assertTrue(rollback)
        self.assertEqual(after.selected_source, "fallback")
        self.assertIn(after.selected_source, SELECTED_SOURCES)

    def test_no_last_good_means_no_rollback_even_when_requested(self):
        before = ModelState(
            selected_key="too-large.gguf",
            selected_backend_id="too-large",
        )
        after, rollback = mark_load_failure(
            before,
            error="boom",
            error_type="unknown",
            rollback_to_last_good=True,
        )
        self.assertFalse(rollback)
        self.assertEqual(after.selected_key, "too-large.gguf")
        self.assertEqual(after.failed_candidate_key, "too-large.gguf")

    def test_observation_only_when_rollback_disabled(self):
        before = ModelState(
            selected_key="too-large.gguf",
            selected_backend_id="too-large",
            last_good_key="kuato.gguf",
            last_good_backend_id="kuato",
        )
        after, rollback = mark_load_failure(
            before,
            error="boom",
            error_type="unknown",
            rollback_to_last_good=False,
        )
        self.assertFalse(rollback)
        self.assertEqual(after.selected_key, "too-large.gguf")  # not rolled back

    def test_does_not_mutate_input(self):
        before = ModelState(selected_key="too-large.gguf", selected_backend_id="tl")
        _ = mark_load_failure(before, error="x", error_type="unknown")
        self.assertEqual(before.failed_candidate_key, "")
        self.assertEqual(before.last_load_result, "")


class ModelStateV1RecoveryRoundTripTests(unittest.TestCase):

    def test_recovery_fields_round_trip(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-state.json"
            original = ModelState(
                selected_key="kuato.gguf",
                selected_backend_id="kuato",
                selected_source="operator",
                last_good_key="kuato.gguf",
                last_good_backend_id="kuato",
                last_good_label="Kuato",
                last_good_source="operator",
                last_good_loaded_at="2026-05-22T01:00:00Z",
                failed_candidate_key="too-large.gguf",
                failed_candidate_backend_id="too-large",
                failed_candidate_at="2026-05-22T02:00:00Z",
            )
            write_model_state(original, path)
            result = load_model_state(path)
        self.assertEqual(result.state, original)


if __name__ == "__main__":
    unittest.main()
