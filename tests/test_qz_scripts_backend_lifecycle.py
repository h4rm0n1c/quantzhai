"""Structural tests for #65 B3 script changes.

Reads script source text to verify the design invariants; never executes
Docker or proxy processes.
"""

import os
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _read(name: str) -> str:
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8")


class QzUpTests(unittest.TestCase):
    """scripts/qz-up must not own backend Docker lifecycle."""

    def setUp(self):
        self.src = _read("qz-up")

    def test_no_docker_run(self):
        """qz-up must not call qz_docker run or docker run."""
        self.assertNotIn("qz_docker run", self.src)
        self.assertNotIn("docker run", self.src)

    def test_no_container_rm(self):
        """qz-up must not force-remove the backend container."""
        self.assertNotIn("qz_docker rm", self.src)
        self.assertNotIn("docker rm", self.src)

    def test_no_qz_wait_ready(self):
        """qz-up must not call qz-wait-ready (catalog gate moved to proxy)."""
        self.assertNotIn("qz-wait-ready", self.src)

    def test_no_backend_health_loop(self):
        """qz-up must not poll the backend /health endpoint directly."""
        self.assertNotIn("backend_wait_seconds", self.src)
        self.assertNotIn("QZ_MODEL_LOAD_TIMEOUT", self.src)
        # No curl to the backend server health endpoint
        self.assertNotIn("QZ_SERVER_PORT/health", self.src)
        self.assertNotIn("QZ_SERVER_HOST", self.src)

    def test_still_sources_qz_env(self):
        self.assertIn("qz-env", self.src)

    def test_still_calls_qz_proxy(self):
        self.assertIn("qz-proxy", self.src)

    def test_mentions_backend_status(self):
        """qz-up should tell the operator how to check backend status."""
        self.assertTrue(
            "/qz/backend/status" in self.src or "qz-backend status" in self.src
        )

    def test_mentions_control_plane(self):
        self.assertIn("/qz/control-plane", self.src)

    def test_mentions_qz_down(self):
        self.assertIn("qz-down", self.src)

    def test_hold_flag_present(self):
        self.assertIn("--hold", self.src)

    def test_codex_model_flag_present(self):
        self.assertIn("--codex-model", self.src)

    def test_no_backend_args_array(self):
        """The llama.cpp backend_args array must not appear in qz-up."""
        self.assertNotIn("backend_args", self.src)
        self.assertNotIn("-ngl", self.src)
        self.assertNotIn("--kv-unified", self.src)

    def test_is_executable(self):
        path = SCRIPTS_DIR / "qz-up"
        self.assertTrue(os.access(path, os.X_OK), "qz-up is not executable")


class QzDownTests(unittest.TestCase):
    """scripts/qz-down must use graceful proxy path by default."""

    def setUp(self):
        self.src = _read("qz-down")

    def test_has_force_option(self):
        self.assertIn("--force", self.src)

    def test_normal_path_calls_backend_stop_endpoint(self):
        """Normal (no --force) path must POST /qz/backend/stop through the proxy."""
        self.assertIn("/qz/backend/stop", self.src)

    def test_docker_rm_only_in_force_path(self):
        """docker rm -f must only appear after the --force check."""
        # Find the line number of the force section and the docker rm line.
        lines = self.src.splitlines()
        force_line = None
        rm_lines = []
        in_force_block = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'force=1' in stripped or '"$force" == "1"' in stripped or '"force" == "1"' in stripped:
                force_line = i
                in_force_block = True
            if ('qz_docker rm' in stripped or 'docker rm' in stripped) and '-f' in stripped:
                rm_lines.append(i)
        # There should be at least one docker rm -f call
        self.assertTrue(len(rm_lines) > 0, "No 'docker rm -f' found in qz-down --force path")
        # All docker rm -f calls must appear after the force flag is checked
        self.assertIsNotNone(force_line, "Could not find force=1 assignment in qz-down")
        for rm_line in rm_lines:
            self.assertGreater(rm_line, force_line,
                f"docker rm at line {rm_line} appears before force check at line {force_line}")

    def test_warns_when_proxy_unreachable(self):
        self.assertTrue(
            "not reachable" in self.src or "Proxy not reachable" in self.src
        )

    def test_kills_proxy_pid(self):
        self.assertTrue(
            "pid_file" in self.src or "kill_proxy_pid" in self.src
        )

    def test_is_executable(self):
        path = SCRIPTS_DIR / "qz-down"
        self.assertTrue(os.access(path, os.X_OK), "qz-down is not executable")

    def test_has_usage(self):
        self.assertIn("usage", self.src.lower())


class QzDownAuditTests(unittest.TestCase):
    """Additional qz-down audit tests for B.1 fixes."""

    def setUp(self):
        self.src = _read("qz-down")

    def test_qz_down_waits_for_backend_stopped(self):
        """qz-down must poll /qz/backend/status after posting stop."""
        self.assertIn("/qz/backend/status", self.src)

    def test_qz_down_has_backend_stop_timeout(self):
        """qz-down must have a bounded wait timeout for backend stop."""
        self.assertTrue(
            "QZ_BACKEND_STOP_TIMEOUT" in self.src or "backend_stop_timeout" in self.src
        )

    def test_qz_down_checks_stopped_or_failed_phase(self):
        """Wait loop must check for stopped/failed phase to exit."""
        self.assertIn("stopped", self.src)
        self.assertIn("failed", self.src)


class QzBackendTests(unittest.TestCase):
    """scripts/qz-backend must be a thin proxy wrapper with no Docker calls."""

    def setUp(self):
        self.src = _read("qz-backend")

    def test_script_exists(self):
        self.assertTrue((SCRIPTS_DIR / "qz-backend").exists())

    def test_has_status_subcommand(self):
        self.assertIn("status", self.src)

    def test_has_start_subcommand(self):
        self.assertIn("start", self.src)

    def test_has_stop_subcommand(self):
        self.assertIn("stop", self.src)

    def test_has_restart_subcommand(self):
        self.assertIn("restart", self.src)

    def test_no_docker_calls(self):
        self.assertNotIn("docker run", self.src)
        self.assertNotIn("docker rm", self.src)
        self.assertNotIn("docker stop", self.src)
        self.assertNotIn("docker ps", self.src)
        self.assertNotIn("qz_docker", self.src)

    def test_calls_proxy_endpoints(self):
        self.assertIn("/qz/backend/", self.src)

    def test_uses_curl(self):
        self.assertIn("curl", self.src)

    def test_proxy_health_check_before_call(self):
        self.assertIn("/health", self.src)

    def test_has_usage(self):
        self.assertIn("usage", self.src.lower())

    def test_is_executable(self):
        path = SCRIPTS_DIR / "qz-backend"
        self.assertTrue(os.access(path, os.X_OK), "qz-backend is not executable")

    def test_sources_qz_env(self):
        self.assertIn("qz-env", self.src)


if __name__ == "__main__":
    unittest.main()
