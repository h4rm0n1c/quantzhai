import json
import os
import subprocess
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_qz_thoughts_namespace():
    script = (ROOT / "scripts/qz-thoughts").read_text(encoding="utf-8")
    marker = "python3 - \"$@\" <<'PY'\n"
    start = script.index(marker) + len(marker)
    end = script.rindex("\nPY")
    namespace = {"__name__": "qz_thoughts_test"}
    exec(compile(script[start:end], str(ROOT / "scripts/qz-thoughts"), "exec"), namespace)
    return namespace


class _JsonHandler(BaseHTTPRequestHandler):
    telemetry_payload = {}
    config_payload = {}

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path.startswith("/qz/telemetry/recent"):
            self._send_json(self.telemetry_payload)
            return
        if self.path == "/qz/config/effective":
            self._send_json(self.config_payload)
            return
        self.send_response(404)
        self.end_headers()

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _JsonServer:
    def __init__(self, telemetry_payload, config_payload):
        handler = type(
            "QzThoughtsJsonHandler",
            (_JsonHandler,),
            {
                "telemetry_payload": telemetry_payload,
                "config_payload": config_payload,
            },
        )
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self):
        return self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class QzThoughtsCliTests(unittest.TestCase):
    def test_once_file_coalesces_delta_activity(self):
        raw = textwrap.dedent(
            """
            event: response.created
            data: {"type":"response.created","response":{"id":"resp_test","status":"in_progress","model":"model-test"}}

            event: response.output_item.added
            data: {"type":"response.output_item.added","item":{"id":"rs_test","type":"reasoning","status":"in_progress"}}

            event: response.reasoning_summary_text.delta
            data: {"type":"response.reasoning_summary_text.delta","delta":"I"}

            event: response.reasoning_summary_text.delta
            data: {"type":"response.reasoning_summary_text.delta","delta":"'ll"}

            event: response.reasoning_summary_text.done
            data: {"type":"response.reasoning_summary_text.done","text":"I'll"}

            event: response.output_text.delta
            data: {"type":"response.output_text.delta","delta":"hello"}

            event: response.content_part.added
            data: {"type":"response.content_part.added","part":{"type":"output_text","text":""}}

            event: response.output_text.delta
            data: {"type":"response.output_text.delta","delta":" world"}

            event: response.content_part.done
            data: {"type":"response.content_part.done","part":{"type":"output_text","text":"hello world"}}

            event: response.output_text.done
            data: {"type":"response.output_text.done","text":"hello world"}

            event: response.output_item.done
            data: {"type":"response.output_item.done","item":{"id":"msg_test","type":"message","status":"completed"}}

            event: response.completed
            data: {"type":"response.completed","response":{"id":"resp_test","status":"completed","model":"model-test"}}

            data: [DONE]

            """
        ).lstrip()
        with tempfile.NamedTemporaryFile("w", suffix=".raw", delete=False) as handle:
            handle.write(raw)
            capture = handle.name

        env = os.environ.copy()
        env["QZ_PROXY_PORT"] = "9"
        env["QZ_PROXY_HOST"] = "127.0.0.1"
        try:
            result = subprocess.run(
                [str(ROOT / "scripts/qz-thoughts"), "--once", "--file", capture],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=True,
            )
        finally:
            os.unlink(capture)

        self.assertIn("THOUGHT\nI'll", result.stdout)
        self.assertIn("ANSWER\nhello world", result.stdout)
        self.assertEqual(result.stdout.count("  thought   done 4 chars"), 1)
        self.assertEqual(result.stdout.count("  answer    done 11 chars"), 1)
        self.assertNotIn("  thought   I", result.stdout)
        self.assertNotIn("  thought   'll", result.stdout)
        self.assertNotIn("response.content_part", result.stdout)

    def test_once_does_not_read_default_stale_capture(self):
        raw = textwrap.dedent(
            """
            event: response.output_text.done
            data: {"type":"response.output_text.done","text":"stale capture answer"}

            """
        ).lstrip()
        with tempfile.TemporaryDirectory() as tmpdir:
            capture_dir = Path(tmpdir) / "captures"
            capture_dir.mkdir()
            (capture_dir / "latest-synthetic-sse.raw").write_text(raw, encoding="utf-8")

            env = os.environ.copy()
            env["QZ_VAR_DIR"] = tmpdir
            env["QZ_PROXY_PORT"] = "9"
            env["QZ_PROXY_HOST"] = "127.0.0.1"
            result = subprocess.run(
                [str(ROOT / "scripts/qz-thoughts"), "--once"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=True,
            )

        self.assertIn("source=proxy-telemetry", result.stdout)
        self.assertIn("status=unavailable", result.stdout)
        self.assertIn("telemetry unavailable", result.stdout)
        self.assertNotIn("stale capture answer", result.stdout)

    def test_once_renders_new_stream_lifecycle_and_capture_mode(self):
        def event(seq, ev_type, payload):
            payload = dict(payload)
            payload.setdefault("request_id", "req-1")
            return {
                "schema": "qz.telemetry.event.v1",
                "seq": seq,
                "type": ev_type,
                "request_id": "req-1",
                "ts": 4102444800 + seq,
                "payload": payload,
            }

        latest_completed_events = [
            event(1, "request_started", {"method": "POST", "path": "/v1/responses"}),
            event(2, "empty_answer_repair_started", {"repair_hop_index": 0, "reasoning_chars": 123, "upstream_output_items": 0}),
            event(3, "stream_event_timing", {"event_type": "response.completed", "suppressed": "empty_answer_repair_started"}),
            event(4, "empty_answer_repair_failed", {"repair_hop_index": 0, "reasoning_chars": 456, "upstream_output_items": 0}),
            event(5, "stream_event_timing", {"event_type": "response.completed", "suppressed": "reasoning_only_completed_without_answer"}),
            event(6, "reasoning_only_completed_without_answer", {"reasoning_chars": 456}),
            event(7, "stream_completed", {"model": "model-test", "output_items": 1, "duration_ms": 99.4, "fallback": True}),
            event(8, "private_tool_call_aborted", {"tool_name": "write_stdin", "reason": "delta_limit"}),
            event(9, "reasoning_only_aborted", {"reason": "timeout", "reasoning_chars": 777}),
            event(
                10,
                "web_search_route",
                {
                    "query": "QuantZhai",
                    "requested_profile": "auto",
                    "selected_profile": "broad",
                    "engines": ["brave", "duckduckgo"],
                    "result_count": 0,
                    "fallback_used": "ai_models",
                    "fallback_attempts": [{"profile": "ai_models", "result_count": 2}],
                },
            ),
            event(11, "tool_call_started", {"tool": "web_search", "public_item_type": "web_search_call", "execution": "proxy_local"}),
            event(12, "tool_call_completed", {"tool": "web_search", "public_item_type": "web_search_call", "execution": "proxy_local", "sources": 2, "upstream_items": 2}),
            event(13, "request_failed", {"method": "POST", "path": "/v1/responses", "phase": "upstream_stream", "error": "upstream boom"}),
        ]
        telemetry_payload = {
            "schema": "qz.telemetry.recent.v1",
            "events": [
                latest_completed_events[1],
                latest_completed_events[11],
            ],
            "state": {
                "schema": "qz.telemetry.state.v1",
                "status": "ok",
                "latest_completed_events": latest_completed_events,
            },
        }
        config_payload = {
            "schema": "qz.effective_config.v1",
            "capture": {
                "mode": "off",
                "enabled": False,
                "state": "disabled",
                "source_layer": "default",
                "default": "off",
                "classification": "debug_capture_policy",
                "note": "captures disabled; existing latest/request-scoped capture files may be stale",
            },
            "settings": [
                {
                    "name": "capture_mode",
                    "active_value": "off",
                    "source_layer": "default",
                    "classification": "debug_capture_policy",
                    "default": "off",
                }
            ],
        }

        with _JsonServer(telemetry_payload, config_payload) as server:
            env = os.environ.copy()
            env["QZ_PROXY_HOST"] = "127.0.0.1"
            env["QZ_PROXY_PORT"] = str(server.port)
            result = subprocess.run(
                [str(ROOT / "scripts/qz-thoughts"), "--once"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=True,
            )

        self.assertIn("capture   mode=off disabled source=default default=off", result.stdout)
        self.assertIn("repair    start idx=0 chars=123 upstream_items=0", result.stdout)
        self.assertIn("repair    failed idx=0 chars=456 upstream_items=0 -> fallback", result.stdout)
        self.assertIn("suppress  empty answer repair suppressed response.completed", result.stdout)
        self.assertIn("suppress  reasoning-only fallback suppressed response.completed", result.stdout)
        self.assertIn("fallback  reasoning-only completed without answer chars=456 -> fallback", result.stdout)
        self.assertIn("stream    completed fallback=true output_items=1 99ms", result.stdout)
        self.assertIn("tool      private abort write_stdin reason=delta_limit -> fallback", result.stdout)
        self.assertIn("fallback  reasoning-only abort reason=timeout chars=777 -> fallback", result.stdout)
        self.assertIn("web       QuantZhai profile=auto->broad results=0 fallback=ai_models retries=ai_models:2 engines=brave,duckduckgo", result.stdout)
        self.assertIn("tool      start web_search type=web_search_call exec=proxy_local", result.stdout)
        self.assertEqual(result.stdout.count("tool      done web_search type=web_search_call exec=proxy_local sources=2 upstream_items=2"), 1)
        self.assertIn("error     POST /v1/responses phase=upstream_stream upstream boom", result.stdout)

    def test_reconnect_resets_stale_telemetry_sequence_floor(self):
        ns = _load_qz_thoughts_namespace()
        state = ns["ThoughtState"](path=Path("proxy-telemetry"))
        state.status = "unavailable"
        state.parse_error = "proxy unavailable"
        state.last_seq = 500

        feed = object.__new__(ns["TelemetryFeed"])
        feed.state = state
        feed._apply_event({
            "type": "monitor_connection",
            "payload": {"status": "reconnected", "url": "http://127.0.0.1/qz/telemetry/stream"},
        })
        feed._apply_event({
            "seq": 1,
            "type": "request_started",
            "ts": 4102444800,
            "payload": {"method": "POST", "path": "/v1/responses", "request_id": "req-new"},
        })

        self.assertEqual(state.last_seq, 1)
        self.assertIn(("proxy", "reconnected"), state.backend)
        self.assertIn(("request", "POST /v1/responses"), state.activity)


if __name__ == "__main__":
    unittest.main()
