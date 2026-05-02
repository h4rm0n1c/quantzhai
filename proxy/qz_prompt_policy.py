#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

try:
    from .qz_runtime_io import runtime_state_path
except ImportError:
    from qz_runtime_io import runtime_state_path


DEFAULT_PROMPT_POLICY = {
    "mode": "replace_client",
    "allow_replace": True,
    "allow_prepend_before_client": False,
    "synthesize_missing_client": True,
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


def _replacement_prompt(report, manifest, policy, model_overrides):
    inline = _first_block(
        model_overrides.get("system_prompt"),
        model_overrides.get("codex_base_instructions"),
        model_overrides.get("base_instructions"),
        model_overrides.get("prompt_replace"),
        manifest.get("system_prompt"),
        manifest.get("codex_base_instructions"),
        manifest.get("base_instructions"),
        manifest.get("prompt_replace"),
        policy.get("system_prompt"),
        policy.get("codex_base_instructions"),
        policy.get("base_instructions"),
        policy.get("prompt_replace"),
        policy.get("global_replace"),
    )
    if inline:
        return inline

    return _first_file_block(
        report,
        model_overrides.get("system_prompt_file"),
        model_overrides.get("codex_base_instructions_file"),
        model_overrides.get("base_instructions_file"),
        model_overrides.get("prompt_replace_files"),
        model_overrides.get("prompt_replace_file"),
        manifest.get("system_prompt_file"),
        manifest.get("codex_base_instructions_file"),
        manifest.get("base_instructions_file"),
        manifest.get("prompt_replace_files"),
        manifest.get("prompt_replace_file"),
        policy.get("system_prompt_file"),
        policy.get("codex_base_instructions_file"),
        policy.get("base_instructions_file"),
        policy.get("prompt_replace_files"),
        policy.get("prompt_replace_file"),
        policy.get("global_replace_files"),
        policy.get("global_replace_file"),
    )


def _proxy_added_instruction_blocks(existing_blocks):
    """Keep proxy-added instruction crumbs while dropping Codex/client harness text."""
    kept = []
    for block in existing_blocks:
        for part in re.split(r"\n{2,}", block):
            text = part.strip()
            if not text:
                continue
            if text.startswith("<QZSTATE "):
                kept.append(text)
                continue
            if text.startswith("Use ") and "reasoning effort" in text:
                kept.append(text)
                continue
    return kept


def _has_non_proxy_instruction_block(existing_blocks):
    """True when instructions already contain a real selected/profile prompt."""
    for block in existing_blocks:
        for part in re.split(r"\n{2,}", block):
            text = part.strip()
            if not text:
                continue
            if text.startswith("<QZSTATE "):
                continue
            if text.startswith("Use ") and "reasoning effort" in text:
                continue
            return True
    return False


def assemble_instruction_stack(existing_instructions="", client_blocks=None, selected_model=None, synthesize_missing_client=None):
    """
    Build final upstream instructions.

    Ordering:
    1. QuantZhai selected/global system_prompt_file by default.
    2. Optional prompt prepend/append blocks from selected/global overrides.
    3. Existing proxy-added instructions, such as reasoning hint and QZSTATE.

    Codex/client instruction blocks are preserved only when policy opts out of
    the default replace_client mode.
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

    mode = str(policy.get("mode") or "replace_client").strip().lower()
    if mode not in {"preserve_client", "append_only", "replace_client", "debug_dump"}:
        mode = "replace_client"

    allow_replace = bool(policy.get("allow_replace"))
    allow_prepend_before_client = bool(policy.get("allow_prepend_before_client"))
    if synthesize_missing_client is None:
        synthesize_missing_client = bool(policy.get("synthesize_missing_client", True))
    else:
        synthesize_missing_client = bool(synthesize_missing_client)

    report = {
        "mode": mode,
        "allow_replace": allow_replace,
        "allow_prepend_before_client": allow_prepend_before_client,
        "synthesize_missing_client": synthesize_missing_client,
        "prompt_files_loaded": [],
        "prompt_files_missing": [],
        "prompt_files_failed": [],
        "replacement_files_loaded": [],
        "replacement_files_missing": [],
        "replacement_files_failed": [],
    }

    client_blocks = _blocks(client_blocks or [])
    existing_blocks = _blocks(existing_instructions)

    if selected_model is None and not client_blocks and _has_non_proxy_instruction_block(existing_blocks):
        final_blocks = _dedupe_preserve_order(existing_blocks)
        final_text = "\n\n".join(final_blocks)
        report.update({
            "client_blocks": len(client_blocks),
            "existing_blocks": len(existing_blocks),
            "global_prepend_blocks": 0,
            "global_append_blocks": 0,
            "model_prepend_blocks": 0,
            "model_append_blocks": 0,
            "replacement_available": False,
            "replacement_already_present": True,
            "replaced_client": False,
            "synthesized_missing_client": False,
            "reused_existing_replacement": True,
            "reused_existing_without_selected_model": True,
            "ignored_replace": False,
        })
        return final_text, report

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

    replacement = _replacement_prompt(report, manifest, policy, model_overrides)
    existing_text = "\n\n".join(existing_blocks)
    replacement_already_present = bool(replacement and replacement in existing_text)

    replaced_client = False
    synthesized_missing_client = False
    reused_existing_replacement = False
    ignored_replace = False
    append_existing_blocks = True

    if replacement_already_present and not client_blocks:
        base_blocks = list(existing_blocks)
        append_existing_blocks = False
        reused_existing_replacement = True
    elif replacement and mode == "replace_client" and allow_replace:
        base_blocks = [replacement]
        existing_blocks = _proxy_added_instruction_blocks(existing_blocks)
        replaced_client = bool(client_blocks or existing_text)
    elif replacement and synthesize_missing_client and not client_blocks:
        base_blocks = [replacement]
        synthesized_missing_client = True
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
        if append_existing_blocks:
            stack.extend(existing_blocks)
    else:
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
        "replacement_available": bool(replacement),
        "replacement_already_present": replacement_already_present,
        "replaced_client": replaced_client,
        "synthesized_missing_client": synthesized_missing_client,
        "reused_existing_replacement": reused_existing_replacement,
        "ignored_replace": ignored_replace,
    })

    return final_text, report
