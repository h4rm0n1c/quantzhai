MODEL_BUDGETS = {
    "QwenZhai-low": 0,
    "QwenZhai-medium": 256,
    "QwenZhai-high": 512,
    "QwenZhai-max": -1,
    "QwenZhai-caveman": 256,
    "QwenZhai": 256,
    "Qwen3.6Turbo-low": 0,
    "Qwen3.6Turbo-medium": 256,
    "Qwen3.6Turbo-high": 512,
    "Qwen3.6Turbo-max": -1,
    "Qwen3.6Turbo-caveman": 256,
    "Qwen3.6Turbo": 256,
}


LOCAL_CODEX_RATE_LIMITS = {
    "limit_id": "codex",
    "limit_name": "local",
    "primary": {
        "used_percent": 0.0,
        "window_minutes": 300,
        "resets_in_seconds": 300 * 60,
        "resets_at": 4102444800,
    },
    "secondary": {
        "used_percent": 0.0,
        "window_minutes": 10080,
        "resets_in_seconds": 10080 * 60,
        "resets_at": 4102444800,
    },
    "credits": {
        "has_credits": True,
        "unlimited": True,
        "balance": None,
    },
    "plan_type": "local",
    "rate_limit_reached_type": None,
}


CURRENT_API_ENDPOINTS = (
    "/v1/responses",
    "/v1/responses/compact",
)


LEGACY_API_ENDPOINTS = {
    "/v1/chat/completions": {
        "replacement": "/v1/responses",
        "status": "deprecated",
        "removal": "pending after confirmed local clients no longer need it",
        "reason": "QuantZhai is standardizing on Responses for current Codex/OpenAI-style agent clients.",
    },
    "/chat/completions": {
        "replacement": "/v1/responses",
        "status": "deprecated",
        "removal": "pending after confirmed local clients no longer need it",
        "reason": "Unversioned Chat Completions compatibility route is legacy.",
    },
}


def api_contract_payload() -> dict:
    return {
        "current": list(CURRENT_API_ENDPOINTS),
        "legacy": LEGACY_API_ENDPOINTS,
    }
