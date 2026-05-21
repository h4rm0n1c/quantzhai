import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from proxy.qz_model_state import ModelState, write_model_state
from proxy.qz_request_router import RequestRouter

class FakeBackend:
    def __init__(self):
        self.last_body = None

    def request(self, path, method, body, headers, timeout):
        self.last_body = json.loads(body.decode("utf-8"))
        return MagicMock(status=200, content_type="application/json", data=b"{}")

class FakeModelRouter:
    def status_summary(self, path):
        return {}
    def apply_reasoning_policy(self, body, model):
        return body
    def inject_runtime_state(self, body, model):
        return body

class FakeModelCatalog:
    def __init__(self):
        self.entries = [{
            "key": "test-model",
            "slug": "test-model",
            "filename": "test-model.gguf",
            "backend_id": "test-model",
            "profile_valid": True,
        }]
        self.selected = self.entries[0]

    def resolve(self, query=None):
        return self.selected, "test"

class FakeBackendManager:
    def snapshot(self):
        return {
            "phase": "healthy",
            "backend_health_ok": True,
            "launch_model_key": "test-model",
            "launch_model_backend_id": "test-model",
            "launch_model_path_basename": "test-model.gguf",
            "launch_model_error": None,
        }

class FakeHandler:
    def __init__(self):
        self.path = "/v1/responses"
        self.headers = {"Content-Length": "2", "Accept": "application/json"}
        self.rfile = MagicMock()
        self.rfile.read.return_value = b"{}"
        self.telemetry = MagicMock()
        self.upstream = "http://upstream"
        self.reasoning_stream_format = "raw"
        self._backend_mock = FakeBackend()
        self._model_router_mock = FakeModelRouter()
        self._model_catalog_mock = FakeModelCatalog()
        self.backend_manager = FakeBackendManager()
        self._tmp = tempfile.TemporaryDirectory()
        self.model_state_path = str(Path(self._tmp.name) / "model-state.json")
        write_model_state(
            ModelState(selected_key="test-model", selected_backend_id="test-model", last_load_result="loaded"),
            self.model_state_path,
        )
        self.wfile = MagicMock()
        self.searxng_policy = {}
        self.searxng_policy_path = ""
        self.searxng_base_url = ""
        self.searxng_timeout = 10
        self.searxng_capabilities = {}
        self.web_search_cache = MagicMock()
        self.opened_page_cache = MagicMock()
        self.root = "/tmp"

    def _backend(self):
        return self._backend_mock

    def _model_router(self):
        return self._model_router_mock

    def _model_catalog(self):
        return self._model_catalog_mock

    def _initialization_payload(self):
        return {"state": "ready", "ready": True, "catalog_ready": True}

    def _send_json(self, status, body):
        pass

    def _request_id(self, ts):
        return "req-123"

    def _send_codex_rate_limit_headers(self):
        pass

    def send_response(self, code):
        pass
    
    def send_header(self, keyword, value):
        pass

    def end_headers(self):
        pass

class RequestMutationRegressionTests(unittest.TestCase):
    @patch("proxy.qz_request_router.incoming_headers_payload")
    @patch("proxy.qz_request_router.write_dual_capture")
    @patch("proxy.qz_request_router.write_capture")
    def test_proxy_json_api_does_not_inject_qz_metadata(self, mock_write_capture, mock_write_dual, mock_headers):
        mock_headers.return_value = {"session_id": "client-sid"}
        handler = FakeHandler()
        # Mocking _resolve_model_selection on handler with memory_domain override
        handler._resolve_model_selection = MagicMock(return_value=(
            {"slug": "test-model", "overrides": {"memory_domain": "coding"}}, 
            "ok"
        ))
        
        router = RequestRouter(handler)
        
        # We need to mock RequestRouter's own methods that might be called
        router._runtime_metrics = MagicMock(return_value={})
        router._promote_prompt_contract_runtime_truth = MagicMock(side_effect=lambda m, c: m)
        router._prompt_contract = MagicMock(return_value={})
        router._emit_prompt_contract = MagicMock()
        router._capture_contract = MagicMock(return_value={})
        router._effective_reasoning_stream_format = MagicMock(return_value="raw")
        router._request_id = MagicMock(return_value="req-123")
        
        # Mock _call_upstream_json
        def fake_call_upstream(url, body):
            router.last_forwarded_body = body
            return 200, "application/json", b"{}"
        router._call_upstream_json = MagicMock(side_effect=fake_call_upstream)
        
        router.proxy_json_api("/v1/responses")
        
        last_body = getattr(router, "last_forwarded_body", None)
        self.assertIsNotNone(last_body, "Upstream was never called")
        metadata = last_body.get("metadata", {})
        
        # Contract: internal qz metadata must not leak upstream
        self.assertNotIn("qz_session_id", metadata)
        self.assertNotIn("qz_workspace_id", metadata)
        self.assertNotIn("qz_memory_domain", metadata)
        self.assertNotIn("qz_text_verbosity", metadata)
        
        # Verify intentional qz metadata is still present
        self.assertIn("qz_request_id", metadata)
        self.assertEqual(metadata["qz_request_id"], "req-123")

if __name__ == "__main__":
    unittest.main()
