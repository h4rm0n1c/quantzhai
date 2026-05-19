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


def codex_generated_dir() -> Path:
    """Server-side generated Codex artifact directory (#56 Slice D-impl).

    Replaces var/codex-home/ as the server-internal location for generated
    Codex catalog and config. CODEX_HOME is client-local state after #58.
    """
    return qz_var_dir() / "generated" / "codex"


def codex_model_catalog_path() -> Path:
    return codex_generated_dir() / "qwenzhai-models.json"


def codex_config_path() -> Path:
    return codex_generated_dir() / "config.toml"


# Deprecated: var/codex-home/ was the server-local CODEX_HOME before #58.
# After #58, CODEX_HOME is client-local ($HOME/.qz-codex/codex-home).
# Kept for compatibility; not called by any runtime code after Slice D-impl.

def codex_home_dir() -> Path:
    """Deprecated — server-side var/codex-home/ is no longer CODEX_HOME after #58."""
    return qz_var_dir() / "codex-home"


def codex_model_catalog_dir() -> Path:
    """Deprecated — model catalogs now live under codex_generated_dir()."""
    return codex_home_dir() / "model-catalogs"
