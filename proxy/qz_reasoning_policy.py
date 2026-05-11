#!/usr/bin/env python3
from copy import deepcopy
from typing import Any, Dict, List


DEFAULT_REASONING_POLICY_MODE = "prompt"

_SHARED_SAMPLING = {
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0,
    # presence_penalty for agentic multi-hop use — value is model-dependent.
    # 0 (Qwen model card recommendation for thinking-mode coding): causes
    #   looping on open-ended tasks; model produces intermediate messages and
    #   never commits to a final answer.
    # 1.5 (original): works for HauhauCS but causes kuato-DPO to produce no
    #   output at all on some prompts — DPO reasoning chains exhaust the
    #   penalty budget before answer generation.
    # 0.5 (current): enough pressure to conclude without starving answer
    #   generation on models with longer reasoning chains.
    "presence_penalty": 0.5,
    "repeat_penalty": 1.0,
}

REASONING_POLICIES: Dict[str, Dict[str, Any]] = {
    "low": {
        "effort": "low",
        "description": "One tool call maximum. Direct answer, two sentences max.",
        "prompt": (
            "Use at most one tool call to answer. "
            "Do not follow imports, explore subdirectories, or run a second command unless the first fails. "
            "Answer directly in two sentences or fewer."
        ),
        "sampling": dict(_SHARED_SAMPLING),
    },
    "medium": {
        "effort": "medium",
        "description": "Default coding-agent balance. 3 tool calls max, concise answer.",
        "prompt": (
            "Use at most 3 tool calls. "
            "Stop after 3 regardless of task complexity — work with what you have. "
            "Give a concise answer with brief supporting detail."
        ),
        "sampling": dict(_SHARED_SAMPLING),
    },
    "high": {
        "effort": "high",
        "description": "Multi-file investigation. Cross-reference before answering.",
        "prompt": (
            "Investigate across multiple files. "
            "Read at least two relevant files and cross-reference their contents before answering. "
            "Stop when you are confident there are no conflicting definitions or missing context. "
            "Explain your reasoning."
        ),
        "sampling": dict(_SHARED_SAMPLING),
    },
    "xhigh": {
        "effort": "xhigh",
        "description": "Exhaustive investigation. Trace dependencies, verify everything.",
        "prompt": (
            "Perform exhaustive investigation: map the directory structure, read every file that could affect the answer, "
            "trace dependencies between modules, and verify assumptions from source rather than inference. "
            "Stop only when you have no remaining uncertainty about the answer. "
            "Document all relevant findings."
        ),
        "sampling": dict(_SHARED_SAMPLING),
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
