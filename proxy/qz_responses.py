#!/usr/bin/env python3
import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any

try:
    from .qz_request_normalization import (
        CHECKPOINT_MARKER,
        HARNESS_TEXT_MARKERS,
        META_ASSISTANT_TEXT_MARKERS,
        META_USER_TEXT_MARKERS,
        clean_content,
        content_to_text as _content_to_text,
        is_local_checkpoint_prompt as _is_local_checkpoint_prompt,
        normalize_responses_input_for_qwen,
        recursive_clean,
    )
    from .qz_tool_apply_patch import (
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _parse_apply_patch_arguments,
        ensure_apply_patch_tool_policy,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
    )
    from .qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from .qz_tool_request import normalize_tools_for_llamacpp
    from .qz_survival_weight import score_text, format_survival_hints
except ImportError:
    from qz_request_normalization import (
        CHECKPOINT_MARKER,
        HARNESS_TEXT_MARKERS,
        META_ASSISTANT_TEXT_MARKERS,
        META_USER_TEXT_MARKERS,
        clean_content,
        content_to_text as _content_to_text,
        is_local_checkpoint_prompt as _is_local_checkpoint_prompt,
        normalize_responses_input_for_qwen,
        recursive_clean,
    )
    from qz_tool_apply_patch import (
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _parse_apply_patch_arguments,
        ensure_apply_patch_tool_policy,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
    )
    from qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from qz_tool_request import normalize_tools_for_llamacpp
    from qz_survival_weight import score_text, format_survival_hints

_VALID_COMPACTION_MODES = frozenset({"heuristic", "llm", "auto"})
_DEFAULT_LLM_TIMEOUT_SEC = 30
_DEFAULT_LLM_MAX_INPUT_CHARS = 100000
_DEFAULT_LLM_MAX_OUTPUT_TOKENS = 1536
_DEFAULT_LLM_DISABLE_REASONING = True
_DEFAULT_SURVIVAL_PROFILE = "coding"
_VALID_SURVIVAL_PROFILES = frozenset({_DEFAULT_SURVIVAL_PROFILE})
_COMPACTION_CONFIG_ENV = "QZ_COMPACTION_CONFIG"
_COMPACTION_PROFILE_ENV = "QZ_COMPACTION_PROFILE"
_COMPACTION_SURVIVAL_PROFILE_ENV = "QZ_COMPACTION_SURVIVAL_PROFILE"
_LLM_COMPACT_DISABLE_REASONING_ENV = "QZ_LLM_COMPACT_DISABLE_REASONING"
_REQUIRED_ANCHORED_HEADINGS = (
    "## Goal",
    "## Active Constraints & Guardrails",
    "## Current Status",
    "## Key Decisions",
    "## Evidence Boundaries",
    "## Technical State",
    "## Next Actions",
)


def _coerce_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (ValueError, TypeError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _get_env_int_from_mapping(env, key: str, default: int) -> int:
    try:
        val = env.get(key)
        if val is None:
            return default
        return _coerce_positive_int(val, default)
    except (ValueError, TypeError):
        return default


def _get_env_int(key: str, default: int) -> int:
    return _get_env_int_from_mapping(os.environ, key, default)


def _normalize_compaction_mode(mode) -> str:
    if not isinstance(mode, str):
        return "heuristic"
    normalized = mode.strip().lower()
    return normalized if normalized in _VALID_COMPACTION_MODES else "heuristic"


def _normalize_survival_profile(value) -> str:
    if not isinstance(value, str):
        return _DEFAULT_SURVIVAL_PROFILE
    normalized = value.strip().lower()
    return normalized if normalized in _VALID_SURVIVAL_PROFILES else _DEFAULT_SURVIVAL_PROFILE


def _current_compaction_mode() -> str:
    return _normalize_compaction_mode(COMPACTION_CONFIG.get("mode"))


def _positive_config_int(key: str, default: int) -> int:
    return _coerce_positive_int(COMPACTION_CONFIG.get(key), default)


def _current_survival_profile() -> str:
    return _normalize_survival_profile(COMPACTION_CONFIG.get("survival_profile"))


def _score_survival_text(text: str):
    # Stage 5 reserves survival_profile as a selector; only coding is implemented.
    _current_survival_profile()
    return score_text(text)


def _qz_root(env=None):
    env = os.environ if env is None else env
    raw = env.get("QZ_ROOT")
    if isinstance(raw, str) and raw.strip():
        return os.path.abspath(os.path.expanduser(raw.strip()))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _default_prompt_file(env=None) -> str:
    env = os.environ if env is None else env
    return os.path.join(env.get("QZ_ROOT", ""), "config/default/prompts/compact-v0.md")


def _default_compaction_json_path(env=None, root=None) -> str:
    root_path = root or _qz_root(env)
    return os.path.join(str(root_path), "config/default/compaction.json")


def _default_compaction_config(env=None) -> dict:
    env = os.environ if env is None else env
    return {
        "keep_recent_items": _get_env_int_from_mapping(env, "QZ_COMPACT_KEEP_RECENT", 20),
        "min_preserve_items": _get_env_int_from_mapping(env, "QZ_COMPACT_MIN_PRESERVE", 6),
        "max_summary_chars": _get_env_int_from_mapping(env, "QZ_COMPACT_MAX_SUMMARY_CHARS", 48000),
        "max_tool_output_chars": _get_env_int_from_mapping(env, "QZ_COMPACT_MAX_TOOL_OUTPUT_CHARS", 3200),
        "max_item_summary_chars": _get_env_int_from_mapping(env, "QZ_COMPACT_MAX_ITEM_CHARS", 2000),
        "max_compaction_depth": _get_env_int_from_mapping(env, "QZ_COMPACT_MAX_DEPTH", 8),
        "target_output_tokens": _get_env_int_from_mapping(env, "QZ_COMPACT_TARGET_TOKENS", 56000),
        "mode": "heuristic",
        "survival_profile": _DEFAULT_SURVIVAL_PROFILE,
        "llm_base_url": "",
        "llm_model": "",
        "llm_timeout_sec": _DEFAULT_LLM_TIMEOUT_SEC,
        "llm_max_input_chars": _DEFAULT_LLM_MAX_INPUT_CHARS,
        "llm_max_output_tokens": _DEFAULT_LLM_MAX_OUTPUT_TOKENS,
        "llm_disable_reasoning": _DEFAULT_LLM_DISABLE_REASONING,
        "prompt_file": _default_prompt_file(env),
    }


def _load_compaction_json(path) -> dict:
    if not isinstance(path, str) or not path.strip():
        return {}
    try:
        with open(os.path.expanduser(path.strip()), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _select_compaction_profile(config: dict, profile_name: str) -> dict:
    if not isinstance(config, dict):
        return {}
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        return {}

    requested = profile_name if isinstance(profile_name, str) and profile_name.strip() else "default"
    for name in (requested.strip(), "default"):
        profile = profiles.get(name)
        if isinstance(profile, dict):
            return dict(profile)
    return {}


def _apply_profile_compaction_values(merged: dict, profile: dict) -> dict:
    if not isinstance(profile, dict):
        return merged

    if "mode" in profile:
        merged["mode"] = _normalize_compaction_mode(profile.get("mode"))
    if "survival_profile" in profile:
        merged["survival_profile"] = _normalize_survival_profile(profile.get("survival_profile"))

    for key in ("prompt_file", "llm_base_url", "llm_model"):
        if key not in profile:
            continue
        value = profile.get(key)
        if isinstance(value, str):
            merged[key] = value.strip().rstrip("/") if key == "llm_base_url" else value.strip()

    int_defaults = {
        "llm_timeout_sec": _DEFAULT_LLM_TIMEOUT_SEC,
        "llm_max_input_chars": _DEFAULT_LLM_MAX_INPUT_CHARS,
        "llm_max_output_tokens": _DEFAULT_LLM_MAX_OUTPUT_TOKENS,
    }
    for key, default in int_defaults.items():
        if key in profile:
            merged[key] = _coerce_positive_int(profile.get(key), default)
    return merged


def _apply_env_compaction_overrides(merged: dict, env) -> dict:
    env = os.environ if env is None else env
    if "QZCOMPACT" in env:
        merged["mode"] = _normalize_compaction_mode(env.get("QZCOMPACT"))
    if _COMPACTION_SURVIVAL_PROFILE_ENV in env:
        merged["survival_profile"] = _normalize_survival_profile(env.get(_COMPACTION_SURVIVAL_PROFILE_ENV))

    string_env_map = {
        "QZ_LLM_COMPACT_BASE_URL": "llm_base_url",
        "QZ_LLM_COMPACT_MODEL": "llm_model",
        "QZ_LLM_COMPACT_PROMPT_FILE": "prompt_file",
    }
    for env_key, cfg_key in string_env_map.items():
        if env_key not in env:
            continue
        value = env.get(env_key)
        if isinstance(value, str):
            merged[cfg_key] = value.strip().rstrip("/") if cfg_key == "llm_base_url" else value.strip()

    int_env_map = {
        "QZ_LLM_COMPACT_TIMEOUT_SEC": ("llm_timeout_sec", _DEFAULT_LLM_TIMEOUT_SEC),
        "QZ_LLM_COMPACT_MAX_INPUT_CHARS": ("llm_max_input_chars", _DEFAULT_LLM_MAX_INPUT_CHARS),
        "QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS": ("llm_max_output_tokens", _DEFAULT_LLM_MAX_OUTPUT_TOKENS),
    }
    for env_key, (cfg_key, default) in int_env_map.items():
        if env_key in env:
            merged[cfg_key] = _get_env_int_from_mapping(env, env_key, default)
    if _LLM_COMPACT_DISABLE_REASONING_ENV in env:
        merged["llm_disable_reasoning"] = _coerce_bool(
            env.get(_LLM_COMPACT_DISABLE_REASONING_ENV),
            _DEFAULT_LLM_DISABLE_REASONING,
        )
    return merged


def _merge_compaction_config(defaults: dict, profile: dict, env=None) -> dict:
    merged = dict(defaults or _default_compaction_config(env))
    merged = _apply_profile_compaction_values(merged, profile)
    merged = _apply_env_compaction_overrides(merged, os.environ if env is None else env)
    merged["mode"] = _normalize_compaction_mode(merged.get("mode"))
    merged["survival_profile"] = _normalize_survival_profile(merged.get("survival_profile"))
    return merged


def _get_effective_compaction_config(env=None, root=None, config_path=None, profile_name=None) -> dict:
    env = os.environ if env is None else env
    defaults = _default_compaction_config(env)
    selected_config_path = (
        config_path
        or (env.get(_COMPACTION_CONFIG_ENV) if isinstance(env.get(_COMPACTION_CONFIG_ENV), str) and env.get(_COMPACTION_CONFIG_ENV).strip() else None)
        or _default_compaction_json_path(env, root)
    )
    data = _load_compaction_json(selected_config_path)
    selected_profile = profile_name
    if selected_profile is None:
        selected_profile = env.get(_COMPACTION_PROFILE_ENV, "default")
    profile = _select_compaction_profile(data, selected_profile)
    return _merge_compaction_config(defaults, profile, env)

# Item types that are proxy-generated and should be excluded from old-history
# compaction summaries and microcompaction replays. Proxy-local tool types are
# derived from the registry so new tools don't require edits here.
_PROXY_LOCAL_ITEM_TYPES: frozenset[str] = frozenset(
    spec.public_item_type
    for spec in DEFAULT_TOOL_REGISTRY.specs()
    if spec.execution == "proxy_local" and spec.public_item_type
)
# Reasoning items come from upstream and are also excluded; they are not
# managed by the tool registry.
_UPSTREAM_TRANSIENT_ITEM_TYPES: frozenset[str] = frozenset({"reasoning"})
_COMPACTION_DROP_TYPES: frozenset[str] = _PROXY_LOCAL_ITEM_TYPES | _UPSTREAM_TRANSIENT_ITEM_TYPES

LOCAL_COMPACTION_PREFIX = "localcmp:v2:"
COMPACTION_CONFIG = _get_effective_compaction_config()

FUNCTION_CALL_TYPES = {"function_call", "computer_call", "code_interpreter_call", "custom_tool_call"}
FUNCTION_OUTPUT_TYPES = {
    "function_call_output",
    "computer_call_output",
    "custom_tool_call_output",
    "tool_result",
    "tool_output",
}


def normalize_tool_output_for_codex(output_items):
    return DEFAULT_TOOL_REGISTRY.output_items_to_codex(output_items)


def extract_response_output_text(out: dict) -> str:
    texts = []
    for item in out.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts).strip()


def _now_ts() -> int:
    import time
    return int(time.time())


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _approx_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


_AUTOCOMPACT_BUFFER_RATIO = 0.165
_COMPACT_THRESHOLD_PLAUSIBILITY_FLOOR = 1024


def _resolve_llm_compaction_budget(
    context_window_tokens: Optional[int],
    old_history_estimated_tokens: int = 0,
    survival_hint_count: int = 0,
    compact_threshold_tokens: Optional[int] = None,
    env=None,
) -> dict:
    """Derive effective LLM compaction limits from the selected model's context window.

    Budget policy (context_window_autocompact_buffer_v1):
      autocompact_buffer_tokens = floor(context_window_tokens * 0.165)
      safe_compaction_budget_tokens = context_window_tokens - autocompact_buffer_tokens

    For a 256k window (262144 tokens):
      autocompact_buffer_tokens = 43253
      safe_compaction_budget_tokens = 218891

    Request compact_threshold handling (compact_threshold_tokens):
      - None: no client threshold; effective = safe_compaction_budget_tokens.
      - < 1024: treated as force/test signal (e.g. compact_threshold=1 from dogfood
                runner); not used as a budget cap.
      - <= safe budget: used as a budget cap (role "budget_cap").
      - > safe budget: capped to safe budget (role "capped_to_safe_budget").
      - <= 0 or non-int: ignored (role "ignored_invalid").

    Env overrides (QZ_LLM_COMPACT_TIMEOUT_SEC, QZ_LLM_COMPACT_MAX_INPUT_CHARS,
    QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS) win over derived values when set.

    Returns a dict with all effective limits plus metadata. If context_window_tokens is
    missing or non-positive, fail_v3=True is set and v3 compaction must not proceed.
    """
    env = os.environ if env is None else env

    base: dict = {
        "old_history_estimated_tokens": old_history_estimated_tokens,
        "survival_hint_count": survival_hint_count,
        "env_overrides": {"timeout_sec": False, "max_input_chars": False, "max_output_tokens": False},
    }

    if not isinstance(context_window_tokens, int) or context_window_tokens <= 0:
        return {
            **base,
            "policy": "missing_context_window",
            "budget_policy": "missing_context_window",
            "fail_v3": True,
            "context_source": None,
            "context_window_tokens": None,
            "autocompact_buffer_ratio": None,
            "autocompact_buffer_tokens": None,
            "safe_compaction_budget_tokens": None,
            "client_compact_threshold_tokens": None,
            "client_compact_threshold_role": None,
            "client_compact_threshold_used": False,
            "effective_compaction_budget_tokens": None,
            "compaction_budget_tokens": None,
            "effective_max_input_tokens": None,
            "effective_max_input_chars": None,
            "effective_max_output_tokens": None,
            "effective_timeout_sec": None,
        }

    autocompact_buffer_tokens = int(context_window_tokens * _AUTOCOMPACT_BUFFER_RATIO)
    safe_compaction_budget_tokens = context_window_tokens - autocompact_buffer_tokens

    # Determine compact_threshold role and effective budget.
    client_compact_threshold_tokens_out: Optional[int] = None
    client_compact_threshold_role: Optional[str] = None
    client_compact_threshold_used: bool = False
    effective_compaction_budget_tokens: int = safe_compaction_budget_tokens

    if compact_threshold_tokens is not None:
        if not isinstance(compact_threshold_tokens, int) or compact_threshold_tokens <= 0:
            client_compact_threshold_tokens_out = (
                compact_threshold_tokens if isinstance(compact_threshold_tokens, int) else None
            )
            client_compact_threshold_role = "ignored_invalid"
        elif compact_threshold_tokens < _COMPACT_THRESHOLD_PLAUSIBILITY_FLOOR:
            client_compact_threshold_tokens_out = compact_threshold_tokens
            client_compact_threshold_role = "force_compaction_only"
        elif compact_threshold_tokens > safe_compaction_budget_tokens:
            client_compact_threshold_tokens_out = compact_threshold_tokens
            client_compact_threshold_role = "capped_to_safe_budget"
        else:
            client_compact_threshold_tokens_out = compact_threshold_tokens
            client_compact_threshold_role = "budget_cap"
            effective_compaction_budget_tokens = compact_threshold_tokens
            client_compact_threshold_used = True

    # Derived limits — scale with effective budget, degrade gracefully for small windows.
    # 4% of budget for output tokens, capped at 8192, floored at the static default.
    derived_max_output_tokens = min(8192, max(
        _DEFAULT_LLM_MAX_OUTPUT_TOKENS,
        int(effective_compaction_budget_tokens * 0.04),
    ))
    # 20% of budget (in token-count) → converted to chars for the prompt input.
    derived_max_input_tokens = max(
        _DEFAULT_LLM_MAX_INPUT_CHARS // 4,
        int(effective_compaction_budget_tokens * 0.20),
    )
    derived_max_input_chars = derived_max_input_tokens * 4
    # Timeout scales between 30 and 120 seconds.
    derived_timeout_sec = max(
        _DEFAULT_LLM_TIMEOUT_SEC,
        min(int(effective_compaction_budget_tokens / 2000), 120),
    )

    # Env overrides still win over all derived values.
    timeout_override = "QZ_LLM_COMPACT_TIMEOUT_SEC" in env
    input_override = "QZ_LLM_COMPACT_MAX_INPUT_CHARS" in env
    output_override = "QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS" in env

    if timeout_override:
        effective_timeout_sec = _get_env_int_from_mapping(env, "QZ_LLM_COMPACT_TIMEOUT_SEC", derived_timeout_sec)
    else:
        effective_timeout_sec = derived_timeout_sec
    if input_override:
        effective_max_input_chars = _get_env_int_from_mapping(env, "QZ_LLM_COMPACT_MAX_INPUT_CHARS", derived_max_input_chars)
    else:
        effective_max_input_chars = derived_max_input_chars
    if output_override:
        effective_max_output_tokens = _get_env_int_from_mapping(env, "QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS", derived_max_output_tokens)
    else:
        effective_max_output_tokens = derived_max_output_tokens

    return {
        **base,
        "policy": "context_window_autocompact_buffer_v1",
        "budget_policy": "context_window_autocompact_buffer_v1",
        "fail_v3": False,
        "context_source": "selected_model.context_window",
        "context_window_tokens": context_window_tokens,
        "autocompact_buffer_ratio": _AUTOCOMPACT_BUFFER_RATIO,
        "autocompact_buffer_tokens": autocompact_buffer_tokens,
        "safe_compaction_budget_tokens": safe_compaction_budget_tokens,
        "client_compact_threshold_tokens": client_compact_threshold_tokens_out,
        "client_compact_threshold_role": client_compact_threshold_role,
        "client_compact_threshold_used": client_compact_threshold_used,
        "effective_compaction_budget_tokens": effective_compaction_budget_tokens,
        "compaction_budget_tokens": effective_compaction_budget_tokens,
        "effective_max_input_tokens": effective_max_input_chars // 4,
        "effective_max_input_chars": effective_max_input_chars,
        "effective_max_output_tokens": effective_max_output_tokens,
        "effective_timeout_sec": effective_timeout_sec,
        "env_overrides": {
            "timeout_sec": timeout_override,
            "max_input_chars": input_override,
            "max_output_tokens": output_override,
        },
    }


def _decode_local_compaction_blob(blob: str):
    if not isinstance(blob, str):
        return None
    
    prefix = ""
    for p in ("localcmp:v3:", "localcmp:v2:", "localcmp:v1:"):
        if blob.startswith(p):
            prefix = p
            break
            
    if not prefix:
        return None
        
    raw = blob[len(prefix):]
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _encode_local_compaction_blob(payload: dict) -> str:
    version = payload.get("version", 2)
    prefix = f"localcmp:v{version}:"
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return prefix + encoded


def _make_input_text_message(role: str, text: str) -> dict:
    part_type = "output_text" if role == "assistant" else "input_text"
    part = {"type": part_type, "text": text}
    if part_type == "output_text":
        part["annotations"] = []
    return {
        "type": "message",
        "role": role,
        "content": [part],
    }


def _item_text(item: dict) -> str:
    if not isinstance(item, dict):
        return _normalize_ws(str(item))

    item_type = item.get("type")
    if item_type == "message":
        text = _normalize_ws(_content_to_text(item.get("content")))
        if not text:
            return ""
        lower = text.lower()
        if CHECKPOINT_MARKER in text or any(marker in lower for marker in HARNESS_TEXT_MARKERS):
            return ""
        if item.get("role") == "user" and any(marker in lower for marker in META_USER_TEXT_MARKERS):
            return ""
        if item.get("role") == "assistant" and any(marker in lower for marker in META_ASSISTANT_TEXT_MARKERS):
            return ""
        role = item.get("role", "unknown")
        return f"{role}: {text}"

    if item_type in _COMPACTION_DROP_TYPES:
        return ""

    if item_type in FUNCTION_CALL_TYPES:
        name = item.get("name") or item.get("call_id") or "function"
        arguments = _truncate(_normalize_ws(item.get("arguments") or ""), COMPACTION_CONFIG["max_item_summary_chars"])
        return f"tool call {name}: {arguments}" if arguments else f"tool call {name}"

    if item_type in FUNCTION_OUTPUT_TYPES:
        name = item.get("name") or item.get("call_id") or "tool output"
        output = _truncate(_normalize_ws(_content_to_text(item.get("content")) or item.get("output") or item.get("result") or ""), COMPACTION_CONFIG["max_tool_output_chars"])
        return f"tool result {name}: {output}" if output else f"tool result {name}"

    if item_type == "compaction":
        payload = _decode_local_compaction_blob(item.get("encrypted_content", ""))
        if payload:
            return _normalize_ws(payload.get("summary_text", ""))
        return "compacted earlier context"

    text = _normalize_ws(_content_to_text(item.get("content")))
    if text:
        return text
    return _normalize_ws(json.dumps(item, sort_keys=True))


def _is_tool_like(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    item_type = item.get("type")
    if item_type in FUNCTION_CALL_TYPES or item_type in FUNCTION_OUTPUT_TYPES:
        return True
    return item.get("role") == "tool"


def _tail_start_for_compaction(items):
    keep_recent = COMPACTION_CONFIG["keep_recent_items"]
    if len(items) <= keep_recent:
        return 0
    start = max(0, len(items) - keep_recent)
    while start > 0 and _is_tool_like(items[start]):
        start -= 1
    if start > 0 and items[start - 1].get("type") in FUNCTION_CALL_TYPES and _is_tool_like(items[start]):
        start -= 1
    return start


def _tool_output_signal(item: dict) -> str:
    """Extract a brief, informative signal from a tool output for the compaction
    placeholder. Preserves success/failure status and first-line error context
    so the model retains useful history even after the full output is dropped.
    """
    name = item.get("name") or item.get("call_id") or "tool"
    raw = item.get("output") or item.get("result") or ""
    if not isinstance(raw, str):
        raw = ""

    # Try JSON payloads first (apply_patch, web_search, coercion errors).
    try:
        import json as _json
        parsed = _json.loads(raw)
        if isinstance(parsed, dict):
            ok = parsed.get("ok")
            error = parsed.get("error") or ""
            status = parsed.get("status") or ""
            if ok is False or error:
                snippet = str(error)[:200] if error else str(parsed)[:200]
                return f"Tool {name}: FAILED — {snippet}"
            if status == "failed":
                snippet = str(parsed.get("output") or parsed)[:200]
                return f"Tool {name}: FAILED — {snippet}"
            return f"Tool {name}: completed OK."
    except Exception:
        pass

    # Raw string output — look for exit code signals and error keywords.
    if not raw.strip():
        return f"Tool {name}: completed (no output)."

    first_line = raw.strip().splitlines()[0][:200]
    lower = raw.lower()
    has_error = any(k in lower for k in ("error:", "exception:", "traceback", "failed:", "exit 1", "exit code"))
    if has_error:
        return f"Tool {name}: FAILED. Output (compacted): {first_line}"
    return f"Tool {name}: completed. Output (compacted): {first_line}"


def _microcompact_old_tool_results(items):
    if not isinstance(items, list):
        return items
    start = _tail_start_for_compaction(items)
    compacted = []
    for idx, item in enumerate(items):
        if idx >= start or not isinstance(item, dict):
            compacted.append(item)
            continue
        item_type = item.get("type")
        if item_type in FUNCTION_OUTPUT_TYPES or item.get("role") == "tool":
            placeholder = _make_input_text_message(
                "assistant",
                _tool_output_signal(item),
            )
            compacted.append(placeholder)
            continue
        compacted.append(item)
    return compacted


def _expand_local_compaction_items(items):
    if not isinstance(items, list):
        return items
    expanded = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "compaction":
            encrypted = item.get("encrypted_content", "")
            is_local = isinstance(encrypted, str) and (
                encrypted.startswith("localcmp:v3:") or 
                encrypted.startswith("localcmp:v2:") or 
                encrypted.startswith("localcmp:v1:")
            )
            if not is_local:
                # Native or unknown compaction.
                expanded.append(item)
                continue

            payload = _decode_local_compaction_blob(encrypted)
            if payload:
                summary_text = _normalize_ws(payload.get("summary_text", ""))
                if summary_text:
                    # Keep local compaction summaries alive.
                    expanded.append(_make_input_text_message(
                        "user",
                        summary_text,
                    ))
                    continue
                expanded.append(item)
                continue
        expanded.append(item)
    return expanded


def _extract_previous_anchored_summary(items: list) -> str:
    """Extract the summary text from the most recent local compaction item."""
    for item in reversed(items):
        if not isinstance(item, dict) or item.get("type") != "compaction":
            continue
        payload = _decode_local_compaction_blob(item.get("encrypted_content", ""))
        if payload and payload.get("summary_text"):
            return payload["summary_text"]
    return ""


def _build_survival_weighted_compaction_prompt(
    previous_summary: str,
    new_items: list,
    max_input_chars: Optional[int] = None,
) -> str:
    """Construct the LLM prompt using the Stage 1 template and Stage 2.1 hints.

    When max_input_chars is provided (from the budget resolver), it overrides the
    COMPACTION_CONFIG value.  Pass None to fall back to COMPACTION_CONFIG as before.
    """
    prompt_file = COMPACTION_CONFIG["prompt_file"]
    template = ""
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            template = f.read()
    except Exception:
        # Fallback to a minimal built-in template if file is missing
        template = "Previous anchored summary, if any:\n{{PREVIOUS_ANCHORED_SUMMARY}}\n\nNew conversation/items to compact:\n{{NEW_CONVERSATION}}"

    # Format new conversation items for the prompt.
    new_convo_text = []
    for item in new_items:
        text = _item_text(item)
        if text:
            new_convo_text.append(text)

    raw_convo_body = "\n".join(new_convo_text)

    # Generate hints from the full normalized body, then cap the raw body while
    # keeping those exact-atom hints at the end of the prompt.
    spans = _score_survival_text(raw_convo_body)
    hints = format_survival_hints(spans, max_spans=40)
    if max_input_chars is None or not (isinstance(max_input_chars, int) and max_input_chars > 0):
        max_input_chars = _positive_config_int("llm_max_input_chars", _DEFAULT_LLM_MAX_INPUT_CHARS)
    if hints:
        hint_block = f"\n\n### Preservation Hints (Survival Weighting)\n{hints}"
        if len(hint_block) >= max_input_chars:
            convo_body = _truncate(hint_block, max_input_chars)
        else:
            body_limit = max_input_chars - len(hint_block)
            convo_body = _truncate(raw_convo_body, body_limit) + hint_block
    else:
        convo_body = _truncate(raw_convo_body, max_input_chars)

    prompt = template.replace("{{PREVIOUS_ANCHORED_SUMMARY}}", previous_summary or "(none)")
    prompt = prompt.replace("{{NEW_CONVERSATION}}", convo_body)
    
    return prompt


def _validate_anchored_summary(text: str) -> bool:
    """Verify that the LLM output contains required canonical headings."""
    if not isinstance(text, str):
        return False

    stripped = text.strip()
    if not stripped or len(stripped) < 80:
        return False

    if stripped.startswith("```") and stripped.endswith("```"):
        return False
    
    # Reject if it's just repeating the template placeholders
    if "{{NEW_CONVERSATION}}" in stripped or "{{PREVIOUS_ANCHORED_SUMMARY}}" in stripped:
        return False

    return all(
        re.search(rf"(?m)^{re.escape(heading)}\s*$", stripped)
        for heading in _REQUIRED_ANCHORED_HEADINGS
    )


def _strip_openai_v1_path(path: str) -> str:
    stripped = (path or "").rstrip("/")
    if stripped.endswith("/v1"):
        stripped = stripped[:-3].rstrip("/")
    return stripped


def _canonical_url_identity(raw_url: str):
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    try:
        parsed = urllib.parse.urlparse(raw_url.strip().rstrip("/"))
        if not parsed.scheme or not parsed.hostname:
            return None
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower()
        port = parsed.port
    except ValueError:
        return None

    if port is None:
        port = 443 if scheme == "https" else 80
    return (scheme, hostname, port, _strip_openai_v1_path(parsed.path))


def _active_backend_base_url(env=None) -> str:
    """Return the active llama-server backend URL derived from the same env vars as the proxy.

    Uses QZ_SERVER_HOST (default: 127.0.0.1) and QZ_SERVER_PORT (default: 18084),
    matching the --upstream argument constructed by scripts/qz-proxy.

    This is the default LLM backend for Zenkai v3 compaction when QZ_LLM_COMPACT_BASE_URL
    is not explicitly set. It avoids requiring manual configuration in compaction.json
    for normal operation.

    Returns empty string if the env vars indicate an unusable target (e.g. blank host/port).
    """
    env = os.environ if env is None else env
    host = env.get("QZ_SERVER_HOST", "127.0.0.1").strip()
    port = env.get("QZ_SERVER_PORT", "18084").strip()
    if not host or not port:
        return ""
    try:
        int(port)
    except (ValueError, TypeError):
        return ""
    return f"http://{host}:{port}"


_QZ_PROXY_DEFAULT_PORTS = ("18180", "18183")


def _is_probably_quantzhai_proxy_url(base_url: str, env=None) -> bool:
    """Conservatively reject known QuantZhai proxy URLs to avoid recursion.

    Checks env-configured proxy URLs, the hardcoded default proxy ports (18180, 18183),
    and CODEX_OSS_BASE_URL. This ensures the compactor never calls the QuantZhai proxy
    itself even when QZ_PROXY_HOST/QZ_PROXY_PORT env vars are not explicitly set.
    """
    env = os.environ if env is None else env
    base_identity = _canonical_url_identity(base_url)
    if not base_identity:
        return False

    candidate_urls = [
        env.get("CODEX_OSS_BASE_URL", ""),
        env.get("QZ_PROXY_BASE_URL", ""),
    ]
    # Always check known proxy default ports against common loopback addresses,
    # regardless of whether QZ_PROXY_HOST/QZ_PROXY_PORT env vars are set.
    for default_port in _QZ_PROXY_DEFAULT_PORTS:
        for loopback in ("127.0.0.1", "localhost", "::1"):
            candidate_urls.append(f"http://{loopback}:{default_port}")
    # Also check the configured proxy host:port from env vars.
    proxy_host = env.get("QZ_PROXY_HOST", "127.0.0.1")
    proxy_port = env.get("QZ_PROXY_PORT", "18180")
    if proxy_host and proxy_port:
        candidate_urls.append(f"http://{proxy_host}:{proxy_port}")

    for candidate_url in candidate_urls:
        candidate_identity = _canonical_url_identity(candidate_url)
        if candidate_identity and base_identity == candidate_identity:
            return True
    return False


def _llm_chat_completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if _strip_openai_v1_path(parsed.path) != (parsed.path or "").rstrip("/"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _extract_llm_compactor_content(data) -> Optional[str]:
    if not isinstance(data, dict):
        return None

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                content = message["content"].strip()
                return content or None
            if isinstance(first.get("text"), str):
                content = first["text"].strip()
                return content or None

    for key in ("content", "response"):
        if isinstance(data.get(key), str):
            content = data[key].strip()
            return content or None

    return None


def _build_llm_compactor_payload(prompt: str, max_output_tokens: Optional[int] = None) -> dict:
    """Build the chat-completions payload for the LLM compactor.

    When max_output_tokens is provided (from the budget resolver), it overrides the
    COMPACTION_CONFIG value.  Pass None to fall back to COMPACTION_CONFIG as before.
    """
    if isinstance(max_output_tokens, int) and max_output_tokens > 0:
        tokens = max_output_tokens
    else:
        tokens = _positive_config_int("llm_max_output_tokens", _DEFAULT_LLM_MAX_OUTPUT_TOKENS)
    payload = {
        "model": COMPACTION_CONFIG["llm_model"] or "local-model",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise context compaction engine for a coding agent. "
                    "Return only the final anchored summary in message.content. "
                    "Do not put the summary in reasoning_content. "
                    "Preserve exact technical atoms."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": tokens,
        "stream": False,
    }
    if _coerce_bool(COMPACTION_CONFIG.get("llm_disable_reasoning"), _DEFAULT_LLM_DISABLE_REASONING):
        # llama.cpp's OAI path reads thinking_budget_tokens; mirror the newer
        # name for backend compatibility. This is compactor-only and opt-in.
        payload["thinking_budget_tokens"] = 0
        payload["reasoning_budget_tokens"] = 0
    return payload


def _call_llm_compactor(
    prompt: str,
    timeout_sec: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
) -> Optional[str]:
    """Execute a direct call to the local LLM backend for compaction.

    When timeout_sec / max_output_tokens are provided (from the budget resolver),
    they override COMPACTION_CONFIG values.  Pass None to fall back to COMPACTION_CONFIG.

    Backend URL resolution (Stage 6.10.2):
    1. QZ_LLM_COMPACT_BASE_URL env var or compaction.json llm_base_url — explicit override.
    2. _active_backend_base_url() — derived from QZ_SERVER_HOST:QZ_SERVER_PORT, same as
       the proxy's --upstream argument. This is the default for normal operation so that
       Zenkai v3 LLM calls reach the llama-server directly without manual config.
    3. Recursion guard: rejects any URL matching known QuantZhai proxy ports (18180, 18183)
       or CODEX_OSS_BASE_URL. The proxy must never be its own compaction backend.
    """
    base_url = str(COMPACTION_CONFIG.get("llm_base_url", "") or "").rstrip("/")
    if not base_url:
        # Fall back to the active llama-server backend derived from env vars.
        # This allows v3 LLM compaction to work without explicit QZ_LLM_COMPACT_BASE_URL.
        base_url = _active_backend_base_url()
    if not base_url:
        return None
    if _is_probably_quantzhai_proxy_url(base_url):
        return None

    url = _llm_chat_completions_url(base_url)
    payload = _build_llm_compactor_payload(prompt, max_output_tokens=max_output_tokens)

    if isinstance(timeout_sec, int) and timeout_sec > 0:
        effective_timeout = timeout_sec
    else:
        effective_timeout = _positive_config_int("llm_timeout_sec", _DEFAULT_LLM_TIMEOUT_SEC)

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return _extract_llm_compactor_content(data)
    except Exception:
        # Fallback to heuristic is handled by caller
        return None


def _build_local_compaction_response_v3(
    body: dict,
    working_items: list,
    existing_depth: int,
    selected_context_tokens: Optional[int] = None,
    compact_threshold_tokens: Optional[int] = None,
    remote_compaction: bool = False,
) -> tuple:
    """Attempt LLM-generated anchored compaction (v3).

    Returns a 2-tuple (result_dict, reason_str):
      (result_dict, "ok")             — success
      (None, "mode_not_llm_or_auto")  — compaction mode is not llm or auto
      (None, "no_backend")            — no LLM backend configured or derivable
      (None, "empty_llm_source_items") — nothing to summarise (e.g. short history)
      (None, "missing_context_window") — context window unavailable; budget fail_v3
      (None, "llm_call_failed")       — LLM backend returned no usable content
      (None, "validation_failed")     — summary text failed anchored-v0 validation

    selected_context_tokens must be the context_window value from the selected model.
    If it is None or non-positive the budget resolver sets fail_v3=True → reason
    "missing_context_window", falling through to heuristic v2.

    compact_threshold_tokens is the raw value from context_management.compact_threshold
    in the request body. It is classified by the budget resolver (force_compaction_only,
    budget_cap, capped_to_safe_budget, or ignored_invalid). A value below 1024 (e.g.
    compact_threshold=1 from the dogfood runner) is treated as a force signal, not a
    1-token budget cap.

    remote_compaction=True: use all working_items as the LLM compaction source.
    Codex has already decided what to compact and sent it as request.input; no
    recent tail is preserved in the output (the compaction blob is the full replacement).

    remote_compaction=False (default): normal incremental compaction — only the
    pre-recent-tail slice ("older") is summarised; the recent tail is preserved
    verbatim in the output.
    """
    if _current_compaction_mode() not in ("llm", "auto"):
        return None, "mode_not_llm_or_auto"
    # Allow v3 to proceed when no explicit llm_base_url is configured: _call_llm_compactor()
    # will fall back to _active_backend_base_url() (QZ_SERVER_HOST:QZ_SERVER_PORT).
    # Only bail out if neither configured nor active backend is reachable (both empty).
    if not COMPACTION_CONFIG["llm_base_url"] and not _active_backend_base_url():
        return None, "no_backend"

    if remote_compaction:
        # Remote compact path (/v1/responses/compact): Codex has already selected all
        # items for compaction. Summarise everything; no recent tail to preserve.
        llm_source = working_items
        recent = []
    else:
        # Normal incremental path (/v1/responses via compact_threshold): only compress
        # the pre-recent-tail "older" slice; keep the recent tail verbatim.
        tail_start = _tail_start_for_compaction(working_items)
        llm_source = working_items[:tail_start]
        recent = working_items[tail_start:]
        if len(recent) < COMPACTION_CONFIG["min_preserve_items"]:
            recent = working_items[-COMPACTION_CONFIG["min_preserve_items"]:]
            llm_source = working_items[:-len(recent)] if recent else working_items

    if not llm_source:
        # Nothing to summarise — the entire history falls within the preserved recent
        # tail (normal mode) or the input was empty (remote mode). Fall back to v2.
        return None, "empty_llm_source_items"

    # Resolve compaction budget from selected model's context window.
    combined_source_text = "\n".join(_item_text(it) for it in llm_source)
    spans = _score_survival_text(combined_source_text)
    budget = _resolve_llm_compaction_budget(
        selected_context_tokens,
        old_history_estimated_tokens=_estimate_items_tokens(llm_source),
        survival_hint_count=len(spans),
        compact_threshold_tokens=compact_threshold_tokens,
    )
    if budget["fail_v3"]:
        # Context window metadata unavailable — fail closed to v2.
        return None, "missing_context_window"

    previous_summary = _extract_previous_anchored_summary(llm_source)
    prompt = _build_survival_weighted_compaction_prompt(
        previous_summary,
        llm_source,
        max_input_chars=budget["effective_max_input_chars"],
    )

    start_ms = int(time.time() * 1000)
    summary_text = _call_llm_compactor(
        prompt,
        timeout_sec=budget["effective_timeout_sec"],
        max_output_tokens=budget["effective_max_output_tokens"],
    )
    latency_ms = int(time.time() * 1000) - start_ms

    if not summary_text:
        return None, "llm_call_failed"
    if not _validate_anchored_summary(summary_text):
        return None, "validation_failed"

    depth = min(existing_depth + 1, COMPACTION_CONFIG["max_compaction_depth"])

    payload = {
        "version": 3,
        "source": "turboquant-local",
        "engine": "anchored-llm",
        "schema_version": "anchored-v0",
        "depth": depth,
        "created_at": _now_ts(),
        "summary_text": summary_text,
        "preserved_items": len(recent),
        "metadata": {
            "format": "anchored-markers-v3",
            "fallback": False,
            "prompt": "compact-v0",
            "survival_hint_count": len(spans),
            "llm_model": COMPACTION_CONFIG["llm_model"],
            "latency_ms": latency_ms,
            "remote_compaction": remote_compaction,
            "budget": {
                "policy": budget["policy"],
                "context_window_tokens": budget["context_window_tokens"],
                "autocompact_buffer_ratio": budget["autocompact_buffer_ratio"],
                "autocompact_buffer_tokens": budget["autocompact_buffer_tokens"],
                "safe_compaction_budget_tokens": budget["safe_compaction_budget_tokens"],
                "client_compact_threshold_tokens": budget["client_compact_threshold_tokens"],
                "client_compact_threshold_role": budget["client_compact_threshold_role"],
                "client_compact_threshold_used": budget["client_compact_threshold_used"],
                "effective_compaction_budget_tokens": budget["effective_compaction_budget_tokens"],
                "compaction_budget_tokens": budget["compaction_budget_tokens"],
                "effective_max_input_chars": budget["effective_max_input_chars"],
                "effective_max_output_tokens": budget["effective_max_output_tokens"],
                "effective_timeout_sec": budget["effective_timeout_sec"],
                "env_overrides": budget["env_overrides"],
            },
        },
    }
    encrypted = _encode_local_compaction_blob(payload)

    recent_clean = [
        item for item in recent
        if not (isinstance(item, dict) and item.get("type") == "compaction")
        and not _is_local_checkpoint_prompt(item)
    ]

    output_items = [
        {
            "type": "compaction",
            "id": f"cmp_local_v3_{_now_ts()}",
            "created_by": "turboquant-local",
            "encrypted_content": encrypted,
        },
    ]
    output_items.extend(recent_clean)

    return {
        "id": f"resp_cmp_local_v3_{_now_ts()}",
        "object": "response.compaction",
        "created_at": _now_ts(),
        "output": output_items,
        "usage": {
            "input_tokens": _estimate_items_tokens(working_items),
            "output_tokens": _estimate_items_tokens(output_items),
            "total_tokens": _estimate_items_tokens(working_items) + _estimate_items_tokens(output_items),
        },
    }, "ok"


def _resolve_compact_context_window(catalog, client_model: str) -> tuple:
    """Resolve the effective context window for /v1/responses/compact.

    ModelCatalog entries use 'runtime_context_length' and 'context_length' —
    NOT 'context_window' (that field exists only in the Codex-facing catalog JSON).
    The priority is:
      1. runtime_context_length (override from manifest/model-overrides.json)
      2. context_length         (inferred from GGUF metadata)

    Resolution order:
      1. Query-based: catalog.resolve(query=client_model) if client_model is non-empty.
      2. Fallback: catalog.selected (the currently selected model in the proxy session).
      3. Fallback: catalog.entries[0] (first available entry).

    Returns (ctx_tokens_or_None, reason_str, selected_label_or_None).

    reason values:
      "resolved_by_query"               — matched on client_model slug/key/alias
      "resolved_from_selected"          — fell back to catalog.selected
      "resolved_from_first_entry"       — fell back to catalog.entries[0]
      "model_not_found"                 — no match and no catalog selection available
      "selected_model_no_context_window" — entry found but no usable context_length
      "catalog_empty"                   — catalog has no entries
      "catalog_exception:<ExcType>"     — unexpected exception during resolution
    """
    try:
        entries = getattr(catalog, "entries", None)
        if not entries:
            return None, "catalog_empty", None

        entry = None
        resolution_reason = None

        # 1. Query-based resolution on client slug/key/alias.
        if client_model:
            try:
                resolved, _reason_str = catalog.resolve(query=client_model)
                if resolved is not None:
                    entry = resolved
                    resolution_reason = "resolved_by_query"
            except Exception:
                pass  # query resolution failed; fall through to selected

        # 2. Fall back to catalog.selected (proxy's currently active model).
        if entry is None:
            selected = getattr(catalog, "selected", None)
            if isinstance(selected, dict) and selected:
                entry = selected
                resolution_reason = "resolved_from_selected"

        # 3. Fall back to first available entry.
        if entry is None:
            entry = entries[0]
            resolution_reason = "resolved_from_first_entry"

        if not isinstance(entry, dict):
            return None, "model_not_found", None

        # Extract effective context length using catalog field priority.
        ctx = entry.get("runtime_context_length")
        if not isinstance(ctx, int) or ctx <= 0:
            ctx = entry.get("context_length")
        if not isinstance(ctx, int) or ctx <= 0:
            label = entry.get("label") or entry.get("name") or entry.get("stem")
            return None, "selected_model_no_context_window", label

        label = entry.get("label") or entry.get("name") or entry.get("stem")
        return ctx, resolution_reason, label

    except Exception as e:
        return None, f"catalog_exception:{type(e).__name__}", None


def _summarize_items_for_compaction(items):
    lines = []
    for item in items:
        text = _item_text(item)
        text = _normalize_ws(text)
        if not text:
            continue
        text = _truncate(text, COMPACTION_CONFIG["max_item_summary_chars"])
        if not lines or lines[-1] != text:
            lines.append(text)
    if not lines:
        return ""
    summary = "<|history_summary|>\nPrior turn summary:\n" + "\n".join(f"- {line}" for line in lines) + "\n<|end_history_summary|>"
    return _truncate(summary, COMPACTION_CONFIG["max_summary_chars"])


def _estimate_items_tokens(items):
    total = 0
    for item in items or []:
        total += _approx_tokens(_item_text(item))
    return total


def _build_local_compaction_response(
    body: dict,
    selected_context_tokens: Optional[int] = None,
    compact_threshold_tokens: Optional[int] = None,
    remote_compaction: bool = False,
    context_resolution_reason: Optional[str] = None,
) -> dict:
    """Build a compaction response blob.

    selected_context_tokens should be the context_window value from the currently
    selected model (available in qz_request_router at the compaction call site).
    When None or non-positive the v3 LLM path fails closed to heuristic v2.

    compact_threshold_tokens is the raw value from context_management.compact_threshold
    in the request body. It is passed to the budget resolver to determine the effective
    compaction budget. Values below 1024 (e.g. compact_threshold=1 used by the dogfood
    corpus runner to force compaction) are treated as force signals, not token budgets.

    remote_compaction=True: caller is _handle_responses_compact() (POST /v1/responses/compact).
    Codex has already identified all items for compaction; the entire input is summarised.
    remote_compaction=False (default): normal inline path from /v1/responses; only the
    pre-recent-tail slice is summarised.

    context_resolution_reason: reason string from _resolve_compact_context_window() when
    called from _handle_responses_compact(). Recorded in v2 fallback metadata for diagnostics.
    None when the caller did not perform catalog-based context resolution (normal inline path).
    """
    input_items = body.get("input")
    if isinstance(input_items, str):
        input_items = [_make_input_text_message("user", input_items)]
    elif not isinstance(input_items, list):
        input_items = []

    working_items = []
    for item in input_items:
        if _is_local_checkpoint_prompt(item):
            continue
        if isinstance(item, dict) and item.get("type") in _COMPACTION_DROP_TYPES:
            continue
        if isinstance(item, dict) and item.get("type") == "message":
            text = _normalize_ws(_content_to_text(item.get("content")))
            lower = text.lower()
            role = item.get("role")
            if any(marker in lower for marker in HARNESS_TEXT_MARKERS):
                continue
            if role == "user" and any(marker in lower for marker in META_USER_TEXT_MARKERS):
                continue
            if role == "assistant" and any(marker in lower for marker in META_ASSISTANT_TEXT_MARKERS):
                continue
        working_items.append(item)
    working_items = _microcompact_old_tool_results(working_items)

    existing_depth = 0
    for item in working_items:
        if isinstance(item, dict) and item.get("type") == "compaction":
            payload = _decode_local_compaction_blob(item.get("encrypted_content", ""))
            if payload:
                existing_depth = max(existing_depth, int(payload.get("depth", 1)))

    # Stage 4: Opt-in LLM-generated anchored compaction (v3/Zenkai default).
    # _build_local_compaction_response_v3 returns (result, reason_str). The reason
    # is propagated into the v2 blob metadata for diagnostics when v3 falls back.
    v3_resp, v3_reason = _build_local_compaction_response_v3(
        body, working_items, existing_depth,
        selected_context_tokens=selected_context_tokens,
        compact_threshold_tokens=compact_threshold_tokens,
        remote_compaction=remote_compaction,
    )
    if v3_resp:
        return v3_resp

    # Fallback / Default: Heuristic v2 compaction
    tail_start = _tail_start_for_compaction(working_items)
    older = working_items[:tail_start]
    recent = working_items[tail_start:]
    if len(recent) < COMPACTION_CONFIG["min_preserve_items"]:
        recent = working_items[-COMPACTION_CONFIG["min_preserve_items"]:]
        older = working_items[:-len(recent)] if recent else working_items

    summary_text = _summarize_items_for_compaction(older)
    if not summary_text:
        summary_text = "No older turns required compaction."

    depth = min(existing_depth + 1, COMPACTION_CONFIG["max_compaction_depth"])

    payload = {
        "version": 2,
        "source": "turboquant-local",
        "depth": depth,
        "created_at": _now_ts(),
        "summary_text": summary_text,
        "preserved_items": len(recent),
        "metadata": {
            "engine": "qwen3.6-bridge",
            "format": "structured-markers-v2",
            "v3_fallback_reason": v3_reason,
            "context_resolution_reason": context_resolution_reason,
        }
    }
    encrypted = _encode_local_compaction_blob(payload)

    recent = [
        item for item in recent
        if not (isinstance(item, dict) and item.get("type") == "compaction")
        and not _is_local_checkpoint_prompt(item)
    ]

    output_items = [
        {
            "type": "compaction",
            "id": f"cmp_local_{_now_ts()}",
            "created_by": "turboquant-local",
            "encrypted_content": encrypted,
        },
    ]
    output_items.extend(recent)

    while _estimate_items_tokens(output_items) > COMPACTION_CONFIG["target_output_tokens"] and len(recent) > COMPACTION_CONFIG["min_preserve_items"]:
        recent = recent[1:]
        output_items = output_items[:1] + recent

    summary_text = _truncate(summary_text, COMPACTION_CONFIG["max_summary_chars"])
    payload["summary_text"] = summary_text
    payload["preserved_items"] = len(recent)
    output_items[0]["encrypted_content"] = _encode_local_compaction_blob(payload)

    return {
        "id": f"resp_cmp_local_{_now_ts()}",
        "object": "response.compaction",
        "created_at": _now_ts(),
        "output": output_items,
        "usage": {
            "input_tokens": _estimate_items_tokens(working_items),
            "output_tokens": _estimate_items_tokens(output_items),
            "total_tokens": _estimate_items_tokens(working_items) + _estimate_items_tokens(output_items),
        },
    }
