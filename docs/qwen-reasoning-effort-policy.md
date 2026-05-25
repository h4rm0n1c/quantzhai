# Qwen Reasoning Effort Policy

## Purpose

QuantZhai should treat Codex `low`, `medium`, `high`, and `xhigh`
reasoning choices as Qwen-aware effort policies, not fixed reasoning token
budgets.

The current fixed-budget shape is too blunt for Qwen agent use. Small hard
caps can interrupt reasoning mid-process, while Qwen's own guidance points
toward thinking mode, sampling policy, and prompt-level effort guidance as the
main controls.

## Source Guidance

Qwen3.6 model guidance separates sampling recommendations by task and thinking
mode:

- Thinking/coding: `temperature=0.6`, `top_p=0.95`, `top_k=20`,
  `presence_penalty=0`
- Thinking/general: `temperature=1.0`, `top_p=0.95`, `top_k=20`,
  `presence_penalty=1.5`

Qwen also notes that supported runtimes can raise `presence_penalty` between
`0` and `2` to reduce endless repetitions. QuantZhai keeps Qwen's coding
temperature/top-p/top-k shape, but adds mild anti-repeat sampling for Codex
agent sessions because unbounded thinking can otherwise degenerate into
repetitive reasoning loops.
- Non-thinking: `temperature=0.7`, `top_p=0.8`, `top_k=20`,
  `presence_penalty=1.5`

Qwen3.6 also supports thinking preservation for agent-style use. For Codex
sessions, the useful default is to preserve thinking where the backend supports
it, rather than repeatedly forcing short thought budgets.

References:

- <https://huggingface.co/Qwen/Qwen3.6-35B-A3B>
- <https://qwen.readthedocs.io/en/stable/getting_started/quickstart.html>
- <https://huggingface.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive>

## Policy Contract

The Codex model picker must continue to list real local GGUF model inventory.
After a model is selected, the Codex reasoning effort screen should select one
of these policies.

| Effort | Intent | Sampling | Prompt guidance |
| --- | --- | --- | --- |
| `low` | Fast/shallow effort. Good for simple prompts. | `temperature=0.7`, `top_p=0.8`, `top_k=20`, `min_p=0`, `presence_penalty=1.5`, `repeat_penalty=1.0` | `Reasoning effort: low.` |
| `medium` | Default coding-agent balance. | `temperature=0.6`, `top_p=0.95`, `top_k=20`, `min_p=0`, `presence_penalty=1.5`, `repeat_penalty=1.0` | `Reasoning effort: medium.` |
| `high` | Careful reasoning for complex coding work. | Same as `medium` | `Reasoning effort: high.` |
| `xhigh` | Deep effort when complexity warrants it. | Same as `medium` | `Reasoning effort: xhigh.` |

For QuantZhai's default Codex/coding-agent path, keep `medium`, `high`, and
`xhigh` on Qwen's precise coding temperature/top-p/top-k sampler, with a
presence penalty to reduce repetitive thought loops. Keep repeat penalty at
Qwen's default `1.0` and do not enable DRY by default; local smoke showed those
controls can corrupt compiler-style anchor text. Do not use hotter
general-thinking sampling for coding by default; it should be a later
task-classifier or research-mode policy if benchmarks justify it.

Optional future general/research policy:

| Mode | Sampling |
| --- | --- |
| Thinking/general | `temperature=1.0`, `top_p=0.95`, `top_k=20`, `min_p=0`, `presence_penalty=1.5`, `repeat_penalty=1.0` |
| Non-thinking/reasoning | `temperature=1.0`, `top_p=1.0`, `top_k=40`, `min_p=0`, `presence_penalty=2.0`, `repeat_penalty=1.0` |

## Reasoning Budget Policy

QuantZhai reasoning control has two axes:

### Axis 1: thinking_mode

| Value | Meaning |
|---|---|
| `thinking` | Model uses a `<think>…</think>` reasoning block. Inject per-block token budget. |
| `non_thinking` | Model is a coder/instruct variant without a thinking block. No budget injected. |
| `auto` | Mode unknown. No budget injected (safe fallback). |

**Detection priority:**
1. Explicit `runtime.thinking_mode` (aliases: `reasoning_mode`, `default_thinking_mode`) in the model profile.
2. Model-name heuristic: `coder`/`instruct` substrings → `non_thinking`; `qwen3.6`/`a3b`/`thinking` substrings → `thinking`.
3. Fallback: `auto`.

### Axis 2: reasoning_effort

| Effort | Intent | Per-block token budget (thinking models only) |
|---|---|---|
| `low` | Fast/shallow | 16 384 |
| `medium` | Default balance | 24 576 |
| `high` | Careful | 32 768 |
| `xhigh` | Deep | 49 152 |

For non-thinking models the effort level still controls prompt guidance and
sampling pressure, but no `reasoning_budget_tokens` is injected.

### Per-block budget semantics

- Budgets are **per `<think>…</think>` block**, not total run caps.
- Total output size is still controlled by `max_output_tokens`/`n_predict`.
- When the model's reasoning block exceeds the budget, the backend injects the
  configured `--reasoning-budget-message` (env: `QZ_REASONING_BUDGET_MESSAGE`)
  into the thinking stream to prompt early exit.

### What QuantZhai injects (thinking mode)

For each thinking-mode request QuantZhai injects **both** field names into the
forwarded body:

| Field | Used by |
|---|---|
| `reasoning_budget_tokens` | TheTom `/completion` path (`server-task.cpp`) |
| `thinking_budget_tokens` | TheTom `/v1/responses` OAI path (`server-common.cpp`) |

Both fields are set to the same resolved value. This mirroring is required
because `server-common.cpp` reads `thinking_budget_tokens` (old field name) as
the per-request override, then internally maps it to `reasoning_budget_tokens`
before passing to the sampler. Without `thinking_budget_tokens`, the OAI path
ignored the per-request budget and fell back to `opt.reasoning_budget = -1`
(no cap). See `docs/thetom-oai-responses-compat.md` for details.

**Do not remove the mirror** unless TheTom's `server-common.cpp` budget-reading
logic changes.

### Profile config example

```jsonc
// Thinking model (Qwen3.6/A3B)
"runtime": {
  "thinking_mode": "thinking",
  "default_reasoning_level": "medium"
}

// Non-thinking model (Coder/Instruct)
"runtime": {
  "thinking_mode": "non_thinking",
  "default_reasoning_level": "medium"
}
```

### Server-side startup budget

The llama.cpp backend has a server-side `--reasoning-budget` launch setting.
QuantZhai exposes that as `QZ_REASONING_BUDGET`, default `-1` (no startup cap).
The server-common.cpp OAI path only reads the per-request `thinking_budget_tokens`
override when `opt.reasoning_budget == -1`. If you set a positive
`QZ_REASONING_BUDGET`, the startup value takes precedence and per-request budgets
are ignored on the OAI path. Keep `QZ_REASONING_BUDGET=-1` (default) for normal
operation.

## Request Behavior

For each `/v1/responses` request:

1. Resolve selected GGUF backend model from current catalog.
2. Resolve `thinking_mode` from profile or name heuristic.
3. Resolve `reasoning_effort` from Codex metadata or profile default.
4. Apply the effort policy:
   - inject compact prompt guidance into model-visible instructions;
   - apply sampling params unless caller explicitly supplied them;
   - for `thinking` mode: inject `reasoning_budget_tokens` and `thinking_budget_tokens`
     (both, for backend compat — see reasoning budget section above);
   - for `non_thinking` mode: remove any budget fields from the body;
   - for `auto` mode: no budget injection.
5. Preserve existing tool, SSE, and Responses normalization behavior.

Prompt injection should be system/developer-style context, not appended to the
user's text. Keep prompt-side effort control to the compact labels above.
Longer reasoning instructions belong in request metadata, telemetry, or future
backend controls, not repeated model-visible prose.

## Status And Telemetry

`/qz/status`, `qz-top`, and relevant telemetry expose:

- `backend.selected_thinking_mode` — resolved thinking mode for the active model
- `backend.selected_reasoning_level` — resolved effort level
- `backend.selected_thinking_budget_tokens` — per-block token budget (null if non-thinking)
- `backend.backend_reasoning_budget` — server-side startup budget (`-1` = no cap)
- `profile.thinking_mode` / `profile.thinking_budget_tokens` — same, from `/qz/control-plane`

This makes live behaviour inspectable without relying on log files.

## Acceptance Tests

- `qz-codex /model` first screen shows real local GGUF models.
- Reasoning effort screen still offers `low`, `medium`, `high`, and `xhigh`.
- For thinking-mode models, normalized upstream request contains:
  - expected prompt guidance and sampling params
  - `reasoning_budget_tokens` matching the effort-level table
  - `thinking_budget_tokens` equal to `reasoning_budget_tokens`
- For non-thinking-mode models, normalized upstream request contains neither
  budget field.
- `/qz/status` `backend.selected_thinking_mode` matches the active model's mode.
- `/qz/control-plane` `profile.thinking_mode` and `profile.thinking_budget_tokens`
  are populated.
- Direct tiny-budget probe (e.g. `thinking_budget_tokens: 16`) fires the
  budget break-out message and yields a short reasoning block.
- Live smoke (Qwen3.6, grounded prompt):
  - first tool call fires immediately (no reasoning spin);
  - total tokens within the relevant budget for the chosen effort level;
  - budget does not trigger on normal short tasks.

## Non-Goals For First Pass

- Do not remove real GGUF inventory model selection.
- Do not reintroduce old `QwenZhai-*`, `Qwen3.6Turbo-*`, or other synthetic
  profile-only model picker entries.
- Do not disable Qwen thinking for `low` yet.
- Do not tune per-model quant variants beyond the shared policy table.
