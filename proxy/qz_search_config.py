#!/usr/bin/env python3
"""Load and merge QuantZhai search configuration (qz.search.v1).

Precedence (highest to lowest):
  1. QZ_SEARCH_CONFIG_PATH env var  — explicit path override
  2. config/user/search.json        — local user overrides (never committed)
  3. config/default/search.json     — tracked default
  4. SEARXNG_POLICY env var         — legacy fallback (search-policy.json format)

Within the merged config:
  - SEARXNG_BASE_URL overrides searxng.base_url
  - SEARXNG_TIMEOUT overrides searxng.timeout_s
  - SEARXNG_CAPABILITIES remains available as a separate path record

IMPORTANT: searxng.base_url is never exposed in the effective/public summary.
Only a boolean (searxng_base_url_set) is surfaced externally.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


SEARCH_CONFIG_SCHEMA = "qz.search.v1"

QZ_SEARCH_CONFIG_PATH_ENV = "QZ_SEARCH_CONFIG_PATH"


def _qz_root(env: Optional[Dict[str, str]] = None) -> Path:
    source = os.environ if env is None else env
    raw = source.get("QZ_ROOT")
    if raw and raw.strip():
        return Path(raw.strip()).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _load_json_file(path: Path) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (data, None) on success or (None, error_msg) on failure."""
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return None, f"expected JSON object, got {type(data).__name__}"
        return data, None
    except FileNotFoundError:
        return None, None  # absent is not an error
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge overlay into base. overlay wins on scalar conflicts."""
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class SearchConfigResult:
    """Result of loading and merging search configuration.

    config     — merged config dict (qz.search.v1 shape)
    source     — which source won ("explicit", "user", "default", "legacy", "empty")
    path       — path of the winning file, or None
    legacy_policy_path — path to legacy search-policy.json if compat mode active, or None
    warnings   — list of warning strings (missing files, parse errors, etc.)
    """
    config: Dict[str, Any]
    source: str
    path: Optional[Path]
    legacy_policy_path: Optional[Path]
    warnings: List[str]

    def effective_summary(self) -> Dict[str, Any]:
        """Return a bounded summary suitable for /qz/config/effective.

        NEVER includes the actual base_url — only a boolean indicator.
        """
        cfg = self.config
        searxng = cfg.get("searxng") or {}
        base_url = searxng.get("base_url") or ""
        defaults = cfg.get("defaults") or {}
        profiles = cfg.get("profiles") or {}
        compat = cfg.get("compatibility") or {}
        return {
            "schema": "qz.search.effective.v1",
            "source": self.source,
            "path": str(self.path) if self.path else None,
            "searxng_base_url_set": bool(base_url.strip()),
            "searxng_enabled": bool(searxng.get("enabled", True)),
            "default_profile": defaults.get("profile", "auto"),
            "profile_names": sorted(profiles.keys()),
            "legacy_policy_path": str(self.legacy_policy_path) if self.legacy_policy_path else None,
            "warnings": list(self.warnings),
        }


_DEFAULT_CONFIG: Dict[str, Any] = {
    "schema": SEARCH_CONFIG_SCHEMA,
    "searxng": {
        "base_url": "",
        "timeout_s": 15,
        "enabled": True,
    },
    "defaults": {
        "profile": "auto",
        "language": "en",
        "safe_search": 0,
    },
    "profiles": {},
    "routing": {},
    "disabled_engines": [],
    "quarantined_engines": [],
    "non_text_engines_disabled": True,
    "compatibility": {
        "legacy_policy_path": "",
    },
}


def load_search_config(
    env: Optional[Dict[str, str]] = None,
    root: Optional[Path] = None,
) -> SearchConfigResult:
    """Load, merge, and return the active search configuration.

    Parameters
    ----------
    env   — environment dict (defaults to os.environ)
    root  — repo root (defaults to auto-detected from __file__)

    Returns a SearchConfigResult with merged config, source, path, and warnings.
    Never raises; errors become warnings and fall through to a safe default.
    """
    source_env = os.environ if env is None else env
    if root is None:
        root = _qz_root(env)

    warnings: List[str] = []
    merged = dict(_DEFAULT_CONFIG)
    source = "empty"
    winning_path: Optional[Path] = None

    # --- Resolve candidate paths ----------------------------------------
    explicit_raw = source_env.get(QZ_SEARCH_CONFIG_PATH_ENV)
    explicit_path = Path(explicit_raw.strip()).expanduser() if (explicit_raw and explicit_raw.strip()) else None

    user_path = root / "config" / "user" / "search.json"
    default_path = root / "config" / "default" / "search.json"

    # --- Load base: config/default/search.json --------------------------
    if default_path.is_file():
        data, err = _load_json_file(default_path)
        if err:
            warnings.append(f"config/default/search.json parse error: {err}")
        elif data is not None:
            merged = _deep_merge(merged, data)
            source = "default"
            winning_path = default_path

    # --- Overlay: config/user/search.json (unless explicit override) -----
    if explicit_path is None and user_path.is_file():
        data, err = _load_json_file(user_path)
        if err:
            warnings.append(f"config/user/search.json parse error: {err}")
        elif data is not None:
            merged = _deep_merge(merged, data)
            source = "user"
            winning_path = user_path

    # --- Override: QZ_SEARCH_CONFIG_PATH --------------------------------
    if explicit_path is not None:
        if explicit_path.is_file():
            data, err = _load_json_file(explicit_path)
            if err:
                warnings.append(f"QZ_SEARCH_CONFIG_PATH parse error: {err}")
            elif data is not None:
                merged = _deep_merge(merged, data)
                source = "explicit"
                winning_path = explicit_path
        else:
            warnings.append(f"QZ_SEARCH_CONFIG_PATH not found: {explicit_path}")

    # --- Legacy fallback: SEARXNG_POLICY if no search.json loaded -------
    legacy_policy_path: Optional[Path] = None
    if source == "empty":
        policy_env = source_env.get("SEARXNG_POLICY")
        if policy_env and policy_env.strip():
            legacy_candidate = Path(policy_env.strip()).expanduser()
            if legacy_candidate.is_file():
                legacy_policy_path = legacy_candidate
                source = "legacy"
            else:
                warnings.append(f"SEARXNG_POLICY not found: {legacy_candidate}")
        else:
            # Try the repo default search-policy.json as the final fallback
            legacy_candidate = root / "config" / "default" / "search-policy.json"
            if legacy_candidate.is_file():
                legacy_policy_path = legacy_candidate
                source = "legacy"

        if legacy_policy_path is not None:
            warnings.append(
                "Active search config is legacy search-policy.json. "
                "Create config/default/search.json or config/user/search.json to migrate."
            )

    # Carry forward legacy_policy_path from compatibility key if set
    if legacy_policy_path is None:
        compat_raw = (merged.get("compatibility") or {}).get("legacy_policy_path") or ""
        if compat_raw.strip():
            candidate = Path(compat_raw.strip())
            if not candidate.is_absolute():
                candidate = root / candidate
            if candidate.is_file():
                legacy_policy_path = candidate

    # --- Env overrides --------------------------------------------------
    base_url_env = source_env.get("SEARXNG_BASE_URL")
    if base_url_env is not None:
        searxng = dict(merged.get("searxng") or {})
        searxng["base_url"] = base_url_env
        merged["searxng"] = searxng

    timeout_env = source_env.get("SEARXNG_TIMEOUT")
    if timeout_env is not None:
        try:
            timeout_val = float(timeout_env)
            searxng = dict(merged.get("searxng") or {})
            searxng["timeout_s"] = timeout_val
            merged["searxng"] = searxng
        except (ValueError, TypeError):
            warnings.append(f"SEARXNG_TIMEOUT is not a valid number: {timeout_env!r}")

    # --- Warn if SearXNG base URL is unset and search is enabled ---------
    searxng_cfg = merged.get("searxng") or {}
    if searxng_cfg.get("enabled", True) and not (searxng_cfg.get("base_url") or "").strip():
        warnings.append(
            "No SearXNG base URL configured. "
            "Set searxng.base_url in config/user/search.json or SEARXNG_BASE_URL env."
        )

    return SearchConfigResult(
        config=merged,
        source=source,
        path=winning_path,
        legacy_policy_path=legacy_policy_path,
        warnings=warnings,
    )
