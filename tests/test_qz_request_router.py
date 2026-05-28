import json
import io
import os
import time
import unittest
from unittest.mock import patch

from proxy.qz_proxy_tools import ProxyLocalToolExecutor, ProxyLocalToolRegistry
from proxy.qz_request_normalization import NOT_PLANNING_MODE_HINT
from proxy.qz_request_router import RequestRouter
from proxy.qz_tool_lifecycle import ToolContinuationResult
from proxy.qz_tools import ToolLifecycleSpec


class ProbeProxyToolExecutor(ProxyLocalToolExecutor):
    function_name = "qz_probe"
    lifecycle = ToolLifecycleSpec(
        name="qz_probe",
        execution="proxy_local",
        public_item_type="qz_probe_call",
        telemetry_name="qz_probe",
        continuation_hops=2,
    )

    def __init__(self):
        self.calls = []

    def started_public_item(self, call, public_index):
        return {
            "id": call.get("id") or f"qz_probe_{public_index}",
            "type": "qz_probe_call",
            "status": "in_progress",
            "call_id": call.get("call_id"),
        }

    def execute(self, call, context):
        self.calls.append({
            "call": call,
            "request_id": context.request_id,
            "counters": context.counters,
            "seen_signatures": context.seen_signatures,
        })
        return ToolContinuationResult(
            public_item={
                "id": "qz_probe_public",
                "type": "qz_probe_call",
                "status": "completed",
                "call_id": call.get("call_id"),
            },
            upstream_items=(
                call,
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": "{\"probe\":\"ok\"}",
                },
            ),
        )


class FakeHandler:
    upstream = "http://127.0.0.1:1"

    def __init__(self, registry):
        self.proxy_tool_registry_factory = lambda _web_runtime: registry


class FakeRouter(RequestRouter):
    def __init__(self, handler, responses):
        super().__init__(handler)
        self.responses = list(responses)
        self.request_bodies = []

    def _web_runtime(self, selected_model=None):
        return object()

    def _call_upstream_json(self, url, body):
        self.request_bodies.append(json.loads(json.dumps(body)))
        payload = self.responses.pop(0)
        return 200, "application/json", json.dumps(payload).encode("utf-8")


class ResponsesAdmissionTests(unittest.TestCase):
    class _Telemetry:
        def __init__(self):
            self.events = []

        def emit(self, event_type, payload):
            self.events.append((event_type, payload))

    class _Headers:
        def __init__(self, body, accept="application/json"):
            self._raw = json.dumps(body).encode("utf-8")
            self._accept = accept

        def get(self, key, default=None):
            if key == "Content-Length":
                return str(len(self._raw))
            if key == "Accept":
                return self._accept
            return default

    class _ModelRouter:
        def status_summary(self, _path):
            return {}

        def apply_reasoning_policy(self, body, _selected_model):
            return body

        def inject_runtime_state(self, body, _client_model):
            return body

    class _Catalog:
        def __init__(self, entries, selected=None, resolve_result=None):
            self.entries = list(entries)
            self.selected = selected
            self._resolve_result = resolve_result

        def resolve(self, query=None):
            if self._resolve_result is not None:
                return self._resolve_result
            for entry in self.entries:
                if query in {entry.get("key"), entry.get("slug"), entry.get("backend_id")}:
                    return entry, "match"
            return None, f"no match for {query}"

    class _Handler:
        path = "/v1/responses"
        upstream = "http://127.0.0.1:1"
        reasoning_stream_format = "raw"

        def __init__(self, body, catalog, initializations=None, guard_startup_boundary=False):
            self.headers = ResponsesAdmissionTests._Headers(body)
            self.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
            self.wfile = io.BytesIO()
            self.telemetry = ResponsesAdmissionTests._Telemetry()
            self._catalog = catalog
            self._initializations = list(initializations or [])
            self._last_initialization = (
                dict(self._initializations[0])
                if self._initializations
                else {"state": "ready", "ready": True, "catalog_ready": True}
            )
            self.startup_ready_seen = bool(self._last_initialization.get("ready"))
            self.guard_startup_boundary = guard_startup_boundary
            self.model_catalog_calls = 0
            self.model_router_calls = 0
            self.backend_calls = 0
            self.sent = []
            self.response_headers = {}
            self.response_status = None
            self.sse_headers = []
            self.send_response_calls = []
            self.end_headers_calls = 0
            self.rate_limit_event_calls = 0
            self.close_connection = False

        def _model_catalog(self):
            self.model_catalog_calls += 1
            if self.guard_startup_boundary and not self.startup_ready_seen:
                raise AssertionError("model catalog touched before proxy startup readiness")
            return self._catalog

        def _model_router(self):
            self.model_router_calls += 1
            if self.guard_startup_boundary and not self.startup_ready_seen:
                raise AssertionError("model router touched before proxy startup readiness")
            return ResponsesAdmissionTests._ModelRouter()

        def _backend(self, authorization=None):
            self.backend_calls += 1
            if self.guard_startup_boundary and not self.startup_ready_seen:
                raise AssertionError("backend touched before proxy startup readiness")
            raise AssertionError("backend should not be touched by these focused tests")

        def _initialization_payload(self):
            if self._initializations:
                self._last_initialization = self._initializations.pop(0)
            if self._last_initialization.get("ready"):
                self.startup_ready_seen = True
            return dict(self._last_initialization)

        def _send_json(self, status, payload, headers=None):
            self.response_headers = dict(headers or {})
            self.sent.append((status, payload))

        def send_response(self, status):
            self.send_response_calls.append(status)
            self.response_status = status

        def send_header(self, key, value):
            self.sse_headers.append((key, value))

        def end_headers(self):
            self.end_headers_calls += 1

        def _send_codex_rate_limit_headers(self):
            self.send_header("x-ratelimit-limit-requests", "999999")

        def _write_codex_rate_limits_event(self):
            self.rate_limit_event_calls += 1
            self.wfile.write(b"event: codex.rate_limits\ndata: {\"type\":\"codex.rate_limits\"}\n\n")

        def _emit_sse_telemetry(self, _chunk, request_id=""):
            pass

    @staticmethod
    def _entry(key, *, backend_id=None, profile_valid=True, profile_error=""):
        entry = {
            "key": key,
            "slug": key,
            "stem": key,
            "backend_id": backend_id or key,
            "label": key,
            "profile_valid": profile_valid,
        }
        if profile_error:
            entry["profile_error"] = profile_error
        return entry

    def _run_responses_request(self, body, catalog):
        handler = self._Handler(body, catalog)
        with (
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
        ):
            RequestRouter(handler).proxy_json_api("/v1/responses")
        self.assertEqual(len(handler.sent), 1)
        return handler.sent[0], handler

    def test_responses_stream_flag_off_proxy_initializing_still_returns_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        initialization = {"state": "initializing", "ready": False, "catalog_ready": False}

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "0"}, clear=False),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status") as build_status,
            patch.object(RequestRouter, "_run_responses_streaming_locally") as run_stream,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi", "stream": True},
                catalog,
                initializations=[initialization],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "proxy not ready")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.model_catalog_calls, 0)
        self.assertEqual(handler.model_router_calls, 0)
        build_status.assert_not_called()
        run_stream.assert_not_called()

    def test_responses_non_stream_flag_off_proxy_initializing_still_returns_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        initialization = {"state": "initializing", "ready": False, "catalog_ready": False}

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "0"}, clear=False),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status") as build_status,
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi"},
                catalog,
                initializations=[initialization],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "proxy not ready")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.model_catalog_calls, 0)
        self.assertEqual(handler.model_router_calls, 0)
        build_status.assert_not_called()
        run_local.assert_not_called()

    def test_responses_stream_flag_on_proxy_startup_waits_then_streams_once(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        initializing = {"state": "initializing", "ready": False, "catalog_ready": False}
        ready_init = {"state": "ready", "ready": True, "catalog_ready": True}
        ready_status = {
            "selected_model_ready": True,
            "backend_loaded_model": "known-model",
            "request_admission_state": "ready",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=ready_status),
            patch.object(RequestRouter, "_run_responses_streaming_locally", return_value={"usage": {}}) as run_stream,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi", "stream": True},
                catalog,
                initializations=[initializing, ready_init],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        stream_bytes = handler.wfile.getvalue()
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.send_response_calls, [200])
        self.assertEqual(
            [header for header in handler.sse_headers if header[0] == "Content-Type"],
            [("Content-Type", "text/event-stream")],
        )
        self.assertEqual(handler.end_headers_calls, 1)
        self.assertEqual(handler.rate_limit_event_calls, 1)
        self.assertEqual(handler.sent, [])
        self.assertIn(b": qz hold-open waiting for proxy startup\n\n", stream_bytes)
        self.assertGreater(handler.model_catalog_calls, 0)
        self.assertGreater(handler.model_router_calls, 0)
        self.assertEqual(handler.backend_calls, 0)
        run_stream.assert_called_once()

    def test_responses_non_stream_startup_wait_does_not_touch_model_paths_before_ready_then_runs_local_once(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        initializing = {"state": "initializing", "ready": False, "catalog_ready": False}
        ready_init = {"state": "ready", "ready": True, "catalog_ready": True}
        ready_status = {
            "selected_model_ready": True,
            "backend_loaded_model": "known-model",
            "request_admission_state": "ready",
        }
        local_response = {
            "id": "resp_ready",
            "object": "response",
            "output": [],
            "usage": {},
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=ready_status),
            patch.object(
                RequestRouter,
                "_run_responses_locally",
                return_value=(200, "application/json", local_response),
            ) as run_local,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi"},
                catalog,
                initializations=[initializing, ready_init],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(handler.sent, [(200, local_response)])
        self.assertEqual(handler.response_headers, {})
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.wfile.getvalue(), b"")
        self.assertGreater(handler.model_catalog_calls, 0)
        self.assertGreater(handler.model_router_calls, 0)
        self.assertEqual(handler.backend_calls, 0)
        run_local.assert_called_once()

    def test_responses_stream_startup_holdopen_timeout_emits_sse_failure(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        initializing = {"state": "initializing", "ready": False, "catalog_ready": False}

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_MAX_SECONDS", 0.0),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status") as build_status,
            patch.object(RequestRouter, "_run_responses_streaming_locally") as run_stream,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi", "stream": True},
                catalog,
                initializations=[initializing],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        stream_bytes = handler.wfile.getvalue()
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.sent, [])
        self.assertIn(b"event: response.failed", stream_bytes)
        self.assertIn(b"proxy startup hold-open timed out", stream_bytes)
        self.assertIn(b"data: [DONE]\n\n", stream_bytes)
        self.assertEqual(handler.model_catalog_calls, 0)
        self.assertEqual(handler.model_router_calls, 0)
        build_status.assert_not_called()
        run_stream.assert_not_called()

    def test_responses_non_stream_startup_wait_timeout_returns_existing_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        initializing = {"state": "initializing", "ready": False, "catalog_ready": False}

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_MAX_SECONDS", 0.0),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status") as build_status,
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi"},
                catalog,
                initializations=[initializing],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "proxy not ready")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.model_catalog_calls, 0)
        self.assertEqual(handler.model_router_calls, 0)
        build_status.assert_not_called()
        run_local.assert_not_called()

    def test_responses_stream_startup_failed_emits_sse_failure(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        failed = {
            "state": "failed",
            "ready": False,
            "catalog_ready": False,
            "error": "catalog load failed",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status") as build_status,
            patch.object(RequestRouter, "_run_responses_streaming_locally") as run_stream,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi", "stream": True},
                catalog,
                initializations=[failed],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        stream_bytes = handler.wfile.getvalue()
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.sent, [])
        self.assertIn(b"event: response.failed", stream_bytes)
        self.assertIn(b"catalog load failed", stream_bytes)
        self.assertIn(b"data: [DONE]\n\n", stream_bytes)
        self.assertEqual(handler.model_catalog_calls, 0)
        self.assertEqual(handler.model_router_calls, 0)
        build_status.assert_not_called()
        run_stream.assert_not_called()

    def test_responses_non_stream_startup_failed_returns_initialization_failed_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        failed = {
            "state": "failed",
            "ready": False,
            "catalog_ready": False,
            "error": "catalog load failed",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status") as build_status,
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._Handler(
                {"model": "known-model", "input": "hi"},
                catalog,
                initializations=[failed],
                guard_startup_boundary=True,
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "proxy initialization failed")
        self.assertEqual(payload["reason"], "catalog load failed")
        self.assertNotIn("Retry-After", handler.response_headers)
        self.assertNotIn("retry_suggested", payload)
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.model_catalog_calls, 0)
        self.assertEqual(handler.model_router_calls, 0)
        build_status.assert_not_called()
        run_local.assert_not_called()

    def test_responses_unknown_model_returns_400_not_503(self):
        catalog = self._Catalog([self._entry("known-model")])

        (status, payload), handler = self._run_responses_request(
            {"model": "missing-model", "input": "hi"},
            catalog,
        )

        self.assertEqual(status, 400)
        self.assertNotEqual(status, 503)
        self.assertEqual(payload["status_code"], 400)
        self.assertEqual(payload["error"], "model not found")
        self.assertEqual(payload["error_code"], "model_not_found")
        self.assertEqual(payload["requested_model"], "missing-model")
        self.assertIn("no match", payload["reason"])
        self.assertIn("known-model", payload["available_models"])
        self.assertNotIn("Retry-After", handler.response_headers)
        self.assertNotIn("retry_suggested", payload)
        self.assertIn(
            "responses_rejected_model_missing",
            [event_type for event_type, _payload in handler.telemetry.events],
        )

    def test_responses_stream_flag_off_loading_still_returns_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        model_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "0"}, clear=False),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=model_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
        ):
            handler = self._Handler({"model": "known-model", "input": "hi", "stream": True}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["status_code"], 503)
        self.assertEqual(payload["error"], "model not ready")
        self.assertEqual(payload["readiness"]["request_admission_state"], "loading")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)

    def test_responses_non_stream_flag_off_loading_still_returns_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        model_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "0"}, clear=False),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=model_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi"}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["readiness"]["request_admission_state"], "loading")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)
        run_local.assert_not_called()

    def test_responses_stream_flag_off_switching_mismatch_returns_retryable_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        model_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "old-model",
            "request_admission_state": "idle",
            "model_switch_state": "loading",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "0"}, clear=False),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=model_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
            patch.object(RequestRouter, "_run_responses_streaming_locally") as run_stream,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi", "stream": True}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["readiness"]["request_admission_state"], "idle")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)
        run_stream.assert_not_called()

    def test_responses_stream_flag_on_loading_holds_open_then_streams_once(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        ready_status = {
            "selected_model_ready": True,
            "backend_loaded_model": "known-model",
            "request_admission_state": "ready",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", side_effect=[loading_status, ready_status]),
            patch.object(RequestRouter, "_run_responses_streaming_locally", return_value={"usage": {}}) as run_stream,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi", "stream": True}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.send_response_calls, [200])
        self.assertEqual(handler.end_headers_calls, 1)
        self.assertEqual(handler.rate_limit_event_calls, 1)
        self.assertIn(("Content-Type", "text/event-stream"), handler.sse_headers)
        self.assertEqual(
            [header for header in handler.sse_headers if header[0] == "Content-Type"],
            [("Content-Type", "text/event-stream")],
        )
        self.assertEqual(handler.sent, [])
        self.assertIn(b": qz hold-open waiting for model readiness\n\n", handler.wfile.getvalue())
        run_stream.assert_called_once()

    def test_responses_non_stream_flag_on_loading_waits_then_runs_local_once(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        ready_status = {
            "selected_model_ready": True,
            "backend_loaded_model": "known-model",
            "request_admission_state": "ready",
        }
        local_response = {
            "id": "resp_ready",
            "object": "response",
            "output": [],
            "usage": {},
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", side_effect=[loading_status, ready_status]),
            patch.object(
                RequestRouter,
                "_run_responses_locally",
                return_value=(200, "application/json", local_response),
            ) as run_local,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi"}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(handler.sent, [(200, local_response)])
        self.assertEqual(handler.response_headers, {})
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.wfile.getvalue(), b"")
        run_local.assert_called_once()

    def test_responses_non_stream_flag_on_loading_timeout_returns_existing_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        model_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_MAX_SECONDS", 0.0),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=model_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi"}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["readiness"]["request_admission_state"], "loading")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.wfile.getvalue(), b"")
        run_local.assert_not_called()

    def test_responses_non_stream_flag_on_switching_timeout_returns_retryable_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        model_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "old-model",
            "request_admission_state": "idle",
            "model_switch_state": "loading",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_MAX_SECONDS", 0.0),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=model_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi"}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["readiness"]["request_admission_state"], "idle")
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.wfile.getvalue(), b"")
        run_local.assert_not_called()

    def test_responses_non_stream_flag_on_terminal_after_wait_returns_existing_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        failed_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "failed",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", side_effect=[loading_status, failed_status]),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi"}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["readiness"]["request_admission_state"], "failed")
        self.assertNotIn("Retry-After", handler.response_headers)
        self.assertNotIn("retry_suggested", payload)
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.wfile.getvalue(), b"")
        run_local.assert_not_called()

    def test_responses_stream_holdopen_timeout_emits_sse_failure_not_late_503(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_MAX_SECONDS", 0.0),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", side_effect=[loading_status, loading_status]),
            patch.object(RequestRouter, "_run_responses_streaming_locally") as run_stream,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi", "stream": True}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        stream_bytes = handler.wfile.getvalue()
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.sent, [])
        self.assertIn(b"event: response.failed", stream_bytes)
        self.assertIn(b"model readiness hold-open timed out", stream_bytes)
        self.assertIn(b"data: [DONE]\n\n", stream_bytes)
        run_stream.assert_not_called()

    def test_responses_stream_holdopen_terminal_failure_emits_sse_failure(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        failed_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "failed_gpu_not_available",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", side_effect=[loading_status, failed_status]),
            patch.object(RequestRouter, "_run_responses_streaming_locally") as run_stream,
        ):
            handler = self._Handler({"model": "known-model", "input": "hi", "stream": True}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        stream_bytes = handler.wfile.getvalue()
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.send_response_calls, [200])
        self.assertEqual(handler.sent, [])
        self.assertIn(b"event: response.failed", stream_bytes)
        self.assertIn(b"failed_gpu_not_available", stream_bytes)
        self.assertIn(b"data: [DONE]\n\n", stream_bytes)
        run_stream.assert_not_called()

    def test_responses_unknown_model_stream_flag_on_returns_400_no_sse(self):
        catalog = self._Catalog([self._entry("known-model")])

        with patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False):
            (status, payload), handler = self._run_responses_request(
                {"model": "missing-model", "input": "hi", "stream": True},
                catalog,
            )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "model not found")
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_responses_unknown_model_non_stream_flag_on_returns_400_no_wait(self):
        catalog = self._Catalog([self._entry("known-model")])

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_model_status.build_model_status") as build_status,
        ):
            (status, payload), handler = self._run_responses_request(
                {"model": "missing-model", "input": "hi"},
                catalog,
            )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "model not found")
        self.assertIsNone(handler.response_status)
        self.assertEqual(handler.wfile.getvalue(), b"")
        build_status.assert_not_called()

    def test_responses_failed_model_503_does_not_retry_suggest(self):
        entry = self._entry("known-model")
        catalog = self._Catalog([entry], selected=entry)
        model_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "failed",
        }

        with (
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=model_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
        ):
            handler = self._Handler({"model": "known-model", "input": "hi"}, catalog)
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        self.assertEqual(payload["status_code"], 503)
        self.assertEqual(payload["readiness"]["request_admission_state"], "failed")
        self.assertNotIn("Retry-After", handler.response_headers)
        self.assertNotIn("retry_suggested", payload)

    def test_responses_profile_backend_missing_stays_503(self):
        bad_profile = self._entry(
            "profile-model",
            profile_valid=False,
            profile_error="symlink target not found in scanned GGUF models: target.gguf",
        )
        catalog = self._Catalog([bad_profile], resolve_result=(bad_profile, "match"))

        (status, payload), handler = self._run_responses_request(
            {"model": "profile-model", "input": "hi"},
            catalog,
        )

        self.assertEqual(status, 503)
        self.assertEqual(payload["status_code"], 503)
        self.assertEqual(payload["error"], "profile backend missing")
        self.assertEqual(payload["error_code"], "profile_backend_missing")
        self.assertIn("fix", payload)
        self.assertNotIn("Retry-After", handler.response_headers)
        self.assertNotIn("retry_suggested", payload)


class RequestRouterProxyLocalToolTests(unittest.TestCase):
    def test_non_streaming_proxy_local_loop_is_not_web_search_specific(self):
        executor = ProbeProxyToolExecutor()
        registry = ProxyLocalToolRegistry([executor])
        router = FakeRouter(
            FakeHandler(registry),
            responses=[
                {
                    "id": "resp_probe_call",
                    "object": "response",
                    "output": [{
                        "id": "fc_probe",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_probe",
                        "name": "qz_probe",
                        "arguments": "{\"value\":1}",
                    }],
                    "usage": {},
                },
                {
                    "id": "resp_final",
                    "object": "response",
                    "output": [{
                        "id": "msg_final",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "probe done", "annotations": []}],
                    }],
                    "usage": {},
                },
            ],
        )

        status, content_type, out = router._run_responses_locally(
            {
                "model": "fake",
                "input": [{
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "probe"}],
                }],
                "tools": [{"type": "function", "name": "qz_probe"}],
            },
            "fake",
            request_id="qz_req_probe",
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(executor.calls[0]["request_id"], "qz_req_probe")
        self.assertEqual(out["output"][0]["type"], "qz_probe_call")
        self.assertEqual(out["output"][1]["type"], "message")
        self.assertEqual(router.request_bodies[1]["input"][-1]["type"], "function_call_output")
        self.assertEqual(router.request_bodies[1]["input"][-1]["call_id"], "call_probe")

    def test_non_streaming_renormalization_preserves_disabled_system_prompt(self):
        router = FakeRouter(
            FakeHandler(ProxyLocalToolRegistry([])),
            responses=[{
                "id": "resp_final",
                "object": "response",
                "output": [{
                    "id": "msg_final",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                }],
                "usage": {},
            }],
        )

        router._run_responses_locally(
            {
                "model": "fake",
                "input": [{
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "direct"}],
                }],
                "tools": [],
            },
            "fake",
            selected_model={"overrides": {"disable_system_prompt": True}},
            request_id="qz_req_blank",
        )

        self.assertEqual(len(router.request_bodies), 1)
        self.assertEqual(router.request_bodies[0]["instructions"], NOT_PLANNING_MODE_HINT)
        self.assertTrue(router.request_bodies[0]["metadata"]["qz_prompt_policy"]["disable_system_prompt"])


class WebSearchCapabilitiesEndpointTests(unittest.TestCase):
    def test_get_web_search_capabilities_returns_same_schema(self):
        from proxy.qz_tool_web import WebSearchRuntime, build_web_search_capabilities

        runtime = WebSearchRuntime(
            search_config_profiles={"docs_live": {"categories": ["it"], "engines": ["mdn"]}},
            budget_mode_table={"normal": {"max_results": 5}},
        )
        expected_schema = build_web_search_capabilities(runtime)["schema"]

        class Handler:
            path = "/qz/web-search/capabilities"
            sent = None

            def _send_json(self, status, payload):
                self.sent = (status, payload)

            def _handle_ollama_get(self):
                return False

            def _handle_ready_get(self):
                return False

        class Router(RequestRouter):
            def _log_request_path(self, method):
                pass

            def _web_runtime(self, selected_model=None):
                return runtime

        handler = Handler()
        Router(handler).handle_get()
        status, payload = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], expected_schema)
        self.assertIn("docs_live", payload["profiles"])

    def test_get_web_search_capabilities_failure_is_read_only_json(self):
        class Handler:
            path = "/qz/web-search/capabilities"
            sent = None

            def _send_json(self, status, payload):
                self.sent = (status, payload)

            def _handle_ollama_get(self):
                return False

            def _handle_ready_get(self):
                return False

        class Router(RequestRouter):
            def _log_request_path(self, method):
                pass

            def _web_runtime(self, selected_model=None):
                raise RuntimeError("agent api probe failed")

        handler = Handler()
        Router(handler).handle_get()
        status, payload = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], "qz.web_search.capabilities.v1")
        self.assertFalse(payload["ok"])


class SignalDecisionEmissionTests(unittest.TestCase):
    """Tests for RequestRouter._emit_signal_decisions helper."""

    def setUp(self):
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        self.FeedbackChannel = FeedbackChannel
        self.FeedbackVisibility = FeedbackVisibility
        self.SignalDecision = SignalDecision

    class FakeTelemetry:
        def __init__(self):
            self.emitted = []

        def emit(self, event_type, payload):
            self.emitted.append((event_type, payload))

    class FakeHandler:
        def __init__(self):
            self.telemetry = SignalDecisionEmissionTests.FakeTelemetry()

    def test_emits_telemetry_for_telemetry_channel_signal(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        signal = self.SignalDecision(
            event_type="test_event",
            payload={"key": "val"},
            visibility=self.FeedbackVisibility.OPERATOR,
            channel=self.FeedbackChannel.TELEMETRY,
        )

        router._emit_signal_decisions([signal], request_id="req_123")

        self.assertEqual(len(handler.telemetry.emitted), 1)
        event_type, payload = handler.telemetry.emitted[0]
        self.assertEqual(event_type, "test_event")
        self.assertEqual(payload["key"], "val")
        self.assertEqual(payload["request_id"], "req_123")

    def test_ignores_non_telemetry_channel_signals(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        signal = self.SignalDecision(
            event_type="ignored_event",
            payload={},
            visibility=self.FeedbackVisibility.MODEL,
            channel=self.FeedbackChannel.FUNCTION_CALL_OUTPUT,
        )

        router._emit_signal_decisions([signal])

        self.assertEqual(len(handler.telemetry.emitted), 0)

    def test_handles_empty_signal_list(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        router._emit_signal_decisions([])
        self.assertEqual(len(handler.telemetry.emitted), 0)

    def test_robust_to_malformed_signals(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        # Should not crash on None or non-SignalDecision items
        router._emit_signal_decisions([None, "not-a-signal", 42])
        self.assertEqual(len(handler.telemetry.emitted), 0)


class SandboxAdvisoryHelperTests(unittest.TestCase):
    """Unit tests for RequestRouter._model_visible_native_advisories()."""

    class _FakeTelemetry:
        def __init__(self):
            self.emitted = []

        def emit(self, event_type, payload):
            self.emitted.append((event_type, payload))

    def _make_router(self):
        from proxy.qz_proxy_tools import ProxyLocalToolRegistry
        handler = FakeHandler(ProxyLocalToolRegistry([]))
        handler.telemetry = self._FakeTelemetry()
        return RequestRouter(handler)

    def _make_sandbox_signal(self, call_id="call_ro", confidence="high"):
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        return SignalDecision(
            event_type="tool_sandbox_denied",
            payload={
                "call_id": call_id,
                "tool": "exec_command",
                "classifier": "sandbox_denied_readonly_fs",
                "matched_string": "Read-only file system",
                "exit_code": 1,
                "output_preview": "...Read-only file system...",
                "confidence": confidence,
            },
            visibility=FeedbackVisibility.OPERATOR,
            channel=FeedbackChannel.TELEMETRY,
            confidence=confidence,
        )

    def _make_connection_refused_signal(self, call_id="call_conn"):
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        return SignalDecision(
            event_type="tool_connection_failed",
            payload={
                "call_id": call_id,
                "tool": "exec_command",
                "classifier": "native_tool_connection_refused",
                "matched_string": "Connection refused",
                "exit_code": 1,
                "output_preview": "...Connection refused...",
                "confidence": "medium",
            },
            visibility=FeedbackVisibility.OPERATOR,
            channel=FeedbackChannel.TELEMETRY,
            confidence="medium",
        )

    def _make_request_permissions_signal(
        self,
        call_id="call_perm",
        classifier="request_permissions_denied_or_unavailable",
        outcome="denied_or_unavailable",
        confidence="high",
    ):
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        return SignalDecision(
            event_type="request_permissions_outcome",
            payload={
                "call_id": call_id,
                "tool": "request_permissions",
                "classifier": classifier,
                "outcome": outcome,
                "scope": "turn",
                "strict_auto_review": False,
                "permission_summary": {"network": False, "file_system": False},
                "output_preview": '{"permissions":{"network":null,"file_system":null},"scope":"turn"}',
                "confidence": confidence,
            },
            visibility=FeedbackVisibility.OPERATOR,
            channel=FeedbackChannel.TELEMETRY,
            confidence=confidence,
        )

    def test_sandbox_denied_high_confidence_produces_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertEqual(len(advisories), 1)

    def test_advisory_is_function_call_output(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertEqual(advisories[0]["type"], "function_call_output")

    def test_advisory_uses_original_call_id(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal("call_ro_original")])
        self.assertEqual(advisories[0]["call_id"], "call_ro_original")

    def test_advisory_text_mentions_readonly_filesystem(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertIn("read-only filesystem", advisories[0]["output"].lower())

    def test_advisory_text_mentions_not_retrying(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertIn("retry", advisories[0]["output"].lower())

    def test_advisory_is_plain_text_not_json_error(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        output = advisories[0]["output"]
        self.assertIsInstance(output, str)
        # Must not be a JSON {"ok": false, ...} error payload
        self.assertNotIn('"ok"', output)
        try:
            parsed = json.loads(output)
            # If it somehow parses as JSON, it must not have "ok"
            self.assertNotIn("ok", parsed)
        except (ValueError, KeyError):
            pass  # plain text is the expected form

    def test_connection_refused_produces_no_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_connection_refused_signal()])
        self.assertEqual(advisories, [])

    def test_request_permissions_denied_or_unavailable_produces_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_request_permissions_signal()])
        self.assertEqual(len(advisories), 1)
        self.assertEqual(advisories[0]["type"], "function_call_output")
        self.assertEqual(advisories[0]["call_id"], "call_perm")
        self.assertIn("denied or unavailable", advisories[0]["output"])

    def test_request_permissions_granted_produces_no_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([
            self._make_request_permissions_signal(
                classifier="request_permissions_granted",
                outcome="granted",
            )
        ])
        self.assertEqual(advisories, [])

    def test_request_permissions_non_high_confidence_produces_no_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([
            self._make_request_permissions_signal(confidence="medium")
        ])
        self.assertEqual(advisories, [])

    def test_non_high_confidence_sandbox_signal_produces_no_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal(confidence="medium")])
        self.assertEqual(advisories, [])

    def test_duplicate_call_id_produces_one_advisory(self):
        router = self._make_router()
        signals = [self._make_sandbox_signal("call_same"), self._make_sandbox_signal("call_same")]
        advisories = router._model_visible_native_advisories(signals)
        self.assertEqual(len(advisories), 1)

    def test_duplicate_permission_call_id_produces_one_advisory(self):
        router = self._make_router()
        signals = [
            self._make_request_permissions_signal("call_same"),
            self._make_request_permissions_signal("call_same"),
        ]
        advisories = router._model_visible_native_advisories(signals)
        self.assertEqual(len(advisories), 1)

    def test_empty_signals_produces_no_advisory(self):
        router = self._make_router()
        self.assertEqual(router._model_visible_native_advisories([]), [])

    def test_malformed_signals_do_not_crash(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([None, "bad", 42, {}])
        self.assertEqual(advisories, [])

    def test_multiple_different_call_ids_produce_multiple_advisories(self):
        router = self._make_router()
        signals = [self._make_sandbox_signal("call_a"), self._make_sandbox_signal("call_b")]
        advisories = router._model_visible_native_advisories(signals)
        self.assertEqual(len(advisories), 2)
        call_ids = {a["call_id"] for a in advisories}
        self.assertIn("call_a", call_ids)
        self.assertIn("call_b", call_ids)

    def test_connection_refused_mixed_with_sandbox_denied(self):
        """Only sandbox_denied triggers advisory; connection_refused does not."""
        router = self._make_router()
        signals = [
            self._make_sandbox_signal("call_ro"),
            self._make_connection_refused_signal("call_conn"),
        ]
        advisories = router._model_visible_native_advisories(signals)
        self.assertEqual(len(advisories), 1)
        self.assertEqual(advisories[0]["call_id"], "call_ro")

    def test_telemetry_is_not_suppressed_by_advisory(self):
        """_emit_signal_decisions still emits tool_sandbox_denied even when advisory is added."""
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        handler = FakeHandler.__new__(FakeHandler)
        handler.telemetry = self._FakeTelemetry()
        handler.proxy_tool_registry_factory = None
        router = RequestRouter(handler)

        signal = self._make_sandbox_signal("call_tel")
        router._emit_signal_decisions([signal], request_id="req_abc")
        router._model_visible_native_advisories([signal])

        # Telemetry emitted once by _emit_signal_decisions
        emitted_events = [e[0] for e in handler.telemetry.emitted]
        self.assertIn("tool_sandbox_denied", emitted_events)


class SandboxAdvisoryInjectionTests(unittest.TestCase):
    """Integration tests: advisory items are injected into body input for sandbox-denied signals."""

    _READONLY_FS_OUTPUT = (
        "Chunk ID: 0c3a9b\n"
        "Wall time: 0.0000 seconds\n"
        "Process exited with code 1\n"
        "Original token count: 16\n"
        "Output:\n"
        "/bin/bash: line 1: /etc/qz-denied-test: Read-only file system\n"
    )
    _CONN_REFUSED_OUTPUT = (
        "Chunk ID: ab12cd\n"
        "Wall time: 0.0010 seconds\n"
        "Process exited with code 1\n"
        "Output:\n"
        "curl: (7) Failed to connect to 127.0.0.1 port 18180: Connection refused\n"
    )
    _REQUEST_PERMISSIONS_EMPTY_OUTPUT = json.dumps({
        "permissions": {
            "network": None,
            "file_system": None,
        },
        "scope": "turn",
    })

    def _classify_and_advise(self, input_items):
        """Classify input_items and return the advisory items a router would produce."""
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        from proxy.qz_proxy_tools import ProxyLocalToolRegistry
        handler = FakeHandler(ProxyLocalToolRegistry([]))
        router = RequestRouter(handler)
        signals = classify_native_tool_output_signals(input_items)
        return router._model_visible_native_advisories(signals)

    def test_sandbox_denied_input_produces_advisory(self):
        input_items = [
            {"type": "function_call", "call_id": "call_denied", "name": "exec_command",
             "arguments": '{"cmd": "echo hi > /etc/test"}'},
            {"type": "function_call_output", "call_id": "call_denied",
             "output": self._READONLY_FS_OUTPUT},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(len(advisories), 1)
        advisory = advisories[0]
        self.assertEqual(advisory["type"], "function_call_output")
        self.assertEqual(advisory["call_id"], "call_denied")
        self.assertIn("read-only filesystem", advisory["output"].lower())

    def test_advisory_added_to_body_input(self):
        """Advisory items are appended after the existing input items."""
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        from proxy.qz_proxy_tools import ProxyLocalToolRegistry
        handler = FakeHandler(ProxyLocalToolRegistry([]))
        router = RequestRouter(handler)

        input_items = [
            {"type": "function_call_output", "call_id": "call_ro",
             "output": self._READONLY_FS_OUTPUT},
        ]
        body = {"input": list(input_items)}
        signals = classify_native_tool_output_signals(input_items)
        advisories = router._model_visible_native_advisories(signals)

        self.assertEqual(len(advisories), 1)
        # Simulate the injection (as done in proxy_json_api)
        body["input"] = list(input_items) + advisories

        self.assertEqual(len(body["input"]), 2)
        self.assertEqual(body["input"][0]["type"], "function_call_output")
        self.assertEqual(body["input"][1]["type"], "function_call_output")
        self.assertEqual(body["input"][1]["call_id"], "call_ro")

    def test_normal_output_produces_no_advisory(self):
        input_items = [
            {"type": "function_call_output", "call_id": "call_ok",
             "output": "Process exited with code 0\nOutput:\nall good\n"},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(advisories, [])

    def test_permission_denied_does_not_add_advisory(self):
        input_items = [
            {"type": "function_call_output", "call_id": "call_perm",
             "output": "Process exited with code 1\nOutput:\nbash: permission denied\n"},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(advisories, [])

    def test_connection_refused_produces_no_advisory(self):
        input_items = [
            {"type": "function_call_output", "call_id": "call_conn",
             "output": self._CONN_REFUSED_OUTPUT},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(advisories, [])

    def test_request_permissions_empty_output_produces_advisory(self):
        input_items = [
            {"type": "function_call", "call_id": "call_perm", "name": "request_permissions",
             "arguments": '{"reason": "need network", "permissions": {"network": {"enabled": true}}}'},
            {"type": "function_call_output", "call_id": "call_perm",
             "output": self._REQUEST_PERMISSIONS_EMPTY_OUTPUT},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(len(advisories), 1)
        self.assertEqual(advisories[0]["type"], "function_call_output")
        self.assertEqual(advisories[0]["call_id"], "call_perm")
        self.assertIn("non-escalated", advisories[0]["output"])
        self.assertNotIn("response.custom_tool_call_input.done", json.dumps(advisories))

    def test_original_input_items_not_mutated_by_advisory_path(self):
        import copy
        input_items = [
            {"type": "function_call_output", "call_id": "call_ro",
             "output": self._READONLY_FS_OUTPUT},
        ]
        original = copy.deepcopy(input_items)
        self._classify_and_advise(input_items)
        self.assertEqual(input_items, original)


class ToolSchemaTelemetryRouterTests(unittest.TestCase):
    """Tests for RequestRouter._emit_schema_normalization_telemetry helper."""

    class FakeTelemetry:
        def __init__(self):
            self.emitted = []

        def emit(self, event_type, payload):
            self.emitted.append((event_type, payload))

    class FakeHandlerWithTelemetry:
        def __init__(self):
            self.telemetry = ToolSchemaTelemetryRouterTests.FakeTelemetry()

    def _router(self):
        return RequestRouter(self.FakeHandlerWithTelemetry())

    def _make_report(self, **kwargs):
        from proxy.qz_tool_request import ToolRequestNormalizationReport
        return ToolRequestNormalizationReport(**kwargs)

    def test_emits_event_when_replaced_nonempty(self):
        router = self._router()
        report = self._make_report(replaced=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_1")

        self.assertEqual(len(router.handler.telemetry.emitted), 1)
        event_type, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(event_type, "tool_schema_replaced")
        self.assertIn("web_search", payload["replaced"])

    def test_emits_event_when_translated_nonempty(self):
        router = self._router()
        report = self._make_report(translated=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_2")

        event_type, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(event_type, "tool_schema_replaced")
        self.assertIn("web_search", payload["translated"])

    def test_emits_event_when_deduped_nonempty(self):
        router = self._router()
        report = self._make_report(deduped=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_3")

        event_type, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(event_type, "tool_schema_replaced")
        self.assertIn("web_search", payload["deduped"])

    def test_payload_has_source_field(self):
        router = self._router()
        report = self._make_report(replaced=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_4")

        _, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(payload["source"], "tool_schema_normalizer")

    def test_payload_has_request_id(self):
        router = self._router()
        report = self._make_report(translated=("apply_patch",))

        router._emit_schema_normalization_telemetry(report, request_id="req_xyz")

        _, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(payload["request_id"], "req_xyz")

    def test_payload_has_dropped_list_and_count(self):
        router = self._router()
        report = self._make_report(dropped=("write_stdin(no live exec session)",))

        router._emit_schema_normalization_telemetry(report, request_id="req_5")

        _, payload = router.handler.telemetry.emitted[0]
        self.assertIsInstance(payload["dropped"], list)
        self.assertIn("write_stdin(no live exec session)", payload["dropped"])
        self.assertEqual(payload["dropped_count"], 1)

    def test_no_emit_when_nothing_changed(self):
        router = self._router()
        report = self._make_report()  # all empty

        router._emit_schema_normalization_telemetry(report, request_id="req_6")

        self.assertEqual(len(router.handler.telemetry.emitted), 0)

    def test_payload_does_not_include_full_schema(self):
        """Only names/counts go into telemetry — not full schema dicts."""
        router = self._router()
        report = self._make_report(replaced=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_7")

        _, payload = router.handler.telemetry.emitted[0]
        # Values must all be scalars or lists of strings, not dicts
        for val in payload.values():
            if isinstance(val, list):
                for item in val:
                    self.assertIsInstance(item, str, f"non-string in payload list: {item}")


class NonStreamingDroppedToolTests(unittest.TestCase):
    """
    Non-streaming path: dropped/unknown tool errors injected for non-proxy-local
    items in _run_responses_locally.

    The loop condition requires at least one proxy-local call to keep running
    (otherwise _run_responses_locally exits early with a clean final response).
    We use the existing ProbeProxyToolExecutor as the proxy-local anchor.
    """

    class FakeTelemetry:
        def __init__(self):
            self.emitted: list[tuple[str, dict]] = []

        def emit(self, event_type, payload):
            self.emitted.append((event_type, dict(payload)))

    class FakeHandlerWithTelemetry:
        upstream = "http://127.0.0.1:1"

        def __init__(self, registry):
            self.proxy_tool_registry_factory = lambda _web_runtime: registry
            self.telemetry = NonStreamingDroppedToolTests.FakeTelemetry()

    def _make_router(self, responses):
        executor = ProbeProxyToolExecutor()
        registry = ProxyLocalToolRegistry([executor])
        handler = self.FakeHandlerWithTelemetry(registry)
        router = FakeRouter(handler, responses)
        return router

    def _dropped_and_probe_response(self, dropped_name="some_dropped_tool", call_id="call_dropped"):
        """
        Upstream returns a proxy-local qz_probe call AND a non-proxy-local call
        whose name is in dropped_tool_names. The non-proxy-local item triggers
        the elif-error branch; the probe item keeps the loop alive.
        """
        return [
            {
                "id": "resp_mixed",
                "object": "response",
                "output": [
                    {
                        "id": "fc_probe",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_probe",
                        "name": "qz_probe",
                        "arguments": "{\"value\":1}",
                    },
                    {
                        "id": "fc_dropped",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": call_id,
                        "name": dropped_name,
                        "arguments": "{}",
                    },
                ],
                "usage": {},
            },
            {
                "id": "resp_final",
                "object": "response",
                "output": [{
                    "id": "msg_final",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                }],
                "usage": {},
            },
        ]

    def _run(self, dropped_name="some_dropped_tool", call_id="call_dropped"):
        router = self._make_router(self._dropped_and_probe_response(dropped_name, call_id))
        router._run_responses_locally(
            {
                "model": "fake",
                "input": [{"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": "hi"}]}],
                "tools": [{"type": "function", "name": "qz_probe"}],
                "metadata": {"qz_dropped_tool_names": [dropped_name]},
            },
            "fake",
            request_id="qz_req_drop_test",
        )
        return router

    def test_dropped_tool_error_injected_not_original_item(self):
        """Error result goes into next hop input; the original function_call is dropped."""
        router = self._run()

        second_request = router.request_bodies[1]
        input_types = [item.get("type") for item in second_request["input"]]
        # The error result is a function_call_output
        self.assertIn("function_call_output", input_types,
                      "expected error function_call_output in second hop input")

    def test_dropped_tool_original_item_not_in_next_input(self):
        """The original dropped function_call must not appear in next hop input."""
        router = self._run(dropped_name="some_dropped_tool")

        second_request = router.request_bodies[1]
        for item in second_request["input"]:
            if item.get("type") == "function_call" and item.get("name") == "some_dropped_tool":
                self.fail("original dropped function_call should not be in next hop input")

    def test_dropped_tool_emits_tool_call_error_event(self):
        """telemetry.emit('tool_call_error', ...) fires for the dropped item."""
        router = self._run()

        telemetry_events = router.handler.telemetry.emitted
        error_events = [e for e in telemetry_events if e[0] == "tool_call_error"]
        self.assertTrue(error_events, "expected at least one tool_call_error telemetry event")

    def test_dropped_tool_telemetry_has_tool_name(self):
        router = self._run(dropped_name="some_dropped_tool")

        error_events = [p for t, p in router.handler.telemetry.emitted if t == "tool_call_error"]
        self.assertTrue(error_events)
        self.assertEqual(error_events[0]["tool"], "some_dropped_tool")

    def test_dropped_tool_telemetry_has_call_id(self):
        router = self._run(dropped_name="some_dropped_tool", call_id="call_abc")

        error_events = [p for t, p in router.handler.telemetry.emitted if t == "tool_call_error"]
        self.assertTrue(error_events)
        self.assertEqual(error_events[0]["call_id"], "call_abc")

    def test_dropped_tool_telemetry_has_source(self):
        router = self._run()

        error_events = [p for t, p in router.handler.telemetry.emitted if t == "tool_call_error"]
        self.assertTrue(error_events)
        self.assertEqual(error_events[0]["source"], "tool_decision")

    def test_dropped_tool_telemetry_has_request_id(self):
        router = self._run()

        error_events = [p for t, p in router.handler.telemetry.emitted if t == "tool_call_error"]
        self.assertTrue(error_events)
        self.assertEqual(error_events[0]["request_id"], "qz_req_drop_test")

    def test_unknown_tool_error_injected(self):
        """
        A tool that is not in dropped_tool_names AND not known to the registry
        (step 5 of completed_call_decision) also gets an error injected.
        """
        router = self._make_router([
            {
                "id": "resp_unknown",
                "object": "response",
                "output": [
                    {
                        "id": "fc_probe",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_probe",
                        "name": "qz_probe",
                        "arguments": "{\"value\":1}",
                    },
                    {
                        "id": "fc_unknown",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_unknown_xyz",
                        "name": "totally_unknown_tool",
                        "arguments": "{}",
                    },
                ],
                "usage": {},
            },
            {
                "id": "resp_final",
                "object": "response",
                "output": [{
                    "id": "msg_final",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                }],
                "usage": {},
            },
        ])
        router._run_responses_locally(
            {
                "model": "fake",
                "input": [{"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": "hi"}]}],
                "tools": [{"type": "function", "name": "qz_probe"}],
                "metadata": {},
            },
            "fake",
            request_id="qz_req_unknown_test",
        )

        second_request = router.request_bodies[1]
        input_types = [item.get("type") for item in second_request["input"]]
        self.assertIn("function_call_output", input_types,
                      "unknown tool should have error function_call_output in next hop input")
        error_events = [p for t, p in router.handler.telemetry.emitted if t == "tool_call_error"]
        self.assertTrue(error_events, "unknown tool should emit tool_call_error telemetry")
        self.assertEqual(error_events[0]["source"], "tool_decision")


class HoldOpenDeadlineSharingTests(unittest.TestCase):
    """
    Slice 6: one aggregate hold-open deadline shared across layered wait phases.

    All four wait helpers now accept an optional `deadline` kwarg.  When called
    from proxy_json_api the same deadline object is threaded through both the
    startup-wait phase and the selected-model-wait phase.  These tests verify:

    1. Stream helper respects an externally supplied expired deadline (immediate SSE failure).
    2. Non-stream helper respects an externally supplied expired deadline (immediate False).
    3. Integration: startup wait succeeds → model wait sees same exhausted deadline → stream SSE failure.
    4. Integration: startup wait succeeds → model wait sees same exhausted deadline → non-stream falls back.
    5. Positive composition: adequate budget across both phases → success.
    6. Guard: helpers called *without* a passed deadline still work (backward compat).
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    class _Wfile:
        def __init__(self):
            self.buf = io.BytesIO()

        def write(self, b):
            self.buf.write(b)

        def flush(self):
            pass

        def getvalue(self):
            return self.buf.getvalue()

    def _make_sse_router(self, wfile=None):
        """Return a minimal RequestRouter wired to write SSE into wfile."""
        wfile = wfile or self._Wfile()

        class _Handler:
            def __init__(self_h):
                self_h.wfile = wfile
                self_h.close_connection = False

            def _emit_sse_telemetry(self_h, _chunk, request_id=""):
                pass

        return RequestRouter(_Handler()), wfile

    def _make_catalog_entry(self, key="known-model"):
        return {
            "key": key,
            "slug": key,
            "backend_id": key,
            "label": key,
            "profile_valid": True,
        }

    def _make_handler_for_integration(self, body, catalog, initializations=None):
        return ResponsesAdmissionTests._Handler(
            body,
            catalog,
            initializations=initializations,
        )

    # ------------------------------------------------------------------
    # 1. Stream helper — expired deadline → immediate SSE failure
    # ------------------------------------------------------------------

    def test_stream_hold_open_ready_respects_passed_expired_deadline(self):
        router, wfile = self._make_sse_router()
        entry = self._make_catalog_entry()
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }

        def _build_status(_h):
            return loading_status

        def _identity(_key, _backend, _loaded):
            return False

        with patch("proxy.qz_request_router.write_request_capture"):
            result = router._hold_open_responses_stream_until_ready(
                selected_model=entry,
                requested_model="known-model",
                request_id="req_test",
                initial_model_status=loading_status,
                build_model_status=_build_status,
                identity_matches_loaded=_identity,
                reset_capture=True,
                deadline=time.monotonic() - 10.0,  # already expired
            )

        self.assertFalse(result)
        stream_bytes = wfile.getvalue()
        self.assertIn(b"event: response.failed", stream_bytes)
        self.assertIn(b"data: [DONE]\n\n", stream_bytes)
        # Failure must be "timeout", not terminal-failure reason
        self.assertIn(b"timed out", stream_bytes)

    # ------------------------------------------------------------------
    # 2. Non-stream helper — expired deadline → immediate False
    # ------------------------------------------------------------------

    def test_non_stream_wait_respects_passed_expired_deadline(self):
        router, _ = self._make_sse_router()
        entry = self._make_catalog_entry()
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        build_calls = []

        def _build_status(_h):
            build_calls.append(1)
            return loading_status

        def _identity(_key, _backend, _loaded):
            return False

        result, returned_status = router._wait_responses_until_ready_before_forward(
            selected_model=entry,
            initial_model_status=loading_status,
            build_model_status=_build_status,
            identity_matches_loaded=_identity,
            deadline=time.monotonic() - 10.0,  # already expired
        )

        self.assertFalse(result)
        # Should have returned immediately — no extra build_model_status polls beyond initial
        self.assertEqual(build_calls, [])

    # ------------------------------------------------------------------
    # 3. Integration stream: startup succeeds, phase 2 sees exhausted budget
    # ------------------------------------------------------------------

    def test_stream_stacked_waits_share_budget_phase2_sees_no_remaining_time(self):
        """
        time.monotonic is mocked so that:
        - deadline is established at T=100 → deadline = 370.0
        - phase 1 startup: sees T=100, init is ready → returns immediately
        - T jumps to 380 (past deadline)
        - phase 2 model wait: sees T=380 >= 370 → immediate SSE failure

        Under the OLD behavior (fresh deadline per helper), phase 2 would compute
        deadline = 380 + 270 = 650 and would NOT time out immediately.
        """
        entry = self._make_catalog_entry()
        catalog = ResponsesAdmissionTests._Catalog([entry], selected=entry)
        ready_init = {"state": "ready", "ready": True, "catalog_ready": True}
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        ready_status = {
            "selected_model_ready": True,
            "backend_loaded_model": "known-model",
            "request_admission_state": "ready",
        }

        # time.monotonic side effects:
        # [0] deadline establishment: 100.0
        # [1] phase 1 startup loop first iteration: 100.0 → keepalive written, init ready → return
        # [2] phase 2 model loop first iteration: 380.0 → past deadline → timeout
        # [3+] additional calls if needed
        mono_values = [100.0, 100.0, 380.0, 380.0, 380.0]

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_MAX_SECONDS", 270.0),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.time.monotonic", side_effect=mono_values),
            patch("proxy.qz_request_router.time.sleep"),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=loading_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch.object(RequestRouter, "_run_responses_streaming_locally") as run_stream,
        ):
            handler = self._make_handler_for_integration(
                {"model": "known-model", "input": "hi", "stream": True},
                catalog,
                initializations=[ready_init],
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        stream_bytes = handler.wfile.getvalue()
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.sent, [])
        self.assertIn(b"event: response.failed", stream_bytes)
        self.assertIn(b"timed out", stream_bytes)
        self.assertIn(b"data: [DONE]\n\n", stream_bytes)
        # Streaming runtime must NOT have been invoked
        run_stream.assert_not_called()

    # ------------------------------------------------------------------
    # 4. Integration non-stream: startup consumes budget, phase 2 falls back
    # ------------------------------------------------------------------

    def test_non_stream_stacked_waits_share_budget_phase2_falls_back_immediately(self):
        """
        Same clock mock strategy as test 3 but for non-stream.

        Phase 1 non-stream startup wait: sees T=100, init ready → returns True.
        Phase 2 model wait: sees T=380 >= deadline 370 → returns (False, status).
        Fallback 503 is emitted without a second full wait window.
        """
        entry = self._make_catalog_entry()
        catalog = ResponsesAdmissionTests._Catalog([entry], selected=entry)
        ready_init = {"state": "ready", "ready": True, "catalog_ready": True}
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }

        mono_values = [100.0, 100.0, 380.0, 380.0, 380.0]

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_MAX_SECONDS", 270.0),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.time.monotonic", side_effect=mono_values),
            patch("proxy.qz_request_router.time.sleep"),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch("proxy.qz_model_status.build_model_status", return_value=loading_status),
            patch("proxy.qz_model_status.identity_matches_loaded", return_value=False),
            patch("proxy.qz_request_router.build_control_plane_status", return_value={"service_status": {}}),
            patch.object(RequestRouter, "_run_responses_locally") as run_local,
        ):
            handler = self._make_handler_for_integration(
                {"model": "known-model", "input": "hi"},
                catalog,
                initializations=[ready_init],
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(len(handler.sent), 1)
        status, payload = handler.sent[0]
        self.assertEqual(status, 503)
        # Transitional loading → has Retry-After
        self.assertEqual(handler.response_headers.get("Retry-After"), "5")
        self.assertIs(payload["retry_suggested"], True)
        self.assertEqual(handler.wfile.getvalue(), b"")
        run_local.assert_not_called()

    # ------------------------------------------------------------------
    # 5. Positive composition: adequate budget, startup + model both succeed
    # ------------------------------------------------------------------

    def test_stacked_waits_positive_composition_stream_both_phases_succeed(self):
        """Adequate shared budget → startup waits, then model becomes ready, stream runs."""
        entry = self._make_catalog_entry()
        catalog = ResponsesAdmissionTests._Catalog([entry], selected=entry)
        initializing = {"state": "initializing", "ready": False, "catalog_ready": False}
        ready_init = {"state": "ready", "ready": True, "catalog_ready": True}
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        ready_status = {
            "selected_model_ready": True,
            "backend_loaded_model": "known-model",
            "request_admission_state": "ready",
        }

        with (
            patch.dict(os.environ, {"QZ_HOLDOPEN_LOADING": "1"}, clear=False),
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_dual_capture"),
            patch("proxy.qz_request_router.write_capture"),
            patch("proxy.qz_request_router.write_request_capture"),
            patch("proxy.qz_request_router.incoming_headers_payload", return_value={}),
            patch(
                "proxy.qz_model_status.build_model_status",
                side_effect=[loading_status, ready_status],
            ),
            patch.object(
                RequestRouter, "_run_responses_streaming_locally", return_value={"usage": {}}
            ) as run_stream,
        ):
            handler = self._make_handler_for_integration(
                {"model": "known-model", "input": "hi", "stream": True},
                catalog,
                initializations=[initializing, ready_init],
            )
            RequestRouter(handler).proxy_json_api("/v1/responses")

        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.send_response_calls, [200])
        self.assertEqual(handler.end_headers_calls, 1)
        self.assertEqual(handler.sent, [])
        run_stream.assert_called_once()

    # ------------------------------------------------------------------
    # 6. Backward compat: helpers without a passed deadline still work
    # ------------------------------------------------------------------

    def test_stream_hold_open_without_deadline_param_still_works(self):
        """Calling helper without deadline kwarg computes its own (backward compat)."""
        router, wfile = self._make_sse_router()
        entry = self._make_catalog_entry()
        loading_status = {
            "selected_model_ready": False,
            "backend_loaded_model": "",
            "request_admission_state": "loading",
        }
        ready_status = {
            "selected_model_ready": True,
            "backend_loaded_model": "known-model",
            "request_admission_state": "ready",
        }
        statuses = iter([loading_status, ready_status])

        def _build_status(_h):
            return next(statuses)

        def _identity(_key, _backend, _loaded):
            return _loaded == "known-model"

        with (
            patch("proxy.qz_request_router.HOLDOPEN_LOADING_POLL_SECONDS", 0.001),
            patch("proxy.qz_request_router.write_request_capture"),
        ):
            result = router._hold_open_responses_stream_until_ready(
                selected_model=entry,
                requested_model="known-model",
                request_id="req_compat",
                initial_model_status=loading_status,
                build_model_status=_build_status,
                identity_matches_loaded=_identity,
                reset_capture=True,
                # No deadline kwarg — must compute its own
            )

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
