#!/usr/bin/env python3
from copy import deepcopy
from typing import Any, Dict, List


DEFAULT_REASONING_POLICY_MODE = "prompt"

REASONING_POLICIES: Dict[str, Dict[str, Any]] = {
    "low": {
        "effort": "low",
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
        "effort": "medium",
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
        "effort": "high",
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
        "effort": "xhigh",
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


def normalize_reasoning_level(level: str | None) -> str:
    if not isinstance(level, str):
        return "medium"
    value = level.strip().lower()
    if value in {"max", "extra_high", "extra-high"}:
        return "xhigh"
    if value in REASONING_POLICIES:
        return value
    return "medium"


def reasoning_policy_mode() -> str:
    return DEFAULT_REASONING_POLICY_MODE


def reasoning_policy_for_level(level: str | None) -> Dict[str, Any]:
    policy = REASONING_POLICIES[normalize_reasoning_level(level)]
    return deepcopy(policy)


def supported_reasoning_levels(default_level: str | None = None) -> List[Dict[str, Any]]:
    supported = []
    default_effort = normalize_reasoning_level(default_level)
    for effort in ("low", "medium", "high", "xhigh"):
        policy = reasoning_policy_for_level(effort)
        supported.append({
            "effort": effort,
            "description": policy["description"],
            "prompt": policy["prompt"],
            "sampling": policy["sampling"],
            "default": effort == default_effort,
        })
    return supported


def requested_reasoning_level(body: Dict[str, Any] | None, default_level: str | None) -> str:
    if isinstance(body, dict):
        reasoning = body.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("effort"):
            return normalize_reasoning_level(reasoning.get("effort"))
        for key in ("reasoning_effort", "effort"):
            if body.get(key):
                return normalize_reasoning_level(body.get(key))
    return normalize_reasoning_level(default_level)


def apply_reasoning_policy(body: Dict[str, Any], level: str | None, mode: str | None = None) -> Dict[str, Any]:
    if not isinstance(body, dict):
        return body

    policy = reasoning_policy_for_level(level)
    mode = mode or reasoning_policy_mode()

    body.pop("thinking_budget_tokens", None)

    for key, value in policy["sampling"].items():
        body.setdefault(key, value)

    block = policy["prompt"]
    existing = body.get("instructions")
    if isinstance(existing, str) and existing.strip():
        if block not in existing:
            body["instructions"] = existing.strip() + "\n\n" + block
    else:
        body["instructions"] = block

    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["qz_reasoning"] = {
        "level": policy["effort"],
        "policy": mode,
        "prompt": policy["prompt"],
        "sampling": policy["sampling"],
        "thinking_budget_tokens": None,
    }
    body["metadata"] = metadata
    return body
