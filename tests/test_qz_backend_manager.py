"""Tests for proxy/qz_backend_manager.py — Slice B1: skeleton + command builder."""

import unittest

from proxy.qz_backend_manager import (
    BackendManager,
    BackendState,
    PHASE_IDLE,
    PHASE_DISABLED,
    PHASE_UNKNOWN,
    _parse_bool_env,
    _iso_now,
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
            **params,
        )

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
                     "--mount", "/models"):
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

    def test_models_dir_flag(self):
        args = self._mgr().build_docker_run_args()
        self.assertIn("--models-dir", args)
        self.assertEqual(args[args.index("--models-dir") + 1], "/models")

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


if __name__ == "__main__":
    unittest.main()
