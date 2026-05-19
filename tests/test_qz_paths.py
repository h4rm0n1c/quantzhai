"""Tests for proxy/qz_paths.py — #56 Slice B path helper abstraction."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import proxy.qz_paths as qz_paths_module
from proxy.qz_paths import (
    codex_config_path,
    codex_generated_dir,
    codex_model_catalog_path,
    model_inventory_path,
    qz_root,
    qz_var_dir,
)


class QzPathsDefaultTests(unittest.TestCase):
    """Tests that helpers return expected current paths with default env."""

    _ENV_KEYS = ("QZ_ROOT", "QZ_VAR_DIR")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_helpers_return_path_objects(self):
        self.assertIsInstance(qz_root(), Path)
        self.assertIsInstance(qz_var_dir(), Path)
        self.assertIsInstance(model_inventory_path(), Path)
        self.assertIsInstance(codex_generated_dir(), Path)
        self.assertIsInstance(codex_model_catalog_path(), Path)
        self.assertIsInstance(codex_config_path(), Path)

    def test_qz_root_points_to_repo_root(self):
        root = qz_root()
        self.assertTrue((root / "proxy" / "qz_paths.py").is_file())
        self.assertTrue((root / "scripts" / "qz-env").is_file())

    def test_qz_var_dir_defaults_to_root_var(self):
        root = qz_root()
        self.assertEqual(qz_var_dir(), root / "var")

    def test_model_inventory_path_default_matches_current_var_path(self):
        expected = qz_var_dir() / "generated" / "model-inventory.json"
        self.assertEqual(model_inventory_path(), expected)

    def test_codex_model_catalog_path_default_matches_current_var_path(self):
        expected = codex_generated_dir() / "qwenzhai-models.json"
        self.assertEqual(codex_model_catalog_path(), expected)

    def test_codex_config_path_default_matches_current_var_path(self):
        expected = codex_generated_dir() / "config.toml"
        self.assertEqual(codex_config_path(), expected)


class QzPathsEnvOverrideTests(unittest.TestCase):
    """Tests that QZ_VAR_DIR env var is respected."""

    _ENV_KEYS = ("QZ_ROOT", "QZ_VAR_DIR")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_qz_var_dir_env_override_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom_var = Path(tmp) / "custom-var"
            custom_var.mkdir()
            os.environ["QZ_VAR_DIR"] = str(custom_var)
            self.assertEqual(qz_var_dir(), custom_var)
            self.assertEqual(model_inventory_path(), custom_var / "generated" / "model-inventory.json")
            self.assertEqual(codex_generated_dir(), custom_var / "generated" / "codex")
            self.assertEqual(codex_model_catalog_path(), custom_var / "generated" / "codex" / "qwenzhai-models.json")
            self.assertEqual(codex_config_path(), custom_var / "generated" / "codex" / "config.toml")

    def test_qz_root_env_override_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom_root = Path(tmp) / "custom-root"
            custom_root.mkdir()
            os.environ["QZ_ROOT"] = str(custom_root)
            os.environ.pop("QZ_VAR_DIR", None)
            self.assertEqual(qz_root(), custom_root)
            self.assertEqual(qz_var_dir(), custom_root / "var")


class QzPathsNoSideEffectsTests(unittest.TestCase):
    """Tests that helpers do not create files or directories."""

    _ENV_KEYS = ("QZ_ROOT", "QZ_VAR_DIR")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_helpers_do_not_create_files_or_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["QZ_VAR_DIR"] = tmp
            os.environ.pop("QZ_ROOT", None)
            paths = [
                model_inventory_path(),
                codex_generated_dir(),
                codex_model_catalog_path(),
                codex_config_path(),
            ]
            for path in paths:
                self.assertFalse(path.exists(), f"helper created {path}")


class QzPathsCodexHomeIndependenceTests(unittest.TestCase):
    """Tests that qz_paths helpers ignore CODEX_HOME env var (stale after #58)."""

    _ENV_KEYS = ("QZ_ROOT", "QZ_VAR_DIR", "CODEX_HOME")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_codex_model_catalog_path_ignores_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            var_dir = Path(tmp) / "var"
            var_dir.mkdir(parents=True)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ["CODEX_HOME"] = str(Path(tmp) / "client-home")
            path = codex_model_catalog_path()
            self.assertIn(str(var_dir), str(path))
            self.assertNotIn("client-home", str(path))

    def test_codex_config_path_ignores_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            var_dir = Path(tmp) / "var"
            var_dir.mkdir(parents=True)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ["CODEX_HOME"] = str(Path(tmp) / "client-home")
            path = codex_config_path()
            self.assertIn(str(var_dir), str(path))
            self.assertNotIn("client-home", str(path))

    def test_model_inventory_path_ignores_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            var_dir = Path(tmp) / "var"
            var_dir.mkdir(parents=True)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ["CODEX_HOME"] = str(Path(tmp) / "client-home")
            path = model_inventory_path()
            self.assertIn(str(var_dir), str(path))
            self.assertNotIn("client-home", str(path))


class QzPathsCodexGeneratedDirTests(unittest.TestCase):
    """Tests for codex_generated_dir() and migrated A2/A3 paths (#56 Slice D-impl)."""

    _ENV_KEYS = ("QZ_ROOT", "QZ_VAR_DIR", "CODEX_HOME")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_codex_generated_dir_default_matches_generated_codex_path(self):
        expected = qz_var_dir() / "generated" / "codex"
        self.assertEqual(codex_generated_dir(), expected)

    def test_codex_model_catalog_path_moves_to_var_generated_codex(self):
        expected = codex_generated_dir() / "qwenzhai-models.json"
        self.assertEqual(codex_model_catalog_path(), expected)
        self.assertIn("generated", codex_model_catalog_path().parts)
        self.assertNotIn("codex-home", codex_model_catalog_path().parts)

    def test_codex_config_path_moves_to_var_generated_codex(self):
        expected = codex_generated_dir() / "config.toml"
        self.assertEqual(codex_config_path(), expected)
        self.assertIn("generated", codex_config_path().parts)
        self.assertNotIn("codex-home", codex_config_path().parts)

    def test_codex_generated_paths_ignore_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            var_dir = Path(tmp) / "var"
            var_dir.mkdir(parents=True)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ["CODEX_HOME"] = str(Path(tmp) / "client-home")
            self.assertIn(str(var_dir), str(codex_generated_dir()))
            self.assertNotIn("client-home", str(codex_generated_dir()))
            self.assertIn(str(var_dir), str(codex_model_catalog_path()))
            self.assertNotIn("client-home", str(codex_model_catalog_path()))
            self.assertIn(str(var_dir), str(codex_config_path()))
            self.assertNotIn("client-home", str(codex_config_path()))

    def test_old_var_codex_home_catalog_not_created_by_default(self):
        """Calling helpers must not create var/codex-home/model-catalogs/."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["QZ_VAR_DIR"] = tmp
            _ = codex_generated_dir()
            _ = codex_model_catalog_path()
            _ = codex_config_path()
            old_catalog_dir = Path(tmp) / "codex-home" / "model-catalogs"
            self.assertFalse(old_catalog_dir.exists(), "old codex-home dir must not be created")

    def test_no_deprecated_codex_home_helpers_exported(self):
        """codex_home_dir and codex_model_catalog_dir must not exist in qz_paths (#56 Slice E)."""
        self.assertFalse(hasattr(qz_paths_module, "codex_home_dir"),
                         "codex_home_dir was removed in Slice E; do not re-add")
        self.assertFalse(hasattr(qz_paths_module, "codex_model_catalog_dir"),
                         "codex_model_catalog_dir was removed in Slice E; do not re-add")


if __name__ == "__main__":
    unittest.main()
