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

## Source of Truth

Implementation:

```text
proxy/qz_responses_stream.py   streamed Responses runtime and continuation loop
proxy/qz_streaming.py          SSE parser and streamed function-call assembler
proxy/qz_responses.py          request normalization, tool filtering, history cleanup
proxy/qz_tool_lifecycle.py     private streamed tool-call state, completed-call
                               routing, public item conversion, and upstream
                               continuation item shaping
proxy/qz_tool_apply_patch.py   apply_patch envelope adaptation
proxy/qz_runtime_io.py         request-scoped capture helpers
proxy/qz_telemetry.py          status and telemetry events
proxy/qz_request_router.py     request id and routing envelope
```

Regression tests:

```text
tests/test_qz_responses_stream.py
tests/test_qz_streaming.py
tests/test_qz_tool_lifecycle.py
tests/test_apply_patch_adapter.py
tests/test_qz_runtime_io.py
tests/test_qz_thoughts_cli.py
```

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
- Audit whether Codex CLI `/status` consumes token/context data from final
  Responses `usage`, generated model catalog metadata, request metadata, or an
  undocumented local client state path.

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
roleplay profile such as `amber`:

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
runs for multiple seconds. Remaining live-state gaps should be investigated in
the harder cases:

- proxy-local/private tools such as `web_search`
- streamed `apply_patch` shape conversion and Codex execution handoff
- TUI rendering and `/status` consumption of token/context data
- long-running tool calls where progress is available only inside proxy
  telemetry

## State Table

| Upstream signal | Proxy state/action | Codex-visible stream | Telemetry/capture | Evidence |
| --- | --- | --- | --- | --- |
| `response.created`, `response.in_progress` | Parse as lifecycle events. Suppress duplicate start events across continuation hops. | One coherent response lifecycle. | `sse_event`, `stream_event_timing`. | `tests/test_qz_responses_stream.py` |
| `response.reasoning_text.delta` | Transform according to configured reasoning stream mode. Track progress. | Usually `response.reasoning_summary_text.delta` in summary mode. | Coalesced in `qz-thoughts`; raw enough in captures. | 2026-05-07 audit in bug note. |
| `response.output_text.delta` | Forward after parse/transform without waiting for tool-loop completion. | Answer text streams promptly. | Timing telemetry records parse-to-forward delay. | `tests/test_qz_responses_stream.py` |
| `response.output_item.added` with `function_call` | Start assembling a tool call. Do not emit runnable call yet. | Suppressed until arguments complete. | Internal stream state and captures. | `StreamedFunctionCallAssembler` tests. |
| `response.function_call_arguments.delta` | Append argument delta to assembler. | Suppressed until complete. | Captured as upstream protocol. | `tests/test_qz_streaming.py` |
| `response.function_call_arguments.done` | Validate assembled function name and argument JSON. | Emit one complete public tool item if the call belongs to Codex. | Tool-call telemetry and request captures. | `tests/test_qz_responses_stream.py` |
| Completed proxy-local `web_search` call | Emit safe built-in web-search progress, execute local search, append result into continuation context. | `response.output_item.added` with `web_search_call status=in_progress`, `response.web_search_call.in_progress`, `response.web_search_call.searching`, then completed `web_search_call` item and `response.web_search_call.completed`. No private `function_call` is exposed. | Search/tool telemetry emits `tool_call_started` and `tool_call_completed`; stream timing marks private function-call suppression/start/completion. | `tests/test_qz_responses_stream.py`, `tests/test_qz_streaming.py` |
| Completed `apply_patch` call | Adapt native/custom envelope according to request `metadata.qz_tool_policy.apply_patch_output_style`. Delegate execution to Codex path unless proxy-side execution is explicitly implemented. | One complete patch tool item/result path matching the client-declared shape. | Adapter captures and tests. | `tests/test_apply_patch_adapter.py`, `tests/test_qz_responses_stream.py` |
| Private tool call exceeds guard | Abort private call, do not publish incomplete runnable state. | Completed fallback/error path if needed. | `private_tool_call_aborted`. | `tests/test_qz_responses_stream.py` |
| Reasoning-only idle stream | If reasoning appears with no answer/tool and no progress past timeout, classify as a stall. | Completed fallback answer plus terminal markers. | `reasoning_only_aborted`. | `tests/test_qz_responses_stream.py` |
| Tool/artifact payload appears only in reasoning | Treat as protocol failure. Do not execute it. | Completed fallback answer. | `reasoning_only_aborted` with `artifact_tool_payload`. | Request `qz_req_1778177240868_e0d0`. |
| Malformed empty historical function call | Filter bad pair before upstream replay. | No malformed replay visible to model. | Dropped-history diagnostics when capture is enabled. | `tests/test_qz_responses_stream.py` |
| Upstream `response.completed` or close | Finish transformed stream once and append `[DONE]` if needed. | `response.completed` and one `data: [DONE]`. | Status/capture terminal record. | 2026-05-07 audit in bug note. |

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

## Golden Fixture Checklist

Current seed fixtures live under `tests/fixtures/sse/`. Keep adding fixtures
before broad stream/tool refactors.

- `basic_message.raw`: normal output and terminal `[DONE]`
- `public_function_call.raw`: public function call buffered until arguments are complete
- `apply_patch_call.raw`: function-call patch stream rewritten to `apply_patch_call`
- `apply_patch_update_call.raw` and `apply_patch_delete_call.raw`: native
  update/delete patch operations rewritten to `apply_patch_call`
- `apply_patch_multihunk_update_call.raw`: larger native multi-hunk update
  operation rewritten to `apply_patch_call`
- `custom_apply_patch_call.raw`: function-call patch stream rewritten to Codex
  custom `apply_patch` envelope
- `custom_apply_patch_update_call.raw` and `custom_apply_patch_delete_call.raw`:
  update/delete patch operations rewritten to Codex custom `apply_patch`
  envelopes
- `custom_apply_patch_multihunk_update_call.raw`: larger multi-hunk update
  operation rewritten to a Codex custom `apply_patch` envelope
- `invalid_apply_patch_call.raw`: malformed model-side patch operation becomes
  an assistant error message, not a runnable private tool call
- `invalid_apply_patch_move_call.raw`: move/rename-style operations are
  rejected because the current adapter supports create/update/delete only
- `completed_without_done.raw`: upstream `response.completed` without `[DONE]`
  is closed with exactly one terminal `[DONE]`
- `reasoning_only.raw`: reasoning-only fallback path
- `reasoning_artifact.raw`: artifact-in-reasoning protocol failure
- `long_active_reasoning.raw`: long reasoning followed by answer is not killed
  by the default disabled char limit
- `web_search_call.raw` and `web_search_final.raw`: proxy-local web search continuation
  including duplicate `response.created` suppression across continuation hops
- `web_search_call.raw` plus `completed_without_done.raw`: continuation final hop
  appends exactly one terminal `[DONE]` when upstream completes without one
- `responses_input/malformed_empty_tool_history.json`: empty tool-call plus
  parse-error output is filtered while valid neighboring history survives
- `tests/test_qz_tool_lifecycle.py`: pins private streamed function-call state,
  guard accounting, completed-call routing decisions, apply_patch public item
  conversion, and proxy-local upstream continuation shaping
- `metadata.qz_tool_policy`: request-scoped proxy-owned tool-shape policy.
  For apply_patch it records whether the client declared patch support, the
  original client tool type (`apply_patch`, `custom`, or `absent`), and the
  Codex-facing output style (`native` or `custom`). Stream conversion reads
  this metadata instead of re-inferring shape after tool normalization.

Still needed:

- explicit move/rename support; current behavior is a pinned rejection until
  the model-facing schema and Codex-facing adapter are expanded beyond
  create/update/delete
- more non-web-search continuation terminal-edge cases if another proxy-local
  continuation tool is added

## Known Gaps

- Golden replay fixture coverage has expanded across normal output, tool-call
  buffering, web-search continuation, reasoning aborts, and apply_patch
  adaptation for create/update/delete patches, but is not broad enough yet.
- Tool lifecycle now has an initial internal boundary for streamed call state,
  public item conversion, completed-call routing decisions, and proxy-local
  continuation shaping, but request normalization, adapter ownership, and
  telemetry still need tighter ownership.
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
