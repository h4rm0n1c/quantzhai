#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path

try:
    from .qz_reasoning_policy import (
        HARD_BUDGET_POLICY_MODE,
        apply_reasoning_policy,
        hard_budget_for_level,
        normalize_reasoning_level,
        reasoning_policy_for_level,
        reasoning_policy_mode,
    )
    from .qz_runtime_io import read_json, runtime_state_path, write_json
except ImportError:
    from qz_reasoning_policy import (
        HARD_BUDGET_POLICY_MODE,
        apply_reasoning_policy,
        hard_budget_for_level,
        normalize_reasoning_level,
        reasoning_policy_for_level,
        reasoning_policy_mode,
    )
    from qz_runtime_io import read_json, runtime_state_path, write_json


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


def reasoning_budget_for_level(level: str | None) -> int:
    return hard_budget_for_level(level)


def reasoning_budget_map_for_entry(entry: dict | None):
    entry = entry if isinstance(entry, dict) else {}
    budgets = {}
    supported = entry.get("supported_reasoning_levels")
    if isinstance(supported, list):
        for item in supported:
            if not isinstance(item, dict):
                continue
            effort = normalize_reasoning_level(item.get("effort"))
            if effort not in {"low", "medium", "high", "xhigh"}:
                continue
            budget = item.get("budget_tokens")
            if budget is None:
                budget = item.get("thinking_budget_tokens")
            if budget is None:
                budget = hard_budget_for_level(effort)
            try:
                budgets[effort] = int(budget)
            except Exception:
                budgets[effort] = hard_budget_for_level(effort)
    return budgets


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
        selected = selected if isinstance(selected, dict) else {}
        payload = {
            "selected_key": entry_identity(selected),
            "selected_backend_id": selected.get("backend_id") or entry_identity(selected),
            "selected_label": selected.get("label") or "",
            "selected_path": selected.get("path") or "",
            "selected_reason": reason or "",
            "source": source or "",
            "updated_at": time.time(),
        }
        try:
            write_json(self.model_state_path(), payload)
        except Exception:
            pass

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
        except Exception:
            pass

    def load_backend_state(self):
        payload = read_json(self.backend_state_path(), default={})
        return payload if isinstance(payload, dict) else {}

    def backend_context_length(self, backend_models=None, selected_backend_id: str = ""):
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
                    return context
        state = self.load_backend_state()
        context = self._parse_context_length(state.get("backend_context_length"), None)
        if context is not None:
            return context
        return self._parse_context_length(os.environ.get("QZ_CONTEXT"), 131072)

    def selected_context_length(self, selected: dict | None = None):
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        if isinstance(selected, dict):
            runtime_context = self._parse_context_length(selected.get("runtime_context_length"), None)
            if runtime_context is not None:
                return runtime_context
        return self._parse_context_length(os.environ.get("QZ_CONTEXT"), 131072)

    def _emit(self, event_type: str, payload: dict | None = None):
        telemetry = getattr(self.handler, "telemetry", None)
        if telemetry is None:
            return
        try:
            telemetry.emit(event_type, payload if isinstance(payload, dict) else {})
        except Exception:
            pass

    def backend_models(self):
        try:
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
        model_drift = (
            model_state.get("selected_key") != selected_key
            or model_state.get("selected_backend_id") != selected_backend_id
            or model_state.get("selected_path") != (selected.get("path") or "")
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
        if model_drift:
            self._persist_model_state(selected, reason="status reconciliation", source="status_snapshot")
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

    def backend_health(self):
        resp = self.handler._backend().get_health(timeout=10)
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

    def selected_thinking_budget_tokens(self, selected: dict | None = None):
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        level = self.selected_reasoning_level(selected)
        budgets = reasoning_budget_map_for_entry(selected)
        if level in budgets:
            return budgets[level]
        if selected is not None:
            for fallback in ("medium", "high", "low", "xhigh"):
                if fallback in budgets:
                    return budgets[fallback]
        return reasoning_budget_for_level(level)

    def selected_reasoning_policy(self, selected: dict | None = None, body: dict | None = None):
        selected = selected if isinstance(selected, dict) else self.selected_model_entry()
        default_level = self.selected_reasoning_level(selected)
        level = default_level
        if isinstance(body, dict):
            reasoning = body.get("reasoning")
            if isinstance(reasoning, dict) and reasoning.get("effort"):
                level = normalize_reasoning_level(reasoning.get("effort"))
            elif body.get("reasoning_effort"):
                level = normalize_reasoning_level(body.get("reasoning_effort"))
        policy = reasoning_policy_for_level(level)
        policy["mode"] = reasoning_policy_mode()
        if policy["mode"] == HARD_BUDGET_POLICY_MODE:
            policy["thinking_budget_tokens"] = hard_budget_for_level(level, selected)
        else:
            policy["thinking_budget_tokens"] = None
        return policy

    def apply_reasoning_policy(self, body: dict, selected: dict | None = None):
        policy = self.selected_reasoning_policy(selected, body)
        result = apply_reasoning_policy(body, policy.get("effort"), policy.get("mode"))
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        qz_reasoning = metadata.get("qz_reasoning") if isinstance(metadata.get("qz_reasoning"), dict) else {}
        qz_reasoning["thinking_budget_tokens"] = policy.get("thinking_budget_tokens")
        metadata["qz_reasoning"] = qz_reasoning
        result["metadata"] = metadata
        if policy.get("mode") == HARD_BUDGET_POLICY_MODE:
            result["thinking_budget_tokens"] = policy.get("thinking_budget_tokens")
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
        selected = self.selected_model_entry()
        backend_models = self.backend_models()
        health_status, health_body = self.backend_health()
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
        reasoning_level = self.selected_reasoning_level(selected)
        reasoning_policy = self.selected_reasoning_policy(selected)
        selected_context_length = self.selected_context_length(selected)
        backend_context_length = self.backend_context_length(backend_models, selected_backend_id)
        self._reconcile_status_state(
            selected,
            health_status,
            selected_backend_id,
            selected_context_length,
            backend_context_length,
            backend_state,
            loaded_model,
        )
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
                "selected_thinking_budget_tokens": reasoning_policy.get("thinking_budget_tokens"),
                "selected_context_length": selected_context_length,
                "backend_context_length": backend_context_length,
                "restart_required": selected_context_length != backend_context_length,
                "loaded_model": loaded_model,
                "loaded_count": len(loaded_models),
                "loaded_models": loaded_models,
                "models": backend_models,
            },
            "load": {
                "state": load_state,
                "started_at": load_started_at,
                "finished_at": load_finished_at,
                "error": load_error,
                "model": load_model,
            },
            "timestamp": time.time(),
        }

    def status_summary(self, reason: str = ""):
        snapshot = self.status_snapshot()
        selected = snapshot.get("selected") or {}
        backend = snapshot.get("backend") or {}
        load = snapshot.get("load") or {}
        health = snapshot.get("health") or {}
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
            "thinking_budget_tokens": backend.get("selected_thinking_budget_tokens"),
            "selected_context_length": backend.get("selected_context_length"),
            "backend_context_length": backend.get("backend_context_length"),
            "loaded": loaded_ids,
            "loaded_model": loaded_model,
            "loaded_count": backend.get("loaded_count") or len(loaded_ids),
            "load_state": load.get("state") or "unknown",
            "health_status": health.get("status"),
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
            "backend_context_length": backend.get("backend_context_length"),
            "restart_required": bool(backend.get("restart_required")),
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
        cls.model_load_model = model_id
        cls.model_load_started_at = time.time()
        cls.model_load_finished_at = None
        cls.model_load_error = None

        existing_state = self.backend_model_state(model_id)
        if existing_state == "loaded":
            cls.model_load_state = "ready"
            cls.model_load_finished_at = time.time()
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
            cls.model_load_state = "loading"
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
            current_context_length = self.backend_context_length()
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

            self.load_backend_model(target_backend_id, wait=True)

            backend_state = self.backend_model_state(target_backend_id)
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
        catalog = self.handler._model_catalog()
        return catalog.to_v1_models(backend_models=self.backend_models())

    def ollama_models(self):
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
            self._emit("status_snapshot", self.status_summary(self.handler.path))
            self.handler._send_json(200 if snapshot["ready"] else 503, snapshot)
            return True
        if self.handler.path in ("/qz/status",):
            snapshot = self.status_snapshot()
            self._emit("status_snapshot", self.status_summary(self.handler.path))
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
                    "qwen36turbo.context_length": 131072,
                },
                "capabilities": ["completion", "tools", "thinking"],
            })
            return True

        return False
