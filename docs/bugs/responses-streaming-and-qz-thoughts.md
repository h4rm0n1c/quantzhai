# Responses SSE Streaming and `qz-thoughts` Monitor Bugs

## Status

End-to-end audited on 2026-05-07. `qz-thoughts` delta coalescing, lightweight
stream timing telemetry, runtime summary-mode transformation, synthetic
terminal `data: [DONE]` forwarding, and per-request stream telemetry
correlation are implemented and live-smoked. Remaining work is profile/runtime
behaviour for upstream reasoning-only completions.

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

The runtime transform now accepts both line-split SSE chunks and complete SSE
blocks, so `summary` mode applies on the live streaming path as well as in
offline transform tests.

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

The current stream/tool state contract is documented in
`docs/responses-stream-tool-state-contract.md`. Use that contract as the next
source of truth when turning these incident notes into replay fixtures.

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

## 2026-05-07 audit result

Live audited through a temporary capture-enabled proxy against the real
TurboQuant backend:

```text
client -> patched proxy -> llama.cpp/TurboQuant -> proxy transform -> client SSE
/qz/telemetry/stream
/qz/telemetry/recent
qz-top --once
qz-thoughts --once
```

Observed for request `qz_req_1778145026771_bbf0`:

```text
upstream events: 389
upstream reasoning_text.delta: 384
upstream output_text.delta: 0
upstream [DONE]: 0

client events: 391
client reasoning_summary_text.delta: 384
client reasoning_text.delta: 0
client output_text.delta: 0
client response.completed: 1
client [DONE]: 1

telemetry events for request: 781
sse_event records: 388
stream_event_timing records: 389
avg parsed_to_forwarded_ms: 0.172
max parsed_to_forwarded_ms: 2.717
suppressed duplicate response start: 1
```

Conclusions:

```text
Transport is healthy for this path.
Summary-mode transform does not leak raw reasoning_text events to the client.
Terminal forwarding is correct: response.completed once, [DONE] once.
Timing telemetry is low-latency and request-correlated.
qz-thoughts reads proxy telemetry and no longer spams per-delta activity rows.
qz-top reads shared status/telemetry and reports the same request throughput.
```

Remaining issue:

```text
The backend can spend the whole completion budget in reasoning_text and emit no
output_text deltas. In that case transport is correct but ANSWER is empty in
qz-thoughts and the client receives only reasoning_summary_text deltas. Fix this
in profile/reasoning preset policy or classify/report reasoning-only
completions explicitly.
```

Update: proxy now classifies this as a reasoning-only stall when a stream emits
reasoning deltas without any answer/tool item and then makes no progress past
`QZ_REASONING_ONLY_TIMEOUT_S`. `QZ_REASONING_ONLY_CHAR_LIMIT` is disabled by
default (`-1`) because llama.cpp can misclassify legitimate generated artifact
text as `reasoning_text`. Operators can set a positive char limit for aggressive
local debugging, but production/default behavior must not abort active output
only because it is long. On abort, the proxy emits a completed fallback answer
plus `reasoning_only_aborted` telemetry instead of leaving Codex with no final
status.

Mitigation update: the backend launch exposes llama.cpp's `--reasoning-budget`
as `QZ_REASONING_BUDGET` and defaults it to `-1`. This is a backend generation
control, separate from the proxy's fallback watchdog. Positive values are for
deliberate cap testing only; the default avoids killing legitimate long
reasoning before answer/tool output.

## 2026-05-08 request-scoped audit result

Added request-scoped captures under:

```text
var/captures/requests/<request_id>/
```

This avoids treating `latest-*` files as proof when concurrent Codex, monitor,
or smoke requests can overwrite them.

Live prompt-compiler probe with `apply_patch` declared:

```text
request_id: qz_req_1778171144593_2c0
incoming tools: [{"type":"apply_patch"}]
forwarded tools: function apply_patch adapter
tool_choice: auto
upstream events: 244
upstream reasoning_text.delta: 187
upstream output_text.delta: 47
forwarded events: 245
forwarded reasoning_summary_text.delta: 187
forwarded output_text.delta: 47
client events: 246 including codex.rate_limits and [DONE]
reasoning chars: 863
answer chars: 237
avg parsed_to_forwarded_ms: 0.364
max parsed_to_forwarded_ms: 3.745
```

Conclusion: for this path, transport and channel mapping were healthy. The
proxy translated raw reasoning text to summary text, forwarded normal answer
deltas, and ended the stream correctly.

Same prompt-compiler probe without tools:

```text
request_id: qz_req_1778171217515_3a00
incoming tools: none
forwarded tools: none
upstream events before abort: 615
upstream reasoning_text.delta: 612
upstream output_text.delta: 0
forwarded reasoning_summary_text.delta: 611
forwarded output_text.delta: 0
reasoning chars at abort: 2502
reasoning_only_aborted reason: char_limit
client received response.completed and [DONE]
fallback answer present in response.completed.output[0]
avg parsed_to_forwarded_ms: 0.352
max parsed_to_forwarded_ms: 1.405
```

Conclusion: the reasoning-only failure reproduces without `apply_patch` or any
tool declaration. Tool exposure is not the primary cause of this class of stall.
The likely fault line is prompt/profile + Qwen reasoning behavior + llama.cpp
Responses channel output, not a Codex tool-policy mismatch.

Follow-up: request `qz_req_1778172550606_da50` showed the 12k char watchdog
cutting legitimate prompt-compiler output. Upstream emitted about 12,008 chars
as `response.reasoning_text.delta`, but the tail was artifact/body text rather
than hidden scratch. The default char watchdog was therefore removed; use the
idle timeout for true no-progress stalls.

Follow-up: public Codex tool-call state was too coarse, but executable tool
start events cannot safely be synthesized with empty arguments. A live prompt
showed Codex treating `response.output_item.added` for `function_call` as
runnable immediately, before `response.function_call_arguments.done`, which
produced an empty-argument tool execution and destabilized the next upstream
request. The proxy therefore buffers upstream executable tool calls until
arguments are complete, then emits one complete public tool item. Proxy-local
tools such as `web_search` remain private until the proxy has a completed public
display item.

The same live run left a malformed empty-argument `function_call` plus Codex's
local parse-error `function_call_output` in conversation history. Replaying that
pair to llama.cpp can trigger a 500 on the next request before any stream events
arrive. Input normalization now drops empty `function_call` history items and
their parse-error outputs before forwarding to upstream.

Follow-up: request `qz_req_1778177240868_e0d0` showed the model streaming an
`apply_patch`-shaped JSON/diff payload for `amber_v4.md` entirely through
`response.reasoning_text.delta`. It never emitted `output_text` or a real
`function_call`, so the idle timeout did not apply because tokens were still
arriving. The proxy now treats patch/tool-shaped payloads in reasoning-only
streams as a protocol failure and emits a completed fallback answer plus
`reasoning_only_aborted` telemetry with `reason=artifact_tool_payload`. It does
not execute hidden reasoning as a tool call.

Open compliance wrinkle: the fallback answer is currently present in the
synthetic `response.completed` output, but not streamed as
`response.output_text.delta` before completion. Codex can still receive a final
completed response, but a more standard fallback stream would synthesize a
message output item and output_text delta/done sequence before
`response.completed`.

Follow-up prompt-compiler file-write failure:

```text
request_id: qz_req_1778171634737_8a10
user request: write compiled output to amber_v2.md
incoming tools: exec_command, write_stdin, custom apply_patch, ...
forwarded tools: exec_command, write_stdin, function apply_patch, ...
upstream reasoning_text.delta: 671
upstream output_text.delta: 0
upstream tool call: function_call write_stdin
upstream function_call_arguments.delta: 1201
forwarded public tool calls: 0
telemetry: private_tool_call_aborted reason=delta_limit tool_name=write_stdin
```

Conclusion: this was not an `apply_patch` adapter failure. The model selected
`write_stdin`, hallucinated an exec session id, and streamed a shell heredoc
instead of returning an `apply_patch` call. The proxy correctly avoided
forwarding an incomplete private tool call, but the request had exposed
`write_stdin` even though no live exec session was present in request history.

Mitigation: `normalize_tools_for_llamacpp` now drops `write_stdin` unless prior
request history contains an exec session id from an `exec_command` tool output,
and adds generic file-edit hints to shell tools. This is a Codex tool-contract
sanity check, not prompt-compiler-specific policy.

## Minimal fixes likely needed

### 1. Coalesce qz-thoughts activity rows

Status: implemented in `scripts/qz-thoughts`. Delta events now update rolling
thought/answer state rows, while lifecycle events remain in activity. Monitor
polling noise such as `/qz/status`, `/health`, and telemetry endpoints is
filtered from the activity feed. `tests/test_qz_thoughts_cli.py` covers the
`--once --file` rendering path with tiny reasoning and answer deltas.
Default mode now reads proxy telemetry only; raw capture replay requires
explicit `--file`, so stale `latest-*` captures cannot masquerade as live
thought state.

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

Status: implemented in `proxy/qz_responses_stream.py`. Each parsed upstream SSE
event emits lightweight `stream_event_timing` telemetry with parse, forward, and
telemetry lag fields. Stream timing and forwarded SSE telemetry carry the
request id for request-level correlation. `qz-top` and `qz-thoughts` ignore
these timing events in normal activity views so the monitor stays low-noise.

Emit timing counters around the stream path:

```text
upstream_event_received
sse_event_parsed
sse_event_forwarded
telemetry_event_emitted
```

Keep it lightweight. Do not spam the normal monitor.

### 3. Audit summary-mode transform

Status: implemented for the runtime transform path. Live audit confirmed
`response.reasoning_text.delta` is no longer forwarded in summary mode,
`response.reasoning_summary_text.delta` is forwarded, assistant answer deltas
still stream, and the proxy emits a final `data: [DONE]` marker even when
upstream closes after `response.completed`.

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
latest-request-id.txt
latest-upstream-response.raw
latest-forwarded.json
latest-stream-runtime-error.txt
latest-upstream-status.txt
requests/<request_id>/incoming-request.json
requests/<request_id>/forwarded-request.json
requests/<request_id>/request-contract.json
requests/<request_id>/upstream-response.raw
requests/<request_id>/upstream-status.txt
requests/<request_id>/forwarded-sse.raw
```

Request-scoped captures are the audit source of truth when multiple Codex or
monitor requests are active. `latest-*` files are only a convenience view and
may be overwritten by later traffic.

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
docs/responses-stream-tool-state-contract.md
docs/runtime-observability-notes.md
```
