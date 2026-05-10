#!/usr/bin/env python3
"""Generate the Codex-facing model catalog from the QuantZhai model inventory.

Extracted verbatim from the bash heredoc in scripts/qz-codex-common.
The logic and globals are unchanged; the imperative section is wrapped in
generate() so the module can be imported and tested.

Usage (matches the original heredoc invocation):
  python3 proxy/qz_codex_catalog.py <inventory_path> <catalog_dst> <config_dst>

Arguments:
  inventory_path  Path to var/model-inventory.json
  catalog_dst     Output path for the generated Codex catalog JSON
  config_dst      Path to var/codex-home/config.toml (patched in-place)
"""
import json
import os
import re
import sys
from pathlib import Path

# Module-level globals set by generate() before any helper function is called.
root_dir: Path = Path(".")
OVERRIDE_MANIFEST: dict = {}
DEFAULT_PROMPT_POLICY: dict = {
    "allow_prepend_before_client": False,
}


# ---------------------------------------------------------------------------
# Helper functions (verbatim from heredoc)
# ---------------------------------------------------------------------------

def load_json(path):
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def deep_merge(base, overlay):
    result = dict(base) if isinstance(base, dict) else {}
    if not isinstance(overlay, dict):
        return result
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def first_existing_path(*paths):
    for path in paths:
        if path.is_file():
            return path
    return paths[0]


def load_override_manifest():
    manifest = {}
    user_override = Path(os.environ.get("QZ_MODEL_OVERRIDES", str(root_dir / "config" / "user" / "model-overrides.json"))).expanduser()
    user_paths = [user_override]
    default_user = root_dir / "config" / "user" / "model-overrides.json"
    legacy_user = root_dir / "var" / "model-overrides.json"
    if user_override == default_user and not user_override.is_file():
        user_paths.append(legacy_user)
    for path in (
        first_existing_path(
            root_dir / "config" / "default" / "model-overrides.json",
            root_dir / "config" / "qz-model-overrides.default.json",
        ),
    ):
        data = load_json(path)
        if data:
            manifest = deep_merge(manifest, data)
    for path in user_paths:
        data = load_json(path)
        if data:
            manifest = deep_merge(manifest, data)
            break
    if not isinstance(manifest.get("models"), dict):
        manifest["models"] = {}
    return manifest


def _resolve_repo_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = root_dir / path
    return path


def _read_prompt_file(value):
    path = _resolve_repo_path(value)
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _prompt_blocks(value):
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


def _prompt_file_blocks(value):
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [item for item in value if isinstance(item, str)]
    else:
        values = []
    return [text for text in (_read_prompt_file(item) for item in values) if text]


def _dedupe_blocks(blocks):
    seen = set()
    out = []
    for block in blocks:
        text = block.strip() if isinstance(block, str) else ""
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _boolish(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
    return False


def override_value(overrides, *keys):
    if not isinstance(overrides, dict):
        return None
    for key in keys:
        value = overrides.get(key)
        if value is not None:
            return value
    return None


def system_prompt_for_model(overrides):
    """Return the Codex catalog base_instructions for one generated model."""
    if not isinstance(overrides, dict):
        overrides = {}

    model_disable = override_value(overrides, "disable_system_prompt")
    global_disable = override_value(OVERRIDE_MANIFEST, "disable_system_prompt")
    if _boolish(model_disable if model_disable is not None else global_disable):
        return ""

    # Per-model inline override wins when intentionally set.
    inline = override_value(
        overrides,
        "system_prompt",
        "codex_base_instructions",
        "base_instructions",
    )
    if isinstance(inline, str) and inline.strip():
        base = inline.strip()
    else:
        # Per-model file override is preferred for long prompts.
        file_value = override_value(
            overrides,
            "system_prompt_file",
            "codex_base_instructions_file",
            "base_instructions_file",
        )
        file_text = _read_prompt_file(file_value) if isinstance(file_value, str) else ""
        if file_text:
            base = file_text
        else:
            # Global default, shipped through config/default/model-overrides.json and
            # optionally overridden in config/user/model-overrides.json.
            inline = override_value(
                OVERRIDE_MANIFEST,
                "system_prompt",
                "codex_base_instructions",
                "base_instructions",
            )
            if isinstance(inline, str) and inline.strip():
                base = inline.strip()
            else:
                file_value = override_value(
                    OVERRIDE_MANIFEST,
                    "system_prompt_file",
                    "codex_base_instructions_file",
                    "base_instructions_file",
                )
                base = _read_prompt_file(file_value) if isinstance(file_value, str) else ""

    top_policy = OVERRIDE_MANIFEST.get("prompt_policy")
    model_policy = overrides.get("prompt_policy")
    policy = dict(DEFAULT_PROMPT_POLICY)
    if isinstance(top_policy, dict):
        policy = deep_merge(policy, top_policy)
    if isinstance(model_policy, dict):
        policy = deep_merge(policy, model_policy)
    global_prepend = (
        _prompt_blocks(policy.get("global_prepend"))
        + _prompt_blocks(policy.get("prompt_prepend"))
        + _prompt_file_blocks(policy.get("global_prepend_files"))
        + _prompt_file_blocks(policy.get("prompt_prepend_files"))
    )
    global_append = (
        _prompt_blocks(policy.get("global_append"))
        + _prompt_blocks(policy.get("prompt_append"))
        + _prompt_file_blocks(policy.get("global_append_files"))
        + _prompt_file_blocks(policy.get("prompt_append_files"))
    )
    model_prepend = (
        _prompt_blocks(overrides.get("prompt_prepend"))
        + _prompt_file_blocks(overrides.get("prompt_prepend_files"))
    )
    model_append = (
        _prompt_blocks(overrides.get("prompt_append"))
        + _prompt_file_blocks(overrides.get("prompt_append_files"))
    )

    stack = []
    if bool(policy.get("allow_prepend_before_client")):
        stack.extend(global_prepend)
        stack.extend(model_prepend)
    stack.append(base)
    if not bool(policy.get("allow_prepend_before_client")):
        stack.extend(global_prepend)
        stack.extend(model_prepend)
    stack.extend(global_append)
    stack.extend(model_append)
    return "\n\n".join(_dedupe_blocks(stack))


def reasoning_level(entry):
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


REASONING_POLICIES = {
    "low": {
        "description": "Fast/shallow effort for simple prompts.",
        "prompt": "Reasoning effort: low.",
        "sampling": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        },
    },
    "medium": {
        "description": "Default coding-agent balance.",
        "prompt": "Reasoning effort: medium.",
        "sampling": {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        },
    },
    "high": {
        "description": "Careful reasoning for complex coding work.",
        "prompt": "Reasoning effort: high.",
        "sampling": {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        },
    },
    "xhigh": {
        "description": "Deep effort when complexity warrants it.",
        "prompt": "Reasoning effort: xhigh.",
        "sampling": {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        },
    },
}


def supported_reasoning_levels(default_level):
    supported = []
    for effort, policy in REASONING_POLICIES.items():
        supported.append({
            "effort": effort,
            "description": policy["description"],
            "prompt": policy["prompt"],
            "sampling": policy["sampling"],
            "default": effort == default_level,
        })
    return supported


def default_model_messages():
    return {
        "instructions_template": "",
        "instructions_variables": {
            "personality_default": "",
            "personality_friendly": "",
        },
    }


def catalog_defaults():
    return {
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "additional_speed_tiers": [],
        "availability_nux": None,
        "upgrade": None,
        "base_instructions": "",
        "model_messages": default_model_messages(),
        "supports_reasoning_summaries": True,
        "default_reasoning_summary": "none",
        "support_verbosity": True,
        "default_verbosity": "low",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text",
        "supports_parallel_tool_calls": True,
        "supports_image_detail_original": False,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "supports_search_tool": False,
    }


def int_override(overrides, *keys):
    value = override_value(overrides, *keys)
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def truncation_limit(entry, overrides, runtime_context):
    explicit = int_override(
        overrides,
        "codex_truncation_limit",
        "truncation_limit",
        "codex_truncation_tokens",
        "truncation_tokens",
    )
    if explicit is not None and explicit > 0:
        return explicit

    if isinstance(runtime_context, int) and runtime_context > 0:
        # Codex uses this catalog value as its client-side context/truncation
        # budget. A stale 10k default makes 128k/256k local contexts behave as
        # if they were tiny and can trigger pathological compaction/replay.
        return max(10000, int(runtime_context * 0.95))

    return 10000


def profile_slug(entry):
    """Codex-visible model/profile id.

    This stays as the profile/symlink identity. The proxy maps symlink profiles
    to the scanned real backend target later.
    """
    for key in ("stem", "filename", "key", "label", "name", "backend_id"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            if key == "filename" and value.endswith(".gguf"):
                return value[:-5]
            return value.strip()
    return ""


def build_live_model(entry, priority):
    if entry.get("profile_valid", True) is False:
        return None

    slug = profile_slug(entry)
    if not slug:
        return None

    overrides = entry.get("overrides")
    if not isinstance(overrides, dict):
        overrides = {}

    model = catalog_defaults()
    model["slug"] = slug
    label = entry.get("label") or entry.get("name") or slug
    model["display_name"] = label
    notes = entry.get("notes") or overrides.get("notes")
    model["description"] = notes or f"Local GGUF model: {label}"

    base_instructions = system_prompt_for_model(overrides)
    model["base_instructions"] = base_instructions

    if not base_instructions:
        model_disable = override_value(overrides, "disable_system_prompt")
        global_disable = override_value(OVERRIDE_MANIFEST, "disable_system_prompt")
        disabled = _boolish(model_disable if model_disable is not None else global_disable)
        if not disabled:
            print(
                f"warning: model '{slug}' has no system prompt and disable_system_prompt is not set",
                file=sys.stderr,
            )

    model_messages = override_value(overrides, "codex_model_messages", "model_messages")
    if isinstance(model_messages, dict):
        model["model_messages"] = model_messages

    model["default_reasoning_level"] = entry.get("default_reasoning_level") or reasoning_level(entry)
    supported = entry.get("supported_reasoning_levels")
    if isinstance(supported, list) and supported:
        model["supported_reasoning_levels"] = supported
    else:
        model["supported_reasoning_levels"] = supported_reasoning_levels(model["default_reasoning_level"])

    model["priority"] = entry.get("priority") if entry.get("priority") is not None else priority
    runtime_context = entry.get("runtime_context_length")
    if runtime_context is None:
        runtime_context = entry.get("context_length")
    if runtime_context is not None:
        try:
            runtime_context = int(runtime_context)
        except Exception:
            runtime_context = None
    if runtime_context is not None:
        model["context_window"] = runtime_context
        model["max_context_window"] = runtime_context
    model["truncation_policy"] = {
        "mode": "tokens",
        "limit": truncation_limit(entry, overrides, runtime_context),
    }
    model["backend_id"] = entry.get("backend_id") or slug
    model["supported_in_api"] = True
    model["visibility"] = "list"
    return model


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate(inventory_path: Path, catalog_dst: Path, config_dst: Path) -> None:
    """Generate the Codex model catalog and patch config.toml.

    Sets module-level globals (root_dir, OVERRIDE_MANIFEST) that the helper
    functions reference, then runs the catalog generation verbatim.
    """
    global root_dir, OVERRIDE_MANIFEST
    root_dir = inventory_path.parent.parent
    OVERRIDE_MANIFEST = load_override_manifest()

    inventory = load_json(inventory_path)
    inventory_models = [entry for entry in inventory.get("models", []) if isinstance(entry, dict)]

    merged_models = []
    for index, entry in enumerate(inventory_models):
        model = build_live_model(entry, 1500 + index)
        if model:
            merged_models.append(model)

    catalog = {"models": merged_models}
    catalog_dst.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    text = config_dst.read_text(encoding="utf-8") if config_dst.is_file() else ""
    # Model limits now come from the generated model catalog and Codex defaults.
    # Older local configs may still contain static overrides that make /status
    # report stale runtime limits for the selected profile.
    text = re.sub(r'(?m)^\s*model_context_window\s*=\s*\d+\s*\n?', '', text)
    text = re.sub(r'(?m)^\s*model_max_output_tokens\s*=\s*\d+\s*\n?', '', text)
    catalog_line = f'model_catalog_json = "{catalog_dst}"'
    if re.search(r'(?m)^model_catalog_json\s*=\s*".*"$', text):
        text = re.sub(r'(?m)^model_catalog_json\s*=\s*".*"$', catalog_line, text, count=1)
    else:
        text = catalog_line + "\n" + text
    config_dst.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} <inventory_path> <catalog_dst> <config_dst>", file=sys.stderr)
        sys.exit(2)
    generate(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
