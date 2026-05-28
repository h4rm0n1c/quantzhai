# Client-Facing Availability and 503 Minimisation Audit

Status: **Active** — initial audit 2026-05-28; grounding tightened and terminal-failure guidance clarified 2026-05-28

---

## 1. Why this audit exists

QuantZhai is software people need to actually use. When the backend is loading, when a
model is switching, when the proxy is still initializing — those are *transitional* states.
They are not failures. Returning a hard 503 for transitional states is not accurate and
not acceptable as a product goal.

Too many current states still collapse into hard 503 responses when a better client
experience is possible. The Codex retry budget (default 5 stream retries) is finite. If
QuantZhai burns those retries during a normal loading window, the user sees a failure for
something that was never actually broken. That is pointless friction.

Minimising client-facing 503s is an explicit reliability goal, not a cosmetic one.

---

## 2. Truth sources consulted

### QuantZhai sources (working tree, 2026-05-28)

| File | What was read |
|---|---|
| `proxy/quantzhai_proxy.py` | Startup, initialization flow, `_handle_responses_compact`, `_send_json` |
| `proxy/qz_request_router.py` | All GET/POST route handlers, 503 gates, `/v1/responses` model-readiness check |
| `proxy/qz_model_status.py` | `_request_admission_state()`, `_recommended_action()` |
| `proxy/qz_backend_manager.py` | Full phase lifecycle (`PHASE_*` constants), `_do_start()`, GPU offload verification |
| `proxy/qz_service_status.py` | `_model_state()`, `_request_admission()` enums |
| `proxy/qz_model_router.py` | `status_snapshot()`, BackendManager fallback, `handle_ready_get()` |
| `scripts/qz-codex-common` | `qz_codex_exec_preflight()`, `poll_until_ready()`, `active_match()` |
| `docs/qz-codex-wrapper-contract.md` | Wrapper pre-flight contract, loading wait/poll |
| `docs/model-selection-and-compaction-correction-plan.md` | Model state authority, recovery rules |

### Codex sources (openai/codex, fetched via GitHub API, 2026-05-28)

| File | What was read |
|---|---|
| `codex-rs/responses-api-proxy/src/lib.rs` | Full proxy request forwarding — verbatim status pass-through |
| `codex-rs/codex-client/src/request.rs` | Request/Response types |
| `codex-rs/codex-client/src/transport.rs` | `execute()` and `stream()` — non-2xx handling |
| `codex-rs/codex-client/src/error.rs` | `TransportError`, `StreamError` |
| `codex-rs/codex-client/src/retry.rs` | `RetryPolicy`, `RetryOn`, `run_with_retry`, `backoff()` |
| `codex-rs/codex-client/src/sse.rs` | SSE stream parser, idle timeout, stream-close error |
| `codex-rs/codex-api/src/error.rs` | `ApiError` variants including `ServerOverloaded`, `Retryable` |
| `codex-rs/codex-api/src/api_bridge.rs` | `map_api_error()` — full HTTP-status-to-CodexErr mapping |
| `codex-rs/protocol/src/error.rs` | `CodexErr::is_retryable()` — authoritative retry decision |
| `codex-rs/model-provider-info/src/lib.rs` | `DEFAULT_STREAM_MAX_RETRIES=5`, `DEFAULT_REQUEST_MAX_RETRIES=4`, `retry_5xx=true` |
| `codex-rs/core/src/session/turn.rs` | Retry loop structure, `max_retries` origin |
| `codex-rs/core/src/responses_retry.rs` | `handle_retryable_response_stream_error()` |
| `codex-rs/core/tests/common/streaming_sse.rs` | Test SSE server — confirms POST `/v1/responses` as the only expected path |

---

## 3. North-star rule

Before returning a client-facing 503, QuantZhai should ask:

1. Can the request be accepted and worked right now?
2. Can the request be held open while work completes?
3. Can a retryable/in-progress payload be returned instead, preserving client retry budget?
4. Can the request degrade gracefully to a truthful fallback?
5. Only if none of the above apply: is 503 actually warranted?

A 503 that burns a Codex retry is a 503 that could have been something better.

---

## 4. Inventory of current client-facing availability surfaces

### `/v1/responses`

**What the client is trying to do:** Send a Responses API request to the model.

**Success path:** Request is forwarded to the backend, response (streaming or JSON) is returned.

**Current failure behavior:**

| Condition | Response | Notes |
|---|---|---|
| Proxy still initializing | 503 `{"error": "proxy initializing"}` | Transitional — retried by Codex |
| Model not found in catalog | 503 `{"error": "model not found"}` | **Wrong HTTP status** — see §7 |
| Model found, backend loading | 503 `{"error": "model not ready", "request_admission_state": "loading"}` | Transitional — retried by Codex, up to 5 retries |
| Backend start_requested / starting | 503 `{"error": "model not ready", "request_admission_state": "starting"}` | Transitional — retried |
| Hard backend failure | 503 `{"error": "model not ready", "request_admission_state": "failed"}` | **Terminal state burned through retries** |
| Profile backend missing (broken symlink) | 503 `{"error": "profile backend missing"}` | Recoverable with operator action |

**Assessment:** The loading/starting 503s will be retried by Codex (up to 5 times with
exponential backoff), but if the model takes longer than the retry budget, the user sees
a hard failure for something that was never actually broken. The terminal-failure case
also burns retries needlessly. The model-not-found case uses the wrong HTTP status.

---

### `/v1/responses/compact`

**What the client is trying to do:** Remote compaction of conversation history.

**Success path:** Returns 200 with compacted output (v3 LLM-based or v2 heuristic fallback).

**Failure behavior:** No startup guard. Always returns 200. Falls back to v2 heuristic when
no backend is available (backend_manager missing, phase not running/healthy, etc.).

**Assessment:** This is the best-behaved endpoint in the proxy. It never returns 503.
The v2 fallback is honest and useful. Other endpoints should learn from this pattern.

---

### `/qz/model/select`

**What the client is trying to do:** Record a model selection without triggering a backend restart.

**Success path:** 200 with current model status.

**Failure behavior:**
- Proxy not ready → 503
- Invalid/missing model field → 400
- Profile invalid → 400
- State persistence failure → 500

**Assessment:** 503 on proxy startup is acceptable here. Operators tolerate startup delays
for management endpoints.

---

### `/qz/model/select-and-restart`

**What the client is trying to do:** Select a model and trigger a backend restart.

**Success path:** 200 with model status (loading or ready).

**Failure behavior:**
- Proxy not ready → 503
- Load failure → 409 (good — not a 503)
- State write failure → 500

**Assessment:** The 409 on load failure is better than a 503. The proxy-not-ready 503 is
acceptable. This endpoint is operator-initiated.

---

### `/qz/model/reload`

**What the client is trying to do:** Reload the currently selected model.

**Success path:** 200 with model status.

**Failure behavior:**
- Proxy not ready → 503
- No selected model → 409
- Model not in catalog → 404
- Load failure → 409

**Assessment:** Good error differentiation. The 503 on proxy startup is acceptable.

---

### `/qz/model/status`

**What the client is trying to do:** Poll model/backend status, especially during loading.
This is the endpoint `qz-codex`'s pre-flight polling loop calls.

**Success path:** 200 with full status payload including `request_admission_state`.

**Failure behavior:**
- Proxy not ready → **503** `{"error": "proxy initializing"}`

**Assessment:** This is a bug. `/qz/model/status` is a polling/diagnostic endpoint.
During proxy startup, the wrapper polls this endpoint every 3 seconds. When it returns
503, `urllib.request.urlopen` raises an `HTTPError`, caught as `None` by the wrapper's
`fetch_status()`. The wrapper silently continues polling — it works, but it silently
discards useful state. More importantly: any future client that is less forgiving about
503 on a status endpoint will break.

**This endpoint should always return 200.** Return `{"ready": false, "request_admission_state": "starting"}` during proxy initialization instead of 503.

---

### `/qz/codex/client-config`

**What the client is trying to do:** Bootstrap Codex config/catalog.

**Behavior:** Always returns 200. No startup guard.

**Assessment:** Correct. No action needed.

---

### `/qz/models/refresh`

**What the client is trying to do:** Rescan the model catalog and update the Codex artifact.

**Success path:** 200 with catalog update result.

**Failure behavior:**
- Proxy not ready → 503

**Assessment:** Acceptable. This is an operator action; startup delays are expected.

---

### qz-codex wrapper launch/preflight path

**What the user is trying to do:** Launch a Codex session against the local proxy.

**Behavior:** `qz_codex_exec_preflight()` polls `/qz/model/status` every 3 seconds for
up to `QZ_CODEX_READY_TIMEOUT=300s`. It handles:
- `request_admission_state` in `{"starting", "loading"}` → wait and poll
- `request_admission_state` in `{"failed", "failed_gpu_not_available"}` → fail immediately
- `None` return from `fetch_status()` (including 503 responses) → continue polling

**Assessment:** The wrapper is resilient and well-implemented. The pre-flight wait is
the primary mechanism that prevents Codex from ever seeing a loading-state 503 for
normal startup. However, this is wrapper-side compensation for rough server-side behavior.
The proxy should not depend on the wrapper always being present or always doing the right
thing. An HTTPS client that talks directly to `/v1/responses` gets no such protection.

---

### Recovery trigger paths

**Read-only status endpoints** (`GET /qz/recovery/status`, `GET /qz/recovery`):
Always return 200. Errors are caught and embedded as structured payloads in the
response body. Correct design.

**Action endpoints** (`POST /qz/recovery/plan`, `POST /qz/recovery/trigger`,
`GET /qz/recovery/jobs/<id>`): Return a range of 4xx and 5xx for validation failures,
authority checks, feasibility blocks, backoff states, and execution errors.
`/qz/recovery/trigger` uses 400, 403, 409, 423, 429, 500, and 200/202.
`/qz/recovery/plan` returns 400 on body validation failures.
`/qz/recovery/jobs/<id>` returns 400 (missing ID) or 404 (job not found).

**Assessment**: No recovery endpoint returns 503 for transitional or loading states.
The 4xx/5xx on action endpoints reflect genuine errors or infeasibility, not service
unavailability. These endpoints are correctly designed and do not contribute pointless 503s.

---

## 5. Failure classes

| Class | Description | Is 503 correct? |
|---|---|---|
| A | Proxy still starting — catalog and policy loading in background | Sometimes acceptable: `/v1/responses` should use retryable semantics; `/qz/model/status` should use 200+not-ready |
| B | Backend loading — container starting or model loading into VRAM | Usually wrong: transitional, not a failure; request should be held open or expressed as retryable |
| C | Accepted model/backend switch in progress | Usually wrong: same as B; this is an accepted transition, not a failure |
| D | Backend temporarily unreachable or unhealthy but likely recoverable | Sometimes acceptable: if truly transient, retryable 503 with short Retry-After; if persistent, 503 is warranted |
| E | Catalog/config not yet loaded | Sometimes acceptable for management endpoints; 200+not-ready for polling endpoints |
| F | Hard terminal failure — backend crashed, GPU unavailable, VRAM exceeded | Currently 503 (same surface as classes B/C). **Wrong**: terminal failures should use a different mechanism so Codex does not waste retry budget |
| G | Invalid request — model not in catalog, bad model ID, bad parameters | Currently 503. **Wrong**: should be 4xx so clients know this is a client error, not a server error |

---

## 6. Codex compatibility audit

This section is grounded in Codex source. Claims are labelled by evidence confidence.

### How Codex handles non-2xx from `/v1/responses`

**Direct source evidence** (`codex-rs/codex-client/src/transport.rs`):
```rust
if !status.is_success() {
    let body = resp.text().await.ok();
    return Err(TransportError::Http { status, url, headers, body });
}
```
Any non-2xx status (including all 503s) is converted to `TransportError::Http` immediately.
No special handling. The raw status code and body are preserved.

### How HTTP 503 maps to CodexErr

**Direct source evidence** (`codex-rs/codex-api/src/api_bridge.rs`, `map_api_error()`):

503 with `{"error": {"code": "server_is_overloaded"}}` or `{"error": {"code": "slow_down"}}`:
→ `CodexErr::ServerOverloaded`

503 with any other body (including QuantZhai's current responses):
→ `CodexErr::UnexpectedStatus`

**This distinction is critical.** See the next section.

### What Codex retries

**Direct source evidence** (`codex-rs/protocol/src/error.rs`, `CodexErr::is_retryable()`):

| `CodexErr` variant | Retryable? | Triggered by |
|---|---|---|
| `UnexpectedStatus` | **YES** | Plain 503 (QuantZhai's current behavior) |
| `ServerOverloaded` | **NO** | 503 with `error.code = "server_is_overloaded"` |
| `InternalServerError` | **YES** | HTTP 500 |
| `InvalidRequest` | **NO** | HTTP 400 with error body |
| `Stream` | **YES** | Stream disconnected mid-response |
| `RequestTimeout` | **YES** | Timeout |
| `ContextWindowExceeded` | **NO** | Context full |
| `RetryLimit` | **NO** | Retry budget exhausted |

### Retry budget

**Direct source evidence** (`codex-rs/model-provider-info/src/lib.rs`):
- `DEFAULT_STREAM_MAX_RETRIES = 5` — per-turn stream retry budget
- `DEFAULT_REQUEST_MAX_RETRIES = 4` — per-request transport retry budget
- `retry_5xx: true` — 5xx errors are retried at the transport layer too

**Implication**: QuantZhai's current plain-503 responses are treated as `UnexpectedStatus`
which IS retried, up to 5 times with exponential backoff. This provides some resilience.
However, the retry budget is not infinite. A model load that takes 60–120 seconds will
exhaust the default retry budget. Once exhausted, the user sees a hard failure.

### The `server_is_overloaded` trap

**Direct source evidence**: `ServerOverloaded` is NOT retryable.

**Implication**: If QuantZhai ever emits `{"error": {"code": "server_is_overloaded"}}` in
a 503 response for a transitional state, Codex will not retry it at all. This would be
strictly worse than the current behavior. QuantZhai must not use this code for loading,
starting, or switching states.

### Retry-After header

**Inference (not directly evidenced in Codex source)**: Codex uses exponential backoff
defined in `codex-rs/codex-client/src/retry.rs` (`backoff()` function). There is no
evidence in the sources reviewed that Codex specifically reads `Retry-After` headers from
the server. Adding `Retry-After` to QuantZhai's 503 responses may help external clients
or future Codex versions but should not be relied upon for current behavior.

### SSE / streaming behavior

**Direct source evidence** (`codex-rs/codex-client/src/sse.rs`):
- Stream closed before `[DONE]` → `StreamError::Stream("stream closed before completion")`
- Idle for `idle_timeout` (default 300s) without a new SSE event → `StreamError::Timeout`
- These map to `CodexErr::Stream(msg, None)` which IS retryable
- The idle timer wraps each `stream.next()` call; it resets after each yielded data event

**Implication (strong inference — requires live test to confirm)**:
The `eventsource_stream` crate used by Codex follows the SSE spec: comment lines
(`: keepalive`) are consumed by the parser but are NOT dispatched as events. This means
SSE comments do NOT yield values from `stream.next()` and do NOT reset the Codex
idle timer. The 300s countdown continues regardless of comment traffic.

SSE comments DO maintain the underlying TCP/HTTP connection at the network layer,
preventing OS or intermediate proxy timeouts on an otherwise-idle socket. This is
necessary for a hold-open design but is distinct from resetting the application-level
idle timer.

**Practical implication for a hold-open design**: works when (a) keepalive comments
prevent TCP-level connection drops during the hold period, AND (b) the first real SSE
data event reaches Codex within 300s of the connection being established. Holding for a
maximum of 270s leaves a 30s margin before the Codex idle timer fires.

### Responses-API-proxy (the Codex-side proxy)

**Direct source evidence** (`codex-rs/responses-api-proxy/src/lib.rs`):
The proxy does simple verbatim forwarding. It does not filter or translate HTTP statuses.
Whatever QuantZhai returns, Codex's core client receives it as-is.

### What Codex sends as its only POST path

**Direct source evidence** (`codex-rs/core/tests/common/streaming_sse.rs`):
The test SSE server only handles `POST /v1/responses`. There is no evidence that Codex
sends requests to any other path. `/v1/responses/compact` is called by Codex for remote
compaction (confirmed by QuantZhai's existing handler and behavior).

---

## 7. Current obvious UX offenders

### Offender 1: Backend loading → hard 503, full stop

When the user runs `qz-codex` without the wrapper's pre-flight wait, or when any HTTP
client hits `/v1/responses` during a backend restart, they get:
```json
{"error": "model not ready", "reason": "selected model is not ready for direct backend launch"}
```
with HTTP 503. Codex will retry this up to 5 times. If the load takes longer than the
retry budget, the user sees a failure. The backend was never actually broken — it was
working on it. This is the worst offender.

### Offender 2: "Model not found" returns 503

When a client sends a request with an unknown model ID, they get HTTP 503. This is wrong.
503 means "service unavailable". The service is available; the model name is wrong. Codex
treats this as `UnexpectedStatus` and retries it. Each retry burns budget on a request
that cannot succeed regardless. The correct status is 400 or 404.

**Specifically**: returning HTTP 400 with a clear error body maps to
`CodexErr::InvalidRequest` which is NOT retried. Returning HTTP 404 still maps to
`UnexpectedStatus` (IS retried), so 400 is the correct choice for invalid model names.

### Offender 3: Hard terminal failure shares the same 503 surface as loading

A backend that failed to load (`request_admission_state: "failed"`) returns the same 503
as a backend that is loading. Codex cannot distinguish them. For a terminal failure, all
5 retries are wasted. The user sees a failure after ~30 seconds of pointless waiting
instead of immediately.

### Offender 4: `/qz/model/status` returns 503 during startup

The wrapper polls this endpoint. The wrapper's `fetch_status()` silently swallows 503
(via exception handling → returns None → continue loop). This works but is opaque.
The endpoint's job is to report state. Returning 503 from a state-reporting endpoint
is a category error.

### Offender 5: Internal split actions leaking into client-visible gaps

`select-and-restart` is a compound action: it writes model state, then triggers a backend
restart, then the backend goes through `start_requested → starting → running → healthy`.
During this transition, `/v1/responses` returns 503 immediately for any arriving request.
There is no "switching, please hold" state exposed to the client. The transition is
invisible except as a string of 503s.

### Offender 6: Wrapper-side waiting is the primary mitigation

The `qz-codex` wrapper correctly waits up to 300s for loading to complete before
launching Codex. This prevents most user-visible loading-state 503s. But it is wrapper-
side compensation. Any client that does not go through the wrapper, or any request that
arrives after the wrapper has already launched Codex (e.g., a model switch mid-session),
gets no such protection.

---

## 8. Better-than-503 options

### Class A/E: Proxy still starting

**Current**: 503 on most endpoints.

**Better options**:

1. **For `/qz/model/status`**: Always return 200 with `{"ready": false, "request_admission_state": "starting"}`. Cost: trivial code change. Confidence: high — works correctly with current wrapper polling.

2. **For `/v1/responses`**: The current behavior (503 → retried as `UnexpectedStatus`) is tolerable for brief startup. No immediate change needed, but adding `Retry-After: 5` would be good hygiene. Confidence: only inference that Codex reads this header; worth doing anyway for other clients.

3. **`/health` pattern**: Already correct. Return 200 with `"status": "initializing"` body. Other endpoints should learn from this.

### Class B/C: Backend loading or switching

**Current**: 503 → Codex retries ≤5 times → user fails if load exceeds retry budget.

**Better options** (ranked by impact):

1. **Hold request open with SSE keepalive** *(highest impact, moderate complexity)*:
   When `/v1/responses` arrives with the backend loading, instead of returning 503,
   open a streaming response, emit SSE keepalive comments every 15–20s, poll
   `BackendManager.snapshot()` internally, and forward the request to the backend
   once `phase == "healthy"`. The SSE idle timeout is 300s, which matches the existing
   wrapper wait budget.
   
   **Codex source** (direct evidence): SSE idle timeout = 300s
   (`DEFAULT_STREAM_IDLE_TIMEOUT_MS = 300_000`). Stream errors are retryable.

   **SSE comment behavior** (strong inference, not directly tested): SSE comment
   lines do not yield events in `eventsource_stream` and do not reset the 300s Codex
   idle timer. They maintain the TCP/network connection during the hold period.
   The 270s hold-open limit leaves a 30s margin before the idle timer fires.

   **QuantZhai design** (plausible, built on top of source evidence — not
   directly source-proven end-to-end): hold open up to 270s with keepalive comments;
   once the backend is healthy, forward the real request and start emitting SSE events.
   Codex sees one uninterrupted stream. "Seamless delayed forwarding" is a
   QuantZhai-side design choice — Codex source proves the 300s window exists and that
   stream errors are retried, not that the full hold-open design works end-to-end.

2. **Structured retryable payload with short Retry-After** *(low complexity)*:
   Return 503 but add `Retry-After: 5` and a body with `retry_suggested: true`.
   Benefit over current: external clients can act on the header.
   Codex behavior: inference only that Codex reads `Retry-After`; behavior is unchanged.
   Worth doing as a step forward.

3. **Do not use `error.code: "server_is_overloaded"`** *(zero-cost rule)*:
   QuantZhai must never emit `{"error": {"code": "server_is_overloaded"}}` for
   loading/transition states. Doing so maps to `CodexErr::ServerOverloaded` which is
   NOT retried. Current behavior correctly avoids this; document it as a hard rule.

### Class D: Backend temporarily unhealthy

**Current**: Backend health probe fails → 503.

**Better options**:
- Same "hold open and retry internally" approach as class B/C.
- For health-probe failures that resolve within ~30s, holding open is better than 503.
- For persistent health failures (>60s), escalate to a real 503 with operator hint.

### Class E: Catalog not ready

**Current**: 503 on most endpoints.
Same as class A — short startup window. The current behavior is tolerable. Add
`Retry-After: 5` header for good hygiene.

### Class F: Hard terminal failure

**Current**: Same 503 surface as loading states. Retried by Codex.

**Better options**:

There is no HTTP status combination that is both semantically correct AND stops Codex
retries for a terminal server-side failure. The two honest options:

**Option A: HTTP 503 with structured terminal body** *(monitoring/semantics purity)*
```json
{"error": "backend_failed", "terminal": true, "reason": "GPU unavailable",
 "request_admission_state": "failed"}
```
- HTTP 503 is semantically correct: this is a server-side failure, not a client error.
- `terminal: true` is a future-friendly payload hint for smarter clients and monitoring.
- **Codex behavior** (direct source evidence): `api_bridge.rs` does not inspect
  QuantZhai-specific JSON fields. A 503 with this body still maps to
  `CodexErr::UnexpectedStatus` which IS retried. All 5 retry attempts are burned.
  Codex cannot distinguish a terminal failure 503 from a loading-state 503.
- Tradeoff: correct semantics and monitoring value, at the cost of wasted retry budget.

**Option B: HTTP 400** *(stop wasted retries immediately)*
- `CodexErr::InvalidRequest` is NOT retried (direct source evidence).
- Semantically wrong for hardware or configuration failures (those are not client errors).
- Will confuse operators expecting 5xx for server-side failures.
- Tradeoff: saves retry budget; loses semantic correctness.

**No clean resolution with current Codex**: Option A is preferred for semantics and
future compatibility. Accept that Codex wastes retries on terminal failures until Codex
gains the ability to read structured failure hints. Document the known behavior so
operators understand why retries fire for a terminal state.

**Note**: `request_admission_state: "failed"` / `"failed_gpu_not_available"` is already
present in QuantZhai's 503 payload. The gap is not in the payload — it is that Codex
does not currently read QuantZhai-specific fields, so this information does not change
Codex retry behavior.

### Class G: Invalid request / bad model

**Current**: 503 → retried up to 5 times despite the model name being wrong.

**Better options**:

1. **Return HTTP 400 for unknown model names** *(direct source evidence)*:
   HTTP 400 → `TransportError::Http {status: 400}` → `CodexErr::InvalidRequest` →
   `is_retryable() == false`. Codex stops immediately instead of burning retry budget.
   
   This is the highest-confidence, lowest-risk fix in this audit. The model name is
   not going to become valid between retries.

2. **For broken profile symlinks**: These are recoverable with operator action.
   Keep the current 503 but add a clear `fix` field (already present in payload) and
   consider returning 503 rather than 400 since the issue is server-side.

---

## 9. Recommended client-facing availability contract

For every client-facing request to `/v1/responses`, QuantZhai should classify the situation as one of these states, in preference order:

| State | HTTP | Behavior |
|---|---|---|
| **ready** | 200 | Forward request, return response normally |
| **accepted and working** | 200 (streaming) | Hold open with SSE keepalives, forward once backend ready |
| **accepted and switching/loading** | 200 (streaming) | Same as above; emit progress events if possible |
| **retryable temporary unavailability** | 503 + `Retry-After` | Backend unreachable, expected to recover; include `retry_after_seconds` |
| **terminal failure** | 503 + `"terminal": true` | Hard failure; include `request_admission_state` and operator hint. **Note**: Codex still retries this (maps to `UnexpectedStatus`). `terminal: true` is monitoring/future value only — it does not stop current Codex retry waste. |
| **invalid request** | 400 | Unknown model, malformed body; Codex will NOT retry |

QuantZhai should reach state 5 (503 terminal) only after confirming the failure is
not transitional. States 2 and 3 are the current gap — they collapse into state 4 or 5
when they should be state 2 or 3.

For all other endpoints (`/qz/model/status`, `/health`, control-plane):
- Always return 200. Embed the actual state in the body. Never use 503 as the signal
  for "not ready yet" on diagnostic/polling endpoints.

---

## 10. Immediate implementation priorities

Ordered by user-facing impact and implementation confidence.

### Priority 1: Fix "model not found" from 503 to 400

**Where**: `qz_request_router.py`, the `selected_model is None` branch in `proxy_json_api()`.

**Change**: Return 400 (not 503) for unknown model names.

**Why**: HTTP 400 → `CodexErr::InvalidRequest` → NOT retried by Codex. Currently Codex
wastes up to 5 retries on a model name that will never resolve. Zero UX benefit.

**Risk**: Low. Any client that handled the current 503 needs to handle 400 instead. Both
are error responses. The body structure stays the same.

**Note**: Keep 503 for the broken-profile-symlink case since that is a server-side issue.

---

### Priority 2: Always return 200 from `/qz/model/status`

**Where**: `qz_request_router.py` at the `/qz/model/status` handler, lines 677–690.

**Change**: Remove the `_proxy_startup_ready()` guard that returns 503. Instead call
through to the model router, which already handles the not-ready case via
`_initializing_status_snapshot()` (returns a valid JSON payload with `ready: false`).

**Why**: This endpoint's job is to report state. 503 from a state-reporting endpoint
is a category error. The wrapper's polling loop silently handles it, but this is fragile.

**Risk**: Very low. The endpoint already has a not-ready fallback path in the model router.

---

### Priority 3: Define one graceful hold-open path for `/v1/responses` during backend loading

**Where**: `qz_request_router.py`, the `not active_ready or not request_matches_active` block.

**Change**: When `request_admission_state` is `"starting"` or `"loading"`, instead of
returning 503 immediately, hold the connection open with a streaming response. Emit SSE
keepalive comments every 15s. Poll `BackendManager.snapshot()` internally. When phase
reaches `"healthy"`, forward the original request. Timeout after 270s (just under the
Codex SSE idle timeout of 300s) and return 503 only then.

**Why**: This eliminates the most common loading-state 503. Model loads typically take
30–120s. The Codex SSE idle timeout is 300s. The window exists; use it.

**Codex source** (direct evidence): SSE idle timeout = 300s
(`DEFAULT_STREAM_IDLE_TIMEOUT_MS`). Stream errors are retryable. The idle timer resets
on each yielded data event.

**SSE comment behavior** (strong inference, not directly tested): SSE comments do not
yield events in `eventsource_stream` and do not reset the Codex idle timer. They maintain
the TCP/network connection during the hold period. The 270s limit leaves a 30s margin
before the idle timer fires for the first real backend event.

**Risk**: Medium. Requires careful connection management. The hold-open path needs proper
cleanup on client disconnect and error handling if the backend fails during the wait.
Start with a feature flag (`QZ_HOLDOPEN_LOADING=1`) before making it the default.

---

### Priority 4: Stop leaking internal split actions into dead gaps

**Where**: The `select-and-restart` → backend restart transition.

**Change**: When a model switch is in progress and a `/v1/responses` request arrives,
the hold-open path from Priority 3 handles this naturally. The request waits until the
new backend is healthy, then proceeds.

This is automatically resolved by Priority 3. No separate implementation needed.

---

### Priority 5: Add `Retry-After` to transitional 503s as stop-gap

**Where**: `_send_json` calls that emit 503 for loading/starting states.

**Change**: Add `Retry-After: 5` response header when returning 503 for transitional
states (classes A, B, C, E).

**Why**: Useful for external clients and monitoring. Does not break existing behavior.
May benefit future Codex versions or alternative clients.

**Risk**: Very low. Existing behavior is unchanged; only adds a header.

---

### Do not do

- **Do not use `{"error": {"code": "server_is_overloaded"}}` for transitional states.**
  This maps to `CodexErr::ServerOverloaded` which is NOT retried. It would be strictly
  worse than the current behavior for loading states.

- **Do not add wrapper-side waits as a substitute for Priority 3.**
  The wrapper wait is correct as a complement but must not be the only mitigation. Clients
  that bypass the wrapper get no protection.

- **Do not return HTTP 400 for broken-profile-symlink or hardware-failure cases.**
  These are server-side issues, not client errors. Keep 503 with clear operator hint.
