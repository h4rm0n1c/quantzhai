# Responses Stream and Tool State Contract

Status: living contract for QuantZhai's current `/v1/responses` streamed path.
Last reconciled: 2026-05-09.

This document defines how QuantZhai should translate upstream llama.cpp/TurboQuant
SSE into Codex-visible Responses events, local telemetry, and debug captures. It
is not a complete OpenAI API specification. The implementation and tests remain
the local source of truth; external references are protocol and compatibility
anchors only.

## Scope

This contract covers:

- streamed `POST /v1/responses` requests through the proxy
- reasoning, answer, function-call, and terminal SSE events
- local tool continuation for supported proxy tools
- Codex-visible events versus private proxy state
- telemetry and request-scoped capture expectations

It does not define a general filesystem, shell, browser, MCP, or computer-use
tool runtime. Those are still out of scope unless they are explicitly added and
tested as proxy-executed tools.

## Proxy-Local Tool Boundary

Status: first pass implemented.

QuantZhai distinguishes protocol adapters from proxy-local executors.

Protocol adapters translate between Codex's client-facing tool contract and a
Qwen-friendly function-call contract, then hand execution back to Codex. They
must not execute the tool in the proxy. `apply_patch` is the current protocol
adapter and keeps filesystem writes under Codex's sandbox and approval model.

Proxy-local executors are tools QuantZhai actually runs. They own local
execution, hidden upstream continuation items, Codex-visible display items, and
tool telemetry. `web_search` is the current proxy-local executor.

If Codex already has a safe built-in execution path for a tool, prefer a
protocol adapter. Add a proxy-local executor only when local execution is
required and its safety, telemetry, replay, and event shape are documented and
tested.

QuantZhai currently has a `ToolAdapter` registry for declaration and item-shape
normalization. Each adapter exposes a `ToolLifecycleSpec` with one execution
mode:

- `protocol_adapter`: QuantZhai adapts the tool shape and Codex executes it.
- `proxy_local`: QuantZhai executes the tool and sends hidden continuation state
  upstream.

Proxy-executed tools have a `ProxyLocalToolRegistry` for completed-call
classification, public protocol-adapter conversion, proxy-local execution, and
continuation-result shaping. The registry also exposes the lifecycle spec used
by streaming code for Codex-visible event shape, continuation hop budget, and
progress/completed event stages. The registry builds proxy-local start/done
lifecycle SSE chunks from that spec. `web_search` is the first proxy-local
executor and is used by both streamed and non-streamed `/v1/responses` paths.
The registry path is also pinned with a test-only `qz_probe` executor so the
contract is not only proven through web-search-specific behavior.

Codex-facing output adaptation is adapter-owned. The generic `ToolRegistry`
can normalize a list of output items back to the client shape, and the
non-streamed Responses path uses that registry instead of calling a
tool-specific patch helper directly.

Completed-call routing is registry-owned. The lifecycle helpers do not carry
their own default list of private tool names or protocol-adapter conversions,
and the stream runtime does not call adapter-specific public-output helpers
directly. Streamed runtimes ask the active `ProxyLocalToolRegistry` to classify
completed calls and return either a Codex-visible public item or a proxy-local
continuation result. This keeps future proxy-side tools from requiring a second
hard-coded allowlist, event-prefix branch, or public conversion branch.

A proxy-local executor owns:

- the model-facing function name
- its `ToolLifecycleSpec`
- completed-call classification
- argument validation
- local execution
- hidden upstream continuation items
- Codex-visible display items
- request-scoped telemetry
- failure-to-message behavior

Request-scoped telemetry payload shaping is owned by
`RequestTelemetryEmitter` in `proxy/qz_telemetry.py`. Stream runtimes use that
helper to inject the active `request_id`, build standard
`stream_event_timing` payloads, and keep telemetry failures from breaking the
client stream.

Stream-specific lifecycle writes still live in `qz_responses_stream.py` because
forwarded byte accounting is owned by the stream runtime. Proxy-local lifecycle
stage selection and SSE chunk construction are registry-owned. `apply_patch`
remains a protocol adapter and Codex execution handoff path unless a separate
security review explicitly adds proxy-side filesystem writes.

Proxy-local telemetry payloads, terminal-suppression reason names, and
continuation-limit fallback text are registry-owned. Stream and non-stream
runtime paths should ask `ProxyLocalToolRegistry` for those values instead of
adding tool-name-specific branches.

## Source of Truth

Implementation:

```text
proxy/qz_responses_stream.py   streamed Responses runtime and continuation loop
proxy/qz_streaming.py          SSE parser and streamed function-call assembler
proxy/qz_responses.py          compatibility exports, output cleanup, and
                               compaction helpers
proxy/qz_request_normalization.py
                               Responses input history cleanup, harness/meta
                               filtering, content canonicalization, prompt
                               policy assembly, and local compaction replay
                               markers
proxy/qz_tool_request.py       tool declaration normalization, tool-choice
                               adaptation, write_stdin gating, tool policy
                               metadata, and capture notes
proxy/qz_proxy_tools.py        completed-call routing, public adapter conversion,
                               proxy-local tool registry, execution context,
                               and continuation-result shaping
proxy/qz_tool_lifecycle.py     private streamed tool-call state and malformed
                               historical tool-call filtering
proxy/qz_tool_apply_patch.py   apply_patch envelope adaptation
proxy/qz_runtime_io.py         capture policy, latest/request-scoped dual
                               writes, request path sanitization, and runtime
                               file helpers
proxy/qz_telemetry.py          status and telemetry events
proxy/qz_request_router.py     request id and routing envelope
```

Regression tests:

```text
tests/test_qz_responses_stream.py
tests/test_qz_streaming.py
tests/test_qz_proxy_tools.py
tests/test_qz_tool_lifecycle.py
tests/test_qz_request_normalization.py
tests/test_qz_tool_request.py
tests/test_apply_patch_adapter.py
tests/test_qz_runtime_io.py
tests/test_qz_thoughts_cli.py
```

## Capture Policy

Status: first ownership pass implemented.

`proxy/qz_runtime_io.py` owns the capture-mode interpretation and the helper
API for writing latest convenience files and request-scoped files. Capture mode
`off` writes nothing. Current enabled modes preserve existing behaviour:
`latest`, `minimal`, and `full` write both latest and request-scoped captures,
including raw stream files.

Use latest files as operator convenience only:

```text
var/captures/latest-*.*
```

Use request-scoped files as replay/audit evidence:

```text
var/captures/requests/<request_id>/
```

Code that has a request id should prefer `write_dual_capture()` or
`append_dual_capture()` so latest and request-scoped artifacts stay paired.
Direct `capture_path(...).write_*` calls should not be added outside the runtime
IO helper.

Responses normalization fixtures:

```text
tests/fixtures/responses_input/malformed_empty_tool_history.json
tests/fixtures/responses_input/mixed_history_normalization.json
tests/fixtures/responses_input/tool_declaration_normalization.json
```

These pin replay cleanup before Qwen sees the request: malformed empty tool
history is removed, stale harness/meta/reasoning items are dropped, message
content is canonicalized, supported tool declarations are converted to
llama.cpp-friendly function tools, and client-facing apply_patch history is
adapted back to upstream function-call history.

Stream/tool fixtures:

```text
tests/fixtures/sse/web_search_call.raw
tests/fixtures/sse/web_search_final.raw
tests/fixtures/sse/public_function_call.raw
tests/fixtures/sse/apply_patch_call.raw
tests/fixtures/sse/custom_apply_patch_call.raw
```

These pin streamed proxy-local continuation, normal public function passthrough,
and protocol-adapted `apply_patch`. `tests/test_qz_responses_stream.py` also
pins multi-hop proxy-local continuation through the generic registry lifecycle
rather than direct `web_search` branching, plus `tool_call_started` and
`tool_call_completed` telemetry from the lifecycle spec.

Useful forensic captures retained locally:

```text
var/captures/requests/qz_req_1778171634737_8a10
var/captures/requests/qz_req_1778172550606_da50
var/captures/requests/qz_req_1778177240868_e0d0
var/captures/requests/qz_req_1778240861756_fad0
```

Do not treat `latest-*` capture files as proof when concurrent Codex, monitor,
or smoke requests may have overwritten them. Prefer request-scoped captures.

## External References

These references describe the shapes QuantZhai is trying to interoperate with:

- OpenAI Responses streaming guide:
  `https://platform.openai.com/docs/guides/streaming-responses`
- OpenAI Responses `response.function_call_arguments.done` event:
  `https://platform.openai.com/docs/api-reference/responses-streaming/response/function_call_arguments/done`
- OpenAI Responses web search call events:
  `https://platform.openai.com/docs/api-reference/responses-streaming/response/web_search_call`
- WHATWG server-sent events:
  `https://html.spec.whatwg.org/dev/server-sent-events.html`
- OpenAI Agents SDK Python streaming:
  `https://openai.github.io/openai-agents-python/streaming/`
- OpenAI Agents SDK JS streaming:
  `https://openai.github.io/openai-agents-js/guides/streaming/`
- LiteLLM proxy documentation:
  `https://docs.litellm.ai/`
- LiteLLM Responses API notes:
  `https://docs.litellm.com.cn/docs/response_api`
- Open Responses project:
  `https://www.openresponses.org/`
- llama.cpp function calling:
  `https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md`
- llama.cpp issue on `arguments` object versus JSON string compatibility:
  `https://github.com/ggml-org/llama.cpp/issues/20198`
- llama.cpp issue on malformed or incomplete JSON arguments:
  `https://github.com/ggml-org/llama.cpp/issues/22072`
- QwenPaw issue on Qwen3.6 plus llama.cpp streaming tool-call parse failures:
  `https://github.com/agentscope-ai/QwenPaw/issues/3560`
- vLLM tool-calling documentation:
  `https://docs.vllm.ai/en/stable/features/tool_calling/`
- vLLM issue on streaming tool-call edge cases:
  `https://github.com/vllm-project/vllm/issues/16340`

QuantZhai should not blindly copy another proxy's behavior. Use these as
evidence that streamed tool-call assembly, argument typing, and text-shaped tool
artifacts are common compatibility traps.

## Core Rules

1. Forward complete SSE event frames, not arbitrary byte chunks.
2. Treat raw upstream deltas as protocol events, not human activity rows.
3. Never expose a runnable public tool call before its arguments are complete.
4. Keep proxy-local tool calls private until the proxy has a complete, safe
   Codex-visible item or result.
5. Never execute hidden reasoning or artifact-shaped reasoning text as a tool.
6. Drop malformed empty tool-call history before replaying context upstream.
7. Keep telemetry/status live and request-scoped captures replayable.
8. Emit one terminal completion path per logical client stream.

## Codex-Facing Live Lifecycle Relay

Status: explicit open hardening task.

Observed gap:

```text
Hosted OpenAI-backed Codex sessions visibly relay more live state while tools
run. QuantZhai currently has proxy-local telemetry and replay captures, but the
Codex-facing SSE stream can still look too two-step: a request starts, internal
tool/model work happens, and then a completed result appears.
```

Target behavior:

- Emit `response.created` and `response.in_progress` as one coherent lifecycle.
- Emit output-item start events with `status: "in_progress"` when Codex can
  safely render them.
- For public tool calls, emit a runnable item only after arguments are complete,
  but preserve the lifecycle shape Codex expects for tool start/running/done.
- For proxy-local/private tools, emit no unsafe function call. For local
  `web_search`, emit the built-in Responses web-search lifecycle events before
  and after proxy execution.
- Emit completed/failed/incomplete terminal status for tool calls and outputs.
- Preserve final `usage` data in `response.completed` where upstream or proxy
  accounting can provide it.
- Keep generated model catalog metadata in sync with proxy policy so Codex CLI
  `/status` sees the selected context window and truncation limit.
- Keep final Responses `usage` populated so Codex CLI `/status` can report the
  latest turn token usage and cached-token split.

Useful external references:

- OpenAI Responses streaming guide:
  <https://platform.openai.com/docs/guides/streaming-responses>
- OpenAI Responses streaming event reference, especially
  `response.output_item.added`, `response.output_item.done`,
  `response.function_call_arguments.delta`, and terminal response events:
  <https://platform.openai.com/docs/api-reference/responses-streaming>
- OpenAI Responses item schemas for `apply_patch_call`, `shell_call`,
  `local_shell_call`, and MCP call statuses:
  <https://platform.openai.com/docs/api-reference/responses>
- OpenAI shell tool guide, which documents streamed `shell_call` items and
  `status: "in_progress"` / `status: "completed"` semantics:
  <https://platform.openai.com/docs/guides/tools-shell>
- OpenAI Codex agent-loop article, which confirms Codex CLI drives its loop via
  configurable Responses API endpoints and consumes `instructions`, `tools`,
  and `input` through that contract:
  <https://openai.com/index/unrolling-the-codex-agent-loop/>

These references define the protocol shape to emulate. They do not by
themselves prove which events the current Codex CLI UI renders, so this task
needs a real Codex capture against hosted/OpenAI-compatible Responses streams
before changing behavior broadly.

### Supported Codex Event-Shape Capture

Use supported Codex CLI output for client-lifecycle comparison. Do not MITM
hosted OpenAI traffic, store auth/session material, replay raw hosted requests,
or commit raw captures. The safe baseline command shape is:

```bash
codex exec --json --ephemeral --skip-git-repo-check --ignore-rules \
  --sandbox read-only -c approval_policy="never" -C /tmp \
  'Run exactly one shell command: pwd. Then respond with the single word done.'
```

For this tiny shell-command baseline, hosted OpenAI-backed Codex produced this
public JSONL lifecycle shape:

```text
thread.started
turn.started
item.started      command_execution status=in_progress
item.completed    command_execution status=completed
item.completed    agent_message
turn.completed    usage={input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}
```

The same prompt through `qz-codex exec --json --ephemeral` produced the same
shell-command lifecycle shape, plus one completed `reasoning` item before the
command:

```text
thread.started
turn.started
item.completed    reasoning
item.started      command_execution status=in_progress
item.completed    command_execution status=completed
item.completed    agent_message
turn.completed    usage={input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}
```

Long-running shell capture with the compiler/coding profile must force the
model explicitly, because the persistent local Codex config can be left on a
roleplay profile such as `example-roleplay`:

```bash
./scripts/qz-codex exec --json --ephemeral --skip-git-repo-check --ignore-rules \
  --sandbox workspace-write -c approval_policy="never" -m prompt-compiler \
  -C /tmp \
  "Run exactly one shell command: sh -c 'sleep 2; echo ok'. Then reply done."
```

Observed QZ JSONL shape for that case:

```text
thread.started
turn.started
item.completed    reasoning
item.started      command_execution status=in_progress
item.completed    command_execution status=completed aggregated_output="ok\n"
item.completed    agent_message text="done"
turn.completed    usage={input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}
```

Implication: the basic Codex shell-command start/completed lifecycle is already
visible through the QZ path in `codex exec --json`, including a command that
runs for multiple seconds.

Proxy-local `web_search` was live-smoked through `qz-codex exec` on
2026-05-09 with an explicit `-m prompt-compiler` model selection:

```bash
./scripts/qz-codex exec -m prompt-compiler --json --ephemeral \
  --skip-git-repo-check --ignore-rules --sandbox read-only \
  -c approval_policy="never" -C /tmp \
  "Use web_search exactly once to search for QuantZhai. Then answer in one sentence with one result title or say no result."
```

Observed public JSONL shape:

```text
thread.started
turn.started
item.completed    reasoning
item.started      web_search action=search queries=["QuantZhai"]
item.completed    web_search action=search queries=["QuantZhai"]
item.completed    agent_message
turn.completed    usage={input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}
```

The matching `/qz/status` latest request was
`qz_req_1778292797704_7860`, status 200, model `prompt-compiler`,
with `usage.input_tokens=11303`, `usage.output_tokens=6`, and
`runtime_metrics.selected_context_length=262144`. The public Codex lifecycle is
therefore present for the proxy-local search path. Request-scoped capture files
were not retained for this smoke, and `/qz/telemetry/recent` did not retain the
request's web-search lifecycle rows by the time it was queried, so telemetry
retention was handled as a separate observability gap.

The proxy now retains a bounded per-request lifecycle summary for important
events such as request start/completion, throughput, prompt contract,
tool-call start/completion, private tool aborts, and reasoning-only aborts.
That retained summary is exposed through:

```text
/qz/status latest_request.latest_completed_events
/qz/telemetry/state latest_completed_events
/qz/telemetry/request?request_id=<id>
```

`qz-top` and `qz-thoughts` merge `state.latest_completed_events` with
`/qz/telemetry/recent` so completed tool lifecycle rows do not disappear from
local monitors just because the short recent ring has moved on.

Remaining live-state gaps should be investigated in the harder cases:

- live-Qwen streamed `apply_patch` edge cases beyond the hermetic fake-upstream
  handoff smoke
- long-running TUI rendering while tools are active
- long-running tool calls where progress is available only inside proxy
  telemetry

Codex CLI v0.125.0 TUI `/status` was checked with `prompt-compiler` after a
short streamed turn. It displayed the generated catalog's effective context
window as `10.6K used / 249K` and displayed final Responses usage as `527 total
(503 input + 24 output)`, with `+10,066 cached` printed on exit. This confirms
the supported Codex-facing status path is model-catalog metadata plus terminal
Responses `usage`; QuantZhai `/qz/status` remains a separate richer proxy
runtime surface.

Hermetic Codex apply_patch handoff is regression-pinned by
`tests/smoke_apply_patch_codex_exec.py`. The fake upstream emits a streamed
model-side `function_call` named `apply_patch`; the proxy rewrites it into the
Codex-declared patch shape; Codex applies the patch in a temp workspace; and
`codex exec --json` exposes this public lifecycle:

```text
item.started      file_change status=in_progress kind=add
item.completed    file_change status=completed kind=add
item.completed    agent_message
turn.completed    usage={input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}
```

The client-facing JSONL does not expose the intermediate `custom_tool_call` for
this path. Codex collapses the successful patch handoff into `file_change`
lifecycle rows, so that is the stable public assertion for the smoke.

Live Qwen/TurboQuant smoke on 2026-05-09 confirmed the same Codex-facing shape
for a real prompt-compiler run. `qz-codex exec -m prompt-compiler --json
--ephemeral` created `live-qwen-apply-patch-smoke.txt`, emitted one completed
reasoning item, then emitted `file_change` started/completed, final
`agent_message` text `done`, and terminal usage
`input_tokens=10723`, `cached_input_tokens=10605`, `output_tokens=4`,
`reasoning_output_tokens=0`. The proxy request id was
`qz_req_1778312516589_af70`.

## State Table

This table is evidence-backed. Each row names the owner code and the current
test, fixture, or live capture that pins the behavior. A row without evidence is
not a contract yet.

| State | Entry signal | Owner code | Proxy action | Codex-visible result | Telemetry/capture | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Response start | `response.created`, `response.in_progress` | `ResponsesStreamRuntime.run`, `is_terminal_stream_event` helpers | Forward first lifecycle start, suppress duplicate starts across continuation hops. | One logical response start. | `stream_event_timing`, raw/latest captures. | `test_web_search_continuation_suppresses_duplicate_response_start` |
| Reasoning stream | `response.reasoning_text.delta`, `response.reasoning_summary_text.delta` before answer/tool output | `qz_sse.transform_sse_event`, `ResponsesStreamRuntime.run` | Transform to configured reasoning stream format, track reasoning-only progress, do not treat length alone as failure when char limit is disabled. | Summary-mode clients see `response.reasoning_summary_text.delta`; raw reasoning is not leaked in summary mode. | Stream timing plus coalesced monitor state. | 2026-05-07 audit in `docs/bugs/responses-streaming-and-qz-thoughts.md`, `test_summary_mode_transforms_reasoning_stream`, `test_default_reasoning_only_char_limit_does_not_abort_long_active_output`, `test_golden_long_active_reasoning_reaches_answer_without_default_char_abort` |
| Answer stream | `response.output_text.delta` | `ResponsesStreamRuntime._transformed_chunks` | Forward each complete SSE event frame after parse/transform. | Answer text streams normally. | `stream_event_timing` parse/forward fields. | 2026-05-07 audit showed low parse-to-forward latency; normal stream fixtures in `tests/test_qz_responses_stream.py` |
| Function call assembly | `response.output_item.added` with `function_call`, `response.function_call_arguments.delta`, `response.function_call_arguments.done` | `StreamedFunctionCallAssembler`, `StreamToolCallState` | Buffer private executable call until arguments are complete; count deltas for stall guard. | No runnable public function call with empty or partial args. | Suppressed `function_call` timing rows; upstream captures retain raw protocol. | `test_stream_tool_call_state_tracks_name_completion_and_delta_abort`, `test_public_function_call_is_buffered_until_arguments_are_complete`, `test_golden_public_function_call_buffers_until_arguments_done`, `test_golden_public_function_call_without_done_still_completes_once` |
| Completed public function call | Completed assembled call whose name is not proxy-local and has no protocol adapter conversion | `ProxyLocalToolRegistry.completed_call_decision`, `ToolRegistry.output_to_codex` | Treat as public; emit one complete public item. | Complete `function_call` item for Codex-owned tools. | `stream_completed`; private argument deltas suppressed. | `test_completed_call_decision_keeps_unknown_function_call_public`, public function-call stream fixtures |
| Completed protocol adapter call | Completed assembled call handled by a protocol adapter, currently `apply_patch` | `ToolRegistry.output_to_codex`, `qz_tool_apply_patch.py`, stream public-item emitter | Convert model-facing function call to the client-requested native/custom Codex shape using `metadata.qz_tool_policy.apply_patch_output_style`; do not execute filesystem writes in proxy. | Complete `apply_patch_call` or `custom_tool_call` that Codex can execute through its own sandbox/tool path. | Adapter captures; stream completion. | `tests/test_apply_patch_adapter.py`, `test_golden_apply_patch_stream_rewrites_to_apply_patch_call`, `test_golden_custom_apply_patch_stream_rewrites_to_custom_tool_call`, update/delete/move/rename golden fixtures, live request `qz_req_1778312516589_af70` |
| Completed proxy-local call | Completed assembled call registered as proxy-local, currently `web_search` | `ProxyLocalToolRegistry`, `WebSearchProxyToolExecutor`, `ResponsesStreamRuntime` | Emit display-only start/progress chunks from registry, execute local tool, append hidden `function_call` + `function_call_output` to next upstream hop, suppress private terminal. | `web_search_call` start/progress/completed events; no private `function_call` exposed. | `tool_call_started`, `tool_call_completed`, `stream_event_timing` suppression labels, request summary state. | `test_web_search_call_is_public_and_upstream_resumes_with_hidden_output`, `test_golden_web_search_stream_replays_with_continuation`, `test_proxy_local_continuation_can_multi_hop_from_registry_lifecycle`, `test_proxy_local_lifecycle_events_are_registry_owned`, live web-search smoke recorded in this doc |
| Private tool-call guard | Function-call assembly exceeds timeout or delta limit before safe completion | `StreamToolCallState.abort_reason`, `ResponsesStreamRuntime._emit_private_tool_call_aborted` | Abort private call before exposing incomplete runnable state. | Completed fallback message and terminal completion. | `private_tool_call_aborted`, suppressed `function_call_aborted`. | `test_stuck_function_call_aborts_instead_of_silent_dead_air` |
| Reasoning-only guard | Reasoning-only stream idles past `QZ_REASONING_ONLY_TIMEOUT_S` before answer/tool output | `ResponsesStreamRuntime._emit_reasoning_only_aborted` | Classify stall; synthesize fallback completion. Default char limit remains disabled. | Completed fallback answer plus `[DONE]`. | `reasoning_only_aborted`, suppressed `reasoning_only_aborted`. | `test_reasoning_only_stream_aborts_instead_of_never_answering`, `test_golden_reasoning_only_abort_replays_fallback`, long-active reasoning tests listed above |
| Artifact in reasoning | Reasoning-only sample looks like JSON/tool/patch payload | `_looks_like_reasoning_tool_artifact`, `_emit_reasoning_only_aborted` | Treat as protocol failure; never convert hidden reasoning into an executed tool call. | Completed fallback answer; no public function/tool call. | `reasoning_only_aborted` with `reason=artifact_tool_payload`, suppressed `reasoning_artifact_aborted`. | `test_reasoning_tool_artifact_aborts_without_length_limit`, `test_golden_reasoning_artifact_aborts_without_executing_tool`, request `qz_req_1778177240868_e0d0` |
| Malformed replay history | Prior empty `function_call` or parse-error output in request input | `ToolHistoryReplayFilter`, `normalize_responses_input_for_qwen` | Drop malformed call/output pair before forwarding to llama.cpp. | Bad historical call is not replayed to model. | Forwarded request captures show cleaned input. | `test_tool_history_replay_filter_drops_empty_call_and_matching_output`, `test_tool_history_replay_filter_drops_parse_error_and_later_output`, malformed history stream fixtures |
| Terminal handling | Upstream `response.completed`, bare `[DONE]`, malformed terminal event, final-hop close | `ResponsesStreamRuntime.run`, `_emit_completed`, `_normalize_response_usage` | Finish once; append `[DONE]` if needed; synthesize clean completion for proxy-local final-hop terminal edge cases. | One `response.completed` and one terminal done marker. Usage normalized for Codex `/status`. | `stream_completed`, terminal timing suppression labels, status usage fields. | `test_web_search_continuation_final_completed_without_done_appends_done_once`, `test_web_search_continuation_final_empty_close_emits_completed_once`, `test_web_search_continuation_final_done_only_emits_completed_once`, `test_web_search_continuation_malformed_terminal_emits_completed_once`, `test_proxy_local_final_usage_is_normalized_for_codex_status` |
| Client disconnect | Downstream write raises `BrokenPipeError`, `ConnectionResetError`, or `OSError` | `ResponsesStreamRuntime._write_chunk`, `ClientStreamDisconnected` handling | Stop streaming, close upstream in cleanup, re-raise for router; do not synthesize completion after failed write. | Client sees disconnect, not a fake completed stream. | `client_disconnected`. | `test_client_disconnect_closes_upstream_and_emits_cancel_telemetry` |

Stream telemetry names currently owned by the proxy are:
`stream_event_timing`, `stream_completed`, `client_disconnected`,
`private_tool_call_aborted`, and `reasoning_only_aborted`. Proxy-local tool runtimes also emit
`tool_call_started` and `tool_call_completed` for monitor/status consumers.

## Coverage Matrix

This matrix is a current test-strength summary for the state table. It should
change only when code, fixtures, live captures, or tests change.

| State | Coverage | Why | Next gap |
| --- | --- | --- | --- |
| Response start | Strong fixture coverage | Duplicate start suppression is pinned through proxy-local continuation tests. A test-only non-web proxy-local executor pins the generic registry path. | Add a golden non-web continuation fixture only when another real proxy-local tool exists. |
| Reasoning stream | Strong for transform/guards, live-backed for latency | Summary-mode transform, long active reasoning, and reasoning-only guard behavior are tested. The low-latency claim comes from the 2026-05-07 audit. | Add an automated timing regression only if test flake risk is acceptable. |
| Answer stream | Strong event-order coverage, live-backed latency | Normal streams and a direct writer-order test show answer deltas are written before terminal completion. The 2026-05-07 audit backs the low-latency timing claim. | Add a deterministic fake-clock timing test only if latency regresses in live use. |
| Function call assembly | Strong | Unit and stream tests pin buffering until arguments are complete and no private argument deltas reach Codex. | None before adding a new tool class. |
| Completed public function call | Strong for passthrough shape | Unknown public calls are left public and streamed as one complete item. | Add one fixture for a public call with large arguments if a real Codex tool needs it. |
| Completed protocol adapter call | Strong for `apply_patch` | Native/custom, update/delete/move/rename, invalid operations, and live Codex handoff are covered. | Add more negative parser-failure history fixtures if Qwen emits new bad patch shapes. |
| Completed proxy-local call | Strong for `web_search`; generic path unit-covered | Unit, fixture, multi-hop, telemetry, and live smoke evidence exist for `web_search`. Test-only `qz_probe` coverage proves the registry lifecycle, streaming continuation, and non-streaming continuation path are not web-search-only. | Repeat the live/golden checklist for any future real proxy-local tool. |
| Private tool-call guard | Strong | Delta-limit and timeout abort paths are both pinned at stream level, and both avoid leaking private function-call state. | Add a fake-clock variant only if wall-clock flake appears. |
| Reasoning-only guard | Strong for current policy | Timeout fallback, disabled default char cap, and long active reasoning are pinned. | Keep live Qwen captures when profile prompts change reasoning behavior. |
| Artifact in reasoning | Strong | Synthetic and golden artifact streams show no tool execution and correct telemetry reason. | Expand marker detection only from real captures, not guesses. |
| Malformed replay history | Strong unit/input coverage | Stateful drop behavior and mixed-history cleanup are pinned. | Add stream-level fixture only if replay cleanup regresses in live sessions. |
| Terminal handling | Strong | Completed-without-DONE, done-only, malformed-terminal, empty-close, and usage normalization are pinned. | Recheck after changing `/status` usage wiring. |
| Client disconnect | Strong | Write failure closes upstream and avoids synthetic completion. | None unless router-level disconnect behavior changes. |

Current weak spots are therefore narrow:

- future negative patch fixtures only when new real bad shapes appear
- repeating the proxy-local checklist for the next proxy-executed tool

## Scenario Contracts

### Normal Assistant Answer

Reasoning deltas may be transformed into summary deltas. Answer deltas must
stream promptly after parsing. Terminal completion must happen once.

### Reasoning-Only Stall

If upstream emits reasoning without answer or tool output, and then makes no
progress past `QZ_REASONING_ONLY_TIMEOUT_S`, classify the stream as stalled.
The default must not abort only because reasoning text is long:
`QZ_REASONING_ONLY_CHAR_LIMIT=-1`.

### Artifact in Reasoning

If a reasoning-only stream contains JSON, patch, or tool-shaped payload that
looks like generated artifact text, treat it as a protocol failure. The proxy
must never convert hidden reasoning into an executed tool call.

### Public Function Call

The proxy buffers function-call start and argument deltas until arguments are
complete. Codex should see one complete runnable tool item, not a premature
"tool started" event with empty or partial args.

### Proxy-Local Tool Call

Proxy-local tools such as local web search are runtime implementation details
until they produce a safe continuation result. They must not leak incomplete
private state as Codex-runnable calls.

Current public-stream behavior:

```text
upstream function_call complete
proxy emits display-only web_search_call in_progress/searching lifecycle
proxy executes local web_search
proxy emits completed public web_search_call/completed lifecycle
proxy resumes upstream with hidden function_call_output
```

This is intentionally not a streamed `function_call`. The upstream model's
private `web_search` function call and arguments remain buffered until complete,
then the proxy maps them to Responses built-in web-search progress events that
Codex can render without being asked to execute a private tool itself.

### `write_stdin`

`write_stdin` is exposed upstream only when prior request history contains a
live exec session id from an `exec_command` result. The proxy must not invite
the model to invent a session id.

### Malformed History

Malformed empty function-call items and their parse-error outputs are removed
before upstream replay. This avoids a 500 or repeated bad-call loop when the
previous stream had already failed.

Ownership: `ToolHistoryReplayFilter` in `proxy/qz_tool_lifecycle.py` owns the
stateful malformed-history drop decision. `normalize_responses_input_for_qwen`
invokes it while replaying request history upstream.

### Client Disconnect

If the downstream client closes while the proxy is writing a streamed chunk,
the stream runtime classifies the write failure as `client_disconnected`, closes
the upstream response in its normal cleanup path, and lets the router handle the
broken client connection. The runtime must not emit a synthetic
`response.completed`, fallback answer, or `[DONE]` after a failed client write.

## Golden Fixture Checklist

Current seed fixtures live under `tests/fixtures/sse/`. Keep adding fixtures
before broad stream/tool refactors.

- `basic_message.raw`: normal output and terminal `[DONE]`
- `public_function_call.raw`: public function call buffered until arguments are complete
- `public_function_call_without_done.raw`: public function call reaches a
  completed call item and the upstream closes without `[DONE]`; the proxy still
  emits one local `response.completed` and one `[DONE]`
- `apply_patch_call.raw`: function-call patch stream rewritten to `apply_patch_call`
- `apply_patch_update_call.raw` and `apply_patch_delete_call.raw`: native
  update/delete patch operations rewritten to `apply_patch_call`
- `apply_patch_multihunk_update_call.raw`: larger native multi-hunk update
  operation rewritten to `apply_patch_call`
- `apply_patch_large_multihunk_update_call.raw`: four-hunk native update with
  file-level unified-diff metadata stripped before Codex-facing output
- `custom_apply_patch_call.raw`: function-call patch stream rewritten to Codex
  custom `apply_patch` envelope
- `custom_apply_patch_update_call.raw` and `custom_apply_patch_delete_call.raw`:
  update/delete patch operations rewritten to Codex custom `apply_patch`
  envelopes
- `custom_apply_patch_multihunk_update_call.raw`: larger multi-hunk update
  operation rewritten to a Codex custom `apply_patch` envelope
- `custom_apply_patch_large_multihunk_update_call.raw`: four-hunk custom patch
  envelope with file-level unified-diff metadata stripped before output
- `apply_patch_unified_diff_update_call.raw` and
  `custom_apply_patch_unified_diff_update_call.raw`: Qwen-style streamed
  `update_file.diff` payloads containing file-level unified-diff metadata
  (`diff --git`, `index`, `---/+++`) and numbered hunk headers are normalized
  before either native `apply_patch_call` output or Codex custom patch-envelope
  output
- `apply_patch_move_call.raw`: streamed `move_file` operation rewritten to a
  native Codex `apply_patch_call` with explicit `path` and `destination`
- `custom_apply_patch_move_call.raw`: streamed `rename_file` alias rewritten to
  a Codex custom `apply_patch` envelope using `*** Update File:` plus
  `*** Move to:` and a non-empty context hunk
- `apply_patch_rename_alias_move_call.raw` and
  `custom_apply_patch_rename_alias_move_call.raw`: streamed `rename_file` with
  `new_path` destination alias is normalized to canonical `move_file` behavior;
  git rename metadata is stripped before native/custom Codex-facing output
- `tests/test_apply_patch_adapter.py`: pins normalization of file-level
  unified-diff metadata and line-number hunk headers from model
  `update_file.diff` payloads before Codex-facing native/custom output
- `tests/fixtures/responses_input/native_codex_first_request_shape.json`: pins
  the redacted native Codex first-request envelope used by
  `normalize_responses_input_for_qwen` and `normalize_tools_for_llamacpp`
- `invalid_apply_patch_call.raw`: bare-operation create_file (no diff) now
  emits a partial `*** Add File: <path>` envelope so Codex's verifier
  surfaces a specific error to the model on the next turn (was: assistant
  message dead-end before 2026-05-10)
- `invalid_apply_patch_move_call.raw`: move/rename-style operations without
  an explicit destination still fall back to a descriptive assistant message,
  because no usable envelope can be salvaged from no-destination args
- `qwen_create_file_sibling_patch.raw`,
  `qwen_update_file_sibling_patch_with_unified_headers.raw`: Qwen-observed
  shape A — operation lacks diff but a sibling top-level `patch` carries
  the file content; proxy promotes it into `operation.diff` and coercion
  proceeds normally. The unified-diff variant exercises the existing file-
  header stripping path
- `qwen_create_file_bare_operation.raw`,
  `qwen_update_file_bare_operation.raw`: Qwen-observed shape B — operation
  with type+path but no content anywhere; proxy emits a partial Codex
  envelope so the verifier produces a specific actionable error
- `qwen_legacy_patch_missing_path.raw`: full Codex patch envelope as
  top-level `patch` string with no separate `path`; proxy extracts type+path
  from the envelope's `*** Update File:` header line
- `qwen_rename_no_hunk.raw`: rename without a content hunk emits a partial
  `*** Update File + *** Move to` envelope rather than the previous silent
  no-op
- `completed_without_done.raw`: upstream `response.completed` without `[DONE]`
  is closed with exactly one terminal `[DONE]`
- `done_only.raw`: final continuation hop with only `[DONE]` is converted into
  one clean completed response plus one `[DONE]`
- `malformed_terminal.raw`: malformed final continuation terminal bytes are
  suppressed and replaced with one clean completed response plus one `[DONE]`
- `reasoning_only.raw`: reasoning-only fallback path
- `reasoning_artifact.raw`: artifact-in-reasoning protocol failure
- `long_active_reasoning.raw`: long reasoning followed by answer is not killed
  by the default disabled char limit
- `web_search_call.raw`, `web_search_call_second.raw`, and
  `web_search_final.raw`: proxy-local web search continuation, including
  multi-hop raw fixture replay
  including duplicate `response.created` suppression across continuation hops
- `web_search_call.raw` plus `completed_without_done.raw`: continuation final hop
  appends exactly one terminal `[DONE]` when upstream completes without one
- `responses_input/malformed_empty_tool_history.json`: empty tool-call plus
  parse-error output is filtered while valid neighboring history survives
- `tests/test_qz_tool_lifecycle.py`: pins private streamed function-call state,
  guard accounting, malformed historical tool-call filtering, and legacy
  lifecycle helper behavior
- `tests/test_qz_proxy_tools.py`: pins completed-call routing decisions,
  apply_patch public item conversion, proxy-local execution context, and
  upstream continuation shaping through the active proxy tool registry
- `tests/test_qz_tool_request.py`: pins tool declaration normalization,
  `write_stdin` gating, tool-choice adaptation, request-scoped
  `qz_tool_policy`, and capture-note output
- `tests/smoke_apply_patch_codex_exec.py`: hermetic fake-upstream Codex exec
  smoke pins the end-to-end apply_patch handoff and public Codex JSONL
  `file_change` started/completed lifecycle
- `metadata.qz_tool_policy`: request-scoped proxy-owned tool-shape policy.
  For apply_patch it records whether the client declared patch support, the
  original client tool type (`apply_patch`, `custom`, or `absent`), and the
  Codex-facing output style (`native` or `custom`). Stream conversion reads
  this metadata instead of re-inferring shape after tool normalization.

Still needed:

- additional move/rename negative fixtures for traversal, absolute paths, and
  invalid source/destination combinations if a local patch harness is added
- more non-web-search continuation terminal-edge cases if another proxy-local
  continuation tool is added

## Known Gaps

- Golden replay fixture coverage has expanded across normal output, tool-call
  buffering, web-search continuation, reasoning aborts, and apply_patch
  adaptation for create/update/delete patches, but is not broad enough yet.
- Live Qwen update-patch behavior can still emit one malformed attempt before
  converging, but follow-up validation through a temporary proxy confirmed the
  adapter now handles leaked unified-diff file headers and line-number hunk
  headers well enough for Codex to apply the next update patch. Golden replay
  fixtures now cover that metadata normalization for native and custom
  Codex-facing output styles.
- Tool lifecycle now has internal boundaries for streamed call state,
  malformed historical tool-call filtering, completed-call routing decisions,
  public protocol-adapter conversion, proxy-local continuation shaping, and
  registry-owned proxy-local lifecycle SSE chunk construction. Tool request
  normalization and non-tool request normalization each have their own module
  boundary. A test-only proxy-local executor now pins the same registry path
  for both streamed and non-streamed `/v1/responses`.
- Capture mode interpretation and latest/request-scoped dual writes now have a
  runtime IO boundary.
- No redaction layer for captures.
- Request-scoped captures are not grouped under a higher-level run id.
- Proxy-side shell/code/computer/MCP execution is not implemented.
- External gateway behavior is useful evidence, but not a substitute for local
  captures against Qwen plus llama.cpp/TurboQuant.

## Related Docs

- [Master stabilisation plan](master-stabilisation-plan.md)
- [Proxy capability roadmap](proxy-capability-roadmap.md)
- [Responses SSE streaming and qz-thoughts bug](bugs/responses-streaming-and-qz-thoughts.md)
- [Runtime observability notes](runtime-observability-notes.md)
- [Patch tool roadmap](patch-tool-roadmap.md)
