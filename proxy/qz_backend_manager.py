"""Backend lifecycle manager for the QuantZhai proxy control plane.

Owns Docker container start/stop/restart and /health polling for the
llama.cpp backend.  Proxied through /qz/backend/* endpoints; reported
in /qz/control-plane backend_manager section.

This module is import-safe: no Docker or network calls at import time.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


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

# Phases that mean "not running" — start() is allowed from these.
_STARTABLE_PHASES = frozenset({
    PHASE_IDLE, PHASE_FAILED, PHASE_STOPPED, PHASE_UNKNOWN,
})
# Phases that mean "already running" — start() should decline.
_RUNNING_PHASES = frozenset({
    PHASE_START_REQUESTED, PHASE_STARTING, PHASE_RUNNING, PHASE_HEALTHY,
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


def _default_runner(args: list[str], timeout: float | None = None) -> tuple[int, str, str]:
    """Default subprocess runner. Returns (returncode, stdout, stderr)."""
    import subprocess
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as exc:
        return -1, "", str(exc)


def _default_health_checker(url: str, timeout: float = 3.0) -> bool:
    """Check backend /health. Returns True when the URL returns HTTP 200."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# GPU log scanner — shared by startup check and post-load check
# ---------------------------------------------------------------------------

_GPU_HARD_FAIL_PATTERNS: tuple[str, ...] = (
    "ggml_cuda_init: failed to initialize CUDA",
    "no usable GPU found",
    "--gpu-layers option will be ignored",
    "compiled without support for GPU offload",
)
_GPU_SUCCESS_PATTERNS: tuple[str, ...] = (
    r"offloaded \d+/\d+ layers to GPU",
    r"offloaded .* layers to GPU",
    r"CUDA\d+ model buffer size",
    r"CUDA_Host model buffer size",
)
_GPU_CPU_MAPPED = "CPU_Mapped model buffer size"


def _scan_gpu_log_lines(logs: str) -> tuple[str, str | None]:
    """Scan a block of log text for GPU offload signals.

    Uses "latest relevant signal wins" so that small CPU_Mapped residual
    buffers that appear alongside CUDA/offload lines do not falsely trigger
    cpu_fallback.

    Returns (state, error_msg):
      'gpu'          — GPU offload confirmed
      'cpu_fallback' — CPU_Mapped present, no GPU success anywhere
      'failed'       — hard CUDA init/compile failure
      'unknown'      — no recognisable signal
    """
    last_fail = -1
    last_fail_msg: str | None = None
    last_success = -1
    last_cpu_mapped = -1

    for i, line in enumerate(logs.splitlines()):
        for pat in _GPU_HARD_FAIL_PATTERNS:
            if pat in line:
                last_fail = i
                last_fail_msg = pat
        for pat in _GPU_SUCCESS_PATTERNS:
            if re.search(pat, line):
                last_success = i
        if _GPU_CPU_MAPPED in line:
            last_cpu_mapped = i

    if last_success >= 0 and last_success >= last_fail:
        return "gpu", None
    if last_fail >= 0 and last_fail > last_success:
        return "failed", f"GPU not used: {last_fail_msg!r} in container logs"
    if last_cpu_mapped >= 0:
        return "cpu_fallback", f"GPU not used: {_GPU_CPU_MAPPED!r} in container logs"
    return "unknown", None


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
    gpu_required: bool = True
    gpu_offload_state: str = "unknown"  # unknown | gpu | cpu_fallback | failed | unknown_after_retries
    gpu_observed: bool | None = None   # True=GPU confirmed, False=CPU-only confirmed, None=not yet checked
    gpu_error: str | None = None
    # Direct launch metadata.  ``launch_model_*`` are observation-only
    # descriptors of the next docker run argv; the model selection authority
    # remains in qz.model_state.v1.
    backend_model_mode: str = "direct"
    launch_model_key: str = ""
    launch_model_backend_id: str = ""
    launch_model_path_basename: str = ""
    launch_model_error: str | None = None
    runtime_failure_result: str = ""
    runtime_failure_error: str = ""
    runtime_failure_error_type: str = ""
    runtime_failure_at: str | None = None
    backend_died_after_healthy: bool = False

    def as_dict(self) -> dict:
        """Return a safe dict — no secrets, no paths, no Docker commands."""
        return {
            "phase":                       self.phase,
            "container_name":              self.container_name,
            "container_running":           self.container_running,
            "backend_health_ok":           self.backend_health_ok,
            "last_start_requested_at":     self.last_start_requested_at,
            "last_started_at":             self.last_started_at,
            "last_healthy_at":             self.last_healthy_at,
            "last_stopped_at":             self.last_stopped_at,
            "last_error":                  self.last_error,
            "autostart":                   self.autostart,
            "gpu_required":                self.gpu_required,
            "gpu_offload_state":           self.gpu_offload_state,
            "gpu_observed":                self.gpu_observed,
            "gpu_error":                   self.gpu_error,
            "backend_model_mode":          self.backend_model_mode,
            "launch_model_key":            self.launch_model_key,
            "launch_model_backend_id":     self.launch_model_backend_id,
            "launch_model_path_basename":  self.launch_model_path_basename,
            "launch_model_error":          self.launch_model_error,
            "runtime_failure_result":      self.runtime_failure_result,
            "runtime_failure_error":       self.runtime_failure_error,
            "runtime_failure_error_type":  self.runtime_failure_error_type,
            "runtime_failure_at":          self.runtime_failure_at,
            "backend_died_after_healthy":  self.backend_died_after_healthy,
        }


# ---------------------------------------------------------------------------
# BackendManager
# ---------------------------------------------------------------------------

class BackendManager:
    """Manages the TurboQuant llama.cpp Docker container in router mode.

    The container is a persistent service — it starts once and stays alive.
    Model switching uses HTTP (load_model_http / unload_model_http), never
    a container restart.  The proxy reconnects to an already-running container
    on restart rather than killing it.

    Lifecycle:
      start()           — start the container if not already running
      stop()            — emergency teardown (qz-down); not for model switches
      load_model_http() — load a model into the running router via HTTP
      unload_model_http() — unload a model from the running router via HTTP

    restart() and _do_restart() have been removed: they embodied the
    pre-router-mode "kill-and-restart-on-model-switch" pattern that caused
    CUDA_ERROR_INITIALIZATION_ERROR by restarting the container before the GPU
    driver had released the previous session's context.
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
        require_gpu: bool = True,
        gpu_log_tail: int = 1000,
        gpu_check_retry_count: int = 60,
        gpu_check_retry_delay: float = 2.0,
        launch_model_key: str = "",
        launch_model_backend_id: str = "",
        launch_model_path_basename: str = "",
        health_check_interval: float = 5.0,
        health_check_timeout: float = 120.0,
        autostart_delay: float = 0.5,
        operational_store: Any = None,
        runner: Callable[..., tuple[int, str, str]] | None = None,
        health_checker: Callable[[str, float], bool] | None = None,
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
        self._require_gpu            = bool(require_gpu)
        self._gpu_log_tail           = int(gpu_log_tail)
        self._gpu_check_retry_count  = max(0, int(gpu_check_retry_count))
        self._gpu_check_retry_delay  = max(0.0, float(gpu_check_retry_delay))
        self._launch_model_key       = _safe_str(launch_model_key)
        self._launch_model_backend_id = _safe_str(launch_model_backend_id)
        self._launch_model_path_basename = _safe_str(launch_model_path_basename)
        self._health_check_interval  = float(health_check_interval)
        self._health_check_timeout   = float(health_check_timeout)
        self._autostart_delay        = float(autostart_delay)
        self._operational_store      = operational_store
        self._runner                 = runner or _default_runner
        self._health_checker         = health_checker or _default_health_checker

        # Threading
        self._lock = threading.Lock()
        self._busy = False           # True while a lifecycle operation is running

        # Load tracking — records the last time load_model_http() was called
        # so callers can detect an in-flight async model load before the
        # model inventory (/v1/models) confirms the new model.
        self._last_load_attempted_at: float | None = None

        # (Crash tracking was here — now owned by the C++ router via the
        # recovering flag, exit_code, last_error, and reload_attempts in
        # /v1/models.  The proxy reads these signals directly.)

        # State
        initial_phase = PHASE_IDLE if autostart else PHASE_DISABLED
        self._state = BackendState(
            phase=initial_phase,
            container_name=self._container_name,
            autostart=bool(autostart),
            gpu_required=self._require_gpu,
            backend_model_mode="direct",
            launch_model_key=self._launch_model_key,
            launch_model_backend_id=self._launch_model_backend_id,
            launch_model_path_basename=self._launch_model_path_basename,
        )

    # ------------------------------------------------------------------
    # Public state access
    # ------------------------------------------------------------------

    def llm_base_url(self) -> str:
        """Return the canonical host-side llama.cpp base URL managed by BackendManager.

        For in-process proxy use only (e.g. Zenkai v3 compaction).  The URL is
        never exposed in snapshots, logs, or /qz/* endpoints — callers that need
        it for network access must call this method directly.
        """
        return f"http://{self._server_host}:{self._server_port}"

    def snapshot(self) -> dict:
        """Return a safe state snapshot — no secrets, paths, or Docker commands."""
        with self._lock:
            return self._state.as_dict()

    @property
    def phase(self) -> str:
        with self._lock:
            return self._state.phase

    @property
    def backend_model_mode(self) -> str:
        return "direct"

    def set_launch_model(
        self,
        *,
        key: str,
        backend_id: str,
        path_basename: str,
    ) -> None:
        """Set the model the next backend launch or HTTP load will target."""
        with self._lock:
            self._launch_model_key = _safe_str(key)
            self._launch_model_backend_id = _safe_str(backend_id)
            self._launch_model_path_basename = _safe_str(path_basename)
            self._state.launch_model_key = self._launch_model_key
            self._state.launch_model_backend_id = self._launch_model_backend_id
            self._state.launch_model_path_basename = self._launch_model_path_basename
            self._state.launch_model_error = None

    def record_runtime_failure(self, *, error: str, error_type: str = "runtime_failure") -> None:
        """Record that a healthy direct-launched backend died during use."""
        with self._lock:
            had_been_healthy = bool(self._state.last_healthy_at)
            self._state.runtime_failure_result = "failed"
            self._state.runtime_failure_error = _safe_str(error)[:500]
            self._state.runtime_failure_error_type = _safe_str(error_type, "runtime_failure")
            self._state.runtime_failure_at = _iso_now()
            self._state.backend_died_after_healthy = had_been_healthy
            self._state.phase = PHASE_FAILED
            self._state.backend_health_ok = False
            self._state.container_running = False
            self._state.last_error = self._state.runtime_failure_error

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    def begin_autostart(self) -> None:
        """Enqueue an autostart if enabled. Called once after proxy bind. Non-blocking."""
        with self._lock:
            if self._state.phase != PHASE_IDLE or not self._state.autostart:
                return
        threading.Thread(
            target=self._autostart_with_delay,
            daemon=True,
            name="qz-backend-autostart",
        ).start()

    def start(self) -> dict:
        """Request a backend start. Returns immediately with a status dict."""
        with self._lock:
            phase = self._state.phase
            if phase == PHASE_DISABLED:
                return {"ok": False, "action": "start",
                        "error": "backend autostart is disabled",
                        "backend_manager": self._state.as_dict()}
            if phase in _RUNNING_PHASES or self._busy:
                return {"ok": False, "action": "start",
                        "error": f"backend is already {phase}",
                        "backend_manager": self._state.as_dict()}

            self._busy = True
            self._state.phase = PHASE_START_REQUESTED
            self._state.last_start_requested_at = _iso_now()
            self._state.last_error = None
        self._emit("backend_start_requested")
        threading.Thread(
            target=self._do_start,
            daemon=True,
            name="qz-backend-start",
        ).start()
        return {"ok": True, "action": "start",
                "backend_manager": self.snapshot()}

    def stop(self) -> dict:
        """Request a backend stop. Returns immediately with a status dict."""
        with self._lock:
            phase = self._state.phase
            if phase == PHASE_DISABLED:
                return {"ok": False, "action": "stop",
                        "error": "backend is disabled",
                        "backend_manager": self._state.as_dict()}
            if phase in (PHASE_STOPPING, PHASE_STOPPED, PHASE_IDLE):
                return {"ok": False, "action": "stop",
                        "error": f"backend is already {phase}",
                        "backend_manager": self._state.as_dict()}
            if self._busy:
                return {"ok": False, "action": "stop",
                        "error": f"another operation is in progress ({phase})",
                        "backend_manager": self._state.as_dict()}
            self._busy = True
            self._state.phase = PHASE_STOPPING
        self._emit("backend_stop_requested")
        threading.Thread(
            target=self._do_stop,
            daemon=True,
            name="qz-backend-stop",
        ).start()
        return {"ok": True, "action": "stop",
                "backend_manager": self.snapshot()}

    # restart() removed: in router mode the container is a persistent service.
    # Model switching uses load_model_http() / unload_model_http() — the container
    # is never killed for a model switch.  Callers that previously used restart()
    # should use start() (container absent) or load_model_http() (model switch).

    def status(self) -> dict:
        """Return the current state snapshot."""
        return {"ok": True, "action": "status",
                "backend_manager": self.snapshot()}

    # ------------------------------------------------------------------
    # Docker command builders (pure — no subprocess calls)
    # ------------------------------------------------------------------

    def build_backend_args(self) -> list[str]:
        """Build the llama.cpp backend argument list."""
        args: list[str] = [
            "--models-dir", "/models",
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
            # Reasoning config is set via models-preset.ini [*] global section.
            # DO NOT hardcode --reasoning here — CLI args take highest precedence
            # in the router's preset cascade (server-models.cpp:304-306) and
            # would override per-model overrides from the INI file.
            #--reasoning "on" (moved to models-preset.ini [*])
            "--cache-ram", str(self._cache_ram),
            "--cache-reuse", str(self._cache_reuse),
            "--mlock",
            "-ctk", self._kv_key,
            "-ctv", self._kv_value,
            "--metrics",
            # --reasoning-format was moved to models-preset.ini [*] for
            # the same CLI-precedence reason as --reasoning above.
            # HTTP read timeout: how long the router's proxy handler waits for a
            # child to respond.  Default is 3600s (1 hour) in llama.cpp — far too
            # long for our setup.  When a child crashes mid-request, the handler
            # thread blocks in the proxy constructor until this timeout fires,
            # starving the thread pool and making /v1/models unresponsive.
            # 300s (5 min) allows long 256K inference while bounding damage from
            # orphaned proxy connections.
            "--timeout", "300",
        ]
        if self._spec_default:
            args.append("--spec-default")
        # Per-model spec overrides: if models-preset.ini exists in the models dir,
        # pass it to the router so each model section can add e.g. spec-type = draft-mtp
        # without affecting models that don't have MTP heads.
        import os as _os
        # Router-side model load timeout: how long the router waits for a child
        # process to load before giving up.  Uses the same env as the proxy's
        # outer timeout but with a larger default (300 vs 120) so the proxy
        # always waits longer than the router.
        args += ["--model-load-timeout", str(_os.environ.get("QZ_ROUTER_LOAD_TIMEOUT", _os.environ.get("QZ_MODEL_LOAD_TIMEOUT", "300")))]
        _preset_host = _os.path.join(self._model_dir, "models-preset.ini")
        print(f"[preset] checking {_preset_host}: exists={_os.path.isfile(_preset_host)}", flush=True)
        if _os.path.isfile(_preset_host):
            args += ["--models-preset", "/models/models-preset.ini"]
            print(f"[preset] added --models-preset", flush=True)
        return args

    def build_docker_run_args(self) -> list[str]:
        """Build the full `docker run` invocation.

        Exact port of scripts/qz-up qz_docker run call (lines 121-130).

        docker_cmd note: must be a simple space-separated command prefix,
        e.g. "docker", "sudo docker", "sudo -n /usr/local/sbin/qz-docker-quantzhai".
        Shell function forms such as "sg docker -c" cannot be represented here
        because sg's -c flag expects a single shell string, not separate argv
        entries. Use QZ_DOCKER_CMD="sudo docker" or a thin wrapper script instead.
        """
        cmd = self._docker_cmd.split()
        container_flags = [
            "run", "-d",
            "--name", self._container_name,
            "--gpus", "all",
            "--cap-add", "IPC_LOCK",
            "--ulimit", "memlock=-1:-1",
            "-p", f"{self._server_port}:8080",
            "--mount", f"type=bind,src={self._model_dir},dst=/models,readonly",
            self._image,
        ]
        return cmd + container_flags + self.build_backend_args()

    def build_docker_rm_args(self, force: bool = True) -> list[str]:
        """Build `docker rm [-f] <container>` args."""
        cmd = self._docker_cmd.split()
        flags = ["rm", "-f"] if force else ["rm"]
        return cmd + flags + [self._container_name]

    def build_docker_stop_args(self) -> list[str]:
        """Build `docker stop <container>` args."""
        return self._docker_cmd.split() + ["stop", self._container_name]

    def build_docker_ps_args(self) -> list[str]:
        """Build `docker ps --filter name=<container> --format {{.Names}}` args."""
        return (
            self._docker_cmd.split()
            + ["ps", "--filter", f"name=^/{self._container_name}$",
               "--format", "{{.Names}}"]
        )

    def build_docker_logs_args(self, tail: int | None = None) -> list[str]:
        """Build `docker logs --tail <N> <container>` args.

        Defaults to ``gpu_log_tail``; callers (e.g. the model-load classifier)
        may pass an explicit ``tail`` to read more lines.
        """
        actual = int(tail) if tail is not None else self._gpu_log_tail
        return (
            self._docker_cmd.split()
            + ["logs", "--tail", str(actual), self._container_name]
        )

    def fetch_recent_logs(self, tail: int | None = None) -> str | None:
        """Return recent container logs as text, or None when unavailable.

        Safe to call even when the container has gone away — failures of the
        underlying docker call return None instead of raising.

        Combines stdout and stderr because llama-server writes its model-load
        diagnostics to stderr.
        """
        try:
            rc, out, err = self._runner(self.build_docker_logs_args(tail=tail), timeout=15.0)
        except Exception:
            return None
        if rc != 0:
            return None
        combined = (out or "") + "\n" + (err or "")
        return combined if combined.strip() else None

    # ------------------------------------------------------------------
    # HTTP Model Management
    # ------------------------------------------------------------------

    def load_model_http(self, model_basename: str, timeout: float = 120.0) -> dict:
        """Issue POST /models/load to the running container.

        The router expects the model *name* (the ``.gguf`` basename without
        extension), not a filesystem path.  See
        ``server_models::post_router_models_load`` in server-models.cpp.
        """
        import json
        import urllib.request
        model_name = model_basename
        if model_name.endswith(".gguf"):
            model_name = model_name[:-5]
        url = f"http://{self._server_host}:{self._server_port}/models/load"
        data = json.dumps({"model": model_name}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= int(resp.status) < 300:
                    with self._lock:
                        self._last_load_attempted_at = time.time()
                    return {"ok": True}
                return {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def unload_model_http(self, model_id: str, timeout: float = 30.0) -> dict:
        """Issue POST /models/unload to the running container.

        ``model_id`` is the model's inventory id (as returned by GET /v1/models),
        which may be the stem name or a full path depending on how the server
        registered the model.
        """
        import json
        import urllib.request
        url = f"http://{self._server_host}:{self._server_port}/models/unload"
        data = json.dumps({"model": model_id}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= int(resp.status) < 300:
                    return {"ok": True}
                return {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # Crash tracking methods removed — the C++ router now owns crash recovery
    # via the recovering flag, exit_code, last_error, and reload_attempts.
    # The proxy reads these signals directly from /v1/models.

    def get_loaded_model_ids(self) -> list[str]:
        """Return ids of all currently loaded models from GET /v1/models."""
        status = self.get_models_status()
        if not status:
            return []
        loaded = []
        for entry in (status.get("data") or []):
            if isinstance(entry, dict):
                s = entry.get("status") or {}
                if isinstance(s, dict) and s.get("value") == "loaded":
                    model_id = entry.get("id")
                    if isinstance(model_id, str) and model_id.strip():
                        loaded.append(model_id.strip())
        return loaded

    def get_active_model_ids(self) -> dict[str, str]:
        """Return {model_id: state} for all loaded or loading models.

        Used by the same-model optimisation to detect when a model with the
        same underlying GGUF is already active (loaded or mid-load) under a
        different alias.  Checking only 'loaded' misses the window where a
        load is in progress — causing dual loads of the same GGUF.
        """
        status = self.get_models_status()
        if not status:
            return {}
        active = {}
        for entry in (status.get("data") or []):
            if isinstance(entry, dict):
                s = entry.get("status") or {}
                state = s.get("value") if isinstance(s, dict) else None
                if state in ("loaded", "loading"):
                    model_id = entry.get("id")
                    if isinstance(model_id, str) and model_id.strip():
                        active[model_id.strip()] = state
        return active

    def get_model_error_info(self, model_id: str) -> dict:
        """Return error details from the router for *model_id*.

        Reads exit_code, exit_signal, last_error, and failed from the
        router's /v1/models response for the given model.  Returns an
        empty dict if the model is not found or has no error info.

        exit_code < 0 means signal death (our subprocess.h encodes
        killing signals as negative exit codes: -6 = SIGABRT from OOM,
        -15 = SIGTERM from force-kill).  last_error contains the human-
        readable error message from CMD_CHILD_TO_ROUTER_ERROR or the
        GGML_ABORT callback.
        """
        status = self.get_models_status()
        if not status:
            return {}
        mid = model_id.strip()
        for entry in (status.get("data") or []):
            if not isinstance(entry, dict):
                continue
            eid = (entry.get("id") or "").strip()
            if eid != mid:
                continue
            s = entry.get("status") or {}
            if not isinstance(s, dict):
                return {}
            result = {}
            for key in ("exit_code", "exit_signal", "last_error", "failed"):
                if key in s:
                    result[key] = s[key]
            return result
        return {}

    def is_load_in_flight(self, timeout: float = 120.0) -> bool:
        """Return True if load_model_http was called within *timeout* seconds.

        This lets callers detect an in-flight async model load before the
        model inventory endpoint (/v1/models) confirms the new model.
        """
        t = self._last_load_attempted_at
        if t is None:
            return False
        return (time.time() - t) < timeout

    def get_models_status(self, timeout: float = 3.0) -> dict | None:
        """Fetch GET /v1/models from the running container."""
        import json
        import urllib.request
        url = f"http://{self._server_host}:{self._server_port}/v1/models"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # GPU offload verification
    # ------------------------------------------------------------------

    def _check_gpu_offload_for_loaded_model(
        self,
        model_id: str,
        retry_count: int = 15,
        retry_delay: float = 2.0,
    ) -> tuple[str, str | None]:
        """Post-load GPU check scoped to a specific model's child process.

        After ``load_model_http()`` returns, polls ``/v1/models`` until the
        target model shows as ``loaded`` and its child port is known, then
        filters docker logs to that port's prefix and runs the pattern scanner.

        Retries up to ``retry_count`` times with ``retry_delay`` between
        attempts so the child has time to write its startup log lines.

        Returns the same states as ``_check_gpu_offload_from_logs()``:
          'gpu'          — GPU offload confirmed in child's log lines
          'cpu_fallback' — CPU_Mapped present, no GPU success
          'failed'       — hard CUDA init/compile failure in child's logs
          'unknown'      — child not yet loaded, port unavailable, or no signal
        """
        for _ in range(max(1, retry_count)):
            status = self.get_models_status()
            if status:
                for entry in (status.get("data") or []):
                    if not isinstance(entry, dict) or entry.get("id") != model_id:
                        continue
                    s = entry.get("status") or {}
                    if not isinstance(s, dict) or s.get("value") != "loaded":
                        break  # not loaded yet — retry after sleep
                    args = s.get("args") or []
                    port = next(
                        (args[i + 1] for i, a in enumerate(args)
                         if a == "--port" and i + 1 < len(args)),
                        None,
                    )
                    if not port:
                        return "unknown", None
                    rc, logs_out, logs_err = self._runner(
                        self.build_docker_logs_args(), timeout=15.0)
                    if rc != 0:
                        return "unknown", None
                    raw = (logs_out or "") + "\n" + (logs_err or "")
                    prefix = f"[{port}]"
                    child_lines = "\n".join(
                        l for l in raw.splitlines()
                        if l.strip().startswith(prefix)
                    )
                    if not child_lines.strip():
                        break  # child hasn't written load lines yet — retry
                    return _scan_gpu_log_lines(child_lines)
            time.sleep(retry_delay)
        return "unknown", None

    def _check_gpu_offload_from_models_api(self) -> tuple[str, str | None]:
        """Check GPU offload via the /v1/models inventory.

        In router mode the parent server is a pure message-router and never
        loads a model itself, so its docker logs contain no GPU offload
        evidence.  This method checks the model inventory directly: if any
        loaded model's args include --n-gpu-layers > 0, GPU offload is
        confirmed.

        Returns:
          'gpu'     — at least one loaded model has n-gpu-layers > 0
          'unknown' — no loaded models yet, or inventory unavailable
        """
        status = self.get_models_status()
        if not status:
            return "unknown", None
        for entry in (status.get("data") or []):
            model_status = entry.get("status") or {}
            if model_status.get("value") != "loaded":
                continue
            args = model_status.get("args") or []
            for i, arg in enumerate(args):
                if arg in ("--n-gpu-layers", "-ngl") and i + 1 < len(args):
                    try:
                        if int(args[i + 1]) > 0:
                            return "gpu", None
                    except (ValueError, TypeError):
                        pass
        return "unknown", None

    def _check_gpu_offload_from_logs(self) -> tuple[str, str | None]:
        """Inspect container logs for GPU offload evidence after health passes.

        Scans only the parent router's own log lines — child process lines
        (prefixed ``[PORT]``) are filtered out.  In router mode the parent is
        a pure message-router that never loads a model itself, so child CUDA
        failures must not trigger a global PHASE_FAILED on the parent backend.
        Per-model GPU verification after a load is handled by
        ``_check_gpu_offload_for_loaded_model()``.

        Returns (state, error_msg):
          'gpu'          — GPU offload confirmed (success signal is the last signal)
          'cpu_fallback' — CPU_Mapped present, no GPU success signal anywhere
          'failed'       — hard CUDA init/compile failure is the last signal
          'unknown'      — logs unavailable or no recognisable signal
        """
        rc, logs_out, logs_err = self._runner(self.build_docker_logs_args(), timeout=15.0)
        if rc != 0:
            return "unknown", None
        raw = (logs_out or "") + "\n" + (logs_err or "")
        # Strip child-process lines so child CUDA failures don't kill the parent.
        _child_prefix = re.compile(r"^\s*\[\d+\]")
        parent_logs = "\n".join(
            l for l in raw.splitlines() if not _child_prefix.match(l)
        )
        return _scan_gpu_log_lines(parent_logs)

    # ------------------------------------------------------------------
    # Background lifecycle workers
    # ------------------------------------------------------------------

    def _autostart_with_delay(self) -> None:
        """Called by begin_autostart() in a daemon thread."""
        time.sleep(self._autostart_delay)
        with self._lock:
            # Re-check phase after delay — may have been stopped/disabled
            if self._state.phase not in (PHASE_IDLE,) or self._busy:
                return
            self._busy = True
            self._state.phase = PHASE_START_REQUESTED
            self._state.last_start_requested_at = _iso_now()
            self._state.last_error = None
        self._emit("backend_start_requested")
        self._do_start()

    def _do_start(self) -> None:
        """Background: attach to or start the backend container, then GPU-check.

        In router mode the container is a persistent service that must survive
        proxy restarts.  Killing and restarting a healthy container causes
        CUDA_ERROR_INITIALIZATION_ERROR because the GPU driver has not finished
        releasing the previous context before the new container initialises.

        Three cases:
          1. Container already running and healthy → reconnect, skip rm+run.
          2. Container running but unhealthy → rm -f then fresh run.
          3. Container not running → rm -f (clean up stale name) then run.
        """
        try:
            health_url = f"http://{self._server_host}:{self._server_port}/health"
            try:
                rc_ps, out_ps, _ = self._runner(self.build_docker_ps_args(), timeout=5.0)
                _already_running = rc_ps == 0 and self._container_name in out_ps
                _already_healthy = _already_running and self._health_checker(health_url, timeout=3.0)
            except Exception:
                _already_running = False
                _already_healthy = False

            if _already_healthy:
                # Case 1: container is up and the router responds — reconnect.
                with self._lock:
                    self._state.phase = PHASE_RUNNING
                    self._state.backend_model_mode = "direct"
                    self._state.launch_model_key = self._launch_model_key
                    self._state.launch_model_backend_id = self._launch_model_backend_id
                    self._state.launch_model_path_basename = self._launch_model_path_basename
                    self._state.launch_model_error = None
                    self._state.container_running = True
                    self._state.last_started_at = _iso_now()
                self._emit("backend_started")
            else:
                # Cases 2 & 3: rm -f (ignores rc) then fresh docker run.
                self._runner(self.build_docker_rm_args(force=True), timeout=30.0)

                with self._lock:
                    self._state.phase = PHASE_STARTING
                    self._state.backend_model_mode = "direct"
                    self._state.launch_model_key = self._launch_model_key
                    self._state.launch_model_backend_id = self._launch_model_backend_id
                    self._state.launch_model_path_basename = self._launch_model_path_basename
                    self._state.launch_model_error = None
                self._emit("backend_starting", {
                    "backend_model_mode": "direct",
                    "launch_model_backend_id": self._launch_model_backend_id,
                })

                rc, _out, err = self._runner(self.build_docker_run_args(), timeout=60.0)
                if rc != 0:
                    raise RuntimeError(f"docker run failed (rc={rc}): {err.strip()}")

                with self._lock:
                    self._state.phase = PHASE_RUNNING
                    self._state.container_running = True
                    self._state.last_started_at = _iso_now()
                self._emit("backend_started")

                # Health-check loop (only for fresh starts)
                deadline = time.monotonic() + self._health_check_timeout
                while time.monotonic() < deadline:
                    rc_ps, out_ps, _ = self._runner(self.build_docker_ps_args(), timeout=5.0)
                    if rc_ps != 0 or self._container_name not in out_ps:
                        raise RuntimeError("container exited before becoming healthy")
                    if not self._health_checker(health_url, timeout=3.0):
                        time.sleep(self._health_check_interval)
                        continue
                    break
                else:
                    raise RuntimeError(
                        f"backend did not become healthy within {self._health_check_timeout:.0f}s"
                    )

            # Health confirmed (either reconnected or fresh start passed above).
            # Eager model load: only on fresh starts.  On reconnect the model
            # is either already loaded (container was healthy) or the auto-trigger
            # will load it on the first request.  Firing a load from both _do_start()
            # AND the auto-trigger simultaneously causes dual-loading of the same
            # underlying GGUF when a profile alias is used.
            if not _already_healthy:
                if getattr(self, "_launch_model_path_basename", None):
                    import threading
                    threading.Thread(
                        target=self.load_model_http,
                        args=(self._launch_model_path_basename,),
                        daemon=True
                    ).start()
            if True:
                # GPU offload grace window.
                # /health passing does not guarantee model-load log lines are
                # written yet.  Treat any non-GPU provisional state (unknown,
                # failed, cpu_fallback) as provisional within the bounded retry
                # window — only GPU success or window expiry is terminal.
                # The backend stays in PHASE_RUNNING throughout so
                # request_admission_state remains "loading", not "failed".
                gpu_state, gpu_err = self._check_gpu_offload_from_logs()
                # Supplement with the model inventory check when logs are
                # ambiguous OR when the parent router logged a hard CUDA
                # failure during its own init (router mode: the parent never
                # loads models, so its CUDA init failure is benign — children
                # use separate CUDA contexts).  If the API confirms a loaded
                # model with n-gpu-layers > 0, override the log-based failure.
                # CPU_Mapped stays blocking (it's unambiguous about CPU use).
                if gpu_state in ("unknown", "failed"):
                    api_state, _ = self._check_gpu_offload_from_models_api()
                    if api_state == "gpu":
                        gpu_state, gpu_err = "gpu", None
                if self._require_gpu and gpu_state != "gpu":
                    retries_left = self._gpu_check_retry_count
                    while retries_left > 0 and gpu_state != "gpu":
                        # Container-exit check: if it died during the window,
                        # fail immediately rather than spinning until deadline.
                        rc_ps2, out_ps2, _ = self._runner(
                            self.build_docker_ps_args(), timeout=5.0
                        )
                        if rc_ps2 != 0 or self._container_name not in out_ps2:
                            gpu_state = "failed"
                            gpu_err = "container exited during GPU offload verification"
                            break
                        time.sleep(self._gpu_check_retry_delay)
                        gpu_state, gpu_err = self._check_gpu_offload_from_logs()
                        if gpu_state in ("unknown", "failed"):
                            api_state, _ = self._check_gpu_offload_from_models_api()
                            if api_state == "gpu":
                                gpu_state, gpu_err = "gpu", None
                        retries_left -= 1
                if gpu_state == "unknown" and self._require_gpu:
                    gpu_state = "unknown_after_retries"
                    gpu_err = (
                        f"GPU offload state could not be confirmed after "
                        f"{self._gpu_check_retry_count} retries. "
                        "Logs may be unavailable or model load not yet complete."
                    )
                _gpu_observed: bool | None = (
                    True if gpu_state == "gpu"
                    else False if gpu_state in ("cpu_fallback", "failed")
                    else None
                )
                _gpu_blocking = self._require_gpu and gpu_state in (
                    "cpu_fallback", "failed", "unknown_after_retries"
                )
                if _gpu_blocking and gpu_state == "unknown_after_retries":
                    # Router mode: the parent server is a pure message-router
                    # and never loads a model itself.  Neither docker logs nor
                    # the model inventory found GPU evidence — the selected
                    # model is probably still loading asynchronously via HTTP.
                    # Let the backend proceed to HEALTHY so it can serve
                    # requests once a model is ready.  Hard failures (CUDA
                    # init error, CPU_Mapped) still block via the elif below.
                    gpu_state = "unknown"
                    gpu_err = None
                    _gpu_observed = None
                    _gpu_blocking = False
                elif _gpu_blocking:
                    err_str = gpu_err or f"GPU not used (gpu_offload_state={gpu_state})"
                    with self._lock:
                        self._state.phase = PHASE_FAILED
                        self._state.backend_health_ok = False
                        self._state.gpu_offload_state = gpu_state
                        self._state.gpu_observed = _gpu_observed
                        self._state.gpu_error = gpu_err
                        self._state.last_error = err_str
                    self._emit("backend_failed", {"error": err_str, "gpu_offload_state": gpu_state})
                    return
                with self._lock:
                    self._state.phase = PHASE_HEALTHY
                    self._state.backend_health_ok = True
                    self._state.last_healthy_at = _iso_now()
                    self._state.gpu_offload_state = gpu_state
                    self._state.gpu_observed = _gpu_observed
                    self._state.gpu_error = gpu_err
                self._emit("backend_healthy")
                return

            raise RuntimeError(
                f"backend did not become healthy within {self._health_check_timeout:.0f}s"
            )

        except Exception as exc:
            err_str = str(exc)
            with self._lock:
                self._state.phase = PHASE_FAILED
                self._state.container_running = False
                self._state.backend_health_ok = False
                self._state.last_error = err_str
            self._emit("backend_failed", {"error": err_str})
        finally:
            with self._lock:
                self._busy = False

    def _do_stop(self) -> None:
        """Background: graceful stop then force-remove.

        docker stop is a best-effort hint (some installations deny it).
        docker rm -f is the authoritative cleanup step; its return code
        determines whether the container was actually removed.
        Phase is set to STOPPED only when rm -f succeeds (rc==0).
        """
        err_str: str | None = None
        try:
            # Best-effort graceful stop; ignore rc — some wrappers deny "stop"
            rc_stop, _, _ = self._runner(self.build_docker_stop_args(), timeout=30.0)

            # Force-remove: this is the authoritative step
            rc_rm, _, err_rm = self._runner(self.build_docker_rm_args(force=True), timeout=30.0)
            if rc_rm != 0 and err_rm.strip():
                err_str = f"docker rm -f failed (rc={rc_rm}): {err_rm.strip()}"
        except Exception as exc:
            err_str = f"stop exception: {exc}"
            # Try force-remove even on exception
            try:
                self._runner(self.build_docker_rm_args(force=True), timeout=15.0)
            except Exception:
                pass
        finally:
            with self._lock:
                self._state.phase = PHASE_STOPPED
                self._state.container_running = False
                self._state.backend_health_ok = False
                self._state.last_stopped_at = _iso_now()
                if err_str:
                    self._state.last_error = err_str
                self._busy = False
            self._emit("backend_stopped")

    # _do_restart() removed: see restart() note above.

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
