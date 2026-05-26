import unittest
from proxy.qz_survival_weight import score_text, score_items, format_survival_hints, SurvivalSpan

class TestSurvivalWeight(unittest.TestCase):

    def test_score_text_empty(self):
        self.assertEqual(score_text(""), [])
        self.assertEqual(score_text("   "), [])
        self.assertEqual(score_text(None), [])

    def test_detects_file_paths(self):
        text = "Check proxy/qz_responses.py and /tmp/linuxstreamtools and ./scripts/qz-codex"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("proxy/qz_responses.py", texts)
        self.assertIn("/tmp/linuxstreamtools", texts)
        self.assertIn("./scripts/qz-codex", texts)
        
        for s in spans:
            if s.text in ("proxy/qz_responses.py", "/tmp/linuxstreamtools", "./scripts/qz-codex"):
                self.assertEqual(s.weight, "heavy")
                self.assertEqual(s.exactness_risk, "high")
                self.assertEqual(s.features, ("path",))

    def test_detects_commands_and_flags(self):
        text = "Run git status --short and python3 -m pytest tests/test_qz_compaction.py"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("git status", texts)
        self.assertIn("python3 -m pytest", texts) # Matches PATTERNS["command"]
        self.assertIn("--short", texts)
        
        cmd_spans = [s for s in spans if s.features == ("command",)]
        for s in cmd_spans:
            self.assertEqual(s.weight, "heavy")
            self.assertEqual(s.exactness_risk, "high")

    def test_detects_env_vars(self):
        text = "DOCKER_BUILDKIT=1 and $QZ_ROOT"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("DOCKER_BUILDKIT=1", texts)
        self.assertIn("$QZ_ROOT", texts)
        for s in spans:
            if s.text in ("DOCKER_BUILDKIT=1", "$QZ_ROOT"):
                self.assertEqual(s.weight, "heavy")
                self.assertEqual(s.exactness_risk, "high")

    def test_detects_shas(self):
        text = "Commit 095b12c and 46f30d02828bd4c52827e5f0482a6f2a982cce5b"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("095b12c", texts)
        self.assertIn("46f30d02828bd4c52827e5f0482a6f2a982cce5b", texts)
        for s in spans:
            if s.features == ("sha",):
                self.assertEqual(s.weight, "heavy")
                self.assertEqual(s.exactness_risk, "high")

    def test_detects_issue_refs(self):
        text = "Fixes #8 and see issue #75 and PR #12"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("#8", texts)
        self.assertIn("issue #75", texts)
        self.assertIn("PR #12", texts)

    def test_detects_version_strings(self):
        text = "Version v0.43.0 and localcmp:v2: and Codex 0.130"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("v0.43.0", texts)
        self.assertIn("localcmp:v2:", texts)
        self.assertIn("Codex 0.130", texts)

    def test_detects_error_strings(self):
        text = "Error: permission denied and traceback and exit_code=1"
        spans = score_text(text)
        texts_lower = [s.text.lower() for s in spans]
        self.assertIn("error:", texts_lower)
        self.assertIn("permission denied", texts_lower)
        self.assertIn("traceback", texts_lower)
        self.assertIn("exit_code=1", texts_lower)

    def test_detects_test_names_and_symbols(self):
        text = "Run SurvivalWeightTests or test_detects_env_var in qz_survival_weight.py"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("SurvivalWeightTests", texts)
        self.assertIn("test_detects_env_var", texts)
        self.assertIn("qz_survival_weight.py", texts)

    def test_detects_negations_and_corrections(self):
        text = "Do not change runtime code. User corrected the path. Mistake found."
        spans = score_text(text)
        texts = [s.text.lower() for s in spans]
        self.assertIn("not", texts)
        self.assertIn("user corrected", texts)
        self.assertIn("mistake", texts)
        
        for s in spans:
            if s.text.lower() in ("not", "user corrected", "mistake"):
                self.assertEqual(s.weight, "heavy")
                self.assertEqual(s.exactness_risk, "high")

    def test_detects_decisions(self):
        text = "Therefore we decided to defer. Evidence found."
        spans = score_text(text)
        texts = [s.text.lower() for s in spans]
        self.assertIn("therefore", texts)
        self.assertIn("decided", texts)
        self.assertIn("evidence", texts)

    def test_deduplicates_spans(self):
        text = "proxy/qz_responses.py and proxy/qz_responses.py"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertEqual(texts.count("proxy/qz_responses.py"), 1)

    def test_score_items(self):
        items = [
            {"type": "message", "role": "user", "content": "Check proxy/qz_responses.py"},
            {"type": "message", "role": "assistant", "content": [{"type": "input_text", "text": "I checked it. No errors."}]},
            {"type": "function_call", "name": "rg", "arguments": '{"pattern": "compact_threshold"}'},
            {"type": "function_call_output", "output": "proxy/qz_request_router.py:950: compact_threshold"}
        ]
        spans = score_items(items)
        texts = [s.text for s in spans]
        self.assertIn("proxy/qz_responses.py", texts)
        self.assertTrue(any(s.text.lower() == "no" for s in spans))
        self.assertIn("rg", texts)
        self.assertIn("compact_threshold", texts)
        self.assertIn("proxy/qz_request_router.py", texts)

    def test_format_survival_hints(self):
        spans = [
            SurvivalSpan("proxy/qz_responses.py", "heavy", "high", ("path",)),
            SurvivalSpan("just some text", "medium", "medium", ("code_symbol",))
        ]
        hints = format_survival_hints(spans)
        self.assertIn("Survival hints:", hints)
        self.assertIn("- heavy/high path: proxy/qz_responses.py", hints)
        self.assertIn("- medium/medium code_symbol: just some text", hints)

    def test_format_survival_hints_empty(self):
        self.assertEqual(format_survival_hints([]), "")

    def test_fixture_01_atoms(self):
        fixture_text = """
        [User] Fix the import-mode regression — proxy was dropping input items when
               input_mode was "full_history".
        [Agent] Checking proxy/qz_request_router.py ... found the bug:
                normalize_responses_input_for_qwen returns early when input is a list
                without checking mode. Line 312.
        [Agent] Fix applied: added explicit input_mode guard. Ran:
                  python3 -m py_compile proxy/quantzhai_proxy.py
        """
        spans = score_text(fixture_text)
        texts = [s.text for s in spans]
        self.assertIn("proxy/qz_request_router.py", texts)
        self.assertIn("normalize_responses_input_for_qwen", texts)
        self.assertIn("python3 -m py_compile", texts)
        self.assertIn("proxy/quantzhai_proxy.py", texts)

if __name__ == "__main__":
    unittest.main()
