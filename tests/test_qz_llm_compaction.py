import unittest
import json
import base64
import os
from unittest.mock import patch, MagicMock
from proxy.qz_responses import (
    _build_local_compaction_response,
    _decode_local_compaction_blob,
    _expand_local_compaction_items,
    COMPACTION_CONFIG
)

class TestLLMCompaction(unittest.TestCase):

    def setUp(self):
        self.original_config = COMPACTION_CONFIG.copy()
        # Set up a base config for testing
        COMPACTION_CONFIG["mode"] = "llm"
        COMPACTION_CONFIG["llm_base_url"] = "http://mock-llm:8080"
        COMPACTION_CONFIG["llm_model"] = "test-model"

    def tearDown(self):
        # Restore original config
        for k, v in self.original_config.items():
            COMPACTION_CONFIG[k] = v

    def _make_body(self, items):
        return {
            "input": items,
            "model": "qwen3.6",
            "context_management": {"compact_threshold": 10} # Trigger compaction
        }

    def test_default_is_heuristic_v2(self):
        # Reset mode to default
        COMPACTION_CONFIG["mode"] = "heuristic"
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        
        result = _build_local_compaction_response(self._make_body(items))
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))

    @patch("urllib.request.urlopen")
    def test_llm_compaction_success_produces_v3(self, mock_urlopen):
        # Mock successful LLM response
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "## Goal\nTest\n## Current Status\nDone\n## Key Decisions\nNone\n## Technical State\nOk\n## Next Actions\nNone"}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items))
        
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v3:"))
        
        payload = _decode_local_compaction_blob(blob)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["engine"], "anchored-llm")
        self.assertIn("## Goal", payload["summary_text"])

    @patch("urllib.request.urlopen")
    def test_llm_compaction_fallback_on_invalid_output(self, mock_urlopen):
        # Mock LLM response with missing headings
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Bad output without headings"}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items))
        
        # Should fallback to v2
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))

    @patch("urllib.request.urlopen")
    def test_llm_compaction_fallback_on_error(self, mock_urlopen):
        # Mock LLM error
        mock_urlopen.side_effect = Exception("Connection error")

        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items))
        
        # Should fallback to v2
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))

    def test_expand_v3_blob(self):
        payload = {
            "version": 3,
            "summary_text": "Anchored Summary Content",
            "metadata": {"engine": "anchored-llm"}
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        blob = "localcmp:v3:" + encoded
        
        items = [{"type": "compaction", "encrypted_content": blob}]
        expanded = _expand_local_compaction_items(items)
        
        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0]["role"], "user")
        self.assertIn("Anchored Summary Content", expanded[0]["content"][0]["text"])

    def test_v1_v2_still_expand_correctly(self):
        # Test v2
        v2_payload = {"version": 2, "summary_text": "V2 Summary"}
        v2_blob = "localcmp:v2:" + base64.urlsafe_b64encode(json.dumps(v2_payload).encode()).decode().rstrip("=")
        
        # Test v1
        v1_payload = {"summary_text": "V1 Summary"}
        v1_blob = "localcmp:v1:" + base64.urlsafe_b64encode(json.dumps(v1_payload).encode()).decode().rstrip("=")
        
        items = [
            {"type": "compaction", "encrypted_content": v1_blob},
            {"type": "compaction", "encrypted_content": v2_blob}
        ]
        expanded = _expand_local_compaction_items(items)
        
        self.assertEqual(len(expanded), 2)
        self.assertIn("V1 Summary", expanded[0]["content"][0]["text"])
        self.assertIn("V2 Summary", expanded[1]["content"][0]["text"])

if __name__ == "__main__":
    unittest.main()
