"""qz.model_state.v1 — proxy-owned model selection state.

Single source of truth for which model the operator wants loaded.  Distinct
from observation fields like ``last_loaded_model``.

See ``docs/proxy-model-selection-authority.md`` for the design rationale.

Authority fields (selection):
  - selected_key
  - selected_backend_id
  - selected_label
  - selected_source     (provenance — see ``SELECTED_SOURCES``)
  - selected_at         (ISO8601 UTC)
  - selected_reason     (free text)
  - runtime_context_length

Observation fields (never authoritative for selection):
  - last_load_result
  - last_load_error
  - last_load_error_type
  - last_loaded_model

Pre-v1 files are migrated when they carry an explicit selected_key or
selected_backend_id.  Files that have only a ``loaded_model`` field are
treated as having no selection — loaded_model has never been operator intent.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SCHEMA = "qz.model_state.v1"

# Canonical provenance values used by new code paths (Slice B introduces the
# vocabulary; Slice C rewires the router callers to use it strictly).
SELECTED_SOURCES = frozenset({
    "operator",
    "qz_codex",
    "env_seed",
    "config_default",
    "fallback",
    "migration",
})

LOAD_RESULTS = frozenset({"loaded", "failed", "unknown"})

LOAD_ERROR_TYPES = frozenset({
    "insufficient_vram",
    "context_creation_failed",
    "unknown",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelState:
    """In-memory model-state snapshot — safe to serialise as qz.model_state.v1.

    The dataclass itself does not validate ``selected_source`` against
    ``SELECTED_SOURCES`` so that legacy router callers writing historical
    source strings still round-trip cleanly.  Use ``state_from_selection``
    when you want strict validation.
    """

    schema: str = SCHEMA
    selected_key: str = ""
    selected_backend_id: str = ""
    selected_label: str = ""
    selected_source: str = ""
    selected_at: str = ""
    selected_reason: str = ""
    runtime_context_length: int | None = None
    last_load_result: str = ""
    last_load_error: str | None = None
    last_load_error_type: str | None = None
    last_loaded_model: str = ""
    # Failure-recovery fields (post-Slice F).  ``last_good_*`` records the
    # most recently confirmed-loaded selection.  ``failed_candidate_*``
    # records the most recent failed attempt.  Both are observation only;
    # selection authority remains ``selected_key`` / ``selected_backend_id``.
    last_good_key: str = ""
    last_good_backend_id: str = ""
    last_good_label: str = ""
    last_good_source: str = ""
    last_good_loaded_at: str = ""
    failed_candidate_key: str = ""
    failed_candidate_backend_id: str = ""
    failed_candidate_label: str = ""
    failed_candidate_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema":                       self.schema,
            "selected_key":                 self.selected_key,
            "selected_backend_id":          self.selected_backend_id,
            "selected_label":               self.selected_label,
            "selected_source":              self.selected_source,
            "selected_at":                  self.selected_at,
            "selected_reason":              self.selected_reason,
            "runtime_context_length":       self.runtime_context_length,
            "last_load_result":             self.last_load_result,
            "last_load_error":              self.last_load_error,
            "last_load_error_type":         self.last_load_error_type,
            "last_loaded_model":            self.last_loaded_model,
            "last_good_key":                self.last_good_key,
            "last_good_backend_id":         self.last_good_backend_id,
            "last_good_label":              self.last_good_label,
            "last_good_source":             self.last_good_source,
            "last_good_loaded_at":          self.last_good_loaded_at,
            "failed_candidate_key":         self.failed_candidate_key,
            "failed_candidate_backend_id":  self.failed_candidate_backend_id,
            "failed_candidate_label":       self.failed_candidate_label,
            "failed_candidate_at":          self.failed_candidate_at,
        }

    def has_last_good(self) -> bool:
        return bool(self.last_good_key.strip() or self.last_good_backend_id.strip())

    def has_selection(self) -> bool:
        return bool(self.selected_key.strip() or self.selected_backend_id.strip())


@dataclass
class ModelStateResult:
    """Return value from ``load_model_state``."""

    state: ModelState
    migrated: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_path() -> Path:
    env_path = os.environ.get("QZ_MODEL_STATE_PATH")
    if env_path:
        return Path(env_path).expanduser()
    var_dir = os.environ.get("QZ_VAR_DIR")
    if var_dir:
        return Path(var_dir).expanduser() / "model-state.json"
    return Path(__file__).resolve().parents[1] / "var" / "model-state.json"


def _iso_now() -> str:
    t = time.gmtime()
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"


def _str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    return str(value)


def _coerce_context(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_model_state(path: Path | None = None) -> ModelStateResult:
    """Read model state from ``path`` (defaults to QZ_MODEL_STATE_PATH).

    Returns an empty state with a warning on missing / corrupt / non-object
    files.  Pre-v1 files with selected_key or selected_backend_id are
    migrated.  Pre-v1 files with only ``loaded_model`` are treated as having
    no selection (loaded_model is observation, not authority).
    """
    path = Path(path).expanduser() if path else _default_path()
    if not path.is_file():
        return ModelStateResult(state=ModelState())

    try:
        raw_text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return ModelStateResult(
            state=ModelState(),
            warnings=[f"model-state.json unreadable: {exc}; starting empty"],
        )

    try:
        raw = json.loads(raw_text)
    except Exception as exc:
        return ModelStateResult(
            state=ModelState(),
            warnings=[f"model-state.json invalid JSON: {exc}; starting empty"],
        )

    if not isinstance(raw, dict):
        return ModelStateResult(
            state=ModelState(),
            warnings=["model-state.json was not a JSON object; starting empty"],
        )

    schema = _str(raw.get("schema"))
    if schema == SCHEMA:
        return ModelStateResult(state=_state_from_v1_dict(raw))

    # Pre-v1 file — migrate only when an explicit selection field exists.
    selected_key = _str(raw.get("selected_key"))
    selected_backend_id = _str(raw.get("selected_backend_id"))
    warnings: list[str] = []

    if not (selected_key or selected_backend_id):
        if _str(raw.get("loaded_model")):
            warnings.append(
                "pre-v1 model-state.json had only loaded_model; dropped as "
                "selection authority (observation only)"
            )
        return ModelStateResult(state=ModelState(), warnings=warnings)

    state = ModelState(
        schema=SCHEMA,
        selected_key=selected_key,
        selected_backend_id=selected_backend_id,
        selected_label=_str(raw.get("selected_label")),
        selected_source="migration",
        selected_at=_iso_now(),
        selected_reason=_str(raw.get("selected_reason")) or "migrated from pre-v1 state file",
        runtime_context_length=_coerce_context(
            raw.get("runtime_context_length")
            if raw.get("runtime_context_length") is not None
            else raw.get("selected_context_length")
        ),
        last_load_result="",
        last_load_error=None,
        last_load_error_type=None,
        last_loaded_model=_str(raw.get("last_loaded_model")) or _str(raw.get("loaded_model")),
    )
    warnings.append("model-state.json migrated to qz.model_state.v1")
    return ModelStateResult(state=state, migrated=True, warnings=warnings)


def _state_from_v1_dict(raw: dict[str, Any]) -> ModelState:
    return ModelState(
        schema=SCHEMA,
        selected_key=_str(raw.get("selected_key")),
        selected_backend_id=_str(raw.get("selected_backend_id")),
        selected_label=_str(raw.get("selected_label")),
        selected_source=_str(raw.get("selected_source")),
        selected_at=_str(raw.get("selected_at")),
        selected_reason=_str(raw.get("selected_reason")),
        runtime_context_length=_coerce_context(raw.get("runtime_context_length")),
        last_load_result=_str(raw.get("last_load_result")),
        last_load_error=_optional_str(raw.get("last_load_error")),
        last_load_error_type=_optional_str(raw.get("last_load_error_type")),
        last_loaded_model=_str(raw.get("last_loaded_model")),
        last_good_key=_str(raw.get("last_good_key")),
        last_good_backend_id=_str(raw.get("last_good_backend_id")),
        last_good_label=_str(raw.get("last_good_label")),
        last_good_source=_str(raw.get("last_good_source")),
        last_good_loaded_at=_str(raw.get("last_good_loaded_at")),
        failed_candidate_key=_str(raw.get("failed_candidate_key")),
        failed_candidate_backend_id=_str(raw.get("failed_candidate_backend_id")),
        failed_candidate_label=_str(raw.get("failed_candidate_label")),
        failed_candidate_at=_str(raw.get("failed_candidate_at")),
    )


def write_model_state(state: ModelState, path: Path | None = None) -> Path:
    """Atomically write ``state`` as qz.model_state.v1 JSON.

    Writes to a temp file in the same directory then ``os.replace``s into
    place so concurrent readers never observe a half-written file.
    """
    path = Path(path).expanduser() if path else _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(state.as_dict())
    payload["schema"] = SCHEMA  # force schema regardless of in-memory state

    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                # Some filesystems (e.g. tmpfs under certain conditions) reject
                # fsync; the temp file is still on disk and replace() is atomic
                # at the directory level.
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def selected_identity_from_state(state: ModelState) -> str:
    """Return ``selected_key`` if set, else ``selected_backend_id``, else ""."""
    if state.selected_key:
        return state.selected_key
    if state.selected_backend_id:
        return state.selected_backend_id
    return ""


def state_from_selection(
    entry: dict[str, Any],
    source: str,
    reason: str,
    runtime_context_length: int | None = None,
) -> ModelState:
    """Build a fresh ModelState from a catalog entry + provenance.

    Validates ``source`` against ``SELECTED_SOURCES``.  Observation fields
    are reset (this is a new selection, not an observation update).
    """
    if source not in SELECTED_SOURCES:
        raise ValueError(
            f"unknown selected_source {source!r}; "
            f"must be one of {sorted(SELECTED_SOURCES)}"
        )
    entry = entry if isinstance(entry, dict) else {}

    key = ""
    for field_name in ("slug", "key", "filename", "stem"):
        value = entry.get(field_name)
        if isinstance(value, str) and value.strip():
            key = value.strip()
            break

    backend_id = ""
    raw_backend = entry.get("backend_id")
    if isinstance(raw_backend, str) and raw_backend.strip():
        backend_id = raw_backend.strip()
    elif key:
        backend_id = key

    label = entry.get("label")
    label = label.strip() if isinstance(label, str) else ""

    return ModelState(
        schema=SCHEMA,
        selected_key=key,
        selected_backend_id=backend_id,
        selected_label=label,
        selected_source=source,
        selected_at=_iso_now(),
        selected_reason=str(reason or ""),
        runtime_context_length=runtime_context_length,
        last_load_result="",
        last_load_error=None,
        last_load_error_type=None,
        last_loaded_model="",
    )


def mark_load_success(state: ModelState, *, loaded_model: str = "") -> ModelState:
    """Record a confirmed successful load.

    Effects:
      * ``last_load_result`` → 'loaded'
      * ``last_load_error`` / ``last_load_error_type`` → None
      * ``last_loaded_model`` → ``loaded_model``
      * ``last_good_*`` updated from current ``selected_*`` (since the
        backend has now confirmed this selection works)
      * ``failed_candidate_*`` cleared (any prior failure is recovered)
    """
    new_state = replace(state)
    new_state.last_load_result = "loaded"
    new_state.last_load_error = None
    new_state.last_load_error_type = None
    new_state.last_loaded_model = _str(loaded_model)
    if new_state.selected_key or new_state.selected_backend_id:
        new_state.last_good_key = new_state.selected_key
        new_state.last_good_backend_id = new_state.selected_backend_id
        new_state.last_good_label = new_state.selected_label
        new_state.last_good_source = new_state.selected_source
        new_state.last_good_loaded_at = _iso_now()
    new_state.failed_candidate_key = ""
    new_state.failed_candidate_backend_id = ""
    new_state.failed_candidate_label = ""
    new_state.failed_candidate_at = ""
    return new_state


def mark_load_failure(
    state: ModelState,
    *,
    error: str | None,
    error_type: str | None,
    rollback_to_last_good: bool = True,
) -> tuple[ModelState, bool]:
    """Record a failed load attempt.

    Effects:
      * ``failed_candidate_*`` ← current ``selected_*``
      * ``last_load_result`` → 'failed'
      * ``last_load_error`` / ``last_load_error_type`` recorded
      * If ``rollback_to_last_good`` and ``last_good_*`` exists, ``selected_*``
        is rolled back to the last successfully-loaded selection.

    Returns ``(new_state, rollback_performed)``.
    """
    new_state = replace(state)
    new_state.failed_candidate_key = new_state.selected_key
    new_state.failed_candidate_backend_id = new_state.selected_backend_id
    new_state.failed_candidate_label = new_state.selected_label
    new_state.failed_candidate_at = _iso_now()
    new_state.last_load_result = "failed"
    new_state.last_load_error = _optional_str(error)
    new_state.last_load_error_type = _optional_str(error_type)

    rollback_performed = False
    if rollback_to_last_good and (new_state.last_good_key or new_state.last_good_backend_id):
        candidate_id = (
            new_state.failed_candidate_key
            or new_state.failed_candidate_backend_id
            or "<unknown>"
        )
        new_state.selected_key = new_state.last_good_key
        new_state.selected_backend_id = new_state.last_good_backend_id
        new_state.selected_label = new_state.last_good_label
        # Preserve the canonical source from the last successful load when
        # available; fall back to "fallback" (canonical SELECTED_SOURCES tag).
        new_state.selected_source = new_state.last_good_source or "fallback"
        new_state.selected_at = _iso_now()
        new_state.selected_reason = f"rolled back from failed candidate {candidate_id}"
        rollback_performed = True

    return new_state, rollback_performed


def update_load_observation(
    state: ModelState,
    *,
    loaded_model: str | None = None,
    result: str | None = None,
    error: str | None = None,
    error_type: str | None = None,
) -> ModelState:
    """Return a copy of ``state`` with observation fields updated.

    Authority fields (``selected_*``, ``runtime_context_length``) are never
    modified by this helper.
    """
    new_state = replace(state)
    if loaded_model is not None:
        new_state.last_loaded_model = _str(loaded_model)
    if result is not None:
        new_state.last_load_result = _str(result)
    if error is not None:
        new_state.last_load_error = _optional_str(error)
    if error_type is not None:
        new_state.last_load_error_type = _optional_str(error_type)
    return new_state
