# Responses Stream and Tool State Contract

Status: living contract for QuantZhai's current `/v1/responses` streamed path.
Last reconciled: 2026-05-08.

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

## State Table

| Upstream signal | Proxy state/action | Codex-visible stream | Telemetry/capture | Evidence |
| --- | --- | --- | --- | --- |
| `response.created`, `response.in_progress` | Parse as lifecycle events. Suppress duplicate start events across continuation hops. | One coherent response lifecycle. | `sse_event`, `stream_event_timing`. | `tests/test_qz_responses_stream.py` |
| `response.reasoning_text.delta` | Transform according to configured reasoning stream mode. Track progress. | Usually `response.reasoning_summary_text.delta` in summary mode. | Coalesced in `qz-thoughts`; raw enough in captures. | 2026-05-07 audit in bug note. |
| `response.output_text.delta` | Forward after parse/transform without waiting for tool-loop completion. | Answer text streams promptly. | Timing telemetry records parse-to-forward delay. | `tests/test_qz_responses_stream.py` |
| `response.output_item.added` with `function_call` | Start assembling a tool call. Do not emit runnable call yet. | Suppressed until arguments complete. | Internal stream state and captures. | `StreamedFunctionCallAssembler` tests. |
| `response.function_call_arguments.delta` | Append argument delta to assembler. | Suppressed until complete. | Captured as upstream protocol. | `tests/test_qz_streaming.py` |
| `response.function_call_arguments.done` | Validate assembled function name and argument JSON. | Emit one complete public tool item if the call belongs to Codex. | Tool-call telemetry and request captures. | `tests/test_qz_responses_stream.py` |
| Completed proxy-local `web_search` call | Execute local search and append result into continuation context. | Do not expose half-built private tool events. | Search/tool telemetry and captures. | Existing smoke path plus roadmap. |
| Completed `apply_patch` call | Adapt native/custom envelope according to incoming declaration. Delegate execution to Codex path unless proxy-side execution is explicitly implemented. | One complete patch tool item/result path. | Adapter captures and tests. | `tests/test_apply_patch_adapter.py` |
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
- `custom_apply_patch_call.raw`: function-call patch stream rewritten to Codex
  custom `apply_patch` envelope
- `custom_apply_patch_update_call.raw` and `custom_apply_patch_delete_call.raw`:
  update/delete patch operations rewritten to Codex custom `apply_patch`
  envelopes
- `invalid_apply_patch_call.raw`: malformed model-side patch operation becomes
  an assistant error message, not a runnable private tool call
- `completed_without_done.raw`: upstream `response.completed` without `[DONE]`
  is closed with exactly one terminal `[DONE]`
- `reasoning_only.raw`: reasoning-only fallback path
- `reasoning_artifact.raw`: artifact-in-reasoning protocol failure
- `long_active_reasoning.raw`: long reasoning followed by answer is not killed
  by the default disabled char limit
- `web_search_call.raw` and `web_search_final.raw`: proxy-local web search continuation
- `responses_input/malformed_empty_tool_history.json`: empty tool-call plus
  parse-error output is filtered while valid neighboring history survives
- `tests/test_qz_tool_lifecycle.py`: pins private streamed function-call state,
  guard accounting, completed-call routing decisions, apply_patch public item
  conversion, and proxy-local upstream continuation shaping

Still needed:

- continuation hop with no duplicate response start beyond web-search coverage
- more continuation terminal-edge cases
- larger multi-hunk and move patch variants

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
