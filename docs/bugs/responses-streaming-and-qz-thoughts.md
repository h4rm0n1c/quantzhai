# Responses SSE Streaming and `qz-thoughts` Monitor Bugs

## Status

Open. Needs audit before implementation.

This is a known streaming/observability problem. It affects the feel of Codex output, the correctness of Responses API SSE forwarding, and the usefulness of `qz-thoughts` as a live reasoning monitor.

## Summary

QuantZhai currently shows signs of two related failures:

```text
1. Data is not always pushed through to Codex as soon as it safely can be.
2. qz-thoughts renders reasoning/thought telemetry as noisy micro-events such as "thought +1 chars".
```

The result feels wrong in both directions:

```text
Codex side:      waits, batches, or feels like "crunch then paste" instead of fast streaming.
qz-thoughts side: floods with loopy +1/+2 char activity rows instead of useful live state.
```

The core suspicion is that the proxy, SSE transform layer, and monitor are not using the same event contract.

## Observed symptoms

`qz-thoughts` can show repeated rows like:

```text
thought +1 chars
thought +1 chars
thought +4 chars
thought +2 chars
```

The visible monitor becomes loopy and hard to trust. It looks like the model is repeating itself, even when the renderer may simply be showing every tiny reasoning delta as a separate activity item.

At the same time, Codex does not always receive useful output as soon as possible. The stream can feel like it is buffering or transforming too much before forwarding.

## Relevant code paths

### SSE transformation

`proxy/qz_sse.py` contains the core SSE response block generation and reasoning-summary transformation.

Known risk areas:

```text
transform_sse_event()
make_response_stream_events()
reasoning_text -> reasoning_summary_text conversion
summary_started handling
```

The summary mode currently converts `response.reasoning_text.delta` into `response.reasoning_summary_text.delta`. If the upstream model emits one-character or tiny deltas, the proxy can forward those as tiny summary deltas.

That may be technically faithful, but it is not useful as a monitor signal.

### Streaming runtime loop

`proxy/qz_responses_stream.py` runs the local Responses SSE tool-continuation loop.

Known risk areas:

```text
ResponsesStreamRuntime.run()
resp.readline() loop
parse_sse_event_lines()
_transform_chunks()
function-call suppression and tool-loop continuation
terminal event handling
```

The loop forwards complete SSE event frames, not arbitrary bytes. That is correct for SSE validity, but it needs an audit to ensure it forwards each completed event immediately after parsing and does not wait for avoidable local processing.

Tool-call handling intentionally suppresses function-call stream events while assembling calls. That may be correct for the local tool-loop, but it must not accidentally suppress normal assistant text or reasoning events.

### SSE parser and function-call assembler

`proxy/qz_streaming.py` parses SSE blocks and assembles function calls.

Known risk areas:

```text
parse_sse_event_lines()
is_function_call_stream_event()
is_terminal_stream_event()
StreamedFunctionCallAssembler.observe()
```

The parser and assembler should be checked for assumptions that work for synthetic streams but fail against llama.cpp/TurboQuant real SSE chunking.

### qz-thoughts monitor

`scripts/qz-thoughts` currently appends an activity row for every reasoning and answer delta.

Known bad behaviour:

```text
response.reasoning_text.delta          -> activity row: thought +N chars
response.reasoning_summary_text.delta  -> activity row: thought +N chars
response.output_text.delta             -> activity row: answer +N chars
```

This makes the monitor noisy and misleading.

`qz-thoughts` should maintain rolling state, not treat every tiny delta as a high-level activity.

## Design rule

Streaming has two different consumers and they need different views:

```text
Codex client:        valid Responses API SSE, forwarded as soon as safely possible.
Human monitor:       coalesced live state, low-noise activity, useful progress signals.
Debug capture files: raw enough to diagnose transport bugs.
```

Do not confuse those views.

A raw SSE delta is not automatically a useful human-facing activity row.

## Required audit

Before patching, capture one real failing session with:

```text
raw upstream SSE
transformed forwarded SSE
telemetry events
qz-thoughts view
Codex visible behaviour
```

Compare event timing and content across each stage:

```text
upstream received time
proxy parsed time
proxy forwarded time
telemetry emitted time
qz-thoughts rendered time
Codex displayed time if observable
```

The audit should answer:

```text
Is the proxy delaying completed SSE events?
Are output_text deltas forwarded immediately?
Are reasoning deltas being transformed into too many summary deltas?
Are function-call events being suppressed correctly and only when needed?
Are terminal events being forwarded exactly once?
Are response.created / response.in_progress emitted once per logical response?
Does the local web/tool continuation loop create duplicate or malformed streams?
Does qz-thoughts mix backend telemetry and response SSE in a confusing order?
```

## Minimal fixes likely needed

### 1. Coalesce qz-thoughts activity rows

Status: implemented in `scripts/qz-thoughts`. Delta events now update rolling
thought/answer state rows, while lifecycle events remain in activity. Monitor
polling noise such as `/qz/status`, `/health`, and telemetry endpoints is
filtered from the activity feed.

Do not append a new activity row for every delta.

Instead:

```text
Maintain thought text buffer.
Maintain answer text buffer.
Show one rolling status row per active stream, e.g.:
  thought streaming... 842 chars
  answer streaming... 120 chars
Only add activity rows for lifecycle events:
  response created
  output item added/done
  reasoning done
  answer done
  tool call started/done
  stream completed/failed
```

This should make `qz-thoughts` useful without changing wire behaviour.

### 2. Add stream timing telemetry

Emit timing counters around the stream path:

```text
upstream_event_received
sse_event_parsed
sse_event_forwarded
telemetry_event_emitted
```

Keep it lightweight. Do not spam the normal monitor.

### 3. Audit summary-mode transform

Check whether `reasoning_text.delta` -> `reasoning_summary_text.delta` should be passed through as-is, coalesced, hidden, or converted only on done.

Possible modes:

```text
raw      = pass raw reasoning events
summary  = show summary events, but consider coalescing tiny deltas
hidden   = strip reasoning events
debug    = expose exact raw/transformed deltas for diagnosis
```

Do not pretend character-by-character summary deltas are meaningful human summaries.

### 4. Confirm immediate forwarding to Codex

For non-tool assistant output, the proxy should forward each completed SSE event frame as soon as it is parsed and transformed.

It should not wait for:

```text
full response completion
large local buffers
capture file writes
qz-thoughts telemetry
tool-loop completion unless the event is part of a suppressed local tool call
```

### 5. Keep raw captures separate from UX

Raw captures should preserve the ugly stream when needed.

Human tools should render a cleaner state view.

## What not to do

Do not fix this by only lowering reasoning effort.

Do not fix this by hiding all thought telemetry globally.

Do not fix this by adding another one-off shell script.

Do not fold monitor logic into `qz-up`, `qz-down`, or `qz-codex`.

Do not treat every upstream delta as a human-readable activity event.

## Acceptance tests

### Test 1: qz-thoughts no longer floods activity with micro-deltas

Given a stream with many tiny reasoning deltas:

```text
response.reasoning_summary_text.delta: "I"
response.reasoning_summary_text.delta: "'ll"
response.reasoning_summary_text.delta: " read"
```

Expected monitor behaviour:

```text
THOUGHT panel updates live.
ACTIVITY does not add one row per tiny delta.
ACTIVITY shows one rolling or periodic thought status row.
```

### Test 2: normal assistant text streams promptly to Codex

Given upstream emits output text deltas over time:

```text
response.output_text.delta: "hello"
response.output_text.delta: " world"
```

Expected:

```text
Proxy forwards each completed SSE event frame promptly.
Codex displays incremental output instead of waiting for response.completed.
Capture timestamps show low delay between parse and forward.
```

### Test 3: function-call stream suppression remains correct

Given a streamed tool/function call:

```text
response.output_item.added function_call
response.function_call_arguments.delta
response.function_call_arguments.done
response.output_item.done function_call
```

Expected:

```text
Local tool call assembly still works.
Suppressed function-call internals are not leaked incorrectly.
Public tool item events are emitted correctly.
Normal assistant output around the tool call is not suppressed.
```

### Test 4: terminal events are correct

Expected:

```text
response.completed emitted once per logical response.
[DONE] emitted once.
No duplicate response.created after web/tool continuation hops.
No malformed sequence numbers caused by transformed events.
```

### Test 5: captures still diagnose raw bugs

Expected files should remain useful:

```text
latest-upstream-response.raw
latest-forwarded.json
latest-stream-runtime-error.txt
latest-upstream-status.txt
```

If new timing captures are added, they should live under `var/captures/` or structured telemetry, not as another permanent shell script.

## Relationship to config/error plan

This bug should be handled under the broader principles in `docs/edge-case-config-contract-plan.md`:

```text
auditable data paths
clear contracts between runtime layers
compact user-facing errors
separate raw debug data from user-facing status
reduce script sprawl
```

## Related files

```text
proxy/qz_sse.py
proxy/qz_responses_stream.py
proxy/qz_streaming.py
scripts/qz-thoughts
docs/edge-case-config-contract-plan.md
docs/observability-streaming-bugfix-agenda.md
docs/runtime-observability-notes.md
```
