#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path

try:
    from .qz_reasoning_policy import (
        apply_reasoning_policy,
        normalize_reasoning_level,
        normalize_thinking_mode,
        reasoning_policy_for_level,
        reasoning_policy_mode,
        THINKING_MODE_BUDGET_TOKENS,
    )
    from .qz_prompt_policy import assemble_instruction_stack
    from .qz_runtime_io import read_json, runtime_state_path, write_json
    from .qz_vram_snapshot import get_cached_vram_snapshot
except ImportError:
    from qz_reasoning_policy import (
        apply_reasoning_policy,
        normalize_reasoning_level,
        normalize_thinking_mode,
        reasoning_policy_for_level,
        reasoning_policy_mode,
        THINKING_MODE_BUDGET_TOKENS,
    )
    from qz_prompt_policy import assemble_instruction_stack
    from qz_runtime_io import read_json, runtime_state_path, write_json
    from qz_vram_snapshot import get_cached_vram_snapshot


STATUS_SNAPSHOT_SCHEMA = "qz.status.snapshot.v1"
STATUS_SUMMARY_SCHEMA = "qz.status.summary.v1"
RUNTIME_STATE_SCHEMA = "qz.runtime.state.v1"


def entry_identity(entry: dict | None) -> str:
    entry = entry if isinstance(entry, dict) else {}
    for field in ("slug", "key", "backend_id", "filename", "stem"):
        value = entry.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def backend_reasoning_budget() -> int | None:
    raw = os.environ.get("QZ_REASONING_BUDGET", "-1")
    try:
        return int(str(raw).strip())
    except Exception:
        return None


def _iso_state_now() -> str:
    t = time.gmtime()
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"


def control_plane_backend_timeout() -> float:
    raw = os.environ.get("QZ_CONTROL_PLANE_BACKEND_TIMEOUT", "0.75")
    try:
        return max(0.05, float(str(raw).strip()))
    except Exception:
        return 0.75


def profile_backend_error_payload(entry: dict | None, requested_model: str = "") -> dict:
    entry = entry if isinstance(entry, dict) else {}
    profile = (
        requested_model
        or entry.get("label")
        or entry.get("slug")
        or entry.get("stem")
        or entry.get("key")
        or ""
    )
    reason = entry.get("profile_error") or "profile backend target is invalid"
    return {
        "error": "profile backend missing",
        "profile": profile,
        "reason": reason,
        "fix": "Update the profile symlink under var/models or restore the missing target GGUF file.",
    }


class ModelRouter:
    def __init__(self, handler):
        self.handler = handler

    def _initialization_payload(self) -> dict:
        payload_fn = getattr(self.handler, "_initialization_payload", None)
        if not callable(payload_fn):
            return {"state": "ready", "ready": True}
        try:
            payload = payload_fn()
        except Exception:
            return {"state": "unknown", "ready": False}
        return payload if isinstance(payload, dict) else {"state": "unknown", "ready": False}

    def _initializing_status_snapshot(self, initialization: dict):
        latest_request = {}
        try:
            telemetry = getattr(self.handler, "telemetry", None)
            if telemetry is not None and hasattr(telemetry, "latest_request_summary"):
                latest_request = telemetry.latest_request_summary()
        except Exception:
            latest_request = {}
        state = initialization.get("state") or "initializing"
        return {
            "schema": STATUS_SNAPSHOT_SCHEMA,
            "status": state,
            "router_mode": True,
            "ready": False,
            "proxy_initialization": initialization,
            "health": {
                "status": None,
                "body": {
                    "status": state,
                    "initialization": initialization,
                },
            },
            "selected": {},
            "backend": {
                "selected_key": "",
                "selected_backend_id": "",
                "selected_state": state,
                "selected_path": None,
                "selected_reasoning_level": "medium",
                "selected_reasoning_policy": reasoning_policy_mode(),
                "selected_reasoning_prompt": None,
                "selected_sampling_params": {},
                "selected_thinking_mode": None,
                "selected_thinking_budget_tokens": None,
                "backend_reasoning_budget": backend_reasoning_budget(),
                "selected_context_length": None,
                "selected_context_length_state": "unknown",
                "selected_context_length_source": "",
                "backend_context_length": None,
                "backend_context_length_state": "unknown",
                "backend_context_length_source": "",
                "restart_required": False,
                "restart_required_state": "pending",
                "loaded_model": "",
                "loaded_count": 0,
                "loaded_models": [],
                "models": {},
            },
            "prompt": {
                "schema": "qz.prompt.status.v1",
                "mode": "",
                "disabled": False,
                "prompt_empty": True,
                "policy": {},
                "files_loaded": [],
                "files_missing": [],
                "files_failed": [],
            },
            "load": {
                "state": state,
                "started_at": initialization.get("started_at"),
                "finished_at": initialization.get("finished_at"),
                "error": initialization.get("error"),
                "model": "",
            },
            "latest_request": latest_request,
            "timestamp": time.time(),
        }

    def _parse_context_length(self, value, default=None):
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if not text:
                return default
            multiplier = 1
            if text.endswith("k"):
                multiplier = 1024
                text = text[:-1]
            elif text.endswith("m"):
                multiplier = 1024 * 1024
                text = text[:-1]
            elif text.endswith("g"):
                multiplier = 1024 * 1024 * 1024
                text = text[:-1]
            try:
                return int(float(text) * multiplier)
            except Exception:
                return default
        try:
            return int(value)
        except Exception:
            return default

    def _context_length_from_args(self, args) -> int | None:
        if not isinstance(args, list):
            return None
        for index, arg in enumerate(args):
            if arg == "--ctx-size" and index + 1 < len(args):
                return self._parse_context_length(args[index + 1], None)
            if isinstance(arg, str) and arg.startswith("--ctx-size="):
                return self._parse_context_length(arg.split("=", 1)[1], None)
        return None

    def _context_length_from_preset(self, preset) -> int | None:
        if not isinstance(preset, str):
            return None
        for line in preset.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "ctx-size":
                return self._parse_context_length(value.strip(), None)
        return None

    def model_state_path(self):
        cls_path = getattr(self.handler.__class__, "model_state_path", None)
        if isinstance(cls_path, str) and cls_path:
            return Path(cls_path).expanduser() if os.path.isabs(cls_path) else runtime_state_path(cls_path)
        env_path = os.environ.get("QZ_MODEL_STATE_PATH")
        if isinstance(env_path, str) and env_path:
            return Path(env_path).expanduser() if os.path.isabs(env_path) else runtime_state_path(env_path)
        return runtime_state_path("model-state.json")

    def _persist_model_state(self, selected: dict | None = None, reason: str = "", source: str = ""):
        """Write proxy-owned model selection state as qz.model_state.v1.

        Source strings written here (status_snapshot, resolve_model_selection,
        load_backend_model, ...) are legacy router values; Slice C of the
        model-selection-authority cleanup migrates callers to the canonical
        SELECTED_SOURCES vocabulary.
        """
        try:
            from .qz_model_state import (
                SELECTED_SOURCES,
                ModelState,
                load_model_state,
                write_model_state,
            )
        except ImportError:
            from qz_model_state import (
                SELECTED_SOURCES,
                ModelState,
                load_model_state,
                write_model_state,
            )

        selected = selected if isinstance(selected, dict) else {}
        state_path = self.model_state_path()

        # Preserve observation fields from any existing state file so writes
        # of authority fields do not clobber last_load_* observations.
        existing = load_model_state(state_path).state

        key = entry_identity(selected)
        backend_id = selected.get("backend_id") or key
        label_raw = selected.get("label")
        label = label_raw.strip() if isinstance(label_raw, str) else ""

        # Preserve an existing canonical SELECTED_SOURCES tag (operator,
        # qz_codex, env_seed, etc.) when:
        #   a) The write source is "status_snapshot" (observational — must never
        #      overwrite deliberate operator/qz_codex/env_seed choices), OR
        #   b) The underlying backend selection has not changed (compare by
        #      backend_id rather than selected_key because resolve() may remap
        #      the user-facing alias while routing to the same backend target).
        new_source = source or ""
        new_at = _iso_state_now()
        new_reason = reason or ""
        if source == "status_snapshot" and existing.selected_source in SELECTED_SOURCES:
            # Observational reconciliation writes must not overwrite canonical selection
            # authority.  Preserve both the source provenance AND the selection identity
            # (key/backend_id/label) so that a status probe cannot swap the operator's
            # chosen model for whatever the catalog currently reports as its default.
            new_source = existing.selected_source
            new_at = existing.selected_at or new_at
            new_reason = existing.selected_reason or new_reason
            key = existing.selected_key
            backend_id = existing.selected_backend_id
            label = existing.selected_label
        elif (
            existing.selected_source in SELECTED_SOURCES
            and existing.selected_backend_id
            and existing.selected_backend_id == backend_id
        ):
            new_source = existing.selected_source
            new_at = existing.selected_at or new_at
            new_reason = existing.selected_reason or new_reason

        new_state = ModelState(
            selected_key=key,
            selected_backend_id=backend_id,
            selected_label=label,
            selected_source=new_source,
            selected_at=new_at,
            selected_reason=new_reason,
            runtime_context_length=existing.runtime_context_length,
            last_load_result=existing.last_load_result,
            last_load_error=existing.last_load_error,
            last_load_error_type=existing.last_load_error_type,
            last_loaded_model=existing.last_loaded_model,
            # Preserve recovery memory — ordinary writes must not erase these.
            last_good_key=existing.last_good_key,
            last_good_backend_id=existing.last_good_backend_id,
            last_good_label=existing.last_good_label,
            last_good_source=existing.last_good_source,
            last_good_loaded_at=existing.last_good_loaded_at,
            failed_candidate_key=existing.failed_candidate_key,
            failed_candidate_backend_id=existing.failed_candidate_backend_id,
            failed_candidate_label=existing.failed_candidate_label,
            failed_candidate_at=existing.failed_candidate_at,
        )

        try:
            write_model_state(new_state, state_path)
        except Exception as exc:
            self._emit("state_write_failed", {"file": "model-state.json", "error": str(exc)})

    def load_runtime_model_state(self):
        payload = read_json(self.model_state_path(), default={})
        return payload if isinstance(payload, dict) else {}

    def backend_state_path(self):
        cls_path = getattr(self.handler.__class__, "backend_state_path", None)
        if isinstance(cls_path, str) and cls_path:
            return Path(cls_path).expanduser() if os.path.isabs(cls_path) else runtime_state_path(cls_path)
        env_path = os.environ.get("QZ_BACKEND_STATE_PATH")
        if isinstance(env_path, str) and env_path:
            return Path(env_path).expanduser() if os.path.isabs(env_path) else runtime_state_path(env_path)
        return runtime_state_path("backend-state.json")

    def _persist_backend_state(self, selected: dict | None = None, context_length=None, reason: str = "", source: str = "", state: str = "", loaded_model: str = "", error: str = "", health_status=None, restarted: bool | None = None):
        selected = selected if isinstance(selected, dict) else {}
        payload = {
            "selected_key": entry_identity(selected),
            "selected_backend_id": selected.get("backend_id") or entry_identity(selected),
            "selected_label": selected.get("label") or "",
            "selected_path": selected.get("path") or "",
            "selected_context_length": self.selected_context_length(selected),
            "backend_context_length": self._parse_context_length(context_length, self.backend_context_length()),
            "state": state or "",
            "loaded_model": loaded_model or "",
            "selected_reason": reason or "",
            "source": source or "",
            "error": error or "",
            "health_status": health_status,
            "restarted": restarted,
            "updated_at": time.time(),
        }
        try:
            write_json(self.backend_state_path(), payload)
        except Exception as exc:
            self._emit("state_write_failed", {"file": "backend-state.json", "error": str(exc)})

    def load_backend_state(self):
        payload = read_json(self.backend_state_path(), default={})
        return payload if isinstance(payload, dict) else {}

    def backend_context_length(self, backend_models=None, selected_backend_id: str = ""):
        context, _source, _state = self.backend_context_length_fact(backend_models, selected_backend_id)
        return context

    def backend_context_length_fact(self, backend_models=None, selected_backend_id: str = ""):
        backend_models = backend_models if isinstance(backend_models, dict) else None
        if backend_models:
            candidate_ids = []
            if selected_backend_id:
                candidate_ids.append(selected_backend_id)
            candidate_ids.extend(
                model_id
                for model_id, entry in backend_models.items()
                if isinstance(entry, dict) and entry.get("state") == "loaded"
            )
            candidate_ids.extend(backend_models.keys())
            seen = set()
            for model_id in candidate_ids:
                if model_id in seen:
                    continue
                seen.add(model_id)
                entry = backend_models.get(model_id)
                if not isinstance(entry, dict):
                    continue
                context = self._parse_context_length(entry.get("context_length"), None)
                if context is not None:
                    return context, "backend_inventory", "confirmed"
        state = self.load_backend_state()
        context = self._parse_context_length(state.get("backend_context_length"), None)
        if context is not None:
            return context, "backend_state", "cached"
        env_value = os.environ.get("QZ_CONTEXT")
        context = self._parse_context_length(env_value, None)
        if context is not None:
            return context, "env:QZ_CONTEXT", "default"
        return 262144, "built_in_default", "default"

    def selected_context_length(self, selected: dict | None = None):
        context, _source, _state = self.selected_context_length_fact(selected)
        return context

    def selected_context_length_fact(self, selected: dict | None = None):
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        if isinstance(selected, dict):
            runtime_context = self._parse_context_length(selected.get("runtime_context_length"), None)
            if runtime_context is not None:
                return runtime_context, "catalog.runtime_context_length", "intended"
            catalog_context = self._parse_context_length(selected.get("context_length"), None)
            if catalog_context is not None:
                return catalog_context, "catalog.context_length", "intended"
        env_value = os.environ.get("QZ_CONTEXT")
        context = self._parse_context_length(env_value, None)
        if context is not None:
            return context, "env:QZ_CONTEXT", "intended"
        return 262144, "built_in_default", "default"

    def _emit(self, event_type: str, payload: dict | None = None):
        telemetry = getattr(self.handler, "telemetry", None)
        if telemetry is None:
            return
        try:
            telemetry.emit(event_type, payload if isinstance(payload, dict) else {})
        except Exception:
            pass

    def backend_models(self, timeout: float | None = None):
        try:
            if timeout is None:
                payload = self.handler._backend().get_models()
            else:
                try:
                    payload = self.handler._backend().get_models(timeout=timeout)
                except TypeError:
                    payload = self.handler._backend().get_models()
        except Exception as exc:
            self._emit("model_inventory_failed", {"error": str(exc)})
            return {}

        backend = {}
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id") or item.get("name")
            if not isinstance(model_id, str) or not model_id:
                continue
            status = item.get("status") or {}
            state = status.get("value") if isinstance(status, dict) else "unknown"
            backend[model_id] = {
                "state": state or "unknown",
                "path": item.get("path"),
                "quantization_level": item.get("quantization_level") or item.get("quant") or item.get("type"),
            }
            if isinstance(status, dict):
                context = self._context_length_from_args(status.get("args"))
                if context is None:
                    context = self._context_length_from_preset(status.get("preset"))
                if context is not None:
                    backend[model_id]["context_length"] = context
        return backend

    def backend_model_control_available(self, backend_models=None):
        backend_models = backend_models if isinstance(backend_models, dict) else self.backend_models()
        for entry in backend_models.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("state") and entry.get("state") != "unknown":
                return True
            if entry.get("path") or entry.get("quantization_level"):
                return True
        return False

    def backend_model_entry(self, model_id: str):
        if not model_id:
            return {}
        return self.backend_models().get(model_id, {})

    def backend_model_state(self, model_id: str):
        entry = self.backend_model_entry(model_id)
        return entry.get("state") or "unknown"

    def loaded_backend_models(self, backend_models=None):
        backend_models = backend_models if isinstance(backend_models, dict) else self.backend_models()
        loaded = []
        for model_id, entry in backend_models.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("state") == "loaded":
                loaded.append({
                    "id": model_id,
                    "path": entry.get("path"),
                    "quantization_level": entry.get("quantization_level"),
                })
        return loaded

    def _reconcile_status_state(
        self,
        selected: dict | None,
        health_status,
        selected_backend_id: str,
        selected_context_length,
        backend_context_length,
        backend_state: str,
        loaded_model: str,
    ):
        selected = selected if isinstance(selected, dict) else {}
        selected_key = entry_identity(selected)
        model_state = self.load_runtime_model_state()
        backend_state_file = self.load_backend_state()
        # selected_path was dropped from qz.model_state.v1 — selection identity
        # is selected_key / selected_backend_id only.
        model_drift = (
            model_state.get("selected_key") != selected_key
            or model_state.get("selected_backend_id") != selected_backend_id
        )
        backend_drift = (
            backend_state_file.get("selected_key") != selected_key
            or backend_state_file.get("selected_backend_id") != selected_backend_id
            or self._parse_context_length(backend_state_file.get("selected_context_length"), None) != selected_context_length
            or self._parse_context_length(backend_state_file.get("backend_context_length"), None) != backend_context_length
            or backend_state_file.get("state") != backend_state
            or backend_state_file.get("loaded_model", "") != loaded_model
            or backend_state_file.get("health_status") != health_status
        )
        if model_drift and selected_key:
            # Only persist from status reconciliation when a valid selection
            # exists.  This prevents "status_snapshot" from creating authoritative
            # empty selections during startup/scan races.
            self._persist_model_state(selected, reason="status reconciliation", source="status_snapshot")
        # Record last_good_backend_id when the backend is confirmed healthy and no
        # recovery point has been written yet.  This runs on the first status probe
        # after a successful load and provides the self-heal anchor for future restarts.
        if health_status == 200 and backend_state == "loaded" and selected_backend_id:
            try:
                from .qz_model_state import (
                    load_model_state as _lms,
                    mark_load_success as _mls,
                    write_model_state as _wms,
                )
            except ImportError:
                from qz_model_state import (
                    load_model_state as _lms,
                    mark_load_success as _mls,
                    write_model_state as _wms,
                )
            try:
                _state_path = self.model_state_path()
                _ms = _lms(_state_path).state
                if not _ms.last_good_backend_id:
                    _updated = _mls(_ms, loaded_model=loaded_model)
                    _wms(_updated, _state_path)
            except Exception:
                pass
        if backend_drift:
            self._persist_backend_state(
                selected=selected,
                context_length=backend_context_length,
                reason="status reconciliation",
                source="status_snapshot",
                state=backend_state,
                loaded_model=loaded_model,
                health_status=health_status,
                restarted=False,
            )

    def backend_health(self, timeout: float | None = None):
        if timeout is None:
            timeout = 10
        try:
            resp = self.handler._backend().get_health(timeout=timeout)
        except Exception as exc:
            self._emit("backend_health_failed", {"error": str(exc)})
            return 0, {"status": "unreachable", "error": str(exc)}
        try:
            body = json.loads(resp.data.decode("utf-8")) if resp.data else {}
        except Exception:
            body = {}
        return resp.status, body

    def unload_backend_model(self, model_id: str, wait: bool = False, timeout: float | None = None):
        if timeout is None:
            timeout = float(getattr(self.handler.__class__, "model_load_timeout", 600.0))
        if not model_id:
            return
        cls = self.handler.__class__
        cls.model_load_model = model_id
        cls.model_load_started_at = time.time()
        cls.model_load_finished_at = None
        cls.model_load_error = None

        existing_state = self.backend_model_state(model_id)
        if existing_state in ("unloaded", "unknown"):
            cls.model_load_state = "idle"
            cls.model_load_finished_at = time.time()
            self._persist_backend_state(
                selected={"backend_id": model_id, "key": model_id},
                context_length=self.backend_context_length(),
                reason="cached unloaded model",
                source="unload_backend_model",
                state="idle",
                loaded_model="",
                health_status=200,
                restarted=False,
            )
            self._emit("model_unload_ready", {
                "model": model_id,
                "cached": True,
            })
            return

        self._emit("model_unload_started", {
            "model": model_id,
            "wait": bool(wait),
            "timeout": timeout,
        })
        resp = self.handler._backend().unload_model(model_id, timeout=timeout)
        if resp.status >= 400:
            cls.model_load_state = "failed"
            cls.model_load_error = f"unload failed: HTTP {resp.status}"
            cls.model_load_finished_at = time.time()
            self._emit("model_unload_failed", {
                "model": model_id,
                "status": resp.status,
                "error": cls.model_load_error,
                "wait": bool(wait),
            })
            return
        cls.model_load_state = "unloading"
        if not wait:
            self._emit("model_unload_pending", {
                "model": model_id,
                "wait": False,
            })
            return
        ok, snapshot = self.handler._backend().wait_for_model_state(model_id, {"unloaded", "unknown"}, timeout=timeout)
        cls.model_load_finished_at = time.time()
        if ok:
            cls.model_load_state = "idle"
            cls.model_load_error = None
            self._persist_backend_state(
                selected={"backend_id": model_id, "key": model_id},
                context_length=self.backend_context_length(),
                reason="unloaded model",
                source="unload_backend_model",
                state="idle",
                loaded_model="",
                health_status=snapshot.get("health_status"),
                restarted=False,
            )
            self._emit("model_unload_ready", {
                "model": model_id,
                "health_status": snapshot.get("health_status"),
            })
        else:
            cls.model_load_state = "unloading"
            cls.model_load_error = "timed out waiting for backend model unload"
            cls.model_load_health = snapshot.get("health_status")
            self._emit("model_unload_failed", {
                "model": model_id,
                "error": cls.model_load_error,
                "health_status": cls.model_load_health,
                "wait": True,
            })

    def selected_model_entry(self):
        catalog = self.handler._model_catalog()
        selected = catalog.selected or (catalog.entries[0] if catalog.entries else None)
        if selected is None:
            return None
        return selected

    def selected_backend_id(self):
        selected = self.selected_model_entry()
        if not selected:
            return ""
        return selected.get("backend_id") or entry_identity(selected)

    def selected_reasoning_level(self, selected: dict | None = None):
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        if not isinstance(selected, dict):
            return "medium"
        level = normalize_reasoning_level(selected.get("default_reasoning_level"))
        if level != "medium" or selected.get("default_reasoning_level"):
            return level
        supported = selected.get("supported_reasoning_levels")
        if isinstance(supported, list):
            for item in supported:
                if isinstance(item, dict):
                    effort = normalize_reasoning_level(item.get("effort"))
                    if effort in {"low", "medium", "high", "xhigh"}:
                        return effort
        text = " ".join(
            str(value).lower()
            for value in (
                selected.get("label"),
                selected.get("name"),
                selected.get("notes"),
                selected.get("slug"),
                selected.get("backend_id"),
            )
            if value
        )
        if "apex" in text or "reasoning" in text:
            return "high"
        if "iq4" in text or "aggressive" in text or "fast" in text:
            return "low"
        return "medium"

    def selected_thinking_mode(self, selected: dict | None = None) -> str:
        """Return the resolved thinking mode for the selected model/profile.

        Source priority:
          1. Explicit profile override (thinking_mode / reasoning_mode / default_thinking_mode)
          2. Model-name heuristic (Coder/Instruct → non_thinking; Qwen3.6/A3B → thinking)
          3. Fallback: 'auto' (unknown — no budget injected)
        """
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        if not isinstance(selected, dict):
            return "auto"
        overrides = selected.get("overrides") if isinstance(selected, dict) else {}
        overrides = overrides if isinstance(overrides, dict) else {}
        # 1. Explicit profile override
        for key in ("thinking_mode", "reasoning_mode", "default_thinking_mode"):
            value = overrides.get(key)
            if isinstance(value, str) and value.strip():
                return normalize_thinking_mode(value)
        # 2. Model-name heuristic
        text = " ".join(
            str(v).lower()
            for v in (
                selected.get("label"),
                selected.get("name"),
                selected.get("notes"),
                selected.get("slug"),
                selected.get("backend_id"),
            )
            if v
        ).strip()
        # Non-thinking markers take priority (more specific)
        if any(kw in text for kw in ("coder", "instruct")):
            return "non_thinking"
        # Thinking markers (Qwen3.6 / MoE A3B / explicit "thinking"/"reasoning")
        if any(kw in text for kw in ("qwen3.6", "qwen3-6", "a3b", "thinking", "reasoning")):
            return "thinking"
        return "auto"

    def selected_thinking_budget_tokens(self, selected: dict | None = None) -> int | None:
        """Return the per-block reasoning budget in tokens for the selected model, or None."""
        mode = self.selected_thinking_mode(selected)
        if mode == "thinking":
            level = self.selected_reasoning_level(selected)
            return THINKING_MODE_BUDGET_TOKENS.get(normalize_reasoning_level(level))
        return None

    def selected_reasoning_policy(self, selected: dict | None = None, body: dict | None = None):
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        overrides = selected.get("overrides") if isinstance(selected, dict) else {}
        overrides = overrides if isinstance(overrides, dict) else {}
        allow_client_override = overrides.get("allow_client_reasoning_override")
        if allow_client_override is None:
            allow_client_override = not bool(overrides.get("force_default_reasoning_level"))
        default_level = self.selected_reasoning_level(selected)
        level = default_level
        if allow_client_override and isinstance(body, dict):
            reasoning = body.get("reasoning")
            if isinstance(reasoning, dict) and reasoning.get("effort"):
                level = normalize_reasoning_level(reasoning.get("effort"))
            elif body.get("reasoning_effort"):
                level = normalize_reasoning_level(body.get("reasoning_effort"))
        policy = reasoning_policy_for_level(level)
        policy["mode"] = reasoning_policy_mode()
        # Resolve thinking mode and per-block budget
        thinking_mode = self.selected_thinking_mode(selected)
        thinking_budget_tokens = None
        if thinking_mode == "thinking":
            thinking_budget_tokens = THINKING_MODE_BUDGET_TOKENS.get(normalize_reasoning_level(level))
        policy["thinking_mode"] = thinking_mode
        policy["thinking_budget_tokens"] = thinking_budget_tokens
        policy["client_override_allowed"] = bool(allow_client_override)
        policy["default_effort"] = default_level
        return policy

    def selected_prompt_status(self, selected: dict | None = None) -> dict:
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        try:
            _instructions, policy = assemble_instruction_stack(
                existing_instructions="",
                client_blocks=[],
                selected_model=selected,
            )
        except Exception as exc:
            policy = {
                "mode": "",
                "prompt_files_loaded": [],
                "replacement_files_loaded": [],
                "prompt_files_missing": [],
                "replacement_files_missing": [],
                "prompt_files_failed": [{"path": "", "error": str(exc)}],
                "replacement_files_failed": [],
            }

        def _strings(value):
            if not isinstance(value, list):
                return []
            return [item for item in value if isinstance(item, str)]

        def _failures(*values):
            out = []
            for value in values:
                if not isinstance(value, list):
                    continue
                for item in value:
                    if isinstance(item, dict):
                        out.append({
                            "path": str(item.get("path") or ""),
                            "error": str(item.get("error") or ""),
                        })
                    elif isinstance(item, str):
                        out.append({"path": item, "error": ""})
            return out

        loaded = []
        for item in _strings(policy.get("replacement_files_loaded")) + _strings(policy.get("prompt_files_loaded")):
            if item not in loaded:
                loaded.append(item)
        missing = []
        for item in _strings(policy.get("replacement_files_missing")) + _strings(policy.get("prompt_files_missing")):
            if item not in missing:
                missing.append(item)

        disabled = bool(policy.get("disable_system_prompt"))
        prompt_empty = not disabled and not (_instructions or "").strip()

        return {
            "schema": "qz.prompt.status.v1",
            "mode": policy.get("mode") or "",
            "disabled": disabled,
            "prompt_empty": prompt_empty,
            "policy": policy,
            "files_loaded": loaded,
            "files_missing": missing,
            "files_failed": _failures(
                policy.get("replacement_files_failed"),
                policy.get("prompt_files_failed"),
            ),
        }

    def apply_reasoning_policy(self, body: dict, selected: dict | None = None):
        policy = self.selected_reasoning_policy(selected, body)
        result = apply_reasoning_policy(
            body,
            policy.get("effort"),
            policy.get("mode"),
            policy.get("thinking_mode"),
        )
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        qz_reasoning = metadata.get("qz_reasoning") if isinstance(metadata.get("qz_reasoning"), dict) else {}
        qz_reasoning["thinking_mode"] = policy.get("thinking_mode")
        qz_reasoning["thinking_budget_tokens"] = policy.get("thinking_budget_tokens")
        qz_reasoning["client_override_allowed"] = bool(policy.get("client_override_allowed"))
        qz_reasoning["default_level"] = policy.get("default_effort")
        metadata["qz_reasoning"] = qz_reasoning
        result["metadata"] = metadata
        return result

    def profile_model_entry(self, requested_model: str):
        catalog = self.handler._model_catalog()
        if not isinstance(requested_model, str) or not requested_model.strip():
            return None, ""
        selected, reason = catalog.resolve(query=requested_model)
        if selected is None:
            return None, ""
        return selected, reason

    def status_snapshot(self):
        initialization = self._initialization_payload()
        if not initialization.get("ready") and getattr(self.handler.__class__, "model_catalog", None) is None:
            return self._initializing_status_snapshot(initialization)

        selected = self.selected_model_entry()
        probe_timeout = control_plane_backend_timeout()
        backend_models = self.backend_models(timeout=probe_timeout)
        health_status, health_body = self.backend_health(timeout=probe_timeout)
        selected_key = entry_identity(selected)
        selected_backend_id = self.selected_backend_id()
        backend_entry = backend_models.get(selected_backend_id or selected_key or "", {})
        backend_state = backend_entry.get("state") or "unknown"
        loaded_models = self.loaded_backend_models(backend_models)
        loaded_model = loaded_models[0]["id"] if loaded_models else ""
        load_state = getattr(self.handler, "model_load_state", None)
        load_model = getattr(self.handler, "model_load_model", None)
        load_error = getattr(self.handler, "model_load_error", None)
        load_started_at = getattr(self.handler, "model_load_started_at", None)
        load_finished_at = getattr(self.handler, "model_load_finished_at", None)
        if load_model and selected_backend_id and load_model != selected_backend_id and load_state not in ("loading", "unloading"):
            load_state = backend_state
            load_model = selected_backend_id
            load_error = None
            load_started_at = None
            load_finished_at = None
        if load_state in (None, "", "idle"):
            load_state = backend_state
        ready = health_status == 200 and backend_state == "loaded"
        # BackendManager fallback: llama.cpp /v1/models does not populate
        # status.value, so backend_state is "unknown" even when the model is
        # loaded.  Consult BackendManager snapshot as authoritative source for
        # direct-launch mode (same logic as qz_control_plane / build_model_status).
        if not ready and backend_state in (None, "", "unknown") and health_status == 200:
            try:
                _bm = getattr(self.handler, "backend_manager", None)
                if _bm is not None and callable(getattr(_bm, "snapshot", None)):
                    _bm_snap = _bm.snapshot()
                    if (
                        isinstance(_bm_snap, dict)
                        and _bm_snap.get("phase") == "healthy"
                        and _bm_snap.get("backend_health_ok") is True
                        and not _bm_snap.get("launch_model_error")
                    ):
                        backend_state = "loaded"
                        ready = True
                        if not loaded_model:
                            for _k in ("launch_model_backend_id", "launch_model_key"):
                                _v = (_bm_snap.get(_k) or "").strip()
                                if _v:
                                    loaded_model = _v
                                    break
                        if load_state in (None, "", "idle", "unknown"):
                            load_state = "loaded"
            except Exception:
                pass
        reasoning_level = self.selected_reasoning_level(selected)
        thinking_mode = self.selected_thinking_mode(selected)
        reasoning_policy = self.selected_reasoning_policy(selected)
        prompt_status = self.selected_prompt_status(selected)
        selected_context_length, selected_context_source, selected_context_state = self.selected_context_length_fact(selected)
        backend_context_length, backend_context_source, backend_context_state = self.backend_context_length_fact(backend_models, selected_backend_id)
        restart_required_state = "confirmed" if backend_context_state == "confirmed" else "pending"
        restart_required = (
            selected_context_length != backend_context_length
            if restart_required_state == "confirmed"
            else False
        )
        self._reconcile_status_state(
            selected,
            health_status,
            selected_backend_id,
            selected_context_length,
            backend_context_length,
            backend_state,
            loaded_model,
        )
        latest_request = {}
        try:
            telemetry = getattr(self.handler, "telemetry", None)
            if telemetry is not None and hasattr(telemetry, "latest_request_summary"):
                latest_request = telemetry.latest_request_summary()
        except Exception:
            latest_request = {}
        return {
            "schema": STATUS_SNAPSHOT_SCHEMA,
            "status": "ok" if ready else "loading",
            "router_mode": True,
            "ready": ready,
            "health": {
                "status": health_status,
                "body": health_body,
            },
            "selected": selected,
            "backend": {
                "selected_key": selected_key,
                "selected_backend_id": selected_backend_id,
                "selected_state": backend_state,
                "selected_path": backend_entry.get("path"),
                "selected_reasoning_level": reasoning_level,
                "selected_reasoning_policy": reasoning_policy.get("mode"),
                "selected_reasoning_prompt": reasoning_policy.get("prompt"),
                "selected_sampling_params": reasoning_policy.get("sampling"),
                "selected_thinking_mode": thinking_mode,
                "selected_thinking_budget_tokens": reasoning_policy.get("thinking_budget_tokens"),
                "backend_reasoning_budget": backend_reasoning_budget(),
                "selected_context_length": selected_context_length,
                "selected_context_length_state": selected_context_state,
                "selected_context_length_source": selected_context_source,
                "backend_context_length": backend_context_length,
                "backend_context_length_state": backend_context_state,
                "backend_context_length_source": backend_context_source,
                "restart_required": restart_required,
                "restart_required_state": restart_required_state,
                "loaded_model": loaded_model,
                "loaded_count": len(loaded_models),
                "loaded_models": loaded_models,
                "models": backend_models,
            },
            "prompt": prompt_status,
            "load": {
                "state": load_state,
                "started_at": load_started_at,
                "finished_at": load_finished_at,
                "error": load_error,
                "model": load_model,
            },
            "latest_request": latest_request,
            "timestamp": time.time(),
        }

    def status_summary(self, reason: str = "", snapshot: dict | None = None):
        snapshot = snapshot if isinstance(snapshot, dict) else self.status_snapshot()
        selected = snapshot.get("selected") or {}
        backend = snapshot.get("backend") or {}
        load = snapshot.get("load") or {}
        health = snapshot.get("health") or {}
        latest_request = snapshot.get("latest_request") if isinstance(snapshot.get("latest_request"), dict) else {}
        prompt = snapshot.get("prompt") if isinstance(snapshot.get("prompt"), dict) else {}
        loaded_models = backend.get("loaded_models") or []
        loaded_ids = [
            model.get("id")
            for model in loaded_models
            if isinstance(model, dict) and model.get("id")
        ]
        loaded_model = backend.get("loaded_model") or (loaded_ids[0] if loaded_ids else "")
        return {
            "schema": STATUS_SUMMARY_SCHEMA,
            "reason": reason,
            "ready": snapshot.get("ready", False),
            "router_mode": snapshot.get("router_mode", False),
            "selected": backend.get("selected_backend_id") or backend.get("selected_key") or "",
            "selected_key": backend.get("selected_key") or entry_identity(selected),
            "selected_state": backend.get("selected_state") or "unknown",
            "selected_reasoning_level": backend.get("selected_reasoning_level") or "medium",
            "reasoning_policy": backend.get("selected_reasoning_policy") or reasoning_policy_mode(),
            "reasoning_prompt": backend.get("selected_reasoning_prompt"),
            "sampling": backend.get("selected_sampling_params") or {},
            "selected_thinking_mode": backend.get("selected_thinking_mode"),
            "thinking_budget_tokens": backend.get("selected_thinking_budget_tokens"),
            "backend_reasoning_budget": backend.get("backend_reasoning_budget"),
            "selected_context_length": backend.get("selected_context_length"),
            "selected_context_length_state": backend.get("selected_context_length_state") or "unknown",
            "selected_context_length_source": backend.get("selected_context_length_source") or "",
            "backend_context_length": backend.get("backend_context_length"),
            "backend_context_length_state": backend.get("backend_context_length_state") or "unknown",
            "backend_context_length_source": backend.get("backend_context_length_source") or "",
            "restart_required": bool(backend.get("restart_required")),
            "restart_required_state": backend.get("restart_required_state") or "unknown",
            "loaded": loaded_ids,
            "loaded_model": loaded_model,
            "loaded_count": backend.get("loaded_count") or len(loaded_ids),
            "load_state": load.get("state") or "unknown",
            "health_status": health.get("status"),
            "latest_request": latest_request,
            "prompt": prompt,
            "timestamp": snapshot.get("timestamp"),
        }

    def runtime_state_payload(self, requested_model: str = ""):
        snapshot = self.status_snapshot()
        selected = snapshot.get("selected") or {}
        backend = snapshot.get("backend") or {}
        load = snapshot.get("load") or {}
        profile = requested_model or selected.get("label") or selected.get("slug") or selected.get("key") or ""
        selected_key = backend.get("selected_key") or entry_identity(selected)
        return {
            "schema": RUNTIME_STATE_SCHEMA,
            "ready": snapshot["ready"],
            "load_state": load.get("state") or "unknown",
            "profile": profile,
            "selected": selected_key,
            "selected_key": selected_key,
            "selected_backend_id": backend.get("selected_backend_id") or "",
            "selected_state": backend.get("selected_state") or "unknown",
            "context_length": backend.get("selected_context_length"),
            "context_length_state": backend.get("selected_context_length_state") or "unknown",
            "context_length_source": backend.get("selected_context_length_source") or "",
            "backend_context_length": backend.get("backend_context_length"),
            "backend_context_length_state": backend.get("backend_context_length_state") or "unknown",
            "backend_context_length_source": backend.get("backend_context_length_source") or "",
            "restart_required": bool(backend.get("restart_required")),
            "restart_required_state": backend.get("restart_required_state") or "unknown",
            "health_status": (snapshot.get("health") or {}).get("status"),
            "reasoning_level": backend.get("selected_reasoning_level") or "medium",
            "reasoning_policy": backend.get("selected_reasoning_policy") or reasoning_policy_mode(),
        }

    def runtime_state_block(self, requested_model: str = ""):
        state = self.runtime_state_payload(requested_model)
        ready = "1" if state["ready"] else "0"
        return (
            f'<QZSTATE v=1 ready={ready} '
            f'load={state["load_state"]} ctx={state["context_length"]} '
            f'prof={state["profile"]} sel={state["selected"]}>'
        )

    def inject_runtime_state(self, body: dict, requested_model: str = ""):
        if not getattr(self.handler, "runtime_state_enabled", False):
            return body
        if not isinstance(body, dict):
            return body
        state = self.runtime_state_payload(requested_model)
        metadata = body.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["qz_runtime"] = state
        body["metadata"] = metadata

        block = self.runtime_state_block(requested_model)
        existing = body.get("instructions")
        if isinstance(existing, str) and existing.strip():
            if block not in existing:
                body["instructions"] = existing.strip() + "\n\n" + block
        else:
            body["instructions"] = block
        return body

    def load_backend_model(self, model_id: str, wait: bool = False, timeout: float | None = None):
        if timeout is None:
            timeout = float(getattr(self.handler.__class__, "model_load_timeout", 600.0))
        if not model_id:
            return
        cls = self.handler.__class__

        existing_state = self.backend_model_state(model_id)
        if existing_state == "loaded":
            cls.model_load_model = model_id
            cls.model_load_state = "ready"
            cls.model_load_error = None
            self._persist_backend_state(
                selected={"backend_id": model_id, "key": model_id},
                context_length=self.backend_context_length(),
                reason="cached loaded model",
                source="load_backend_model",
                state="loaded",
                loaded_model=model_id,
                health_status=200,
                restarted=False,
            )
            self._persist_model_state(
                {
                    "key": model_id,
                    "backend_id": model_id,
                },
                reason="cached loaded model",
                source="load_backend_model",
            )
            self._emit("model_load_ready", {
                "model": model_id,
                "health_status": 200,
                "cached": True,
            })
            return
        if existing_state == "loading":
            cls.model_load_model = model_id
            cls.model_load_state = "loading"
            cls.model_load_error = None
            self._emit("model_load_pending", {
                "model": model_id,
                "wait": bool(wait),
                "cached": True,
            })
            if not wait:
                return
            ok, snapshot = self.handler._backend().wait_for_model_ready(model_id, timeout=timeout)
            cls.model_load_finished_at = time.time()
            if ok:
                cls.model_load_state = "ready"
                cls.model_load_error = None
                self._persist_backend_state(
                    selected={"backend_id": model_id, "key": model_id},
                    context_length=self.backend_context_length(),
                    reason="loaded model",
                    source="load_backend_model",
                    state="loaded",
                    loaded_model=model_id,
                    health_status=snapshot.get("health_status"),
                    restarted=False,
                )
                self._persist_model_state(
                    {
                        "key": model_id,
                        "backend_id": model_id,
                    },
                    reason="loaded model",
                    source="load_backend_model",
                )
                self._emit("model_load_ready", {
                    "model": model_id,
                    "health_status": snapshot.get("health_status"),
                    "cached": True,
                })
            else:
                cls.model_load_state = "loading"
                cls.model_load_error = "timed out waiting for backend model ready"
                cls.model_load_health = snapshot.get("health_status")
                self._emit("model_load_failed", {
                    "model": model_id,
                    "error": cls.model_load_error,
                    "health_status": cls.model_load_health,
                    "wait": True,
                    "cached": True,
            })
            return

        cls.model_load_model = model_id
        cls.model_load_started_at = time.time()
        cls.model_load_finished_at = None
        cls.model_load_error = None
        self._emit("model_load_started", {
            "model": model_id,
            "wait": bool(wait),
            "timeout": timeout,
        })
        resp = self.handler._backend().load_model(model_id, timeout=timeout)
        if resp.status >= 400:
            cls.model_load_state = "failed"
            cls.model_load_error = f"load failed: HTTP {resp.status}"
            cls.model_load_finished_at = time.time()
            self._emit("model_load_failed", {
                "model": model_id,
                "status": resp.status,
                "error": cls.model_load_error,
                "wait": bool(wait),
            })
            return
        cls.model_load_state = "loading"
        if not wait:
            self._emit("model_load_pending", {
                "model": model_id,
                "wait": False,
            })
            return
        ok, snapshot = self.handler._backend().wait_for_model_ready(model_id, timeout=timeout)
        cls.model_load_finished_at = time.time()
        if ok:
            cls.model_load_state = "ready"
            cls.model_load_error = None
            self._persist_backend_state(
                selected={"backend_id": model_id, "key": model_id},
                context_length=self.backend_context_length(),
                reason="loaded model",
                source="load_backend_model",
                state="loaded",
                loaded_model=model_id,
                health_status=snapshot.get("health_status"),
                restarted=False,
            )
            self._persist_model_state(
                {
                    "key": model_id,
                    "backend_id": model_id,
                },
                reason="loaded model",
                source="load_backend_model",
            )
            self._emit("model_load_ready", {
                "model": model_id,
                "health_status": snapshot.get("health_status"),
            })
        else:
            cls.model_load_state = "loading"
            cls.model_load_error = "timed out waiting for backend model ready"
            cls.model_load_health = snapshot.get("health_status")
            self._emit("model_load_failed", {
                "model": model_id,
                "error": cls.model_load_error,
                "health_status": cls.model_load_health,
                "wait": True,
            })

    def restart_backend_for_context(self, context_length, selected: dict | None = None, reason: str = "", timeout: float | None = None):
        timeout = timeout if timeout is not None else float(getattr(self.handler.__class__, "model_load_timeout", 600.0))
        desired_context = self._parse_context_length(context_length, self.backend_context_length())
        current_context = self.backend_context_length()
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        if desired_context == current_context:
            self._persist_backend_state(
                selected=selected,
                context_length=current_context,
                reason=reason or "backend context unchanged",
                source="restart_backend_for_context",
                state="ready",
                loaded_model=self.selected_backend_id(),
                health_status=200,
                restarted=False,
            )
            return {
                "restarted": False,
                "context_length": current_context,
                "health_status": 200,
            }

        self._emit("backend_restart_started", {
            "selected": entry_identity(selected),
            "current_context_length": current_context,
            "desired_context_length": desired_context,
            "reason": reason,
        })
        self._persist_backend_state(
            selected=selected,
            context_length=current_context,
            reason=reason or "backend restart requested",
            source="restart_backend_for_context",
            state="restarting",
            loaded_model=self.selected_backend_id(),
            health_status=None,
            restarted=False,
        )
        try:
            result = self.handler._backend().restart_container(desired_context, timeout=timeout)
        except Exception as exc:
            error = str(exc)
            cls = self.handler.__class__
            cls.model_load_state = "failed"
            cls.model_load_error = error
            cls.model_load_finished_at = time.time()
            self._persist_backend_state(
                selected=selected,
                context_length=current_context,
                reason=reason or "backend restart failed",
                source="restart_backend_for_context",
                state="failed",
                loaded_model="",
                error=error,
                health_status=None,
                restarted=False,
            )
            self._emit("backend_restart_failed", {
                "selected": entry_identity(selected),
                "current_context_length": current_context,
                "desired_context_length": desired_context,
                "error": error,
                "reason": reason,
            })
            raise

        cls = self.handler.__class__
        cls.model_load_state = "idle"
        cls.model_load_error = None
        cls.model_load_finished_at = time.time()
        self._persist_backend_state(
            selected=selected,
            context_length=desired_context,
            reason=reason or "backend restarted",
            source="restart_backend_for_context",
            state="idle",
            loaded_model="",
            health_status=result.get("health_status"),
            restarted=True,
        )
        self._emit("backend_restart_ready", {
            "selected": entry_identity(selected),
            "current_context_length": current_context,
            "desired_context_length": desired_context,
            "health_status": result.get("health_status"),
            "reason": reason,
        })
        return result

    def resolve_model_selection(self, requested_model):
        catalog = self.handler._model_catalog()
        if not requested_model:
            selected = catalog.selected or (catalog.entries[0] if catalog.entries else None)
            reason = "default"
        else:
            selected, reason = catalog.resolve(query=requested_model)
        if selected is None and not requested_model and catalog.entries:
            selected = catalog.entries[0]
            reason = reason or "catalog fallback"
        if selected is None:
            return None, reason
        if selected.get("profile_valid", True) is False:
            payload = profile_backend_error_payload(selected, requested_model or "")
            self._emit("model_selection_failed", {
                "requested": requested_model,
                "selected": entry_identity(selected),
                "error": payload.get("reason"),
            })
            return None, payload
        target_backend_id = selected.get("backend_id") or entry_identity(selected)
        backend_inventory = self.backend_models()
        desired_context_length = self.selected_context_length(selected)
        if self.backend_model_control_available(backend_inventory):
            current_backend_id = self.selected_backend_id()
            current_context_length = self.backend_context_length(backend_inventory, current_backend_id)
            if desired_context_length != current_context_length:
                try:
                    backend_timeout = float(getattr(self.handler.__class__, "model_load_timeout", 600.0))
                    self.restart_backend_for_context(desired_context_length, selected=selected, reason=reason, timeout=backend_timeout)
                except Exception as exc:
                    self._emit("model_load_failed", {
                        "model": target_backend_id,
                        "error": f"backend restart failed: {exc}",
                        "wait": True,
                    })
                    return None, f"{reason}; backend restart failed ({exc})"
                backend_inventory = self.backend_models()
                current_backend_id = ""
            if current_backend_id and current_backend_id != target_backend_id:
                current_state = backend_inventory.get(current_backend_id, {}).get("state") or self.backend_model_state(current_backend_id)
                if current_state in ("loaded", "loading"):
                    self.unload_backend_model(current_backend_id, wait=True)
                    current_state = self.backend_model_state(current_backend_id)
                    if current_state not in ("unloaded", "unknown"):
                        self._emit("model_load_failed", {
                            "model": current_backend_id,
                            "error": f"current model not unloaded: {current_state}",
                            "wait": True,
                        })
                        return None, f"{reason}; current {current_backend_id} not unloaded ({current_state})"

            target_state = backend_inventory.get(target_backend_id, {}).get("state") or "unknown"
            if target_state != "loaded":
                self.load_backend_model(target_backend_id, wait=True)
                backend_inventory = self.backend_models()

            backend_state = backend_inventory.get(target_backend_id, {}).get("state") or self.backend_model_state(target_backend_id)
            if backend_state != "loaded":
                self._emit("model_load_failed", {
                    "model": target_backend_id,
                    "error": f"target model not ready: {backend_state}",
                    "wait": True,
                })
                return None, f"{reason}; target {target_backend_id} not ready ({backend_state})"
        else:
            self._emit("model_load_skipped", {
                "model": target_backend_id,
                "reason": "backend model inventory unavailable",
            })

        catalog.selected = selected
        catalog.reason = reason
        self._persist_model_state(selected, reason=reason, source="resolve_model_selection")
        self._persist_backend_state(
            selected=selected,
            context_length=desired_context_length,
            reason=reason,
            source="resolve_model_selection",
            state=self.backend_model_state(target_backend_id) if self.backend_model_control_available(backend_inventory) else "unknown",
            loaded_model=target_backend_id,
            restarted=False,
        )
        self._emit("model_selected", {
            "requested": requested_model,
            "selected": entry_identity(selected),
            "backend_id": selected.get("backend_id") or entry_identity(selected),
            "target_backend_id": target_backend_id,
            "reason": reason,
            "reasoning_level": self.selected_reasoning_level(selected),
            "reasoning_policy": self.selected_reasoning_policy(selected).get("mode"),
            "selected_context_length": desired_context_length,
        })
        return selected, reason

    def model_catalog_payload(self):
        initialization = self._initialization_payload()
        if not initialization.get("ready") and getattr(self.handler.__class__, "model_catalog", None) is None:
            return {
                "object": "list",
                "data": [],
                "status": initialization.get("state") or "initializing",
                "initialization": initialization,
            }
        catalog = self.handler._model_catalog()
        return catalog.to_v1_models(backend_models=self.backend_models())

    def ollama_models(self):
        initialization = self._initialization_payload()
        if not initialization.get("ready") and getattr(self.handler.__class__, "model_catalog", None) is None:
            return []
        catalog = self.handler._model_catalog()
        return catalog.to_ollama_models(backend_models=self.backend_models())

    def handle_ollama_get(self):
        # Codex --oss may probe Ollama-compatible endpoints before using /v1.
        if self.handler.path in ("/api/tags", "/v1/api/tags"):
            self.handler._send_json(200, {"models": self.ollama_models()})
            return True

        if self.handler.path in ("/api/version", "/v1/api/version"):
            self.handler._send_json(200, {"version": "0.13.4"})
            return True

        if self.handler.path in ("/api/ps", "/v1/api/ps"):
            self.handler._send_json(200, {"models": self.ollama_models()})
            return True

        return False

    def handle_ready_get(self):
        if self.handler.path in ("/ready", "/qz/ready"):
            snapshot = self.status_snapshot()
            self._emit("status_snapshot", self.status_summary(self.handler.path, snapshot=snapshot))
            self.handler._send_json(200 if snapshot["ready"] else 503, snapshot)
            return True
        if self.handler.path in ("/qz/status",):
            snapshot = self.status_snapshot()
            self._emit("status_snapshot", self.status_summary(self.handler.path, snapshot=snapshot))
            try:
                snapshot["vram"] = get_cached_vram_snapshot(handler=self.handler)
            except Exception:
                pass
            self.handler._send_json(200, snapshot)
            return True
        return False

    def handle_ollama_post(self):
        if self.handler.path not in ("/api/pull", "/v1/api/pull", "/api/show", "/v1/api/show"):
            return False

        length = int(self.handler.headers.get("Content-Length", "0") or "0")
        raw = self.handler.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

        model = body.get("model") or body.get("name") or ""

        if self.handler.path in ("/api/pull", "/v1/api/pull"):
            # Pretend the model is already installed.
            # Ollama permits non-stream response {"status":"success"} when stream=false;
            # Codex only needs pull to not fail.
            self.handler._send_json(200, {"status": "success"})
            return True

        if self.handler.path in ("/api/show", "/v1/api/show"):
            self.handler._send_json(200, {
                "modelfile": f"FROM {model}\n",
                "parameters": "",
                "template": "",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "35B-A3B",
                    "quantization_level": "Q4_K_M+TurboQuant",
                },
                "model_info": {
                    "general.architecture": "qwen",
                    "general.name": model,
                    "qwen36turbo.context_length": 262144,
                },
                "capabilities": ["completion", "tools", "thinking"],
            })
            return True

        return False
