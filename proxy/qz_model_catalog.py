#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def load_manifest(root: Path, overrides_path: Optional[Path] = None) -> Dict[str, Any]:
    manifest = {
        "default_key": None,
        "models": {},
    }
    base_path = root / "config" / "qz-model-overrides.example.json"
    runtime_path = overrides_path or Path(os.environ.get("QZ_MODEL_OVERRIDES", str(root / "var" / "model-overrides.json")))
    for path in (base_path, runtime_path):
        loaded = load_json(path)
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


def build_entry(path: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    stat = path.stat()
    tensor_count, metadata = read_gguf_metadata(path)
    stem = path.stem
    filename = path.name
    name = infer_model_name(metadata, stem)
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
        architecture,
        str(path),
    }
    entry = {
        "key": filename,
        "filename": filename,
        "stem": stem,
        "backend_id": stem,
        "path": str(path.resolve()),
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
    entry["label"] = overrides.get("label") or name or stem
    entry["default"] = bool(overrides.get("default"))
    entry["server_alias"] = overrides.get("server_alias")
    launch_args = overrides.get("launch_args", [])
    entry["launch_args"] = list(launch_args) if isinstance(launch_args, list) else []
    entry["notes"] = overrides.get("notes")
    entry["priority"] = overrides.get("priority")
    entry["selected"] = False
    return entry


def scan_models(model_dir: Path, manifest: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not model_dir.is_dir():
        return [], []
    entries = []
    errors: List[Dict[str, Any]] = []
    for path in sorted(model_dir.glob("*.gguf")):
        if path.is_file():
            try:
                entries.append(build_entry(path, manifest))
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
    return entries, errors


def match_model(entries: List[Dict[str, Any]], query: str) -> Optional[Dict[str, Any]]:
    q = query.strip().lower()
    if not q:
        return None
    for entry in entries:
        haystack = {
            entry["key"],
            entry["filename"],
            entry["stem"],
            entry["name"],
            entry["label"],
            entry.get("server_alias"),
            entry["architecture"],
            str(entry["path"]),
        }
        haystack.update(entry.get("aliases", []))
        for value in haystack:
            if isinstance(value, str) and value.lower() == q:
                return entry
    return None


def choose_default(entries: List[Dict[str, Any]], manifest: Dict[str, Any], query: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    if not entries:
        return None, "no gguf files found"

    if query:
        match = match_model(entries, query)
        if match:
            return match, f"matched {query}"
        return None, f"no match for {query}"

    default_key = manifest.get("default_key")
    if isinstance(default_key, str) and default_key:
        match = match_model(entries, default_key)
        if match:
            return match, f"default_key={default_key}"

    for entry in entries:
        if entry.get("default"):
            return entry, f"default flag on {entry['key']}"

    if len(entries) == 1:
        return entries[0], "single model"

    return entries[0], "alphabetical fallback"


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
    cache_path = root / "var" / "model-inventory.json"
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
        f"QZ_MODEL_RESOLVED_KEY={format_shell_value(selected['key'])}",
        f"QZ_MODEL_RESOLVED_LABEL={format_shell_value(selected['label'])}",
        f"QZ_MODEL_RESOLVED_ARCHITECTURE={format_shell_value(selected.get('architecture'))}",
        f"QZ_MODEL_RESOLVED_CONTEXT={format_shell_value(selected.get('context_length'))}",
        f"QZ_MODEL_RESOLVED_REASON={format_shell_value(reason)}",
        f"QZ_MODEL_INVENTORY_CACHE={format_shell_value(str(cache_path))}",
        f"QZ_MODEL_RESOLVED_SERVER_ALIAS={format_shell_value(selected.get('server_alias'))}",
        "QZ_MODEL_LAUNCH_ARGS=(" + " ".join(shlex.quote(str(arg)) for arg in launch_args) + ")",
    ]
    return "\n".join(lines)


def plain_listing(entries: List[Dict[str, Any]], selected: Optional[Dict[str, Any]], reason: str) -> str:
    lines = []
    for entry in entries:
        marker = "*" if selected and entry["key"] == selected["key"] else " "
        label = entry["label"]
        arch = entry.get("architecture") or "unknown"
        context = entry.get("context_length")
        context_text = str(context) if context is not None else "?"
        launch = len(entry.get("launch_args", [])) if isinstance(entry.get("launch_args"), list) else 0
        lines.append(
            f"{marker} {entry['key']} | {label} | {arch} | ctx={context_text} | launch_args={launch}"
        )
    if selected:
        lines.append(f"selected: {selected['key']} ({reason})")
    return "\n".join(lines)


class ModelCatalog:
    def __init__(self, root: Path, model_dir: Path, manifest: Dict[str, Any]):
        self.root = root
        self.model_dir = model_dir
        self.manifest = manifest
        self.entries: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.selected: Optional[Dict[str, Any]] = None
        self.reason = "uninitialized"
        self.cache_path = root / "var" / "model-inventory.json"
        self.refresh()

    @classmethod
    def from_env(cls, root: Path) -> "ModelCatalog":
        model_dir = Path(os.environ.get("QZ_MODEL_DIR", str(root / "var" / "models")))
        manifest = load_manifest(root)
        return cls(root, model_dir, manifest)

    def refresh(self, query: Optional[str] = None) -> None:
        self.entries, self.errors = scan_models(self.model_dir, self.manifest)
        self.selected, self.reason = choose_default(self.entries, self.manifest, query or os.environ.get("QZ_MODEL_KEY"))
        payload = cache_payload(self.root, self.model_dir, self.manifest, self.entries, self.selected, self.reason, self.errors)
        self.cache_path = write_cache(self.root, payload)

    def resolve(self, query: Optional[str] = None, direct_path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
        if direct_path and direct_path.is_file():
            return build_entry(direct_path, self.manifest), "direct path"
        selected, reason = choose_default(self.entries, self.manifest, query)
        return selected, reason

    def select(self, query: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
        selected, reason = choose_default(self.entries, self.manifest, query)
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
            backend = backend_models.get(entry.get("backend_id") or entry["key"], backend_models.get(entry["key"], {}))
            data.append({
                "id": entry["key"],
                "object": "model",
                "owned_by": "local",
                "label": entry["label"],
                "architecture": entry.get("architecture"),
                "context_length": entry.get("context_length"),
                "backend_id": entry.get("backend_id"),
                "server_alias": entry.get("server_alias"),
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
            backend = backend_models.get(entry.get("backend_id") or entry["key"], backend_models.get(entry["key"], {}))
            models.append({
                "name": entry.get("backend_id") or entry["key"],
                "model": entry.get("backend_id") or entry["key"],
                "modified_at": now,
                "size": entry.get("size_bytes", 1),
                "digest": f"local-{entry.get('backend_id') or entry['key']}",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": entry.get("architecture") or "unknown",
                    "families": [entry.get("architecture") or "unknown"],
                    "parameter_size": entry.get("label") or entry.get("backend_id") or entry["key"],
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
    payload["cache_path"] = str(cache_path)

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
