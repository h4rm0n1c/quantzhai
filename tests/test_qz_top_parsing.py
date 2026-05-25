import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch

# scripts/qz-top is a bash script with embedded python.
# We extract the python code to test it.

def get_qz_top_python():
    with open("scripts/qz-top", "r") as f:
        lines = f.readlines()
    
    py_lines = []
    in_py = False
    for line in lines:
        if line.strip() == "python3 - \"$@\" <<'PY'":
            in_py = True
            continue
        if in_py and line.strip() == "PY":
            break
        if in_py:
            py_lines.append(line)
    return "".join(py_lines)

# Execute the extracted python code in a new module-like namespace
qz_top_ns = {}
exec(get_qz_top_python(), qz_top_ns)

class TestQzTopParsing(unittest.TestCase):
    def test_model_status_from_control_plane_success(self):
        cp = {
            "profile": {
                "backend_reasoning_budget": 500,
                "profile_symlink": False,
                "prompt_files": [],
                "reasoning_level": "high",
                "reasoning_policy": "prompt",
                "sampling": {},
                "selected_context_length": 32768,
                "backend_context_length": 32768
            },
            "models": {
                "selected": "test-model.gguf",
                "selected_backend_id": "test-model",
                "selected_model_ready": True
            },
            "backend": {
                "health_status": 200,
                "loaded_model": "test-model"
            },
            "readiness": {
                "backend_ready": True
            },
            "service_status": {
                "operator_hints": []
            }
        }
        
        # We need to make sure the namespace has all required classes/functions
        # ModelStatus is a dataclass in qz-top
        model_status_from_control_plane = qz_top_ns["model_status_from_control_plane"]
        
        status = model_status_from_control_plane(cp)
        self.assertEqual(status.backend_reasoning_budget, "500")
        self.assertTrue(status.ready)

    def test_model_status_from_control_plane_fallback(self):
        # Missing backend_reasoning_budget in profile
        cp = {
            "profile": {
                # backend_reasoning_budget missing
            },
            "models": {
                "selected": "test-model.gguf"
            },
            "backend": {},
            "readiness": {},
            "service_status": {}
        }
        
        model_status_from_control_plane = qz_top_ns["model_status_from_control_plane"]
        
        with patch.dict(os.environ, {"QZ_REASONING_BUDGET": "1000"}):
            status = model_status_from_control_plane(cp)
            self.assertEqual(status.backend_reasoning_budget, "1000")

if __name__ == "__main__":
    unittest.main()
