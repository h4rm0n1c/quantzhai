# LLM Signal System

Date: 2026-05-11

## The core idea

An LLM doing agentic work is a feedback control system. The loop is:

```
action → observation → updated belief → next action
```

The proxy sits in the middle and controls what observations the model receives.
Every signal the proxy silently discards, truncates, or genericises is a
degraded observation. Degraded observations produce worse next actions.

The coercion system (built 2026-05-11) was the first concrete implementation of
this principle: instead of silently dropping malformed tool calls, the proxy
now injects specific error feedback so the model can self-correct. It trades one
extra hop (10–30 seconds) for task completion instead of failure.

This document tracks all the signals the model needs, what's implemented, and
what remains. Every item here is the same class of problem: the model flying
blind because the proxy was not relaying information it had.

**Design rule:** The proxy should be a faithful information relay. Filtering,
truncation, and genericisation are explicit decisions with documented reasons,
not default behaviour. The cost of an extra hop to give the model better
information is almost always lower than the cost of a failed task the user
has to manually restart.

---

## Signal inventory

### Implemented

**Tool argument recovery (coercion system)**
Status: ✅ shipped 2026-05-11

When the model emits a malformed function_call, the proxy tries to recover the
arguments. On success it corrects silently. On failure it injects a specific
`function_call_output` error back to the model: "apply_patch: missing 'diff'
for create_file; include file content as operation.diff." The model retries with
the right shape. No task failure, no user involvement.

Covers: apply_patch argument coercion, web_search structural validation, dropped
tool feedback, unknown tool feedback, Codex-native tool passthrough.

See: `proxy/qz_proxy_tools.py`, `docs/tool-coercion-design.md`.

**Compaction signal preservation**
Status: ✅ shipped 2026-05-11

Old tool outputs in compacted conversation history used to become an opaque
"payload dropped" placeholder. Now the placeholder carries the essential signal:
"Tool exec_command (call_1): FAILED. Output: No such file or directory."
The model retains success/failure context even for old turns.

See: `proxy/qz_responses.py:_tool_output_signal()`.

**Stack health state (QZSTATE)**
Status: ✅ implemented, but opt-in and incomplete

When `QZSTATE=1` is set, the proxy injects a compact state block into
instructions:

```
<QZSTATE v=1 ready=1 load=loaded ctx=262144 prof=caveman sel=caveman>
```

Fields: ready, load_state, context_length, profile, selected model.

**Not included:** context usage, hop budget, token counts, reasoning budget.
**Not on by default.** Sessions without QZSTATE get no environmental signals.

See: `proxy/qz_model_router.py:runtime_state_block()`.

**Tool execution result passthrough**
Status: ✅ working

apply_patch results (success/failure/error) are explicitly normalized and reach
the model. exec_command and shell_command results come back as
`function_call_output` and pass through to llama.cpp natively. Codex execution
errors are not lost.

---

### Planned / not implemented

**Context window pressure**
Priority: HIGH

The model does not know how full its context window is. At 85% of 256k tokens
it continues building long responses, requesting more tool calls, and reasoning
at full depth — exactly when it should be more concise and convergent.

What to add to QZSTATE or per-turn metadata:
```
ctx_used=218000 ctx_max=262144 ctx_pct=83
```

The model can read this and self-manage: shorter answers, tighter tool use,
earlier summarization of what it has learned.

Implementation: the proxy receives `usage.input_tokens` in each `response.completed`
event. It can track cumulative context usage per session and inject it. llama.cpp
also reports context usage via `/props` and health endpoints.

**Hop budget**
Priority: HIGH

The model does not know how many continuation hops it has remaining. It behaves
identically on hop 1 and hop 5 of 6. On the last hop the proxy emits a fallback
message the model never asked for. This is disorienting.

What to add:
```
hops_used=4 hops_max=6 hops_remaining=2
```

With this, the model can adapt: on hop 5 of 6, prefer direct answers over more
tool calls. Stop reasoning deeply and converge. Avoid starting a new search that
won't complete before the cutoff.

Implementation: the proxy already tracks hop count in the stream runtime loop.
Injecting it per-hop requires threading it into the body metadata or a per-turn
instruction block.

**Reasoning budget**
Priority: MEDIUM

The proxy controls `QZ_REASONING_BUDGET` at the backend level. The model doesn't
know how much reasoning budget it has or how much it has spent. For a complex
task on a low-budget profile, the model may reason past the limit without knowing
it.

What to add:
```
reasoning_budget=8192 reasoning_mode=low
```

The model can calibrate reasoning depth to the available budget.

**Backend error vs task error distinction**
Priority: MEDIUM

When llama.cpp returns a 500 or times out mid-stream, the model currently sees
nothing — the stream ends. It cannot distinguish "my tool call failed because I
gave bad arguments" from "the backend had a transient error and I should retry
as-is." These require completely different responses.

What to add: a synthetic error message injected into the conversation when the
proxy detects a backend error (not a model error):
"Backend error: upstream returned 500. Your tool call was not executed. Retry."

Implementation: the stream runtime already detects backend failures. Injecting
a model-visible signal requires the error-feedback path from the coercion system
(kind="error" → function_call_output).

**Web search quality signals**
Priority: MEDIUM

When a search returns zero results, hits only quarantined engines, or uses the
low-result fallback, the model gets an empty results list or a generic error.
It tends to retry the same query. The signal needed is WHY the search produced
poor results.

What to add to search results:
```json
{
  "search_quality": "low_result_fallback_used",
  "engines_available": 3,
  "engines_tried": 5,
  "results_before_fallback": 0
}
```

The model can then try a different query, a different profile, or open a page
directly.

Implementation: `qz_tool_web.py` already has the routing and fallback logic;
adding this metadata to the result payload is additive.

**Search result provenance / quality scoring**
Priority: LOW

The model treats all search results as equally credible. A primary source and
a low-signal mirror look the same. A `"source_quality": "primary"` vs
`"mirror"` vs `"low_signal"` field per result would let the model weight
evidence and prioritise opening the primary source.

**Tool call provenance in error results**
Priority: LOW

The coercion system injects error results as `function_call_output`. The model
cannot distinguish "tool executed but failed" from "proxy couldn't form a valid
call." Both look like an error output. Adding a `"source": "proxy_coercion"` vs
`"source": "codex_execution"` field would let the model reason about whether
it should fix its call shape or try a different approach.

---

## The bigger picture

These signals cluster into two categories:

**Self-management signals** (context pressure, hop budget, reasoning budget):
The model needs these to make good decisions about how to proceed within its
constraints. A model that knows it has 2 hops left on a 90%-full context behaves
very differently from one that doesn't. Without these, the proxy has to impose
hard limits that surprise the model at the worst possible moment.

**Quality signals** (tool errors, search quality, backend errors, result
provenance): The model needs these to know whether its actions are having the
intended effect. The coercion system delivered the first tier. The remaining
items deliver finer-grained feedback for more precise self-correction.

Together these form the LLM signal system: the proxy's role is not just to
translate formats but to ensure the model has the information it needs to make
good decisions under the constraints of the local stack.

---

## Why this is a differentiator

Hosted LLMs get these signals from the platform — OpenAI knows the context
window, manages hops, knows the backend state. Local proxies typically don't
bother because the model "just works" against a stable hosted API.

QuantZhai is different: the backend is a local llama.cpp server with real
constraints (context limit, hop budget, reasoning budget, backend health).
Without explicit signals, the model acts as if it has unlimited resources and
a reliable backend — which it doesn't. The signal system closes this gap.

Coercion in particular is the most visible part of this: it makes the model
self-correcting at the tool-call level, which is the most common failure mode
in local agentic sessions. A proxy that silently drops malformed calls and a
proxy that recovers them and continues are qualitatively different products.

---

## Implementation order

1. **Hop budget** — inject `hops_used` / `hops_remaining` per-turn. Low risk,
   high value, contained change in the stream runtime.
2. **Context pressure** — inject `ctx_used` / `ctx_pct` from usage data. Requires
   accumulating token counts across hops; moderate complexity.
3. **Backend error injection** — reuse coercion's kind="error" path when the
   upstream returns a non-200. Contained.
4. **Web search quality metadata** — additive to existing result payload.
5. **Reasoning budget** — inject from QZ_REASONING_BUDGET; low complexity.
6. **Search provenance / tool error provenance** — lower priority; more data
   needed first.

---

## Related documents

- `docs/tool-coercion-design.md` — the coercion system that kicked this off
- `docs/compaction-bridge-plan.md` — the compaction system needs its own signals
- `docs/conversation-history-audit-plan.md` — related: what does the model
  actually see in history?
- `proxy/qz_model_router.py` — current QZSTATE implementation
- `proxy/qz_responses_stream.py` — hop tracking, context accumulation
