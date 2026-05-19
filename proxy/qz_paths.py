#!/usr/bin/env python3
import os
from pathlib import Path


def qz_root() -> Path:
    raw = os.environ.get("QZ_ROOT")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def qz_var_dir() -> Path:
    raw = os.environ.get("QZ_VAR_DIR")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return qz_root() / "var"


def model_inventory_path() -> Path:
    return qz_var_dir() / "generated" / "model-inventory.json"


def codex_home_dir() -> Path:
    return qz_var_dir() / "codex-home"


def codex_model_catalog_dir() -> Path:
    return codex_home_dir() / "model-catalogs"


def codex_model_catalog_path() -> Path:
    return codex_model_catalog_dir() / "qwenzhai-models.json"


def codex_config_path() -> Path:
    return codex_home_dir() / "config.toml"
