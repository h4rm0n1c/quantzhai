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

    def test_autostart_reconnect_when_container_already_healthy(self):
        """Container already running and healthy → reconnect path: no docker run, PHASE_HEALTHY.

        In router mode the container is a persistent service.  If it is already
        up and healthy the proxy must reconnect without killing and restarting it.
        docker run must NOT be called so the GPU context is never torn down.
        """
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

        mgr.set_launch_model(key="m1", backend_id="m1", path_basename="m1.gguf")
        mgr.begin_autostart()

        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"Phase {mgr.phase} did not reach terminal state")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

        # Router mode: reconnect path must NOT call docker run.
        docker_runs = [c for c in calls if "run" in c and "rm" not in c]
        self.assertEqual(len(docker_runs), 0, "docker run must not be called on reconnect")

    def test_autostart_fresh_start_when_container_not_running(self):
        """Container not running → fresh start path: docker run called (no -m flag in router mode)."""
        calls = []
        started = [False]
        def runner(args, timeout=None):
            calls.append(args)
            if "run" in args and "rm" not in args:
                started[0] = True
            if "ps" in args:
                # Return container name after docker run, "" before
                return (0, "test-ctr", "") if started[0] else (0, "", "")
            if "logs" in args:
                return 0, "offloaded 99/99 layers to GPU", ""
            return 0, "", ""

        mgr = _make_mgr(autostart=True, runner=runner)
        mgr.set_launch_model(key="m1", backend_id="m1", path_basename="m1.gguf")
        mgr.begin_autostart()

        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached, f"Phase {mgr.phase} did not reach terminal state")
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

        # Router mode: docker run is called but WITHOUT -m (models loaded via HTTP).
        docker_runs = [c for c in calls if "run" in c and "rm" not in c]
        self.assertTrue(len(docker_runs) > 0, "docker run must be called for fresh start")
        self.assertNotIn("-m", docker_runs[0], "router mode must not pass -m to docker run")
        self.assertIn("--models-dir", docker_runs[0])

    def test_autostart_without_launch_model_still_starts_container(self):
        """Router mode: container can start without a pre-selected model.

        In router mode the container is a pure message-router.  A model is
        loaded via HTTP after startup.  Autostart without a configured model
        is valid — the container starts and waits for a load request.
        launch_model_error must remain None (no pre-selected model is not an error).
        """
        calls = []
        started = [False]
        def runner(args, timeout=None):
            calls.append(args)
            if "run" in args and "rm" not in args:
                started[0] = True
            if "ps" in args:
                return (0, "test-ctr", "") if started[0] else (0, "", "")
            if "logs" in args:
                return 0, "", ""
            return 0, "", ""

        mgr = _make_mgr(autostart=True, runner=runner, require_gpu=False)
        self.assertEqual(mgr.phase, PHASE_IDLE)

        mgr.begin_autostart()

        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached)
        self.assertEqual(mgr.phase, PHASE_HEALTHY)

        status = mgr.status()["backend_manager"]
        self.assertIsNone(status.get("launch_model_error"),
                          "no launch model is not an error in router mode")

    def test_start_backend_with_empty_launch_model_succeeds_in_router_mode(self):
        """Router mode: start() without a launch model starts the container.

        The container is a persistent router.  A model is loaded via HTTP
        after start.  start() must return ok=True so the proxy can proceed
        to the hold-open / auto-trigger path which fires the actual HTTP load.
        """
        calls = []
        started = [False]
        def runner(args, timeout=None):
            calls.append(args)
            if "run" in args and "rm" not in args:
                started[0] = True
            if "ps" in args:
                return (0, "test-ctr", "") if started[0] else (0, "", "")
            if "logs" in args:
                return 0, "", ""
            return 0, "", ""

        mgr = _make_mgr(autostart=False, runner=runner, require_gpu=False)
        with mgr._lock:
            mgr._state.phase = PHASE_IDLE

        result = mgr.start()
        self.assertTrue(result["ok"])

        reached = self._wait_phase(mgr, PHASE_HEALTHY, PHASE_FAILED)
        self.assertTrue(reached)
        self.assertEqual(mgr.phase, PHASE_HEALTHY)
        self.assertIsNone(mgr.snapshot().get("launch_model_error"))

if __name__ == "__main__":
    unittest.main()
