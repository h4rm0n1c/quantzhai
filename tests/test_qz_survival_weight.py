import unittest
import os
from proxy.qz_survival_weight import score_text, score_items, format_survival_hints, SurvivalSpan

class TestSurvivalWeight(unittest.TestCase):

    def test_score_text_empty(self):
        self.assertEqual(score_text(""), [])
        self.assertEqual(score_text("   "), [])
        self.assertEqual(score_text(None), [])

    def test_detects_file_paths(self):
        text = "Check proxy/qz_responses.py and /tmp/linuxstreamtools.sh and ./scripts/qz-codex.py"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("proxy/qz_responses.py", texts)
        self.assertIn("/tmp/linuxstreamtools.sh", texts)
        self.assertIn("./scripts/qz-codex.py", texts)
        
        for s in spans:
            if s.text in ("proxy/qz_responses.py", "/tmp/linuxstreamtools.sh", "./scripts/qz-codex.py"):
                self.assertEqual(s.weight, "heavy")
                self.assertEqual(s.exactness_risk, "high")
                self.assertEqual(s.features, ("path",))

    def test_detects_commands_git(self):
        text = "Run git status --short and git push origin main"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("git status --short", texts)
        self.assertIn("git push origin main", texts)

    def test_detects_commands_python_rg_curl(self):
        text = 'python3 -m pytest tests/test_qz_survival_weight.py and rg "compact_prompt_file" -n docs config and curl http://127.0.0.1:8080/v1/models'
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("python3 -m pytest tests/test_qz_survival_weight.py", texts)
        # rg and curl are a bit tricky if they have quotes, but let's check basic match
        self.assertTrue(any("rg" in t for t in texts))
        self.assertTrue(any("curl" in t for t in texts))

    def test_detects_commands_bash_sudo(self):
        text = "bash -n scripts/qz-env.sh and sudo -v"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("bash -n scripts/qz-env.sh", texts)
        self.assertIn("sudo -v", texts)

    def test_detects_flags(self):
        text = "Flags -n and --ff-only and --compact_prompt_file"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("-n", texts)
        self.assertIn("--ff-only", texts)
        self.assertIn("--compact_prompt_file", texts)

    def test_detects_env_vars(self):
        text = "DOCKER_BUILDKIT=1 and $QZ_ROOT_PATH and $V"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("DOCKER_BUILDKIT=1", texts)
        self.assertIn("$QZ_ROOT_PATH", texts)
        self.assertNotIn("$V", texts) # Too short for hardening regex (requires 3 chars after $)

    def test_detects_shas(self):
        text = "Commit 095b12c and 46f30d02828bd4c52827e5f0482a6f2a982cce5b"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("095b12c", texts)
        self.assertIn("46f30d02828bd4c52827e5f0482a6f2a982cce5b", texts)

    def test_detects_issue_refs(self):
        text = "Fixes #8 and see issue #75 and PR #12"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("#8", texts)
        self.assertIn("issue #75", texts)
        self.assertIn("PR #12", texts)

    def test_detects_version_and_models(self):
        text = "Codex v0.130.0 and localcmp:v2: and Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS and gemini-3-flash-preview and GPT-5.5 High"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("Codex v0.130.0", texts)
        self.assertIn("localcmp:v2:", texts)
        self.assertIn("Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS", texts)
        self.assertIn("gemini-3-flash-preview", texts)
        self.assertIn("GPT-5.5", texts)

    def test_detects_error_strings(self):
        text = """
        ImportError: attempted relative import with no known parent package
        local streaming runtime error
        response.custom_tool_call_input.done
        Traceback (most recent call last):
        Permission denied
        exit_code=1
        """
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("ImportError", texts)
        self.assertIn("attempted relative import with no known parent package", texts)
        self.assertIn("local streaming runtime error", texts)
        self.assertIn("response.custom_tool_call_input.done", texts)
        self.assertIn("Traceback", texts)
        self.assertIn("Permission denied", texts)
        self.assertIn("exit_code=1", texts)

    def test_detects_test_names_and_code_symbols(self):
        text = "Run SurvivalWeightTests or test_detects_env_var or score_items() or proxy.qz_survival_weight"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("SurvivalWeightTests", texts)
        self.assertIn("test_detects_env_var", texts)
        self.assertIn("score_items()", texts)
        self.assertIn("proxy.qz_survival_weight", texts)

    def test_code_symbol_noise_filtering(self):
        # Ordinary prose words should not be marked as code symbols
        text = "working general summary because because although decided therefore"
        spans = score_text(text)
        texts = [s.text for s in spans]
        # These are decision boundaries or ordinary prose
        for s in spans:
            if s.features == ("code_symbol",):
                self.assertNotIn(s.text.lower(), ["working", "general", "summary", "because", "although"])

    def test_detects_negations_and_corrections(self):
        text = "Do not change runtime code. User corrected the path. User rejected the approach."
        spans = score_text(text)
        texts = [s.text.lower() for s in spans]
        self.assertIn("not", texts)
        self.assertIn("user corrected", texts)
        self.assertIn("user rejected", texts)

    def test_detects_decisions_and_evidence(self):
        text = "source-backed findings therefore evidence-to-decision concluded decided deferred blocked because inferred"
        spans = score_text(text)
        texts = [s.text.lower() for s in spans]
        self.assertIn("source-backed", texts)
        self.assertIn("therefore", texts)
        self.assertIn("evidence-to-decision", texts)
        self.assertIn("concluded", texts)
        self.assertIn("decided", texts)
        self.assertIn("deferred", texts)
        self.assertIn("blocked because", texts)
        self.assertIn("inferred", texts)

    def test_deduplicates_spans(self):
        text = "proxy/qz_responses.py and proxy/qz_responses.py"
        spans = score_text(text)
        self.assertEqual(len(spans), 1)

    def test_overlapping_matches_prioritize_specific(self):
        text = "Check tests/test_qz_survival_weight.py"
        spans = score_text(text)
        # Should prefer 'path' over 'test_name' or 'code_symbol'
        self.assertEqual(spans[0].features, ("path",))
        self.assertEqual(spans[0].text, "tests/test_qz_survival_weight.py")

    def test_score_items_safety(self):
        # Malformed items should not crash
        self.assertEqual(score_items(None), [])
        self.assertEqual(score_items("not a list"), [])
        self.assertEqual(score_items([None, 123, "just a string"]), [])
        
        items = [
            {"type": "message", "content": [{"type": "text", "text": "path/to/file.py"}]},
            {"type": "message", "content": ["direct string part"]},
            {"type": "unknown", "text": "fallback_code_symbol()"},
            {"type": "function_call", "arguments": {"non": "string"}} 
        ]
        spans = score_items(items)
        texts = [s.text for s in spans]
        self.assertIn("path/to/file.py", texts)
        self.assertIn("fallback_code_symbol()", texts)

    def test_score_items_message_list(self):
        items = [{"type": "message", "content": [{"type": "input_text", "text": "abc/def.py"}, {"type": "output_text", "text": "fixed_bug()"}]}]
        spans = score_items(items)
        texts = [s.text for s in spans]
        self.assertIn("abc/def.py", texts)
        self.assertIn("fixed_bug()", texts)

    def test_score_items_tool_calls(self):
        items = [
            {"type": "function_call", "name": "rg_search()", "arguments": "pattern_with_underscore"},
            {"type": "function_call_output", "output": "found_it()"},
            {"type": "custom_tool_call", "name": "web_search()", "input": "search_query_with_underscore"}
        ]
        spans = score_items(items)
        texts = [s.text for s in spans]
        self.assertIn("rg_search()", texts)
        self.assertIn("pattern_with_underscore", texts)
        self.assertIn("found_it()", texts)
        self.assertIn("web_search()", texts)
        self.assertIn("search_query_with_underscore", texts)

    def test_format_survival_hints_determinism(self):
        spans = [
            SurvivalSpan("b.py", "heavy", "high", ("path",)),
            SurvivalSpan("a.py", "heavy", "high", ("path",)),
            SurvivalSpan("c_d_e_f", "medium", "medium", ("code_symbol",))
        ]
        hints = format_survival_hints(spans)
        # Should be sorted: heavy/high first, then a.py before b.py (tie break)
        self.assertIn("- heavy/high path: a.py\n- heavy/high path: b.py\n- medium/medium code_symbol: c_d_e_f", hints)

    def test_format_survival_hints_bounds(self):
        spans = [SurvivalSpan(f"span{i:03d}", "heavy", "high", ("path",)) for i in range(100)]
        hints = format_survival_hints(spans, max_spans=5)
        self.assertEqual(hints.count("\n"), 5) # "Survival hints:" line + 5 hints

    def test_format_survival_hints_truncation(self):
        long_text = "a" * 200
        spans = [SurvivalSpan(long_text, "heavy", "high", ("path",))]
        hints = format_survival_hints(spans)
        self.assertIn("...", hints)
        # Truncation: 58 + 3 + 59 = 120 chars total
        parts = hints.split(": ")
        self.assertEqual(len(parts[1]), 120)

    # ------------------------------------------------------------------
    # Stage 6.6: corpus-driven classifier improvements
    # ------------------------------------------------------------------

    def test_detects_build_files(self):
        text = "Found package.json and pyproject.toml plus Cargo.toml with Cargo.lock go.mod go.sum CMakeLists.txt Makefile"
        spans = score_text(text)
        texts = [s.text for s in spans]
        for f in ("package.json", "pyproject.toml", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "CMakeLists.txt", "Makefile"):
            self.assertIn(f, texts)
        for s in spans:
            if s.text in ("package.json", "pyproject.toml", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "CMakeLists.txt", "Makefile"):
                self.assertEqual(s.features, ("build_file",))
                self.assertEqual(s.weight, "heavy")

    def test_detects_repo_dirs_with_slash(self):
        text = "Look in src/ and tests/ and docs/ examples/ include/ completions/"
        spans = score_text(text)
        texts = [s.text for s in spans]
        for d in ("src/", "tests/", "docs/", "examples/", "include/", "completions/"):
            self.assertIn(d, texts)
        for s in spans:
            if s.text in ("src/", "tests/", "docs/"):
                self.assertEqual(s.features, ("repo_dir",))
                self.assertEqual(s.weight, "medium")

    def test_ignores_generic_prose_words(self):
        text = "The source code includes test coverage. The view should update the package build."
        spans = score_text(text)
        for s in spans:
            self.assertNotIn(s.text.lower(), ["source", "test", "include", "view", "update", "package", "build"])

    def test_detects_js_commands(self):
        text = "Run npm test or pnpm test and yarn test or npm run test"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("npm test", texts)
        self.assertIn("pnpm test", texts)
        self.assertIn("yarn test", texts)
        self.assertIn("npm run test", texts)
        for s in spans:
            if s.text in ("npm test", "pnpm test", "yarn test", "npm run test"):
                self.assertEqual(s.features, ("language_command",))
                self.assertEqual(s.weight, "heavy")

    def test_detects_go_atoms(self):
        text = "Found go.mod and go.sum and run go test ./..."
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("go.mod", texts)
        self.assertIn("go.sum", texts)
        self.assertIn("go test ./...", texts)
        for s in spans:
            if s.text == "go.mod":
                self.assertEqual(s.features, ("build_file",))
            if s.text == "go test ./...":
                self.assertEqual(s.features, ("language_command",))

    def test_detects_rust_atoms(self):
        text = "Found Cargo.toml Cargo.lock and run cargo test or cargo build"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("Cargo.toml", texts)
        self.assertIn("Cargo.lock", texts)
        self.assertIn("cargo test", texts)
        self.assertIn("cargo build", texts)

    def test_detects_cmake_atoms(self):
        text = "Found CMakeLists.txt and run cmake --build then ctest"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("CMakeLists.txt", texts)
        self.assertIn("cmake --build", texts)
        self.assertIn("ctest", texts)
        for s in spans:
            if s.text == "CMakeLists.txt":
                self.assertEqual(s.features, ("build_file",))

    def test_detects_c_macros(self):
        text = "Found #define STB_IMAGE_IMPLEMENTATION and stb_image.h and #define STB_TRUETYPE_IMPLEMENTATION"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("#define", texts)
        self.assertIn("STB_IMAGE_IMPLEMENTATION", texts)
        self.assertIn("stb_image.h", texts)
        self.assertIn("STB_TRUETYPE_IMPLEMENTATION", texts)
        for s in spans:
            if s.text in ("#define", "STB_IMAGE_IMPLEMENTATION", "STB_TRUETYPE_IMPLEMENTATION"):
                self.assertEqual(s.features, ("c_macro",))

    def test_detects_qualified_symbols(self):
        text = "Use tea.Model and call Update() or View() for rendering"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("Update()", texts)
        self.assertIn("View()", texts)
        for s in spans:
            if s.text in ("Update()", "View()"):
                self.assertEqual(s.features, ("qualified_symbol",))
                self.assertEqual(s.weight, "medium")

    def test_preserves_existing_shell_python_detections(self):
        text = "QZ_COMPACTION_PROFILE=coding-llm QZ_LLM_COMPACT_BASE_URL=http://127.0.0.1:18084 python3 -m pytest tests/test_qz_survival_weight.py proxy/qz_responses.py"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertIn("QZ_COMPACTION_PROFILE=coding-llm", texts)
        self.assertIn("QZ_LLM_COMPACT_BASE_URL=http://127.0.0.1:18084", texts)
        # The command pattern captures the full python3 invocation including paths
        full_cmd = [t for t in texts if "python3 -m pytest" in t]
        self.assertTrue(len(full_cmd) > 0)
        self.assertTrue(any("proxy/qz_responses.py" in t for t in full_cmd))

    def test_dedupes_repo_dirs_and_build_files(self):
        text = "src/ and src/ and package.json and package.json"
        spans = score_text(text)
        texts = [s.text for s in spans]
        self.assertEqual(texts.count("src/"), 1)
        self.assertEqual(texts.count("package.json"), 1)

    def test_overlapping_build_file_over_code_symbol(self):
        text = "Build with CMakeLists.txt configuration"
        spans = score_text(text)
        # CMakeLists.txt should be build_file not code_symbol
        for s in spans:
            if s.text == "CMakeLists.txt":
                self.assertEqual(s.features, ("build_file",))

    def test_corpus_snippets_mini_hit_check(self):
        """One snippet per non-Python repo, check expected atoms and non-code_symbol features."""
        cases = [
            # p-limit (JS)
            ("package.json and index.js with npm test",
             {"build_file": ["package.json"], "language_command": ["npm test"]}),
            # bubbletea (Go)
            ("go.mod go.sum and run go test ./... with Update() View()",
             {"build_file": ["go.mod", "go.sum"], "language_command": ["go test ./..."], "qualified_symbol": ["Update()", "View()"]}),
            # fd (Rust)
            ("Cargo.toml Cargo.lock and cargo test or cargo build",
             {"build_file": ["Cargo.toml", "Cargo.lock"], "language_command": ["cargo test", "cargo build"]}),
            # fmt (C++)
            ("CMakeLists.txt and cmake --build then ctest",
             {"build_file": ["CMakeLists.txt"], "language_command": ["cmake --build", "ctest"]}),
            # stb (C)
            ("#define STB_IMAGE_IMPLEMENTATION and stb_image.h",
             {"c_macro": ["#define", "STB_IMAGE_IMPLEMENTATION"]}),
        ]
        for snippet, expected in cases:
            spans = score_text(snippet)
            texts = [s.text for s in spans]
            feat_by_text = {s.text: s.features[0] for s in spans}
            for feat_name, expected_texts in expected.items():
                for et in expected_texts:
                    self.assertIn(et, texts, f"Missing {et} in {snippet!r}")
                    self.assertEqual(feat_by_text[et], feat_name, f"{et} should be {feat_name} not {feat_by_text.get(et)}")
            # At least one non-code_symbol feature per snippet
            non_cs = [f for f in feat_by_text.values() if f != "code_symbol"]
            self.assertTrue(non_cs, f"No non-code_symbol feature in {snippet!r}")

    def test_format_survival_hints_deterministic_with_new_features(self):
        spans = [
            SurvivalSpan("src/", "medium", "medium", ("repo_dir",)),
            SurvivalSpan("package.json", "heavy", "high", ("build_file",)),
            SurvivalSpan("npm test", "heavy", "high", ("language_command",)),
            SurvivalSpan("#define X", "heavy", "high", ("c_macro",)),
            SurvivalSpan("Update()", "medium", "medium", ("qualified_symbol",)),
            SurvivalSpan("aa.py", "heavy", "high", ("path",)),
        ]
        hints1 = format_survival_hints(spans)
        hints2 = format_survival_hints(list(reversed(spans)))
        self.assertEqual(hints1, hints2)

    def _read_fixture(self, name):
        path = f"docs/fixtures/compaction/{name}.md"
        if os.path.exists(path):
            with open(path, "r") as f:
                return f.read()
        return ""

    def test_fixture_01_actual(self):
        content = self._read_fixture("fixture-01-basic-coding-session")
        if not content: self.skipTest("Fixture 01 not found")
        spans = score_text(content)
        texts = [s.text for s in spans]
        self.assertIn("proxy/qz_request_router.py", texts)
        # The exact command might be tricky due to how it's formatted in the fixture, 
        # but let's check for pieces
        self.assertTrue(any("python3 -m py_compile" in t for t in texts))
        self.assertIn("0627f39", texts)

    def test_fixture_02_actual(self):
        content = self._read_fixture("fixture-02-tool-heavy-session")
        if not content: self.skipTest("Fixture 02 not found")
        spans = score_text(content)
        texts = [s.text for s in spans]
        self.assertTrue(any("smoke_compaction_live.py" in t for t in texts))
        self.assertIn("exit_code=0", texts)
        self.assertIn("_build_local_compaction_response", texts)

    def test_fixture_03_actual(self):
        content = self._read_fixture("fixture-03-rejected-approaches")
        if not content: self.skipTest("Fixture 03 not found")
        spans = score_text(content)
        texts = [s.text for s in spans]
        self.assertTrue(any("sudo" in t for t in texts))
        self.assertTrue(any("rejected" in t.lower() for t in texts))
        self.assertIn("model_auto_compact_token_limit", texts)

if __name__ == "__main__":
    unittest.main()
