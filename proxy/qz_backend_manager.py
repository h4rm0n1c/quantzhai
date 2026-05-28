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
    """Owns Docker lifecycle for the llama.cpp backend.

    Construction is side-effect-free. Call begin_autostart() once after the
    proxy HTTP server is bound, or call start() / stop() / restart() directly.
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

    def restart(self) -> dict:
        """Request a backend restart. Returns immediately with a status dict."""
        with self._lock:
            phase = self._state.phase
            if phase == PHASE_DISABLED:
                return {"ok": False, "action": "restart",
                        "error": "backend is disabled",
                        "backend_manager": self._state.as_dict()}
            if self._busy:
                return {"ok": False, "action": "restart",
                        "error": f"another operation is in progress ({phase})",
                        "backend_manager": self._state.as_dict()}

            if not self._launch_model_path_basename:
                err = ("direct backend restart requires a launch model; "
                       "POST /qz/model/select-and-restart with a valid model")
                self._state.phase = PHASE_FAILED
                self._state.launch_model_error = err
                self._state.last_error = err
                return {"ok": False, "action": "restart",
                        "error": err,
                        "backend_manager": self._state.as_dict()}

            self._busy = True
            self._state.phase = PHASE_STOPPING
        self._emit("backend_restart_requested")
        threading.Thread(
            target=self._do_restart,
            daemon=True,
            name="qz-backend-restart",
        ).start()
        return {"ok": True, "action": "restart",
                "backend_manager": self.snapshot()}

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
            "--models-autoload",
            "--numa", "distribute",
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
        """Issue POST /models/load to the running container."""
        import json
        import urllib.request
        url = f"http://{self._server_host}:{self._server_port}/models/load"
        data = json.dumps({"model": f"/models/{model_basename}"}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= int(resp.status) < 300:
                    return {"ok": True}
                return {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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

    def _check_gpu_offload_from_logs(self) -> tuple[str, str | None]:
        """Inspect container logs for GPU offload evidence after health passes.

        Uses a "latest relevant signal wins" strategy so that small CPU_Mapped
        residual buffers that appear alongside CUDA/offload lines do not falsely
        trigger cpu_fallback.  (llama.cpp routinely maps a small host-side buffer
        even when the bulk of the model is on GPU.)

        Returns (state, error_msg):
          'gpu'          — GPU offload confirmed (success signal is the last signal)
          'cpu_fallback' — CPU_Mapped present, no GPU success signal anywhere
          'failed'       — hard CUDA init/compile failure is the last signal
          'unknown'      — logs unavailable or no recognisable signal

        Note: docker logs sends container-stdout to client-stdout and
        container-stderr to client-stderr.  llama-server writes its model-load
        lines (offload counts, CUDA buffer sizes) to stderr.  We combine both
        streams so GPU offload evidence is detected regardless of which fd the
        container process used.
        """
        rc, logs_out, logs_err = self._runner(self.build_docker_logs_args(), timeout=15.0)
        if rc != 0:
            return "unknown", None
        logs = (logs_out or "") + "\n" + (logs_err or "")

        _HARD_FAIL_PATTERNS = [
            "ggml_cuda_init: failed to initialize CUDA",
            "no usable GPU found",
            "--gpu-layers option will be ignored",
            "compiled without support for GPU offload",
        ]
        _SUCCESS_PATTERNS = [
            r"offloaded \d+/\d+ layers to GPU",
            r"offloaded .* layers to GPU",
            r"CUDA\d+ model buffer size",
            r"CUDA_Host model buffer size",
        ]
        _CPU_MAPPED = "CPU_Mapped model buffer size"

        last_fail = -1
        last_fail_msg: str | None = None
        last_success = -1
        last_cpu_mapped = -1

        for i, line in enumerate(logs.splitlines()):
            for pat in _HARD_FAIL_PATTERNS:
                if pat in line:
                    last_fail = i
                    last_fail_msg = pat
            for pat in _SUCCESS_PATTERNS:
                if re.search(pat, line):
                    last_success = i
            if _CPU_MAPPED in line:
                last_cpu_mapped = i

        # Latest relevant signal wins.
        if last_success >= 0 and last_success >= last_fail:
            return "gpu", None
        if last_fail >= 0 and last_fail > last_success:
            return "failed", f"GPU not used: {last_fail_msg!r} in container logs"
        if last_cpu_mapped >= 0:
            # CPU_Mapped with no GPU success anywhere: full CPU fallback.
            return "cpu_fallback", f"GPU not used: {_CPU_MAPPED!r} in container logs"
        return "unknown", None

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
        """Background: rm -f, docker run, then health-check loop."""
        try:
            # Remove any existing container first (match qz-up semantics)
            self._runner(self.build_docker_rm_args(force=True), timeout=30.0)

            with self._lock:
                self._state.phase = PHASE_STARTING
                # Refresh launch_model_* on the state snapshot so observers
                # see what we're actually launching with.
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

            # Health-check loop
            health_url = f"http://{self._server_host}:{self._server_port}/health"
            deadline = time.monotonic() + self._health_check_timeout
            while time.monotonic() < deadline:
                # Check container still running
                rc_ps, out_ps, _ = self._runner(self.build_docker_ps_args(), timeout=5.0)
                if rc_ps != 0 or self._container_name not in out_ps:
                    raise RuntimeError("container exited before becoming healthy")
                if self._health_checker(health_url, timeout=3.0):
                    # Trigger eager load of the selected model if any
                    if getattr(self, "_launch_model_path_basename", None):
                        # Run in background to not block the health check loop immediately,
                        # though the GPU check below will wait.
                        import threading
                        threading.Thread(
                            target=self.load_model_http,
                            args=(self._launch_model_path_basename,),
                            daemon=True
                        ).start()
                    # GPU offload grace window.
                    # /health passing does not guarantee model-load log lines are
                    # written yet.  Treat any non-GPU provisional state (unknown,
                    # failed, cpu_fallback) as provisional within the bounded retry
                    # window — only GPU success or window expiry is terminal.
                    # The backend stays in PHASE_RUNNING throughout so
                    # request_admission_state remains "loading", not "failed".
                    gpu_state, gpu_err = self._check_gpu_offload_from_logs()
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
                    if _gpu_blocking:
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
                time.sleep(self._health_check_interval)

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

    def _do_restart(self) -> None:
        """Background: stop then start.

        _do_stop() clears _busy=False. Before re-taking _busy for the start
        phase, we check it again to avoid a race with a concurrent start().
        """
        self._do_stop()
        with self._lock:
            if self._state.phase != PHASE_STOPPED or self._busy:
                # Stop failed, or another operation snuck in during the window.
                return
            self._busy = True
            self._state.phase = PHASE_START_REQUESTED
            self._state.last_start_requested_at = _iso_now()
            self._state.last_error = None
        self._emit("backend_start_requested")
        self._do_start()

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
