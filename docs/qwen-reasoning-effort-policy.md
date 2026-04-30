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
| `low` | Fast/shallow effort. Good for simple prompts. | `temperature=0.7`, `top_p=0.8`, `top_k=20`, `min_p=0`, `presence_penalty=1.5`, `repetition_penalty=1.0` | `Use low reasoning effort. Think briefly.` |
| `medium` | Default coding-agent balance. | `temperature=0.6`, `top_p=0.95`, `top_k=20`, `min_p=0`, `presence_penalty=0`, `repetition_penalty=1.0` | `Use medium reasoning effort. Balance speed and correctness.` |
| `high` | Careful reasoning for complex coding work. | Same as `medium` | `Use high reasoning effort. Reason carefully before acting.` |
| `xhigh` | Deep effort when complexity warrants it. | Same as `medium` | `Use extra-high reasoning effort. Explore deeply when complexity warrants it.` |

For QuantZhai's default Codex/coding-agent path, keep `medium`, `high`, and
`xhigh` on Qwen's precise coding sampler. Do not use hotter general-thinking
sampling for coding by default; it should be a later task-classifier or
research-mode policy if benchmarks justify it.

Optional future general/research policy:

| Mode | Sampling |
| --- | --- |
| Thinking/general | `temperature=1.0`, `top_p=0.95`, `top_k=20`, `min_p=0`, `presence_penalty=1.5`, `repetition_penalty=1.0` |
| Non-thinking/reasoning | `temperature=1.0`, `top_p=1.0`, `top_k=40`, `min_p=0`, `presence_penalty=2.0`, `repetition_penalty=1.0` |

## Hard Budget Policy

Do not send `thinking_budget_tokens` by default for normal reasoning effort
selection.

Hard reasoning budgets may remain as an explicit diagnostic or emergency
guardrail, but they must not be the main `low`/`medium`/`high`/`xhigh`
implementation.

Recommended default:

- `QZ_REASONING_POLICY=prompt`
- Optional diagnostic mode: `QZ_REASONING_POLICY=hard_budget`

When hard-budget mode is off, status and telemetry should report no active hard
budget.

## Request Behavior

For each `/v1/responses` request:

1. Resolve selected GGUF backend model from current catalog.
2. Resolve selected reasoning effort from Codex model selection metadata.
3. Apply the effort policy:
   - inject compact prompt guidance into model-visible instructions;
   - apply sampling params unless caller explicitly supplied them;
   - avoid `thinking_budget_tokens` unless diagnostic hard-budget mode is on.
4. Preserve existing tool, SSE, and Responses normalization behavior.

Prompt injection should be system/developer-style context, not appended to the
user's text.

## Status And Telemetry

`/qz/status`, `qz-top`, and relevant telemetry should expose:

- selected model;
- loaded model;
- selected reasoning effort;
- reasoning policy mode;
- active sampling params;
- hard budget only when enabled.

This makes live behavior inspectable without relying on log files.

## Acceptance Tests

- `qz-codex /model` first screen shows real local GGUF models.
- Reasoning effort screen still offers `low`, `medium`, `high`, and `xhigh`.
- Normalized upstream request for each effort contains the expected prompt
  guidance and sampling params.
- Normalized upstream request does not contain `thinking_budget_tokens` by
  default.
- Hard-budget diagnostic mode sends the expected budget only when explicitly
  enabled.
- Live smoke:
  - `low` greeting gives short answer with minimal thought;
  - `medium` coding task remains balanced;
  - `high` and `xhigh` repo-eval prompts show deeper reasoning without forced
    cutoff.

## Non-Goals For First Pass

- Do not remove real GGUF inventory model selection.
- Do not reintroduce old `Qwen3.6Turbo-*` profile-only model picker entries.
- Do not disable Qwen thinking for `low` yet.
- Do not tune per-model quant variants beyond the shared policy table.
