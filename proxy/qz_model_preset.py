"""Auto-configure models-preset.ini based on model architecture.

Called by the proxy when a model is selected via /qz/model/select-and-restart.
Reads the model's architecture from the catalog and adds the appropriate
preset section so the llama.cpp router uses compatible flags.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

# Architecture → preset overrides.
# These are appended to the base server args.  Only overrides that differ
# from the base should be listed — the router merges base + preset.
_ARCH_OVERRIDES: Dict[str, Dict[str, str]] = {
    # Gemma 4 doesn't support --reasoning on (crash during model init).
    "gemma4": {
        "reasoning": "off",
    },
    # Mistral 3 (Devstral) — same issue as Gemma 4 with reasoning.
    "mistral3": {
        "reasoning": "off",
    },
    # Qwen3.6 MoE with MTP heads: enable self-speculative decoding.
    # Detected by filename containing "MTP" or "APEX" — not architecture alone
    # since base qwen35moe also exists without MTP.
    # These are set in a separate step below since they're file-name based.
}

# Models whose filename contains these substrings get MTP spec override.
_MTP_MARKERS = ("MTP", "APEX", "Native-MTP")

_MTP_OVERRIDES: Dict[str, str] = {
    "spec-type": "draft-mtp",
    "spec-draft-n-max": "2",
    "spec-draft-p-min": "0.75",
}

# Models whose GGUF stem contains these exact substrings get reasoning=off.
# Falls back to architecture-based detection when no filename matches.
_REASONING_OFF_FILENAMES = (
    "gemma-4",
    "google_gemma-4",
    "Devstral",
)


def _detect_overrides(entry: Dict[str, Any]) -> Dict[str, str]:
    """Determine what preset overrides a model needs based on its catalog entry."""
    overrides: Dict[str, str] = {}
    arch = (entry.get("architecture") or "").lower()
    fn = (entry.get("filename") or entry.get("stem") or "")

    # Architecture-based overrides
    arch_overrides = _ARCH_OVERRIDES.get(arch)
    if arch_overrides:
        overrides.update(arch_overrides)

    # Filename-based reasoning override (catches variants arch alone misses)
    if any(marker in fn for marker in _REASONING_OFF_FILENAMES):
        overrides["reasoning"] = "off"

    # MTP heads: enable speculative decoding
    if any(marker in fn for marker in _MTP_MARKERS):
        overrides.update(_MTP_OVERRIDES)

    return overrides


def _preset_path(handler: Any) -> Path:
    """Resolve the models-preset.ini path from the proxy's model dir."""
    model_dir = getattr(handler, "backend_manager", None)
    if model_dir is not None:
        model_dir_path = getattr(model_dir, "_model_dir", None)
        if model_dir_path:
            return Path(model_dir_path) / "models-preset.ini"
    # Fallback: env var or default path
    root = Path(os.environ.get("QZ_ROOT", str(Path.cwd())))
    return root / "var" / "models" / "models-preset.ini"


def _read_preset(path: Path) -> Dict[str, Dict[str, str]]:
    """Parse models-preset.ini into {section: {key: value}}."""
    result: Dict[str, Dict[str, str]] = {}
    if not path.is_file():
        return result
    current_section: Optional[str] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            if current_section not in result:
                result[current_section] = {}
            continue
        if current_section and "=" in line:
            key, _, val = line.partition("=")
            result[current_section][key.strip()] = val.strip()
    return result


def _write_preset(path: Path, sections: Dict[str, Dict[str, str]],
                  header_lines: list[str]) -> None:
    """Write models-preset.ini preserving header comments and section order."""
    lines = list(header_lines)
    for section_name, kv in sections.items():
        if not kv:
            continue
        lines.append(f"\n[{section_name}]")
        for key, val in kv.items():
            lines.append(f"{key}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_models_preset(handler: Any) -> None:
    """Ensure models-preset.ini has the correct section for the selected model.

    Called when a model is selected.  Reads the proxy's model catalog to find
    the entry that matches the current selection, computes the needed preset
    overrides, and updates the ini file.  Existing sections are preserved.
    """
    # Resolve the currently selected model from the catalog
    catalog = getattr(handler, "model_catalog", None)
    if catalog is None:
        return
    selected = getattr(catalog, "selected", None)
    if not selected:
        return

    # Compute what overrides this model needs
    overrides = _detect_overrides(selected)
    if not overrides:
        return  # no overrides needed

    # Resolve the section name (GGUF stem without .gguf)
    section = selected.get("stem") or selected.get("backend_id") or selected.get("key", "").replace(".gguf", "")
    if not section:
        return

    # Read current ini, preserving structure
    preset_path = _preset_path(handler)
    existing = _read_preset(preset_path)

    # Collect header comments from the existing file
    header_lines: list[str] = []
    if preset_path.is_file():
        for line in preset_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                header_lines.append(line)
            elif stripped.startswith("[") and stripped.endswith("]"):
                break  # stop at first section
        else:
            header_lines = []
    if not header_lines:
        header_lines = [
            "# Per-model llama.cpp arg overrides for the router.",
            "# Auto-generated by QuantZhai proxy — do not edit manually.",
        ]

    # Check if the section already has all required overrides
    current = existing.get(section, {})
    missing = {k: v for k, v in overrides.items()
               if k not in current or current[k] != v}
    if not missing:
        return  # already configured correctly

    # Merge: existing values take priority (user settings win)
    merged = dict(current)
    for k, v in overrides.items():
        if k not in merged:
            merged[k] = v
    existing[section] = merged

    _write_preset(preset_path, existing, header_lines)
