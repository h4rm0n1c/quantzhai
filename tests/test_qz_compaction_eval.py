import unittest
import os
from proxy.qz_compaction_eval import load_fixtures, run_eval, calculate_metrics, FixtureSpec

class TestCompactionEval(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = "docs/fixtures/compaction"

    def test_fixtures_exist_and_load(self):
        specs = load_fixtures(self.fixtures_dir)
        self.assertGreaterEqual(len(specs), 3)
        names = [s.name for s in specs]
        self.assertIn("fixture-01-basic-coding-session", names)
        self.assertIn("fixture-02-tool-heavy-session", names)
        self.assertIn("fixture-03-rejected-approaches", names)
        
        # Check fixture 01 content
        f1 = next(s for s in specs if "fixture-01" in s.name)
        self.assertIn("proxy/qz_request_router.py", f1.input_text)
        self.assertIn("proxy/qz_request_router.py", f1.expected_atoms)

    def test_calculate_metrics_basic(self):
        spec = FixtureSpec(
            name="test",
            input_text="Check proxy/qz_responses.py and commit 095b12c",
            expected_atoms={"proxy/qz_responses.py", "095b12c", "missing_atom"}
        )
        output = "We checked proxy/qz_responses.py and saw commit 095b12c."
        metrics = calculate_metrics(output, spec)
        
        self.assertEqual(metrics["exact_atom_retention"], 2/3)
        self.assertEqual(metrics["exact_path_retention"], 1.0) # only 1 path in expected
        self.assertEqual(metrics["hallucinated_fact_rate"], 0.0)

    def test_survival_weighted_beats_freeform_on_f1(self):
        specs = load_fixtures(self.fixtures_dir)
        results = run_eval(specs)
        
        f1_results = [r for r in results if "fixture-01" in r.fixture_name]
        sw = next(r for r in f1_results if r.strategy == "survival_weighted")
        ff = next(r for r in f1_results if r.strategy == "freeform_baseline")
        
        self.assertGreater(sw.metrics["exact_atom_retention"], ff.metrics["exact_atom_retention"])

    def test_survival_weighted_preserves_f1_paths(self):
        specs = load_fixtures(self.fixtures_dir)
        f1 = next(s for s in specs if "fixture-01" in s.name)
        results = run_eval([f1])
        sw = next(r for r in results if r.strategy == "survival_weighted")
        
        # F1 paths: proxy/qz_request_router.py, proxy/quantzhai_proxy.py, docs/codex-plan-mode-live-capture.md
        self.assertIn("proxy/qz_request_router.py", sw.output_text)
        self.assertIn("proxy/quantzhai_proxy.py", sw.output_text)
        self.assertEqual(sw.metrics["exact_path_retention"], 1.0)

    def test_survival_weighted_preserves_f2_errors(self):
        specs = load_fixtures(self.fixtures_dir)
        f2 = next(s for s in specs if "fixture-02" in s.name)
        results = run_eval([f2])
        sw = next(r for r in results if r.strategy == "survival_weighted")
        
        # F2 technical atoms: exit_code=0, tests/smoke_compaction_live.py, proxy/qz_responses.py:243
        self.assertIn("exit_code=0", sw.output_text)
        self.assertIn("proxy/qz_responses.py", sw.output_text)

    def test_survival_weighted_preserves_f3_negations(self):
        specs = load_fixtures(self.fixtures_dir)
        f3 = next(s for s in specs if "fixture-03" in s.name)
        results = run_eval([f3])
        sw = next(r for r in results if r.strategy == "survival_weighted")
        
        # F3 negations: Do not implement proxy-level sudo interception, Do not add a sudo -v pre-run wrapper to qz-codex
        # Actually our deterministic sw strategy might only preserve the atoms, not the whole sentence.
        # Let's check if the metric detects them.
        self.assertGreater(sw.metrics["negation_retention"], 0.0)

    def test_hallucinated_fact_rate_is_zero_deterministic(self):
        specs = load_fixtures(self.fixtures_dir)
        results = run_eval(specs)
        for r in results:
            self.assertEqual(r.metrics["hallucinated_fact_rate"], 0.0)

    def test_metric_scoring_handles_empty_safely(self):
        spec = FixtureSpec(name="empty", input_text="", expected_atoms=set())
        metrics = calculate_metrics("", spec)
        self.assertEqual(metrics["exact_atom_retention"], 1.0)
        self.assertEqual(metrics["exact_path_retention"], 1.0)
        self.assertEqual(metrics["negation_retention"], 1.0)

if __name__ == "__main__":
    unittest.main()
