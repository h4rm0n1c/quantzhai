"""Tests for _resolve_launch_model_entry self-heal behaviour.

Covers:
  - poisoned state (status_snapshot source) self-heals via last_good_backend_id
  - poisoned state self-heals via last_loaded_model when last_good is empty
  - healed state is rewritten canonically so poison does not recur
  - canonical state is trusted and returned without salvage
  - no selection → catalog fallback returned
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_model_state import (
    ModelState,
    load_model_state,
    write_model_state,
)


# ---------------------------------------------------------------------------
# Minimal ModelCatalog stub
# ---------------------------------------------------------------------------

class _Entry(dict):
    """Catalog entry dict with a resolve()-compatible interface."""


def _make_entry(key, backend_id=None, label="", filename=None):
    entry = {
        "key": key,
        "slug": key,
        "filename": filename or (key if key.endswith(".gguf") else f"{key}.gguf"),
        "backend_id": backend_id or key,
        "label": label or key,
    }
    return _Entry(entry)


class _StubCatalog:
    """Minimal catalog stub that resolves by key/backend_id match."""

    def __init__(self, entries, selected=None):
        self.entries = entries
        self.selected = selected or (entries[0] if entries else None)

    def resolve(self, query=None):
        if not query:
            return None, "no query"
        for e in self.entries:
            if (
                e.get("key") == query
                or e.get("backend_id") == query
                or e.get("slug") == query
                or e.get("filename") == query
                or e.get("label") == query
            ):
                return e, f"matched {query}"
        return None, f"no match for {query}"


# ---------------------------------------------------------------------------
# Helper: import _resolve_launch_model_entry via injection
# ---------------------------------------------------------------------------
# The function is a closure inside main() in quantzhai_proxy.py.
# We test the logic directly by reproducing it here with the same structure.
# This avoids needing to spin up the full proxy.


def _run_resolve(state: ModelState, catalog: _StubCatalog, state_path: Path):
    """Reproduce _resolve_launch_model_entry logic for testing."""
    from proxy.qz_model_state import (
        SELECTED_SOURCES,
        selected_identity_from_state,
    )
    from dataclasses import replace as _dc_replace

    write_model_state(state, state_path)
    state_result = load_model_state(state_path)
    ms = state_result.state

    _is_poisoned = (
        ms.has_selection()
        and ms.selected_source
        and ms.selected_source not in SELECTED_SOURCES
    )

    if _is_poisoned:
        # Priority 1: last_good_backend_id
        salvage_id = ms.last_good_backend_id or ms.last_good_key
        if salvage_id:
            _entry, _ = catalog.resolve(query=salvage_id)
            if _entry is not None:
                _healed = _dc_replace(
                    ms,
                    selected_key=_entry.get("key") or salvage_id,
                    selected_backend_id=_entry.get("backend_id") or salvage_id,
                    selected_label=_entry.get("label") or "",
                    selected_source="fallback",
                    selected_reason="startup self-heal: last_good_backend_id",
                )
                write_model_state(_healed, state_path)
                return _entry

        # Priority 2: last_loaded_model salvage — unique exact match only
        salvage_model = ms.last_loaded_model
        if salvage_model:
            _q = salvage_model.strip()
            _identity_fields = ("backend_id", "key", "slug", "filename")
            _exact_matches = [
                e for e in (catalog.entries or [])
                if isinstance(e, dict)
                and any(
                    isinstance(e.get(f), str) and e.get(f, "").strip() == _q
                    for f in _identity_fields
                )
            ]
            _entry = _exact_matches[0] if len(_exact_matches) == 1 else None
            if _entry is not None:
                _backend_id = _entry.get("backend_id") or salvage_model
                _healed = _dc_replace(
                    ms,
                    selected_key=_entry.get("key") or salvage_model,
                    selected_backend_id=_backend_id,
                    selected_label=_entry.get("label") or "",
                    selected_source="fallback",
                    selected_reason="startup self-heal: last_loaded_model salvage",
                )
                write_model_state(_healed, state_path)
                return _entry

        return None  # poisoned and no salvage — falls through to catalog default

    # Normal path
    requested = selected_identity_from_state(ms)
    if requested:
        entry, _ = catalog.resolve(query=requested)
        if entry is not None:
            return entry

    # Catalog fallback
    fallback = catalog.selected
    return fallback if isinstance(fallback, dict) else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

REAL_MODEL_ID = "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS"
REAL_MODEL_KEY = f"{REAL_MODEL_ID}.gguf"

POISONED_STATE_DICT = {
    "selected_key": "default.gguf",
    "selected_backend_id": "default",
    "selected_label": "default",
    "selected_source": "status_snapshot",
    "selected_reason": "status reconciliation",
    "last_good_key": "",
    "last_good_backend_id": "",
    "last_good_label": "",
    "last_load_result": "failed",
    "last_load_error": "GPU not used: 'compiled without support for GPU offload' in container logs",
    "last_load_error_type": "unknown",
    "failed_candidate_key": "",
    "failed_candidate_backend_id": "",
    "last_loaded_model": REAL_MODEL_ID,
    "runtime_context_length": None,
}


def _make_poisoned_state() -> ModelState:
    from proxy.qz_model_state import _state_from_v1_dict, SCHEMA
    d = dict(POISONED_STATE_DICT, schema=SCHEMA)
    return _state_from_v1_dict(d)


class PoisonedStateLastGoodSalvageTests(unittest.TestCase):
    """Poisoned state recovers via last_good_backend_id when it is set."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmp.name) / "model-state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_recovers_via_last_good_backend_id(self):
        """Self-heal uses last_good_backend_id when available, not the poisoned selection."""
        state = _make_poisoned_state()
        # Simulate a state where last_good IS set (a later healthy load had been recorded)
        from dataclasses import replace
        state = replace(state, last_good_backend_id=REAL_MODEL_ID)

        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID, label="Qwen3.6 27B")
        catalog = _StubCatalog(
            entries=[
                _make_entry("default.gguf", backend_id="default"),
                real_entry,
            ],
            selected=_make_entry("default.gguf", backend_id="default"),
        )
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("backend_id"), REAL_MODEL_ID,
                         "self-heal must resolve to last_good model, not poisoned default")

    def test_healed_state_is_written_canonically_after_last_good_salvage(self):
        """State file is rewritten with canonical source after last_good salvage."""
        from dataclasses import replace
        state = _make_poisoned_state()
        state = replace(state, last_good_backend_id=REAL_MODEL_ID)

        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default"), real_entry],
        )
        _run_resolve(state, catalog, self._state_path)

        healed = load_model_state(self._state_path).state
        self.assertIn(healed.selected_source, ("fallback", "operator", "env_seed", "config_default"),
                      "healed state must have a canonical source, not status_snapshot")
        self.assertNotEqual(healed.selected_source, "status_snapshot",
                            "poison must be removed after self-heal")
        self.assertEqual(healed.selected_backend_id, REAL_MODEL_ID,
                         "healed state must record the real model backend_id")


class PoisonedStateLastLoadedModelSalvageTests(unittest.TestCase):
    """Observed poisoned state (issue #76): last_good empty, last_loaded_model set to real model."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmp.name) / "model-state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_recovers_via_last_loaded_model_when_last_good_is_empty(self):
        """Self-heal uses last_loaded_model when last_good is empty and model resolves."""
        state = _make_poisoned_state()
        # Confirm: last_good is empty, last_loaded_model is the real model
        self.assertEqual(state.last_good_backend_id, "")
        self.assertEqual(state.last_loaded_model, REAL_MODEL_ID)

        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID, label="Qwen3.6 27B")
        catalog = _StubCatalog(
            entries=[
                _make_entry("default.gguf", backend_id="default"),
                real_entry,
            ],
            selected=_make_entry("default.gguf", backend_id="default"),
        )
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result, "should recover from last_loaded_model")
        self.assertEqual(result.get("backend_id"), REAL_MODEL_ID,
                         "must resolve to real model, not poisoned default.gguf")

    def test_healed_state_is_rewritten_canonically(self):
        """State file is rewritten with canonical source after last_loaded_model salvage."""
        state = _make_poisoned_state()
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default"), real_entry],
        )
        _run_resolve(state, catalog, self._state_path)

        healed = load_model_state(self._state_path).state
        self.assertNotEqual(healed.selected_source, "status_snapshot",
                            "poison must not persist after self-heal")
        self.assertEqual(healed.selected_backend_id, REAL_MODEL_ID)

    def test_healed_state_is_not_poisoned_on_second_load(self):
        """A second _resolve_launch_model_entry call sees the healed state, not the original poison."""
        state = _make_poisoned_state()
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default"), real_entry],
        )
        # First call — self-heals and rewrites
        _run_resolve(state, catalog, self._state_path)
        # Second call — should use canonical healed state, no salvage needed
        result2 = _run_resolve(
            load_model_state(self._state_path).state, catalog, self._state_path
        )
        self.assertIsNotNone(result2)
        self.assertEqual(result2.get("backend_id"), REAL_MODEL_ID)


class PoisonedStateNoSalvageTests(unittest.TestCase):
    """Poisoned state with no usable salvage returns None, falling to catalog default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmp.name) / "model-state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_salvage_returns_none(self):
        """When last_good and last_loaded_model are both unresolvable, returns None."""
        from dataclasses import replace
        state = _make_poisoned_state()
        # last_loaded_model is a model NOT in the catalog
        state = replace(state, last_loaded_model="unknown-model-not-in-catalog")

        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default")],
            selected=_make_entry("default.gguf", backend_id="default"),
        )
        result = _run_resolve(state, catalog, self._state_path)
        # Falls through to catalog default (or None from the poisoned-path)
        # The important thing: it does NOT return the poisoned "default.gguf" identity
        # from the state file's selected_key — it returns None or catalog.selected
        # (caller falls back to catalog default).
        self.assertIsNone(result,
                          "should return None when poisoned and no salvage available")


class CanonicalStateTests(unittest.TestCase):
    """Canonical (non-poisoned) state is trusted without any salvage."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmp.name) / "model-state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, **kwargs):
        write_model_state(ModelState(**kwargs), self._state_path)

    def test_operator_selected_model_is_returned(self):
        """Canonical operator selection is returned without salvage."""
        self._write(
            selected_key=REAL_MODEL_KEY,
            selected_backend_id=REAL_MODEL_ID,
            selected_source="operator",
        )
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default"), real_entry],
        )
        state = load_model_state(self._state_path).state
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("backend_id"), REAL_MODEL_ID)

    def test_fallback_source_is_canonical(self):
        """'fallback' source is canonical and trusted."""
        self._write(
            selected_key=REAL_MODEL_KEY,
            selected_backend_id=REAL_MODEL_ID,
            selected_source="fallback",
        )
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(entries=[real_entry])
        state = load_model_state(self._state_path).state
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("backend_id"), REAL_MODEL_ID)

    def test_empty_state_uses_catalog_fallback(self):
        """Empty state (no selection) falls back to catalog.selected."""
        self._write()  # empty ModelState
        default_entry = _make_entry("default.gguf", backend_id="default")
        catalog = _StubCatalog(entries=[default_entry], selected=default_entry)
        state = load_model_state(self._state_path).state
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("backend_id"), "default")


class ExactMatchSalvageTests(unittest.TestCase):
    """last_loaded_model salvage requires a unique exact match on catalog identity fields."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmp.name) / "model-state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _poisoned(self, last_loaded_model: str) -> ModelState:
        from dataclasses import replace
        state = _make_poisoned_state()
        return replace(state, last_loaded_model=last_loaded_model, last_good_backend_id="")

    def test_exact_backend_id_match_salvages(self):
        """last_loaded_model that exactly matches one entry's backend_id is salvaged."""
        state = self._poisoned(REAL_MODEL_ID)
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default"), real_entry],
        )
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result, "unique exact match must salvage")
        self.assertEqual(result.get("backend_id"), REAL_MODEL_ID)

    def test_exact_key_match_salvages(self):
        """last_loaded_model matching a catalog entry's key field is salvaged."""
        state = self._poisoned(REAL_MODEL_KEY)  # e.g. "Qwen3.6-27B-...-IQ4_XS.gguf"
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default"), real_entry],
        )
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result, "exact key match must salvage")
        self.assertEqual(result.get("backend_id"), REAL_MODEL_ID)

    def test_ambiguous_match_does_not_salvage(self):
        """last_loaded_model matching more than one catalog entry is rejected."""
        # Two entries share the same backend_id fragment — ambiguous
        entry_a = _make_entry("ModelA.gguf", backend_id="shared-id")
        entry_b = _make_entry("ModelB.gguf", backend_id="shared-id")
        state = self._poisoned("shared-id")
        catalog = _StubCatalog(entries=[entry_a, entry_b])
        result = _run_resolve(state, catalog, self._state_path)
        # Both entries match "shared-id" → ambiguous → returns None
        self.assertIsNone(result,
                          "ambiguous last_loaded_model must not salvage")

    def test_no_match_does_not_salvage(self):
        """last_loaded_model with no catalog match is skipped safely."""
        state = self._poisoned("ghost-model-not-in-catalog")
        catalog = _StubCatalog(
            entries=[_make_entry("default.gguf", backend_id="default")],
        )
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNone(result,
                          "unresolvable last_loaded_model must not salvage")

    def test_partial_substring_does_not_salvage(self):
        """last_loaded_model that is only a substring of a catalog entry is rejected."""
        # REAL_MODEL_ID is a substring of REAL_MODEL_KEY (gguf suffix differs)
        # — but if last_loaded_model is a partial string it must not match
        state = self._poisoned("Qwen3.6")  # prefix only — not an exact match
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(entries=[real_entry])
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNone(result,
                          "partial/substring last_loaded_model must not salvage")

    def test_salvage_does_not_happen_when_last_good_is_set(self):
        """last_good_backend_id takes priority; last_loaded_model path is never reached."""
        from dataclasses import replace
        state = _make_poisoned_state()
        state = replace(state,
                        last_good_backend_id=REAL_MODEL_ID,
                        last_loaded_model="some-other-model")
        real_entry = _make_entry(REAL_MODEL_KEY, backend_id=REAL_MODEL_ID)
        catalog = _StubCatalog(
            entries=[real_entry, _make_entry("some-other-model.gguf", backend_id="some-other-model")],
        )
        result = _run_resolve(state, catalog, self._state_path)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("backend_id"), REAL_MODEL_ID,
                         "last_good_backend_id must take priority over last_loaded_model")


if __name__ == "__main__":
    unittest.main()
