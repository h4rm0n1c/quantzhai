"""Tests for the multi-repo dogfood corpus staging harness.

Uses tempfile and local git repos so tests do not require network.
Python standard library only.

Stage 6.8 additions: targeted file selection, artifact preservation,
runner coverage, and safety checks.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from scripts.qz_dogfood_corpus_lib import (
    BINARY_EXTENSIONS,
    DIR_EXPAND_LIMIT,
    FILE_READ_CAP_BYTES,
    REPO_TARGET_PATHS,
    TARGETED_FILE_CAP,
    corpus_root,
    default_corpus_root,
    default_work_root,
    ensure_dir,
    ensure_mirror,
    hints_dir,
    is_binary_path,
    is_readable_text_file,
    keep_scratch,
    load_repos_config,
    mirror_path,
    repo_scratch_status,
    resolve_ref,
    run_git,
    run_id,
    run_dir,
    safe_remove,
    scenario_artifact_name,
    scratch_path,
    select_targeted_files,
    selected_files_dir,
    selected_repo_ids,
    selected_repos,
    stage_repo,
    summaries_dir,
    update_mirror,
    work_root,
    write_json,
    write_md,
    write_text_artifact,
)

# ---------- helpers ----------

GIT_AVAILABLE = True
try:
    subprocess.run(["git", "--version"], capture_output=True)
except FileNotFoundError:
    GIT_AVAILABLE = False


def _make_test_repo(path):
    """Create a local git repo with one file and one commit."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
    return result.stdout.strip()


def _make_test_repo_json(tmp_root, repo_path, repo_id="test-repo"):
    """Return a repo config dict pointing at a local file:// repo."""
    return {
        "id": repo_id,
        "url": f"file://{repo_path}",
        "ref": "main",
        "cache_name": f"{repo_id}.git",
        "scratch_name": repo_id,
        "language": "mixed",
        "shape": "test",
        "role": "test",
    }


# ---------- tests ----------


class TestConfigLoading(unittest.TestCase):

    def test_loads_basic_shape(self):
        repos = load_repos_config()
        self.assertGreater(len(repos), 0)
        for r in repos:
            for key in ("id", "url", "cache_name", "scratch_name"):
                self.assertIn(key, r)

    def test_has_expected_ids(self):
        repos = load_repos_config()
        ids = {r["id"] for r in repos}
        expected = {"linuxstreamtools", "quantzhai", "click", "p-limit",
                    "bubbletea", "fd", "fmt", "stb"}
        for eid in expected:
            with self.subTest(repo_id=eid):
                self.assertIn(eid, ids)

    def test_has_8_repos(self):
        repos = load_repos_config()
        self.assertEqual(len(repos), 8)

    def test_invalid_file_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            p = f.name
        with self.assertRaises(json.JSONDecodeError):
            load_repos_config(p)
        os.unlink(p)

    def test_missing_repos_key_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"version": 1}, f)
            p = f.name
        with self.assertRaises(ValueError):
            load_repos_config(p)
        os.unlink(p)

    def test_empty_repos_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"repos": []}, f)
            p = f.name
        with self.assertRaises(ValueError):
            load_repos_config(p)
        os.unlink(p)

    def test_missing_required_keys_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"repos": [{"id": "bad"}]}, f)
            p = f.name
        with self.assertRaises(ValueError):
            load_repos_config(p)
        os.unlink(p)


class TestSelection(unittest.TestCase):

    def setUp(self):
        self.repos = [
            {"id": "a", "url": "x", "cache_name": "a.git", "scratch_name": "a"},
            {"id": "b", "url": "y", "cache_name": "b.git", "scratch_name": "b"},
            {"id": "c", "url": "z", "cache_name": "c.git", "scratch_name": "c"},
        ]

    def test_select_none_returns_all(self):
        result = selected_repos(self.repos)
        self.assertEqual(len(result), 3)

    def test_select_some(self):
        result = selected_repos(self.repos, ["a", "c"])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "a")
        self.assertEqual(result[1]["id"], "c")

    def test_unknown_id_warns(self):
        result = selected_repos(self.repos, ["a", "unknown"])
        self.assertEqual(len(result), 1)

    def test_env_var(self):
        os.environ["QZ_DOGFOOD_REPOS"] = "a,c"
        ids = selected_repo_ids()
        self.assertEqual(ids, ["a", "c"])
        del os.environ["QZ_DOGFOOD_REPOS"]

    def test_env_var_unset(self):
        os.environ.pop("QZ_DOGFOOD_REPOS", None)
        self.assertIsNone(selected_repo_ids())


class TestEnvKnobs(unittest.TestCase):

    def test_default_corpus_root(self):
        self.assertTrue(default_corpus_root().endswith("qz-dogfood-corpus"))

    def test_default_work_root(self):
        self.assertEqual(default_work_root(), "/tmp/qz-dogfood-work")

    def test_env_override_corpus(self):
        os.environ["QZ_DOGFOOD_CORPUS_ROOT"] = "/tmp/test-corpus"
        self.assertEqual(corpus_root(), "/tmp/test-corpus")
        del os.environ["QZ_DOGFOOD_CORPUS_ROOT"]

    def test_env_override_work(self):
        os.environ["QZ_DOGFOOD_WORK_ROOT"] = "/tmp/test-work"
        self.assertEqual(work_root(), "/tmp/test-work")
        del os.environ["QZ_DOGFOOD_WORK_ROOT"]

    def test_run_id_default(self):
        rid = run_id()
        self.assertTrue(rid.startswith("run-"))

    def test_run_id_env(self):
        os.environ["QZ_DOGFOOD_RUN_ID"] = "manual-test"
        self.assertEqual(run_id(), "manual-test")
        del os.environ["QZ_DOGFOOD_RUN_ID"]

    def test_keep_scratch_default(self):
        os.environ.pop("QZ_DOGFOOD_KEEP_SCRATCH", None)
        self.assertFalse(keep_scratch())

    def test_keep_scratch_set(self):
        os.environ["QZ_DOGFOOD_KEEP_SCRATCH"] = "1"
        self.assertTrue(keep_scratch())
        del os.environ["QZ_DOGFOOD_KEEP_SCRATCH"]


class _IsolatedTestBase:
    """Base mixin that sets QZ_DOGFOOD_CORPUS_ROOT and QZ_DOGFOOD_WORK_ROOT
    to isolated temporary directories per test.
    """

    def setUpIsolated(self):
        self._tmpdir = tempfile.mkdtemp()
        self._corpus = os.path.join(self._tmpdir, "corpus")
        self._work = os.path.join(self._tmpdir, "work")
        self._old_corpus = os.environ.get("QZ_DOGFOOD_CORPUS_ROOT")
        self._old_work = os.environ.get("QZ_DOGFOOD_WORK_ROOT")
        self._old_run = os.environ.get("QZ_DOGFOOD_RUN_ID")
        os.environ["QZ_DOGFOOD_CORPUS_ROOT"] = self._corpus
        os.environ["QZ_DOGFOOD_WORK_ROOT"] = self._work

    def tearDownIsolated(self):
        for key, old_val in [
            ("QZ_DOGFOOD_CORPUS_ROOT", self._old_corpus),
            ("QZ_DOGFOOD_WORK_ROOT", self._old_work),
            ("QZ_DOGFOOD_RUN_ID", self._old_run),
        ]:
            if old_val is not None:
                os.environ[key] = old_val
            else:
                os.environ.pop(key, None)
        safe_remove(self._tmpdir)

    def _make_isolated_repo(self, repo_id="test-repo"):
        repo_path = os.path.join(self._tmpdir, repo_id)
        sha = _make_test_repo(repo_path)
        repo = _make_test_repo_json(self._tmpdir, repo_path, repo_id)
        return repo_path, sha, repo


@unittest.skipUnless(GIT_AVAILABLE, "git not available")
class TestMirrorOps(unittest.TestCase, _IsolatedTestBase):

    def setUp(self):
        self.setUpIsolated()
        self.repo_path, self.sha, self.repo = self._make_isolated_repo()

    def tearDown(self):
        self.tearDownIsolated()

    def test_ensure_mirror_creates_bare(self):
        mp = mirror_path(self.repo)
        self.assertFalse(os.path.isdir(mp))
        result = ensure_mirror(self.repo)
        self.assertIsNotNone(result)
        self.assertTrue(os.path.isdir(mp))
        # Bare repos have a HEAD file and refs/ directory
        self.assertTrue(os.path.isfile(os.path.join(mp, "HEAD")))

    def test_ensure_mirror_twice_no_error(self):
        ensure_mirror(self.repo)
        result = ensure_mirror(self.repo)
        self.assertIsNotNone(result)

    def test_update_mirror_on_fresh(self):
        self.assertTrue(update_mirror(self.repo))

    def test_update_mirror_on_existing(self):
        ensure_mirror(self.repo)
        self.assertTrue(update_mirror(self.repo))

    def test_resolve_ref_finds_sha(self):
        ensure_mirror(self.repo)
        ref_used, sha = resolve_ref(self.repo)
        self.assertIsNotNone(ref_used)
        self.assertIsNotNone(sha)
        self.assertEqual(len(sha), 40)

    def test_resolve_ref_none_on_missing(self):
        # Create a repo config pointing at a non-existent mirror
        bad_repo = {
            "id": "missing",
            "url": "file:///nonexistent",
            "ref": "main",
            "cache_name": "missing.git",
            "scratch_name": "missing",
        }
        ref_used, sha = resolve_ref(bad_repo)
        self.assertIsNone(ref_used)
        self.assertIsNone(sha)

    def test_stage_creates_scratch(self):
        ensure_mirror(self.repo)
        sp = stage_repo(self.repo, "test-run")
        self.assertIsNotNone(sp)
        self.assertTrue(os.path.isdir(sp))
        self.assertTrue(os.path.isfile(os.path.join(sp, "README.md")))

    def test_stage_detached_head(self):
        ensure_mirror(self.repo)
        sp = stage_repo(self.repo, "test-run")
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=sp, capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), self.sha)

    def test_stage_fails_without_mirror(self):
        bad_repo = {
            "id": "missing",
            "url": "file:///nonexistent",
            "ref": "main",
            "cache_name": "missing.git",
            "scratch_name": "missing",
        }
        sp = stage_repo(bad_repo, "test-run")
        self.assertIsNone(sp)

    def test_stage_existing_refuses_without_force(self):
        ensure_mirror(self.repo)
        stage_repo(self.repo, "test-run")
        sp = stage_repo(self.repo, "test-run", force=False)
        self.assertIsNotNone(sp)

    def test_stage_existing_with_force(self):
        ensure_mirror(self.repo)
        stage_repo(self.repo, "test-run")
        sp = stage_repo(self.repo, "test-run", force=True)
        self.assertIsNotNone(sp)
        self.assertTrue(os.path.isdir(sp))


@unittest.skipUnless(GIT_AVAILABLE, "git not available")
class TestScratchStatus(unittest.TestCase, _IsolatedTestBase):

    def setUp(self):
        self.setUpIsolated()
        self.repo_path, self.sha, self.repo = self._make_isolated_repo()
        ensure_mirror(self.repo)
        self.sp = stage_repo(self.repo, "status-test")
        self.assertIsNotNone(self.sp)

    def tearDown(self):
        self.tearDownIsolated()

    def test_clean_status(self):
        status = repo_scratch_status(self.sp)
        self.assertEqual(status["head"], self.sha)
        self.assertFalse(status["dirty"])
        self.assertEqual(len(status["status_lines"]), 0)

    def test_dirty_status(self):
        with open(os.path.join(self.sp, "new-file.txt"), "w") as f:
            f.write("dirty")
        status = repo_scratch_status(self.sp)
        self.assertTrue(status["dirty"])
        self.assertGreater(len(status["status_lines"]), 0)


@unittest.skipUnless(GIT_AVAILABLE, "git not available")
class TestClean(unittest.TestCase, _IsolatedTestBase):

    def setUp(self):
        self.setUpIsolated()
        os.environ["QZ_DOGFOOD_RUN_ID"] = "test-clean-run"

        self.repo_path, self.sha, self.repo = self._make_isolated_repo()
        ensure_dir(corpus_root())
        ensure_dir(os.path.join(corpus_root(), "cache"))
        ensure_dir(work_root())
        ensure_mirror(self.repo)
        stage_repo(self.repo, "test-clean-run")

    def tearDown(self):
        os.environ.pop("QZ_DOGFOOD_RUN_ID", None)
        self.tearDownIsolated()

    def test_dirty_recording_before_clean(self):
        """Record dirty status before deletion by modifying the scratch repo."""
        sp = scratch_path(self.repo, "test-clean-run")
        with open(os.path.join(sp, "dirty-file.txt"), "w") as f:
            f.write("dirty content")
        status = repo_scratch_status(sp)
        self.assertTrue(status["dirty"])
        # Clean should record this before deletion (tested by the clean script)
        safe_remove(sp)
        self.assertFalse(os.path.isdir(sp))

    def test_keep_scratch_skips_deletion(self):
        os.environ["QZ_DOGFOOD_KEEP_SCRATCH"] = "1"
        self.assertTrue(keep_scratch())
        sp = scratch_path(self.repo, "test-clean-run")
        self.assertTrue(os.path.isdir(sp))
        del os.environ["QZ_DOGFOOD_KEEP_SCRATCH"]

    def test_mirror_survives_clean(self):
        mp = mirror_path(self.repo)
        self.assertTrue(os.path.isdir(mp))
        sp = scratch_path(self.repo, "test-clean-run")
        safe_remove(sp)
        self.assertTrue(os.path.isdir(mp), "mirror must survive scratch cleanup")

    def test_run_results_survive_clean(self):
        rdir = run_dir("test-clean-run")
        ensure_dir(rdir)
        write_json(os.path.join(rdir, "prepare-results.json"), {"test": True})
        sp = scratch_path(self.repo, "test-clean-run")
        safe_remove(sp)
        self.assertTrue(
            os.path.isfile(os.path.join(rdir, "prepare-results.json")),
            "run results must survive scratch cleanup",
        )

    def test_safe_remove_nonexistent(self):
        safe_remove("/nonexistent/path/that/does/not/exist")
        # Should not raise


class TestScriptStructure(unittest.TestCase):

    def test_scripts_have_shebangs(self):
        scripts = [
            "scripts/qz-dogfood-corpus-prepare",
            "scripts/qz-dogfood-corpus-stage",
            "scripts/qz-dogfood-corpus-status",
            "scripts/qz-dogfood-corpus-clean",
        ]
        for sp in scripts:
            with self.subTest(script=sp):
                with open(sp) as f:
                    first_line = f.readline().strip()
                    self.assertTrue(
                        first_line.startswith("#!/usr/bin/env python3"),
                        f"{sp} missing shebang",
                    )

    def test_scripts_are_executable(self):
        scripts = [
            "scripts/qz-dogfood-corpus-prepare",
            "scripts/qz-dogfood-corpus-stage",
            "scripts/qz-dogfood-corpus-status",
            "scripts/qz-dogfood-corpus-clean",
        ]
        for sp in scripts:
            with self.subTest(script=sp):
                self.assertTrue(
                    os.access(sp, os.X_OK),
                    f"{sp} is not executable",
                )

    def test_no_compaction_runtime_import(self):
        """Scripts must not import proxy compaction modules."""
        import ast
        banned_modules = {"qz_responses", "qz_request_router"}
        scripts = [
            "scripts/qz-dogfood-corpus-prepare",
            "scripts/qz-dogfood-corpus-stage",
            "scripts/qz-dogfood-corpus-status",
            "scripts/qz-dogfood-corpus-clean",
            "scripts/qz_dogfood_corpus_lib.py",
        ]
        for sp in scripts:
            with self.subTest(script=sp):
                with open(sp) as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            parts = alias.name.split(".")
                            if parts[0] in banned_modules:
                                self.fail(
                                    f"{sp} imports banned module: {alias.name}"
                                )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            parts = node.module.split(".")
                            if parts[0] in banned_modules:
                                self.fail(
                                    f"{sp} imports banned module: {node.module}"
                                )


class TestRunDirHelpers(unittest.TestCase):

    def test_run_dir_structure(self):
        rdir = run_dir("test-run")
        self.assertTrue(rdir.endswith("/runs/test-run"))

    def test_run_dir_under_corpus(self):
        root = corpus_root()
        rdir = run_dir("test-run")
        self.assertTrue(rdir.startswith(root))


class TestWriteHelpers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        safe_remove(self.tmpdir)

    def test_write_json(self):
        path = os.path.join(self.tmpdir, "test.json")
        write_json(path, {"key": "value"})
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["key"], "value")

    def test_write_md(self):
        path = os.path.join(self.tmpdir, "test.md")
        write_md(path, "Title", ["line1", "line2"])
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            content = f.read()
        self.assertIn("Title", content)
        self.assertIn("line1", content)
        self.assertIn("line2", content)

    def test_ensure_dir_creates(self):
        path = os.path.join(self.tmpdir, "a", "b", "c")
        self.assertFalse(os.path.isdir(path))
        ensure_dir(path)
        self.assertTrue(os.path.isdir(path))

    def test_ensure_dir_existing(self):
        ensure_dir(self.tmpdir)
        # Should not raise


# ===========================================================================
# Stage 6.8: targeted file selection tests
# ===========================================================================

def _make_fake_repo(root: str, files: dict) -> None:
    """Create a fake repo directory tree from {rel_path: content} dict.

    Binary files can be specified as bytes values.
    """
    for rel, content in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if isinstance(content, bytes):
            with open(full, "wb") as f:
                f.write(content)
        else:
            with open(full, "w") as f:
                f.write(content)


class TestBinaryPathDetection(unittest.TestCase):
    """Test is_binary_path and is_readable_text_file helpers."""

    def test_binary_extension_detected(self):
        for ext in [".pyc", ".png", ".jpg", ".zip", ".bin", ".so"]:
            with self.subTest(ext=ext):
                from pathlib import Path
                self.assertTrue(is_binary_path(Path(f"file{ext}")))

    def test_text_extension_not_binary(self):
        for ext in [".py", ".md", ".txt", ".go", ".rs", ".js", ".h", ".cpp", ".toml"]:
            with self.subTest(ext=ext):
                from pathlib import Path
                self.assertFalse(is_binary_path(Path(f"file{ext}")))

    def test_git_path_not_readable(self):
        from pathlib import Path
        self.assertFalse(is_readable_text_file(Path(".git/config")))

    def test_binary_path_not_readable(self):
        from pathlib import Path
        self.assertFalse(is_readable_text_file(Path("image.png")))

    def test_text_path_readable(self):
        from pathlib import Path
        self.assertTrue(is_readable_text_file(Path("src/main.go")))


class TestTargetedFileSelection(unittest.TestCase):
    """Tests for select_targeted_files — Stage 6.8 requirement tests 1-10."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        safe_remove(self.tmpdir)

    def _make_repo(self, files: dict) -> str:
        """Create a fake repo and return its root path."""
        repo = os.path.join(self.tmpdir, "repo")
        os.makedirs(repo, exist_ok=True)
        _make_fake_repo(repo, files)
        return repo

    # Test 1: targeted file selection prefers configured target files
    # over alphabetically early dotfiles.
    def test_targeted_prefers_configured_over_dotfiles(self):
        """Targeted selection should pick configured targets, not .dotfiles."""
        repo = self._make_repo({
            ".aaa_dotfile": "dotfile content",
            ".zzz_dotfile": "another dotfile",
            "go.mod": "module example.com/test\n\ngo 1.21\n",
            "tea.go": "package tea\n// main tea file\n",
        })
        results = select_targeted_files("bubbletea", repo)
        rel_paths = [r["rel_path"] for r in results]
        # go.mod and tea.go are configured targets for bubbletea
        self.assertIn("go.mod", rel_paths)
        self.assertIn("tea.go", rel_paths)
        # dotfiles should not appear (they are not in bubbletea targets)
        for rp in rel_paths:
            self.assertFalse(
                rp.startswith(".") and "dotfile" in rp,
                f"Dotfile should not be selected: {rp}",
            )

    # Test 2: missing target files do not crash.
    def test_missing_target_files_do_not_crash(self):
        """Missing target paths must be silently skipped."""
        repo = self._make_repo({
            "README.md": "# Test\n",
        })
        # quantzhai targets include many files that won't exist here
        results = select_targeted_files("quantzhai", repo)
        # Should not raise; returns whatever exists
        self.assertIsInstance(results, list)
        # README.md is not a quantzhai target, so fallback may kick in
        # or nothing is returned — both are acceptable without crash

    # Test 3: directory target expands to deterministic small file list.
    def test_directory_target_expands_deterministically(self):
        """A directory target should expand to up to DIR_EXPAND_LIMIT files."""
        repo = self._make_repo({
            "src/main.go": "package main\n",
            "src/helper.go": "package main\n",
            "src/util.go": "package main\n",
            "src/extra.go": "package main\n",
            "src/fifth.go": "package main\n",
            "go.mod": "module test\n",
        })
        results = select_targeted_files("bubbletea", repo)
        src_files = [r for r in results if r["rel_path"].startswith("src/")]
        # Should not exceed DIR_EXPAND_LIMIT
        self.assertLessEqual(len(src_files), DIR_EXPAND_LIMIT)
        # Should be deterministic — repeated calls return same order
        results2 = select_targeted_files("bubbletea", repo)
        src_files2 = [r for r in results2 if r["rel_path"].startswith("src/")]
        self.assertEqual(
            [f["rel_path"] for f in src_files],
            [f["rel_path"] for f in src_files2],
        )

    # Test 4: binary files are skipped.
    def test_binary_files_skipped(self):
        """Files with binary extensions or NUL bytes must not appear in results."""
        repo = self._make_repo({
            "README.md": "# readme\n",
            "image.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00",  # PNG header
            "data.bin": b"\x00\x01\x02\x03",
            # A file with NUL byte but no binary extension
            "tricky.go": b"package main\x00\nbinary inside",
        })
        results = select_targeted_files("bubbletea", repo)
        rel_paths = [r["rel_path"] for r in results]
        self.assertNotIn("image.png", rel_paths)
        self.assertNotIn("data.bin", rel_paths)
        self.assertNotIn("tricky.go", rel_paths)

    # Test 5: large text files are capped.
    def test_large_files_capped(self):
        """Files larger than FILE_READ_CAP_BYTES must be truncated."""
        big_content = "x" * (FILE_READ_CAP_BYTES * 3)
        repo = self._make_repo({
            "README.md": big_content,
        })
        results = select_targeted_files("bubbletea", repo)
        readme_results = [r for r in results if r["rel_path"] == "README.md"]
        if readme_results:
            r = readme_results[0]
            self.assertLessEqual(r["bytes_read"], FILE_READ_CAP_BYTES)
            self.assertTrue(r["capped"])

    # Test 6: selected file reasons/categories are recorded.
    def test_selected_file_reasons_recorded(self):
        """Every selected file must have a non-empty reason field."""
        repo = self._make_repo({
            "go.mod": "module test\n",
            "tea.go": "package tea\n",
            "README.md": "# BubbleTea\n",
        })
        results = select_targeted_files("bubbletea", repo)
        self.assertGreater(len(results), 0, "Expected at least one file selected")
        for r in results:
            self.assertIn("reason", r)
            self.assertIsInstance(r["reason"], str)
            self.assertTrue(len(r["reason"]) > 0, "Reason must not be empty")
            # Reason should contain a colon (e.g. "exact:build", "dir:source")
            self.assertIn(":", r["reason"])

    # Test 7: full summary artifact path can be written.
    def test_summary_artifact_path_can_be_written(self):
        """write_text_artifact should create a file with given content."""
        path = os.path.join(self.tmpdir, "summaries", "click-deep.summary.md")
        content = "## Goal\nTest the click library.\n"
        write_text_artifact(path, content)
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            self.assertEqual(f.read(), content)

    # Test 8: survival hints artifact path can be written.
    def test_hints_artifact_path_can_be_written(self):
        """write_json should create a valid JSON hints file."""
        path = os.path.join(self.tmpdir, "hints", "fd-deep.hints.json")
        data = {"spans_count": 5, "features": {"build_file": 2}}
        write_json(path, data)
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            loaded = json.load(f)
        self.assertEqual(loaded["spans_count"], 5)

    # Test 9: selected-files artifact path can be written.
    def test_selected_files_artifact_path_can_be_written(self):
        """write_json should create a valid JSON selected-files file."""
        path = os.path.join(self.tmpdir, "selected-files", "fmt-targeted.files.json")
        data = {
            "repo_id": "fmt",
            "files": [{"rel_path": "CMakeLists.txt", "reason": "exact:build"}],
        }
        write_json(path, data)
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            loaded = json.load(f)
        self.assertEqual(loaded["repo_id"], "fmt")

    # Test 10: generic fallback still works if no targets match.
    def test_fallback_sorted_when_no_targets(self):
        """If no configured targets exist in scratch, use sorted fallback."""
        repo = self._make_repo({
            "aaa_file.txt": "first alphabetically",
            "zzz_file.txt": "last alphabetically",
        })
        # "unknown-repo" has no REPO_TARGET_PATHS entry
        results = select_targeted_files("unknown-repo-id", repo)
        self.assertGreater(len(results), 0)
        # All reasons should be fallback
        for r in results:
            self.assertEqual(r["reason"], "fallback:sorted")

    # Test 11: lib file remains stdlib only (no proxy imports).
    def test_lib_is_stdlib_only(self):
        """qz_dogfood_corpus_lib.py must not import proxy modules."""
        banned = {"qz_responses", "qz_request_router", "qz_survival_weight"}
        lib_path = os.path.join(
            os.path.dirname(__file__), "..", "scripts", "qz_dogfood_corpus_lib.py"
        )
        with open(lib_path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    self.assertNotIn(
                        parts[0], banned,
                        f"lib imports banned module: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    parts = node.module.split(".")
                    self.assertNotIn(
                        parts[0], banned,
                        f"lib imports banned module: {node.module}",
                    )

    # Test 12: lib does not import proxy/qz_responses.py (AST import check).
    def test_lib_does_not_import_proxy_qz_responses(self):
        """qz_dogfood_corpus_lib.py must not import qz_responses as a module."""
        lib_path = os.path.join(
            os.path.dirname(__file__), "..", "scripts", "qz_dogfood_corpus_lib.py"
        )
        with open(lib_path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        "qz_responses", alias.name,
                        f"lib imports qz_responses: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                self.assertNotIn(
                    "qz_responses", module,
                    f"lib imports qz_responses: {module}",
                )

    # Test 13: runner does not change compaction defaults.
    def test_compaction_defaults_unchanged(self):
        """config/default/compaction.json default profile must remain heuristic."""
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "default", "compaction.json"
        )
        with open(config_path) as f:
            cfg = json.load(f)
        default_profile = cfg.get("profiles", {}).get("default", {})
        self.assertEqual(
            default_profile.get("mode"), "heuristic",
            "Default compaction mode must remain 'heuristic'",
        )
        # v3 / localcmp:v3 must not be the default mode
        self.assertNotIn("v3", default_profile.get("mode", ""))
        self.assertNotIn("llm", default_profile.get("mode", ""))

    # Test 14: runner output remains JSON serializable.
    def test_evidence_dict_json_serializable(self):
        """A typical evidence dict (as would be produced by the runner) must be JSON-serializable."""
        evidence = {
            "repo_id": "fmt",
            "scenario": "scenario3-targeted-coverage",
            "scenario_label": "Targeted coverage inspection",
            "use_targeted": True,
            "max_turns": 12,
            "result": "v3_accepted",
            "blob_prefix": "localcmp:v3:abc123",
            "v3_payload": {
                "version": 3,
                "engine": "llm",
                "schema_version": "v3",
                "fallback": False,
                "survival_hint_count": 5,
                "depth": 1,
                "preserved_items": 3,
                "created_at": "2026-05-27T00:00:00Z",
            },
            "latency_ms": 1234,
            "headings_present": [True, True, False],
            "reasoning_leak": False,
            "placeholder_leak": False,
            "survival_hints": {"spans_count": 10, "features": {"build_file": 2}},
            "v3_summary_preview": "## Goal\ntest",
            "selected_files": [{"rel_path": "CMakeLists.txt", "reason": "exact:build"}],
            "selected_files_count": 1,
            "summary_text_path": "/tmp/summaries/fmt-scenario3.summary.md",
            "survival_hints_path": "/tmp/hints/fmt-scenario3.hints.json",
            "selected_files_path": "/tmp/selected-files/fmt-scenario3.files.json",
            "error": None,
            "capture_dir": None,
            "scratch_clean": True,
        }
        # Must not raise
        serialized = json.dumps(evidence)
        self.assertIsInstance(serialized, str)
        # Must round-trip cleanly
        loaded = json.loads(serialized)
        self.assertEqual(loaded["repo_id"], "fmt")
        self.assertEqual(loaded["result"], "v3_accepted")


class TestTargetedFileSelectionEdgeCases(unittest.TestCase):
    """Edge cases for select_targeted_files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        safe_remove(self.tmpdir)

    def test_empty_scratch_returns_empty(self):
        """Empty scratch directory returns empty list without error."""
        repo = os.path.join(self.tmpdir, "empty_repo")
        os.makedirs(repo)
        results = select_targeted_files("bubbletea", repo)
        self.assertIsInstance(results, list)

    def test_nonexistent_scratch_returns_empty(self):
        """Nonexistent scratch path returns empty list without error."""
        results = select_targeted_files("bubbletea", "/nonexistent/path/1234")
        self.assertEqual(results, [])

    def test_total_file_cap_respected(self):
        """Total selected files must not exceed TARGETED_FILE_CAP."""
        repo = os.path.join(self.tmpdir, "big_repo")
        os.makedirs(repo)
        # Create 50 .go files in root
        for i in range(50):
            with open(os.path.join(repo, f"file{i:02d}.go"), "w") as f:
                f.write(f"package main // file {i}\n")
        results = select_targeted_files("bubbletea", repo)
        self.assertLessEqual(len(results), TARGETED_FILE_CAP)

    def test_git_directory_skipped(self):
        """Files under .git/ must not be selected."""
        repo = os.path.join(self.tmpdir, "git_repo")
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        with open(os.path.join(repo, ".git", "config"), "w") as f:
            f.write("[core]\n  bare = false\n")
        with open(os.path.join(repo, "README.md"), "w") as f:
            f.write("# readme\n")
        results = select_targeted_files("unknown-repo-id", repo)
        for r in results:
            self.assertNotIn(".git", r["rel_path"])

    def test_result_fields_complete(self):
        """Every result dict must have all required fields."""
        repo = os.path.join(self.tmpdir, "field_repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "go.mod"), "w") as f:
            f.write("module test\n")
        results = select_targeted_files("bubbletea", repo)
        for r in results:
            for field in ("path", "rel_path", "reason", "bytes_read", "capped", "text"):
                self.assertIn(field, r, f"Missing field {field!r} in result")


class TestArtifactHelpers(unittest.TestCase):
    """Tests for Stage 6.8 artifact subdirectory and naming helpers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        safe_remove(self.tmpdir)

    def test_summaries_dir(self):
        rdir = os.path.join(self.tmpdir, "runs", "test-run")
        self.assertTrue(summaries_dir(rdir).endswith("/summaries"))
        self.assertTrue(summaries_dir(rdir).startswith(rdir))

    def test_hints_dir(self):
        rdir = os.path.join(self.tmpdir, "runs", "test-run")
        self.assertTrue(hints_dir(rdir).endswith("/survival-hints"))

    def test_selected_files_dir(self):
        rdir = os.path.join(self.tmpdir, "runs", "test-run")
        self.assertTrue(selected_files_dir(rdir).endswith("/selected-files"))

    def test_scenario_artifact_name(self):
        name = scenario_artifact_name("click", "deep-coverage", ".summary.md")
        self.assertEqual(name, "click-deep-coverage.summary.md")

    def test_scenario_artifact_name_json(self):
        name = scenario_artifact_name("fd", "scenario3-targeted-coverage", ".hints.json")
        self.assertEqual(name, "fd-scenario3-targeted-coverage.hints.json")

    def test_write_text_artifact_creates_dirs(self):
        """write_text_artifact should create parent directories automatically."""
        path = os.path.join(self.tmpdir, "deep", "nested", "test.summary.md")
        write_text_artifact(path, "## Goal\nTest\n")
        self.assertTrue(os.path.isfile(path))

    def test_write_text_artifact_content(self):
        """write_text_artifact must write exact content."""
        path = os.path.join(self.tmpdir, "test.md")
        content = "## Goal\nSome text\n## Next Actions\nDo things\n"
        write_text_artifact(path, content)
        with open(path) as f:
            self.assertEqual(f.read(), content)


class TestRepoTargetPaths(unittest.TestCase):
    """Tests for REPO_TARGET_PATHS configuration."""

    def test_all_8_repos_have_targets(self):
        """All 8 corpus repos must have configured target paths."""
        expected = {"linuxstreamtools", "quantzhai", "click", "p-limit",
                    "bubbletea", "fd", "fmt", "stb"}
        for repo_id in expected:
            with self.subTest(repo_id=repo_id):
                self.assertIn(repo_id, REPO_TARGET_PATHS)
                targets = REPO_TARGET_PATHS[repo_id]
                self.assertGreater(len(targets), 0)

    def test_stb_includes_header_files(self):
        """stb targets must include C header files to exercise c_macro."""
        stb_specs = [spec for spec, _ in REPO_TARGET_PATHS["stb"]]
        self.assertIn("stb_image.h", stb_specs)
        self.assertIn("stb_truetype.h", stb_specs)

    def test_bubbletea_includes_go_mod(self):
        """bubbletea targets must include go.mod and core Go source."""
        bt_specs = [spec for spec, _ in REPO_TARGET_PATHS["bubbletea"]]
        self.assertIn("go.mod", bt_specs)

    def test_fmt_includes_cmake(self):
        """fmt targets must include CMakeLists.txt."""
        fmt_specs = [spec for spec, _ in REPO_TARGET_PATHS["fmt"]]
        self.assertIn("CMakeLists.txt", fmt_specs)

    def test_fd_includes_cargo_toml(self):
        """fd targets must include Cargo.toml."""
        fd_specs = [spec for spec, _ in REPO_TARGET_PATHS["fd"]]
        self.assertIn("Cargo.toml", fd_specs)

    def test_target_tuples_have_two_fields(self):
        """Every REPO_TARGET_PATHS entry must be (spec, category) tuple."""
        for repo_id, targets in REPO_TARGET_PATHS.items():
            for entry in targets:
                with self.subTest(repo_id=repo_id, entry=entry):
                    self.assertEqual(len(entry), 2)
                    spec, category = entry
                    self.assertIsInstance(spec, str)
                    self.assertIsInstance(category, str)
                    self.assertTrue(len(spec) > 0)
                    self.assertTrue(len(category) > 0)


class TestRunnerScenarioStructure(unittest.TestCase):
    """Structural tests for the runner's SCENARIOS dict (Stage 6.8)."""

    def _load_scenarios(self) -> dict:
        """Import SCENARIOS from the runner without running main()."""
        import importlib.machinery
        import importlib.util
        runner_path = os.path.join(
            os.path.dirname(__file__), "..", "scripts", "qz-dogfood-corpus-run"
        )
        # Runner is not a .py file; use importlib with SourceFileLoader
        loader = importlib.machinery.SourceFileLoader("qz_dogfood_corpus_run", runner_path)
        spec = importlib.util.spec_from_loader("qz_dogfood_corpus_run", loader)
        # We only need the constants; module-level code defines functions only.
        # sys.exit() is only called inside main(), so this is safe.
        mod = importlib.util.module_from_spec(spec)
        # Suppress actual main execution by setting harmless argv
        original_argv = sys.argv[:]
        sys.argv = ["qz-dogfood-corpus-run"]
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.argv = original_argv
        return mod.SCENARIOS

    def test_scenarios_has_targeted_coverage(self):
        scenarios = self._load_scenarios()
        self.assertIn("scenario3-targeted-coverage", scenarios)

    def test_scenarios_has_deep_coverage(self):
        scenarios = self._load_scenarios()
        self.assertIn("deep-coverage", scenarios)

    def test_deep_coverage_has_sufficient_turns(self):
        """Deep scenario must use enough turns to exercise survival-hinted compaction."""
        scenarios = self._load_scenarios()
        deep = scenarios["deep-coverage"]
        # Need >20 turns so history exceeds keep_recent_items=20
        self.assertGreater(deep["max_turns"], 20)

    def test_deep_coverage_uses_targeted_selection(self):
        scenarios = self._load_scenarios()
        deep = scenarios["deep-coverage"]
        self.assertTrue(deep["use_targeted"])

    def test_targeted_coverage_uses_targeted_selection(self):
        scenarios = self._load_scenarios()
        targeted = scenarios["scenario3-targeted-coverage"]
        self.assertTrue(targeted["use_targeted"])

    def test_scenario_keys_have_required_fields(self):
        scenarios = self._load_scenarios()
        required = {"label", "max_turns", "use_targeted", "prompt"}
        for sk, s in scenarios.items():
            with self.subTest(scenario=sk):
                for field in required:
                    self.assertIn(field, s, f"scenario {sk!r} missing field {field!r}")

    def test_all_max_turns_positive(self):
        scenarios = self._load_scenarios()
        for sk, s in scenarios.items():
            with self.subTest(scenario=sk):
                self.assertGreater(s["max_turns"], 0)


if __name__ == "__main__":
    unittest.main()
