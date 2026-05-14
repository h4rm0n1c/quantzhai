# Backend Service Recovery Semantics

Date: 2026-05-15
Status: Slice 1 of #47 — taxonomy and documentation pass only.

---

## Purpose

This document starts #47: Normalize backend service status and recovery semantics.

It builds on #44 (backend control-plane and data-flow audit), which established:
- `GET /qz/control-plane` as the live status authority
- `qz.responses.error.v1` structured error payloads
- Shared readiness helper (`scripts/qz-wait-ready`)
- Consumer migration (qz-doctor, qz-top, qz-thoughts, qz-codex-common, smoke scripts)

After #44, the system can **report** backend trouble much better than before.
But it is not yet a full service manager:

```text
Status visibility:       B+   — /qz/control-plane, structured errors, telemetry
Error payloads:          B    — qz.responses.error.v1 with readiness and hints
Graceful degradation:    B    — proxy usable when backend is down
Graceful recovery:       C+   — load/unload/restart paths exist but not hardened
Service supervision:     C-   — qz-up wraps Docker; no crash-loop detection
Canonical status codes:  C+   — strings scattered; no shared enum
```

This is **not** an auto-restart implementation. Automatic backend recovery requires
explicit design and operator authority decisions that are out of scope for slice 1.

---

## Current status surfaces

| Surface | Path/Function | What it returns |
|---|---|---|
| Control-plane summary | `GET /qz/control-plane` | `qz.control_plane.status.v1` — readiness, models, backend, codex catalog, hints |
| Proxy status snapshot | `GET /qz/status` | Full status snapshot including selected/backend/prompt/load state |
| Telemetry events | `GET /qz/telemetry/recent?limit=N` | Recent lifecycle events (tool, repair, stream, model load, etc.) |
| Telemetry state | `GET /qz/telemetry/state` | Telemetry bus state summary |
| Catalog refresh | `POST /qz/models/refresh` | `qz.codex.catalog.refresh.v1` — ok, catalog_updated, model_ids |
| Model select/load | `POST /qz/models/load` or `/qz/models/select` | Selected entry or 503/404 error |
| Proxy health | `GET /health` | HTTP up + `proxy_initialization` fields |
| Responses | `POST /v1/responses` | Result or `qz.responses.error.v1` |
| Proxy config | `GET /qz/config/effective` | Active config paths, prompt files, memory domains |
| Startup launcher | `scripts/qz-up` | Docker + proxy start, catalog wait, backend /health wait |
| Proxy restart | `scripts/qz-proxy` | Proxy-only restart with HTTP readiness check |
| Model router state | `qz_model_router.ModelRouter` | In-memory: model_load_state, model_load_error, load_started_at |
| Backend client | `qz_backend.DockerLlamaCppBackend` | `get_health()`, `get_models()`, `load_model()`, `restart_container()` |

---

## Current status strings — source audit

### `_overall_status()` in `qz_control_plane.py`

Derives the single `status` field in `/qz/control-plane`:

| String | Condition |
|---|---|
| `"initializing"` | `proxy_ready == false` OR `catalog_ready == false` |
| `"backend_unavailable"` | proxy+catalog ready, but `backend_reachable == false` |
| `"model_not_loaded"` | backend reachable, but `backend_ready == false` |
| `"ready"` | all readiness flags true |

### Model load state (`model_load_state` class variable, `qz_model_router.py`)

Managed by `ModelRouter.load_backend_model()` and `unload_backend_model()`:

| String | Meaning |
|---|---|
| `None` / `""` / `"idle"` | No active load operation (treated as backend_state) |
| `"loading"` | Load request in flight to llama.cpp |
| `"ready"` | Load completed successfully |
| `"failed"` | Load HTTP request returned ≥ 400 or timed out |
| `"unloading"` | Unload request in flight |

### Backend inventory state (from llama.cpp `/v1/models`, `qz_model_router.py`)

Populated by `backend_models()` from the llama.cpp models inventory:

| String | Source |
|---|---|
| `"loaded"` | `status.value == "loaded"` from backend inventory |
| `"loading"` | `status.value == "loading"` from backend inventory |
| `"unloaded"` | `status.value == "unloaded"` or absent |
| `"unknown"` | status field missing or not parseable |

### Backend context state (`backend_context_length_fact()`)

| String | Meaning |
|---|---|
| `"confirmed"` | Live backend inventory confirmed context length |
| `"cached"` | Read from `var/backend-state.json` (stale possible) |
| `"default"` | From env `QZ_CONTEXT` or built-in default |

### `restart_required_state`

| String | Meaning |
|---|---|
| `"confirmed"` | Backend inventory confirmed; context mismatch is real |
| `"pending"` | Backend state not confirmed; mismatch possible but unverified |

### Status snapshot `status` field (`status_snapshot()`)

| String | Condition |
|---|---|
| `"ok"` | `health_status == 200` and backend model state == `"loaded"` |
| `"loading"` | otherwise (fallback) |

### Initialization state (`_initialization_payload()` in `quantzhai_proxy.py`)

| String | Meaning |
|---|---|
| `"starting"` | Background init thread not yet finished |
| `"ready"` | Init completed successfully |
| `"failed"` | Init raised an exception |

---

## Current HTTP behaviour map

Sourced from `qz_request_router.py` and `qz_responses_error.py`.

### `GET /qz/control-plane` and `/qz/control-plane/status`

- Always returns HTTP 200 with JSON, even when backend is down.
- `status` field derives from `_overall_status()` (see above).
- Never returns 4xx/5xx. Degraded states are in the body.

### `GET /health`

- Always returns HTTP 200.
- `proxy_initialization.ready` and `catalog_ready` may be false during startup.

### `GET /qz/status`

- Always returns HTTP 200.
- `ready == false` when backend model is not loaded.

### `GET /v1/models`

- Always returns HTTP 200.
- `data: []` when catalog is not ready or no GGUFs scanned.

### `POST /qz/models/refresh`

- `503` with `qz.codex.catalog.refresh.v1` if proxy initialization not ready.
- `200` with `qz.codex.catalog.refresh.v1` on success.
  - **Callers must check `catalog_updated == true`** — HTTP 200 + `ok: true` + `catalog_updated: false` is a partial failure (Codex catalog file not regenerated).

### `POST /qz/models/load` and `/qz/models/select`

- `503` (proxy not ready) with `_proxy_initializing_error_payload()`
- `400` if request body is not valid JSON
- `404` with `{"error": "no model selected", "reason": "..."}` if model not found
- `503` with `{"error": "...", "reason": "..."}` (compact) if profile backend missing
- `200` with selected entry on success

### `POST /v1/responses` — key response codes

| Condition | HTTP | Schema |
|---|---|---|
| Invalid request JSON | 400 | `{"error": "invalid JSON: ..."}` (plain) |
| Proxy not ready | 503 | `qz.responses.error.v1` with `readiness.*` and `proxy_initialization` |
| Model not found | 503 | `qz.responses.error.v1` with `requested_model`, `available_models`, alias hint |
| Backend unreachable | 502 | `qz.responses.error.v1` with `backend_ready: false`, operator hints |
| Profile backend missing (invalid symlink) | 503 | dict from `profile_backend_error_payload()` — not yet using `qz.responses.error.v1` schema |
| Upstream returns 5xx | 502 | `qz.responses.error.v1` with `error: "backend unavailable"` |
| Local compaction triggered | 200 | Compaction response body |
| Success (stream) | 200 | SSE stream |
| Success (non-stream) | 200 | JSON response body |

**Note:** The profile-backend-missing path (invalid symlink) currently returns a compact dict without the `qz.responses.error.v1` schema. This is a gap (see below).

---

## Proposed canonical status taxonomy

These are proposed target enums for #47. They do not exist as code yet.

### `proxy_state`

```text
starting      — initialization thread running, not yet ready
initializing  — catalog scan or search policy load in progress
ready         — proxy fully initialized, catalog loaded
degraded      — proxy ready but some catalog or search policy load failed
failed        — initialization raised an exception
```

### `catalog_state`

```text
unknown       — not yet checked or information not available
loading       — scan in progress
ready         — models scanned, Codex catalog generated
failed        — scan or catalog generation raised an exception
```

### `backend_state`

```text
unknown       — not checked or no inventory data
unreachable   — backend /health or inventory calls failed
starting      — container started, /health not yet responding
healthy       — backend /health returned 200 (process up, no model loaded required)
unhealthy     — backend /health returned non-200
restarting    — restart_container() in progress
failed        — load/unload/restart raised an exception or timed out
```

### `model_state`

```text
none          — no model selected or configured
unknown       — selected but inventory not confirmed
unloaded      — inventory confirms model is not loaded
loading       — load request in flight
loaded        — inventory confirms model is loaded
failed        — load raised exception or returned HTTP ≥ 400
mismatch      — loaded model != selected model (context or identity)
```

### `request_admission`

```text
accepted                       — request routed to upstream
rejected_proxy_not_ready       — proxy initialization not complete
rejected_model_missing         — requested model not in catalog
rejected_backend_unavailable   — backend /health check failed or exception
rejected_backend_not_ready     — backend reachable but model not loaded
rejected_model_not_loaded      — model not in loaded state (synonym or variant of above)
```

### `recovery_state`

```text
none            — no recovery action known or needed
available       — a recovery action can be attempted
in_progress     — recovery action currently running (e.g. model loading)
throttled       — recovery possible but backoff applies
failed          — last recovery attempt failed
manual_required — automated recovery not available; operator must act
```

### Shared boolean/enum fields

```text
recoverable      bool   — can the system recover without operator intervention?
retryable        bool   — should the client retry the request (after a wait)?
fatal            bool   — is this an unrecoverable permanent failure?
```

### `operator_action` (what the operator should do)

```text
remote_wait            — wait for proxy/catalog to finish initializing
refresh_catalog        — POST /qz/models/refresh
start_backend          — qz-up or docker start
restart_backend        — restart llama.cpp backend process
select_model           — POST /qz/models/select
inspect_logs           — check var/logs/qz-proxy.log or docker logs
manual_intervention    — no automated path; inspect and act manually
```

---

## Proposed `qz.service.status.v1` shape

This payload would eventually appear inside `/qz/control-plane` as an additive field,
**without removing or changing any existing fields**.

```json
{
  "schema": "qz.service.status.v1",
  "proxy_state": "ready",
  "catalog_state": "ready",
  "backend_state": "unreachable",
  "model_state": "unknown",
  "request_admission": "rejected_backend_unavailable",
  "recovery_state": "available",
  "recoverable": true,
  "retryable": true,
  "fatal": false,
  "last_error": "Connection refused to http://127.0.0.1:18084",
  "operator_action": "start_backend",
  "operator_hints": [
    "The llama.cpp backend is unreachable. Start with scripts/qz-up.",
    "Remote qz-codex clients do not need local Docker access."
  ]
}
```

**Design rules:**
- Additive only inside `/qz/control-plane` — no existing fields change.
- `proxy_state`/`catalog_state`/`backend_state`/`model_state` use the canonical strings defined above.
- `request_admission` describes the **current** admission policy, not a specific rejected request.
- Remote `qz-codex` clients must not need Docker access to interpret this.
- `operator_action` and `operator_hints` must be remote-friendly.

---

## Recovery classification matrix

Current behaviour sourced from code. Canonical state and future action are proposed targets.

| Scenario | Current behaviour | Canonical state | Recoverable? | Retryable? | Future action |
|---|---|---|---|---|---|
| Proxy initializing | `/v1/responses` → 503 `qz.responses.error.v1` `proxy not ready` | `proxy_state=initializing` | yes | yes (wait) | `operator_action=remote_wait` |
| Catalog not ready | `/qz/models/refresh` → 503; `/v1/models` → empty | `catalog_state=loading` | yes | yes (wait) | `operator_action=remote_wait` |
| Requested model missing | `/v1/responses` → 503 with `available_models` list | `model_state=none` | possibly | no | `operator_action=select_model` |
| Deprecated alias requested | `/v1/responses` → 503 with `alias_hint` | `model_state=none` | yes | no | `operator_action=select_model` |
| Profile symlink target missing | `/v1/responses` → 503 compact dict (not yet v1 schema) | `model_state=failed` | yes (fix symlink) | no | `operator_action=inspect_logs` — **gap: not using qz.responses.error.v1** |
| Backend unreachable | `/v1/responses` → 502 `qz.responses.error.v1` | `backend_state=unreachable` | yes | no | `operator_action=start_backend` |
| Backend health non-200 | Proxy returns 503 or continues degraded | `backend_state=unhealthy` | possibly | no | `operator_action=restart_backend` |
| Model loading (in flight) | `model_load_state=loading`; requests may proceed or stall | `model_state=loading` | yes | yes (wait) | `recovery_state=in_progress` |
| Model load timeout | `model_load_state=loading` → eventually `failed` | `model_state=failed` | possibly | no | `operator_action=restart_backend` |
| Model load HTTP failure | `model_load_state=failed`; error emitted to telemetry | `model_state=failed` | possibly | no | `operator_action=inspect_logs` |
| Context mismatch / restart_required | `restart_required=true`, `restart_required_state=confirmed/pending` | `backend_state=restarting` (during), `mismatch` (after) | yes | yes (after restart) | `operator_action=restart_backend` |
| Backend restart timeout | `model_load_state=loading` → stuck | `backend_state=failed` | possibly | no | `operator_action=manual_intervention` |
| Backend restart Docker failure | `restart_container()` raises exception | `backend_state=failed` | possibly | no | `operator_action=inspect_logs` |
| Client disconnect during stream | `ClientStreamDisconnected` caught; stream terminates | no service impact | yes | yes (client retry) | `recovery_state=none` |
| Upstream returns HTTP 5xx | `/v1/responses` → 502 `qz.responses.error.v1` | `backend_state=unhealthy` | possibly | yes (if transient) | `operator_action=inspect_logs` |
| Telemetry unavailable | qz-thoughts shows `parse_error`; requests unaffected | no service impact | yes | yes | `recovery_state=none` |
| VRAM allocation known only as approximation | qz-top shows delta only | report-only | n/a | n/a | tracked by #6 |

---

## Service/recovery gaps

### Report-only now (no code action)

```text
- No crash-loop / backoff tracking for repeated backend load failures.
- No last-good-model fallback policy (if selected model fails, no automatic rollback).
- No stuck-loading timeout classification in /qz/control-plane.
- No VRAM confidence labels in control-plane (tracked by #6).
- Profile-backend-missing does not yet use qz.responses.error.v1 schema.
```

### Safe next additive fields (slice 2 target)

```text
- Add qz.service.status.v1 builder (proxy/qz_service_status.py).
- Include service_status inside /qz/control-plane as an optional field.
- All existing fields unchanged.
```

### Future recovery actions (slice 3+)

```text
- Extend qz.responses.error.v1 with canonical error_code, recoverable, retryable, operator_action.
- Add /qz/recovery/status read-only summary (slice 4).
- Design manual recovery endpoints with backoff (slice 5).
- No automatic crash-looping without explicit operator authority design.
```

### Requires Phase 1 SQLite / durable state (#2)

```text
- Load attempt history and backoff tracking.
- Crash-loop detection (N failures in M seconds).
- Recovery attempt timestamps.
```

### Requires local operator authority design

```text
- Automatic Docker restart (qz-up is the current manual path).
- Remote-vs-local operator mode distinction.
- Supervisor-grade service abstraction around Docker backend.
```

---

## Suggested next slices for #47

### Slice 2 (done): Add `qz.service.status.v1` to `/qz/control-plane`

- Added `proxy/qz_service_status.py` with `build_service_status(cp: dict)`.
- Returns a `qz.service.status.v1` dict using the canonical enum strings defined above.
- Included inside `/qz/control-plane` response as `"service_status": {...}`.
- All existing control-plane fields remain unchanged (additive only).
- Tests: `tests/test_qz_service_status.py` (6 scenario classes, 35+ assertions) and
  `tests/test_qz_control_plane.py::ServiceStatusInControlPlaneTests`.
- `build_service_status()` takes the assembled control-plane payload (no double probe).

### Slice 3 (done): Extend `qz.responses.error.v1`

Added to `build_responses_error_payload()` in `proxy/qz_responses_error.py`:
- `error_code` — canonical snake_case code derived from `error` string if not explicit.
- `status_code` — HTTP status returned (503/502) when provided.
- `service_status` — embedded `qz.service.status.v1` block when built by router.
- `recoverable`, `retryable`, `fatal`, `operator_action` — mirrored from `service_status`
  when not explicitly overridden; otherwise from explicit caller arguments.

Added helper: `normalize_error_code(error: str) -> str`.

Three `/v1/responses` rejection paths updated in `qz_request_router.py`:
- Proxy not ready: `status_code=503`, `service_status` from minimal cp, inherits
  `recoverable=True`, `retryable=True`, `operator_action=remote_wait`.
- Model not found: `status_code=503`, `service_status`, `operator_action=select_model`.
  Profile-backend-missing now uses `error_code=profile_backend_missing` and
  `operator_action=inspect_logs` — distinguished from generic model-not-found.
- Backend unavailable: `status_code=502`, `service_status` with
  `backend_state=unreachable`, `operator_action=start_backend`.

All existing fields unchanged (`error`, `reason`, `requested_model`, `available_models`,
`alias_hint`, `readiness`, `proxy_initialization`, `operator_hint`, `fix`).

### Slice 4 (done): Add `/qz/recovery/status` (read-only)

Added `proxy/qz_recovery_status.py` with `build_recovery_status(service_status)`.

New endpoint: `GET /qz/recovery/status` — always returns HTTP 200 JSON,
even when backend is down. No actions taken; purely diagnostic/advisory.

Schema: `qz.recovery.status.v1`. Key fields:
- `state` — mirrors `service_status.recovery_state`
- `ok` — true when no problem or recovery is in progress (false when action needed)
- `remote_client_action` — what a remote qz-codex client should do:
  `wait`, `choose_valid_model`, `retry_after_refresh`, `contact_operator`, `""`
- `local_operator_action` — what a local operator should do:
  `monitor`, `start_backend`, `restart_backend`, `select_model`,
  `refresh_catalog`, `inspect_logs`, `""`
- `summary` — one-sentence human-readable status

Also embedded additively inside `/qz/control-plane` as `"recovery": {...}`.
Existing control-plane fields unchanged.

Tests: `tests/test_qz_recovery_status.py`.

### Slice 5 (done): Manual recovery endpoint design

Policy doc: `docs/backend-manual-recovery-endpoint-policy.md`.

Key decisions:
- `POST /qz/recovery/plan` — dry-run only; always HTTP 200/400; no side effects.
- `POST /qz/recovery/trigger` — state-changing; requires `QZ_RECOVERY_ACTIONS=1`;
  explicit `action`, `reason`, and `confirm` in request body.
- Allowed actions: `refresh_catalog`, `select_model`, `reload_selected_model`,
  `start_backend`, `restart_backend`, `clear_failure`.
- Forbidden: automatic crash-loop restart, remote-client restart, restart without
  `QZ_RECOVERY_ACTIONS=1`, restart with active requests unless `force=true`.
- Backoff: 30 s / 2 min / 5 min, then `manual_required`. Per-action, not global.
- Active request safety: restart actions blocked until request tracking or `force=true`.
- Authority flags: `QZ_RECOVERY_ACTIONS`, `QZ_RECOVERY_BIND_LOCAL_ONLY`,
  `QZ_RECOVERY_CONFIRM_PHRASE`, `QZ_RECOVERY_MAX_ATTEMPTS`, `QZ_RECOVERY_BACKOFF_SECS`.
- New error schema: `qz.recovery.error.v1` (separate from `qz.responses.error.v1`).
- Future implementation slices 6–10 defined in the policy doc.
- Still no automatic crash-looping. No Docker calls. #47 stays open.

### Slice 6 (done): Pure recovery planning helper

Added `proxy/qz_recovery_plan.py` with `build_recovery_plan(service_status, action, ...)`.

Schema: `qz.recovery.plan.v1`. Pure function — no I/O, no backend probe, no mutation,
no HTTP route. Answers: "Would this action be feasible right now, and why?"

Returns boolean blocking flags: `blocked_by_authority`, `blocked_by_locality`,
`blocked_by_in_progress`, `blocked_by_backoff`, `blocked_by_state`,
`blocked_by_active_requests`, `blocked_by_missing_model`. `feasible=True` only
when all blocking flags are False.

Tests: `tests/test_qz_recovery_plan.py` — 75 assertions across 16 test classes.
Details in `docs/backend-manual-recovery-endpoint-policy.md` section 12.

### Slice 7 (done): `POST /qz/recovery/plan` dry-run endpoint

Added route `POST /qz/recovery/plan` in `proxy/qz_request_router.py`.

HTTP 200 for valid body + known action → `qz.recovery.plan.v1`.
HTTP 400 for bad JSON, missing action, unknown action → `qz.recovery.error.v1`.

`active_requests`, `backoff_active`, `recovery_in_progress` passed as `None`/`False`
(slice 8 wires backoff/in_progress). No trigger. No state mutation. No Docker calls.

### Slice 8 (done): In-memory recovery backoff state

Added `proxy/qz_recovery_state.py` with `RecoveryRuntimeState` and `RECOVERY_STATE` singleton.

Schema: `qz.recovery.runtime_state.v1`. Thread-safe. Per-action failure counts,
backoff schedule (30 s / 120 s / 300 s), and `manual_required` after exceeding
`QZ_RECOVERY_MAX_ATTEMPTS` (default 3). In-memory only; does not survive restart.

Integration:
- `ProxyHandler.recovery_state = RECOVERY_STATE` class var
- `GET /qz/recovery/status` includes `runtime_state` and `backoff` fields
- `GET /qz/control-plane` `recovery` field includes `runtime_state` and `backoff`
- `POST /qz/recovery/plan` uses real `backoff_active` / `recovery_in_progress`
- `build_recovery_status(ss, runtime_state=None)` — optional param; backward-compatible

Tests: `tests/test_qz_recovery_state.py` — 69 assertions.

### Slice 9 (done): First safe trigger actions

Added `POST /qz/recovery/trigger` (state-changing, gated).

Implemented: `refresh_catalog` (rescan + Codex catalog), `clear_failure` (clear RECOVERY_STATE).
Blocked with 409: `restart_backend`, `start_backend`, `reload_selected_model`, `select_model`.
Gates: `QZ_RECOVERY_ACTIONS=1` (403), `QZ_RECOVERY_BIND_LOCAL_ONLY=1` default (403),
`reason` required (400), `force=true` rejected (400), backoff/in_progress checked (429/423).
Response: `qz.recovery.trigger.v1` with `accepted`, `pre_status`, `post_status`.
Telemetry: `recovery_trigger_requested`, `recovery_action_started`, `recovery_action_completed`,
`recovery_action_failed`, `recovery_trigger_rejected`.

Still missing: active request tracking (slice 10a), restart actions, durable state (#2 SQLite). #47 open.

### Slice 10a (done): Active request tracking

Added `proxy/qz_active_requests.py` with `ActiveRequestTracker` / `ACTIVE_REQUESTS` singleton.

In-memory, thread-safe, non-durable. `begin()` / `finish()` guaranteed non-raising.
Schema: `qz.active_requests.v1` with `count` and `requests[]` including `age_secs`.

Integration:
- `ProxyHandler.active_requests = ACTIVE_REQUESTS`
- `/v1/responses` in `proxy_json_api`: `begin()` before upstream dispatch; `finish()` at each exit
- `GET /qz/recovery/status` — `active_requests` snapshot embedded
- `/qz/control-plane` `recovery` field — `active_requests` embedded
- `POST /qz/recovery/plan` — passes real `ar.count()` instead of `None`
- `build_recovery_status(ss, runtime_state=None, active_requests=None)` — backward-compat

`restart_backend` plan now reports `blocked_by_active_requests=True` when count > 0.
Tests: `tests/test_qz_active_requests.py` — 29 assertions.

---

## Open questions

1. **Should `restart_required` trigger automatic backend restart?** Currently left to
   the operator. Automating this needs backoff, crash-loop detection, and explicit
   design. Defer to slice 5.

2. **What is the right `operator_action` for remote vs local users?** Remote clients
   cannot run `scripts/qz-up`. The hint must distinguish "contact your local operator"
   from "wait and retry".

3. **How should VRAM allocation confidence be expressed in the service status?**
   Currently qz-top shows approximations (delta-based). Exact allocation requires
   backend-side reporting. Tracked by #6; out of scope for #47.

4. **Should `/qz/status` be deprecated in favour of `/qz/control-plane`?** Currently
   both exist. `/qz/status` is richer for internal/proxy use; `/qz/control-plane` is
   cleaner for clients. Keep both for now; evaluate after slice 2 lands.

5. **Is `model_state=mismatch` the right label for context mismatch?** The current
   code calls this `restart_required`. A more precise enum would distinguish
   identity mismatch (loaded ≠ selected) from context mismatch (context length differs).

---

## Cross-references

| Reference | Relevance |
|---|---|
| `docs/backend-control-plane-audit.md` | #44 migration history; current proxy-owned surfaces |
| `proxy/qz_control_plane.py` | Current `_overall_status()` and `build_control_plane_status()` |
| `proxy/qz_responses_error.py` | Current `qz.responses.error.v1` builder |
| `proxy/qz_model_router.py` | `model_load_state`, `_persist_backend_state()`, `load_backend_model()` |
| `proxy/qz_backend.py` | `get_health()`, `get_models()`, `load_model()`, `restart_container()` |
| `tests/test_qz_control_plane.py` | Control-plane test coverage |
| `tests/test_qz_responses_error.py` | Responses error test coverage |
| `#2` | Phase 1 SQLite substrate (prerequisite for durable recovery tracking) |
| `#6` | Backend VRAM telemetry and monitor polish |
| `#45` | Remove local qz-codex catalog fallback |
| `#46` | Replace qz-write-runtime-state launcher trace |
| `#47` | This issue — normalize backend service status and recovery semantics |
