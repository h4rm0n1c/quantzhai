# Codex/QuantZhai Bidirectional Signal Surface Map

Date: 2026-05-14
Status: source-grounded first edition. Update as signals are implemented or
evidence changes.

---

## Purpose

QuantZhai is not only a passive `/v1/responses` relay. It sits in the middle of
every Codex turn and can observe, classify, store, render, and sometimes feed
back signals that improve local agent behaviour. This document maps every major
signal flowing between Codex CLI, the QuantZhai proxy, the local model backend,
monitors, and future SQLite/LimbiCore state.

The goal is a single place where a new agent or engineer can answer:
- What does Codex actually send?
- What does QuantZhai already parse, store, or surface?
- What can the model already see or receive as feedback?
- What is planned, speculative, or not safe to surface?

---

## Non-goals

This document does not implement anything.

```text
- No runtime signal injection added here.
- No durable memory constructed here.
- No active memory tools added here.
- No memory_domain policy changes.
- No second compaction pipeline.
- Nothing speculative is marked as implemented.
```

---

## High-level direction map

```text
Codex CLI / compatible client
  --[HTTP/SSE or WebSocket]--> QuantZhai proxy (127.0.0.1:18180)
  --[HTTP/SSE]-------------->  llama.cpp / TurboQuant (127.0.0.1:18084)

Side channels:
  /qz/status                  runtime status snapshot
  /qz/telemetry/recent        sliding event window for monitors
  /qz/telemetry/stream        SSE stream for live monitors
  /qz/config/effective        active config, paths, warnings
  /qz/models/refresh          rescan + catalog regeneration
  scripts/qz-thoughts         live SSE thought/activity monitor
  scripts/qz-top              GPU/VRAM/throughput monitor
  var/captures/               request-scoped debug captures
  BrainCaseDB (SQLite)         explicit memory/state records only (not operational facts)
  LimbiCore renderers          model-facing state packets (future)
```

---

## Codex → QuantZhai signals

Grounded in:
- `codex-rs/core/src/client.rs` — `ModelClientState`, `build_responses_request()`,
  `build_responses_identity_headers()`, `build_subagent_headers()`
- `codex-rs/codex-api/src/requests/headers.rs` — header name constants and
  `build_session_headers()`
- `codex-rs/codex-api/src/common.rs` — `ResponsesApiRequest`, `CompactionInput`,
  `MemorySummarizeInput`
- `codex-rs/protocol/src/protocol.rs` — `ThreadMemoryMode`, `SubAgentSource`,
  `SandboxPolicy`, `AskForApproval`
- `docs/codex-0130-live-signal-capture.md` — observed Codex 0.130 header/body
- `docs/codex-context-memory-contract.md` — parser contract and scope policy
- `proxy/qz_codex_metadata.py` — QuantZhai parser boundary

### Identity and session headers

| Signal | Header / Field | Codex source | QZ parser | Scope | Stored? | Model-visible? | Status |
|---|---|---|---|---|---|---|---|
| `session_id` | `session_id` + `session-id` headers | `build_session_headers()` | `extract_codex_identity()` | session | as SourceRef provenance only | no | **implemented** |
| `thread_id` | `thread_id` + `thread-id` headers | `build_session_headers()` | `extract_codex_identity()` | thread | planned | no | **implemented** |
| `x-codex-window-id` | `x-codex-window-id` header, format `{thread_id}:{window_generation}` | `build_responses_identity_headers()` | `parse_codex_window_id()` | turn | planned | no | **implemented** |
| `x-codex-installation-id` | `x-codex-installation-id` header + `client_metadata` body key | `build_responses_identity_headers()` | `extract_codex_identity()` | installation | planned | no | **implemented** |
| `x-codex-parent-thread-id` | `x-codex-parent-thread-id` header | `build_responses_identity_headers()` | `extract_codex_identity()` | thread lineage | planned | no | **implemented** |
| `x-openai-subagent` | `x-openai-subagent` header | `build_subagent_headers()` | `extract_codex_identity()` | session | planned | no | **implemented** |
| `x-openai-memgen-request` | `x-openai-memgen-request` header | `build_subagent_headers()` (set when `InternalSessionSource::MemoryConsolidation`) | `extract_codex_identity()` | request | planned | no | **implemented** |
| `x-client-request-id` | `x-client-request-id` header | Codex request tracing | `extract_codex_identity()` | request | planned | no | **implemented** |

### Turn and window signals

| Signal | Header / Field | Codex source | QZ parser | Notes | Status |
|---|---|---|---|---|---|
| `x-codex-turn-metadata` | `x-codex-turn-metadata` JSON header | `build_ws_client_metadata()` | `parse_codex_turn_metadata_header()` | contains `turn_id`, `turn_started_at_unix_ms`, `workspaces`, `session_id`, `thread_id` | **implemented** |
| `turn_id` | inside `x-codex-turn-metadata` | turn metadata | `extract_codex_identity()` | stable across multi-hop turns | **implemented** |
| `turn_started_at_unix_ms` | inside `x-codex-turn-metadata` | turn metadata | `extract_codex_identity()` | — | **implemented** |
| workspace candidates | `x-codex-turn-metadata.workspaces` | turn metadata | `extract_workspace_candidates()` | repo root, remote URLs, git commit, has_changes | **implemented** |
| `x-codex-turn-state` | `x-codex-turn-state` header | set from server response; replayed per request within a turn | `extract_codex_identity()` | sticky routing token; raw capture only; not decoded | **partial** (captured, not decoded) |
| window generation | from `x-codex-window-id` | `set_window_generation()` / `advance_window_generation()` | `parse_codex_window_id()` | increments per reconnect within a session | **implemented** |

### Body-level signals

| Signal | Body field | Codex source | QZ parser | Notes | Status |
|---|---|---|---|---|---|
| `prompt_cache_key` | `body.prompt_cache_key` | set to `thread_id.to_string()` in `build_responses_request()` | `extract_codex_body_metadata()` | affinity key; equals thread_id in HTTP/SSE | **implemented** |
| `previous_response_id` | `body.previous_response_id` | WebSocket prewarm path | `extract_codex_body_metadata()` | nullable; HTTP/SSE 0.130 capture did not emit it | **partial** (captured, not resolved) |
| `reasoning.effort` | `body.reasoning.effort` | `build_reasoning()` in client.rs | `extract_codex_body_metadata()` | low/medium/high/max | **implemented** |
| `reasoning.summary` | `body.reasoning.summary` | `build_reasoning()` | `extract_codex_body_metadata()` | none/concise/detailed | **implemented** |
| `text.verbosity` | `body.text.verbosity` | `create_text_param_for_request()` | `extract_codex_body_metadata()` | low/medium/high | **implemented** |
| `client_metadata` | `body.client_metadata` | `build_responses_request()` injects installation_id | `extract_codex_body_metadata()` | dictionary; currently contains `x-codex-installation-id` | **implemented** |
| `store` | `body.store` | set `true` only for Azure responses endpoint | parsed/forwarded | — | **implemented** (forwarded) |
| `stream` | `body.stream` | always `true` for SSE Codex path | read for routing | determines streaming vs non-streaming proxy path | **implemented** |
| `service_tier` | `body.service_tier` | filtered by model capability | parsed | — | **implemented** |
| `parallel_tool_calls` | `body.parallel_tool_calls` | from `prompt.parallel_tool_calls` | forwarded | — | **implemented** |
| tools list | `body.tools` | `create_tools_json_for_responses_api()` | scanned for capability set | tool names → capability_set; does not grant memory access | **implemented** |
| `input` items | `body.input` | Codex history replay | scanned before normalization | function_call_output scanned for native tool classifier | **implemented** |

### Sandbox and approval signals (Codex protocol)

These appear in the Codex Protocol layer (`codex-rs/protocol/src/protocol.rs`) as
turn-level config sent from the Codex TUI/client to the agent runtime. They are
not HTTP headers; they travel via the internal Codex protocol, not through
QuantZhai.

| Signal | Codex protocol type | Notes | QZ visibility |
|---|---|---|---|
| `SandboxPolicy` | `ReadOnly`, `WorkspaceWrite`, `DangerFullAccess`, `ExternalSandbox` | Governs which filesystem paths exec_command may write | **not seen by proxy** (internal protocol); proxy infers from tool output content |
| `AskForApproval` | `OnRequest`, `Never`, etc. | Whether escalation prompts are shown | **not seen by proxy** |
| `ThreadMemoryMode` | `Enabled` / `Disabled` | Codex-side turn memory eligibility | **not seen by proxy** (not observed in 0.130 HTTP/SSE capture) |
| `sandbox_permissions` on `exec_command` arguments | `use_default` / `require_escalated` | Model sets this to request elevated sandbox; visible to proxy in outgoing function_call SSE | **implemented** (Slice 1: `tool_escalation_requested`) |

The `sandbox_permissions: "require_escalated"` signal IS visible to QuantZhai
because it appears in the model's outgoing `function_call` arguments as the model
streams its response. The proxy detects it in `ResponsesStreamRuntime._check_sandbox_escalation()`.

---

## QuantZhai → Codex-visible signals

These are the SSE events forwarded to Codex. Only events explicitly forwarded or
generated by QuantZhai are Codex-visible. Internal telemetry events are
operator-only.

### Normal streaming lifecycle (forwarded from model)

| Event type | Forwarded? | Notes |
|---|---|---|
| `response.created` | yes | forwarded verbatim |
| `response.in_progress` | yes (once) | duplicate starts suppressed |
| `response.output_item.added` | yes, but buffered | function_call items buffered until arguments complete |
| `response.function_call_arguments.delta` | suppressed | not forwarded; arguments assembled privately |
| `response.function_call_arguments.done` | suppressed | — |
| `response.output_item.done` | yes (with complete arguments) | forwarded after private assembly |
| `response.output_text.delta` | yes | forwarded verbatim |
| `response.output_text.done` | yes | — |
| `response.reasoning_summary_part.added` | yes (summary mode) | depends on `reasoning_stream_format` |
| `response.reasoning_summary_text.delta` | yes (summary mode) | — |
| `response.reasoning_content_part.added` | yes (raw mode) | — |
| `response.reasoning_content_text.delta` | yes (raw mode) | — |
| `response.completed` | yes | includes usage |
| `[DONE]` | yes | forwarded or synthesized |

### Proxy-generated Codex-visible events

| Event type | Generated by | Notes |
|---|---|---|
| `response.web_search_call.in_progress` | proxy | proxy-local web search lifecycle |
| `response.web_search_call.searching` | proxy | — |
| `response.web_search_call.completed` | proxy | — |
| fallback final message | proxy | generated when reasoning-only or repair paths exhaust |
| rate-limit headers | proxy | `x-ratelimit-*` forwarded to Codex |

### Codex JSONL/TUI rendering

Codex renders `response.output_item.done` items as JSONL or TUI display elements.
QuantZhai shapes:
- function_call output items for `apply_patch` (protocol adapter)
- web_search call/result items (proxy-local)
- final message items

QuantZhai does **not** inject synthetic items into the model's turn without a
documented protocol path. The `inject_runtime_state()` path injects a `QZSTATE`
block into `instructions`, not into `input` items. This is controlled by
`QZSTATE` env flag and is off by default.

---

## Backend/model → QuantZhai signals

The local llama.cpp/TurboQuant backend speaks a Responses-compatible SSE stream.
QuantZhai observes these from the upstream HTTP/SSE connection.

| Signal | SSE event type | QuantZhai behaviour |
|---|---|---|
| Reasoning text | `response.reasoning_content_text.delta` | accumulated for summary transformation; may be suppressed depending on `reasoning_stream_format` |
| Answer text | `response.output_text.delta` | forwarded to Codex |
| Function call start | `response.output_item.added` (function_call) | buffered privately until arguments complete |
| Argument stream | `response.function_call_arguments.delta` | accumulated in `StreamedFunctionCallAssembler` |
| Arguments complete | `response.function_call_arguments.done` | triggers `completed_call_decision()` |
| Item complete | `response.output_item.done` | releases buffered function_call to Codex; triggers escalation check |
| Usage | `response.completed` | token counts forwarded and stored in telemetry |
| Terminal event | `response.completed`, `response.failed`, `response.cancelled` | classified in `is_terminal_stream_event()` |
| Stream close | end of SSE stream | triggers fallback paths if no answer produced |
| Reasoning-only | reasoning without answer text | triggers `_emit_reasoning_only_completed_without_answer()` or stall watchdog |
| Malformed function call | invalid JSON arguments | `coerce()` in tool registry; synthetic error result injected |
| Backend error | HTTP ≥ 400 or connection error | `stream_failed` telemetry; retry/fallback in outer loop |

---

## QuantZhai → backend/model request shaping

QuantZhai transforms every `/v1/responses` body before forwarding upstream. These
are NOT memory — they are request normalization.

| Shaping | Source | Notes | Status |
|---|---|---|---|
| Profile/model selection | `selected_model` from `ModelCatalog` | backend_id substituted for Codex-visible profile slug | **implemented** |
| Reasoning policy | `apply_reasoning_policy()` in `qz_model_router.py` | injects effort level prompt into `instructions`; sets sampling params | **implemented** |
| Text verbosity | parsed from body, applied to text controls | forwarded if model supports it | **implemented** |
| Context length selection | `selected_context_length()` | per-profile `runtime_context_length` override | **implemented** |
| System prompt/harness injection | `assemble_instruction_stack()` | profile system prompt + caveman/turn harness injected | **implemented** |
| Tool declaration normalization | `normalize_tools_for_llamacpp()` | exec_command hint injection, dropped-tool detection | **implemented** |
| `apply_patch` protocol adaptation | `qz_tool_apply_patch.py` | Codex `apply_patch_call` → llama.cpp `function_call` | **implemented** |
| Local compaction replay markers | `_expand_local_compaction_items()` + `_microcompact_old_tool_results()` | old tool outputs microcompacted to signal placeholders | **implemented** |
| History normalization | `normalize_responses_input_for_qwen()` | reasoning items dropped, old harness blocks stripped, message roles canonicalized | **implemented** |
| Turn harness injection | `_inject_turn_harness()` | per-profile harness block injected into newest user turn | **implemented** |
| Hop budget signal | `_hop_budget_signal_message()` | injected into model input when remaining hops ≤ threshold | **implemented** |
| Context pressure signal | `_context_pressure_signal()` emitted as telemetry | emitted when estimated input tokens > threshold | **implemented** (telemetry only; not injected into model input) |

---

## QuantZhai internal derived signals

Signals QuantZhai computes from observed traffic, exposed to monitors or destined
for future storage.

| Signal | Source | Scope | Current status | Model-visible? |
|---|---|---|---|---|
| `profile_id` | model selection from catalog | request | **implemented** | no |
| `backend_loaded_model` | llama.cpp `/models` inventory | request | **implemented** | no |
| `selected_context_length` | catalog `runtime_context_length` or env default | request | **implemented** | no |
| `backend_context_length` | llama.cpp backend state | request | **implemented** | no |
| `restart_required` | context mismatch detection | request | **implemented** | no |
| `memory_domain` | explicit profile config; isolated fallback | request | **implemented** | no (internal only) |
| `workspace_id` | resolved from turn metadata remote URL or repo root hash | turn | **implemented** | no |
| `qz_session_id` | generated from `client_session_id` | session | **implemented** | no |
| `qz_turn_id` | internal; grouped by `codex_turn_id` | turn | as SourceRef provenance only | no |
| `qz_request_id` | generated per HTTP request | request | **implemented** | no (telemetry/captures) |
| `continuation_hop_count` | counted in streaming loop | turn | **implemented** | hop budget signal |
| `continuation_hop_budget` | `WEB_SEARCH_MAX_HOPS` limit | turn | **implemented** | injected near limit |
| tool-call count | counted per turn | turn | **partial** (telemetry events) | no |
| repeated file reads | from `body["input"]` function_call history | request | **planned** (repeated-read v1) | advisory signal |
| file read/write path history | from `body["input"]` | request | **planned** (v1) | advisory signal |
| `tool_sandbox_denied` classification | `classify_native_tool_outputs()` on raw incoming body | request | **implemented** | yes — advisory `function_call_output` injected for `sandbox_denied_readonly_fs` / `high` confidence |
| `tool_escalation_requested` | `_check_sandbox_escalation()` on outgoing function_call SSE | request | **implemented** | no (telemetry only) |
| `tool_connection_failed` | `classify_native_tool_outputs()` | request | **implemented** | no (telemetry only) |
| proxy-local tool provenance | proxy-local executor tags | turn | **implemented** | visible in public items |
| search route / result quality | `qz_tool_web.py` web_search route | request | **partial** (telemetry only) | no |
| VRAM/backend health | `/qz/status` backend snapshot | session | **partial** (proxy approximation; exact allocation unknown) | no |
| capture availability | `capture_policy()` | request | **implemented** | no |
| stream terminal classification | `is_terminal_stream_event()` | request | **implemented** | no |
| compaction pressure | token estimate vs threshold | request | **implemented** (local compaction trigger) | no |
| identity conflicts | header vs body session/thread mismatch | request | **implemented** (detection, no storage yet) | no |

---

## QuantZhai → model feedback signals

These are signals the proxy could inject back to the model. They are classified
by channel and implementation status.

| Signal | Classification | Channel | Status |
|---|---|---|---|
| "You already read this file earlier in this turn." | self-management | function_call_output advisory result | **planned** (repeated-read v1) |
| "The previous native tool call failed because the sandbox reported a read-only filesystem. Do not retry the same write operation unchanged." | quality | `function_call_output` advisory result via `render_advisory_output()` | **implemented** — bounded to `sandbox_denied_readonly_fs` / `high` confidence only; telemetry: `tool_sandbox_advisory_injected` |
| "This tool failed because the sandbox blocked it." | quality | harness guidance (`codex-core-qwenified.md`) | **implemented** (harness text only; not automatic per-failure injection) |
| "This tool call was malformed: missing argument X." | quality | `function_call_output` error result via `synthesize_tool_error_result()` | **implemented** |
| "You have made N tool calls this turn." | self-management | not yet implemented | speculative |
| "Only one continuation hop remains." | self-management | `_hop_budget_signal_message()` injected into input | **implemented** |
| "Context pressure is high; preserve final answer." | self-management | `context_pressure_signal` telemetry only; no injection yet | **partial** (telemetry; no model injection) |
| "Search result appears low-signal / mirror." | quality | not yet implemented | speculative |
| "Backend failed transiently; retrying may be valid." | quality | not yet implemented | speculative |
| "Your prior completion had reasoning but no visible answer." | quality | `empty_answer_repair_started` telemetry; repair hop triggered | **implemented** (repair hop; no explicit model message) |
| Reasoning effort prompt | self-management | injected into `instructions` | **implemented** |
| Turn harness reminder | self-management | injected into newest user turn text | **implemented** |

**Hard rule:** Do not inject QuantZhai-owned `qz_*` internals into forwarded
`/v1/responses` bodies unless an explicit design document approves it. The
`test_qz_request_mutation_regression.py` regression test enforces this.

---

## QuantZhai → operator/monitor signals

All signals below are operator-visible through telemetry, qz-thoughts/qz-top, or
captures. None are model-visible.

### Implemented telemetry events

Source: `REQUEST_LIFECYCLE_EVENT_TYPES` in `proxy/qz_telemetry.py` and emitters
in `proxy/qz_responses_stream.py`, `proxy/qz_request_router.py`,
`proxy/qz_native_tool_output.py`.

| Event | Payload fields | qz-thoughts label |
|---|---|---|
| `request_started` | method, path, model | request row |
| `request_completed` | status, elapsed_ms, usage | request row |
| `request_failed` | error, phase | error row |
| `request_admitted` | — | — |
| `request_queued` | — | — |
| `stream_completed` | output_items, duration_ms, fallback | stream row |
| `throughput_sample` | prompt_rate, gen_rate | — |
| `prompt_contract` | profile, memory_domain, reasoning_level, prompt_files | contract row |
| `tool_call_started` | tool, public_item_type, execution | tool row |
| `tool_call_completed` | tool, sources, upstream_items | tool row |
| `tool_call_failed` | tool, error | error row |
| `tool_sandbox_denied` | tool, call_id, classifier, matched_string, exit_code, output_preview, confidence | **denied** row |
| `tool_connection_failed` | tool, call_id, classifier, matched_string, exit_code, output_preview | **conn-fail** row |
| `tool_escalation_requested` | tool, call_id, sandbox_permissions, justification, cmd_preview | **escalation** row |
| `private_tool_call_aborted` | tool_name, reason | fallback row |
| `empty_answer_repair_started` | repair_hop_index, reasoning_chars | repair row |
| `empty_answer_repair_completed` | repair_hop_index | repair row |
| `empty_answer_repair_failed` | repair_hop_index | repair row |
| `reasoning_only_aborted` | reason, reasoning_chars | fallback row |
| `reasoning_only_completed_without_answer` | reasoning_chars | fallback row |
| `client_disconnected` | — | — |
| `stream_event_timing` | event_type, latency fields | (internal) |
| `status_snapshot` | full status object | (internal) |

### Status and config endpoints

| Endpoint | Content |
|---|---|
| `GET /health` | proxy initialization state, upstream URL |
| `GET /qz/status` | selected profile/backend/context, health, reasoning, prompt status, load state |
| `GET /qz/config/effective` | active config paths, prompt files, memory_domains report, capture mode, warnings |
| `GET /qz/models/refresh` | triggers rescan + catalog regeneration |
| `GET /qz/telemetry/recent?limit=N` | last N events across retained lifecycle types |
| `GET /qz/telemetry/stream` | live SSE telemetry for monitors |
| `GET /v1/models` | Codex-visible model list with `memory_domain`, `profile_valid`, etc. |

### Captures

When `QZ_CAPTURE_MODE=full`:

```text
var/captures/latest-request.json                  incoming request body
var/captures/latest-normalized-request.json       forwarded body
var/captures/latest-request-contract.json         prompt contract + runtime metrics
var/captures/requests/<request_id>/               per-request scoped files
  incoming-request.json
  forwarded-request.json
  request-contract.json
  upstream-response.raw
  forwarded-sse.raw
```

Captures are debug artifacts, not model memory.

---

## QuantZhai → BrainCase memory tool plane

BrainCaseDB is the explicit memory/state storage substrate. Slices A–F complete.
See `docs/codex-context-memory-contract.md` and `docs/braincase-memory-tool-api.md`.

**BrainCaseDB stores:**

```text
StateRecords       — explicit memory/state records written through intentional write paths
SourceRefs         — provenance links attached to stored StateRecords
Record revisions   — retire/supersede chains for stored records
Record links       — record-to-record relationships
FTS / tag indexes  — search indexes over stored records
```

**BrainCaseDB does NOT store (by doctrine):**

```text
Sessions, turns, or requests merely because they were observed.
Operational runtime facts accumulated automatically.
Telemetry events or stream events.
Recovery/backoff state.
memory_domain registry entries.
```

Sessions/turns/requests may appear in BrainCaseDB ONLY as SourceRefs or provenance
attached to an actual stored StateRecord — not as automatic logs.

**BrainCase tool plane (Slice F):**

Slices F+G: braincase.render and braincase.recall are the exposed BrainCase tools.
Feature flag: QZ_BRAINCASE_TOOLS_ENABLED (default: disabled).
When enabled: both tool definitions injected into body["tools"];
harness policy added to turn harness. RenderPacket is the only model-visible
memory output. write/update/search/inspect remain unexposed.
braincase.recall uses predefined recall modes with tier routing.

**Historical note (superseded by BrainCase doctrine — do not implement as BrainCaseDB tables):**

The earlier "Phase 1 SQLite operational facts" plan listed sessions/turns/requests
as planned tables. That framing was superseded once BrainCaseDB doctrine was
established. BrainCaseDB is for memory/state records, not operational fact logging.

If operational-signal persistence becomes needed in the future, it should use a
separate store (not BrainCaseDB) or be explicitly designed as SourceRefs/provenance
attached to specific StateRecords with a written design doc.

**Hard rules:** Storage records are not automatically model-facing memory.
Recall results are not automatically model-facing memory. Renderers decide what
the model sees. "Safe to observe" does not mean "store in BrainCaseDB".

---

## Signal safety matrix

| Signal | Direction | Owner | Scope | Safe to store? | Safe → model? | Safe → Codex client? | Safe → /qz monitor? | Status |
|---|---|---|---|---|---|---|---|---|
| `session_id` | Codex→QZ | Codex | session | yes | no | no | yes | implemented |
| `thread_id` | Codex→QZ | Codex | thread | yes | no | no | yes | implemented |
| `turn_id` | Codex→QZ | Codex | turn | yes | no | no | yes | implemented |
| `x-codex-window-id` | Codex→QZ | Codex | turn | yes | no | no | yes | implemented |
| `workspace_id` | QZ-derived | QZ | workspace | yes | no | yes (qz-doctor) | yes | implemented |
| `memory_domain` | QZ-derived | QZ | profile | yes | no (internal) | no | yes | implemented |
| `qz_session_id` | QZ-generated | QZ | session | yes | no | no | no | implemented (no DB yet) |
| `qz_request_id` | QZ-generated | QZ | request | yes | no | no | yes | implemented |
| `x-codex-turn-state` | Codex→QZ | Codex | turn | raw-only | no | no | no | partial |
| `x-openai-subagent` | Codex→QZ | Codex | session | yes | no | no | yes | implemented |
| `x-openai-memgen-request` | Codex→QZ | Codex | request | yes | no | no | yes | implemented |
| `sandbox_permissions=require_escalated` | model outgoing→QZ | model | request | yes | no | no | yes (escalation row) | implemented |
| `tool_sandbox_denied` (classifier) | QZ-derived | QZ | request | yes | no | no | yes (denied row) | implemented |
| `tool_connection_failed` (classifier) | QZ-derived | QZ | request | yes | no | no | yes (conn-fail row) | implemented |
| `reasoning_only_completed_without_answer` | QZ-derived | QZ | request | yes | no | no | yes | implemented |
| `empty_answer_repair_*` | QZ-derived | QZ | request | yes | no | no | yes | implemented |
| `hop_budget_signal` | QZ→model input | QZ | turn | no (ephemeral) | **yes** (injected) | no | partial | implemented |
| raw captures | QZ-internal | QZ | request | debug only | **never** | no | no | implemented |
| prompt contract | QZ-derived | QZ | request | yes | no | no | yes | implemented |
| context length (selected vs backend) | QZ-derived | QZ | request | yes | no | yes (/qz/status) | yes | implemented |
| `prompt_cache_key` | Codex→QZ | Codex | thread | yes | no | no | no | implemented |
| `previous_response_id` | Codex→QZ | Codex | response | yes | no | no | no | partial |
| tool coercion error | QZ→model | QZ | request | yes | **yes** (via function_call_output) | no | yes | implemented |
| repeated-read advisory | QZ→model | QZ | request | no (ephemeral) | **yes** (advisory) | no | no | planned |
| `installation_id` | Codex→QZ | Codex | installation | yes | no | no | no | implemented |
| identity conflict | QZ-derived | QZ | request | yes (diagnostic) | no | no | yes | implemented |

---

## Current implemented support

What is real now (2026-05-14):

```text
Header parser: session_id, thread_id, turn_id, x-codex-window-id,
  x-codex-turn-metadata (workspaces, turn_started_at), x-client-request-id,
  x-codex-installation-id, x-codex-parent-thread-id, x-openai-subagent,
  x-openai-memgen-request, x-codex-turn-state (captured raw)

Body parser: prompt_cache_key, previous_response_id, reasoning.effort,
  reasoning.summary, text.verbosity, client_metadata, tool declarations,
  tools_count, tool_names

Workspace resolution: remote URL → workspace_id, repo_root → hashed path,
  unknown fallback; backfill from later turn metadata

Memory domain: loaded from qz.profiles.v1 or model-overrides; falls back
  to isolated when missing/invalid; wired through request context

Model feedback (implemented):
  - tool coercion error results (synthesize_tool_error_result)
  - hop budget signal near continuation limit
  - harness guidance text in system prompt (sandbox/tool failure handling)
  - reasoning effort prompt in instructions

Operator signals (implemented):
  - Full telemetry event set (see list above)
  - qz-thoughts renders: tool/escalation/denied/conn-fail/repair/fallback rows
  - /qz/status, /qz/config/effective, /qz/models/*
  - Request-scoped captures when QZ_CAPTURE_MODE=full
  - live smoke test (scripts/qz-live-smoke)
```

---

## Current gaps

```text
1. No BrainCase memory records yet — identity/turn/workspace facts are parsed
   but not persisted. They may appear only as SourceRef provenance attached
   to an explicit StateRecord (not as automatic operational logs).

2. No repeated-read advisory signal — the proxy sees repeated reads but does
   not yet inject an advisory result.

3. x-codex-turn-state captured raw but not decoded — sticky routing token
   semantics unknown; intentionally deferred.

4. context_pressure_signal emitted as telemetry only — not injected into
   model input; model cannot self-regulate on context pressure yet.

5. Tool-call count not tracked per turn as a model-visible signal.

6. No telemetry filter endpoint — /qz/telemetry/recent?type=tool_sandbox_denied
   does not exist; wide limit required (tracked in #39).

7. Codex-visible vs operator-only boundary lacks exhaustive tests for some
   events — particularly reasoning summary mode variants.

8. VRAM/backend allocation breakdown is an approximation — exact model/KV/
   scratch allocation not exposed by llama.cpp yet.

9. Previous_response_id chain resolution deferred — captured but not walked.

10. Compact and memory summarise endpoints (/v1/responses/compact,
    /v1/memories/summarize) are not yet handled by QuantZhai proxy routing
    (tracked in #40 and related).
```

---

## Highest-value next signals

Ranked by expected practical impact:

1. **Repeated-read advisory signal (P2)** — reduces wasted tool calls; v1 is
   stateless and input-history-seeded; no SQLite required; implementation plan
   exists in `docs/repeated-read-dedup-plan.md`.

2. **BrainCase recall/write semantics (next #53 slice)** — defines how the LLM
   reads and writes durable memory records. Operational signal persistence (if
   needed) requires a separate runtime-state store design; it does not belong
   in BrainCaseDB. See `docs/braincase-memory-tool-api.md`.

3. **Compaction/stream-hang watchdog signal (#40)** — detects stalled
   compaction or stream hang and emits telemetry; enables recovery and user
   visibility; design tracked in #40.

4. **Telemetry type filter (#39)** — `/qz/telemetry/recent?type=X` reduces
   SSE chatter false-failures in smoke tests and monitors.

5. **Context pressure model injection** — already emitted as telemetry; adding
   injection into model input (similarly to hop budget signal) would allow
   self-regulation on long turns.

6. **Tool-call count signal** — simple to implement alongside repeated-read v1;
   complements hop budget signal.

---

## Test and capture evidence index

| Evidence | Path | What it covers |
|---|---|---|
| Header parser tests | `tests/test_qz_codex_metadata.py` | window_id, session/thread parsing, turn metadata, workspace candidates |
| Request metadata tests | `tests/test_qz_codex_request_metadata.py` | full context extraction, memory_domain, no-inference rules |
| Mutation regression | `tests/test_qz_request_mutation_regression.py` | no qz_* injection into forwarded body |
| Native tool classifier | `tests/test_qz_native_tool_output.py` | tool_sandbox_denied, tool_connection_failed, pre-normalization wiring |
| Harness guidance | `tests/test_codex_harness_guidance.py` | guidance text in codex-core-qwenified.md |
| Escalation detector | `tests/test_qz_responses_stream.py` (SandboxEscalationDetectionTests) | require_escalated detection, non-string coercion |
| qz-thoughts rendering | `tests/test_qz_thoughts_cli.py` | denied/conn-fail/escalation activity rows |
| Profile/catalog tests | `tests/test_qz_model_catalog.py` | memory_domain on catalog entries, profiles.v1 loader |
| Config report tests | `tests/test_qz_config_report.py` | memory_domains section, profiles.v1 paths in effective config |
| Live probe capture | `docs/codex-0130-live-signal-capture.md` + `var/audits/qz-codex-0130-signal-probe-*` | Codex 0.130 header/body/workspace evidence |
| Denied command capture | `var/captures/requests/qz_req_1778738225429_b2b0/` | real function_call_output with Read-only file system |
| Live smoke | `scripts/qz-live-smoke` | end-to-end: health, normal path, tool_sandbox_denied |

---

## Open questions

1. **x-codex-turn-state format** — what is the internal schema of the sticky
   routing token? Can QuantZhai safely decode it, or must it remain opaque?

2. **previous_response_id chain resolution** — when would QuantZhai need to
   walk the response chain vs simply storing the ID? Deferred until a concrete
   use case emerges.

3. **ThreadMemoryMode visibility** — is `SetThreadMemoryMode` ever observable
   via HTTP/SSE or only via the internal TUI protocol? The 0.130 HTTP/SSE
   capture did not emit it.

4. **Compaction/memory endpoints** — what is the exact request/response shape
   for `/v1/memories/summarize` and `/v1/responses/compact` from Codex's
   perspective? Needed before QuantZhai can intercept or route these.

5. **Signal injection safety** — at what point does a model-visible signal
   become a "second truth" that conflicts with the proxy's actual behaviour?
   This boundary needs tests for each new injection point.

---

## Cross-references

| Issue/PR | Relevance |
|---|---|
| #8 | Survival-weighted compaction RFC — compaction pressure and trigger signal |
| #9 | Reasoning-only no-visible-answer repair — implemented; `empty_answer_repair_*` events |
| #14 | qz-thoughts telemetry/reconnect — monitor signal surface |
| #23/PR#24/PR#25 | memory_domain plumbing — memory.domain in profiles; isolated fallback |
| #26/PR#27 | qz.profiles.v1 active config — profile_id, backend routing, memory.domain |
| #28/PRs#31-34 | Sandbox/tool-failure telemetry — escalation, denied, conn-fail events; harness guidance |
| #35/PR#36 | Live stack smoke — validates signal surface end-to-end |
| #37 | Architectural seam extraction — boundaries relevant to signal routing |
| #38 | Docs refresh — this doc is part of the refresh |
| #39 | Telemetry filter ergonomics — `/qz/telemetry/recent?type=X` |
| #40 | Compaction/stream hang watchdog — new telemetry events planned |
| #41 | This document |

---

## Hard safety rules

These rules are not negotiable and must be preserved in any implementation that
touches signal routing:

```text
1. Do not inject QuantZhai-owned qz_* internals into forwarded /v1/responses
   bodies unless an explicit design document approves it.
   Test: tests/test_qz_request_mutation_regression.py

2. Do not grant memory access from tool names, client names, model names,
   profile names, prompt text, or vibes.
   Test: tests/test_qz_codex_request_metadata.py::test_no_memory_domain_inference

3. Missing memory_domain means isolated. No fuzzy fallback.

4. Capability set (tool declarations) is not authority for memory access.

5. Raw captures are debug artifacts, not model memory.

6. Storage records are not automatically model-facing memory.

7. Recall results are not automatically model-facing memory.

8. Renderers decide what the model sees.

9. Keep /v1/responses upstream-compatible.

10. Use /qz/* for QuantZhai-specific control/observability.
```
