#!/usr/bin/env python3
import time
import unittest
from proxy.qz_backend_manager import (
    BackendManager,
    PHASE_IDLE,
    PHASE_HEALTHY,
    PHASE_FAILED,
)

def _make_mgr(**kwargs):
    # Minimal helper to avoid importing the complex one from test_qz_backend_manager
    defaults = {
        "docker_cmd": "docker",
        "container_name": "test-ctr",
        "image": "test-img",
        "model_dir": "/tmp",
        "server_host": "127.0.0.1",
        "server_port": 18084,
        "runner": lambda args, timeout=None: (0, "", ""),
        "health_checker": lambda url, timeout=3.0: True,
        "autostart": False,
        "autostart_delay": 0.0,
    }
    defaults.update(kwargs)
    return BackendManager(**defaults)

class BackendAutostartInvariantsTests(unittest.TestCase):

    def _wait_phase(self, mgr, *phases, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if mgr.phase in phases:
                return True
            time.sleep(0.01)
        return False

    def test_autostart_true_plus_launch_model_set_starts_backend(self):
        """When autostart is True and a launch model is set, backend should start."""
        calls = []
        def runner(args, timeout=None):
            calls.append(args)
            if "ps" in args:
                return 0, "test-ctr", ""
            if "logs" in args:
                return 0, "offloaded 99/99 layers to GPU", ""
            return 0, "", ""

        mgr = _make_mgr(autostart=True, runner=runner)
        self.assertEqual(mgr.phase, PHASE_IDLE)

        # Simulation of _preload_last_model
        mgr.set_launch_model(key="m1", backend_id="m1", path_basename="m1.gguf")
        mgr.begin_autostart()

        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"Phase {mgr.phase} did not reach terminal state")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

        # Verify docker run was called
        docker_runs = [c for c in calls if "run" in c]
        self.assertTrue(len(docker_runs) > 0)
        self.assertIn("-m", docker_runs[0])
        self.assertIn("/models/m1.gguf", docker_runs[0])

    def test_autostart_true_plus_launch_model_missing_fails_with_error(self):
        """When autostart is True but NO launch model is set, backend should fail with clear error."""
        calls = []
        def runner(args, timeout=None):
            calls.append(args)
            return 0, "", ""

        mgr = _make_mgr(autostart=True, runner=runner)
        self.assertEqual(mgr.phase, PHASE_IDLE)

        # begin_autostart called WITHOUT set_launch_model
        mgr.begin_autostart()

        reached = self._wait_phase(mgr, PHASE_FAILED)
        self.assertTrue(reached)
        self.assertEqual(mgr.phase, PHASE_FAILED)

        status = mgr.status()["backend_manager"]
        self.assertIn("requires a launch model", status.get("launch_model_error", ""))

        # Verify NO docker run was called
        docker_runs = [c for c in calls if "run" in c]
        self.assertEqual(len(docker_runs), 0)

    def test_start_backend_with_empty_launch_model_fails_synchronously(self):
        """Explicit start() with empty launch model should fail immediately, not stay idle."""
        mgr = _make_mgr(autostart=False)
        self.assertEqual(mgr.phase, "disabled")

        # Reset to idle to simulate manual start attempt
        with mgr._lock:
            mgr._state.phase = PHASE_IDLE

        result = mgr.start()
        self.assertFalse(result["ok"])
        self.assertIn("requires a launch model", result["error"])
        self.assertEqual(mgr.phase, PHASE_FAILED)
        self.assertEqual(mgr.snapshot()["launch_model_error"], result["error"])

if __name__ == "__main__":
    unittest.main()
