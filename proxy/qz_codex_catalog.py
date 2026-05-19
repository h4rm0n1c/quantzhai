#!/usr/bin/env python3
"""Generate the Codex-facing model catalog from the QuantZhai model inventory.

Manifest loading delegates to qz_model_catalog.load_manifest so the catalog
generator stays in sync with the proxy's own manifest reading. Prompt assembly
delegates to qz_prompt_policy.assemble_instruction_stack so the catalog's
base_instructions reflect the same prompt-policy logic the proxy applies at
request time.

Usage:
  python3 proxy/qz_codex_catalog.py <inventory_path> <catalog_dst> <config_dst>

Arguments:
  inventory_path  Path to var/generated/model-inventory.json
  catalog_dst     Output path for the generated Codex catalog JSON
  config_dst      Path to var/generated/codex/config.toml (patched in-place)
"""
import json
import re
import sys
from pathlib import Path

try:
    from .qz_model_catalog import deep_merge, load_json, load_manifest
    from .qz_prompt_policy import assemble_instruction_stack
except ImportError:
    from qz_model_catalog import deep_merge, load_json, load_manifest
    from qz_prompt_policy import assemble_instruction_stack


# ---------------------------------------------------------------------------
# Catalog-specific utilities
# ---------------------------------------------------------------------------

def override_value(overrides, *keys):
    if not isinstance(overrides, dict):
        return None
    for key in keys:
        value = overrides.get(key)
        if value is not None:
            return value
    return None


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

    instructions, report = assemble_instruction_stack(
        existing_instructions="",
        client_blocks=[],
        selected_model=entry,
    )
    model["base_instructions"] = instructions

    if not instructions and not report.get("disable_system_prompt"):
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
    """Generate the Codex model catalog and patch config.toml."""
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
