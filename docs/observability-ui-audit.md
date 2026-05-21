# qz-thoughts / qz-top Observability Audit

Date: 2026-05-22
Status: Slice E discovery — authoritative observability map.

Related:
- `docs/runtime-streaming-tool-contract-audit.md` — Slice A (corrected: qz-thoughts IS live).
- `docs/tool-schema-coercion-audit.md` — Slice B coercion gaps.
- `docs/streaming-event-mapper-audit.md` — Slice C mapper gaps.
- `docs/metadata-propagation-audit.md` — Slice D metadata gaps.

---

## 1. Observability Inventory

### qz-thoughts data sources

| source | method | what it provides |
|---|---|---|
| `/qz/telemetry/stream` (SSE) | `TelemetryFeed._iter_sse_events` — `urllib.request.urlopen` with `Accept: text/event-stream`, 30s timeout, reconnect on error | live telemetry events: all proxy-emitted events including `sse_event` wrappers |
| `/qz/telemetry/events` | Fallback URL tried when `/stream` fails | same payload format |
| `/qz/telemetry/recent` | Initial backfill via `load_telemetry_state()` at startup | `qz.telemetry.recent.v1` payload with `events` array and `state.latest_completed_events` |
| `/qz/config/effective` | Polled at startup and on reconnect via `load_config_hint_rows()` | capture mode, config source, settings |
| `/qz/control-plane` | Polled at reconnect via `control_plane_status()` → `control_plane_rows()` | backend/proxy status rows for backend panel |
| File path (`--file` or `--path`) | `load_state(path)` — reads raw SSE bytes | direct file mode for captures/testing; standalone, no telemetry |

**Event polling/streaming method**: SSE stream via `urllib.request.urlopen` with 30s total timeout. Read timeout not set — stream reads block until data or server closes. Idle reconnect fires if read times out.

**Cursor/sequence handling**:
- `state.last_seq` tracks highest sequence number seen.
- Events with `seq <= state.last_seq` are skipped (dedup).
- On **reconnected** (proxy restart): `state.last_seq = 0` reset (floor raised to 0 to accept fresh events).
- On **idle_reconnected**: `state.last_seq` preserved (avoids re-emitting old events after idle disconnect).

**Reconnect behaviour**:
- `status == "unavailable"` → backend panel shows `("proxy", "unavailable")`.
- `status == "reconnected"` → `last_seq = 0`, shows `("proxy", "reconnected")`.
- `status == "idle_reconnecting"` → `state.status = "reconnecting"`, `last_seq` unchanged.
- `status == "idle_reconnected"` → `state.status = "ok"`, `last_seq` unchanged.
- URL rotation: tries `/qz/telemetry/stream` then `/qz/telemetry/events` on failure.

**Event type filters**: No active filter — all events delivered via SSE are processed. `sse_event` wrappers are processed by `_apply_response_event`. Direct telemetry events handled by `_apply_telemetry_event`.

**Thought/reasoning panel**: populated by `_apply_thought_delta` on `response.reasoning_text.delta` and `response.reasoning_summary_text.delta` (via `sse_event` wrapper). Done by `_finish_thought` on `response.reasoning_text.done` / `response.reasoning_summary_text.done`.

**Answer/final text panel**: populated by `_apply_answer_delta` on `response.output_text.delta`. Done by `_finish_answer` on `response.output_text.done`.

**Tool lifecycle display**: `tool_call_started`, `tool_call_completed` → activity rows (`"tool  start {name}"`, `"tool  done {name}"`). `tool_call_failed` → `"tool  failed {name}"`. `private_tool_call_aborted` → fallback row.

**Web search display**: `web_search_route` → activity row with query, profile, results, fallback info. `web_search_budget_exceeded` → not explicitly handled (falls to generic `_append_activity`).

**Coercion/advice visibility**: **NONE**. No `coercion_succeeded`, `coercion_failed`, or `tool_schema_replaced` telemetry events exist yet (B2 gap). These are invisible.

**Schema replacement visibility**: **NONE**. `ToolRequestNormalizationReport.replaced` is captures-only.

**Stream failure visibility**: `request_failed` → backend panel `("error", "{method} {path} {error}")`. `stream_failed` → same path. `stream_terminal_classified` → backend panel if classification is not `"ok"`.

**Backend/runtime failure visibility**: From `prompt_contract` event → `control_plane_rows` in backend panel. From `monitor_connection` events.

**Usage/token display**: **NONE**. grep confirms zero usage/token rendering in qz-thoughts. `response.completed` is visible via `sse_event` telemetry (model/status), but the `usage` subfield inside the compacted response is not rendered anywhere in qz-thoughts' UI.

**Model/status display**: `prompt_contract` event → backend panel with profile, model, reasoning level, context. `response.created` → `state.model` / `state.response_id` updated.

---

### qz-top data sources

| source | refresh | purpose |
|---|---|---|
| `/qz/control-plane` | Every `interval` (default 1s) | Primary model/backend status: selected_model_ready, request_admission_state, runtime_failure_*, failed_candidate_*, backend phase/health |
| `/qz/status` (legacy) | Fallback if control-plane unavailable or wrong schema | Legacy model/backend status — exposes additional fields not yet in control-plane |
| `/qz/telemetry/recent?limit=N` | Every interval | Token rates, request counts, recent activity list |
| `nvidia-smi` (subprocess) | Every interval | GPU util, memory, power, temp |
| `docker stats` / `docker inspect` (subprocess) | Every interval | Container memory, CPU if enabled |
| `QZ_DOCKER_CMD` env | — | Controls docker command (supports `sudo docker`) |
| `var/benchmark-latest.json` | Every interval | Compaction benchmark summary if present |
| VRAM snapshot | From `/qz/control-plane.vram_snapshot` | Process VRAM breakdown |

**Refresh interval**: 1.0s default; `--interval` flag. All sources polled on every tick.

**Timeout**: `monitor_http_timeout()` from `QZ_MONITOR_HTTP_TIMEOUT` env (default 2.0s). `nvidia-smi` timeout 1.0s. docker timeout 3.0s. All exceptions silently swallowed — stale data shown.

**Stale/offline handling**: On any exception, `fetch_json` / `run_cmd` return `None`/`""`. `model_status_from_control_plane(None)` returns `ModelStatus()` (all-empty). qz-top draws with empty state — no explicit "data unavailable" marker in the UI except the health indicators (●/○ for backend and proxy).

**Key limitation**: When `/qz/control-plane` returns a valid payload but does NOT include `prompt_files`, `reasoning_level`, `reasoning_policy`, `sampling`, `selected_context_length`, `backend_context_length` — these are explicitly documented as `None` in `model_status_from_control_plane` with comments "not exposed by /qz/control-plane yet". When control-plane is the active source, these fields show empty/default in qz-top's PROFILE panel.

---

## 2. qz-thoughts Visibility Table

| signal/event | source | currently visible? | panel/location | stale/offline behaviour | missing metadata | misleading risk | related finding | proposed test | fix pass |
|---|---|---|---|---|---|---|---|---|---|
| `response.output_text.delta` | `sse_event` telemetry | **yes** | ANSWER panel (delta chars) | last known answer frozen | none | none | Slice A correction | test answer panel updates | none |
| `response.output_text.done` | `sse_event` | **yes** | ANSWER panel (done state) | frozen | none | none | Slice A | test answer done state | none |
| `response.reasoning_text.delta` | `sse_event` | **yes** (raw mode) | THOUGHT panel | frozen | none | not shown in hidden mode (correct) | Slice A correction | test thought panel | none |
| `response.reasoning_summary_text.delta` | `sse_event` | **yes** (summary mode) | THOUGHT panel | frozen | none | none | Slice A | test summary thought | none |
| `response.completed` | `sse_event` (compacted) | **partial** — model/status visible; **usage NOT rendered** | activity "completed" row | frozen | usage, token counts, response.id | usage invisible | Slice D P2 | test usage not rendered in qz-thoughts | P2 fix |
| `response.failed` | `sse_event` / `request_failed` | **yes** | backend "error" row | frozen | error.code | none | — | test failed renders | none |
| `response.web_search_call.*` | (not a telemetry event; lifecycle from `tool_call_started`) | **partial** — tool started/completed shown; lifecycle stages not shown separately | activity "tool" row | frozen | lifecycle stages | no per-stage lifecycle visibility | — | — | none |
| `tool_call_started` | telemetry | **yes** | activity row | frozen | none | none | — | existing test | none |
| `tool_call_completed` | telemetry | **yes** | activity row | frozen | none | none | — | existing test | none |
| `web_search_route` | telemetry | **yes** | activity row with full details | frozen | none | none | — | existing test | none |
| `tool_schema_replaced` | **does not exist** | **no** | — | — | entire event missing | operator cannot see schema replacement | Slice B B2 | add after B2 emits event | B2 |
| `coercion_success` / `coercion_failed` | **does not exist** | **no** | — | — | entire event missing | operator cannot see coercion | Slice B B2 | add after B2 emits event | B2 |
| `dropped_tool_feedback` | indirect via `tool_call_error` | **partial** — error shows tool name | activity "tool" row | frozen | specific drop reason | could be confused with a tool execution error | Slice B | test `tool_call_error` renders | none |
| `unknown_tool_feedback` | indirect via `tool_call_error` | **partial** | same | frozen | same | same | Slice B | same | none |
| `repeated_read_signal` | telemetry | **partial** — seen in backend panel via `repeated_read_signal` event but limited rendering | depends on implementation | frozen | path, action | none | — | — | none |
| `stream_failed` | via `request_failed` | **yes** | backend error row | frozen | phase, error | none | — | existing test | none |
| `backend_runtime_failure` | indirect via `request_failed` or `stream_terminal_classified` | **partial** — stream terminal classification visible | backend row | frozen | `runtime_failure_error_type`, `backend_died_after_healthy` not surfaced | operator doesn't know if backend died vs. stream timed out | Slice D P2 | test runtime failure classification | P2 |
| `request_started` | telemetry | **yes** | activity row (clears state) | frozen | none | none | — | existing test | none |
| `request_completed` | telemetry | **yes** | backend row (duration, model) | frozen | usage details not rendered | usage/tokens invisible after completion | Slice D P2 | test request_completed render | P2 |
| usage | via `sse_event` response.completed OR `request_completed` | **no** — usage not rendered anywhere | not shown | — | ALL usage fields | operator cannot see token consumption | Slice D P2 | test that usage field is absent | P2 fix |
| cached_tokens | via normalized usage | **no** | not shown | — | all | invisible | Slice D P2 | — | P2 fix |
| reasoning_tokens | via normalized usage | **no** | not shown | — | all | invisible | Slice D P2 | — | P2 fix |
| `response.id` | via `sse_event` response.created (sets `state.response_id`) | **yes** (stored) but NOT RENDERED | state field only; not shown in UI | stale after hop continuation | mismatch across hops | `state.response_id` is set from first response.created but synthesised terminals have different IDs | Slice D P1 | test state.response_id set from response.created | none |
| `request_id` | via `request_started` / `stream_event_timing` | **yes** (in some activity rows) | activity rows | frozen | none | none | — | — | none |
| model | via `sse_event` response.created + prompt_contract | **yes** | backend model row | stale until next request | none | none | — | existing test | none |
| `selected_model_ready` | via `prompt_contract` / control_plane | **partial** — backend panel shows ready status | frozen | none | none | — | — | — | none |
| `request_admission_state` | via control_plane_rows in backend panel | **partial** | backend panel | frozen | none | none | — | — | none |

---

## 3. qz-top Visibility Table

| runtime field | source endpoint/tool | currently visible? | update cadence | timeout/staleness | can be misleading? | missing metadata | proposed test | fix pass |
|---|---|---|---|---|---|---|---|---|
| selected model | `/qz/control-plane` | **yes** | 1s | 2s timeout; empty on failure | no | none | test renders selected model | none |
| loaded/effective model | `/qz/control-plane` (backend.loaded_model) | **yes** | 1s | shows empty if backend down | can show `none` when backend is starting | none | — | none |
| `selected_model_ready` | `/qz/control-plane` via `model_status_from_control_plane` | **yes** | 1s | shows False on timeout | no | none | existing test confirms `ready=` field | none |
| `request_admission_state` | `/qz/control-plane` | **yes** | 1s | shows empty on timeout | no | none | existing test confirms `admission=` field | none |
| backend phase | `/qz/control-plane.backend.phase` | **yes** | 1s | shows empty | none | none | — | none |
| backend health | `curl_health(backend_host)` | **yes** (●/○) | 1s | 2s timeout; ○ on timeout | proxy might be healthy while backend is restarting | none | — | none |
| GPU offload state | `/qz/control-plane.backend.gpu_offload_state` | **yes** | 1s | stale on timeout | none | none | — | none |
| VRAM/process state | `/qz/control-plane.vram_snapshot` | **yes** | 1s | stale on timeout; no explicit stale marker | VRAM may be from last healthy probe | none | — | none |
| `model_switch_state` | `/qz/control-plane` | **yes** | 1s | stale | none | none | — | none |
| `last_good_key` | `/qz/control-plane.models.last_good_key` | **yes** | 1s | stale | none | none | — | none |
| `failed_candidate_key` | `/qz/control-plane.models.failed_candidate_key` | **yes** | 1s | stale | none | none | — | none |
| `last_load_error_type` | `/qz/control-plane.backend.load_error_type` | **yes** | 1s | stale | none | none | — | none |
| `runtime_failure_error_type` | `/qz/control-plane.{models,backend}.runtime_failure_error_type` | **yes** (shown as "DEATH") | 1s | stale | none | none | existing test | none |
| `backend_died_after_healthy` | `/qz/control-plane.{models,backend}.backend_died_after_healthy` | **yes** (triggers DEATH label) | 1s | stale | none | none | existing test | none |
| active request count | `/qz/telemetry/recent.active_requests` or VRAM fields | **partial** | 1s | stale | none | no per-request detail | — | none |
| active stream/tool state | not surfaced | **no** | — | — | no | no live tool-call indicator | add from `tool_call_started` telemetry | P2 |
| web_search profile/budget state | not surfaced | **no** | — | — | no | none | — | P2 |
| coercion/advice state | not surfaced | **no** | — | — | no | no telemetry events yet | B2 | B2 |
| `response.id` | not tracked | **no** | — | — | no | none | — | none |
| `request_id` | not tracked | **no** | — | — | no | none | — | none |
| usage (total tokens) | `/qz/telemetry/recent` via `request_completed.usage.total_tokens` | **yes** (rates panel) | 1s | stale | none | none | — | none |
| `cached_tokens` | not surfaced | **no** | — | — | no | none | add to rates panel | P2 |
| `reasoning_tokens` | not surfaced | **no** | — | — | no | none | add to rates panel | P2 |
| prompt_files | NOT in `/qz/control-plane` | **no** (control-plane path) | — | — | **yes** — shows "default" when control-plane active, actual file when /qz/status active | `model_status_from_control_plane` explicitly notes "not exposed by /qz/control-plane yet" | add to control-plane | P2 |
| reasoning_level | NOT in `/qz/control-plane` | **no** (control-plane path) | — | — | same | same | add to control-plane | P2 |
| sampling params | NOT in `/qz/control-plane` | **no** (control-plane path) | — | — | same | same | — | P2 |
| context lengths | NOT in `/qz/control-plane` | **no** (control-plane path) | — | — | **yes** — shows `ctx=0` in PROFILE | same | add to control-plane | P2 |

---

## 4. Reconnect/Staleness Audit

### qz-thoughts reconnect

**When proxy is stopped**:
- SSE connection breaks; `_iter_sse_events` catches exception.
- Yields `{"type": "monitor_connection", "payload": {"status": "unavailable"}}`.
- `_apply_telemetry_event`: `state.status` tracking unclear from code (backend panel shows unavailable message).
- Waits 1 second, retries. Alternates between `/qz/telemetry/stream` and `/qz/telemetry/events`.

**When proxy restarts**:
- New connection succeeds; yields `{"status": "reconnected"}`.
- `_apply_telemetry_event` for "reconnected": sets `state.last_seq = 0`, appends `("proxy", "reconnected")` to backend.
- Fresh events with seq >= 1 are accepted.
- Prior thought/answer state is preserved (not cleared) — old thought content remains until a new `request_started` clears it.
- **Confirmed by test**: `test_reconnect_resets_stale_telemetry_sequence_floor`.

**Sequence reset**:
- Reconnect resets `last_seq = 0`, not negative. New events with seq=1 are accepted.
- Old events (seq <= old_last_seq) from a reconnected stream would be accepted if they happen to have seq >= 1.
- **Gap**: if proxy restarts with seq=1 and the reconnect fires BEFORE the first new event, old events from before the restart are correctly filtered (seq <= 0 old_last_seq would have been > 0, but reset to 0). Wait — actually the reset sets `last_seq = 0`, so events with seq = 1 (new events) are accepted. This is correct.
- **Confirmed by test**: `test_reconnect_resets_stale_telemetry_sequence_floor` confirms last_seq = 1 after reconnect and event seq=1.

**Old events preserved?**: Yes — thought/answer panels retain last content until a new `request_started` event clears them.

**UI disconnect indicator**: backend panel shows `("proxy", "unavailable")` or `("proxy", "reconnected")`. No explicit "DISCONNECTED" overlay.

**Recovers without restarting**: Yes — automatic reconnect loop with 1s delay. **Confirmed by test**.

### qz-top reconnect/staleness

**When proxy is stopped**:
- `fetch_json("/qz/control-plane")` returns None after 2s timeout.
- `model_status = ModelStatus()` (all-empty).
- qz-top shows `loaded: none`, `state: unknown` in PROFILE.
- Health indicator shows `○` for proxy.
- **Misleading**: a stopped proxy looks identical to a proxy that has no model loaded. No explicit "PROXY OFFLINE" message in the model panel.

**When backend is stopped** (proxy running):
- `/qz/control-plane` returns with `backend.reachable = false`.
- Health check `curl_health(backend_host)` → `○`.
- PROFILE panel shows last known selected model but loaded=none.
- `backend_phase` shows empty or last known value.

**Docker helper denied**:
- `run_cmd` catches `subprocess.CalledProcessError` or any exception.
- Returns `""`.
- `parse_gpus()` returns empty GPU list.
- GPU panel shows no GPU rows — not explicitly marked as unavailable.
- **Misleading**: GPU panel absence is ambiguous — could be no GPUs, docker permissions issue, or nvidia-smi missing.

**nvidia-smi slow/unavailable**:
- 1.0s timeout in `curl_health`.
- `run_cmd` returns `""` on timeout.
- Same as docker: empty GPU panel.
- qz-top does NOT block the hot loop — all slow sources time out with bounded timeouts.

**Stale data marking**: No explicit staleness markers in qz-top UI. A panel showing last-known values is indistinguishable from current values. The `refresh=Xs` label shows the refresh interval but not whether the last refresh succeeded.

**"Unknown" vs "not loaded"**: `model_status_from_control_plane(None)` returns `ModelStatus()` with `loaded=""`. qz-top renders this as `loaded: none`. A model that is actively loading also shows `loaded: none`. **Cannot be distinguished** from the UI alone without seeing `backend_phase`.

---

## 5. Misleading-State Audit

### MS1 — Proxy offline looks like "no model loaded"

When proxy is unreachable: `ModelStatus()` → `loaded: none, state: unknown`. No OFFLINE indicator in the PROFILE panel. Operator may think no model is selected/loaded rather than the proxy being down. Health indicators (●/○) are only in the header row.

### MS2 — Backend died during generation shown as "not loaded"

If backend dies mid-generation and a subsequent refresh shows the backend as unhealthy, `backend_died_after_healthy = true` and `runtime_failure_error_type` are set. qz-top shows "DEATH" in the MODEL panel. **This is correct** — the DEATH label was added specifically for this case. No misleading behaviour here.

### MS3 — Selected model differs from env QZ_MODEL_KEY

`/qz/control-plane.models.configured_env_model` vs `models.selected_key`. If they differ, control-plane emits an operator hint. qz-top's PROFILE panel shows the selected_key, not configured_env_model. The drift is visible only through the service_status operator_hint row, not prominently.

### MS4 — Reasoning/prompt fields missing from control-plane

When `/qz/control-plane` is the active source (normal case), `prompt_files`, `reasoning_level`, `reasoning_policy`, `sampling`, `selected_context_length`, and `backend_context_length` are all absent. qz-top shows:
- `prompt: default` even when a custom prompt file is active.
- `reasoning: ?` even when reasoning is configured.
- `ctx=0` even when context length is set.

The legacy `/qz/status` fallback has these fields. But if control-plane is healthy, the fallback is never used. **This is a concrete misleading state** — the operator sees stale/empty config in qz-top while a non-default prompt is active.

### MS5 — qz-thoughts shows tool lifecycle but not coercion

The tool activity panel shows `tool start/done` for web_search. But if coercion ran (e.g., apply_patch malformed args were silently corrected), nothing is shown. Operator sees a successful tool call but doesn't know coercion was needed. After B2 fixes, this will be visible.

### MS6 — qz-thoughts shows final text but no usage

The answer panel shows the full response text and "done N chars" but shows no token counts. Operator cannot tell if a long response consumed an unusual number of tokens.

### MS7 — qz-thoughts shows response.id in state but not in UI

`state.response_id` is populated from `response.created` but never rendered in the qz-thoughts output panels. The response.id mismatch from multi-hop streaming (Slice D P1) is therefore also invisible.

### MS8 — GPU panel absence is ambiguous

Empty GPU panel in qz-top could mean: no GPUs, nvidia-smi unavailable, docker permissions denied, or all GPU rows have zero util. No explicit marker distinguishes these.

### MS9 — VRAM panel has no staleness marker

VRAM snapshot from `/qz/control-plane` has no timestamp. If the control-plane cached a VRAM snapshot from before a model swap, qz-top shows stale VRAM allocation without any indication it may be old.

### MS10 — web_search capabilities/profile info absent from both UIs

Neither qz-thoughts nor qz-top shows which search profile was requested/selected, what budget mode is active, or whether retrieval is available. `web_search_route` telemetry in qz-thoughts activity shows this for completed searches, but there is no persistent config panel. The capabilities endpoint (`GET /qz/web-search/capabilities`) is not polled by either UI.

---

## 6. Token/Usage Observability Audit

### Current usage display

**qz-thoughts**: Usage fields are parsed from `request_completed` telemetry (in `_apply_telemetry_event`) but are NOT rendered anywhere. The `_apply_response_event` handler for `response.completed` only updates `state.model` and `state.response_id` — not usage. **Zero usage visibility.**

**qz-top RATES panel**: `merge_telemetry(rates, telemetry_recent())` processes `request_completed` events. It reads `usage.input_tokens`, `usage.output_tokens`, `usage.total_tokens` to compute token-per-second rates. Token rates ARE displayed. But `usage.input_tokens_details.cached_tokens` and `usage.output_tokens_details.reasoning_tokens` are **not read or displayed**.

### Whether qz-thoughts can parse usage from response.completed sse_event

The `sse_event` telemetry wrapper for `response.completed` is produced by `_telemetry_sse_payload`, which compacts the response to `{id, model, status, created_at, usage}`. The `usage` subdict IS included in the compact payload and would be accessible as `sse_payload.get("payload", {}).get("response", {}).get("usage")`. But `_apply_response_event` for `response.completed` does not read it. It only reads `response.status` and `response.model`.

### Whether qz-top receives usage from telemetry

Yes — via `request_completed` telemetry event. `merge_telemetry` processes this. `input_tokens` and `output_tokens` are used for rate calculation. `total_tokens` is used if present.

### Token rates calculated

qz-top calculates:
- `prompt_rate` (tokens/s for input processing)
- `gen_rate` (tokens/s for generation)
- `total_rate` (tokens/s overall)
- Latest `prompt_tokens`, `gen_tokens`, `total_tokens` per request

### cached_tokens/reasoning_tokens discarded

In `merge_telemetry` (qz-top, around line 927-956), the code reads `usage.input_tokens` and `usage.output_tokens`. `usage.input_tokens_details` and `usage.output_tokens_details` are never read. Confirmed by grep: no reference to `cached_tokens` or `reasoning_tokens` in the rates computation.

### How to display them without clutter

Reasonable approach: append to the RATES panel bottom row as small suffixes:
- `cached=Nt (X%)` alongside the prompt token count.
- `reason=Nt` alongside gen token count.
Only show when non-zero. These are already in the normalized usage dict — no new data sources needed.

---

## 7. Runtime Failure Observability Audit

### Model load failure

- **qz-top**: `last_load_error_type` and `last_load_error` visible in PROFILE panel via `model_status_from_control_plane.last_load_error_type/error`. `failed_candidate_key` shown.
- **qz-thoughts**: via `prompt_contract` backend panel — fields may show but not prominently.
- **Source**: `/qz/control-plane.backend.{load_error, load_error_type}` + `/qz/model/status`.
- **Missing**: no advisory text explaining what `insufficient_vram` or `context_creation_failed` means in qz-thoughts.

### Too-large model failure (insufficient_vram)

- **qz-top**: `last_load_error_type = "insufficient_vram"` shown. `recommended_recovery_action` from `/qz/control-plane.service_status` shown via `service_recovery_from_control_plane` → recovery label in qz-top.
- **qz-thoughts**: not specifically rendered.
- **Source**: `/qz/control-plane`.

### Rollback to last_good

- **qz-top**: `rollback_performed`, `last_good_key`, `failed_candidate_key` visible. PROFILE panel shows the rollback state.
- **qz-thoughts**: not rendered.
- **Missing**: no explicit "ROLLED BACK" marker in qz-thoughts.

### Backend death after healthy (CUDA/OOM/kill)

- **qz-top**: `backend_died_after_healthy = True` → renders "DEATH" in red bold in MODEL panel. `runtime_failure_error_type` shown as `runtime={type}`.
- **qz-thoughts**: not explicitly rendered. `stream_terminal_classified` may appear if a stream was active.
- **Source**: `/qz/control-plane.{models,backend}.backend_died_after_healthy`.
- **Confirmed by test**: `test_qz_top_renders_runtime_failure_as_runtime_death`.

### stream_failed during active request

- **qz-thoughts**: `request_failed` event → backend error row `"error POST /v1/responses ... upstream boom"`.
- **qz-top**: telemetry `request_failed` is processed in `merge_telemetry` → recent activity list.
- **Missing**: no distinction between "stream failed because backend died" vs "stream failed because of network error".

### CUDA/VRAM failure classification

Backend log classifier (`qz_model_load_failure.py`) classifies container log lines into `insufficient_vram`, `context_creation_failed`, `unknown`. These surface through `/qz/model/status.last_load_error_type`. Runtime CUDA failures during generation (not load) are classified via `backend_died_after_healthy` + `runtime_failure_error_type`. Both are visible in qz-top.

### Request rejected: selected model not ready

- **qz-top**: `selected_model_ready = False` → `ready=false` in MODEL panel. `request_admission_state` shown.
- **qz-thoughts**: request_started fires, then request_failed fires if the proxy rejects. `responses_rejected_model_missing` telemetry → not specifically rendered in qz-thoughts (goes to generic `_append_activity`).
- **Missing**: qz-thoughts doesn't distinguish "request rejected because model not ready" from "upstream failed".

### Backend unavailable

- **qz-top**: ○ indicator; empty model state.
- **qz-thoughts**: backend panel shows `("proxy", "unavailable")` if proxy itself is down. If only backend is down (proxy up), no specific visibility.

---

## 8. Test Coverage Audit

### Existing tests

| test | covers |
|---|---|
| `test_once_file_coalesces_delta_activity` | qz-thoughts: output_text.delta, reasoning_summary_text.delta; thought/answer panels; dedup |
| `test_once_renders_new_stream_lifecycle_and_capture_mode` | qz-thoughts: web_search, tool lifecycle, stream lifecycle, repair, abort, request_failed |
| `test_stream_terminal_classified_ok_is_not_rendered` | qz-thoughts: `stream_terminal_classified` with ok not shown |
| `test_reconnect_resets_stale_telemetry_sequence_floor` | qz-thoughts: reconnect; last_seq reset |
| `test_idle_stream_reconnect_does_not_mark_proxy_unavailable_or_reset_sequence` | qz-thoughts: idle reconnect preserves last_seq |
| `test_tool_escalation_requested_renders_as_escalation_label` | qz-thoughts: tool escalation |
| `test_tool_sandbox_denied_renders_as_denied` | qz-thoughts: sandbox denied |
| `test_tool_connection_failed_renders_as_conn_fail` | qz-thoughts: connection failed |
| `test_qz_top_renders_direct_ready_and_admission_state` | qz-top: `selected_model_ready`, `request_admission_state` |
| `test_qz_top_renders_runtime_failure_as_runtime_death` | qz-top: `runtime_failure_error_type`, `backend_died_after_healthy`, "DEATH" |
| `test_qz_top_no_longer_renders_backend_mode_as_loaded_state` | qz-top: backend mode not shown |

### Missing tests (precise list)

1. **`test_qz_thoughts_usage_not_displayed`** — confirm that even when response.completed sse_event has usage, qz-thoughts renders nothing in the usage/token category. Documents current P2 gap.

2. **`test_qz_thoughts_response_id_stored_not_rendered`** — confirm that `state.response_id` is set from response.created but not visible in output. Documents state/display split.

3. **`test_qz_thoughts_reasoning_text_raw_mode_visible`** — confirm that `response.reasoning_text.delta` (not summary) updates thought panel. Current test uses summary mode only.

4. **`test_qz_thoughts_coercion_event_absent`** — confirm that a `coercion_succeeded` event (once emitted by B2) is NOT yet rendered. Baseline test before B2 rendering is added.

5. **`test_qz_thoughts_tool_schema_replaced_absent`** — same pattern for `tool_schema_replaced`.

6. **`test_qz_top_cached_tokens_not_in_rates`** — confirm that `cached_tokens` in `request_completed.usage` is not shown in rates. Documents P2 gap.

7. **`test_qz_top_control_plane_missing_prompt_files`** — confirm that `prompt_files` is empty/`"default"` when control-plane is the source. Documents MS4.

8. **`test_qz_top_proxy_offline_looks_like_no_model`** — confirm that `ModelStatus()` from a None control-plane has `loaded=""`, `selected_model_ready=False`. Documents MS1 gap.

9. **`test_qz_top_cached_tokens_from_rates`** — test that if `cached_tokens` is added to rates display (P2 fix), it shows correctly. (Write after P2 implementation.)

10. **`test_qz_thoughts_web_search_budget_exceeded_renders`** — confirm whether `web_search_budget_exceeded` event (which exists but isn't explicitly tested for qz-thoughts rendering) produces a visible activity row.

---

## 9. Findings Summary

### P0 — Protocol/tool state corruption

None identified. qz-thoughts and qz-top are read-only displays. They do not modify proxy state. No P0 risks.

### P1 — Hides active failure or gives dangerous wrong operator action

1. **MS1 — Proxy offline indistinguishable from no model loaded**: When `/qz/control-plane` times out, qz-top shows `loaded: none, state: unknown` — same as if a model is not yet loaded. Operator might try to load a model when the proxy is actually just offline. **Mitigation**: ○ health indicator in header. Not a complete fix.

2. **qz-thoughts cannot distinguish "rejected because not ready" from "upstream failed"**: Both produce `request_failed` telemetry. Operator cannot tell whether to wait for the model to load vs. restart the stream.

### P2 — Useful observability missing

3. **Usage/token counts not visible in qz-thoughts** — operator cannot see token consumption per request.
4. **cached_tokens and reasoning_tokens not in qz-top rates** — cache effectiveness invisible.
5. **Coercion/schema replacement invisible in both UIs** (B2 gap — no telemetry events yet).
6. **Prompt files / reasoning_level / context lengths missing from qz-top when control-plane is active** (MS4 — fields not in control-plane).
7. **Active tool call state not live in qz-top** — no indicator that a web_search is running right now.
8. **web_search profile/budget state not in either UI** — operator cannot see what profile is configured.
9. **backend_died_after_healthy not shown in qz-thoughts** — only qz-top has DEATH label.

### P3 — Documentation/test gaps

10. `response.id` stored in qz-thoughts state but never rendered.
11. Missing tests for absence of usage in qz-thoughts and cached/reasoning in qz-top.
12. Missing test confirming raw-mode reasoning_text.delta updates thought panel.
13. `web_search_budget_exceeded` rendering coverage absent.
14. VRAM panel staleness not marked.

### Uncertain areas needing live capture

- Whether `web_search_budget_exceeded` events from the proxy appear in qz-thoughts activity. Grep shows no specific handler but may fall through to generic `_append_activity("event", ev_type)`.
- Whether `control_plane_rows` in qz-thoughts backend panel correctly shows runtime failure fields when a backend death has occurred during a live capture.
- Whether the sequence reset on reconnect correctly handles the case where proxy seq starts at a value > 1 after restart (e.g., if telemetry events were emitted before the SSE client connected).

### Proposed fix-pass order

1. **B2** (already planned): coercion/schema telemetry. After B2, qz-thoughts will show coercion events automatically.
2. **P2a — add usage display to qz-thoughts**: Add a usage row to qz-thoughts showing `in={N} out={N}` from request_completed. Small, safe.
3. **P2b — add cached/reasoning tokens to qz-top rates**: Append `cached=N` and `reason=N` to existing rates panel when non-zero.
4. **P2c — add missing fields to /qz/control-plane**: `prompt_files`, `reasoning_level`, `reasoning_policy`, `sampling`, `selected_context_length`, `backend_context_length`. This fixes MS4 in qz-top.
5. **P1a (from Slice D) — response.id threading**: Fix synthesised response.id in multi-hop terminals.
6. **C2 (from Slice C) — output_text artifact detection**: Add tool artifact detection in output_text channel.
7. **E1 — distinguish proxy-offline from no-model in qz-top**: Add explicit "PROXY OFFLINE" message in PROFILE panel when control-plane is unavailable.

### Recommended next audit slice

**Slice F — fix-pass B2**: Implement the concrete fixes from the audit series (coercion telemetry, schema replacement telemetry, ToolCoercionResult guard, non-streaming dropped-tool gap). These are the smallest safe changes that unlock the most observability gaps and complete the P2 category.
