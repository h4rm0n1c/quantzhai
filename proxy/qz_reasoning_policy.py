#!/usr/bin/env python3
from copy import deepcopy
from typing import Any, Dict, List, Optional


DEFAULT_REASONING_POLICY_MODE = "prompt"

# Token budget per reasoning block for each effort level (thinking-mode models only).
# These are per-block budgets, not total run caps.
# Calibrated for ~90 t/s generation.
# low=~90s, medium=~136s, high=~3min, xhigh=~4.5min at current hardware speed.
THINKING_MODE_BUDGET_TOKENS: Dict[str, int] = {
    "low":    8192,
    "medium": 12288,
    "high":   16384,
    "xhigh":  24576,
}

# Injected into the request as reasoning_budget_message when thinking_mode=thinking.
# Encourages the model to exit its reasoning block rather than loop indefinitely.
REASONING_BUDGET_MESSAGE = (
    "You are repeating yourself. Stop reasoning now and provide the answer."
)

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
        "description": "Minimal reasoning. Answer from obvious evidence.",
        "prompt": (
            "Think briefly. "
            "Answer from obvious evidence. "
            "Do not over-investigate."
        ),
        "sampling": dict(_SHARED_SAMPLING),
    },
    "medium": {
        "effort": "medium",
        "description": "Default balance. Reason to correctness, stop when clear.",
        "prompt": (
            "Reason enough to be correct. "
            "Use tools when useful. "
            "Stop when the answer is clear."
        ),
        "sampling": dict(_SHARED_SAMPLING),
    },
    "high": {
        "effort": "high",
        "description": "Careful reasoning. Stop on diminishing returns.",
        "prompt": (
            "Think carefully. "
            "Check important assumptions. "
            "Stop on diminishing returns. "
            "Produce a final answer."
        ),
        "sampling": dict(_SHARED_SAMPLING),
    },
    "xhigh": {
        "effort": "xhigh",
        "description": "Deeper reasoning for difficult problems. Bounded exploration.",
        "prompt": (
            "Use deeper reasoning for difficult problems. "
            "Explore alternatives, but remain bounded. "
            "Produce a final answer."
        ),
        "sampling": dict(_SHARED_SAMPLING),
    },
}


def normalize_thinking_mode(value: Optional[str]) -> str:
    """Normalise a raw thinking_mode string to one of 'thinking', 'non_thinking', or 'auto'.

    Aliases accepted:
      thinking   : "think", "thinking", "on", "true", "1"
      non_thinking: "non_thinking", "nonthinking", "non-thinking",
                    "instruct", "off", "false", "0", "none", "no"
      auto       : None / empty / unrecognised
    """
    if not isinstance(value, str) or not value.strip():
        return "auto"
    v = value.strip().lower().replace("-", "_")
    if v in {"think", "thinking", "on", "true", "1"}:
        return "thinking"
    if v in {"non_thinking", "nonthinking", "instruct", "off", "false", "0", "none", "no"}:
        return "non_thinking"
    return "auto"


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


def apply_reasoning_policy(
    body: Dict[str, Any],
    level: str | None,
    mode: str | None = None,
    thinking_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply reasoning policy to a request body.

    Args:
        body:          The mutable request body dict (modified in place and returned).
        level:         Reasoning effort level ('low', 'medium', 'high', 'xhigh').
        mode:          Policy mode string (default: DEFAULT_REASONING_POLICY_MODE).
        thinking_mode: Resolved thinking mode ('thinking', 'non_thinking', or 'auto'/None).
                       'thinking'     → injects reasoning_budget_tokens + reasoning_budget_message.
                       'non_thinking' → removes any budget fields.
                       'auto'/None    → no budget injection (safe fallback).
    """
    if not isinstance(body, dict):
        return body

    policy = reasoning_policy_for_level(level)
    mode = mode or reasoning_policy_mode()
    resolved_level = normalize_reasoning_level(level)
    tm = normalize_thinking_mode(thinking_mode)

    body.pop("thinking_budget_tokens", None)

    for key, value in policy["sampling"].items():
        body.setdefault(key, value)

    # Apply per-block reasoning budget for thinking-mode models.
    #
    # Two field names are required for full coverage of the TheTom llama.cpp backend:
    #   reasoning_budget_tokens — read by server-task.cpp (/completion path)
    #   thinking_budget_tokens  — read by server-common.cpp (/v1/responses OAI path)
    #
    # server-common.cpp only falls back to the body's thinking_budget_tokens when
    # opt.reasoning_budget (startup --reasoning-budget) is -1 (no startup cap).
    # QuantZhai defaults QZ_REASONING_BUDGET=-1, so the per-request override applies.
    # Without thinking_budget_tokens in the body the budget never reaches the sampler
    # on the OAI path.
    thinking_budget_tokens: Optional[int] = None
    if tm == "thinking":
        thinking_budget_tokens = THINKING_MODE_BUDGET_TOKENS[resolved_level]
        body.setdefault("reasoning_budget_tokens", thinking_budget_tokens)
        # Mirror to thinking_budget_tokens for server-common.cpp OAI path compat.
        # Use the resolved reasoning_budget_tokens value (honours caller overrides).
        body["thinking_budget_tokens"] = body["reasoning_budget_tokens"]
        body.setdefault("reasoning_budget_message", REASONING_BUDGET_MESSAGE)
    elif tm == "non_thinking":
        body.pop("reasoning_budget_tokens", None)
        body.pop("reasoning_budget_message", None)
    # tm == "auto": no injection — caller did not resolve model type; safe fallback.

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
        "thinking_mode": tm,
        "thinking_budget_tokens": thinking_budget_tokens,
    }
    body["metadata"] = metadata
    return body
