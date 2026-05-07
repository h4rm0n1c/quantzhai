#!/usr/bin/env python3
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


EFFECTIVE_CONFIG_SCHEMA = "qz.config.effective.v1"


def _root_dir() -> Path:
    raw = os.environ.get("QZ_ROOT")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _var_dir(root: Path) -> Path:
    raw = os.environ.get("QZ_VAR_DIR")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return root / "var"


def _path_state(path: Path) -> str:
    try:
        if path.is_symlink():
            return "symlink"
        if path.is_dir():
            return "dir"
        if path.is_file():
            return "file"
    except Exception:
        return "unknown"
    return "missing"


def _record(
    name: str,
    path: Path,
    *,
    source_layer: str,
    classification: str,
    env_var: str = "",
    default: str = "",
    active: bool = True,
    note: str = "",
) -> Dict[str, Any]:
    env_value = os.environ.get(env_var) if env_var else None
    return {
        "name": name,
        "path": str(path),
        "state": _path_state(path),
        "source_layer": source_layer,
        "classification": classification,
        "env_var": env_var,
        "env_value": env_value if isinstance(env_value, str) else "",
        "default": default,
        "active": bool(active),
        "note": note,
    }


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_repo_path(root: Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if path.is_absolute():
        return path
    return root / path


def _iter_prompt_refs(manifest: Dict[str, Any]):
    keys = {
        "system_prompt_file",
        "codex_base_instructions_file",
        "base_instructions_file",
        "prompt_files",
        "prepend_prompt_files",
        "append_prompt_files",
    }
    stack = [manifest]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys:
                    if isinstance(item, str):
                        yield key, item
                    elif isinstance(item, list):
                        for member in item:
                            if isinstance(member, str):
                                yield key, member
                if isinstance(item, (dict, list)):
                    stack.append(item)
        elif isinstance(value, list):
            stack.extend(item for item in value if isinstance(item, (dict, list)))


def _prompt_file_records(root: Path, paths: List[Path]) -> List[Dict[str, Any]]:
    seen = set()
    records = []
    for manifest_path in paths:
        manifest = _load_json(manifest_path)
        if not manifest:
            continue
        for field, raw_path in _iter_prompt_refs(manifest):
            path = _resolve_repo_path(root, raw_path)
            if path is None:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            records.append(_record(
                f"prompt_file:{field}",
                path,
                source_layer="prompt_override",
                classification="tracked_or_user_prompt",
                active=True,
                note=f"referenced by {manifest_path}",
            ))
    return records


def _default_search_policy_path(root: Path, script_dir: Path) -> Path:
    for path in (
        root / "config" / "default" / "search-policy.json",
        root / "docs" / "searxng-agent-policy-profiled.json",
        script_dir / "searxng-agent-policy.json",
    ):
        if path.is_file():
            return path
    return root / "config" / "default" / "search-policy.json"


def _first_existing_path(*paths: Path) -> Path:
    for path in paths:
        if path.is_file():
            return path
    return paths[0]


def effective_config_payload(handler=None) -> Dict[str, Any]:
    root = _root_dir()
    var_dir = _var_dir(root)
    script_dir = Path(__file__).resolve().parent

    model_dir = Path(os.environ.get("QZ_MODEL_DIR", str(var_dir / "models"))).expanduser()
    model_overrides = Path(os.environ.get("QZ_MODEL_OVERRIDES", str(var_dir / "model-overrides.json"))).expanduser()
    inventory = Path(os.environ.get("QZ_MODEL_INVENTORY_CACHE", str(var_dir / "model-inventory.json"))).expanduser()
    model_state = Path(os.environ.get("QZ_MODEL_STATE_PATH", str(var_dir / "model-state.json"))).expanduser()
    backend_state = Path(os.environ.get("QZ_BACKEND_STATE_PATH", str(var_dir / "backend-state.json"))).expanduser()
    runtime_state = Path(os.environ.get("QZ_RUNTIME_STATE_PATH", str(var_dir / "run" / "qz-runtime-state.json"))).expanduser()

    policy_path = getattr(handler, "searxng_policy_path", None) if handler is not None else None
    capabilities_path = getattr(handler, "searxng_capabilities_path", None) if handler is not None else None
    if policy_path:
        searxng_policy = Path(policy_path).expanduser()
    else:
        searxng_policy = Path(os.environ.get("SEARXNG_POLICY") or _default_search_policy_path(root, script_dir)).expanduser()
    if capabilities_path:
        searxng_capabilities = Path(capabilities_path).expanduser()
    else:
        searxng_capabilities = Path(os.environ.get("SEARXNG_CAPABILITIES") or script_dir / "searxng-capabilities.json").expanduser()

    default_overrides = _first_existing_path(
        root / "config" / "default" / "model-overrides.json",
        root / "config" / "qz-model-overrides.default.json",
    )
    example_overrides = _first_existing_path(
        root / "config" / "example" / "model-overrides.json",
        root / "config" / "qz-model-overrides.example.json",
    )

    records = [
        _record("root", root, source_layer="environment", classification="repo_root", env_var="QZ_ROOT"),
        _record("var_dir", var_dir, source_layer="environment", classification="runtime_root", env_var="QZ_VAR_DIR"),
        _record("model_dir", model_dir, source_layer="user_runtime_input", classification="local_models", env_var="QZ_MODEL_DIR", default=str(var_dir / "models")),
        _record("model_overrides_default", default_overrides, source_layer="tracked_default", classification="source_config", active=True),
        _record("model_overrides_user", model_overrides, source_layer="user_override", classification="local_config", env_var="QZ_MODEL_OVERRIDES", default=str(var_dir / "model-overrides.json")),
        _record("model_overrides_example", example_overrides, source_layer="tracked_example", classification="example_config", active=os.environ.get("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", "").strip().lower() in {"1", "true", "yes", "on"}),
        _record("model_inventory_cache", inventory, source_layer="generated", classification="generated_inventory", env_var="QZ_MODEL_INVENTORY_CACHE", default=str(var_dir / "model-inventory.json")),
        _record("codex_config", var_dir / "codex-home" / "config.toml", source_layer="generated", classification="generated_codex_config"),
        _record("codex_model_catalog", var_dir / "codex-home" / "model-catalogs" / "qwenzhai-models.json", source_layer="generated", classification="generated_codex_catalog"),
        _record("model_state", model_state, source_layer="runtime_state", classification="state_fallback", env_var="QZ_MODEL_STATE_PATH", default=str(var_dir / "model-state.json")),
        _record("backend_state", backend_state, source_layer="runtime_state", classification="state_fallback", env_var="QZ_BACKEND_STATE_PATH", default=str(var_dir / "backend-state.json")),
        _record("runtime_state_snapshot", runtime_state, source_layer="runtime_state", classification="startup_snapshot", env_var="QZ_RUNTIME_STATE_PATH", default=str(var_dir / "run" / "qz-runtime-state.json")),
        _record("capture_dir", var_dir / "captures", source_layer="debug_replay", classification="captures"),
        _record("log_dir", var_dir / "logs", source_layer="debug", classification="logs"),
        _record("benchmark_summary", var_dir / "benchmarks" / "latest-summary.json", source_layer="debug_replay", classification="benchmark_cache"),
        _record("searxng_policy", searxng_policy, source_layer="tracked_or_env_config", classification="active_search_policy", env_var="SEARXNG_POLICY"),
        _record("searxng_capabilities", searxng_capabilities, source_layer="tracked_or_env_config", classification="active_search_capabilities", env_var="SEARXNG_CAPABILITIES"),
    ]
    records.extend(_prompt_file_records(root, [default_overrides, model_overrides]))

    warnings = []
    if "docs" in searxng_policy.parts:
        warnings.append({
            "path": str(searxng_policy),
            "warning": "active search policy is under docs; move to config/default with compatibility path",
        })
    for record in records:
        if record["active"] and record["state"] == "missing" and record["name"] in {
            "model_dir",
            "searxng_policy",
        }:
            warnings.append({"path": record["path"], "warning": f"{record['name']} is active but missing"})

    return {
        "schema": EFFECTIVE_CONFIG_SCHEMA,
        "root": str(root),
        "var_dir": str(var_dir),
        "paths": records,
        "warnings": warnings,
    }
