"""Backend lifecycle manager for the QuantZhai proxy control plane.

Owns Docker container start/stop/restart and /health polling for the
llama.cpp backend.  Proxied through /qz/backend/* endpoints; reported
in /qz/control-plane backend_manager section.

This module is import-safe: no Docker or network calls at import time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Phase constants
# ---------------------------------------------------------------------------

PHASE_DISABLED          = "disabled"
PHASE_IDLE              = "idle"
PHASE_START_REQUESTED   = "start_requested"
PHASE_STARTING          = "starting"
PHASE_RUNNING           = "running"
PHASE_HEALTHY           = "healthy"
PHASE_FAILED            = "failed"
PHASE_STOPPING          = "stopping"
PHASE_STOPPED           = "stopped"
PHASE_UNKNOWN           = "unknown"

VALID_PHASES = frozenset({
    PHASE_DISABLED, PHASE_IDLE, PHASE_START_REQUESTED, PHASE_STARTING,
    PHASE_RUNNING, PHASE_HEALTHY, PHASE_FAILED, PHASE_STOPPING,
    PHASE_STOPPED, PHASE_UNKNOWN,
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_bool_env(value: Any, default: bool = False) -> bool:
    """Return True if value is a truthy env-style string (1/true/yes/on)."""
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _iso_now() -> str:
    """Return the current UTC time as a compact ISO 8601 string."""
    t = time.gmtime()
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"


def _safe_str(value: Any, fallback: str = "") -> str:
    """Return str(value) or fallback if value is None/empty after strip."""
    if value is None:
        return fallback
    s = str(value).strip()
    return s if s else fallback


# ---------------------------------------------------------------------------
# BackendState
# ---------------------------------------------------------------------------

@dataclass
class BackendState:
    """Snapshot of backend manager state — safe to serialise and expose."""

    phase: str = PHASE_UNKNOWN
    container_name: str = ""
    container_running: bool | None = None
    backend_health_ok: bool | None = None
    last_start_requested_at: str | None = None
    last_started_at: str | None = None
    last_healthy_at: str | None = None
    last_stopped_at: str | None = None
    last_error: str | None = None
    autostart: bool = True

    def as_dict(self) -> dict:
        """Return a safe dict — no secrets, no paths, no Docker commands."""
        return {
            "phase":                    self.phase,
            "container_name":           self.container_name,
            "container_running":        self.container_running,
            "backend_health_ok":        self.backend_health_ok,
            "last_start_requested_at":  self.last_start_requested_at,
            "last_started_at":          self.last_started_at,
            "last_healthy_at":          self.last_healthy_at,
            "last_stopped_at":          self.last_stopped_at,
            "last_error":               self.last_error,
            "autostart":                self.autostart,
        }


# ---------------------------------------------------------------------------
# BackendManager
# ---------------------------------------------------------------------------

class BackendManager:
    """Owns Docker lifecycle for the llama.cpp backend.

    Construction is side-effect-free.  Call start() / stop() / restart()
    explicitly, or set autostart=True and call begin_autostart() from the
    proxy main thread after the HTTP server is bound.
    """

    def __init__(
        self,
        *,
        docker_cmd: str = "docker",
        container_name: str = "qwen36turbo",
        image: str = "thetom-llama-cpp-turboquant:cuda-server",
        model_dir: str = "/models",
        server_host: str = "127.0.0.1",
        server_port: int = 18084,
        context: int = 262144,
        parallel: int = 1,
        batch: int = 4096,
        ubatch: int = 512,
        threads: int = 12,
        thread_batch: int = 12,
        tensor_split: str = "9,17",
        main_gpu: int = 0,
        cache_ram: int = 8192,
        cache_reuse: int = 256,
        kv_key: str = "q8_0",
        kv_value: str = "turbo3",
        reasoning_budget: str = "-1",
        reasoning_budget_message: str = "I have reasoned long enough. Let me now produce my final answer.",
        spec_default: bool = False,
        autostart: bool = True,
        health_check_interval: float = 10.0,
        health_check_timeout: float = 120.0,
        operational_store: Any = None,
    ) -> None:
        # Config — private; never exposed in snapshot or logs
        self._docker_cmd             = _safe_str(docker_cmd, "docker")
        self._container_name         = _safe_str(container_name, "qwen36turbo")
        self._image                  = _safe_str(image)
        self._model_dir              = _safe_str(model_dir)
        self._server_host            = _safe_str(server_host, "127.0.0.1")
        self._server_port            = int(server_port)
        self._context                = int(context)
        self._parallel               = int(parallel)
        self._batch                  = int(batch)
        self._ubatch                 = int(ubatch)
        self._threads                = int(threads)
        self._thread_batch           = int(thread_batch)
        self._tensor_split           = _safe_str(tensor_split, "9,17")
        self._main_gpu               = int(main_gpu)
        self._cache_ram              = int(cache_ram)
        self._cache_reuse            = int(cache_reuse)
        self._kv_key                 = _safe_str(kv_key, "q8_0")
        self._kv_value               = _safe_str(kv_value, "turbo3")
        self._reasoning_budget       = _safe_str(reasoning_budget, "-1")
        self._reasoning_budget_msg   = _safe_str(reasoning_budget_message)
        self._spec_default           = bool(spec_default)
        self._health_check_interval  = float(health_check_interval)
        self._health_check_timeout   = float(health_check_timeout)
        self._operational_store      = operational_store

        # State
        initial_phase = PHASE_IDLE if autostart else PHASE_DISABLED
        self._state = BackendState(
            phase=initial_phase,
            container_name=self._container_name,
            autostart=bool(autostart),
        )

    # ------------------------------------------------------------------
    # Public state access
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a safe state snapshot — no secrets, paths, or Docker commands."""
        return self._state.as_dict()

    @property
    def phase(self) -> str:
        return self._state.phase

    # ------------------------------------------------------------------
    # Docker command builders (pure — no subprocess calls)
    # ------------------------------------------------------------------

    def build_backend_args(self) -> list[str]:
        """Build the llama.cpp backend argument list.

        Exact port of scripts/qz-up backend_args array (lines 88-119).
        """
        args = [
            "--host", "0.0.0.0",
            "--port", "8080",
            "-ngl", "999",
            "-c", str(self._context),
            "-np", str(self._parallel),
            "-b", str(self._batch),
            "-ub", str(self._ubatch),
            "-t", str(self._threads),
            "-tb", str(self._thread_batch),
            "-fa", "on",
            "--split-mode", "layer",
            "--tensor-split", self._tensor_split,
            "--main-gpu", str(self._main_gpu),
            "--kv-unified",
            "--reasoning", "on",
            "--reasoning-budget", self._reasoning_budget,
            "--reasoning-budget-message", self._reasoning_budget_msg,
            "--cache-ram", str(self._cache_ram),
            "--cache-reuse", str(self._cache_reuse),
            "--mlock",
            "-ctk", self._kv_key,
            "-ctv", self._kv_value,
            "--metrics",
            "--reasoning-format", "deepseek",
        ]
        if self._spec_default:
            args.append("--spec-default")
        return args

    def build_docker_run_args(self) -> list[str]:
        """Build the full `docker run` invocation.

        Exact port of scripts/qz-up qz_docker run call (lines 121-130).
        Includes docker command + all container flags + image + backend args.
        """
        cmd = self._docker_cmd.split()  # handle "sudo docker"
        container_flags = [
            "run", "-d",
            "--name", self._container_name,
            "--gpus", "all",
            "--cap-add", "IPC_LOCK",
            "--ulimit", "memlock=-1:-1",
            "-p", f"{self._server_port}:8080",
            "--mount", f"type=bind,src={self._model_dir},dst=/models,readonly",
            self._image,
            "--models-dir", "/models",
        ]
        return cmd + container_flags + self.build_backend_args()

    # ------------------------------------------------------------------
    # OperationalStore event helper
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict | None = None) -> None:
        """Record a backend lifecycle event if an operational store is attached."""
        if self._operational_store is None:
            return
        try:
            self._operational_store.record_startup_event(
                phase=event_type,
                payload={"container_name": self._container_name, **(payload or {})},
                source="backend_manager",
            )
        except Exception:
            pass
