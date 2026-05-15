# Backend Manual Recovery Endpoint Policy

Date: 2026-05-15
Status: #47 slice 5 — design and policy only. No endpoints implemented yet.

---

## 1. Purpose and status

This document defines the policy for future manual recovery endpoints in the
QuantZhai proxy. It is a design document, not an implementation. No state-changing
endpoints exist yet.

**What exists today (read-only):**

| Surface | Status |
|---|---|
| `GET /qz/control-plane` | Done. Always 200 JSON. Embeds `service_status` and `recovery` fields. |
| `GET /qz/recovery/status` | Done. Always 200 JSON. `qz.recovery.status.v1`. No actions. |
| `qz.service.status.v1` | Done. Pure builder. Canonical state enums. |
| `qz.recovery.status.v1` | Done. Pure builder. Remote/local action split. |
| `qz.responses.error.v1` | Done. Extended with `error_code`, `recoverable`, `retryable`, `operator_action`, `service_status`. |

**What this document designs (not yet built):**

- `POST /qz/recovery/plan` — dry-run only, no side effects.
- `POST /qz/recovery/trigger` — state-changing, explicit confirmation required.
- Backoff and crash-loop policy.
- Authority / env-flag gating.
- Active request safety rules.
- Telemetry event names and shapes.

**Hard non-goals for this policy pass:**

- Do not implement any POST endpoint yet.
- Do not add automatic restart or crash-loop detection.
- Do not call Docker.
- Do not change `/qz/recovery/status`, `/qz/control-plane`, or `/v1/responses` behaviour.
- Do not add SQLite or durable memory.
- Do not close #47.

---

## 2. Current recovery surfaces

### Read-only status

```
GET /qz/control-plane
  schema: qz.control_plane.status.v1
  fields: ok, status, readiness, proxy_initialization, models, backend,
          codex_catalog, operator_hints, service_status, recovery

GET /qz/recovery/status   (also: GET /qz/recovery)
  schema: qz.recovery.status.v1
  fields: ok, state, recoverable, retryable, fatal, operator_action,
          last_error, remote_client_action, local_operator_action,
          summary, service_status, operator_hints
```

### Structured errors on rejection

```
POST /v1/responses → qz.responses.error.v1
  fields include: error_code, status_code, recoverable, retryable, fatal,
                  operator_action, service_status
```

### Canonical state taxonomy

From `proxy/qz_service_status.py` (the shared authority):

```
proxy_state:    starting | initializing | ready | degraded | failed
catalog_state:  unknown | loading | ready | failed
backend_state:  unknown | unreachable | healthy | unhealthy | failed
model_state:    none | unknown | unloaded | loading | loaded | failed | mismatch
request_admission: accepted | rejected_proxy_not_ready | rejected_model_missing |
                   rejected_backend_unavailable | rejected_backend_not_ready |
                   rejected_model_not_loaded
recovery_state: none | available | in_progress | throttled | failed | manual_required
operator_action: remote_wait | refresh_catalog | start_backend | restart_backend |
                 select_model | inspect_logs | manual_intervention
```

---

## 3. Safety model

### Role definitions

**remote_client** (qz-codex running off-host, or any API consumer):

- May read `GET /qz/control-plane` and `GET /qz/recovery/status`.
- Sees `remote_client_action` in recovery status: `wait`, `choose_valid_model`,
  `retry_after_refresh`, `contact_operator`, `""`.
- Must never be instructed to execute Docker or shell commands.
- Must not be able to trigger backend restart by default.
- Any future state-changing capability for remote clients requires an explicit
  authentication/authorization model that does not exist yet.

**local_operator** (shell access on the llama.cpp host):

- Has Docker, shell, and filesystem access.
- May invoke state-changing recovery actions in the future via local HTTP POST.
- Must receive clear `operator_warning` and `pre_status` before any action.
- Authority must be explicitly opt-in via env flags (see section 11).

**proxy** (QuantZhai Python process):

- Reports state via `/qz/control-plane` and `/qz/recovery/status`.
- May eventually accept manual recovery POSTs, subject to authority flags, backoff,
  and active request safety checks.
- Must never initiate automatic recovery loops without explicit policy.
- Must never restart the backend or Docker container without local operator authority.

**future optional automation:**

- Automatic crash-loop detection or self-healing is out of scope until:
  - Phase 1 SQLite substrate (#2) provides durable attempt history.
  - Backoff policy is implemented and tested (see section 7).
  - Active request safety is implemented (see section 8).

---

## 4. Proposed endpoints

### Already implemented

```
GET /qz/recovery/status
  Read-only. Always HTTP 200 JSON. Safe when backend is down.
  Schema: qz.recovery.status.v1
```

### Proposed future — dry-run only

```
POST /qz/recovery/plan
  Purpose: return what would happen for a requested action, with no side effects.
  HTTP: 200 on valid request; 400 on invalid JSON or unknown action.
  Body:   { "action": "...", "model": "...", "reason": "..." }
  Response schema: qz.recovery.plan.v1 (see section 4.1)
  No state changes. No Docker calls. No backend interactions.
```

### Proposed future — state-changing

```
POST /qz/recovery/trigger
  Purpose: manually trigger one recovery action.
  Requires: local operator authority (QZ_RECOVERY_ACTIONS=1).
  Requires: explicit action and confirmation phrase in body.
  No automatic loops. One action per request.
  HTTP codes: see section 10.
  Request body schema: qz.recovery.trigger.request.v1 (see section 4.2)
  Response schema: qz.recovery.trigger.v1 (see section 4.3)
```

### 4.1 Dry-run response shape

```json
{
  "schema": "qz.recovery.plan.v1",
  "action": "restart_backend",
  "feasible": true,
  "blocked_by_active_requests": false,
  "blocked_by_backoff": false,
  "blocked_by_authority": false,
  "blocked_by_state": false,
  "would_interrupt_requests": true,
  "pre_status": { "...": "qz.recovery.status.v1 snapshot" },
  "operator_warning": "Restarting the backend will interrupt any active requests.",
  "notes": ["No active requests currently tracked.", "Backoff not active."]
}
```

### 4.2 Trigger request shape

```json
{
  "action": "start_backend | restart_backend | refresh_catalog | select_model | reload_selected_model | clear_failure",
  "model": "optional model id — required for select_model",
  "reason": "operator-supplied reason string",
  "confirm": "I understand this may interrupt active requests",
  "force": false
}
```

- `action`: required. Must be in the allowed set (see section 5).
- `model`: required when `action == "select_model"`.
- `reason`: required. Recorded in telemetry. Not validated for content.
- `confirm`: required for all restart actions. Exact phrase checked if
  `QZ_RECOVERY_CONFIRM_PHRASE` is set.
- `force`: optional bool, default false. Required to bypass active-request safety
  on restart actions (see section 8).

### 4.3 Trigger response shape

```json
{
  "schema": "qz.recovery.trigger.v1",
  "accepted": true,
  "action": "restart_backend",
  "dry_run": false,
  "request_id": "rec-<uuid>",
  "pre_status": { "...": "qz.recovery.status.v1 snapshot at trigger time" },
  "post_status": null,
  "operator_warning": "Restart in progress. Active requests may be dropped.",
  "telemetry_event": "recovery_trigger_requested"
}
```

- `post_status`: null for async actions; populated for synchronous harmless actions
  like `clear_failure` or `refresh_catalog` if completed before response.
- `request_id`: used to correlate telemetry events.

---

## 5. Allowed actions policy

### `refresh_catalog`

- Calls the existing catalog refresh logic (same path as `POST /qz/models/refresh`).
- No backend restart. No Docker call.
- Allowed when: catalog stale, missing, or failed.
- Blocked when: proxy initialization not complete.
- Safe for: local and potentially remote clients (low risk, no model load side effects).

### `select_model`

- Selects a valid model/profile by slug.
- May trigger model load depending on current `ModelRouter` behaviour and current
  backend state. Must document the potential load side effect clearly in `operator_warning`.
- Allowed when: proxy and catalog ready.
- Blocked when: model slug not in catalog, proxy not ready.

### `reload_selected_model`

- Unloads current model then loads selected model.
- Does not restart Docker or the backend container.
- Requires backend to be reachable.
- Allowed when: backend reachable, model loaded or failed.
- Blocked when: backend unreachable, no model selected.
- Side effect: interrupts any requests waiting on the current model.

### `start_backend`

- Attempts to start the backend container.
- Requires explicit local operator authority (`QZ_RECOVERY_ACTIONS=1`).
- Allowed when: backend unreachable.
- Blocked when: backend already reachable, authority not enabled, backoff active.
- Will eventually call the Docker launcher path. First implementation may just
  return a plan or hint, not a real Docker call.

### `restart_backend`

- Restarts the llama.cpp backend container.
- Requires explicit local operator authority (`QZ_RECOVERY_ACTIONS=1`).
- Requires `confirm` phrase in request body.
- Requires active request safety check (see section 8).
- Allowed when: backend unhealthy, context mismatch, stuck loading, or operator-forced.
- Blocked when: active requests exist and `force != true`, backoff active, authority not enabled.
- Side effect: drops any active upstream connections.

### `clear_failure`

- Clears stored `failed` / `recovery` state in the proxy's in-memory model router.
- Does not start or restart anything.
- Does not touch Docker.
- Useful after a manual fix so the proxy stops reporting a stale failure state.
- Allowed when: `model_load_state == "failed"` or `recovery_state == "failed"`.
- Blocked when: nothing to clear.
- Does NOT clear SQLite state (when that exists); only in-memory state.

---

## 6. Forbidden actions

The first implementation of state-changing recovery endpoints must explicitly refuse:

| Forbidden | Reason |
|---|---|
| Automatic crash-loop restart | No backoff or crash-loop detection yet; would brick the host |
| Repeated restart without backoff | Same reason |
| Remote qz-codex–initiated restart | Remote client safety rule; no auth model exists |
| Restart without local operator authority | `QZ_RECOVERY_ACTIONS` must be 1 |
| Restart while active requests are running without `force=true` | Active request safety |
| Destructive cleanup of model files | Out of scope; unrecoverable |
| Deleting `var/model-state.json` or `var/backend-state.json` as recovery | These files are state, not locks; deleting them silently corrupts cached context |
| Cascading or chained recovery actions in one request | One action per request; sequencing is operator responsibility |
| `force=true` on safe actions (`refresh_catalog`, `clear_failure`) | `force` flag only applies to restart actions; must be rejected with 400 on safe actions to avoid confusion |

---

## 7. Backoff and crash-loop policy

No durable tracking exists yet. Until Phase 1 SQLite (#2) is available, in-memory
tracking is acceptable for early manual endpoints, but the implementation must not
claim durable crash-loop protection.

### Fields to track (in-memory first, SQLite later)

```python
last_recovery_action: str          # action name
last_recovery_started_at: float    # Unix timestamp
last_recovery_finished_at: float   # Unix timestamp
last_recovery_error: str           # error message, if any
recovery_attempt_count: int        # per-action count since last clear
recent_failures: list[float]       # timestamps of recent failures (rolling window)
backoff_until: float               # Unix timestamp; 0 means no active backoff
```

### Backoff schedule (per action type)

| Attempt | Wait before next |
|---|---|
| 1st failure | 30 s |
| 2nd failure | 2 min |
| 3rd failure | 5 min |
| 4th+ failure | `recovery_state = manual_required`; no further automatic attempts |

### State transitions

- `recovery_state = throttled` when `backoff_until > now`.
- `recovery_state = manual_required` when attempt count exceeds threshold.
- Cleared by: `action = clear_failure` (in-memory); future: operator-confirmed reset.

### Implementation note

The backoff clock must be per-action, not global. A `refresh_catalog` failure must
not block `clear_failure`.

---

## 8. Active request safety

### When active request count is available

- `GET /qz/recovery/plan` response must include `blocked_by_active_requests: true/false`.
- `POST /qz/recovery/trigger` for restart actions must reject with HTTP 409 if any
  active requests are in flight, unless `force=true` in the request body.
- When rejected, the response body must name the count and suggest retrying when idle.
- Telemetry must record `recovery_trigger_rejected` with `reason=active_requests`.

### When active request count is NOT yet tracked (current state)

This is a gap. The proxy does not currently track in-flight request count.

- Until this gap is closed, the first endpoint implementation must avoid restart actions
  entirely, or require a strong `force=true` with an extra explicit warning in
  `operator_warning` that request count is unknown.
- `refresh_catalog` and `clear_failure` are safe to allow without active-request tracking.
- `select_model` is moderate-risk and should document the gap.
- `start_backend`, `restart_backend`, `reload_selected_model` must be gated until
  active request tracking is available OR until the operator provides `force=true`
  and acknowledges the gap explicitly.

### Telemetry for blocked attempts

```
recovery_trigger_rejected with:
  reason: active_requests | backoff | authority | unknown_action | bad_confirm | bad_state
```

---

## 9. Telemetry events

Future telemetry events for recovery actions. Each event should be emitted to the
existing telemetry bus (same path as `model_load_started`, `model_load_completed`, etc.).

| Event | When emitted |
|---|---|
| `recovery_plan_requested` | `POST /qz/recovery/plan` received |
| `recovery_trigger_requested` | `POST /qz/recovery/trigger` accepted |
| `recovery_trigger_rejected` | `POST /qz/recovery/trigger` rejected at any check |
| `recovery_action_started` | Action begins (after all safety checks pass) |
| `recovery_action_completed` | Action finished successfully |
| `recovery_action_failed` | Action raised exception or returned error |
| `recovery_backoff_started` | Backoff clock started after failure |
| `recovery_backoff_cleared` | Backoff cleared (by time or operator reset) |
| `recovery_manual_required` | Attempt count exceeded threshold |

### Minimum event fields

```json
{
  "source": "recovery_trigger",
  "event": "recovery_action_started",
  "request_id": "rec-<uuid>",
  "action": "restart_backend",
  "reason": "operator supplied reason",
  "pre_service_status": { "...": "qz.service.status.v1 snapshot" },
  "pre_recovery_status": { "...": "qz.recovery.status.v1 snapshot" },
  "operator_action": "restart_backend",
  "local_operator_required": true,
  "error": null
}
```

---

## 10. HTTP and error semantics

### `GET /qz/recovery/status`

- Always HTTP 200. Already implemented.

### `POST /qz/recovery/plan`

- HTTP 200 on valid request with any known action.
- HTTP 400 on invalid JSON or unknown action value.
- Never 5xx unless the proxy itself has crashed (which would prevent any response).

### `POST /qz/recovery/trigger`

| Code | Meaning |
|---|---|
| `202` | Action accepted for async execution (e.g. `restart_backend`, `start_backend`) |
| `200` | Action completed synchronously (e.g. `clear_failure`, `refresh_catalog`) |
| `400` | Invalid JSON, unknown action, missing required fields, or `force=true` on a safe action |
| `403` | Local operator authority not enabled (`QZ_RECOVERY_ACTIONS != 1`) |
| `409` | Blocked by current state: active requests, recovery already in progress, or bad state for action |
| `423` | Recovery action of this type is already in progress (distinct from 409 state conflict) |
| `429` | Backoff active — too many recent failures; `Retry-After` header if available |
| `500` | Action failed unexpectedly after being accepted |

### Error schema for recovery endpoints

All 4xx/5xx responses from recovery endpoints must use:

```json
{
  "schema": "qz.recovery.error.v1",
  "ok": false,
  "error": "short error code",
  "message": "human-readable explanation",
  "action": "requested action",
  "blocked_by": "active_requests | backoff | authority | state | unknown_action | bad_confirm",
  "retry_after_secs": null,
  "recovery_status": { "...": "qz.recovery.status.v1 snapshot if available" }
}
```

Note: `qz.recovery.error.v1` is a new schema, distinct from `qz.responses.error.v1`.
The latter is for `/v1/responses` rejections. The recovery error schema is for
`/qz/recovery/*` endpoint failures only.

---

## 11. Authority and configuration flags

### Required flags (must exist before any state-changing endpoint lands)

```
QZ_RECOVERY_ACTIONS=0|1
  Default: 0
  Must be 1 to allow state-changing recovery endpoints.
  When 0: POST /qz/recovery/trigger returns HTTP 403 immediately.
  When 0: POST /qz/recovery/plan is still allowed (dry-run, no side effects).
```

```
QZ_RECOVERY_BIND_LOCAL_ONLY=0|1
  Default: 1
  When 1: state-changing recovery requests accepted only from loopback (127.0.0.1 / ::1).
  Proxy should check the client IP. If it cannot determine local vs remote reliably,
  it must assume remote and reject unless QZ_RECOVERY_BIND_LOCAL_ONLY=0 is explicit.
  Note: this is a defence-in-depth measure; QZ_RECOVERY_ACTIONS=0 is the primary gate.
```

### Optional flags

```
QZ_RECOVERY_CONFIRM_PHRASE
  Default: unset (confirm field not validated against phrase)
  When set: the "confirm" field in trigger request body must match this phrase exactly.
  Useful for additional safety: QZ_RECOVERY_CONFIRM_PHRASE="I understand this may interrupt active requests"
```

```
QZ_RECOVERY_MAX_ATTEMPTS=N
  Default: 3
  Per-action failure threshold before recovery_state=manual_required.
```

```
QZ_RECOVERY_BACKOFF_SECS=30,120,300
  Default: 30,120,300
  Comma-separated backoff intervals in seconds for attempts 1, 2, 3+.
  Parsed at startup. Invalid values fall back to default.
```

### Remote-client rule

Even if a future remote user can read recovery status, state-changing recovery must
remain local-operator-only unless an explicit authentication/authorization model is
designed and reviewed. No such model exists yet. Do not add remote recovery capability
as a convenience shortcut.

---

## 12. Implementation slices after this doc

Suggested slice order, building on the policy above:

### Slice 6 (done): Pure recovery planning helper + tests

Added `proxy/qz_recovery_plan.py` with `build_recovery_plan(service_status, action, ...)`.

Key constants:
- `RECOVERY_PLAN_SCHEMA = "qz.recovery.plan.v1"`
- `ALLOWED_RECOVERY_ACTIONS` — frozenset of six action names

Signature:
```python
build_recovery_plan(
    service_status, action, *,
    model="", force=False, authority_enabled=False,
    local_request=True, active_requests=None,
    backoff_active=False, recovery_in_progress=False,
) -> dict
```

Blocking flags computed per-action:
- `blocked_by_authority` — action in `_REQUIRES_AUTHORITY` and `authority_enabled=False`
- `blocked_by_locality` — action requires authority and `local_request=False`
- `blocked_by_in_progress` — state-changing action and `recovery_in_progress=True`
  (refresh_catalog and select_model are NOT blocked)
- `blocked_by_backoff` — backoff-applicable action and `backoff_active=True`
- `blocked_by_state` — wrong state for action (per-action checks)
- `blocked_by_active_requests` — interrupting action, `active_requests > 0`, `force=False`
- `blocked_by_missing_model` — select_model without a model slug
- `feasible` — `True` only when no blocking flag is `True`

Active request safety when `active_requests is None`:
- Does not block, but adds note: "active request count unavailable; implementation
  must require force=true before triggering interrupt actions."

Tests: `tests/test_qz_recovery_plan.py` — 16 test classes, 75 assertions.
No endpoint, no side effects, no I/O.

### Slice 7 (done): `POST /qz/recovery/plan` dry-run endpoint

Wired `build_recovery_plan()` to `POST /qz/recovery/plan` in `qz_request_router.py`.

Route behaviour:
- `HTTP 200` — valid JSON body with known action; returns `qz.recovery.plan.v1`.
  `feasible` may be `true` or `false`; that is not an HTTP error.
- `HTTP 400` — invalid JSON, non-object body, missing `action`, or unknown action;
  returns `qz.recovery.error.v1` with `error` in `{invalid_json, missing_action, unknown_action}`.

Dry-run inputs passed to `build_recovery_plan()`:
- `service_status` — from `build_control_plane_status(handler)["service_status"]`
- `action` — `body["action"]`
- `model` — `body.get("model", "")`
- `force` — `bool(body.get("force", False))`
- `authority_enabled` — `os.environ.get("QZ_RECOVERY_ACTIONS", "0") == "1"`
- `local_request` — derived from `handler.client_address[0]` (127.0.0.1 / ::1 / localhost)
- `active_requests=None` — tracking not yet implemented (slice 8)
- `backoff_active=False` — tracking not yet implemented (slice 8)
- `recovery_in_progress=False` — tracking not yet implemented (slice 8)

New helpers in `RequestRouter`:
- `_recovery_error_payload(error, message, action, blocked_by, recovery_status)` — static
- `_is_local_request(handler)` — derives local/remote from `client_address`
- `_handle_recovery_plan()` — route handler called by `handle_post`

New tests added to `tests/test_qz_recovery_plan.py`:
- `RecoveryErrorPayloadTests` (5 assertions) — schema, ok=False, fields, defaults, serialisable
- `IsLocalRequestTests` (5 assertions) — ipv4/ipv6/localhost/remote/missing

No trigger endpoint. No state mutation. No Docker calls. No action telemetry.

### Slice 8 (done): In-memory backoff and attempt tracking

Added `proxy/qz_recovery_state.py` with `RecoveryRuntimeState` and `RECOVERY_STATE` singleton.

Key API:
- `is_backoff_active(action, now=None) -> bool` — True when time-based backoff or `manual_required`
- `is_recovery_in_progress() -> bool`
- `mark_started(action, request_id, now)` — for slice 9 trigger
- `mark_completed(action, now)` — resets attempt count on success
- `mark_failed(action, error, now)` — increments count, sets backoff; `manual_required` after N fails
- `clear(action=None)` — resets per-action or all state
- `snapshot(now=None) -> dict` — JSON-serialisable `qz.recovery.runtime_state.v1` payload
- `parse_backoff_schedule(s) -> list[int]` — parses `QZ_RECOVERY_BACKOFF_SECS`

Backoff schedule (default from `QZ_RECOVERY_BACKOFF_SECS=30,120,300`):
- failure 1 → 30 s; failure 2 → 120 s; failure 3 → 300 s; failure 4+ → `manual_required`
- `QZ_RECOVERY_MAX_ATTEMPTS=3` controls the threshold

**Important caveats:**
- In-memory only. Does not survive proxy restart.
- Per-action, not global (refresh_catalog failure does not block restart_backend).
- No durable crash-loop protection. Durable tracking requires #2 SQLite.

Integration points:
- `ProxyHandler.recovery_state = RECOVERY_STATE` — class var on the handler
- `GET /qz/recovery/status` — now includes `runtime_state` and `backoff` fields
- `GET /qz/control-plane` — `recovery` field includes `runtime_state` and `backoff` via updated `build_recovery_status()`
- `POST /qz/recovery/plan` — now uses real `backoff_active` and `recovery_in_progress` from `RECOVERY_STATE`
- `build_recovery_status(service_status, runtime_state=None)` — extended with optional param; existing callers unaffected
- `active_requests` remains `None` — tracking not yet implemented (slice 9 gap)

Tests: `tests/test_qz_recovery_state.py` — 14 test classes, 69 assertions.

### Slice 9 (done): First safe state-changing actions

Added `POST /qz/recovery/trigger` in `proxy/qz_request_router.py`.

**Implemented actions:** `refresh_catalog`, `clear_failure` only.

**Blocked (409):** `restart_backend`, `start_backend`, `reload_selected_model`, `select_model`
— message: "action_not_implemented_in_this_slice; restart/reload deferred to future slices."

HTTP gates (in order):
1. **403** `authority_disabled` — `QZ_RECOVERY_ACTIONS != "1"` (checked before action validation)
2. **403** `non_local_request` — `QZ_RECOVERY_BIND_LOCAL_ONLY=1` (default) and non-loopback client
3. **400** `missing_action` / `missing_reason` / `unknown_action` / `force_not_allowed`
4. **409** `action_not_implemented` — known but not-yet-implemented actions
5. **423** `recovery_in_progress` — another action is already running
6. **429** `backoff_active` — per-action backoff or `manual_required` is active
7. **200** success → `qz.recovery.trigger.v1` with `accepted=true`, `pre_status`, `post_status`
8. **500** `action_failed` — action raised unexpected exception

Request body: `action` (required), `reason` (required), `force` (must be absent/false for safe actions), `confirm` (skipped in this slice).

`force=true` is rejected with 400 on `refresh_catalog` and `clear_failure` — those actions
don't interrupt requests and don't need it.

Telemetry events emitted:
- `recovery_trigger_requested` — request accepted for processing
- `recovery_action_started` — action begins (after all checks pass)
- `recovery_action_completed` — action succeeded
- `recovery_action_failed` — action raised exception
- `recovery_trigger_rejected` — authority/locality checks failed

New constants in `qz_request_router.py`:
- `SAFE_TRIGGER_ACTIONS = frozenset({"refresh_catalog", "clear_failure"})`
- `UNIMPLEMENTED_TRIGGER_ACTIONS = ALLOWED_RECOVERY_ACTIONS - SAFE_TRIGGER_ACTIONS`
- `RECOVERY_TRIGGER_SCHEMA = "qz.recovery.trigger.v1"`

New helpers in `RequestRouter`:
- `_emit_recovery_event(event_type, payload)` — no-ops safely if telemetry unavailable
- `_get_recovery_status_snapshot()` — builds `qz.recovery.status.v1` for pre/post_status
- `_build_trigger_response(action, request_id, ...)` — static, builds trigger.v1 payload
- `_do_refresh_catalog()` — calls existing `catalog.refresh()` + `_refresh_codex_catalog(catalog)`
- `_do_clear_failure(rs)` — calls `rs.clear()` (in-memory only; no file mutation)

**Remaining gaps after slice 9:**
- `active_requests` tracking: still `None` — addressed in slice 10a
- Restart actions: deferred to slice 10 or later
- Durable attempt history: requires #2 SQLite
- `QZ_RECOVERY_CONFIRM_PHRASE`: skipped for safe actions

Tests: `tests/test_qz_recovery_trigger.py` — 7 test classes, 34 assertions.

### Slice 10a (done): Active request tracking

Added `proxy/qz_active_requests.py` with `ActiveRequestTracker` and `ACTIVE_REQUESTS` singleton.

Schema: `qz.active_requests.v1`. Thread-safe. In-memory. Non-durable.
`begin()` and `finish()` guaranteed non-raising.

Snapshot fields: `schema`, `count`, `requests[]` with `request_id`, `route`, `model`, `started_at`, `age_secs`.

Integration:
- `ProxyHandler.active_requests = ACTIVE_REQUESTS` — class var
- `/v1/responses` path in `proxy_json_api()`: `begin()` before streaming/non-streaming dispatch; `finish()` at each exit
- Compaction early-return is before `begin()` — correctly not tracked
- `GET /qz/recovery/status` — includes `active_requests` snapshot
- `/qz/control-plane` `recovery` field — includes `active_requests` snapshot
- `POST /qz/recovery/plan` — passes real `active_requests=ar.count()` instead of `None`
- `build_recovery_status(ss, runtime_state=None, active_requests=None)` — extended (backward-compat)

`restart_backend` planning now correctly reports `blocked_by_active_requests=True`
when in-flight count > 0 and `force=False`. No "active request count unavailable" note.

Tests: `tests/test_qz_active_requests.py` — 9 test classes, 29 assertions.

### Slice 10b (done): Harden recovery trigger preflight

Factored trigger preflight gates into small tested static helpers:

| Helper | Purpose |
|---|---|
| `_recovery_authority_enabled()` | reads `QZ_RECOVERY_ACTIONS` |
| `_recovery_local_only_enabled()` | reads `QZ_RECOVERY_BIND_LOCAL_ONLY` |
| `_confirm_phrase_required(action)` | True when phrase env set AND action is dangerous |
| `_confirm_phrase_matches(body, action)` | returns `(ok, msg)` — validates phrase |
| `_validate_recovery_trigger_body(body)` | validates fields, returns `None` or `(status, error, blocked_by, msg)` |

**Confirmation phrase semantics (`QZ_RECOVERY_CONFIRM_PHRASE`):**
- Unset: no confirmation required for any action currently.
- Set + safe action (`refresh_catalog`, `clear_failure`): still no requirement — keeps low-friction.
- Set + dangerous action (`restart_backend`, etc.): phrase required (but blocked by 409 anyway until slice 11).
- If `body["confirm"]` present and phrase unset: harmless, ignored.

**Rejection payload improvements:**
- 409 `action_not_implemented` now includes `recovery_status` snapshot.
- 423 `recovery_in_progress` and 429 `backoff_active` now include `recovery_status`.

**`_handle_recovery_trigger` flow (refactored, same behaviour):**
1. Parse JSON body
2. `_recovery_authority_enabled()` → 403 if false
3. `_recovery_local_only_enabled()` + `_is_local_request()` → 403 if failed
4. `_validate_recovery_trigger_body(body)` → 400/409 if failed
5. `_confirm_phrase_matches(body, action)` → 400 `bad_confirm` if failed
6. Runtime state checks (in_progress → 423, backoff → 429)
7. Execute safe action

Tests added to `tests/test_qz_recovery_trigger.py`:
- `EnvFlagHelpersTests` — authority/locality env flag helpers
- `ConfirmPhraseRequiredTests` — phrase requirement logic
- `ConfirmPhraseMatchesTests` — phrase validation (ok/missing/wrong/case-sensitive)
- `ValidateTriggerBodyTests` — all validation cases
- `RejectionPayloadWithStatusTests` — recovery_status in payload

Total: 60 tests in `test_qz_recovery_trigger.py`. Dangerous actions still blocked.

### Slice 11: `restart_backend` — only after preflight is stable

- Gated by `QZ_RECOVERY_ACTIONS=1`, `force=true`, real active-request count (slice 10a), and confirmation phrase (slice 10b).
- Emits `recovery_backoff_started` on failure.
- All restart preconditions now available from slices 8 + 10a + 10b.

### Slice 11 (done): `restart_backend` trigger action

`restart_backend` added to `POST /qz/recovery/trigger`. First dangerous recovery action.

**Constants updated:**
- `DANGEROUS_TRIGGER_ACTIONS = frozenset({"restart_backend"})`
- `IMPLEMENTED_TRIGGER_ACTIONS = SAFE_TRIGGER_ACTIONS | DANGEROUS_TRIGGER_ACTIONS`
- `UNIMPLEMENTED_TRIGGER_ACTIONS = ALLOWED_RECOVERY_ACTIONS - IMPLEMENTED_TRIGGER_ACTIONS`
  (start_backend, reload_selected_model, select_model remain 409)

**Gates for restart_backend (in order):**
1. `QZ_RECOVERY_ACTIONS=1` → 403 `authority_disabled` if not
2. Local/loopback request when `QZ_RECOVERY_BIND_LOCAL_ONLY=1` → 403 `non_local_request`
3. `reason` required → 400 `missing_reason`
4. `QZ_RECOVERY_CONFIRM_PHRASE` must be set → 400 `bad_confirm` if env unset
5. `body.confirm` must exactly match phrase → 400 `bad_confirm` if missing/wrong
6. Not already in progress → 423 `recovery_in_progress`
7. Backoff not active → 429 `backoff_active`
8. Planner feasibility check → 409 `plan_not_feasible` if blocked
   (active_requests > 0 and force=false → blocked; force=true overrides)
9. Execute via `BackendClient.restart_container(context_size)` — Docker details stay in qz_backend.py
10. Success → HTTP 200 `qz.recovery.trigger.v1` with `accepted=true`, `pre_status`, `post_status`
11. Failure → HTTP 500 + `rs.mark_failed()` + backoff set + `recovery_backoff_started` emitted

**`_do_restart_backend()` helper:**
- Gets context from `model_router.backend_context_length()` (with env fallback)
- Calls `backend.restart_container(context_length)` — synchronous, waits for health
- Resets `handler.model_load_state = "idle"` on success
- Returns `(False, error)` on any exception — never raises

**`_confirm_phrase_matches` for dangerous actions:**
- `QZ_RECOVERY_CONFIRM_PHRASE` must be set (rejecting even if confirm provided but env unset)
- `body["confirm"]` must exactly match (case-sensitive)
- Safe actions always bypass phrase check (unchanged)

**Telemetry events:** same as safe actions plus `recovery_backoff_started` on failure.

**Tests:** `tests/test_qz_recovery_trigger.py` — 92 assertions across 18 test classes.

### Slice 12 (done): `reload_selected_model` trigger action

Added `reload_selected_model` to `POST /qz/recovery/trigger`.

**Constants updated:**
- `DANGEROUS_TRIGGER_ACTIONS = frozenset({"restart_backend", "reload_selected_model"})`
- `IMPLEMENTED_TRIGGER_ACTIONS = SAFE_TRIGGER_ACTIONS | DANGEROUS_TRIGGER_ACTIONS`
- `UNIMPLEMENTED_TRIGGER_ACTIONS` — start_backend, select_model remain blocked (409)

**Operator flow:**
1. `restart_backend` — restarts Docker container; backend healthy but model unloaded
2. `reload_selected_model` — loads the currently selected model into the running backend

These are two separate trigger actions. `restart_backend` does NOT auto-load.
`reload_selected_model` does NOT restart the container.

**Gates for reload_selected_model (same as restart_backend):**
- `QZ_RECOVERY_ACTIONS=1` → 403
- Local request (when `QZ_RECOVERY_BIND_LOCAL_ONLY=1`) → 403
- `reason` required → 400
- `QZ_RECOVERY_CONFIRM_PHRASE` must be set (dangerous action) → 400 `bad_confirm`
- `body.confirm` must exactly match → 400 `bad_confirm`
- Not in progress → 423
- Backoff not active → 429
- Planner feasibility → 409 `plan_not_feasible`
  - blocked when `backend_state == unreachable`
  - blocked when `active_requests > 0` and `force=False` (force overrides)
- Selected model pre-check → 409 `selected_model_missing` if no model configured
- Execute → HTTP 200 success / HTTP 500 action_failed

**New helpers:**
- `_selected_model_for_reload()` — resolves backend_id from router selected entry
- `_do_reload_selected_model()` — calls `load_backend_model(model_id, wait=True)`;
  checks `model_load_state == "ready"` for success; does not restart backend

Tests: `tests/test_qz_recovery_trigger.py` — 124 assertions across 25 test classes.

### Slice 13 (done): `scripts/qz-smoke-recovery` smoke harness

Added `scripts/qz-smoke-recovery` — a repeatable smoke script for the recovery API.

**Default mode (no dangerous flags):**
- Required commands check (curl, jq, python3)
- Proxy and catalog readiness via `qz-wait-ready`
- GET /qz/recovery/status: schema, service_status, runtime_state, active_requests
- GET /qz/control-plane: schema, service_status, recovery, recovery.active_requests
- POST /qz/recovery/plan refresh_catalog: schema, action, feasible
- POST /qz/recovery/plan restart_backend: schema, action, would_interrupt_requests
- Authority detection (probe clear_failure → detects 403 vs 200)
- If authority disabled: asserts 403 authority_disabled + schema
- If authority enabled: bad_confirm/start_backend blocked/select_model blocked/clear_failure ok + refresh_catalog

**Dangerous flags (never run by default):**
- `--allow-restart`: runs restart_backend trigger (interrupts backend)
- `--allow-reload`: runs reload_selected_model trigger
- `--allow-restart-and-reload`: runs restart then polls + reload then verifies control-plane

**Artifacts:** `/tmp/qz-recovery-smoke-*.json`

**Usage:**
```bash
# Default safe mode
scripts/qz-smoke-recovery

# With authority enabled
QZ_RECOVERY_ACTIONS=1 QZ_RECOVERY_CONFIRM_PHRASE='phrase' scripts/qz-proxy
scripts/qz-smoke-recovery --confirm 'phrase'

# Explicit restart smoke (interrupts backend)
scripts/qz-smoke-recovery --confirm 'phrase' --allow-restart

# Full recovery flow
scripts/qz-smoke-recovery --confirm 'phrase' --allow-restart-and-reload
```

### Slice 13b (done): Harden recovery smoke timeout handling

**`scripts/qz-smoke-recovery` changes:**
- `POST_TIMEOUT` (default 15 s) for ordinary checks; `DANGEROUS_POST_TIMEOUT` (default 300 s) for restart/reload
- New `--post-timeout N` and `--dangerous-timeout N` CLI options; env overrides via `QZ_SMOKE_POST_TIMEOUT` / `QZ_SMOKE_DANGEROUS_POST_TIMEOUT`
- New `_post_json_ext URL body outfile timeout stderr_file` helper with explicit timeout + stderr capture
- All dangerous trigger calls now use `_post_json_ext` with `DANGEROUS_POST_TIMEOUT`
- New `_capture_dangerous_timeout_diagnostics LABEL` — on HTTP 000, captures `/qz/recovery/status` + `/qz/control-plane` and prints diagnostic summary
- HTTP 000 now prints: `action may have completed after client timeout; increase --dangerous-timeout`
- If post-timeout control-plane shows `model_state=loaded`, prints specific hint about increasing timeout
- Artifacts extended: `*.curl.err` for curl stderr, `*-after-000.json` for post-timeout diagnostics

**`proxy/quantzhai_proxy.py` `_send_json` change:**
- `BrokenPipeError` and `ConnectionResetError` now caught instead of propagating
- On disconnect: logs to `latest-send-json-disconnect.txt` via `runtime_log`, sets `close_connection = True`
- Normal responses unaffected; other exceptions still propagate
- Eliminates noisy BrokenPipeError traceback spam when curl times out mid-response

### #49 (done): `select_model` trigger action

Added `select_model` to `POST /qz/recovery/trigger`. All 6 recovery actions now implemented.

**`select_model` semantics (conservative Option A):**
- Changes the active model selection only — does NOT load the model
- Operator workflow: `select_model` → (optionally) `reload_selected_model`
- Does NOT call Docker, start/restart backend, or trigger load

**Manual service-control chain:**
1. `start_backend` — start/check backend container
2. `select_model` — select desired model
3. `reload_selected_model` — load it into the running backend
4. `restart_backend` — if container needs replacement

**Request body:** `action`, `model` (required), `reason`, `confirm`, `force`

**Gates:** same as other dangerous actions — authority, locality, phrase, not in progress, backoff, planner.

**`_do_select_model(model)`** implementation:
- `catalog.resolve(query=model)` for lookup (no backend call)
- Sets `catalog.selected = selected` + `catalog.reason`
- Calls `router._persist_model_state(selected, ...)` — no load, no backend
- Returns `(True, "")` or `(False, error)`

**Pre-checks before `mark_started`:**
- `catalog.resolve()` must find the model → 409 `model_not_found` if absent
- `profile_valid` checked → 409 `model_invalid_profile` if false
- Catalog unavailable → 409 `catalog_unavailable`

**Planner updates:** `select_model` added to `_REQUIRES_AUTHORITY`, `_REQUIRES_CONFIRMATION`, `_BACKOFF_APPLIES`, `_BLOCKED_BY_IN_PROGRESS`. Does NOT interrupt active requests.

**Smoke script:** default checks now show `plan/select_model` (feasibility check, no trigger). `--allow-select-model MODEL` runs the actual trigger.

**`UNIMPLEMENTED_TRIGGER_ACTIONS` is now empty** — all 6 actions implemented.

**Tests:** `tests/test_qz_recovery_trigger.py` — 172 assertions; `tests/test_qz_recovery_plan.py` updated.

### #48 (done): `start_backend` trigger action

Added `start_backend` to `POST /qz/recovery/trigger`. Non-destructive backend start.

**`start_backend` vs `restart_backend` vs `reload_selected_model`:**
- `start_backend` — creates or starts the container if absent/stopped; does NOT rm -f; does NOT load model
- `restart_backend` — destructive replacement (rm -f + run); does NOT load model
- `reload_selected_model` — loads selected model into already-running backend; does NOT touch container

**Gates:** identical to `restart_backend` — `QZ_RECOVERY_ACTIONS=1`, local request, `reason`, phrase required, not in progress, backoff, planner feasibility.

**`BackendClient.start_container(context_size)`** (new in `qz_backend.py`):
- Inspects container state via `docker inspect --format {{.State.Status}}`
- Running + healthy → returns success immediately (`already_running: true`)
- Running but unhealthy → polls health
- Stopped/exited → `docker start <container>` + polls health
- Missing → `docker run` with `_backend_launch_args()` + polls health
- Does NOT `rm -f` (use `restart_container` for destructive replacement)
- Helper `_inspect_container_state(container) → "running|stopped|missing|unknown"`

**`_do_start_backend()`:** gets context from model router, calls `backend.start_container()`, updates `model_load_state=idle` on success.

**Planner:** `start_backend` feasible when `backend_state=unreachable`; blocked when healthy/unhealthy (use restart or start instead).

**Smoke:** `scripts/qz-smoke-recovery --allow-start` — triggers, asserts HTTP 200, verifies `backend.reachable=true`.

**Tests:** `tests/test_qz_recovery_trigger.py` — 145 assertions. `tests/test_qz_recovery_plan.py` extended. `start_backend` removed from `UNIMPLEMENTED_TRIGGER_ACTIONS`. `select_model` still blocked (409).

### #50 (done): Async recovery job model

Added async recovery job support for long-running actions (#50).

**New module:** `proxy/qz_recovery_jobs.py`
- `RecoveryJobStore` + `RECOVERY_JOBS` singleton
- Schema: `qz.recovery.job.v1` / `qz.recovery.jobs.v1`
- Job states: `queued → running → completed | failed`
- Thread-safe, in-memory, non-durable, max 50 jobs (pruned)

**New route:** `GET /qz/recovery/jobs/<request_id>`
- HTTP 200 `qz.recovery.job.v1` when found
- HTTP 404 `qz.recovery.error.v1` `job_not_found` when unknown

**Async trigger:** `POST /qz/recovery/trigger` with `"async": true`
- Supported only for `reload_selected_model` (first async action; restart_backend stays sync)
- Returns HTTP 202 immediately with `qz.recovery.trigger.v1` + `"async": true` + `"job": {...}`
- Worker thread runs `_do_reload_selected_model()` and updates job store + recovery state
- `rs.mark_started()` called before thread starts → `recovery_in_progress=true` immediately
- Concurrent trigger requests get 423 while worker is running

**Error:** `async=true` for unsupported actions → HTTP 400 `async_not_supported` (checked early, before planner)

**Recovery status:** `GET /qz/recovery/status` now includes `"jobs"` snapshot.
**Control-plane:** `/qz/control-plane` `recovery` field includes `"jobs"` snapshot.

**`build_recovery_status` extended:**
- New optional param `recovery_jobs=None` — backward-compatible

**Smoke script:** `scripts/qz-smoke-recovery --allow-async-reload`
- POSTs `async=true` trigger, polls `/qz/recovery/jobs/<id>` until completed/failed/timeout

**Not yet async:** `restart_backend`, `refresh_catalog`, `clear_failure`.
**No automatic loops. No durable persistence.** In-memory only; resets on proxy restart.

### Future: durable state (requires #2 SQLite)

- Move attempt history and backoff tracking from in-memory to SQLite.
- Enables crash-loop detection across proxy restarts.
- Enables `recovery_attempt_count` to survive backend restarts.

---

## 13. Update to `docs/backend-service-recovery-semantics.md`

The controlling doc (`backend-service-recovery-semantics.md`) Slice 5 entry should
be updated to mark this design pass as done and point to this file.

See the update made in the same commit as this document.

---

## Cross-references

| Reference | Relevance |
|---|---|
| `docs/backend-service-recovery-semantics.md` | Taxonomy, enums, classification matrix, slices 1–4 |
| `docs/backend-control-plane-audit.md` | Historical data-flow audit; ownership table |
| `proxy/qz_recovery_status.py` | `qz.recovery.status.v1` builder — slice 4 |
| `proxy/qz_service_status.py` | `qz.service.status.v1` builder — canonical enums |
| `proxy/qz_control_plane.py` | Control-plane assembly; embeds service_status and recovery |
| `proxy/qz_request_router.py` | Existing `/qz/recovery/status` route — reference for future routes |
| `proxy/qz_model_router.py` | `model_load_state`, `load_backend_model()`, `restart_container()` |
| `proxy/qz_backend.py` | `restart_container()`, `get_health()` |
| `tests/test_qz_recovery_status.py` | Tests for the existing read-only builder |
| `#2` | Phase 1 SQLite — prerequisite for durable backoff/attempt tracking |
| `#6` | Backend VRAM telemetry — related backend observability |
| `#47` | This issue — stays open through slice 10 |
