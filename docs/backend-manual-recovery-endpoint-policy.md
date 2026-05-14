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

### Slice 6: Pure recovery planning helper + tests

- Add `proxy/qz_recovery_plan.py` with `build_recovery_plan(service_status, action)`.
- Pure function. No I/O. No backend probes.
- Tests: `tests/test_qz_recovery_plan.py` covering feasibility, blocking conditions,
  action-specific logic.
- No endpoint yet.

### Slice 7: `POST /qz/recovery/plan` dry-run endpoint

- Wire `build_recovery_plan()` to a new route in `qz_request_router.py`.
- Returns `qz.recovery.plan.v1`. Always HTTP 200 or 400.
- No side effects. `force` flag parsed but ignored (documented).
- Tests: expand `test_qz_recovery_plan.py` with HTTP-level assertions.

### Slice 8: In-memory backoff and attempt tracking

- Add `proxy/qz_recovery_state.py` with in-memory attempt tracking.
- Fields: `last_recovery_action`, `last_recovery_started_at`, `recovery_attempt_count`,
  `backoff_until`.
- Expose via `GET /qz/recovery/status` as `backoff` sub-object.
- Tests: `tests/test_qz_recovery_state.py`.
- Still no state-changing endpoint.

### Slice 9: First safe state-changing action

- Implement `POST /qz/recovery/trigger` with `refresh_catalog` and `clear_failure` only.
- Apply `QZ_RECOVERY_ACTIONS` gate and `QZ_RECOVERY_BIND_LOCAL_ONLY` check.
- Emit `recovery_trigger_requested`, `recovery_action_started`, `recovery_action_completed`
  telemetry events.
- Do not implement `restart_backend` or `start_backend` yet.
- Tests: HTTP-level assertions for 403, 200/202, 400.

### Slice 10: `restart_backend` — only after slices 8 and 9 are stable

- Gated by `QZ_RECOVERY_ACTIONS=1`, confirmation phrase, and active request safety.
- Emits full telemetry including `recovery_backoff_started` on failure.
- Documents the gap if active request count is still unavailable.
- Requires `force=true` if request tracking is not yet implemented.

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
