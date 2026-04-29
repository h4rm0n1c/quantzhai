import os
import tempfile
import unittest

from proxy import qz_runtime_io


class RuntimeIoTests(unittest.TestCase):
    def test_capture_helpers_use_qz_var_dir(self):
        old_var_dir = os.environ.get("QZ_VAR_DIR")
        old_capture_mode = os.environ.get("QZ_CAPTURE_MODE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["QZ_VAR_DIR"] = tmp
                os.environ["QZ_CAPTURE_MODE"] = "latest"
                qz_runtime_io.write_capture("sample.json", {"ok": True})
                qz_runtime_io.append_capture("sample.log", "one\n")
                qz_runtime_io.append_capture("sample.log", "two\n")

                self.assertEqual(qz_runtime_io.capture_path("sample.log").read_text(), "one\ntwo\n")
                self.assertIn('"ok": true', qz_runtime_io.capture_path("sample.json").read_text())
                self.assertEqual(qz_runtime_io.capture_path("sample.log").parent.name, "captures")
        finally:
            if old_var_dir is None:
                os.environ.pop("QZ_VAR_DIR", None)
            else:
                os.environ["QZ_VAR_DIR"] = old_var_dir
            if old_capture_mode is None:
                os.environ.pop("QZ_CAPTURE_MODE", None)
            else:
                os.environ["QZ_CAPTURE_MODE"] = old_capture_mode

    def test_capture_helpers_noop_when_off(self):
        old_var_dir = os.environ.get("QZ_VAR_DIR")
        old_capture_mode = os.environ.get("QZ_CAPTURE_MODE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["QZ_VAR_DIR"] = tmp
                os.environ["QZ_CAPTURE_MODE"] = "off"
                qz_runtime_io.write_capture("sample.json", {"ok": True})
                qz_runtime_io.append_capture("sample.log", "one\n")

                self.assertFalse(qz_runtime_io.capture_path("sample.log").exists())
                self.assertFalse(qz_runtime_io.capture_path("sample.json").exists())
        finally:
            if old_var_dir is None:
                os.environ.pop("QZ_VAR_DIR", None)
            else:
                os.environ["QZ_VAR_DIR"] = old_var_dir
            if old_capture_mode is None:
                os.environ.pop("QZ_CAPTURE_MODE", None)
            else:
                os.environ["QZ_CAPTURE_MODE"] = old_capture_mode


if __name__ == "__main__":
    unittest.main()
