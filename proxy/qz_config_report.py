#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .qz_runtime_io import capture_policy
except ImportError:
    from qz_runtime_io import capture_policy


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


def _file_meta(path: Path) -> Dict[str, Any]:
    """Return bounded file metadata for a path that exists as a file.

    Safe failure: returns an empty dict rather than raising when stat/hash fails.
    sha256_12 is computed only for files at most 64 KiB to avoid hashing large
    binaries or model files.
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
    state = _path_state(path)
    record: Dict[str, Any] = {
        "name": name,
        "path": str(path),
        "state": state,
        "source_layer": source_layer,
        "classification": classification,
        "env_var": env_var,
        "env_value": env_value if isinstance(env_value, str) else "",
        "default": default,
        "active": bool(active),
        "note": note,
    }
    if state == "file":
        record.update(_file_meta(path))
    return record


def _setting_record(
    name: str,
    *,
    active_value: Any,
    default: Any,
    source_layer: str,
    classification: str,
    env_var: str = "",
    env_value: str = "",
    note: str = "",
) -> Dict[str, Any]:
    return {
        "name": name,
        "active_value": active_value,
        "default": default,
        "source_layer": source_layer,
        "classification": classification,
        "env_var": env_var,
        "env_value": env_value,
        "active": True,
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
        "system_file",              # qz.profiles.v1
        "codex_base_instructions_file",
        "base_instructions_file",
        "prompt_files",
        "prepend_prompt_files",
        "append_prompt_files",
        "prompt_prepend_files",
        "prompt_append_files",
        "prompt_replace_files",
        "append_files",             # qz.profiles.v1
        "prepend_files",            # qz.profiles.v1
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


def _prompt_file_records(
    root: Path,
    paths: List[Path],
    source_layers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Collect all prompt file references from the given manifest paths.

    source_layers maps str(manifest_path) -> source layer label (e.g. "default", "user").
    Paths not found in source_layers get source_layer "unknown".
    """
    if source_layers is None:
        source_layers = {}

    # First pass: collect all (field, prompt_path) references per manifest,
    # accumulating referenced_by per unique prompt path.
    seen_refs: Dict[str, list] = {}  # prompt_path_str -> [{field, source, source_layer}]
    for manifest_path in paths:
        manifest = _load_json(manifest_path)
        if not manifest:
            continue
        layer = source_layers.get(str(manifest_path), "unknown")
        for field, raw_path in _iter_prompt_refs(manifest):
            path = _resolve_repo_path(root, raw_path)
            if path is None:
                continue
            key = str(path)
            if key not in seen_refs:
                seen_refs[key] = []
            seen_refs[key].append({
                "field": field,
                "source": str(manifest_path),
                "source_layer": layer,
            })

    records = []
    referenced = []
    summary: Dict[str, Any] = {
        "schema": "qz.prompt.files.v1",
        "referenced": referenced,
        "loaded": [],
        "missing": [],
        "failed": [],
    }

    for key, refs in seen_refs.items():
        path = Path(key)
        source_layer = refs[0]["source_layer"] if refs else "unknown"
        record = _record(
            f"prompt_file:{refs[0]['field']}",
            path,
            source_layer=source_layer,
            classification="tracked_or_user_prompt",
            active=True,
            note=f"referenced by {refs[0]['source']}",
        )
        if record["state"] == "file":
            try:
                path.read_text(encoding="utf-8")
                summary["loaded"].append(key)
            except Exception as exc:
                record["state"] = "failed"
                record["note"] = f"{record['note']}; read failed: {exc}"
                summary["failed"].append({"path": key, "error": str(exc)})
        elif record["state"] == "missing":
            summary["missing"].append(key)
        else:
            summary["failed"].append({"path": key, "error": f"not a readable file: {record['state']}"})
        records.append(record)

        # Build rich referenced entry for summary — propagate file metadata
        # already computed inside _record() rather than re-reading the file.
        entry: Dict[str, Any] = {
            "path": key,
            "state": record["state"],
            "source_layer": source_layer,
            "classification": "prompt_file",
            "referenced_by": refs,
        }
        for meta_key in ("mtime_ms", "size_bytes", "sha256_12", "hash_skipped"):
            if meta_key in record:
                entry[meta_key] = record[meta_key]
        referenced.append(entry)

    return records, summary


def _memory_domain_report(config_paths: List[Path]) -> Dict[str, Any]:
    profiles: Dict[str, Any] = {}

    def _extract(data: dict) -> None:
        if data.get("schema") == "qz.profiles.v1" or "profiles" in data:
            for slug, bundle in (data.get("profiles") or {}).items():
                if not isinstance(bundle, dict):
                    continue
                memory = bundle.get("memory") or {}
                domain = (memory.get("domain") if isinstance(memory, dict) else None) or bundle.get("memory_domain")
                if isinstance(domain, str) and domain.strip():
                    backend = bundle.get("backend") or {}
                    gguf = backend.get("gguf") or f"{slug}.gguf"
                    profiles[gguf] = domain.strip()
        else:
            for key, overrides in (data.get("models") or {}).items():
                if not isinstance(overrides, dict):
                    continue
                raw = overrides.get("memory_domain")
                if isinstance(raw, str) and raw.strip():
                    profiles[key] = raw.strip()

    for path in config_paths:
        if not isinstance(path, Path):
            path = Path(path)
        if path.is_dir():
            for child in sorted(path.glob("*.json")):
                _extract(_load_json(child))
        elif path.is_file():
            _extract(_load_json(path))

    return {
        "schema": "qz.memory.domains.v1",
        "profiles": profiles,
        "note": (
            "memory_domain is explicit config only. "
            "Profiles not listed here resolve to isolated (no cross-session or workspace memory)."
        ),
    }


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
    model_overrides = Path(os.environ.get("QZ_MODEL_OVERRIDES", str(root / "config" / "user" / "model-overrides.json"))).expanduser()
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

    capture_env_present = "QZ_CAPTURE_MODE" in os.environ
    capture_env_value = os.environ.get("QZ_CAPTURE_MODE", "")
    capture = capture_policy().as_dict()
    capture["env_var"] = "QZ_CAPTURE_MODE"
    capture["env_value"] = capture_env_value
    capture["default"] = "off"
    capture["source_layer"] = "environment" if capture_env_present else "default"
    capture["classification"] = "debug_capture_policy"
    capture["state"] = "enabled" if capture["enabled"] else "disabled"
    capture["note"] = (
        "captures disabled; existing latest/request-scoped capture files may be stale"
        if not capture["enabled"]
        else f"captures enabled in {capture['mode']} mode"
    )

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
        _record("model_overrides_user", model_overrides, source_layer="user_override", classification="local_config", env_var="QZ_MODEL_OVERRIDES", default=str(root / "config" / "user" / "model-overrides.json")),
        _record("model_overrides_example", example_overrides, source_layer="tracked_example", classification="example_config", active=os.environ.get("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", "").strip().lower() in {"1", "true", "yes", "on"}),
        _record("profiles_default", root / "config" / "default" / "profiles.json", source_layer="tracked_default", classification="source_config"),
        _record("profiles_default_dir", root / "config" / "default" / "profiles", source_layer="tracked_default", classification="source_config"),
        _record("profiles_example", root / "config" / "example" / "profiles.json", source_layer="tracked_example", classification="example_config", active=os.environ.get("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", "").strip().lower() in {"1", "true", "yes", "on"}),
        _record("profiles_user", root / "config" / "user" / "profiles.json", source_layer="user_override", classification="local_config"),
        _record("profiles_user_dir", root / "config" / "user" / "profiles", source_layer="user_override", classification="local_config"),
        _record("model_inventory_cache", inventory, source_layer="generated", classification="generated_inventory", env_var="QZ_MODEL_INVENTORY_CACHE", default=str(var_dir / "model-inventory.json")),
        _record("codex_config_template", root / "config" / "example" / "codex-config.toml", source_layer="tracked_example", classification="example_codex_config"),
        _record("codex_catalog_example", root / "config" / "example" / "qwenzhai-models.json", source_layer="tracked_example", classification="example_codex_catalog"),
        _record("benchmark_prompts_default", root / "config" / "default" / "benchmark-prompts.json", source_layer="tracked_default", classification="benchmark_fixture"),
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
    settings = [
        _setting_record(
            "capture_mode",
            active_value=capture["mode"],
            default="off",
            source_layer=capture["source_layer"],
            classification="debug_capture_policy",
            env_var="QZ_CAPTURE_MODE",
            env_value=capture_env_value,
            note=capture["note"],
        )
    ]
    _prompt_source_layers = {
        str(default_overrides): "default",
        str(model_overrides): "user",
        str(root / "config" / "default" / "profiles.json"): "default",
        str(root / "config" / "user" / "profiles.json"): "user",
    }
    prompt_records, prompt_file_summary = _prompt_file_records(root, [
        default_overrides,
        model_overrides,
        root / "config" / "default" / "profiles.json",
        root / "config" / "user" / "profiles.json",
    ], source_layers=_prompt_source_layers)
    records.extend(prompt_records)
    memory_domain_report = _memory_domain_report([
        default_overrides,
        model_overrides,
        root / "config" / "default" / "profiles.json",
        root / "config" / "default" / "profiles",
        root / "config" / "user" / "profiles.json",
        root / "config" / "user" / "profiles",
    ])

    warnings = []
    if not capture["enabled"]:
        warnings.append({
            "env_var": "QZ_CAPTURE_MODE",
            "active_value": capture["mode"],
            "warning": "captures are disabled; latest/request-scoped capture files may be stale",
        })
    if "docs" in searxng_policy.parts:
        warnings.append({
            "path": str(searxng_policy),
            "warning": "active search policy is under docs; move to config/default with compatibility path",
        })
    records_by_name = {r["name"]: r for r in records}
    for record in records:
        if record["active"] and record["state"] == "missing" and record["name"] in {
            "model_dir",
            "searxng_policy",
        }:
            warnings.append({"path": record["path"], "warning": f"{record['name']} is active but missing"})

    # User model overrides explicitly configured via env but file is missing.
    if os.environ.get("QZ_MODEL_OVERRIDES") and records_by_name.get("model_overrides_user", {}).get("state") == "missing":
        warnings.append({
            "name": "model_overrides_user",
            "path": str(model_overrides),
            "warning": "missing_user_model_overrides",
        })

    # Prompt files referenced by config but not found on disk.
    _refs_by_path = {e["path"]: e.get("referenced_by", []) for e in prompt_file_summary.get("referenced", [])}
    for missing_path in prompt_file_summary.get("missing", []):
        w: Dict[str, Any] = {"path": missing_path, "warning": "missing_prompt_file"}
        refs = _refs_by_path.get(missing_path)
        if refs:
            w["referenced_by"] = refs
        warnings.append(w)

    # Generated Codex catalog missing — Codex cannot connect without it.
    if records_by_name.get("codex_model_catalog", {}).get("state") == "missing":
        warnings.append({
            "name": "codex_model_catalog",
            "path": records_by_name["codex_model_catalog"]["path"],
            "warning": "missing_codex_catalog",
            "note": "run /qz/models/refresh to regenerate",
        })

    # Generated Codex config missing — Codex cannot connect without it.
    if records_by_name.get("codex_config", {}).get("state") == "missing":
        warnings.append({
            "name": "codex_config",
            "path": records_by_name["codex_config"]["path"],
            "warning": "missing_codex_config",
            "note": "run /qz/models/refresh to regenerate",
        })

    proxy_initialization = None
    if handler is not None:
        payload_fn = getattr(handler, "_initialization_payload", None)
        if callable(payload_fn):
            try:
                proxy_initialization = payload_fn()
            except Exception:
                proxy_initialization = {
                    "schema": "qz.proxy.initialization.v1",
                    "state": "unknown",
                    "ready": False,
                }

    return {
        "schema": EFFECTIVE_CONFIG_SCHEMA,
        "root": str(root),
        "var_dir": str(var_dir),
        "paths": records,
        "settings": settings,
        "capture": capture,
        "prompt_files": prompt_file_summary,
        "memory_domains": memory_domain_report,
        "warnings": warnings,
        "proxy_initialization": proxy_initialization,
    }
