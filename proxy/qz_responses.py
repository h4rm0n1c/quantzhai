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
_DEFAULT_SURVIVAL_PROFILE = "coding"
_VALID_SURVIVAL_PROFILES = frozenset({_DEFAULT_SURVIVAL_PROFILE})
_COMPACTION_CONFIG_ENV = "QZ_COMPACTION_CONFIG"
_COMPACTION_PROFILE_ENV = "QZ_COMPACTION_PROFILE"
_COMPACTION_SURVIVAL_PROFILE_ENV = "QZ_COMPACTION_SURVIVAL_PROFILE"
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


def _build_survival_weighted_compaction_prompt(previous_summary: str, new_items: list) -> str:
    """Construct the LLM prompt using the Stage 1 template and Stage 2.1 hints."""
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


def _is_probably_quantzhai_proxy_url(base_url: str, env=None) -> bool:
    """Conservatively reject known QuantZhai proxy URLs to avoid recursion."""
    env = os.environ if env is None else env
    base_identity = _canonical_url_identity(base_url)
    if not base_identity:
        return False

    candidate_urls = [
        env.get("CODEX_OSS_BASE_URL", ""),
        env.get("QZ_PROXY_BASE_URL", ""),
    ]
    proxy_host = env.get("QZ_PROXY_HOST", "")
    proxy_port = env.get("QZ_PROXY_PORT", "")
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


def _call_llm_compactor(prompt: str) -> Optional[str]:
    """Execute a direct call to the local LLM backend for compaction."""
    base_url = str(COMPACTION_CONFIG.get("llm_base_url", "") or "").rstrip("/")
    if not base_url:
        return None
    if _is_probably_quantzhai_proxy_url(base_url):
        return None

    # Use OpenAI-compatible chat completions endpoint
    url = _llm_chat_completions_url(base_url)
    payload = {
        "model": COMPACTION_CONFIG["llm_model"] or "local-model",
        "messages": [
            {"role": "system", "content": "You are a precise context compaction engine for a coding agent. Preserve exact technical atoms."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0,
        "max_tokens": _positive_config_int("llm_max_output_tokens", _DEFAULT_LLM_MAX_OUTPUT_TOKENS),
        "stream": False
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        timeout = _positive_config_int("llm_timeout_sec", _DEFAULT_LLM_TIMEOUT_SEC)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return _extract_llm_compactor_content(data)
    except Exception:
        # Fallback to heuristic is handled by caller
        return None


def _build_local_compaction_response_v3(body: dict, working_items: list, existing_depth: int) -> Optional[dict]:
    """Attempt LLM-generated anchored compaction (v3)."""
    if _current_compaction_mode() not in ("llm", "auto"):
        return None
    if not COMPACTION_CONFIG["llm_base_url"]:
        return None

    tail_start = _tail_start_for_compaction(working_items)
    older = working_items[:tail_start]
    recent = working_items[tail_start:]
    
    if len(recent) < COMPACTION_CONFIG["min_preserve_items"]:
        recent = working_items[-COMPACTION_CONFIG["min_preserve_items"]:]
        older = working_items[:-len(recent)] if recent else working_items

    previous_summary = _extract_previous_anchored_summary(older)
    prompt = _build_survival_weighted_compaction_prompt(previous_summary, older)
    
    start_ms = int(time.time() * 1000)
    summary_text = _call_llm_compactor(prompt)
    latency_ms = int(time.time() * 1000) - start_ms

    if not summary_text or not _validate_anchored_summary(summary_text):
        return None # Fallback to v2

    depth = min(existing_depth + 1, COMPACTION_CONFIG["max_compaction_depth"])
    
    # Calculate survival hint count for metadata
    combined_older_text = "\n".join(_item_text(it) for it in older)
    spans = _score_survival_text(combined_older_text)
    
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
            "latency_ms": latency_ms
        }
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
    }


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


def _build_local_compaction_response(body: dict) -> dict:
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

    # Stage 4: Opt-in LLM-generated anchored compaction
    if _current_compaction_mode() in ("llm", "auto") and COMPACTION_CONFIG["llm_base_url"]:
        v3_resp = _build_local_compaction_response_v3(body, working_items, existing_depth)
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
            "format": "structured-markers-v2"
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
