#!/usr/bin/env python3
import json
import time

try:
    from .qz_proxy_config import MODEL_BUDGETS
except ImportError:
    from qz_proxy_config import MODEL_BUDGETS


PROFILE_ALIASES = {
    "QwenZhai-low": "low",
    "QwenZhai-medium": "medium",
    "QwenZhai-high": "high",
    "QwenZhai-max": "max",
    "QwenZhai-caveman": "caveman",
    "Qwen3.6Turbo-low": "low",
    "Qwen3.6Turbo-medium": "medium",
    "Qwen3.6Turbo-high": "high",
    "Qwen3.6Turbo-max": "max",
    "Qwen3.6Turbo-caveman": "caveman",
}


class ModelRouter:
    def __init__(self, handler):
        self.handler = handler

    def _emit(self, event_type: str, payload: dict | None = None):
        telemetry = getattr(self.handler, "telemetry", None)
        if telemetry is None:
            return
        try:
            telemetry.emit(event_type, payload if isinstance(payload, dict) else {})
        except Exception:
            pass

    def backend_models(self):
        payload = self.handler._backend().get_models()

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
        return backend

    def backend_model_entry(self, model_id: str):
        if not model_id:
            return {}
        return self.backend_models().get(model_id, {})

    def backend_model_state(self, model_id: str):
        entry = self.backend_model_entry(model_id)
        return entry.get("state") or "unknown"

    def backend_health(self):
        resp = self.handler._backend().get_health(timeout=10)
        try:
            body = json.loads(resp.data.decode("utf-8")) if resp.data else {}
        except Exception:
            body = {}
        return resp.status, body

    def unload_backend_model(self, model_id: str, wait: bool = False, timeout: float = 120):
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
        return selected.get("backend_id") or selected.get("key") or ""

    def profile_model_entry(self, requested_model: str):
        catalog = self.handler._model_catalog()
        profile_key = PROFILE_ALIASES.get(requested_model, requested_model)
        if not isinstance(profile_key, str) or not profile_key:
            return None, ""
        manifest = getattr(catalog, "manifest", {})
        if not isinstance(manifest, dict):
            manifest = {}
        profile_models = manifest.get("profile_models", {})
        if not isinstance(profile_models, dict):
            return None, ""
        target = profile_models.get(profile_key) or profile_models.get(requested_model)
        if not isinstance(target, str) or not target:
            return None, ""
        selected, reason = catalog.resolve(query=target)
        if selected is None:
            return None, f"profile {requested_model} -> {target} (missing)"
        return selected, f"profile {requested_model} -> {target}"

    def status_snapshot(self):
        selected = self.selected_model_entry()
        backend_models = self.backend_models()
        health_status, health_body = self.backend_health()
        selected_key = selected["key"] if selected else None
        selected_backend_id = self.selected_backend_id()
        backend_entry = backend_models.get(selected_backend_id or selected_key or "", {})
        backend_state = backend_entry.get("state") or "unknown"
        load_state = getattr(self.handler, "model_load_state", None)
        if load_state in (None, "", "idle"):
            load_state = backend_state
        ready = health_status == 200 and backend_state == "loaded"
        return {
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
                "models": backend_models,
            },
            "load": {
                "state": load_state,
                "started_at": getattr(self.handler, "model_load_started_at", None),
                "finished_at": getattr(self.handler, "model_load_finished_at", None),
                "error": getattr(self.handler, "model_load_error", None),
                "model": getattr(self.handler, "model_load_model", None),
            },
            "timestamp": time.time(),
        }

    def status_summary(self, reason: str = ""):
        snapshot = self.status_snapshot()
        selected = snapshot.get("selected") or {}
        backend = snapshot.get("backend") or {}
        load = snapshot.get("load") or {}
        health = snapshot.get("health") or {}
        return {
            "reason": reason,
            "ready": snapshot.get("ready", False),
            "router_mode": snapshot.get("router_mode", False),
            "selected": backend.get("selected_backend_id") or selected.get("key") or "",
            "selected_key": backend.get("selected_key") or selected.get("key") or "",
            "selected_state": backend.get("selected_state") or "unknown",
            "load_state": load.get("state") or "unknown",
            "health_status": health.get("status"),
            "timestamp": snapshot.get("timestamp"),
        }

    def runtime_state_payload(self, requested_model: str = ""):
        snapshot = self.status_snapshot()
        selected = snapshot.get("selected") or {}
        backend = snapshot.get("backend") or {}
        load = snapshot.get("load") or {}
        profile = requested_model or selected.get("label") or selected.get("key") or ""
        selected_key = backend.get("selected_key") or selected.get("key") or ""
        return {
            "ready": snapshot["ready"],
            "load_state": load.get("state") or "unknown",
            "profile": profile,
            "selected": selected_key,
        }

    def runtime_state_block(self, requested_model: str = ""):
        state = self.runtime_state_payload(requested_model)
        ready = "1" if state["ready"] else "0"
        return (
            f'<QZSTATE v=1 ready={ready} '
            f'load={state["load_state"]} prof={state["profile"]} '
            f'sel={state["selected"]}>'
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
                body["instructions"] = block + "\n\n" + existing.strip()
        else:
            body["instructions"] = block
        return body

    def load_backend_model(self, model_id: str, wait: bool = False, timeout: float = 120):
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

    def resolve_model_selection(self, requested_model):
        catalog = self.handler._model_catalog()
        if not requested_model or requested_model in MODEL_BUDGETS:
            selected, reason = self.profile_model_entry(requested_model or "")
            if selected is None:
                selected = catalog.selected or (catalog.entries[0] if catalog.entries else None)
                reason = f"profile {requested_model or 'default'}"
        else:
            selected, reason = catalog.resolve(query=requested_model)
        if selected is None and catalog.entries:
            selected = catalog.entries[0]
            reason = reason or "catalog fallback"
        if selected is None:
            return None, reason
        target_backend_id = selected.get("backend_id") or selected.get("key")
        current_backend_id = self.selected_backend_id()
        if current_backend_id and current_backend_id != target_backend_id:
            current_state = self.backend_model_state(current_backend_id)
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

        catalog.selected = selected
        catalog.reason = reason
        self._emit("model_selected", {
            "requested": requested_model,
            "selected": selected.get("key"),
            "backend_id": selected.get("backend_id") or selected.get("key"),
            "target_backend_id": target_backend_id,
            "reason": reason,
            "budget": MODEL_BUDGETS.get(requested_model, MODEL_BUDGETS.get("Qwen3.6Turbo", 256)),
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

        model = body.get("model") or body.get("name") or "Qwen3.6Turbo-medium"

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
