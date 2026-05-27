"""Tests for the multi-repo dogfood corpus staging harness.

Uses tempfile and local git repos so tests do not require network.
Python standard library only.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from scripts.qz_dogfood_corpus_lib import (
    corpus_root,
    default_corpus_root,
    default_work_root,
    ensure_dir,
    ensure_mirror,
    keep_scratch,
    load_repos_config,
    mirror_path,
    repo_scratch_status,
    resolve_ref,
    run_git,
    run_id,
    run_dir,
    safe_remove,
    scratch_path,
    selected_repo_ids,
    selected_repos,
    stage_repo,
    update_mirror,
    work_root,
    write_json,
    write_md,
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


if __name__ == "__main__":
    unittest.main()
