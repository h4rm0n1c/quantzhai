import unittest
import os
import json
import subprocess
from proxy.qz_compaction_eval import load_fixtures, run_eval, calculate_metrics, FixtureSpec, CANONICAL_HEADINGS

class TestCompactionEval(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = "docs/fixtures/compaction"

    def test_fixtures_exist_and_load(self):
        specs = load_fixtures(self.fixtures_dir)
        self.assertGreaterEqual(len(specs), 3)
        f1 = next(s for s in specs if "fixture-01" in s.name)
        self.assertIn("proxy/qz_request_router.py", f1.input_text)
        self.assertIn("proxy/qz_request_router.py", f1.expected_atoms)

    def test_fixture_expectations_populated(self):
        specs = load_fixtures(self.fixtures_dir)
        f1 = next(s for s in specs if "fixture-01" in s.name)
        f2 = next(s for s in specs if "fixture-02" in s.name)
        f3 = next(s for s in specs if "fixture-03" in s.name)
        
        self.assertGreater(len(f1.expected_paths), 0)
        self.assertGreater(len(f1.expected_commands), 0)
        self.assertGreater(len(f2.expected_errors), 0)
        self.assertGreater(len(f3.expected_negations), 0)
        self.assertGreater(len(f3.expected_deferred), 0)

    def test_eval_result_has_all_keys(self):
        specs = load_fixtures(self.fixtures_dir)
        results = run_eval([specs[0]])
        metrics = results[0].metrics
        required_keys = [
            "exact_atom_retention", "exact_path_retention", "exact_command_retention",
            "error_retention", "negation_retention", "evidence_decision_chain",
            "stale_fact_correction", "hallucinated_fact_rate", "token_budget_ratio",
            "downstream_recovery"
        ]
        for key in required_keys:
            self.assertIn(key, metrics)

    def test_exact_command_retention(self):
        spec = FixtureSpec(name="test", input_text="", expected_commands={"git status", "--short"})
        self.assertEqual(calculate_metrics("git status", spec)["exact_command_retention"], 0.5)
        self.assertEqual(calculate_metrics("git status --short", spec)["exact_command_retention"], 1.0)

    def test_error_retention(self):
        spec = FixtureSpec(name="test", input_text="", expected_errors={"exit_code=1", "ImportError"})
        self.assertEqual(calculate_metrics("exit_code=1", spec)["error_retention"], 0.5)

    def test_evidence_decision_chain_credit(self):
        spec = FixtureSpec(
            name="test", input_text="",
            expected_evidence={"line 123"},
            expected_decisions={"decided to fix"},
            expected_deferred={"deferred part 2"}
        )
        # partial
        self.assertAlmostEqual(calculate_metrics("line 123", spec)["evidence_decision_chain"], 0.34)
        # more
        self.assertAlmostEqual(calculate_metrics("line 123 decided to fix", spec)["evidence_decision_chain"], 0.34 + 0.33)
        # full
        self.assertAlmostEqual(calculate_metrics("line 123 decided to fix deferred part 2", spec)["evidence_decision_chain"], 1.0)

    def test_stale_fact_correction_neutral(self):
        spec = FixtureSpec(name="test", input_text="")
        self.assertEqual(calculate_metrics("any", spec)["stale_fact_correction"], 1.0)

    def test_stale_fact_correction_active(self):
        spec = FixtureSpec(
            name="test", input_text="",
            stale_old_absent={"old fact"},
            stale_new_present={"new fact"}
        )
        # both fail
        self.assertEqual(calculate_metrics("old fact", spec)["stale_fact_correction"], 0.0)
        # one pass
        self.assertEqual(calculate_metrics("nothing", spec)["stale_fact_correction"], 0.5)
        # both pass
        self.assertEqual(calculate_metrics("new fact", spec)["stale_fact_correction"], 1.0)

    def test_downstream_recovery_calculation(self):
        spec = FixtureSpec(name="test", input_text="", expected_paths={"a.py"})
        # 1.0 for path_ret, 1.0 for others (since empty), so 1.0 overall
        self.assertEqual(calculate_metrics("a.py", spec)["downstream_recovery"], 1.0)
        # 0.0 for path_ret, others 1.0, so 0.8
        self.assertEqual(calculate_metrics("empty", spec)["downstream_recovery"], 0.8)

    def test_hallucinated_fact_rate_detection(self):
        spec = FixtureSpec(name="test", input_text="existing_path/file.py")
        # Invent a new path
        output = "existing_path/file.py and invented_path/file.py"
        # qz_survival_weight should detect both as heavy
        metrics = calculate_metrics(output, spec)
        self.assertGreater(metrics["hallucinated_fact_rate"], 0.0)

    def test_anchored_template_uses_canonical_headings(self):
        output = run_eval([load_fixtures(self.fixtures_dir)[0]])[2].output_text # anchored_template
        for heading in ["## Goal", "## Technical State", "## Next Actions"]:
            self.assertIn(heading, output)

    def test_survival_weighted_uses_canonical_headings(self):
        output = run_eval([load_fixtures(self.fixtures_dir)[0]])[3].output_text # survival_weighted
        for heading in ["## Goal", "## Key Decisions", "## Technical State", "## Evidence Boundaries", "## Next Actions"]:
            self.assertIn(heading, output)

    def test_survival_weighted_beats_freeform_on_f1_paths_cmds(self):
        specs = load_fixtures(self.fixtures_dir)
        f1 = next(s for s in specs if "fixture-01" in s.name)
        results = run_eval([f1])
        sw = next(r for r in results if r.strategy == "survival_weighted")
        ff = next(r for r in results if r.strategy == "freeform_baseline")
        
        self.assertGreater(sw.metrics["exact_path_retention"], ff.metrics["exact_path_retention"])
        self.assertGreater(sw.metrics["exact_command_retention"], ff.metrics["exact_command_retention"])

    def test_survival_weighted_preserves_f2_evidence(self):
        specs = load_fixtures(self.fixtures_dir)
        f2 = next(s for s in specs if "fixture-02" in s.name)
        results = run_eval([f2])
        sw = next(r for r in results if r.strategy == "survival_weighted")
        self.assertGreater(sw.metrics["evidence_decision_chain"], 0.0)

    def test_survival_weighted_preserves_f3_atoms(self):
        specs = load_fixtures(self.fixtures_dir)
        f3 = next(s for s in specs if "fixture-03" in s.name)
        results = run_eval([f3])
        sw = next(r for r in results if r.strategy == "survival_weighted")
        self.assertGreater(sw.metrics["negation_retention"], 0.0)

    def test_cli_json_output(self):
        cmd = ["scripts/qz-compaction-eval", "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertIn("metrics", data[0])
        self.assertIn("downstream_recovery", data[0]["metrics"])

    def test_cli_text_output(self):
        cmd = ["scripts/qz-compaction-eval"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("QuantZhai Compaction Strategy Evaluation", result.stdout)
        self.assertIn("Aggregate Averages per Strategy", result.stdout)

    def test_empty_input_output_safety(self):
        spec = FixtureSpec(name="empty", input_text="")
        metrics = calculate_metrics("", spec)
        self.assertEqual(metrics["exact_atom_retention"], 1.0)
        self.assertEqual(metrics["hallucinated_fact_rate"], 0.0)

if __name__ == "__main__":
    unittest.main()
