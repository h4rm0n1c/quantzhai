"""Backend lifecycle manager for the QuantZhai proxy control plane.

Owns Docker container start/stop/restart and /health polling for the
llama.cpp backend.  Proxied through /qz/backend/* endpoints; reported
in /qz/control-plane backend_manager section.

This module is import-safe: no Docker or network calls at import time.
"""

from __future__ import annotations

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
        )

    # ------------------------------------------------------------------
    # Public state access
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a safe state snapshot — no secrets, paths, or Docker commands."""
        with self._lock:
            return self._state.as_dict()

    @property
    def phase(self) -> str:
        with self._lock:
            return self._state.phase

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
            "--models-dir", "/models",
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
            self._emit("backend_starting")

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
                    with self._lock:
                        self._state.phase = PHASE_HEALTHY
                        self._state.backend_health_ok = True
                        self._state.last_healthy_at = _iso_now()
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
        """Background: docker stop, docker rm."""
        err_str: str | None = None
        try:
            self._runner(self.build_docker_stop_args(), timeout=30.0)
            self._runner(self.build_docker_rm_args(force=False), timeout=30.0)
        except Exception as exc:
            err_str = str(exc)
            # Force rm even on stop failure
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
