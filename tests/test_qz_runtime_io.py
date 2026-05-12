import json
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


class IncomingHeadersPayloadTests(unittest.TestCase):
    def _fake_headers(self, items):
        class _FakeHeaders:
            def items(self):
                return list(items)
        return _FakeHeaders()

    def test_incoming_headers_payload_includes_all_headers(self):
        handler = type("Handler", (), {
            "headers": self._fake_headers([
                ("Host", "localhost:18180"),
                ("Content-Type", "application/json"),
                ("X-Unknown", "arbitrary"),
            ]),
        })()
        result = qz_runtime_io.incoming_headers_payload(handler)
        self.assertEqual(result["Host"], "localhost:18180")
        self.assertEqual(result["Content-Type"], "application/json")
        self.assertEqual(result["X-Unknown"], "arbitrary")
        self.assertEqual(len(result), 3)

    def test_incoming_headers_payload_includes_session_and_thread(self):
        handler = type("Handler", (), {
            "headers": self._fake_headers([
                ("session_id", "sess-001"),
                ("thread_id", "thread-abc"),
            ]),
        })()
        result = qz_runtime_io.incoming_headers_payload(handler)
        self.assertEqual(result["session_id"], "sess-001")
        self.assertEqual(result["thread_id"], "thread-abc")

    def test_incoming_headers_payload_includes_underscore_and_hyphen_variants(self):
        handler = type("Handler", (), {
            "headers": self._fake_headers([
                ("session_id", "underscore"),
                ("session-id", "hyphen"),
                ("thread_id", "underscore"),
                ("thread-id", "hyphen"),
            ]),
        })()
        result = qz_runtime_io.incoming_headers_payload(handler)
        self.assertEqual(result["session_id"], "underscore")
        self.assertEqual(result["session-id"], "hyphen")
        self.assertEqual(result["thread_id"], "underscore")
        self.assertEqual(result["thread-id"], "hyphen")

    def test_incoming_headers_payload_includes_authorization(self):
        handler = type("Handler", (), {
            "headers": self._fake_headers([
                ("authorization", "Bearer test-token-123"),
            ]),
        })()
        result = qz_runtime_io.incoming_headers_payload(handler)
        self.assertEqual(result["authorization"], "Bearer test-token-123")

    def test_incoming_headers_capture_written_when_capture_on(self):
        old_var_dir = os.environ.get("QZ_VAR_DIR")
        old_capture_mode = os.environ.get("QZ_CAPTURE_MODE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["QZ_VAR_DIR"] = tmp
                os.environ["QZ_CAPTURE_MODE"] = "latest"
                handler = type("Handler", (), {
                    "headers": self._fake_headers([
                        ("session_id", "sess-999"),
                        ("thread-id", "thread-xyz"),
                        ("authorization", "Bearer sekrit"),
                        ("X-Arbitrary", "present"),
                    ]),
                })()
                headers_raw = qz_runtime_io.incoming_headers_payload(handler)
                envelope = {
                    "schema": "qz.incoming.request.capture.v2",
                    "request_id": "qz_req_test",
                    "method": "POST",
                    "path": "/v1/responses",
                    "headers_raw": headers_raw,
                }
                qz_runtime_io.write_dual_capture(
                    "latest-request-headers.json",
                    "qz_req_test",
                    "incoming-request-headers.json",
                    envelope,
                )
                latest_path = qz_runtime_io.capture_path("latest-request-headers.json")
                self.assertTrue(latest_path.exists())
                payload = json.loads(latest_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], "qz.incoming.request.capture.v2")
                self.assertEqual(payload["request_id"], "qz_req_test")
                self.assertEqual(payload["method"], "POST")
                self.assertEqual(payload["path"], "/v1/responses")
                self.assertIn("session_id", payload["headers_raw"])
                self.assertIn("thread-id", payload["headers_raw"])
                self.assertIn("authorization", payload["headers_raw"])
                self.assertIn("X-Arbitrary", payload["headers_raw"])
                self.assertEqual(payload["headers_raw"]["session_id"], "sess-999")
                self.assertEqual(payload["headers_raw"]["thread-id"], "thread-xyz")
                self.assertEqual(payload["headers_raw"]["authorization"], "Bearer sekrit")

                request_dir = qz_runtime_io.request_capture_path("qz_req_test", "incoming-request-headers.json")
                self.assertTrue(request_dir.exists())
                req_payload = json.loads(request_dir.read_text(encoding="utf-8"))
                self.assertEqual(req_payload["headers_raw"]["X-Arbitrary"], "present")
        finally:
            if old_var_dir is None:
                os.environ.pop("QZ_VAR_DIR", None)
            else:
                os.environ["QZ_VAR_DIR"] = old_var_dir
            if old_capture_mode is None:
                os.environ.pop("QZ_CAPTURE_MODE", None)
            else:
                os.environ["QZ_CAPTURE_MODE"] = old_capture_mode

    def test_incoming_headers_capture_not_written_when_capture_off(self):
        old_var_dir = os.environ.get("QZ_VAR_DIR")
        old_capture_mode = os.environ.get("QZ_CAPTURE_MODE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["QZ_VAR_DIR"] = tmp
                os.environ["QZ_CAPTURE_MODE"] = "off"
                handler = type("Handler", (), {
                    "headers": self._fake_headers([("X-Test", "value")]),
                })()
                headers_raw = qz_runtime_io.incoming_headers_payload(handler)
                envelope = {"schema": "qz.incoming.request.capture.v2", "request_id": "r", "method": "POST", "path": "/v1/responses", "headers_raw": headers_raw}
                qz_runtime_io.write_dual_capture("latest-request-headers.json", "r", "incoming-request-headers.json", envelope)
                self.assertFalse(qz_runtime_io.capture_path("latest-request-headers.json").exists())
                self.assertFalse(qz_runtime_io.request_capture_path("r", "incoming-request-headers.json").exists())
        finally:
            if old_var_dir is None:
                os.environ.pop("QZ_VAR_DIR", None)
            else:
                os.environ["QZ_VAR_DIR"] = old_var_dir
            if old_capture_mode is None:
                os.environ.pop("QZ_CAPTURE_MODE", None)
            else:
                os.environ["QZ_CAPTURE_MODE"] = old_capture_mode

    def test_existing_incoming_request_body_capture_still_works(self):
        old_var_dir = os.environ.get("QZ_VAR_DIR")
        old_capture_mode = os.environ.get("QZ_CAPTURE_MODE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["QZ_VAR_DIR"] = tmp
                os.environ["QZ_CAPTURE_MODE"] = "latest"
                body = {"model": "test-model", "input": [{"role": "user", "content": "hello"}]}
                qz_runtime_io.write_dual_capture("latest-request.json", "req_body_test", "incoming-request.json", body)
                latest = json.loads(qz_runtime_io.capture_path("latest-request.json").read_text())
                self.assertEqual(latest["model"], "test-model")
                self.assertEqual(latest["input"][0]["content"], "hello")
                scoped = json.loads(qz_runtime_io.request_capture_path("req_body_test", "incoming-request.json").read_text())
                self.assertEqual(scoped["model"], "test-model")
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
