"""Tests for scripts/qz-braincase-smoke.

Runs the smoke script as a subprocess and asserts on its output/exit code.
Also tests the core run_smoke() helper directly for speed.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SMOKE_SCRIPT = _REPO / "scripts" / "qz-braincase-smoke"

# Make the smoke module importable
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


class SmokeScriptExistenceTests(unittest.TestCase):

    def test_script_exists(self):
        self.assertTrue(_SMOKE_SCRIPT.exists(),
                        f"Expected smoke script at {_SMOKE_SCRIPT}")

    def test_script_is_executable(self):
        self.assertTrue(os.access(_SMOKE_SCRIPT, os.X_OK),
                        f"Smoke script {_SMOKE_SCRIPT} is not executable")

    def test_script_is_python(self):
        first_line = _SMOKE_SCRIPT.read_text().splitlines()[0]
        self.assertIn("python", first_line,
                      "Smoke script must start with a Python shebang")


class SmokeScriptSubprocessTests(unittest.TestCase):
    """Run the script as a subprocess to test real exit code and output."""

    def _run(self, *extra_args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_SMOKE_SCRIPT)] + list(extra_args),
            capture_output=True,
            text=True,
            cwd=str(_REPO),
        )

    def test_script_exits_zero(self):
        result = self._run()
        self.assertEqual(
            result.returncode, 0,
            f"Smoke script failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_output_contains_pass_lines(self):
        result = self._run()
        self.assertIn("PASS", result.stdout)

    def test_no_fail_lines(self):
        result = self._run()
        self.assertNotIn("FAIL", result.stderr + result.stdout)

    def test_render_check_passes(self):
        result = self._run()
        self.assertIn("render returned RenderPacket", result.stdout)

    def test_recall_check_passes(self):
        result = self._run()
        self.assertIn("recall returned RenderPacket", result.stdout)

    def test_write_candidate_check_passes(self):
        result = self._run()
        self.assertIn("write_candidate returned WriteCandidateResult", result.stdout)

    def test_both_flags_required_for_write_candidate(self):
        result = self._run()
        self.assertIn("both flags expose render+recall+write_candidate", result.stdout)

    def test_candidate_not_returned_by_render(self):
        result = self._run()
        self.assertIn("render does not return the candidate record", result.stdout)

    def test_candidate_not_returned_by_recall(self):
        result = self._run()
        self.assertIn("recall does not return the candidate record", result.stdout)

    def test_no_raw_state_record_leaked(self):
        result = self._run()
        self.assertIn("no raw StateRecord leaked", result.stdout)

    def test_no_automatic_ingestion(self):
        result = self._run()
        self.assertIn("no automatic ingestion", result.stdout)

    def test_no_files_outside_temp_dir(self):
        """Default invocation must not leave any files outside temp dir."""
        before = set(Path(os.getcwd()).glob("*.sqlite3"))
        self._run()
        after = set(Path(os.getcwd()).glob("*.sqlite3"))
        self.assertEqual(before, after,
                         "Smoke script must not create *.sqlite3 files in cwd")

    def test_verbose_flag_accepted(self):
        result = self._run("--verbose")
        self.assertEqual(result.returncode, 0,
                         f"--verbose failed:\n{result.stderr}")

    def test_custom_db_path_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "custom.sqlite3"
            result = self._run("--db-path", str(db_path))
            self.assertEqual(result.returncode, 0,
                             f"--db-path failed:\n{result.stderr}")
            # Script removes the DB by default (no --keep-db)
            self.assertFalse(db_path.exists(),
                             "Script should remove DB after run unless --keep-db")


class SmokeHelperDirectTests(unittest.TestCase):
    """Test run_smoke() directly for faster iteration."""

    @classmethod
    def setUpClass(cls):
        # exec() the script into a fresh namespace so run_smoke() is accessible.
        # The script has no .py extension, so we use compile+exec.
        source = _SMOKE_SCRIPT.read_text()
        ns: dict = {"__name__": "__smoke__", "__file__": str(_SMOKE_SCRIPT)}
        exec(compile(source, str(_SMOKE_SCRIPT), "exec"), ns)  # noqa: S102

        class _Mod:
            pass

        mod = _Mod()
        for k, v in ns.items():
            setattr(mod, k, v)
        cls._mod = mod

    def test_run_smoke_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            exit_code = self._mod.run_smoke(Path(td) / "direct.sqlite3")
        self.assertEqual(exit_code, 0)

    def test_run_smoke_with_custom_db(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "smoke_direct.sqlite3"
            exit_code = self._mod.run_smoke(db_path)
            self.assertEqual(exit_code, 0)
            # DB should still exist (caller manages cleanup)
            self.assertTrue(db_path.exists())


if __name__ == "__main__":
    unittest.main()
