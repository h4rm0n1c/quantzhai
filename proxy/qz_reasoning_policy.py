#!/usr/bin/env python3
import os
from copy import deepcopy
from typing import Any, Dict, List


DEFAULT_REASONING_POLICY_MODE = "prompt"
HARD_BUDGET_POLICY_MODE = "hard_budget"

REASONING_POLICIES: Dict[str, Dict[str, Any]] = {
    "low": {
        "effort": "low",
        "description": "Fast/shallow effort for simple prompts.",
        "prompt": "Use low reasoning effort. Think briefly.",
        "sampling": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        },
        "hard_budget_tokens": 0,
    },
    "medium": {
        "effort": "medium",
        "description": "Default coding-agent balance.",
        "prompt": "Use medium reasoning effort. Balance speed and correctness.",
        "sampling": {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 0,
            "repeat_penalty": 1.0,
        },
        "hard_budget_tokens": 256,
    },
    "high": {
        "effort": "high",
        "description": "Careful reasoning for complex coding work.",
        "prompt": "Use high reasoning effort. Reason carefully before acting.",
        "sampling": {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 0,
            "repeat_penalty": 1.0,
        },
        "hard_budget_tokens": 512,
    },
    "xhigh": {
        "effort": "xhigh",
        "description": "Deep effort when complexity warrants it.",
        "prompt": "Use extra-high reasoning effort. Explore deeply when complexity warrants it.",
        "sampling": {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 0,
            "repeat_penalty": 1.0,
        },
        "hard_budget_tokens": -1,
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
    value = (os.environ.get("QZ_REASONING_POLICY") or DEFAULT_REASONING_POLICY_MODE).strip().lower()
    if value in {HARD_BUDGET_POLICY_MODE, "budget", "hard-budget"}:
        return HARD_BUDGET_POLICY_MODE
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


def hard_budget_for_level(level: str | None, entry: Dict[str, Any] | None = None) -> int:
    effort = normalize_reasoning_level(level)
    if isinstance(entry, dict):
        supported = entry.get("supported_reasoning_levels")
        if isinstance(supported, list):
            for item in supported:
                if not isinstance(item, dict):
                    continue
                if normalize_reasoning_level(item.get("effort")) != effort:
                    continue
                budget = item.get("budget_tokens")
                if budget is None:
                    budget = item.get("thinking_budget_tokens")
                if budget is not None:
                    try:
                        return int(budget)
                    except Exception:
                        break
    return int(REASONING_POLICIES[effort]["hard_budget_tokens"])


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

    if mode == HARD_BUDGET_POLICY_MODE:
        body["thinking_budget_tokens"] = hard_budget_for_level(policy["effort"])
    else:
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
        "thinking_budget_tokens": body.get("thinking_budget_tokens") if mode == HARD_BUDGET_POLICY_MODE else None,
    }
    body["metadata"] = metadata
    return body
