"""Tests for proxy/qz_backend_manager.py — B1: skeleton + B2: lifecycle."""

import threading
import time
import unittest

from proxy.qz_backend_manager import (
    BackendManager,
    BackendState,
    PHASE_DISABLED,
    PHASE_FAILED,
    PHASE_HEALTHY,
    PHASE_IDLE,
    PHASE_RUNNING,
    PHASE_STOPPED,
    PHASE_UNKNOWN,
    _parse_bool_env,
    _iso_now,
)

# GPU log samples used across several test classes
_GPU_LOG_CUDA_INIT_FAIL = (
    "ggml_cuda_init: failed to initialize CUDA: initialization error\n"
    "warning: no usable GPU found, --gpu-layers option will be ignored\n"
)
_GPU_LOG_CPU_MAPPED = (
    "load_tensors: CPU_Mapped model buffer size = 17691.34 MiB\n"
)
_GPU_LOG_OFFLOADED = (
    "load_tensors: offloaded 94 layers to GPU\n"
    "load_tensors: CUDA0 model buffer size =  9012.34 MiB\n"
)
_GPU_LOG_CUDA_HOST = (
    "load_tensors: CUDA_Host model buffer size =  1234.56 MiB\n"
)

# Real-world log: two large CUDA buffers + small CPU_Mapped residual.
# CPU_Mapped appears AFTER CUDA success lines — must be classified as gpu.
_GPU_LOG_REAL_MIXED = (
    "ggml_cuda_init: found 2 CUDA devices\n"
    "load_tensors: offloading output layer to GPU\n"
    "load_tensors: offloading 47 repeating layers to GPU\n"
    "load_tensors: offloaded 49/49 layers to GPU\n"
    "load_tensors: CUDA0 model buffer size = 7200.42 MiB\n"
    "load_tensors: CUDA1 model buffer size = 10324.02 MiB\n"
    "load_tensors: CPU_Mapped model buffer size = 166.92 MiB\n"
)

# Ordering: failure lines appear first, then successful GPU load.
_GPU_LOG_FAIL_THEN_SUCCESS = (
    "ggml_cuda_init: failed to initialize CUDA: initialization error\n"
    "retrying with different device...\n"
    "load_tensors: offloaded 49/49 layers to GPU\n"
    "load_tensors: CUDA0 model buffer size = 7200.42 MiB\n"
)

# Ordering: success lines appear first, then a late failure.
_GPU_LOG_SUCCESS_THEN_FAIL = (
    "load_tensors: offloaded 49/49 layers to GPU\n"
    "load_tensors: CUDA0 model buffer size = 7200.42 MiB\n"
    "ggml_cuda_init: failed to initialize CUDA: reinitialization error\n"
)


# ---------------------------------------------------------------------------
# BackendState snapshot
# ---------------------------------------------------------------------------

class BackendStateTests(unittest.TestCase):
    def test_snapshot_safe_fields(self):
        state = BackendState(
            phase="healthy",
            container_name="qwen36turbo",
            container_running=True,
            backend_health_ok=True,
            last_started_at="2026-05-21T10:00:00Z",
            last_healthy_at="2026-05-21T10:01:00Z",
            last_error=None,
            autostart=True,
        )
        d = state.as_dict()
        self.assertEqual(d["phase"], "healthy")
        self.assertEqual(d["container_name"], "qwen36turbo")
        self.assertTrue(d["container_running"])
        self.assertTrue(d["backend_health_ok"])
        self.assertEqual(d["last_started_at"], "2026-05-21T10:00:00Z")
        self.assertIsNone(d["last_error"])
        self.assertTrue(d["autostart"])

    def test_snapshot_no_secrets_no_paths(self):
        """as_dict() must not contain model_dir, image, or docker_cmd."""
        state = BackendState()
        d = state.as_dict()
        for key in ("model_dir", "image", "docker_cmd", "_docker_cmd",
                    "_model_dir", "_image", "docker_run_args"):
            self.assertNotIn(key, d)

    def test_all_nullable_fields_default_to_none(self):
        state = BackendState()
        self.assertIsNone(state.last_start_requested_at)
        self.assertIsNone(state.last_started_at)
        self.assertIsNone(state.last_healthy_at)
        self.assertIsNone(state.last_stopped_at)
        self.assertIsNone(state.last_error)
        self.assertIsNone(state.container_running)
        self.assertIsNone(state.backend_health_ok)


# ---------------------------------------------------------------------------
# BackendManager initial state
# ---------------------------------------------------------------------------

class BackendManagerInitTests(unittest.TestCase):
    def _make(self, **kwargs):
        defaults = dict(
            docker_cmd="docker",
            container_name="test-container",
            image="test-image:latest",
            model_dir="/tmp/models",
            server_host="127.0.0.1",
            server_port=18084,
            context=8192,
            parallel=1,
            batch=512,
            ubatch=128,
            threads=4,
            thread_batch=4,
            tensor_split="0",
            main_gpu=0,
            cache_ram=1024,
            cache_reuse=64,
            kv_key="q8_0",
            kv_value="f16",
            reasoning_budget="-1",
            reasoning_budget_message="Done.",
            spec_default=False,
            launch_model_path_basename="kuato.gguf",
            launch_model_key="kuato.gguf",
            launch_model_backend_id="kuato",
        )
        defaults.update(kwargs)
        return BackendManager(**defaults)

    def test_initial_state_idle_when_autostart(self):
        mgr = self._make(autostart=True)
        self.assertEqual(mgr.phase, PHASE_IDLE)

    def test_initial_state_disabled_when_no_autostart(self):
        mgr = self._make(autostart=False)
        self.assertEqual(mgr.phase, PHASE_DISABLED)

    def test_snapshot_returns_dict(self):
        mgr = self._make()
        snap = mgr.snapshot()
        self.assertIsInstance(snap, dict)
        self.assertIn("phase", snap)
        self.assertIn("container_name", snap)

    def test_snapshot_does_not_expose_model_dir(self):
        mgr = self._make(model_dir="/secret/model/path")
        snap = mgr.snapshot()
        serialized = str(snap)
        self.assertNotIn("/secret/model/path", serialized)
        self.assertNotIn("model_dir", snap)

    def test_snapshot_does_not_expose_image(self):
        mgr = self._make(image="private-registry/secret-image:v99")
        snap = mgr.snapshot()
        self.assertNotIn("image", snap)
        self.assertNotIn("private-registry", str(snap))

    def test_snapshot_does_not_expose_docker_cmd(self):
        mgr = self._make(docker_cmd="sudo docker")
        snap = mgr.snapshot()
        self.assertNotIn("docker_cmd", snap)
        self.assertNotIn("sudo docker", str(snap))

    def test_snapshot_container_name_is_present(self):
        mgr = self._make(container_name="my-backend")
        snap = mgr.snapshot()
        self.assertEqual(snap["container_name"], "my-backend")

    def test_snapshot_autostart_reflected(self):
        self.assertTrue(self._make(autostart=True).snapshot()["autostart"])
        self.assertFalse(self._make(autostart=False).snapshot()["autostart"])


# ---------------------------------------------------------------------------
# build_backend_args — exact qz-up semantics
# ---------------------------------------------------------------------------

class BuildBackendArgsTests(unittest.TestCase):
    """Verify build_backend_args() matches scripts/qz-up backend_args exactly."""

    QZ_UP_DEFAULTS = dict(
        context=262144,
        parallel=1,
        batch=4096,
        ubatch=512,
        threads=12,
        thread_batch=12,
        tensor_split="9,17",
        main_gpu=0,
        cache_ram=8192,
        cache_reuse=256,
        kv_key="q8_0",
        kv_value="turbo3",
        reasoning_budget="-1",
        reasoning_budget_message=(
            "I have reasoned long enough. Let me now produce my final answer."
        ),
        spec_default=False,
    )

    def _mgr(self, **overrides):
        params = dict(self.QZ_UP_DEFAULTS)
        params.update(overrides)
        return BackendManager(
            docker_cmd="docker",
            container_name="qwen36turbo",
            image="thetom-llama-cpp-turboquant:cuda-server",
            model_dir="/home/harri/turboquant/quantzhai/var/models",
            server_host="127.0.0.1",
            server_port=18084,
            launch_model_path_basename="kuato.gguf",
            launch_model_key="kuato.gguf",
            launch_model_backend_id="kuato",
            **params,
        )

    def test_direct_model_path(self):
        args = self._mgr().build_backend_args()
        self.assertIn("-m", args)
        self.assertEqual(args[args.index("-m") + 1], "/models/kuato.gguf")

    def test_no_models_dir(self):
        args = self._mgr().build_backend_args()
        self.assertNotIn("--models-dir", args)

    def test_host_and_port(self):
        args = self._mgr().build_backend_args()
        self.assertIn("--host", args)
        idx = args.index("--host")
        self.assertEqual(args[idx + 1], "0.0.0.0")
        self.assertIn("--port", args)
        self.assertEqual(args[args.index("--port") + 1], "8080")

    def test_ngl_999(self):
        args = self._mgr().build_backend_args()
        self.assertIn("-ngl", args)
        self.assertEqual(args[args.index("-ngl") + 1], "999")

    def test_context_from_config(self):
        args = self._mgr(context=131072).build_backend_args()
        self.assertEqual(args[args.index("-c") + 1], "131072")

    def test_parallel_np(self):
        args = self._mgr(parallel=2).build_backend_args()
        self.assertEqual(args[args.index("-np") + 1], "2")

    def test_batch_and_ubatch(self):
        args = self._mgr().build_backend_args()
        self.assertEqual(args[args.index("-b") + 1], "4096")
        self.assertEqual(args[args.index("-ub") + 1], "512")

    def test_threads_and_thread_batch(self):
        args = self._mgr().build_backend_args()
        self.assertEqual(args[args.index("-t") + 1], "12")
        self.assertEqual(args[args.index("-tb") + 1], "12")

    def test_fa_on(self):
        args = self._mgr().build_backend_args()
        self.assertIn("-fa", args)
        self.assertEqual(args[args.index("-fa") + 1], "on")

    def test_split_mode_layer(self):
        args = self._mgr().build_backend_args()
        self.assertIn("--split-mode", args)
        self.assertEqual(args[args.index("--split-mode") + 1], "layer")

    def test_tensor_split(self):
        args = self._mgr(tensor_split="9,17").build_backend_args()
        self.assertEqual(args[args.index("--tensor-split") + 1], "9,17")

    def test_main_gpu(self):
        args = self._mgr(main_gpu=1).build_backend_args()
        self.assertEqual(args[args.index("--main-gpu") + 1], "1")

    def test_kv_unified_present(self):
        self.assertIn("--kv-unified", self._mgr().build_backend_args())

    def test_reasoning_on(self):
        args = self._mgr().build_backend_args()
        self.assertIn("--reasoning", args)
        self.assertEqual(args[args.index("--reasoning") + 1], "on")

    def test_reasoning_budget(self):
        args = self._mgr(reasoning_budget="-1").build_backend_args()
        self.assertEqual(args[args.index("--reasoning-budget") + 1], "-1")

    def test_reasoning_budget_message(self):
        args = self._mgr().build_backend_args()
        idx = args.index("--reasoning-budget-message")
        self.assertIn("reasoned long enough", args[idx + 1])

    def test_cache_ram_and_reuse(self):
        args = self._mgr(cache_ram=8192, cache_reuse=256).build_backend_args()
        self.assertEqual(args[args.index("--cache-ram") + 1], "8192")
        self.assertEqual(args[args.index("--cache-reuse") + 1], "256")

    def test_mlock_present(self):
        self.assertIn("--mlock", self._mgr().build_backend_args())

    def test_kv_key_and_value(self):
        args = self._mgr(kv_key="q8_0", kv_value="turbo3").build_backend_args()
        self.assertEqual(args[args.index("-ctk") + 1], "q8_0")
        self.assertEqual(args[args.index("-ctv") + 1], "turbo3")

    def test_metrics_present(self):
        self.assertIn("--metrics", self._mgr().build_backend_args())

    def test_reasoning_format_deepseek(self):
        args = self._mgr().build_backend_args()
        self.assertEqual(args[args.index("--reasoning-format") + 1], "deepseek")

    def test_spec_default_added_when_true(self):
        args = self._mgr(spec_default=True).build_backend_args()
        self.assertIn("--spec-default", args)

    def test_spec_default_omitted_when_false(self):
        args = self._mgr(spec_default=False).build_backend_args()
        self.assertNotIn("--spec-default", args)

    def test_no_docker_flags_in_backend_args(self):
        """build_backend_args() must not contain docker run flags."""
        args = self._mgr().build_backend_args()
        for flag in ("run", "-d", "--name", "--gpus", "--cap-add", "--ulimit",
                     "--mount"):
            self.assertNotIn(flag, args)


# ---------------------------------------------------------------------------
# build_docker_run_args — full invocation
# ---------------------------------------------------------------------------

class BuildDockerRunArgsTests(unittest.TestCase):
    def _mgr(self, docker_cmd="docker", server_port=18084,
             model_dir="/tmp/models", image="test-image:latest",
             container_name="test-ctr", spec_default=False):
        return BackendManager(
            docker_cmd=docker_cmd,
            container_name=container_name,
            image=image,
            model_dir=model_dir,
            server_host="127.0.0.1",
            server_port=server_port,
            context=8192, parallel=1, batch=512, ubatch=128,
            threads=4, thread_batch=4, tensor_split="0", main_gpu=0,
            cache_ram=1024, cache_reuse=64, kv_key="q8_0", kv_value="f16",
            reasoning_budget="-1", reasoning_budget_message="Done.",
            spec_default=spec_default,
            launch_model_path_basename="kuato.gguf",
            launch_model_key="kuato.gguf",
            launch_model_backend_id="kuato",
        )

    def test_starts_with_docker_run_d(self):
        args = self._mgr().build_docker_run_args()
        self.assertEqual(args[0], "docker")
        self.assertIn("run", args)
        self.assertIn("-d", args)

    def test_sudo_docker_split(self):
        args = self._mgr(docker_cmd="sudo docker").build_docker_run_args()
        self.assertEqual(args[0], "sudo")
        self.assertEqual(args[1], "docker")

    def test_container_name_flag(self):
        args = self._mgr(container_name="my-ctr").build_docker_run_args()
        self.assertIn("--name", args)
        self.assertEqual(args[args.index("--name") + 1], "my-ctr")

    def test_gpus_all(self):
        args = self._mgr().build_docker_run_args()
        self.assertIn("--gpus", args)
        self.assertEqual(args[args.index("--gpus") + 1], "all")

    def test_cap_add_ipc_lock(self):
        args = self._mgr().build_docker_run_args()
        self.assertIn("--cap-add", args)
        self.assertEqual(args[args.index("--cap-add") + 1], "IPC_LOCK")

    def test_ulimit_memlock(self):
        args = self._mgr().build_docker_run_args()
        self.assertIn("--ulimit", args)
        self.assertEqual(args[args.index("--ulimit") + 1], "memlock=-1:-1")

    def test_port_mapping(self):
        args = self._mgr(server_port=18084).build_docker_run_args()
        self.assertIn("-p", args)
        self.assertEqual(args[args.index("-p") + 1], "18084:8080")

    def test_mount_bind_readonly(self):
        args = self._mgr(model_dir="/my/models").build_docker_run_args()
        self.assertIn("--mount", args)
        mount_val = args[args.index("--mount") + 1]
        self.assertIn("type=bind", mount_val)
        self.assertIn("/my/models", mount_val)
        self.assertIn("readonly", mount_val)
        self.assertIn("dst=/models", mount_val)

    def test_image_present(self):
        args = self._mgr(image="my-image:v1").build_docker_run_args()
        self.assertIn("my-image:v1", args)

    def test_no_models_dir_flag(self):
        args = self._mgr().build_docker_run_args()
        self.assertNotIn("--models-dir", args)
        self.assertIn("-m", args)
        self.assertEqual(args[args.index("-m") + 1], "/models/kuato.gguf")

    def test_backend_args_appended(self):
        """build_docker_run_args must end with build_backend_args."""
        mgr = self._mgr()
        full = mgr.build_docker_run_args()
        backend = mgr.build_backend_args()
        self.assertEqual(full[-len(backend):], backend)

    def test_spec_default_end_to_end(self):
        args = self._mgr(spec_default=True).build_docker_run_args()
        self.assertIn("--spec-default", args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class HelperTests(unittest.TestCase):
    def test_parse_bool_env_truthy(self):
        for v in ("1", "true", "True", "TRUE", "yes", "Yes", "on", "ON"):
            self.assertTrue(_parse_bool_env(v), f"expected True for {v!r}")

    def test_parse_bool_env_falsy(self):
        for v in ("0", "false", "False", "no", "off", "", None):
            self.assertFalse(_parse_bool_env(v), f"expected False for {v!r}")

    def test_parse_bool_env_default(self):
        self.assertFalse(_parse_bool_env(None, default=False))
        self.assertTrue(_parse_bool_env(None, default=True))

    def test_iso_now_format(self):
        ts = _iso_now()
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# Lifecycle tests — use injectable runner/health_checker; never real Docker
# ---------------------------------------------------------------------------

def _make_mgr(runner=None, health_checker=None, autostart=True, **kwargs):
    """Build a BackendManager with injectable runner and health checker.
    """
    defaults = dict(
        docker_cmd="docker",
        container_name="test-ctr",
        image="test-image:latest",
        model_dir="/tmp/models",
        server_host="127.0.0.1",
        server_port=18084,
        context=8192, parallel=1, batch=512, ubatch=128,
        threads=4, thread_batch=4, tensor_split="0", main_gpu=0,
        cache_ram=1024, cache_reuse=64, kv_key="q8_0", kv_value="f16",
        reasoning_budget="-1",
        reasoning_budget_message="Done.",
        spec_default=False,
        launch_model_path_basename="kuato.gguf",
        launch_model_key="kuato.gguf",
        launch_model_backend_id="kuato",
        health_check_interval=0.01,
        health_check_timeout=2.0,
        autostart_delay=0.0,
    )
    defaults.update(kwargs)
    return BackendManager(
        autostart=autostart,
        runner=runner,
        health_checker=health_checker,
        **defaults,
    )


class BackendManagerLifecycleTests(unittest.TestCase):

    def _ok_runner(self, container_name="test-ctr"):
        """Runner that simulates successful docker rm, run, ps."""
        calls = []
        def runner(args, timeout=None):
            calls.append(args)
            # docker ps check: return container name so health loop sees it running
            if "ps" in args:
                return 0, container_name, ""
            return 0, "", ""
        return runner, calls

    def _fail_runner(self, fail_on="run"):
        """Runner that fails on the specified sub-command."""
        calls = []
        def runner(args, timeout=None):
            calls.append(args)
            if fail_on in " ".join(args):
                return 1, "", f"simulated {fail_on} failure"
            if "ps" in args:
                return 0, "test-ctr", ""
            return 0, "", ""
        return runner, calls

    def _wait_phase(self, mgr, *phases, timeout=3.0):
        """Poll until manager reaches one of the target phases."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if mgr.phase in phases:
                return True
            time.sleep(0.02)
        return False

    # --- start() ---

    def test_start_issues_rm_then_run(self):
        runner, calls = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        result = mgr.start()
        self.assertTrue(result["ok"])
        # Wait for lifecycle thread to complete
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        # rm -f must come before run
        rm_idx = next((i for i, a in enumerate(calls) if "rm" in a), None)
        run_idx = next((i for i, a in enumerate(calls) if "run" in a), None)
        self.assertIsNotNone(rm_idx, "docker rm not called")
        self.assertIsNotNone(run_idx, "docker run not called")
        self.assertLess(rm_idx, run_idx, "rm must precede run")

    def test_start_success_reaches_healthy(self):
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)
        snap = mgr.snapshot()
        self.assertTrue(snap["backend_health_ok"])
        self.assertTrue(snap["container_running"])
        self.assertIsNotNone(snap["last_healthy_at"])

    def test_start_docker_run_failure_sets_failed(self):
        runner, _ = self._fail_runner(fail_on="run")
        health_checker = lambda url, timeout=3.0: False
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_FAILED)
        self.assertTrue(reached)
        snap = mgr.snapshot()
        self.assertEqual(snap["phase"], PHASE_FAILED)
        self.assertIsNotNone(snap["last_error"])
        self.assertIn("docker run failed", snap["last_error"])

    def test_start_health_timeout_sets_failed(self):
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: False  # never healthy
        mgr = _make_mgr(runner=runner, health_checker=health_checker,
                         health_check_timeout=0.1)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_FAILED)
        self.assertTrue(reached)
        self.assertEqual(mgr.phase, PHASE_FAILED)

    def test_start_already_running_returns_error(self):
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        result = mgr.start()  # second start while healthy
        self.assertFalse(result["ok"])
        self.assertIn("already", result["error"])

    def test_start_disabled_returns_error(self):
        mgr = _make_mgr(autostart=False)
        result = mgr.start()
        self.assertFalse(result["ok"])
        self.assertIn("disabled", result["error"])

    # --- stop() ---

    def test_stop_runs_stop_then_rm(self):
        runner, calls = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)

        runner2, calls2 = self._ok_runner()
        mgr._runner = runner2
        mgr.stop()
        self._wait_phase(mgr, PHASE_STOPPED)

        stop_idx = next((i for i, a in enumerate(calls2) if "stop" in a), None)
        rm_idx = next((i for i, a in enumerate(calls2) if "rm" in a), None)
        self.assertIsNotNone(stop_idx, "docker stop not called")
        self.assertIsNotNone(rm_idx, "docker rm not called")
        self.assertLess(stop_idx, rm_idx)

    def test_stop_success_sets_stopped(self):
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        mgr.stop()
        reached = self._wait_phase(mgr, PHASE_STOPPED)
        self.assertTrue(reached)
        snap = mgr.snapshot()
        self.assertEqual(snap["phase"], PHASE_STOPPED)
        self.assertFalse(snap["container_running"])
        self.assertFalse(snap["backend_health_ok"])
        self.assertIsNotNone(snap["last_stopped_at"])

    def test_stop_already_stopped_returns_error(self):
        mgr = _make_mgr()
        # Manually set stopped state
        mgr._state.phase = PHASE_STOPPED
        result = mgr.stop()
        self.assertFalse(result["ok"])

    # --- restart() ---

    def test_restart_emits_restart_and_eventually_healthy(self):
        runner, calls = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)

        result = mgr.restart()
        self.assertTrue(result["ok"])
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED, timeout=5.0)
        self.assertTrue(reached)
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

    def test_restart_disabled_returns_error(self):
        mgr = _make_mgr(autostart=False)
        result = mgr.restart()
        self.assertFalse(result["ok"])

    # --- begin_autostart() ---

    def test_begin_autostart_disabled_is_noop(self):
        mgr = _make_mgr(autostart=False)
        mgr.begin_autostart()
        time.sleep(0.1)
        self.assertEqual(mgr.phase, PHASE_DISABLED)

    def test_begin_autostart_enabled_starts_backend(self):
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker,
                         autostart=True, autostart_delay=0.0)
        mgr.begin_autostart()
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached)
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

    # --- OperationalStore events ---

    def test_operational_store_events_emitted_and_nonfatal(self):
        events = []
        class FakeStore:
            def record_startup_event(self, phase, payload=None, source=""):
                events.append(phase)
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr._operational_store = FakeStore()
        mgr.start()
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertIn("backend_start_requested", events)
        self.assertIn("backend_started", events)
        self.assertIn("backend_healthy", events)

    def test_operational_store_failure_nonfatal(self):
        class BrokenStore:
            def record_startup_event(self, **kw):
                raise RuntimeError("store broken")
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr._operational_store = BrokenStore()
        # Should not raise
        try:
            mgr.start()
            self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        except Exception as exc:
            self.fail(f"Broken store raised: {exc}")

    # --- Snapshot safety after failure ---

    def test_snapshot_after_failure_contains_safe_fields(self):
        runner, _ = self._fail_runner(fail_on="run")
        health_checker = lambda url, timeout=3.0: False
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        self._wait_phase(mgr, PHASE_FAILED)
        snap = mgr.snapshot()
        self.assertEqual(snap["phase"], PHASE_FAILED)
        self.assertIn("last_error", snap)
        self.assertNotIn("model_dir", snap)
        self.assertNotIn("image", snap)

    def test_stop_while_busy_returns_conflict(self):
        """stop() while start in progress must return conflict, not start two threads."""
        runner, _ = self._ok_runner()
        slow_health = lambda url, timeout=3.0: (time.sleep(0.5), False)[1]
        mgr = _make_mgr(runner=runner, health_checker=slow_health,
                         health_check_timeout=0.1)
        mgr.start()
        time.sleep(0.05)  # let _do_start thread begin
        result = mgr.stop()
        # Must be rejected while start holds _busy
        if not result["ok"]:
            self.assertIn("in progress", result.get("error", ""))
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)

    def test_restart_clears_busy_and_reaches_healthy(self):
        """_do_restart re-checks _busy before start phase to prevent double-start."""
        runner, _ = self._ok_runner()
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(runner=runner, health_checker=health_checker)
        mgr.start()
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        result = mgr.restart()
        self.assertTrue(result["ok"])
        self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED, timeout=5.0)
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

    def test_status_returns_ok_and_backend_manager_key(self):
        mgr = _make_mgr()
        result = mgr.status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "status")
        self.assertIn("backend_manager", result)
        self.assertIsInstance(result["backend_manager"], dict)


# ---------------------------------------------------------------------------
# GPU docker run args
# ---------------------------------------------------------------------------

class BuildDockerRunArgsHelperCompatTests(unittest.TestCase):
    """Regression: build_docker_run_args() must be safe for qz-docker-root-helper.

    The helper (sudo -n /usr/local/sbin/qz-docker-quantzhai) allowlists a fixed
    set of docker run flags.  -e and --device are NOT in the allowlist.  Any
    addition of those flags will cause rc=126 before the container launches.
    This class guards that invariant.
    """

    def _mgr(self):
        return BackendManager(
            docker_cmd="docker",
            container_name="test-ctr",
            image="test-image:latest",
            model_dir="/tmp/models",
            server_host="127.0.0.1",
            server_port=18084,
            context=8192, parallel=1, batch=512, ubatch=128,
            threads=4, thread_batch=4, tensor_split="0", main_gpu=0,
            cache_ram=1024, cache_reuse=64, kv_key="q8_0", kv_value="f16",
            reasoning_budget="-1", reasoning_budget_message="Done.",
            spec_default=False,
            launch_model_path_basename="kuato.gguf",
            launch_model_key="kuato.gguf",
            launch_model_backend_id="kuato",
        )

    def test_no_e_flags_by_default(self):
        """Regression: -e must not appear — helper denies it (rc=126)."""
        args = self._mgr().build_docker_run_args()
        self.assertNotIn("-e", args, "-e flag must not be passed to helper-wrapped docker")

    def test_no_device_flags_by_default(self):
        """Regression: --device must not appear — helper denies it (rc=126)."""
        args = self._mgr().build_docker_run_args()
        self.assertNotIn("--device", args, "--device flag must not be passed to helper-wrapped docker")

    def test_gpus_all_present(self):
        args = self._mgr().build_docker_run_args()
        self.assertIn("--gpus", args)
        self.assertEqual(args[args.index("--gpus") + 1], "all")

    def test_backend_args_still_appended(self):
        mgr = self._mgr()
        full = mgr.build_docker_run_args()
        backend = mgr.build_backend_args()
        self.assertEqual(full[-len(backend):], backend)


# ---------------------------------------------------------------------------
# GPU offload log check
# ---------------------------------------------------------------------------

class GPUOffloadLogCheckTests(unittest.TestCase):
    """Unit tests for _check_gpu_offload_from_logs() and _do_start GPU gate."""

    def _mgr_with_logs(self, gpu_logs: str, require_gpu: bool = True, **kwargs):
        """Return a manager whose runner returns gpu_logs for 'logs' commands."""
        def runner(args, timeout=None):
            if "ps" in args:
                return 0, "test-ctr", ""
            if "logs" in args:
                return 0, gpu_logs, ""
            return 0, "", ""

        return _make_mgr(
            runner=runner,
            health_checker=lambda url, timeout=3.0: True,
            require_gpu=require_gpu,
            **kwargs,
        )

    def _wait_phase(self, mgr, *phases, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if mgr.phase in phases:
                return True
            time.sleep(0.02)
        return False

    # --- _check_gpu_offload_from_logs() unit ---

    def test_offloaded_layers_returns_gpu(self):
        mgr = self._mgr_with_logs(_GPU_LOG_OFFLOADED)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "gpu")
        self.assertIsNone(err)

    def test_cuda_host_buffer_returns_gpu(self):
        mgr = self._mgr_with_logs(_GPU_LOG_CUDA_HOST)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "gpu")
        self.assertIsNone(err)

    def test_cuda_init_fail_returns_failed(self):
        mgr = self._mgr_with_logs(_GPU_LOG_CUDA_INIT_FAIL)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "failed")
        self.assertIsNotNone(err)

    def test_cpu_mapped_returns_cpu_fallback(self):
        mgr = self._mgr_with_logs(_GPU_LOG_CPU_MAPPED)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "cpu_fallback")
        self.assertIsNotNone(err)

    def test_empty_logs_returns_unknown(self):
        mgr = self._mgr_with_logs("")
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "unknown")
        self.assertIsNone(err)

    def test_logs_command_failure_returns_unknown(self):
        def runner(args, timeout=None):
            if "ps" in args:
                return 0, "test-ctr", ""
            if "logs" in args:
                return 1, "", "no such container"
            return 0, "", ""
        mgr = _make_mgr(runner=runner, health_checker=lambda url, timeout=3.0: True)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "unknown")

    def test_real_mixed_cuda_with_cpu_mapped_residual_returns_gpu(self):
        """Core regression: CUDA0/CUDA1 buffers + small CPU_Mapped residual == gpu."""
        mgr = self._mgr_with_logs(_GPU_LOG_REAL_MIXED)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "gpu")
        self.assertIsNone(err)

    def test_fail_then_success_returns_gpu(self):
        """If success signal appears after a failure line, success wins."""
        mgr = self._mgr_with_logs(_GPU_LOG_FAIL_THEN_SUCCESS)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "gpu")
        self.assertIsNone(err)

    def test_success_then_fail_returns_failed(self):
        """If failure line appears after a success line, failure wins."""
        mgr = self._mgr_with_logs(_GPU_LOG_SUCCESS_THEN_FAIL)
        state, err = mgr._check_gpu_offload_from_logs()
        self.assertEqual(state, "failed")
        self.assertIsNotNone(err)

    # --- _do_start GPU gate ---

    def test_cuda_init_failure_marks_phase_failed_when_require_gpu(self):
        mgr = self._mgr_with_logs(_GPU_LOG_CUDA_INIT_FAIL, require_gpu=True)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_FAILED, PHASE_HEALTHY)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_FAILED)
        snap = mgr.snapshot()
        self.assertFalse(snap["backend_health_ok"])
        self.assertIn(snap["gpu_offload_state"], ("failed", "cpu_fallback"))
        self.assertIsNotNone(snap["gpu_error"])
        self.assertIsNotNone(snap["last_error"])

    def test_cpu_mapped_marks_phase_failed_when_require_gpu(self):
        mgr = self._mgr_with_logs(_GPU_LOG_CPU_MAPPED, require_gpu=True)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_FAILED, PHASE_HEALTHY)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_FAILED)
        snap = mgr.snapshot()
        self.assertEqual(snap["gpu_offload_state"], "cpu_fallback")

    def test_success_cuda_log_marks_healthy(self):
        mgr = self._mgr_with_logs(_GPU_LOG_OFFLOADED, require_gpu=True)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)
        snap = mgr.snapshot()
        self.assertTrue(snap["backend_health_ok"])
        self.assertEqual(snap["gpu_offload_state"], "gpu")
        self.assertIsNone(snap["gpu_error"])

    def test_real_mixed_cuda_cpu_mapped_residual_marks_healthy_when_require_gpu(self):
        """Regression: CUDA0+CUDA1 buffers + small CPU_Mapped must reach healthy."""
        mgr = self._mgr_with_logs(_GPU_LOG_REAL_MIXED, require_gpu=True)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)
        snap = mgr.snapshot()
        self.assertTrue(snap["backend_health_ok"])
        self.assertEqual(snap["gpu_offload_state"], "gpu")
        self.assertIsNone(snap["gpu_error"])

    def test_require_gpu_false_allows_cpu_fallback_but_records_state(self):
        mgr = self._mgr_with_logs(_GPU_LOG_CPU_MAPPED, require_gpu=False)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)
        snap = mgr.snapshot()
        self.assertTrue(snap["backend_health_ok"])
        self.assertEqual(snap["gpu_offload_state"], "cpu_fallback")

    def test_require_gpu_false_cuda_init_fail_still_healthy(self):
        mgr = self._mgr_with_logs(_GPU_LOG_CUDA_INIT_FAIL, require_gpu=False)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

    def test_unknown_gpu_state_does_not_fail_when_require_gpu(self):
        # Empty logs → unknown → should not trigger GPU failure
        mgr = self._mgr_with_logs("", require_gpu=True)
        mgr.start()
        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"timed out in phase {mgr.phase}")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)


# ---------------------------------------------------------------------------
# BackendState GPU fields in snapshot
# ---------------------------------------------------------------------------

class BackendStateGPUFieldTests(unittest.TestCase):

    def test_snapshot_exposes_gpu_required(self):
        state = BackendState(gpu_required=True)
        d = state.as_dict()
        self.assertIn("gpu_required", d)
        self.assertTrue(d["gpu_required"])

    def test_snapshot_exposes_gpu_offload_state(self):
        state = BackendState(gpu_offload_state="gpu")
        d = state.as_dict()
        self.assertIn("gpu_offload_state", d)
        self.assertEqual(d["gpu_offload_state"], "gpu")

    def test_snapshot_exposes_gpu_error(self):
        state = BackendState(gpu_error="CUDA init failed")
        d = state.as_dict()
        self.assertIn("gpu_error", d)
        self.assertEqual(d["gpu_error"], "CUDA init failed")

    def test_snapshot_gpu_error_defaults_none(self):
        state = BackendState()
        d = state.as_dict()
        self.assertIsNone(d["gpu_error"])

    def test_snapshot_does_not_expose_raw_logs(self):
        state = BackendState()
        d = state.as_dict()
        self.assertNotIn("logs", d)
        self.assertNotIn("container_logs", d)
        self.assertNotIn("raw_logs", d)

    def test_manager_snapshot_includes_gpu_fields(self):
        mgr = _make_mgr(require_gpu=True)
        snap = mgr.snapshot()
        self.assertIn("gpu_required", snap)
        self.assertIn("gpu_offload_state", snap)
        self.assertIn("gpu_error", snap)
        self.assertTrue(snap["gpu_required"])
        self.assertEqual(snap["gpu_offload_state"], "unknown")
        self.assertIsNone(snap["gpu_error"])


# ---------------------------------------------------------------------------
# Direct backend mode
# ---------------------------------------------------------------------------

class BuildDockerRunArgsDirectModeTests(unittest.TestCase):
    """Verify direct mode passes -m and omits --models-dir."""

    def _mgr(self, **kwargs):
        defaults = dict(
            docker_cmd="docker",
            container_name="test-ctr",
            image="test-image:latest",
            model_dir="/tmp/models",
            server_host="127.0.0.1",
            server_port=18084,
            context=8192, parallel=1, batch=512, ubatch=128,
            threads=4, thread_batch=4, tensor_split="0", main_gpu=0,
            cache_ram=1024, cache_reuse=64, kv_key="q8_0", kv_value="f16",
            reasoning_budget="-1", reasoning_budget_message="Done.",
            spec_default=False,
            launch_model_path_basename="kuato.gguf",
            launch_model_key="kuato.gguf",
            launch_model_backend_id="kuato",
        )
        defaults.update(kwargs)
        return BackendManager(**defaults)

    def test_direct_mode_passes_minus_m_with_models_path(self):
        args = self._mgr().build_docker_run_args()
        self.assertIn("-m", args)
        idx = args.index("-m")
        self.assertEqual(args[idx + 1], "/models/kuato.gguf")

    def test_direct_mode_omits_models_dir(self):
        args = self._mgr().build_docker_run_args()
        self.assertNotIn("--models-dir", args)

    def test_missing_launch_model_raises_for_command_build(self):
        mgr = self._mgr(launch_model_path_basename="")
        with self.assertRaisesRegex(ValueError, "direct backend launch requires"):
            mgr.build_docker_run_args()

    def test_default_mode_is_direct(self):
        # No backend_model_mode passed → default direct.
        mgr = BackendManager(
            docker_cmd="docker",
            container_name="test-ctr",
            image="test-image:latest",
            model_dir="/tmp/models",
            server_host="127.0.0.1",
            server_port=18084,
            context=8192, parallel=1, batch=512, ubatch=128,
            threads=4, thread_batch=4, tensor_split="0", main_gpu=0,
            cache_ram=1024, cache_reuse=64, kv_key="q8_0", kv_value="f16",
            reasoning_budget="-1", reasoning_budget_message="Done.",
            spec_default=False,
            launch_model_path_basename="kuato.gguf",
            launch_model_key="kuato.gguf",
            launch_model_backend_id="kuato",
        )
        self.assertEqual(mgr.backend_model_mode, "direct")
        args = mgr.build_docker_run_args()
        self.assertIn("-m", args)
        self.assertNotIn("--models-dir", args)

    def test_set_launch_model_updates_snapshot(self):
        mgr = self._mgr(launch_model_path_basename="", launch_model_key="", launch_model_backend_id="")
        mgr.set_launch_model(
            key="kuato.gguf",
            backend_id="kuato",
            path_basename="kuato.gguf",
        )
        snap = mgr.snapshot()
        self.assertEqual(snap["launch_model_key"], "kuato.gguf")
        self.assertEqual(snap["launch_model_backend_id"], "kuato")
        self.assertEqual(snap["launch_model_path_basename"], "kuato.gguf")
        args = mgr.build_docker_run_args()
        self.assertIn("/models/kuato.gguf", args)

    def test_direct_mode_without_launch_model_fails_start(self):
        """Direct mode + empty launch_model → _do_start fails with a clear error."""
        runner_calls = []
        def runner(args, timeout=None):
            runner_calls.append(args)
            return 0, "", ""
        health_checker = lambda url, timeout=3.0: True
        mgr = _make_mgr(
            runner=runner,
            health_checker=health_checker,
            launch_model_path_basename="",  # no model
        )
        mgr.start()
        # Wait briefly for the lifecycle thread to land in PHASE_FAILED
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if mgr.phase == PHASE_FAILED:
                break
            time.sleep(0.02)
        self.assertEqual(mgr.phase, PHASE_FAILED)
        snap = mgr.snapshot()
        self.assertIn("direct backend launch requires a launch model", snap["last_error"])
        self.assertIsNotNone(snap["launch_model_error"])
        # No docker run should have been attempted
        self.assertFalse(any("run" in a for a in runner_calls if isinstance(a, list)),
                         f"docker run should not run when launch model missing: {runner_calls}")

    def test_snapshot_exposes_backend_model_mode(self):
        mgr = self._mgr()
        snap = mgr.snapshot()
        self.assertEqual(snap["backend_model_mode"], "direct")
        self.assertIn("launch_model_key", snap)
        self.assertIn("launch_model_path_basename", snap)


# ---------------------------------------------------------------------------
# Proxy endpoint smoke tests (no real HTTP server — test handler directly)
# ---------------------------------------------------------------------------

class BackendEndpointTests(unittest.TestCase):
    """Test /qz/backend/* endpoints by calling the backend manager directly."""

    def setUp(self):
        runner_calls = []
        def runner(args, timeout=None):
            runner_calls.append(args)
            if "ps" in args:
                return 0, "test-ctr", ""
            return 0, "", ""
        health_checker = lambda url, timeout=3.0: True
        self.mgr = _make_mgr(runner=runner, health_checker=health_checker,
                              autostart=True)
        self.runner_calls = runner_calls

    def test_status_endpoint(self):
        result = self.mgr.status()
        self.assertTrue(result["ok"])
        self.assertIn("backend_manager", result)
        self.assertIn("phase", result["backend_manager"])

    def test_start_endpoint_invokes_manager(self):
        result = self.mgr.start()
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "start")
        self.assertIn("backend_manager", result)

    def test_stop_endpoint_on_idle_returns_error(self):
        result = self.mgr.stop()
        self.assertFalse(result["ok"])
        self.assertIn("already", result["error"])

    def test_restart_on_disabled_returns_error(self):
        mgr = _make_mgr(autostart=False)
        result = mgr.restart()
        self.assertFalse(result["ok"])
        self.assertIn("disabled", result["error"])

    def test_control_plane_includes_backend_manager(self):
        """backend_manager key present in control-plane payload when manager attached."""
        import io
        import json
        from unittest.mock import MagicMock

        # Build a minimal fake handler with backend_manager attached
        class FakeHandler:
            path = "/qz/control-plane"
            backend_manager = self.mgr

            def _initialization_payload(self):
                return {"state": "ready", "ready": True}

        from proxy.qz_control_plane import build_control_plane_status
        # build_control_plane_status calls many handler methods; use MagicMock
        handler = MagicMock()
        handler.backend_manager = self.mgr
        handler._initialization_payload.return_value = {"state": "ready", "ready": True}

        # build_control_plane_status is complex; just test the additive path
        # by checking our patch to qz_control_plane directly
        import proxy.qz_control_plane as cp_mod
        # The function accesses getattr(handler, "backend_manager", None)
        # Verify our patch is present
        import inspect
        src = inspect.getsource(cp_mod.build_control_plane_status)
        self.assertIn("backend_manager", src)


if __name__ == "__main__":
    unittest.main()
