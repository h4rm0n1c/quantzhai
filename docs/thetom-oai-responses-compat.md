# TheTom `/v1/responses` OAI path — reasoning budget compatibility

Date: 2026-05-26

## Background

QuantZhai forwards all Codex requests to the TheTom llama.cpp TurboQuant
backend via the `/v1/responses` endpoint (the OAI compatibility path). The
backend has two separate code paths that read reasoning budget fields, and they
use **different field names**.

## The two field names

| Field name | Read by | Route |
|---|---|---|
| `reasoning_budget_tokens` | `server-task.cpp` | `/completion` (direct path) |
| `thinking_budget_tokens` | `server-common.cpp` | `/v1/responses` (OAI compat path) |

These are the same logical field. TheTom renamed it when adding OAI
compatibility, but `server-common.cpp` still reads the old name as the
per-request override fallback.

## What server-common.cpp does

```cpp
// server-common.cpp — OAI /v1/responses budget logic
int reasoning_budget = opt.reasoning_budget;   // startup --reasoning-budget arg
if (reasoning_budget == -1 && body.contains("thinking_budget_tokens")) {
    reasoning_budget = json_value(body, "thinking_budget_tokens", -1);
}
if (!chat_params.thinking_end_tag.empty()) {
    llama_params["reasoning_budget_tokens"] = reasoning_budget;  // maps to sampler field name
    llama_params["reasoning_budget_start_tag"] = chat_params.thinking_start_tag;
    llama_params["reasoning_budget_end_tag"]   = chat_params.thinking_end_tag;
    llama_params["reasoning_budget_message"]   = opt.reasoning_budget_message;
}
// copy loop: remaining body fields → llama_params, skipping already-set keys
```

Key points:
- Reads `thinking_budget_tokens` (old name) not `reasoning_budget_tokens`.
- The copy loop then skips body's `reasoning_budget_tokens` because the key is
  already present in `llama_params` (set to the startup arg value or the
  `thinking_budget_tokens` override).
- Tags (`thinking_start_tag`, `thinking_end_tag`) come from the model's chat
  template. For Qwen3, they are `<think>` / `</think>`. The budget sampler only
  activates when both tags are non-empty.
- `reasoning_budget_message` comes from `opt.reasoning_budget_message`
  (startup `--reasoning-budget-message`), not from the body field. The body's
  `reasoning_budget_message` is effectively ignored on the OAI path.

## The bug (now fixed)

Before commit `53eff6b`, QuantZhai injected only `reasoning_budget_tokens` into
forwarded bodies. On the OAI path this was silently ignored because:

1. `opt.reasoning_budget = -1` (from `QZ_REASONING_BUDGET=-1`, the default).
2. `body["thinking_budget_tokens"]` was absent (the proxy stripped it).
3. `llama_params["reasoning_budget_tokens"] = -1` (no cap).
4. Copy loop skipped body's `reasoning_budget_tokens` — key already present.

Result: every thinking-mode request got an unlimited reasoning budget regardless
of the selected effort level.

## The fix (`53eff6b`)

`proxy/qz_reasoning_policy.py` `apply_reasoning_policy()` now mirrors the
resolved budget to **both** field names for thinking-mode requests:

```python
body.setdefault("reasoning_budget_tokens", thinking_budget_tokens)
body["thinking_budget_tokens"] = body["reasoning_budget_tokens"]  # OAI path compat
```

The `thinking_budget_tokens` value tracks `reasoning_budget_tokens` exactly,
including any caller-supplied override of `reasoning_budget_tokens`.

## The startup-budget interaction

`server-common.cpp` only reads `thinking_budget_tokens` when
`opt.reasoning_budget == -1`. If you set `QZ_REASONING_BUDGET` to a positive
value, the startup budget takes precedence and per-request overrides are ignored
on the OAI path.

**Keep `QZ_REASONING_BUDGET=-1` (the default) for per-request budget control
to work.**

## The budget message

The break-out message is taken from `opt.reasoning_budget_message` (the
startup `--reasoning-budget-message` arg, env `QZ_REASONING_BUDGET_MESSAGE`),
not from the body. QuantZhai does inject `reasoning_budget_message` in the body,
but on the OAI path this field is overwritten by the startup arg before the copy
loop runs.

The default `QZ_REASONING_BUDGET_MESSAGE` in `scripts/qz-env`:

```
I have reasoned long enough. Let me now produce my final answer.
```

The body field `REASONING_BUDGET_MESSAGE` in `proxy/qz_reasoning_policy.py`:

```
You are repeating yourself. Stop reasoning now and provide the answer.
```

The body field fires only on the `/completion` direct path. On the OAI path the
startup message fires. Both are functional break-out messages.

## Do not remove the mirror

Do not remove `thinking_budget_tokens` from forwarded bodies unless
`server-common.cpp` changes its budget-reading logic. The field name mismatch
is not a QuantZhai decision — it reflects a TheTom internal naming difference
between the two server paths.

## Validation evidence

From session 2026-05-26:

- Direct probe with `thinking_budget_tokens: 16`: reasoning block cut at ~16
  tokens; break-out message injected; answer produced normally.
- Proxy path (Qwen3.6, medium effort = 24 576 tokens): live trace completed;
  772 tokens total; first tool call immediate; budget did not trigger (correct —
  772 << 24 576).
- Full test suite: 3 626 / 3 626 passed.

## If TheTom server-common.cpp changes

If a future TheTom build starts reading `reasoning_budget_tokens` directly
(instead of the `thinking_budget_tokens` fallback), the mirror becomes harmless
redundancy. Do not remove it without confirming the OAI path picks up the field
by that name.

## Related files

| File | Role |
|---|---|
| `proxy/qz_reasoning_policy.py` | `apply_reasoning_policy()` — budget injection and mirror |
| `proxy/qz_model_router.py` | `selected_thinking_mode()`, `selected_thinking_budget_tokens()` |
| `scripts/qz-env` | `QZ_REASONING_BUDGET`, `QZ_REASONING_BUDGET_MESSAGE` |
| `docs/qwen-reasoning-effort-policy.md` | Full two-axis reasoning policy doc |
| `tests/test_qz_reasoning_policy.py` | OAI compat mirror tests |
