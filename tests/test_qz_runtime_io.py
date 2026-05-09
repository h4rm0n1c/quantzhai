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
                qz_runtime_io.write_request_capture("req/a:b", "request.json", {"id": 1})
                qz_runtime_io.append_request_capture("req/a:b", "stream.raw", b"one\n")
                qz_runtime_io.append_request_capture("req/a:b", "stream.raw", b"two\n")
                qz_runtime_io.write_dual_capture("latest-dual.txt", "req/a:b", "dual.txt", "dual\n")
                qz_runtime_io.append_dual_capture("latest-dual.log", "req/a:b", "dual.log", "a\n")
                qz_runtime_io.append_dual_capture("latest-dual.log", "req/a:b", "dual.log", b"b\n")
                handles = qz_runtime_io.open_dual_capture_append(
                    "latest-open.raw",
                    request_id="req/a:b",
                    request_name="open.raw",
                    binary=True,
                )
                for handle in handles:
                    handle.write(b"raw\n")
                    handle.close()

                self.assertEqual(qz_runtime_io.capture_path("sample.log").read_text(), "one\ntwo\n")
                self.assertIn('"ok": true', qz_runtime_io.capture_path("sample.json").read_text())
                self.assertEqual(qz_runtime_io.capture_path("sample.log").parent.name, "captures")
                request_dir = qz_runtime_io.capture_dir() / "requests" / "req_a_b"
                self.assertIn('"id": 1', (request_dir / "request.json").read_text())
                self.assertEqual((request_dir / "stream.raw").read_bytes(), b"one\ntwo\n")
                self.assertEqual(qz_runtime_io.capture_path("latest-dual.txt").read_text(), "dual\n")
                self.assertEqual((request_dir / "dual.txt").read_text(), "dual\n")
                self.assertEqual(qz_runtime_io.capture_path("latest-dual.log").read_text(), "a\nb\n")
                self.assertEqual((request_dir / "dual.log").read_bytes(), b"a\nb\n")
                self.assertEqual(qz_runtime_io.capture_path("latest-open.raw").read_bytes(), b"raw\n")
                self.assertEqual((request_dir / "open.raw").read_bytes(), b"raw\n")
                self.assertEqual(qz_runtime_io.capture_policy().as_dict()["mode"], "latest")
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
                qz_runtime_io.write_request_capture("req-1", "request.json", {"id": 1})
                qz_runtime_io.append_request_capture("req-1", "stream.raw", b"one\n")
                qz_runtime_io.write_dual_capture("latest-dual.txt", "req-1", "dual.txt", "dual\n")
                qz_runtime_io.append_dual_capture("latest-dual.log", "req-1", "dual.log", "dual\n")
                handles = qz_runtime_io.open_dual_capture_append("latest-open.raw", "req-1", "open.raw", binary=True)

                self.assertFalse(qz_runtime_io.capture_path("sample.log").exists())
                self.assertFalse(qz_runtime_io.capture_path("sample.json").exists())
                self.assertFalse(qz_runtime_io.request_capture_path("req-1", "request.json").exists())
                self.assertFalse(qz_runtime_io.request_capture_path("req-1", "stream.raw").exists())
                self.assertFalse(qz_runtime_io.capture_path("latest-dual.txt").exists())
                self.assertFalse(qz_runtime_io.request_capture_path("req-1", "dual.txt").exists())
                self.assertEqual(handles, [])
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
