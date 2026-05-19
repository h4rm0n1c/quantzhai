"""Tests for proxy/qz_paths.py — #56 Slice B path helper abstraction."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_paths import (
    codex_config_path,
    codex_home_dir,
    codex_model_catalog_dir,
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
        self.assertIsInstance(codex_home_dir(), Path)
        self.assertIsInstance(codex_model_catalog_dir(), Path)
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
        expected = qz_var_dir() / "model-inventory.json"
        self.assertEqual(model_inventory_path(), expected)

    def test_codex_home_dir_default_matches_current_var_path(self):
        expected = qz_var_dir() / "codex-home"
        self.assertEqual(codex_home_dir(), expected)

    def test_codex_model_catalog_dir_default_matches_current_var_path(self):
        expected = codex_home_dir() / "model-catalogs"
        self.assertEqual(codex_model_catalog_dir(), expected)

    def test_codex_model_catalog_path_default_matches_current_var_path(self):
        expected = codex_model_catalog_dir() / "qwenzhai-models.json"
        self.assertEqual(codex_model_catalog_path(), expected)

    def test_codex_config_path_default_matches_current_var_path(self):
        expected = codex_home_dir() / "config.toml"
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
            self.assertEqual(model_inventory_path(), custom_var / "model-inventory.json")
            self.assertEqual(codex_home_dir(), custom_var / "codex-home")
            self.assertEqual(codex_model_catalog_path(), custom_var / "codex-home" / "model-catalogs" / "qwenzhai-models.json")
            self.assertEqual(codex_config_path(), custom_var / "codex-home" / "config.toml")

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
                codex_home_dir(),
                codex_model_catalog_dir(),
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

    def test_codex_home_dir_uses_var_dir_not_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            var_dir = Path(tmp) / "var"
            var_dir.mkdir(parents=True)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ["CODEX_HOME"] = str(Path(tmp) / "client-home")
            path = codex_home_dir()
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


if __name__ == "__main__":
    unittest.main()
