# Foundation Audit Before SQLite

Date: 2026-05-15

Status: audit/docs/planning only. No SQLite implementation, runtime refactor,
stream behaviour change, recovery behaviour change, `qz.vram.snapshot.v1`
change, or generic signal-framework migration.

Read with:

```text
docs/current-architecture-authority.md
docs/current-task-hierarchy.md
docs/codex-context-memory-contract.md
docs/codex-quantzhai-bidirectional-signal-map.md
docs/signal-feedback-subsystem-plan.md
```

## 1. Executive summary

QuantZhai is ready to start #2 only as a narrow Phase 1 SQLite storage substrate.
It is not ready for broad runtime-signal persistence or model-visible memory.

Recommended outcome: **B. Start #2 only after this audit is indexed and #2 is
kept to the parser-boundary/state-store scope already documented.** No runtime
code cleanup is required before #2 if the first SQLite slice stores only:

```text
sessions
turns
requests
workspace_candidates
resolved_workspaces
session_workspace_bindings
identity_conflicts
bounded request/body metadata summaries
```

Do not make SQLite a sink for every telemetry event in the first slice. Runtime
signals should be classified for future persistence, but only persisted when an
owning policy path exists.

The main architectural risk is not SQLite itself. The risk is bolting storage
onto modules that already mix detection, decision, rendering, routing, and
telemetry. The highest-risk modules are:

```text
proxy/qz_request_router.py
proxy/qz_responses_stream.py
scripts/qz-top
scripts/qz-thoughts
```

The strongest existing seams are:

```text
proxy/qz_codex_metadata.py       request identity/workspace/memory parser
proxy/qz_stream_terminal.py      stream terminal classifier
proxy/qz_stream_watchdog.py      stream timeout detector/payload builder
proxy/qz_feedback.py             feedback decision vocabulary
proxy/qz_native_tool_output.py   read-only native tool-output classifier
proxy/qz_recovery_*.py           recovery status/state/jobs/plan seams
docs/patterns/provenance-telemetry.md
```

## 2. Current gravity wells

| Module | Current responsibilities | Suspicious overlaps | Extraction candidates | Risk | Touch before #2? |
| --- | --- | --- | --- | --- | --- |
| `proxy/quantzhai_proxy.py` | HTTP handler class, process globals, initialization state, model catalog/backend client wiring, telemetry singleton wiring. | Imports older response helpers and stream classes even when request routing owns most behaviour. Holds many process-global authorities. | Keep as HTTP transport/handler glue. New state DB singleton can be attached here, but writes should be driven by router/parser seams. | Medium | Only for DB object wiring if #2 needs it. |
| `proxy/qz_request_router.py` | Route dispatch, model selection, prompt policy, capture contract, native tool-output observation, active request tracking, recovery endpoints/triggers, non-stream local tool loop, request telemetry. | Recovery trigger orchestration lives beside `/v1/responses`; prompt/capture summaries duplicate request context summaries future SQLite will want; repeated-read and native output decisions are wired here. | Thin route handlers over pure builders; request-state persistence adapter after `extract_codex_request_context()`; recovery trigger controller moved out when next touched. | High | Yes, but only at the request-context persistence seam. |
| `proxy/qz_responses_stream.py` | Streaming Responses adapter, SSE transformation, private tool-call assembly, proxy-local tool continuation, empty-answer repair, timeout fallback, reasoning-only aborts, hop/context advisories, stream telemetry. | Detection, decision, public stream rendering, next-hop mutation, and telemetry happen inside one loop. Some signals use `qz_feedback`, others emit bespoke telemetry. | Future stream state reducer; runtime signal decision router; repair/fallback renderer. Already extracted: terminal classifier, watchdog, tool lifecycle, proxy-local registry. | High | No. Do not touch for #2. |
| `proxy/qz_proxy_tools.py` / `proxy/qz_tool_lifecycle.py` | Tool lifecycle classification, proxy-local execution, apply_patch coercion, unknown/dropped tool errors, repeated-read advisory, streamed function-call assembly. | Repeated-read decision is embedded in tool-call decision; malformed/dropped tool errors are both model-visible and telemetry from stream/router branches. | Keep lifecycle and proxy-local execution together; later move signal decisions behind `qz_feedback` wrappers. | Medium | No unless #2 later persists file-read facts. |
| `proxy/qz_native_tool_output.py` | Read-only classifier for Codex-native `function_call_output` text, currently sandbox denied and connection failed. | Similar vocabulary to tool coercion failures, but a different source and safety level. | Keep separate. It can emit `SignalDecision` for telemetry, but must not mutate tool outputs. | Low | No. |
| `proxy/qz_telemetry.py` | In-memory event bus, request retention, telemetry state/recent/request payloads, stream subscribers. | Event names are a partial signal registry, but no central visibility/persistence policy. | Future lightweight signal catalog could live near docs/tests, not before #2. | Medium | No. |
| `scripts/qz-thoughts` | Operator stream monitor, event-specific rows, backend/control-plane rows, stream terminal and tool-failure rendering. | Derives user-facing meanings independently from qz-top. Treats unknown events as generic rows. | Renderer-only normalization helpers; no policy decisions. | Medium | No. |
| `scripts/qz-top` | Operator dashboard, control-plane parsing, recovery rows, VRAM panel, fallback `/qz/status` parsing, local GPU baseline. | Two status parsers (`/qz/control-plane` and `/qz/status`) can produce different detail; VRAM display has its own fallback meanings. | Prefer `/qz/control-plane` and `qz.vram.snapshot.v1`; keep fallback read-only. | Medium | No. |
| `proxy/qz_control_plane.py` | Single client-friendly status authority combining proxy/catalog/backend/service/recovery/VRAM. | Duplicates some fields from `/qz/status` for compatibility; recovery status embedded and separately exposed. | Keep as status authority. Avoid adding DB-specific state unless clearly operator status. | Low | No. |
| `proxy/qz_recovery_status.py` | Pure recovery status builder from service status plus runtime state/jobs/active requests. | None significant. | Later SQLite-backed runtime snapshot for #51. | Low | No, #51 after #2. |
| `proxy/qz_recovery_state.py` | In-memory recovery attempts, backoff, manual-required, in-progress state. | Future persistence target, currently intentionally volatile. | #51 durable store adapter after #2. | Low | No. |
| `proxy/qz_recovery_jobs.py` | In-memory async recovery job store. | Future persistence target, currently intentionally volatile. | #51 durable job/event rows after #2. | Low | No. |
| `proxy/qz_file_signal.py` | Repeated-read heuristic parser/state and file read/write helpers. | `record_tool_output()` is currently unused/pass; read/write facts are not durable; shell parsing is necessarily heuristic. | Future file fact recorder after #2; keep v1 advisory stateless until then. | Medium | No for #2 first slice. |
| `proxy/qz_codex_metadata.py` | Codex identity, request metadata, workspace candidates, workspace resolution, memory_domain, identity conflicts. | Router reshapes some context into prompt/capture contracts, but parsing is centralized here. | This is the #2 input boundary. SQLite must consume this API, not reparse headers/body. | Low | Yes, as read-only source for DB writes. |

## 3. Signal inventory

Legend:

```text
Persist later = no / candidate / yes
Operator-visible = telemetry, qz-top, qz-thoughts, control-plane, recovery endpoint
Codex/model-visible = forwarded public SSE item, function_call_output, request rejection, or next-hop advisory
```

| Signal / condition | Detector module | Current classification name | Current reaction | Operator-visible? | Codex/model-visible? | Persist later? | Duplicate/overlap? | Recommended owner |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `stream_no_output_timeout` | `qz_stream_watchdog.py`, finished by `qz_responses_stream.py`, classified by `qz_stream_terminal.py` | `stream_no_output_timeout` | `stream_terminal_classified`; synthetic fallback answer; terminal completion | Yes | Yes, public fallback answer | Candidate | Timer detector plus terminal classifier is intentional | Stream watchdog + terminal classifier |
| `stream_terminal_timeout` | `qz_stream_watchdog.py`, finished by `qz_responses_stream.py`, classified by `qz_stream_terminal.py` | `stream_terminal_timeout` | `stream_terminal_classified`; synthetic terminal completion without replaying answer | Yes | Yes, terminal completion only | Candidate | Intentional detector/classifier split | Stream watchdog + terminal classifier |
| `compact_failed` | `qz_stream_terminal.py` observations from stream evidence | `compact_failed` | `stream_terminal_classified` | Yes | Usually no extra beyond existing fallback/error path | Candidate | Overlaps with empty-answer fallback symptoms | Stream terminal classifier |
| `protocol_drift_seen` | `qz_stream_terminal.py` | `protocol_drift_seen` | `stream_terminal_classified` | Yes | No | Candidate | Could be confused with malformed terminal handling in stream loop | Stream terminal classifier |
| `unrecoverable` | `qz_stream_terminal.py` | `unrecoverable` | `stream_terminal_classified` | Yes | No direct model chatter | Candidate | None significant | Stream terminal classifier |
| `stream_completed_without_visible_answer` | `qz_stream_terminal.py`, repair logic in `qz_responses_stream.py` | `stream_completed_without_visible_answer` | May start empty-answer repair; fallback if repair fails; telemetry | Yes | Yes only through repair/fallback output | Candidate | Overlaps with reasoning-only completed | Stream state reducer later |
| `stream_terminal_missing` | `qz_stream_terminal.py` | `stream_terminal_missing` | `stream_terminal_classified` when non-ok | Yes | No direct extra | Candidate | Overlaps with terminal timeout only when timeout enabled | Stream terminal classifier |
| `reasoning_only_aborted` | `qz_responses_stream.py` | `reasoning_only_aborted` | synthetic fallback answer; `stream_completed` fallback | Yes | Yes, public fallback answer | Candidate | Stream loop owns both detection and render | Future stream state reducer |
| `reasoning_only_completed_without_answer` | `qz_responses_stream.py` | `reasoning_only_completed_without_answer` | fallback answer after repair budget exhausted | Yes | Yes, public fallback answer | Candidate | Overlaps with empty-answer repair failed | Future stream state reducer |
| `empty_answer_repair_started` | `qz_responses_stream.py` | `empty_answer_repair_started` | hidden repair hop with tools disabled by default | Yes | Indirect: next upstream hop gets repair instruction | Candidate | Repair decision lives inside stream loop | Future stream repair policy |
| `empty_answer_repair_completed` | `qz_responses_stream.py` | `empty_answer_repair_completed` | telemetry only | Yes | No extra | Candidate | None | Future stream repair policy |
| `empty_answer_repair_failed` | `qz_responses_stream.py` | `empty_answer_repair_failed` | telemetry plus public fallback answer | Yes | Yes, public fallback answer | Candidate | Same symptom as reasoning-only completed | Future stream repair policy |
| `hop_budget_signal` | `qz_responses_stream.py` | `hop_budget_signal` | next-hop advisory user message | Yes | Yes, next-hop advisory | Candidate | Signal injection is embedded in stream loop | Future signal decision router |
| `context_pressure_signal` | `qz_responses_stream.py` | `context_pressure_signal` | next-hop advisory user message when threshold met | Yes | Yes in streaming continuation hops | Candidate | Uses selected-model context, not central pressure policy | Future signal decision router |
| Malformed tool call | `qz_tool_lifecycle.py`, `qz_proxy_tools.py`, stream assembler | invalid historical call, function-call error | Drop invalid history or inject tool error | Partly | Yes, function_call_output error for active calls | Candidate | History replay filter and active-call errors are separate | Tool lifecycle |
| Dropped/unknown tool | `qz_proxy_tools.py` | `tool_call_error` / synthesized tool error | function_call_output error; telemetry in stream path | Yes in stream path | Yes | Candidate | Non-stream and stream branches emit differently | Tool lifecycle |
| `apply_patch` coercion | `qz_proxy_tools.py`, adapters | `public` or `error` decision | native/custom public item or function_call_output error | Partly | Yes | Candidate | Coercion is distinct from native output classification | Tool lifecycle |
| Proxy-local tool started | `qz_proxy_tools.py`, `qz_responses_stream.py` | `tool_call_started` | public lifecycle item; telemetry | Yes | Yes, public tool lifecycle | Candidate | Stream and non-stream loops have separate wiring | Proxy-local tool registry |
| Proxy-local tool completed | `qz_proxy_tools.py`, `qz_responses_stream.py` | `tool_call_completed` | public lifecycle done; upstream continuation input | Yes | Yes | Candidate | Same | Proxy-local tool registry |
| `tool_call_error` | `qz_proxy_tools.py`, `qz_responses_stream.py` | `tool_call_error` | function_call_output error; next hop | Yes | Yes | Candidate | Error shaping and telemetry live in different branches | Tool lifecycle |
| `tool_sandbox_denied` | `qz_native_tool_output.py` | `tool_sandbox_denied` | telemetry and qz-thoughts row | Yes | Existing tool output already visible to Codex; no extra | Candidate | Similar text could appear in multiple native tools | Native output classifier |
| `tool_connection_failed` | `qz_native_tool_output.py` | `tool_connection_failed` | telemetry and qz-thoughts row | Yes | Existing tool output already visible to Codex; no extra | Candidate | Similar text could appear in multiple native tools | Native output classifier |
| `tool_escalation_requested` | `qz_responses_stream.py` | `tool_escalation_requested` | telemetry and qz-thoughts row | Yes | The tool request itself is visible through normal Codex UX | Candidate | Streaming path only today | Tool request observer |
| `repeated_read_signal` | `qz_file_signal.py`, called by `qz_proxy_tools.py` | `repeated_read_signal` | advisory `function_call_output`; telemetry | Yes | Yes | Yes, v2 | Underused file-output/write facts | File signal policy |
| `backend_unavailable` | `qz_control_plane.py`, service/recovery builders, router admission | `backend_unavailable` / `responses_rejected_backend_unavailable` | control-plane/recovery status; 503 rejection for requests | Yes | Yes only as request rejection | Yes | `/qz/status` and control-plane compatibility | Control-plane/service status |
| `backend_healthy_unloaded` | service/recovery status over control-plane | `model_not_loaded` / reload available | operator hint; recovery plan/trigger available | Yes | No normal-turn chatter | Yes via #51 | None significant | Service/recovery status |
| `model_missing` | router model selection | `responses_rejected_model_missing` | compact 503 with fix hint | Yes | Yes, request rejection | Yes | Catalog/profile status also reports invalid entries | Request admission/model router |
| `recovery_backoff_active` | `qz_recovery_state.py`, `qz_recovery_status.py` | `backoff_active` | recovery status/plan blocks trigger | Yes | No | Yes via #51 | None | Recovery runtime state |
| `recovery_in_progress` | `qz_recovery_state.py` | `in_progress` | recovery status/plan/trigger gating | Yes | No | Yes via #51 | None | Recovery runtime state |
| Recovery job pending/running/completed/failed | `qz_recovery_jobs.py` | `queued`, `running`, `completed`, `failed` | `/qz/recovery/jobs`, `/qz/recovery/status`, telemetry | Yes | No | Yes via #51 | None | Recovery job store |
| `start_backend` | router recovery trigger | recovery action | gated trigger; telemetry | Yes | No | Yes via #51 | Action policy in router | Recovery action controller |
| `restart_backend` | router recovery trigger | recovery action | gated trigger; telemetry | Yes | No | Yes via #51 | Action policy in router | Recovery action controller |
| `reload_selected_model` | router recovery trigger/job worker | recovery action/job | gated sync/async trigger; telemetry | Yes | No | Yes via #51 | Async worker in router | Recovery action controller/job store |
| `clear_failure` | router recovery trigger | recovery action | clears in-memory recovery state | Yes | No | Yes via #51 | None | Recovery action controller |
| `refresh_catalog` | router recovery trigger | recovery action | refreshes proxy catalog | Yes | No | Candidate | Catalog status elsewhere | Model catalog/control-plane |
| `select_model` | router recovery trigger | recovery action | updates selected model only | Yes | No | Candidate | Model router and catalog policy | Model catalog/control-plane |
| VRAM overallocated/calibrated | `qz_vram_snapshot.py`, rendered by `qz-top` | `qz.vram.snapshot.v1` confidence/components | operator dashboard/status | Yes | No | Candidate | Must keep provenance doctrine | VRAM snapshot owner |
| Backend metrics unavailable | `qz_vram_snapshot.py` | backend metrics availability false | operator confidence/provenance | Yes | No | Candidate | None | VRAM snapshot owner |
| Model/KV allocator metrics unknown | `qz_vram_snapshot.py` | confidence/formula safety fields | operator confidence/provenance | Yes | No | Candidate | None | VRAM snapshot owner |
| Active requests count | `qz_active_requests.py`, control-plane/recovery | `active_requests` | recovery safety/status rows | Yes | No | Yes via #51 maybe | None | Active request tracker/recovery status |
| qz-top/qz-thoughts monitor rows | scripts from control-plane/telemetry | renderer rows | operator display only | Yes | No | No as rows | Renderers can diverge | Monitor renderers |
| `memory_domain` | `qz_codex_metadata.py`, profile config | `memory_domain` | prompt/capture contract; future DB fact | Yes in captures/telemetry | No body injection | Yes | Router also copies into contracts | Codex metadata parser |
| Workspace candidates | `qz_codex_metadata.py` | `workspace_candidates` | parser result, future DB fact | Not normally rendered | No | Yes | No duplicate parser found | Codex metadata parser |
| Resolved workspace | `qz_codex_metadata.py` | `workspace_id` / source | parser result, future DB fact | Not normally rendered | No | Yes | No duplicate parser found | Codex metadata parser |
| Identity conflict | `qz_codex_metadata.py` | `identity_conflict` / conflict notes | parser result, future DB fact | Not normally rendered | No | Yes | No duplicate parser found | Codex metadata parser |
| Request metadata / Codex identity | `qz_codex_metadata.py`, router contracts | request/body metadata fields | prompt/capture contract; future DB fact | Yes in captures/telemetry | No body injection | Yes | Router reshaping is not a parser duplicate | Codex metadata parser |

## 4. Detection -> decision -> reaction map

Current runtime paths mix these stages in several places:

```text
detect:    observe raw request, tool output, stream event, status, or timer
classify:  assign a stable condition name and severity/visibility facts
decide:    choose whether action is telemetry-only, operator-only, model-visible,
           request-rejecting, recovery-triggering, or future-persistent
render:    produce public SSE, function_call_output, monitor row, JSON status, or
           telemetry payload
route:     send the rendered item to Codex, monitors, control-plane, recovery,
           capture, or future SQLite
persist:   store bounded facts later without changing visibility
```

Current examples:

| Signal family | Detect | Classify | Decide | Render | Route | Persist later |
| --- | --- | --- | --- | --- | --- | --- |
| Stream timeout | Watchdog socket/event timers | `qz_stream_terminal.py` | `qz_responses_stream.py` fallback path | fallback SSE or terminal completion plus telemetry | Codex public stream + telemetry/qz-thoughts | Candidate, not #2 first slice |
| Empty answer | Stream loop response analysis | local repair labels and terminal labels | repair hop or fallback | hidden repair instruction or fallback SSE | upstream next hop + Codex public stream + telemetry | Candidate |
| Tool coercion/error | Tool lifecycle/registry | `CompletedToolCallDecision` | inject error, proxy-local execute, or public item | function_call_output or public tool item | next upstream hop/Codex stream | Candidate |
| Native tool-output failure | Raw input observer before normalization | `qz_native_tool_output.py` | telemetry-only | telemetry payload/qz-thoughts row | operator telemetry | Candidate |
| Recovery/backend | Control-plane/service/recovery builders | service/recovery states and actions | request rejection, trigger availability, trigger execution | JSON status/rejection/job/status rows | control-plane/recovery endpoints/telemetry | Yes, #51 after #2 |
| Request identity/context | Headers/body/profile config | `CodexRequestContext` | internal request facts only | prompt/capture contract now; DB rows later | telemetry/captures/future SQLite | Yes, #2 first slice |
| VRAM/resource | Snapshot collector and backend/model facts | provenance/confidence fields | operator-only | `qz.vram.snapshot.v1`, qz-top rows | control-plane/qz-top | Candidate |

Reaction taxonomy:

| Reaction type | Current use | Notes |
| --- | --- | --- |
| `telemetry_only` | native tool-output failures, stream timing, repair lifecycle, context/hop signal events | Operator data, not automatically model-visible. |
| `operator_monitor_row` | qz-top/qz-thoughts rows | Rendering only. Do not let scripts become policy authorities. |
| `model_visible_function_call_output` | unknown/dropped tool errors, repeated-read advisory | Useful when the model can recover on the next hop. Keep bounded. |
| `codex_public_stream_item` | proxy-local tool lifecycle, public tool items, fallback messages | User-visible stream surface. Must be tested with SSE contract. |
| `synthetic_fallback_answer` | reasoning-only abort, no-output timeout, empty-answer repair failure | Last-resort user-facing completion. |
| `synthetic_terminal_completion` | terminal-after-output timeout, malformed/done-without-completed repair | Closes client state without replaying content. |
| `next_hop_advisory_message` | hop budget, context pressure, empty-answer repair instruction | Internal upstream input mutation. Use sparingly. |
| `request_rejection` | backend unavailable, proxy not ready, model missing | Codex-visible HTTP error with compact operator hint. |
| `recovery_trigger_available` | recovery status/plan | Operator/control-plane visibility only. |
| `recovery_trigger_executed` | recovery trigger/job endpoints | Operator action path, gated and local-only. |
| `future_sqlite_fact` | request context, recovery state later, bounded runtime signal facts later | Persistence does not imply model visibility. |
| `future_model_visible_memory_packet` | not implemented | Renderer-owned future feature, outside #2. |

## 5. Operator-visible vs Codex/model-visible feedback

Operator-visible channels:

```text
telemetry bus
/qz/control-plane
/qz/recovery/status
/qz/recovery/jobs/<request_id>
/qz/status compatibility
qz-top
qz-thoughts
captures under var/captures
```

Codex/model-visible channels:

```text
forwarded Responses SSE
proxy-synthesized public SSE items
function_call_output injected into the next input
next-hop advisory user messages
HTTP request rejection JSON
```

Current conservative decisions:

| Signal | Codex should receive now | Rationale |
| --- | --- | --- |
| `stream_no_output_timeout` | public streamed fallback | User otherwise stays stuck. |
| `stream_terminal_timeout` | synthetic terminal completion only | The user may already have visible output; avoid duplicate answer text. |
| `compact_failed` | no extra chatter beyond fallback/error path | Usually internal stream/protocol evidence. |
| Recovery available | nothing in normal turns; request rejection when admission fails | Recovery is operator-owned; normal model chatter would be noisy. |
| Backend model unloaded | request rejection or control-plane status, not prompt injection | Loading/recovery is infrastructure state. |
| Repeated read detected | advisory `function_call_output` | The model can choose a different action immediately. |
| Sandbox denied | telemetry only beyond native tool output | Codex already sees the failed tool output. Duplicating it risks noise. |
| Context pressure | current next-hop advisory only when threshold triggers | Useful but should stay sparse. |
| Hop budget low | current next-hop advisory | Directly helps stop runaway continuation loops. |
| Protocol drift | operator telemetry/future fact only | The model cannot fix transport protocol drift reliably. |
| Empty-answer repair | hidden repair hop, fallback if failed | Do not expose internal repair JSON or chatter. |

Answer to "what signals are detected but not telling Codex/qz-codex about?":

```text
protocol_drift_seen
compact_failed detail
stream_terminal_missing detail
recovery available/backoff/job progress
backend healthy but model unloaded
VRAM allocator confidence gaps
identity conflicts
workspace candidate/resolution facts
native tool-output classifications beyond the native tool output itself
```

That is mostly correct. Add Codex-visible feedback only when it helps the model
recover inside the current turn. Prefer future side-channel status endpoints or
future SQLite facts for infrastructure and provenance signals.

## 6. Duplicated or overlapping paths

| Overlap | Classification | Before SQLite? | Needs issue? | Notes |
| --- | --- | --- | --- | --- |
| Stream timeout detection in watchdog plus terminal classification in terminal module | Harmless | No | No | Detector and classifier are separate by design. |
| Stream loop decides repair/fallback while terminal module classifies final result | Can wait | No | Maybe #37 comment | Real gravity well, but touching before #2 risks stream regressions. |
| Empty-answer completed-without-answer and terminal `stream_completed_without_visible_answer` | Should be unified eventually | No | Maybe new issue | Same symptom family, different reaction branches. |
| Tool coercion errors and native tool-output classifiers share tool-failure vocabulary | Harmless if kept separate | No | No | One mutates/continues model flow; one only observes historical/native output. |
| Repeated-read advisory embedded in tool decision instead of generic feedback route | Can wait | No | #42 follow-up optional | V1 is small and tested; v2 persistence later. |
| `qz_feedback.SignalDecision` exists but most stream/runtime signals still emit bespoke telemetry | Can wait | No | #42 follow-up optional | Do not migrate framework before #2. |
| Recovery status embedded in `/qz/control-plane` and separately exposed as `/qz/recovery/status` | Acceptable | No | No | Recovery endpoint derives from control-plane/service status, not independent probing. |
| qz-top parses both `/qz/control-plane` and `/qz/status` | Acceptable compatibility | No | Maybe #46/#5 later | Prefer control-plane; fallback is read-only. |
| qz-top and qz-thoughts render separate meanings from telemetry/control-plane | Can wait | No | No unless divergence causes a bug | Scripts should remain renderers, not authorities. |
| Router prompt/capture contracts and future SQLite request rows need similar context facts | Should be controlled before #2 implementation | Yes, as design constraint | #2 prerequisite comment | DB must consume `extract_codex_request_context()` and bounded summaries, not invent another parser. |
| Runtime launcher trace in `scripts/qz-write-runtime-state` vs live control-plane | Known old path | No for #2 first slice | Existing #46 | Do not remove until startup telemetry or SQLite replacement exists. |

## 7. Underused signals

| Signal | Current use | Better future use |
| --- | --- | --- |
| `protocol_drift_seen` | Operator telemetry only | Persist bounded incident facts; regression dashboard; possible issue evidence. |
| `compact_failed` | Operator telemetry/fallback evidence | Persist bounded stream outcome facts after stream-state policy exists. |
| `stream_terminal_missing` | Operator telemetry | Same as above; useful for transport-quality history. |
| `context_pressure_signal` | Next-hop advisory plus telemetry | Future turn-level fact, but not model memory. |
| `hop_budget_signal` | Next-hop advisory plus telemetry | Future turn-loop fact for tuning continuation caps. |
| `empty_answer_repair_*` | Telemetry and qz-thoughts rows | Future model/backend quality metric. |
| `tool_escalation_requested` | Streaming telemetry only | Extend detection coverage if non-stream native path matters. |
| `tool_sandbox_denied` / `tool_connection_failed` | Operator telemetry from raw input | Future tool reliability facts, not automatic model feedback. |
| `repeated_read_signal` | V1 advisory only | V2 same-scope SQLite file facts after #2. |
| `record_tool_output()` in `qz_file_signal.py` | Stub/pass | Later file-write/outcome tracking for repeated-read v2. |
| Identity conflicts | Parser result only | #2 first slice should persist bounded conflict notes. |
| Workspace candidates/resolution | Parser result only | #2 first slice should persist candidates and resolved binding. |
| Recovery backoff/jobs | In-memory status only | #51 after #2. |
| VRAM unknown allocator metrics | Operator provenance | Keep #52 upstream tracker; no #2 dependency. |

## 8. Recommended unification seams

Pre-#2 minimum:

```text
1. Treat qz_codex_metadata.extract_codex_request_context() as the only
   SQLite input parser for identity/workspace/memory facts.
2. Keep #2 writes optional/non-fatal and scoped to bounded parser-boundary
   identity/scoping facts.
3. Add no model-visible feedback from #2.
```

Post-#2 or when touching the relevant area:

```text
1. Stream state reducer
   Move stream observation accumulation, terminal classification inputs,
   repair eligibility, and fallback decision into a pure reducer. Keep SSE
   rendering in qz_responses_stream.py.

2. Runtime signal decision router
   Use the existing qz_feedback vocabulary for runtime signals only after one
   or two concrete stream/tool cases prove the shape. Do not do a framework
   migration first.

3. Recovery persistence adapter
   Back #51 with SQLite using recovery state/job stores as the interface.

4. File signal fact recorder
   After #2, record same-scope file read/write/signal facts for repeated-read
   v2. Do not expand v1 heuristics into persistence ad hoc.

5. Monitor render normalization
   Keep qz-top and qz-thoughts read-only, but share small formatting/parsing
   helpers if repeated drift appears.
```

## 9. What must happen before #2 SQLite

Before implementation starts:

```text
1. Index this audit in docs/README.md.
2. Update current control docs so #40 is no longer treated as the next blocker.
3. Add a prerequisite note to #2 before coding: Phase 1 SQLite must consume
   qz_codex_metadata.py parser output and must not persist broad runtime signal
   history in its first slice.
```

No runtime refactor is required before #2. Specifically, do not extract stream
state first and do not implement a generic signal framework first.

The #2 first implementation slice is safe if it touches only:

```text
proxy/qz_braincase_db.py
request-router integration immediately after extract_codex_request_context()
small tests proving optional/non-fatal DB writes and parser-boundary storage
docs describing DB scope
```

## 10. What can wait until after #2

```text
stream-state reducer extraction
empty-answer/terminal-fallback unification
runtime signal decision router
generic qz_feedback adoption for stream/runtime signals
repeated-read v2 durable file facts
recovery/backoff/job persistence (#51)
launcher runtime-state trace removal (#46)
qz-top/qz-thoughts renderer convergence
VRAM allocator upstream metrics (#52)
search/config/script cleanup
```

## 11. Suggested issue updates / new issues

Do not close or create issues solely from this audit without first posting the
summary below and confirming the issue scope.

Suggested comment for #2:

```text
Foundation audit before #2: Phase 1 remains safe to start only as a
parser-boundary SQLite storage substrate. The DB should consume
extract_codex_request_context() from qz_codex_metadata.py, store bounded
sessions/turns/requests/workspace/identity-conflict facts, and remain
optional/non-fatal. Do not make the first SQLite slice a generic runtime signal
store or model-visible memory path. Runtime stream/tool/recovery signals can be
classified as future_sqlite_fact candidates, but should wait for owning policy
seams.

Audit doc: docs/foundation-audit-before-sqlite.md
```

Suggested comment for #37:

```text
Foundation audit confirms the main gravity wells are qz_request_router.py and
qz_responses_stream.py. For #2, only the request-context persistence seam should
be touched. Stream-state extraction should wait until a stream behaviour change
is planned. The useful future seam is a pure stream state reducer that consumes
observations and returns repair/fallback/terminal decisions while leaving SSE
rendering in qz_responses_stream.py.

Audit doc: docs/foundation-audit-before-sqlite.md
```

Suggested narrow follow-up from #42, if desired:

```text
Title: Normalize runtime signal routing after SQLite substrate

Body:
After #2 creates the optional SQLite storage substrate, audit one or two
runtime signal families for qz_feedback adoption without changing stream
behaviour. Start with bounded cases such as empty-answer repair lifecycle and
stream terminal classification. Keep operator telemetry, Codex-visible
feedback, and future SQLite persistence decisions separate. Do not migrate
every stream signal or create a broad framework rewrite.
```

Do not create `Add generic SignalDecision core types`: `qz_feedback.py` already
exists and the current need is adoption policy, not another core type set.

Possible future issue if stream work resumes:

```text
Title: Extract stream state reducer from qz_responses_stream

Body:
qz_responses_stream.py still owns stream observation accumulation, repair
eligibility, fallback decisions, next-hop advisory injection, public SSE
rendering, and telemetry hooks. Extract a pure reducer only when stream
behaviour is otherwise being changed. Preserve current SSE output, watchdog
semantics, recovery behaviour, and terminal classifications.
```

## 12. Suggested next implementation slices

1. **Docs/control update**
   Index this audit and mark #2 as next only under parser-boundary constraints.

2. **#2 slice 1: optional DB open and schema**
   Add a minimal DB module with `schema_version`/`PRAGMA user_version`,
   `QZ_STATE_DB_PATH`, optional/non-fatal open, and tests. This is storage
   plumbing for future state/memory experiments, not a telemetry warehouse or
   memory_domain registry.

3. **#2 slice 2: parser-boundary request facts**
   Persist `CodexRequestContext` facts and bounded request/body metadata
   summaries. No raw request bodies by default.

4. **#2 slice 3: workspace and identity conflicts**
   Persist workspace candidates, resolved workspace, session-workspace binding,
   and identity conflict notes.

5. **#51 after #2**
   Persist recovery runtime state/jobs through a narrow adapter. Keep in-memory
   fallback.

6. **#46 after #2 or startup telemetry**
   Replace launcher trace authority with a live startup/state path.

7. **Runtime signal unification later**
   Pick one concrete stream/tool signal family, add tests, and keep detect,
   classify, decide, render, route, and persist decisions separate.
