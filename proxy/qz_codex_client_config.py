#!/usr/bin/env python3
"""Bootstrap metadata and catalog delivery for remote qz-codex clients (#57 Slice C1).

Remote qz-codex clients (topology B — LAN-separated from QuantZhai server) need:
  - provider base_url pointing to the QuantZhai server, not hardcoded 127.0.0.1
  - downloadable Codex model catalog to write locally before launching Codex CLI

This module provides two read-only helper functions for GET /qz/codex/client-config
and GET /qz/codex/model-catalog. Both are pure: no file writes, no refresh calls,
no routing changes, no secrets exposed.

Safety rules:
  - env_key is key NAME only; never expose the key value
  - base_url is derived from QZ_PROXY_HOST:QZ_PROXY_PORT, not hardcoded
  - no API key values, no env var dumps, no prompt contents
  - generated catalog remains cache/view, not routing authority
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from .qz_paths import (
        codex_model_catalog_path as _codex_model_catalog_path,
        model_inventory_path as _model_inventory_path,
    )
except ImportError:
    from qz_paths import (
        codex_model_catalog_path as _codex_model_catalog_path,
        model_inventory_path as _model_inventory_path,
    )

CODEX_CLIENT_CONFIG_SCHEMA = "qz.codex.client_config.v1"
CODEX_MODEL_CATALOG_FILENAME = "qwenzhai-models.json"

# QuantZhai-specific Codex provider constants.
# model_provider identifies the [model_providers.*] block in Codex config.toml.
CODEX_MODEL_PROVIDER = "quantzhai"

# CODEX_PROVIDER_NAME = "OpenAI" is an OpenAI masquerade required for remote compaction.
# Codex's supports_remote_compaction() returns true only when provider.name == "OpenAI"
# (or the provider is Azure). With name = "OpenAI" and requires_openai_auth absent/false,
# Codex selects the /v1/responses/compact remote compaction path. QuantZhai handles that
# endpoint and runs Zenkai v3 compaction on the conversation history. No real OpenAI auth
# is required because requires_openai_auth defaults to false in the generated config.toml.
# See docs/compaction-codex-setup.md §Stage 6.10.1 and compact_remote.rs:should_use_remote_compact_task().
CODEX_PROVIDER_NAME = "OpenAI"
CODEX_WIRE_API = "responses"
CODEX_ENV_KEY = "LOCAL_QWEN_API_KEY"   # env var name only — never the value


def _proxy_base_url(suffix: str = "") -> str:
    """Return http://<QZ_PROXY_HOST>:<QZ_PROXY_PORT><suffix>.

    Uses environment variables so remote operators can set QZ_PROXY_HOST
    to their server IP without hardcoding 127.0.0.1.
    """
    host = os.environ.get("QZ_PROXY_HOST", "127.0.0.1")
    port = os.environ.get("QZ_PROXY_PORT", "18180")
    return f"http://{host}:{port}{suffix}"


def _catalog_path() -> Path:
    """Return the server-side path to the generated Codex model catalog JSON file.

    Uses QZ_VAR_DIR (falling back to <repo_root>/var) and NOT CODEX_HOME.
    CODEX_HOME is a Codex CLI client launcher concept; the client sets its own
    local CODEX_HOME at $HOME/.qz-codex/codex-home and downloads the catalog
    from /qz/codex/model-catalog over HTTP. The server path (var/generated/codex/)
    is independent of the client CODEX_HOME setting (#58, #56 Slice D).
    """
    return _codex_model_catalog_path()


def _catalog_file_meta(path: Path) -> Dict[str, Any]:
    """Return bounded file metadata for the catalog file.

    Safe failure returns {}.
    sha256_12 computed only for files ≤64 KiB.
    """
    try:
        st = path.stat()
        result: Dict[str, Any] = {
            "mtime_ms": int(st.st_mtime * 1000),
            "size_bytes": st.st_size,
            "sha256_12": None,
        }
        if st.st_size <= 65536:
            try:
                result["sha256_12"] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            except Exception:
                pass
        else:
            result["hash_skipped"] = "too_large"
        return result
    except Exception:
        return {}


def _file_mtime_ms(path: Path) -> Optional[int]:
    try:
        return int(path.stat().st_mtime * 1000)
    except Exception:
        return None


def _catalog_freshness(
    catalog_mtime_ms: Optional[int],
    source_mtime_ms: Optional[int],
) -> Dict[str, Any]:
    """Compute catalog freshness vs. the model inventory cache.

    The codex catalog is generated from the model inventory.  If inventory
    is newer than the catalog (or the catalog is missing), the catalog is
    stale and the client should request a refresh.
    """
    now_ms = int(time.time() * 1000)
    freshness: Dict[str, Any] = {
        "catalog_mtime_ms": catalog_mtime_ms,
        "source_mtime_ms": source_mtime_ms,
        "catalog_age_seconds": None,
        "stale": False,
    }
    if catalog_mtime_ms is None:
        freshness["stale"] = True
        freshness["reason"] = "catalog_missing"
        freshness["remediation"] = "POST /qz/codex/model-catalog/refresh"
        return freshness
    freshness["catalog_age_seconds"] = max(0, (now_ms - catalog_mtime_ms) // 1000)
    if source_mtime_ms is not None and source_mtime_ms > catalog_mtime_ms:
        freshness["stale"] = True
        freshness["reason"] = "source_newer_than_catalog"
        freshness["remediation"] = "POST /qz/codex/model-catalog/refresh"
    return freshness


def codex_client_config_payload() -> Dict[str, Any]:
    """Return bounded bootstrap metadata for remote qz-codex clients.

    Pure — no file writes, no refresh calls, no routing changes.
    Never exposes API key values or environment variable contents.
    """
    base_url = _proxy_base_url("/v1")
    catalog_url = _proxy_base_url("/qz/codex/model-catalog")
    catalog_path = _catalog_path()
    inventory_path = _model_inventory_path()

    warnings = []
    model_catalog: Dict[str, Any] = {
        "mode": "download",
        "url": catalog_url,
        "local_filename": CODEX_MODEL_CATALOG_FILENAME,
        "refresh_url": _proxy_base_url("/qz/codex/model-catalog/refresh"),
    }

    meta = _catalog_file_meta(catalog_path)
    catalog_mtime_ms: Optional[int] = None
    if meta:
        if "sha256_12" in meta:
            model_catalog["sha256_12"] = meta["sha256_12"]
        if "mtime_ms" in meta:
            model_catalog["mtime_ms"] = meta["mtime_ms"]
            catalog_mtime_ms = meta["mtime_ms"]
        if "hash_skipped" in meta:
            model_catalog["hash_skipped"] = meta["hash_skipped"]
    else:
        warnings.append({
            "warning": "missing_codex_catalog",
            "path": str(catalog_path),
            "remediation": "POST /qz/codex/model-catalog/refresh",
        })

    source_mtime_ms = _file_mtime_ms(inventory_path)
    freshness = _catalog_freshness(catalog_mtime_ms, source_mtime_ms)
    model_catalog["freshness"] = freshness
    if freshness.get("stale"):
        warnings.append({
            "warning": "stale_codex_catalog",
            "reason": freshness.get("reason") or "stale",
            "remediation": freshness.get("remediation"),
        })

    return {
        "ok": True,
        "schema": CODEX_CLIENT_CONFIG_SCHEMA,
        "model_provider": CODEX_MODEL_PROVIDER,
        "provider": {
            "name": CODEX_PROVIDER_NAME,
            "base_url": base_url,
            "wire_api": CODEX_WIRE_API,
            "env_key": CODEX_ENV_KEY,
        },
        "model_catalog": model_catalog,
        "warnings": warnings,
    }


def codex_model_catalog_content() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (catalog_dict, None) on success or (None, error_code) on failure.

    Pure — no file writes, no refresh calls, no routing changes.
    """
    path = _catalog_path()
    try:
        data = path.read_bytes()
        catalog = json.loads(data)
        if not isinstance(catalog, dict):
            return None, "invalid_codex_catalog"
        return catalog, None
    except FileNotFoundError:
        return None, "missing_codex_catalog"
    except json.JSONDecodeError:
        return None, "invalid_codex_catalog"
    except Exception:
        return None, "invalid_codex_catalog"
