#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .qz_paths import model_inventory_path as _model_inventory_path
    from .qz_reasoning_policy import supported_reasoning_levels
except ImportError:
    from qz_paths import model_inventory_path as _model_inventory_path
    from qz_reasoning_policy import supported_reasoning_levels


GGUF_VALUE_TYPES = {
    0: ("uint8", "B"),
    1: ("int8", "b"),
    2: ("uint16", "H"),
    3: ("int16", "h"),
    4: ("uint32", "I"),
    5: ("int32", "i"),
    6: ("float32", "f"),
    7: ("bool", "?"),
    8: ("string", None),
    9: ("array", None),
    10: ("uint64", "Q"),
    11: ("int64", "q"),
    12: ("float64", "d"),
}

def load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_last_selected_model(root: Path) -> str:
    state_path = Path(os.environ.get("QZ_MODEL_STATE_PATH", str(root / "var" / "model-state.json"))).expanduser()
    try:
        state = load_json(state_path)
    except Exception:
        return ""
    if not isinstance(state, dict):
        return ""
    for key in ("selected_key", "selected_backend_id", "loaded_model"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def entry_identity(entry: Dict[str, Any]) -> str:
    for field in ("slug", "key", "filename", "stem", "backend_id"):
        value = entry.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def read_u32(handle) -> int:
    data = handle.read(4)
    if len(data) != 4:
        raise ValueError("unexpected end of file")
    return struct.unpack("<I", data)[0]


def read_u64(handle) -> int:
    data = handle.read(8)
    if len(data) != 8:
        raise ValueError("unexpected end of file")
    return struct.unpack("<Q", data)[0]


def read_string(handle) -> str:
    length = read_u64(handle)
    data = handle.read(length)
    if len(data) != length:
        raise ValueError("unexpected end of file")
    return data.decode("utf-8", errors="replace")


def read_scalar(handle, type_id: int) -> Any:
    info = GGUF_VALUE_TYPES.get(type_id)
    if info is None:
        raise ValueError(f"unsupported GGUF value type: {type_id}")
    name, fmt = info
    if name == "string":
        return read_string(handle)
    if name == "array":
        raise ValueError("array must be handled separately")
    size = struct.calcsize("<" + fmt)
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("unexpected end of file")
    value = struct.unpack("<" + fmt, data)[0]
    if name == "bool":
        return bool(value)
    return value


def read_value(handle, type_id: int) -> Any:
    if type_id == 9:
        subtype = read_u32(handle)
        count = read_u64(handle)
        values: List[Any] = []
        for _ in range(count):
            values.append(read_value(handle, subtype))
        return values
    return read_scalar(handle, type_id)


def read_gguf_metadata(path: Path) -> Tuple[int, Dict[str, Any]]:
    with path.open("rb") as handle:
        magic = handle.read(4)
        if magic != b"GGUF":
            raise ValueError("not a GGUF file")

        version = read_u32(handle)
        if version not in (2, 3):
            raise ValueError(f"unsupported GGUF version: {version}")

        tensor_count = read_u64(handle)
        metadata_count = read_u64(handle)

        metadata: Dict[str, Any] = {}
        for _ in range(metadata_count):
            key = read_string(handle)
            type_id = read_u32(handle)
            value = read_value(handle, type_id)
            if isinstance(value, list):
                if len(value) <= 32:
                    metadata[key] = value
            else:
                metadata[key] = value

        return tensor_count, metadata


def infer_context_length(metadata: Dict[str, Any]) -> Optional[int]:
    for key, value in metadata.items():
        if not isinstance(value, (int, float)):
            continue
        if key.endswith(".context_length") or key in {
            "context_length",
            "llama.context_length",
            "n_ctx_train",
            "max_position_embeddings",
        }:
            return int(value)
    return None


def infer_architecture(metadata: Dict[str, Any], stem: str) -> str:
    value = metadata.get("general.architecture")
    if isinstance(value, str) and value:
        return value
    return stem.split("-")[0]


def infer_model_name(metadata: Dict[str, Any], stem: str) -> str:
    value = metadata.get("general.name")
    if isinstance(value, str) and value:
        return value
    return stem


def infer_reasoning_level(entry: Dict[str, Any]) -> str:
    overrides = entry.get("overrides")
    if isinstance(overrides, dict):
        for key in ("default_reasoning_level", "reasoning_level", "reasoning_effort"):
            value = overrides.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()

    text = " ".join(
        str(value).lower()
        for value in (
            entry.get("label"),
            entry.get("name"),
            entry.get("notes"),
            entry.get("stem"),
            entry.get("backend_id"),
        )
        if value
    )
    if "apex" in text or "reasoning" in text:
        return "high"
    if "iq4" in text or "aggressive" in text or "fast" in text:
        return "low"
    return "medium"


def keep_metadata_key(key: str, architecture: str) -> bool:
    if key == "tokenizer.chat_template":
        return False
    if key.startswith("general."):
        return True
    if key.startswith("tokenizer.ggml."):
        return True
    if architecture and key.startswith(f"{architecture}."):
        return True
    if key in {
        "general.quantization_version",
        "general.file_type",
        "llama.context_length",
        "llama.block_count",
        "llama.embedding_length",
        "llama.attention.head_count",
        "llama.attention.head_count_kv",
        "llama.expert_count",
        "llama.expert_used_count",
        "llama.attention.key_length",
        "llama.attention.value_length",
        "llama.rope.freq_base",
        "llama.full_attention_interval",
    }:
        return True
    return False


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _first_existing_path(*paths: Path) -> Path:
    for path in paths:
        if path.is_file():
            return path
    return paths[0]


def _profiles_v1_to_manifest(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a qz.profiles.v1 document to internal manifest format."""
    manifest: Dict[str, Any] = {"default_key": None, "models": {}}

    defaults = data.get("defaults") or {}
    shared_harnesses = data.get("shared_harnesses") or {}

    sf = defaults.get("system_prompt_file")
    if sf:
        manifest["system_prompt_file"] = sf
    if shared_harnesses:
        manifest["turn_harness_definitions"] = dict(shared_harnesses)
    for src in (data, defaults):
        pp = src.get("prompt_policy")
        if isinstance(pp, dict):
            manifest["prompt_policy"] = pp
            break

    for slug, bundle in (data.get("profiles") or {}).items():
        if not isinstance(bundle, dict):
            continue
        backend = bundle.get("backend") or {}
        gguf = backend.get("gguf") or f"{slug}.gguf"
        runtime = bundle.get("runtime") or {}
        prompts = bundle.get("prompts") or {}
        behavior = bundle.get("behavior") or {}
        memory_conf = bundle.get("memory") or {}
        meta = bundle.get("metadata") or {}

        overrides: Dict[str, Any] = {}

        label = meta.get("label")
        if label:
            overrides["label"] = label
        if meta.get("notes"):
            overrides["notes"] = meta["notes"]
        if meta.get("default"):
            overrides["default"] = True
            manifest["default_key"] = gguf
        if meta.get("priority") is not None:
            overrides["priority"] = meta["priority"]
        if meta.get("aliases"):
            overrides["aliases"] = meta["aliases"]

        ctx = runtime.get("context_length")
        if ctx is not None:
            overrides["runtime_context_length"] = ctx
        rl = runtime.get("default_reasoning_level")
        if rl:
            overrides["default_reasoning_level"] = rl
        acro = runtime.get("allow_client_reasoning_override")
        if acro is not None:
            overrides["allow_client_reasoning_override"] = acro
        fdrl = runtime.get("force_default_reasoning_level")
        if fdrl is not None:
            overrides["force_default_reasoning_level"] = fdrl
        la = runtime.get("launch_args")
        if la:
            overrides["launch_args"] = la

        sys_file = prompts.get("system_file")
        if sys_file:
            overrides["system_prompt_file"] = sys_file
        aff = prompts.get("append_files")
        if aff:
            overrides["prompt_append_files"] = aff
        pff = prompts.get("prepend_files")
        if pff:
            overrides["prompt_prepend_files"] = pff
        th = prompts.get("turn_harnesses")
        if th:
            overrides["turn_harnesses"] = th
        if prompts.get("disable") or behavior.get("disable_system_prompt"):
            overrides["disable_system_prompt"] = True
        lh = prompts.get("local_harnesses")
        if isinstance(lh, dict) and lh:
            existing = manifest.setdefault("turn_harness_definitions", {})
            existing.update(lh)

        rsf = behavior.get("reasoning_stream_format")
        if rsf:
            overrides["reasoning_stream_format"] = rsf
        hrs = behavior.get("hide_reasoning_stream")
        if hrs is not None:
            overrides["hide_reasoning_stream"] = hrs

        domain = memory_conf.get("domain") if isinstance(memory_conf, dict) else None
        flat_domain = bundle.get("memory_domain")
        final_domain = domain or flat_domain
        if isinstance(final_domain, str) and final_domain.strip():
            overrides["memory_domain"] = final_domain.strip()

        for k in ("system_prompt", "prompt_append", "codex_model_messages"):
            if k in bundle:
                overrides[k] = bundle[k]

        # When the profile slug differs from the GGUF stem, add the slug (and
        # slug+".gguf") as aliases so build_entry can merge them into the
        # scanned entry's alias set, making match_model("alice") work even
        # when the physical file is named "some-model.gguf".
        gguf_stem = Path(gguf).stem
        if slug != gguf_stem:
            existing_aliases = list(overrides.get("aliases") or [])
            for alias_val in [slug, f"{slug}.gguf"]:
                if alias_val not in existing_aliases:
                    existing_aliases.append(alias_val)
            overrides["aliases"] = existing_aliases

        manifest["models"][gguf] = overrides

    return manifest


def _load_profiles_layer(layer_dir: Path) -> Optional[Tuple[Dict[str, Any], List[str]]]:
    """
    Load profiles.json + profiles/*.json from one config layer directory.
    Returns (manifest_dict, warnings) or None when no profiles files exist.
    Profiles.json loads before profiles/*.json; duplicates within a layer warn.
    """
    profiles_json = layer_dir / "profiles.json"
    profiles_dir = layer_dir / "profiles"

    has_top = profiles_json.is_file()
    has_dir = profiles_dir.is_dir() and bool(list(profiles_dir.glob("*.json")))

    if not has_top and not has_dir:
        return None

    warnings: List[str] = []
    combined: Dict[str, Any] = {
        "schema": "qz.profiles.v1",
        "defaults": {},
        "shared_harnesses": {},
        "profiles": {},
    }
    seen_slugs: set = set()

    def _merge(data: dict, source: Path) -> None:
        combined["defaults"].update(data.get("defaults") or {})
        combined["shared_harnesses"].update(data.get("shared_harnesses") or {})
        for k in ("prompt_policy",):
            if k in data:
                combined[k] = data[k]
        for slug, bundle in (data.get("profiles") or {}).items():
            if slug in seen_slugs:
                raise ValueError(
                    f"duplicate profile slug '{slug}' in same config layer "
                    f"({source.name}); each slug must be unique within a layer"
                )
            seen_slugs.add(slug)
            combined["profiles"][slug] = bundle

    if has_top:
        data = load_json(profiles_json)
        if data:
            _merge(data, profiles_json)

    if has_dir:
        for path in sorted(profiles_dir.glob("*.json")):
            data = load_json(path)
            if data:
                _merge(data, path)

    return _profiles_v1_to_manifest(combined), warnings


def load_manifest(root: Path, overrides_path: Optional[Path] = None) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {"default_key": None, "models": {}}

    # Default layer: try profiles.v1 first, fall back to model-overrides.json
    default_layer = _load_profiles_layer(root / "config" / "default")
    if default_layer is not None:
        manifest = deep_merge(manifest, default_layer[0])
    else:
        default_path = _first_existing_path(
            root / "config" / "default" / "model-overrides.json",
            root / "config" / "qz-model-overrides.default.json",
        )
        loaded = load_json(default_path)
        if loaded:
            manifest = deep_merge(manifest, loaded)

    # User layer
    runtime_loaded = False
    if overrides_path is not None:
        loaded = load_json(overrides_path)
        if loaded:
            manifest = deep_merge(manifest, loaded)
            runtime_loaded = True
    else:
        env_path = os.environ.get("QZ_MODEL_OVERRIDES")
        if isinstance(env_path, str) and env_path.strip():
            loaded = load_json(Path(env_path).expanduser())
            if loaded:
                manifest = deep_merge(manifest, loaded)
                runtime_loaded = True
        else:
            user_layer = _load_profiles_layer(root / "config" / "user")
            if user_layer is not None:
                manifest = deep_merge(manifest, user_layer[0])
                runtime_loaded = True
            else:
                user_path = root / "config" / "user" / "model-overrides.json"
                loaded = load_json(user_path)
                if loaded:
                    manifest = deep_merge(manifest, loaded)
                    runtime_loaded = True

    # Example layer (only when env-enabled and no user layer)
    if not runtime_loaded and _truthy_env("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES"):
        example_layer = _load_profiles_layer(root / "config" / "example")
        if example_layer is not None:
            manifest = deep_merge(manifest, example_layer[0])
        else:
            base_path = _first_existing_path(
                root / "config" / "example" / "model-overrides.json",
                root / "config" / "qz-model-overrides.example.json",
            )
            loaded = load_json(base_path)
            if loaded:
                manifest = deep_merge(manifest, loaded)

    if not isinstance(manifest.get("models"), dict):
        manifest["models"] = {}
    return manifest


def model_overrides(manifest: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    models = manifest.get("models", {})
    keys = [
        entry["key"],
        entry["stem"],
        entry["filename"],
        entry["name"],
    ]
    for alias in entry.get("aliases", []):
        keys.append(alias)

    for key in keys:
        if key in models and isinstance(models[key], dict):
            return models[key]

    for model_key, model_value in models.items():
        if not isinstance(model_value, dict):
            continue
        aliases = model_value.get("aliases", [])
        if isinstance(aliases, list) and any(str(alias).lower() == entry["key"].lower() for alias in aliases):
            return model_value

    return {}


def override_context_length(overrides: Dict[str, Any]) -> Optional[int]:
    if not isinstance(overrides, dict):
        return None
    for key in ("runtime_context_length", "context_length"):
        value = overrides.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None


def build_entry(path: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    stat = path.stat()
    tensor_count, metadata = read_gguf_metadata(path)
    stem = path.stem
    filename = path.name
    resolved_path = path.resolve()
    profile_symlink = path.is_symlink()
    name = stem if profile_symlink else infer_model_name(metadata, stem)
    architecture = infer_architecture(metadata, stem)
    context_length = infer_context_length(metadata)
    filtered_metadata = {
        key: value
        for key, value in metadata.items()
        if keep_metadata_key(key, architecture)
    }
    base_aliases = {
        filename,
        stem,
        name,
        backend_id := stem,
        architecture,
        str(path),
    }
    if backend_id:
        base_aliases.add(backend_id)
    entry = {
        "key": filename,
        "filename": filename,
        "stem": stem,
        "backend_id": backend_id,
        "native_backend_id": backend_id,
        "path": str(resolved_path),
        "source_path": str(path),
        "profile_symlink": profile_symlink,
        "symlink_target_path": str(resolved_path) if profile_symlink else "",
        "size_bytes": stat.st_size,
        "mtime": int(stat.st_mtime),
        "tensor_count": tensor_count,
        "metadata": filtered_metadata,
        "architecture": architecture,
        "name": name,
        "context_length": context_length,
        "aliases": sorted(x for x in base_aliases if isinstance(x, str) and x),
    }

    overrides = model_overrides(manifest, entry)
    entry["overrides"] = overrides
    # Merge aliases declared in overrides (e.g. profile slug when slug ≠ GGUF stem)
    # into the entry alias set so match_model() can resolve by profile slug.
    ov_aliases = overrides.get("aliases")
    if isinstance(ov_aliases, list) and ov_aliases:
        entry["aliases"] = sorted(
            set(entry["aliases"]) | {str(a) for a in ov_aliases if isinstance(a, str) and a}
        )
    entry["label"] = overrides.get("label") or name or stem
    entry["default"] = bool(overrides.get("default"))
    entry["backend_target"] = entry.get("native_backend_id") or stem
    entry["profile_valid"] = True
    entry["profile_error"] = ""
    entry["runtime_context_length"] = override_context_length(overrides)
    launch_args = overrides.get("launch_args", [])
    entry["launch_args"] = list(launch_args) if isinstance(launch_args, list) else []
    entry["notes"] = overrides.get("notes")
    entry["priority"] = overrides.get("priority")
    entry["default_reasoning_level"] = infer_reasoning_level(entry)
    entry["supported_reasoning_levels"] = supported_reasoning_levels(entry["default_reasoning_level"])
    entry["selected"] = False
    raw_domain = overrides.get("memory_domain")
    entry["memory_domain"] = raw_domain.strip() if isinstance(raw_domain, str) and raw_domain.strip() else None
    return entry


def build_broken_symlink_entry(path: Path, manifest: Dict[str, Any], error: str) -> Dict[str, Any]:
    stem = path.stem
    filename = path.name
    target_path = path.resolve()
    entry = {
        "key": filename,
        "filename": filename,
        "stem": stem,
        "backend_id": stem,
        "native_backend_id": stem,
        "path": str(target_path),
        "source_path": str(path),
        "profile_symlink": True,
        "symlink_target_path": str(target_path),
        "size_bytes": 0,
        "mtime": int(path.lstat().st_mtime),
        "tensor_count": 0,
        "metadata": {},
        "architecture": "unknown",
        "name": stem,
        "context_length": None,
        "aliases": sorted({filename, stem, str(path)}),
    }
    overrides = model_overrides(manifest, entry)
    entry["overrides"] = overrides
    ov_aliases = overrides.get("aliases")
    if isinstance(ov_aliases, list) and ov_aliases:
        entry["aliases"] = sorted(
            set(entry["aliases"]) | {str(a) for a in ov_aliases if isinstance(a, str) and a}
        )
    entry["label"] = overrides.get("label") or stem
    entry["default"] = bool(overrides.get("default"))
    entry["backend_target"] = ""
    entry["profile_valid"] = False
    entry["profile_error"] = error
    entry["runtime_context_length"] = override_context_length(overrides)
    launch_args = overrides.get("launch_args", [])
    entry["launch_args"] = list(launch_args) if isinstance(launch_args, list) else []
    entry["notes"] = overrides.get("notes")
    entry["priority"] = overrides.get("priority")
    entry["default_reasoning_level"] = infer_reasoning_level(entry)
    entry["supported_reasoning_levels"] = supported_reasoning_levels(entry["default_reasoning_level"])
    entry["selected"] = False
    raw_domain = overrides.get("memory_domain")
    entry["memory_domain"] = raw_domain.strip() if isinstance(raw_domain, str) and raw_domain.strip() else None
    return entry


def validate_profile_targets(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    real_files_by_path: Dict[str, str] = {}
    for entry in entries:
        if entry.get("profile_symlink"):
            continue
        native_id = entry.get("native_backend_id") or entry.get("stem") or entry.get("key") or entry_identity(entry)
        if not isinstance(native_id, str) or not native_id.strip():
            continue
        path_value = entry.get("path")
        if isinstance(path_value, str) and path_value.strip():
            real_files_by_path[str(Path(path_value).resolve())] = native_id.strip()

    for entry in entries:
        if entry.get("profile_valid") is False:
            continue
        native_id = entry.get("native_backend_id") or entry.get("stem") or entry_identity(entry)
        if not entry.get("profile_symlink"):
            entry["backend_target"] = native_id
            entry["profile_valid"] = True
            entry["profile_error"] = ""
            continue

        target_path = entry.get("symlink_target_path")
        target_key = str(Path(target_path).resolve()) if isinstance(target_path, str) and target_path.strip() else ""
        target_id = real_files_by_path.get(target_key)
        if target_id:
            entry["backend_id"] = target_id
            entry["backend_target"] = target_id
            entry["profile_valid"] = True
            entry["profile_error"] = ""
            continue

        target_label = Path(target_key).name if target_key else str(target_path or "")
        error = f"symlink target not found in scanned GGUF models: {target_label}"
        entry["backend_target"] = ""
        entry["profile_valid"] = False
        entry["profile_error"] = error

    return entries


def scan_models(model_dir: Path, manifest: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not model_dir.is_dir():
        return [], []
    entries = []
    errors: List[Dict[str, Any]] = []
    for path in sorted(model_dir.glob("*.gguf")):
        if not path.is_file() and not path.is_symlink():
            continue
        try:
            entries.append(build_entry(path, manifest))
        except Exception as exc:
            if path.is_symlink():
                error = f"symlink target not found in scanned GGUF models: {Path(path.resolve()).name}"
                entries.append(build_broken_symlink_entry(path, manifest, error))
            else:
                errors.append({"path": str(path), "error": str(exc)})
    return validate_profile_targets(entries), errors


def match_model(entries: List[Dict[str, Any]], query: str) -> Optional[Dict[str, Any]]:
    q = query.strip().lower()
    if not q:
        return None
    for entry in entries:
        identity = entry_identity(entry)
        haystack = {
            identity,
            entry.get("key"),
            entry.get("filename"),
            entry.get("stem"),
            str(entry.get("path")),
            str(entry.get("source_path")),
        }
        haystack.update(entry.get("aliases", []))
        for value in haystack:
            if isinstance(value, str) and value.lower() == q:
                return entry
    for entry in entries:
        haystack = {
            entry.get("label"),
            entry.get("backend_id"),
            entry.get("name"),
            entry.get("architecture"),
        }
        for value in haystack:
            if isinstance(value, str) and value.lower() == q:
                return entry
    return None


def choose_default(entries: List[Dict[str, Any]], manifest: Dict[str, Any], query: Optional[str], last_selected: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    if not entries:
        return None, "no gguf files found"

    if query:
        match = match_model(entries, query)
        if match:
            return match, f"matched {query}"
        return None, f"no match for {query}"

    valid_entries = [entry for entry in entries if entry.get("profile_valid", True) is not False]
    if not valid_entries:
        return None, "no valid profiles found"

    if last_selected:
        match = match_model(valid_entries, last_selected)
        if match is not None:
            return match, f"last_selected={last_selected}"

    default_key = manifest.get("default_key")
    if isinstance(default_key, str) and default_key:
        match = match_model(valid_entries, default_key)
        if match is not None:
            return match, f"default_key={default_key}"

    for entry in valid_entries:
        if entry.get("default"):
            return entry, f"default flag on {entry_identity(entry)}"

    if len(valid_entries) == 1:
        return valid_entries[0], "single model"

    return valid_entries[0], "alphabetical fallback"


def cache_payload(root: Path, model_dir: Path, manifest: Dict[str, Any], entries: List[Dict[str, Any]], selected: Optional[Dict[str, Any]], reason: str, errors: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "root": str(root),
        "model_dir": str(model_dir),
        "default_key": manifest.get("default_key"),
        "reason": reason,
        "models": entries,
        "selected": selected,
        "errors": errors,
    }


def write_cache(root: Path, payload: Dict[str, Any]) -> Path:
    cache_path = _model_inventory_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return cache_path


def format_shell_value(value: Any) -> str:
    return shlex.quote("" if value is None else str(value))


def shell_assignments(selected: Dict[str, Any], cache_path: Path, reason: str) -> str:
    launch_args = selected.get("launch_args", [])
    if not isinstance(launch_args, list):
        launch_args = []
    lines = [
        f"QZ_MODEL_RESOLVED_SRC={format_shell_value(selected['path'])}",
        f"QZ_MODEL_RESOLVED_NAME={format_shell_value(selected['filename'])}",
        f"QZ_MODEL_RESOLVED_KEY={format_shell_value(entry_identity(selected))}",
        f"QZ_MODEL_RESOLVED_LABEL={format_shell_value(selected['label'])}",
        f"QZ_MODEL_RESOLVED_ARCHITECTURE={format_shell_value(selected.get('architecture'))}",
        f"QZ_MODEL_RESOLVED_CONTEXT={format_shell_value(selected.get('context_length'))}",
        f"QZ_MODEL_RESOLVED_RUNTIME_CONTEXT={format_shell_value(selected.get('runtime_context_length'))}",
        f"QZ_MODEL_RESOLVED_REASON={format_shell_value(reason)}",
        f"QZ_MODEL_INVENTORY_CACHE={format_shell_value(str(cache_path))}",
        "QZ_MODEL_LAUNCH_ARGS=(" + " ".join(shlex.quote(str(arg)) for arg in launch_args) + ")",
    ]
    return "\n".join(lines)


def plain_listing(entries: List[Dict[str, Any]], selected: Optional[Dict[str, Any]], reason: str) -> str:
    lines = []
    for entry in entries:
        marker = "*" if selected and entry_identity(entry) == entry_identity(selected) else " "
        label = entry["label"]
        arch = entry.get("architecture") or "unknown"
        context = entry.get("runtime_context_length")
        if context is None:
            context = entry.get("context_length")
        context_text = str(context) if context is not None else "?"
        launch = len(entry.get("launch_args", [])) if isinstance(entry.get("launch_args"), list) else 0
        lines.append(
            f"{marker} {entry_identity(entry)} | {label} | {arch} | ctx={context_text} | launch_args={launch}"
        )
    if selected:
        lines.append(f"selected: {entry_identity(selected)} ({reason})")
    return "\n".join(lines)


class ModelCatalog:
    def __init__(self, root: Path, model_dir: Path, manifest: Dict[str, Any], reload_manifest_on_refresh: bool = False):
        self.root = root
        self.model_dir = model_dir
        self.manifest = manifest
        self.reload_manifest_on_refresh = reload_manifest_on_refresh
        self.entries: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.selected: Optional[Dict[str, Any]] = None
        self.reason = "uninitialized"
        self.cache_path = _model_inventory_path()
        self.refresh()

    @classmethod
    def from_env(cls, root: Path) -> "ModelCatalog":
        model_dir = Path(os.environ.get("QZ_MODEL_DIR", str(root / "var" / "models")))
        manifest = load_manifest(root)
        return cls(root, model_dir, manifest, reload_manifest_on_refresh=True)

    def refresh(self, query: Optional[str] = None) -> None:
        if self.reload_manifest_on_refresh:
            self.manifest = load_manifest(self.root)
        self.entries, self.errors = scan_models(self.model_dir, self.manifest)
        requested = query or os.environ.get("QZ_MODEL_KEY")
        last_selected = "" if requested else load_last_selected_model(self.root)
        self.selected, self.reason = choose_default(self.entries, self.manifest, requested, last_selected)
        payload = cache_payload(self.root, self.model_dir, self.manifest, self.entries, self.selected, self.reason, self.errors)
        self.cache_path = write_cache(self.root, payload)

    def resolve(self, query: Optional[str] = None, direct_path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
        if direct_path and direct_path.is_file():
            return build_entry(direct_path, self.manifest), "direct path"
        requested = query or None
        last_selected = "" if requested else load_last_selected_model(self.root)
        selected, reason = choose_default(self.entries, self.manifest, requested, last_selected)
        return selected, reason

    def select(self, query: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
        requested = query or None
        last_selected = "" if requested else load_last_selected_model(self.root)
        selected, reason = choose_default(self.entries, self.manifest, requested, last_selected)
        self.selected = selected
        self.reason = reason
        payload = cache_payload(self.root, self.model_dir, self.manifest, self.entries, self.selected, self.reason, self.errors)
        self.cache_path = write_cache(self.root, payload)
        return selected, reason

    def to_payload(self) -> Dict[str, Any]:
        return cache_payload(self.root, self.model_dir, self.manifest, self.entries, self.selected, self.reason, self.errors)

    def to_v1_models(self, backend_models: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        data = []
        backend_models = backend_models or {}
        for entry in self.entries:
            entry_key = entry_identity(entry)
            backend = backend_models.get(entry.get("backend_id") or entry_key, backend_models.get(entry_key, {}))
            data.append({
                "id": entry_key,
                "object": "model",
                "owned_by": "local",
                "label": entry["label"],
                "architecture": entry.get("architecture"),
                "context_length": entry.get("context_length"),
                "runtime_context_length": entry.get("runtime_context_length"),
                "backend_id": entry.get("backend_id"),
                "backend_target": entry.get("backend_target"),
                "profile_valid": entry.get("profile_valid", True),
                "profile_error": entry.get("profile_error"),
                "memory_domain": entry.get("memory_domain"),
                "state": backend.get("state", "unloaded"),
                "backend_path": backend.get("path"),
                "notes": entry.get("notes"),
            })
        return {"object": "list", "data": data}

    def to_ollama_models(self, backend_models: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        now = "2026-04-27T00:00:00Z"
        backend_models = backend_models or {}
        models = []
        for entry in self.entries:
            entry_key = entry_identity(entry)
            backend = backend_models.get(entry.get("backend_id") or entry_key, backend_models.get(entry_key, {}))
            models.append({
                "name": entry.get("backend_id") or entry_key,
                "model": entry.get("backend_id") or entry_key,
                "modified_at": now,
                "size": entry.get("size_bytes", 1),
                "digest": f"local-{entry.get('backend_id') or entry_key}",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": entry.get("architecture") or "unknown",
                    "families": [entry.get("architecture") or "unknown"],
                    "parameter_size": entry.get("label") or entry.get("backend_id") or entry_key,
                    "quantization_level": backend.get("quantization_level") or "unknown",
                }
            })
        return models


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan and resolve QuantZhai GGUF models")
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--write-cache", action="store_true", default=True)

    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--path", default=None, help="Inspect a specific GGUF path")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--path", default=None, help="Inspect or resolve a specific GGUF path")
    resolve.add_argument("--shell", action="store_true")
    resolve.add_argument("--json", action="store_true")
    sub.add_parser("list")

    args = parser.parse_args()
    root = Path(os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1]))
    model_dir = Path(args.model_dir or os.environ.get("QZ_MODEL_DIR", str(root / "var" / "models")))
    manifest = load_manifest(root)
    catalog = ModelCatalog(root, model_dir, manifest)
    query = os.environ.get("QZ_MODEL_KEY")
    path_arg = getattr(args, "path", None)
    direct_path = Path(path_arg).expanduser() if path_arg else None
    direct_selected = None
    if direct_path and direct_path.is_file():
        try:
            direct_selected = build_entry(direct_path, manifest)
        except Exception as exc:
            print(f"invalid GGUF {direct_path}: {exc}", file=sys.stderr)
            return 1

    if args.command == "scan":
        selected, reason = (direct_selected, "direct path") if direct_selected else catalog.resolve(query=query)
        payload = cache_payload(root, model_dir, manifest, catalog.entries, selected, reason, catalog.errors)
        cache_path = write_cache(root, payload)
        payload["cache_path"] = str(cache_path)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    selected, reason = (direct_selected, "direct path") if direct_selected else catalog.resolve(query=query)
    payload = cache_payload(root, model_dir, manifest, catalog.entries, selected, reason, catalog.errors)
    cache_path = write_cache(root, payload)

    if args.command == "list":
        print(plain_listing(catalog.entries, selected, reason))
        return 0

    if selected is None:
        print(f"no gguf models found under {model_dir}", file=sys.stderr)
        for error in catalog.errors:
            print(f"skip {error['path']}: {error['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.shell:
        print(shell_assignments(selected, cache_path, reason))
        return 0

    print(f"{selected['key']} -> {selected['path']} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
