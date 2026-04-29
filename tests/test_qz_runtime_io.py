import os
import tempfile
import unittest

from proxy import qz_runtime_io


class RuntimeIoTests(unittest.TestCase):
    def test_capture_helpers_use_qz_var_dir(self):
        old_var_dir = os.environ.get("QZ_VAR_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["QZ_VAR_DIR"] = tmp
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


if __name__ == "__main__":
    unittest.main()
