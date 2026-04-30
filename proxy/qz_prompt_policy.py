#!/usr/bin/env python3
import json
import os
from pathlib import Path

try:
    from .qz_runtime_io import runtime_state_path
except ImportError:
    from qz_runtime_io import runtime_state_path


DEFAULT_PROMPT_POLICY = {
    "mode": "preserve_client",
    "allow_replace": False,
    "allow_prepend_before_client": False,
}


def _root_dir() -> Path:
    raw = os.environ.get("QZ_ROOT")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _load_json(path):
    if not isinstance(path, Path) or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _model_overrides_path():
    raw = os.environ.get("QZ_MODEL_OVERRIDES")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return runtime_state_path("model-overrides.json")


def _deep_merge(base, overlay):
    result = dict(base) if isinstance(base, dict) else {}
    if not isinstance(overlay, dict):
        return result
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_manifest():
    manifest = {}
    default_path = _root_dir() / "config" / "qz-model-overrides.default.json"
    default_manifest = _load_json(default_path)
    if default_manifest:
        manifest = _deep_merge(manifest, default_manifest)

    runtime_manifest = _load_json(_model_overrides_path())
    if runtime_manifest:
        manifest = _deep_merge(manifest, runtime_manifest)

    if not isinstance(manifest.get("models"), dict):
        manifest["models"] = {}
    return manifest


def _clean_text(value):
    if not isinstance(value, str):
        return ""
    return value.strip()


def _blocks(value):
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out = []
        for item in value:
            text = _clean_text(item)
            if text:
                out.append(text)
        return out
    return []


def _path_values(value):
    if isinstance(value, (str, Path)):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, (str, Path)):
                text = str(item).strip()
                if text:
                    out.append(text)
        return out
    return []


def _resolve_prompt_path(value) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return _root_dir() / path


def _file_blocks(value, report=None, report_key="prompt_files"):
    blocks = []
    missing = []
    failed = []
    loaded = []

    for item in _path_values(value):
        path = _resolve_prompt_path(item)
        display = str(path)
        if not path.is_file():
            missing.append(display)
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            failed.append({"path": display, "error": str(exc)})
            continue
        if text:
            blocks.append(text)
            loaded.append(display)

    if isinstance(report, dict):
        report.setdefault(f"{report_key}_loaded", []).extend(loaded)
        report.setdefault(f"{report_key}_missing", []).extend(missing)
        report.setdefault(f"{report_key}_failed", []).extend(failed)

    return blocks


def _entry_keys(entry):
    if not isinstance(entry, dict):
        return []
    keys = []
    for field in ("slug", "key", "backend_id", "filename", "stem", "name", "label"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            keys.append(value.strip())
    aliases = entry.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str) and alias.strip():
                keys.append(alias.strip())
    return keys


def _selected_overrides(selected_model, manifest):
    selected_model = selected_model if isinstance(selected_model, dict) else {}

    overrides = selected_model.get("overrides")
    if isinstance(overrides, dict):
        return overrides

    models = manifest.get("models")
    if not isinstance(models, dict):
        return {}

    keys = _entry_keys(selected_model)
    for key in keys:
        value = models.get(key)
        if isinstance(value, dict):
            return value

    lower_keys = {key.lower() for key in keys}
    for value in models.values():
        if not isinstance(value, dict):
            continue
        aliases = value.get("aliases")
        if isinstance(aliases, list):
            if any(isinstance(alias, str) and alias.lower() in lower_keys for alias in aliases):
                return value

    return {}


def _first_block(*values):
    for value in values:
        blocks = _blocks(value)
        if blocks:
            return blocks[0]
    return ""


def _first_file_block(report, *values):
    for value in values:
        blocks = _file_blocks(value, report=report, report_key="replacement_files")
        if blocks:
            return blocks[0]
    return ""


def _dedupe_preserve_order(blocks):
    seen = set()
    out = []
    for block in blocks:
        text = _clean_text(block)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def assemble_instruction_stack(existing_instructions="", client_blocks=None, selected_model=None):
    """
    Build final upstream instructions.

    Ordering:
    1. Client/Codex system+developer harness, unless explicitly replaced.
    2. Optional QuantZhai prompt additions from default/runtime overrides.
    3. Existing proxy-added instructions, such as reasoning hint and QZSTATE.
    """
    manifest = _load_manifest()
    model_overrides = _selected_overrides(selected_model, manifest)

    top_policy = manifest.get("prompt_policy")
    model_policy = model_overrides.get("prompt_policy")

    policy = dict(DEFAULT_PROMPT_POLICY)
    if isinstance(top_policy, dict):
        policy = _deep_merge(policy, top_policy)
    if isinstance(model_policy, dict):
        policy = _deep_merge(policy, model_policy)

    mode = str(policy.get("mode") or "preserve_client").strip().lower()
    if mode not in {"preserve_client", "append_only", "replace_client", "debug_dump"}:
        mode = "preserve_client"

    allow_replace = bool(policy.get("allow_replace"))
    allow_prepend_before_client = bool(policy.get("allow_prepend_before_client"))

    report = {
        "mode": mode,
        "allow_replace": allow_replace,
        "allow_prepend_before_client": allow_prepend_before_client,
        "prompt_files_loaded": [],
        "prompt_files_missing": [],
        "prompt_files_failed": [],
        "replacement_files_loaded": [],
        "replacement_files_missing": [],
        "replacement_files_failed": [],
    }

    client_blocks = _blocks(client_blocks or [])
    existing_blocks = _blocks(existing_instructions)

    global_prepend = (
        _blocks(policy.get("global_prepend"))
        + _blocks(policy.get("prompt_prepend"))
        + _file_blocks(policy.get("global_prepend_files"), report=report)
        + _file_blocks(policy.get("prompt_prepend_files"), report=report)
    )
    global_append = (
        _blocks(policy.get("global_append"))
        + _blocks(policy.get("prompt_append"))
        + _file_blocks(policy.get("global_append_files"), report=report)
        + _file_blocks(policy.get("prompt_append_files"), report=report)
    )

    model_prepend = (
        _blocks(model_overrides.get("prompt_prepend"))
        + _file_blocks(model_overrides.get("prompt_prepend_files"), report=report)
    )
    model_append = (
        _blocks(model_overrides.get("prompt_append"))
        + _file_blocks(model_overrides.get("prompt_append_files"), report=report)
    )

    replacement = _first_block(
        model_overrides.get("prompt_replace"),
        policy.get("prompt_replace"),
        policy.get("global_replace"),
    ) or _first_file_block(
        report,
        model_overrides.get("prompt_replace_files"),
        model_overrides.get("prompt_replace_file"),
        policy.get("prompt_replace_files"),
        policy.get("prompt_replace_file"),
        policy.get("global_replace_files"),
        policy.get("global_replace_file"),
    )

    replaced_client = False
    ignored_replace = False

    if mode == "replace_client" and allow_replace and replacement and client_blocks:
        base_blocks = [replacement]
        replaced_client = True
    else:
        base_blocks = list(client_blocks)
        ignored_replace = bool(mode == "replace_client" and replacement and not replaced_client)

    stack = []

    if base_blocks:
        if allow_prepend_before_client:
            stack.extend(global_prepend)
            stack.extend(model_prepend)

        stack.extend(base_blocks)

        if not allow_prepend_before_client:
            stack.extend(global_prepend)
            stack.extend(model_prepend)

        stack.extend(global_append)
        stack.extend(model_append)
        stack.extend(existing_blocks)
    else:
        # No extracted Codex system/developer blocks. Preserve existing order
        # because existing may already contain client instructions plus QZ hints.
        if allow_prepend_before_client:
            stack.extend(global_prepend)
            stack.extend(model_prepend)
        stack.extend(existing_blocks)
        if not allow_prepend_before_client:
            stack.extend(global_prepend)
            stack.extend(model_prepend)
        stack.extend(global_append)
        stack.extend(model_append)

    final_blocks = _dedupe_preserve_order(stack)
    final_text = "\n\n".join(final_blocks)

    report.update({
        "client_blocks": len(client_blocks),
        "existing_blocks": len(existing_blocks),
        "global_prepend_blocks": len(global_prepend),
        "global_append_blocks": len(global_append),
        "model_prepend_blocks": len(model_prepend),
        "model_append_blocks": len(model_append),
        "replaced_client": replaced_client,
        "ignored_replace": ignored_replace,
    })

    return final_text, report
