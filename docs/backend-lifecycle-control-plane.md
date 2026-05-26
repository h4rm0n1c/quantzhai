# Backend Lifecycle Control Plane

Date: 2026-05-21 (design) / 2026-05-26 (audit)
Issue: #65
Status: Slices A–D.3 COMPLETE. One gap remains: OperationalStore not wired to BackendManager (D.4).

---

## 0. Current implementation audit — 2026-05-26

**Audit HEAD:** 7217676 (post-#72)
**Tests:** 3629 passed
**#65 status:** OPEN. Slices A through D.3 complete in code. One wire-up gap remains.
**#72 relationship:** #72 fixed `/qz/status` ↔ `/qz/control-plane` readiness sync and
`backend_reasoning_budget` surfacing. BackendManager snapshot is already part of both
endpoints. #72 is closed; its fixes are reflected here.

---

### 0.1 Backend lifecycle ownership (current)

| Responsibility | Current owner | Source location |
|---|---|---|
| Starts backend Docker container | `BackendManager._do_start()` | `proxy/qz_backend_manager.py:651` |
| Stops/removes backend Docker container | `BackendManager._do_stop()` | `proxy/qz_backend_manager.py:760+` |
| Builds llama.cpp Docker command | `build_docker_run_args()` + `build_backend_args()` | `qz_backend_manager.py:449,490` |
| Starts proxy | `scripts/qz-proxy` | `scripts/qz-proxy` |
| Waits for proxy health | `scripts/qz-proxy` (blocks until `/health` answers) | `scripts/qz-proxy` |
| Waits for catalog readiness | Proxy init thread (`_initialize_proxy_state`) | `quantzhai_proxy.py:500+` |
| Waits for backend health | BackendManager health-check loop | `qz_backend_manager.py:680+` |
| Waits for model loaded/ready | BackendManager (internal) + `qz-down` status poll | `qz_backend_manager.py`, `scripts/qz-down:85-92` |
| Reports backend health | `GET /qz/backend/status`, `/qz/control-plane` | `qz_request_router.py:673`, `qz_control_plane.py:427` |
| Reports selected model | `/qz/control-plane`, `/qz/model/status` | `qz_control_plane.py`, `qz_model_status.py` |
| Reports backend reasoning budget | `/qz/control-plane` `profile.backend_reasoning_budget` | `qz_control_plane.py:388` (#72) |
| Reports readiness | `/qz/control-plane` `readiness` dict | `qz_control_plane.py:308` |
| Records lifecycle in OperationalStore | **NOT WIRED** | `BackendManager._emit()` exists (line 811) but `operational_store=None` — proxy main never passes the store |

---

### 0.2 #65 target coverage

| Target | Status | Notes |
|---|---|---|
| `qz-up` starts proxy only (no `docker run`) | ✅ DONE | `scripts/qz-up` ~100 lines; no Docker calls |
| Proxy owns backend container lifecycle | ✅ DONE | `proxy/qz_backend_manager.py` 822 lines |
| 9-state machine (disabled → idle → starting → running → healthy \| failed → stopping → stopped) | ✅ DONE | `PHASE_*` constants + transitions |
| `GET /qz/backend/status` endpoint | ✅ DONE | `qz_request_router.py:673` |
| `POST /qz/backend/start\|stop\|restart` endpoints | ✅ DONE | `qz_request_router.py:1555` |
| `/qz/control-plane` has `backend_manager` snapshot section | ✅ DONE | `qz_control_plane.py:427–434` |
| `qz-down` asks proxy to stop backend gracefully | ✅ DONE | `scripts/qz-down:74-92` |
| `qz-down --force` removes Docker container directly | ✅ DONE | `scripts/qz-down:58-67` |
| Proxy remains alive when backend fails | ✅ DONE | BackendManager sets `phase=failed`, proxy continues |
| Docker command fidelity (exact port of `qz-up` flags) | ✅ DONE | `build_docker_run_args` + `build_backend_args` |
| `scripts/qz-backend` thin wrapper (4 subcommands) | ✅ DONE | `scripts/qz-backend` |
| `qz-top` reads `backend_manager` fields from control-plane | ✅ DONE | `scripts/qz-top:378–419` |
| GPU offload gate (`QZ_REQUIRE_GPU`, log check) | ✅ DONE | D.1: `qz_backend_manager.py:559+` |
| Helper compat (no `-e`/`--device` flags) | ✅ DONE | D.2 |
| Log detection (CPU_Mapped + latest-signal-wins) | ✅ DONE | D.3: `qz_backend_manager.py:562` |
| D-smoke cold-start verification | ✅ DONE (unrecorded) | Issue comment "D-smoke complete — #65 closed"; never marked ✅ in design doc |
| OperationalStore records lifecycle events | ⚠️ PARTIAL | `BackendManager._emit()` implemented; `operational_store=None` in proxy — events are silently swallowed |

---

### 0.3 Stale assumptions from original #65 issue

| Original assumption | Current reality |
|---|---|
| `qz-up` is the init system (owns docker run, health loops) | `qz-up` only starts the proxy (~100 lines); proxy owns Docker |
| `qz-down` unconditionally force-removes container | `qz-down` has `--force`; normal path asks proxy gracefully |
| Cannot restart backend without shell access | `/qz/backend/restart` endpoint + `scripts/qz-backend restart` |
| Backend failure bricks the Codex session | `phase=failed` is observable; proxy keeps serving; operator can `POST /qz/backend/start` |
| `docs/backend-lifecycle-control-plane.md` header: "Slice A-design — design only" | Stale; slices A–D.3 are complete |

---

### 0.4 Remaining implementation slice

**D.4 — Wire OperationalStore to BackendManager**

`BackendManager._emit()` (line 811) is fully implemented: it calls
`self._operational_store.record_startup_event(phase=event_type, payload=...)`.
The constructor accepts `operational_store: Any = None`.
But `proxy/quantzhai_proxy.py` `main()` instantiates `BackendManager` without passing the store.

Fix (small, low-risk):

```python
# proxy/quantzhai_proxy.py main() — after existing _eint() helper
try:
    from qz_operational_store import OperationalStore as _OperationalStore
    _operational_store = _OperationalStore.from_env()
    _operational_store.init()
except Exception:
    _operational_store = None

_backend_manager = BackendManager(
    ...existing params...,
    operational_store=_operational_store,
)
```

Tests needed:
- When `operational_store` is passed, `_emit()` calls `record_startup_event` (already tested
  implicitly in `BackendManagerEmitTests` if they exist; otherwise add 1–2 wiring tests).
- Non-fatal: if `OperationalStore.init()` fails, `BackendManager` still starts.

This is the only remaining gap. All other #65 targets are complete.

---

## 1. Problem statement

`scripts/qz-up` currently acts as an init system:

- Unconditionally removes and recreates the backend Docker container (lines 84–86)
- Assembles all llama.cpp args and issues `docker run` (lines 88–130)
- Waits for proxy catalog readiness in a shell loop (lines 146–160)
- Polls backend `/health` in a shell loop (lines 168–206)

`scripts/qz-down` unconditionally force-removes the container regardless of proxy state.

This is backwards. The proxy is the control plane. The proxy already owns:
- OperationalStore runtime events/facts
- `/qz/control-plane` status surface
- `/qz/config/effective` config introspection
- Model routing, telemetry, search/retrieve tooling

Backend lifecycle — starting, stopping, health-checking the Docker container — belongs to the proxy, not shell scripts.

**Root consequence:** the stack cannot be updated (model flags, restart, reconfigure) without shell access. The proxy cannot restart a failed backend. A Codex session cannot ask the proxy to restart. Operators can only introspect state by running curl commands rather than through `/qz/control-plane`.

---

## 2. Target architecture

```
scripts/qz-up
  → scripts/qz-proxy (start proxy process, wait for /health)
  → return (backend starts asynchronously inside proxy)

proxy (quantzhai_proxy.py + qz_backend_manager.py)
  → QZ_BACKEND_AUTOSTART=1: immediately queue backend start
  → BackendManager.start(): docker rm -f + docker run + health-check loop
  → BackendManager state: idle → starting → running → healthy | failed
  → serves /qz/backend/status, /qz/backend/start, /qz/backend/stop, /qz/backend/restart
  → reports backend_manager section in /qz/control-plane

scripts/qz-down [--force]
  → normal path: POST /qz/backend/stop if proxy reachable, kill proxy pid
  → force path:  kill proxy pid + docker rm -f
  → if proxy unavailable normal: warn + kill proxy pid only
  → if proxy unavailable force: kill proxy pid + docker rm -f

scripts/qz-backend start|stop|restart|status  (thin curl wrapper)
```

---

## 3. BackendManager module — `proxy/qz_backend_manager.py`

### 3.1 Responsibilities

- Build the Docker command from env vars (exact port of qz-up lines 84–130)
- Start the backend container (`docker rm -f` if already present, then `docker run -d`)
- Stop the backend container (`docker stop` + `docker rm`)
- Restart the backend container (`stop` then `start`)
- Inspect container running/exited state via `docker ps -a`
- Check backend HTTP `/health` endpoint
- Maintain internal state machine (see §4)
- Expose state as a snapshot dict for API and telemetry
- Emit OperationalStore events (see §8)

### 3.2 Non-responsibilities

- No llama.cpp flag redesign
- No model selection changes
- No persistent retry/backoff state across proxy restarts
- No systemd unit management
- No model loading orchestration (that is the model router's domain)

### 3.3 Construction

```python
class BackendManager:
    def __init__(
        self,
        docker_cmd: str,           # e.g. "docker" or "sudo docker"
        container_name: str,       # QZ_CONTAINER
        image: str,                # QZ_IMAGE
        model_dir: str,            # QZ_MODEL_DIR
        server_host: str,          # QZ_SERVER_HOST
        server_port: int,          # QZ_SERVER_PORT
        backend_args: list[str],   # assembled from env vars (see §6)
        health_check_interval: float = 10.0,
        health_check_timeout: float = 120.0,
        operational_store=None,    # qz_operational_store.OperationalStore | None
        autostart: bool = True,
    ):
```

The proxy instantiates `BackendManager` in `main()` and stores it as a class-level attribute on `ProxyHandler`, the same pattern used for `model_catalog`, `telemetry`, etc.

### 3.4 Threading

`BackendManager` uses one background daemon thread (`_lifecycle_thread`). Operations (start, stop, restart) are serialised through an internal lock. The thread runs the health-check loop after a successful `docker run` and updates state.

---

## 4. State machine

### 4.1 States

| State | Meaning |
|---|---|
| `disabled` | `QZ_BACKEND_AUTOSTART=0`; manager exists but never starts anything |
| `idle` | Manager initialised; autostart pending or waiting for explicit call |
| `starting` | `docker run` issued; waiting for container to appear in `docker ps` |
| `running` | Container is running; polling `/health` |
| `healthy` | Container running AND `/health` returns 200 |
| `failed` | Last start/check attempt failed; `last_error` is set |
| `stopping` | Stop requested; waiting for `docker stop`/`docker rm` to complete |
| `stopped` | Container confirmed not present |
| `unknown` | State indeterminate (Docker unavailable, stale state, etc.) |

### 4.2 Timestamps and fields

```python
@dataclass
class BackendState:
    phase: str                        # one of the state names above
    container_name: str
    container_running: bool | None    # None = unknown
    backend_health_ok: bool | None    # None = not yet checked
    last_start_requested_at: str | None   # ISO8601
    last_started_at: str | None           # when container transitioned to running
    last_healthy_at: str | None           # last /health success
    last_stopped_at: str | None
    last_error: str | None
    autostart: bool
```

### 4.3 Transitions

```
disabled          → (operator calls start) → starting
idle              → (autostart fires)      → starting
                  → (start called)         → starting
starting          → (container running)    → running
                  → (error / timeout)      → failed
running           → (/health OK)           → healthy
                  → (container exited)     → failed
                  → (stop called)          → stopping
healthy           → (/health fails)        → running  (re-checking)
                  → (container exited)     → failed
                  → (stop called)          → stopping
stopping          → (container removed)    → stopped
failed            → (start called)         → starting
stopped           → (start called)         → starting
unknown           → (start called)         → starting
```

---

## 5. HTTP endpoints

### 5.1 New endpoints

```
GET  /qz/backend/status
POST /qz/backend/start
POST /qz/backend/stop
POST /qz/backend/restart
```

#### GET /qz/backend/status

Returns the current `BackendState` snapshot. Safe for operators and tools.

```json
{
  "phase": "healthy",
  "container_name": "qwen36turbo",
  "container_running": true,
  "backend_health_ok": true,
  "last_started_at": "2026-05-21T10:00:00Z",
  "last_healthy_at": "2026-05-21T10:01:30Z",
  "last_error": null,
  "autostart": true
}
```

No secrets. No Docker image contents. No model paths. No raw Docker command.

#### POST /qz/backend/start

Enqueues a start if not already running/starting. Returns immediately with accepted/already-running.

```json
{"ok": true, "action": "start", "phase": "starting"}
```

#### POST /qz/backend/stop

Enqueues a stop. Operator tool use.

```json
{"ok": true, "action": "stop", "phase": "stopping"}
```

#### POST /qz/backend/restart

Enqueues stop then start.

```json
{"ok": true, "action": "restart", "phase": "stopping"}
```

### 5.2 /qz/control-plane integration

`/qz/control-plane` gains a `backend_manager` section:

```json
{
  "backend_manager": {
    "phase": "healthy",
    "container_running": true,
    "backend_health_ok": true,
    "last_healthy_at": "2026-05-21T10:01:30Z",
    "last_error": null,
    "autostart": true
  }
}
```

The backend section does not expose the Docker command, image digest, or model paths.

---

## 6. Docker command preservation

`qz_backend_manager.py` must reproduce exactly the same Docker invocation as the
current `qz-up` lines 84–130. The `_build_docker_args()` method takes the env
vars via constructor params and returns the full `docker run` argument list.

### 6.1 Container flags

```python
docker_run_flags = [
    docker_cmd, "run", "-d",
    "--name", container_name,
    "--gpus", "all",
    "--cap-add", "IPC_LOCK",
    "--ulimit", "memlock=-1:-1",
    "-p", f"{server_port}:8080",
    "--mount", f"type=bind,src={model_dir},dst=/models,readonly",
    image,
]
```

### 6.2 Backend args

Constructed from env vars exactly as in qz-up:

```python
backend_args = [
    "-m", f"/models/{launch_model_path_basename}",
    "--host", "0.0.0.0",
    "--port", "8080",
    "-ngl", "999",
    "-c", str(context),
    "-np", str(parallel),
    "-b", str(batch),
    "-ub", str(ubatch),
    "-t", str(threads),
    "-tb", str(thread_batch),
    "-fa", "on",
    "--split-mode", "layer",
    "--tensor-split", tensor_split,
    "--main-gpu", str(main_gpu),
    "--kv-unified",
    "--reasoning", "on",
    "--reasoning-budget", str(reasoning_budget),
    "--reasoning-budget-message", reasoning_budget_message,
    "--cache-ram", str(cache_ram),
    "--cache-reuse", str(cache_reuse),
    "--mlock",
    "-ctk", kv_key,
    "-ctv", kv_value,
    "--metrics",
    "--reasoning-format", "deepseek",
]
if spec_default:
    backend_args.append("--spec-default")
```

`BackendManager` is constructed in `main()` by reading these same env vars. No flag values change in this design.

---

## 7. Startup semantics

### 7.1 Autostart

`QZ_BACKEND_AUTOSTART` defaults to `1`. When the proxy starts and `autostart=True`,
`BackendManager` enqueues a start after a short delay (e.g., 500ms) to let the
proxy's HTTP server bind first.

### 7.2 qz-up after redesign

```
scripts/qz-up
  1. mkdir -p var/logs var/run var/captures var/models
  2. write_runtime_state --phase requested --source env
  3. exec scripts/qz-proxy   (blocks until proxy /health responds)
  4. write_runtime_state --phase proxy-started --source env
  5. print "Proxy up: http://$QZ_PROXY_HOST:$QZ_PROXY_PORT"
  6. print "Backend starting asynchronously. Check: /qz/backend/status"
  7. print "Full status: GET /qz/control-plane"
  8. (optional: --hold flag calls qz-top as before)
  9. (optional: --codex-model flag calls qz-codex as before)
```

qz-up no longer:
- touches Docker
- calls `qz-wait-ready`
- polls backend `/health`
- blocks on catalog readiness (proxy handles this internally)

### 7.3 Backend failures

Backend start failure sets `phase=failed`, records `last_error`, emits a
`backend_failed` OperationalStore event. The proxy remains available. The operator
can retry via `POST /qz/backend/start` or `scripts/qz-backend start`.

---

## 8. qz-down redesign

### 8.1 New signature

```
scripts/qz-down [--force]
```

### 8.2 Normal path (no --force)

```bash
# 1. Try graceful backend stop through proxy
if proxy_reachable; then
    curl -sS -X POST http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/backend/stop
    # wait briefly for stopping phase
fi
# 2. Kill proxy pid
kill_proxy_pid
# 3. Do not docker rm -f
echo "QuantZhai stopped. Container may still be running; use qz-down --force to remove."
```

### 8.3 Force path (--force)

```bash
kill_proxy_pid
docker_rm_force "$QZ_CONTAINER"
echo "QuantZhai force-stopped."
```

### 8.4 Proxy unavailable

If the proxy is not reachable (was never started, or crashed):
- Normal path: warn + kill proxy pid only + print hint to use `--force`
- Force path: kill proxy pid + `docker rm -f`

---

## 9. `scripts/qz-backend` thin wrapper

One script, four subcommands. Less clutter than four scripts.

```
scripts/qz-backend start
scripts/qz-backend stop
scripts/qz-backend restart
scripts/qz-backend status
```

Each subcommand calls the corresponding `/qz/backend/*` endpoint using curl and
pretty-prints the JSON response. No Docker commands directly.

```bash
PROXY_URL="http://${QZ_PROXY_HOST}:${QZ_PROXY_PORT}"
case "$1" in
  start|stop|restart)
    curl -sS -X POST "$PROXY_URL/qz/backend/$1" | python3 -m json.tool
    ;;
  status)
    curl -sS "$PROXY_URL/qz/backend/status" | python3 -m json.tool
    ;;
  *)
    echo "Usage: qz-backend start|stop|restart|status" >&2
    exit 2
    ;;
esac
```

---

## 10. OperationalStore events

Use existing `OperationalStore.record_event()` pattern (no new schema).

| Event type | Trigger |
|---|---|
| `backend_start_requested` | Start enqueued (autostart or explicit) |
| `backend_starting` | `docker run` issued |
| `backend_started` | Container confirmed running in `docker ps` |
| `backend_healthy` | `/health` returned 200 |
| `backend_failed` | Start failed, container exited, or health-check timeout |
| `backend_stop_requested` | Stop enqueued |
| `backend_stopped` | Container confirmed removed |
| `backend_restart_requested` | Restart enqueued |

Payload fields: `container_name`, `phase`, `error` (on failure), `timestamp`.
No Docker command in any event payload.

---

## 11. Slice B test plan

### BackendManager unit tests

```text
test_build_docker_args_matches_qz_up_semantics
  BackendManager with default env → command list matches qz-up lines 88–130 exactly

test_spec_default_adds_flag
  QZ_SPEC_DEFAULT=1 → --spec-default in backend_args

test_start_issues_docker_rm_then_run
  mocked subprocess; confirms rm -f before run -d

test_start_failure_sets_failed_state
  docker run returns non-zero → phase=failed, last_error set

test_health_check_loop_advances_to_healthy
  mock /health returns 200 → phase transitions running → healthy

test_health_check_loop_container_exited_sets_failed
  docker ps returns empty → phase transitions running → failed

test_stop_calls_docker_stop_rm
  stop() → docker stop + docker rm confirmed

test_state_snapshot_no_secrets
  snapshot dict contains no model_dir, no docker command, no image

test_operational_store_events_emitted
  start/stop cycle emits all expected event types
```

### Endpoint tests

```text
test_backend_status_returns_safe_snapshot
test_backend_start_enqueues_start
test_backend_stop_enqueues_stop
test_backend_restart_enqueues_restart
test_control_plane_includes_backend_manager_section
test_backend_status_no_localhost_no_paths_no_secrets
```

### Script structural tests

```text
test_qz_up_does_not_contain_docker_run
  grep "qz_docker run\|docker run" scripts/qz-up → no match

test_qz_up_does_not_call_qz_wait_ready
  grep "qz-wait-ready" scripts/qz-up → no match

test_qz_up_does_not_poll_backend_health
  grep "backend_wait_seconds\|/health.*curl" scripts/qz-up → no match

test_qz_down_does_not_unconditional_rm
  grep "qz_docker rm -f\|docker rm -f" scripts/qz-down without --force path → no match

test_qz_down_force_path_calls_rm
  scripts/qz-down --force → docker rm -f confirmed
```

---

## 12. Cold-start smoke (Slice D)

From fully stopped state:

```
1. scripts/qz-up
   → returns after proxy /health (seconds, not minutes)

2. curl http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/backend/status
   → phase: starting | running | healthy

3. Poll /qz/backend/status until healthy or failed (max 2 min)
   → confirms BackendManager health-check loop works

4. curl http://$QZ_SERVER_HOST:$QZ_SERVER_PORT/health
   → backend is up and responding

5. POST /v1/responses with a simple prompt
   → confirms model inference works

6. web_search action=search with budget_mode=deep
   → confirms search still works end-to-end

7. scripts/qz-down
   → normal graceful path
   → confirms backend_stopped event in OperationalStore

8. scripts/qz-down --force (recovery)
   → confirms force path cleans up container
```

---

## 13. Non-goals

- No systemd unit
- No persistent retry/backoff state across proxy restarts
- No supervisor process outside the proxy
- No llama.cpp flag changes
- No model selection redesign
- No BrainCase writes
- No web_search/retrieve changes
- No Docker image changes

---

## 14. Slice roadmap

| Slice | Content |
|---|---|
| **A-design** | ✅ this document |
| **B1-impl** | ✅ BackendManager skeleton + Docker command builder + 49 tests |
| **B2-impl** | ✅ Lifecycle methods + proxy integration + /qz/backend/* endpoints + control-plane section; 71 new tests |
| **B3-impl** | ✅ qz-up stripped; qz-down graceful + --force; qz-backend wrapper; 32 structural tests |
| **B.1-audit** | ✅ 4 bugs fixed; docker_cmd documented; 5 new tests; 2929 pass |
| **C-doc** | ✅ Operator guide, QZ_DOCKER_CMD guidance, status URLs documented |
| **D-smoke** | ✅ Cold-start smoke confirmed via issue comment ("D-smoke complete — #65 closed"). Never marked ✅ in this doc — now corrected. |
| **D.1-gpu-fix** | ✅ GPU offload gate: post-health log check, QZ_REQUIRE_GPU/QZ_GPU_LOG_TAIL; docker args unchanged (helper-compatible); 2953 pass |
| **D.2-helper-compat** | ✅ Remove `-e`/`--device` flags added in D.1; they break qz-docker-root-helper (rc=126); restore original qz-up flag set |
| **D.3-log-detection** | ✅ Fix false cpu_fallback: CPU_Mapped + CUDA buffers == gpu; latest-signal-wins algorithm; 6 new tests |
| **D.4-ops-store-wire** | Wire OperationalStore to BackendManager in `proxy/quantzhai_proxy.py` main(). `BackendManager._emit()` is implemented; `operational_store=None` because proxy never passes it. Small fix + 1-2 wiring tests. |

---

## 15. Operator guide (C-doc)

### 15.1 Starting the stack

```bash
# Start proxy only. Backend starts asynchronously.
scripts/qz-up

# Start proxy + keep terminal attached via qz-top
scripts/qz-up --hold

# Start proxy + immediately open Codex
scripts/qz-up --codex-model <model-id>
```

`qz-up` returns as soon as the proxy answers `/health` (seconds). The backend
container is started by the proxy in the background. It will not be ready
immediately. Poll `/qz/backend/status` to check.

### 15.2 Checking backend status

```bash
# Via script wrapper (recommended)
scripts/qz-backend status

# Direct curl
curl http://127.0.0.1:18180/qz/backend/status

# Full stack status (proxy + catalog + backend + VRAM)
curl http://127.0.0.1:18180/qz/control-plane
```

Backend `phase` values:
- `idle` — manager ready; autostart pending or not yet triggered
- `starting` — `docker run` issued
- `running` — container up; `/health` check in progress
- `healthy` — container up AND `/health` OK
- `failed` — last operation failed; `last_error` set
- `stopped` — container removed

### 15.3 Backend lifecycle commands

```bash
scripts/qz-backend start    # POST /qz/backend/start
scripts/qz-backend stop     # POST /qz/backend/stop
scripts/qz-backend restart  # POST /qz/backend/restart
scripts/qz-backend status   # GET  /qz/backend/status
```

These are thin curl wrappers. They do not touch Docker directly.

### 15.4 Stopping the stack

```bash
# Graceful: proxy asks backend to stop, waits, then exits
scripts/qz-down

# Force: kill proxy pid + docker rm -f (use when proxy is unreachable)
scripts/qz-down --force
```

### 15.5 Environment variables

| Variable | Default | Notes |
|---|---|---|
| `QZ_BACKEND_AUTOSTART` | `1` | Set to `0` to disable automatic backend start at proxy launch |
| `QZ_BACKEND_STOP_TIMEOUT` | `15` | Seconds qz-down waits for backend to reach stopped/failed before killing proxy |
| `QZ_DOCKER_CMD` | `docker` | Docker command; see §15.6 |
| `QZ_REQUIRE_GPU` | `1` | Set to `0` to allow CPU fallback; with `1` (default), `phase=failed` if GPU offload is not confirmed from container logs |
| `QZ_GPU_LOG_TAIL` | `1000` | Number of log lines fetched when checking GPU offload after health passes |
| `QZ_BACKEND_MODEL_MODE` | removed | Deprecated compatibility only. Production launches are always direct `-m /models/<selected>.gguf`; `--models-dir` router mode is not supported. |

### 15.6 QZ_DOCKER_CMD guidance

`QZ_DOCKER_CMD` must be a simple space-separated command prefix.

**Valid forms:**
```bash
QZ_DOCKER_CMD=docker                                 # default
QZ_DOCKER_CMD="sudo docker"                          # sudo access
QZ_DOCKER_CMD="sudo -n /usr/local/sbin/qz-docker-quantzhai"  # wrapper helper
```

**Avoid:**
```bash
# Does NOT work — sg -c expects a single shell string, not split argv
QZ_DOCKER_CMD="sg docker -c"
```

If your shell lacks an active docker group, use `sudo docker` or install the
`scripts/qz-install-sudo-helper` wrapper and set `QZ_DOCKER_CMD` accordingly.
See `.env.example` for the supported patterns.

### 15.9 Direct backend model launch

`BackendManager` supports one production model-binding mode:

- **direct** — the container launches with `-m /models/<selected>.gguf`. llama-server binds to that single model. Model switching = `docker rm -f` + new `docker run` with a different `-m`.

Direct-mode safety gate: `_do_start()` refuses to launch when no `launch_model_path_basename` is set. The `_preload_last_model` flow resolves the persisted selection from `qz.model_state.v1` (or `QZ_MODEL_KEY` seed / catalog default) and calls `BackendManager.set_launch_model(...)` before `begin_autostart()`.

Direct-mode switch flow on `POST /qz/model/select-and-restart`:
1. Validate model in catalog + write `qz.model_state.v1` selection.
2. `mgr.set_launch_model(key, backend_id, path_basename)`
3. `mgr.restart()` → graceful stop, force-remove, new `docker run -m /models/<basename>`.
4. Poll `mgr.snapshot().phase` until `healthy` or `failed` (bounded by `QZ_MODEL_LOAD_TIMEOUT`).
5. Run `_record_load_observation` (log classifier + state update).
6. On classified failure: HTTP 409 + rollback to `last_good_*` per the post-Slice F recovery rules.

`POST /qz/model/reload` restarts the backend with the current persisted selection. `POST /qz/model/select-and-restart` is the canonical runtime mutation path: it persists selection and restarts the backend in one operation.

Legacy `/qz/models/select` and `/qz/models/load` are removed as runtime model-load APIs and return `410 Gone` with `Use /qz/model/select-and-restart.` `/qz/models/refresh` remains catalog metadata refresh only.

### Observability

`/qz/model/status` and `/qz/control-plane` `models` block now expose:

- `backend_model_mode` — compatibility field, always `direct`
- `launch_model_key`, `launch_model_backend_id`, `launch_model_path_basename` — what the next/last docker run was parameterised with
- `model_switch_state` — `idle | selecting | restarting | loading | loaded | failed | rolled_back`
- `active_load_operation` — `none | backend_restart | rollback_restart`
- `last_good_key`, `last_good_backend_id`, `failed_candidate_key`, `failed_candidate_backend_id`
- `selected_model_ready`, `request_admission_state`, and runtime death fields (`runtime_failure_*`, `backend_died_after_healthy`)

Operator hints fire on switch state, e.g. *"Model switch in progress — backend container is restarting with the selected model."*

### 15.8 GPU offload verification

After `phase=healthy`, the proxy confirms GPU offload by reading container logs.
BackendState exposes `gpu_offload_state` (one of `gpu | cpu_fallback | failed | unknown`)
and `gpu_error` (null on success).

When `QZ_REQUIRE_GPU=1` (default) and offload fails, the manager sets `phase=failed`
even though `/health` returned 200. HTTP health is not sufficient — GPU offload must
be confirmed.

Detection uses a "latest relevant signal wins" strategy across container log lines.

Hard failure patterns (trigger `phase=failed` when they are the last signal):
- `ggml_cuda_init: failed to initialize CUDA`
- `no usable GPU found`
- `--gpu-layers option will be ignored`
- `compiled without support for GPU offload`

GPU success patterns (trigger `phase=healthy` when they are the last signal):
- `offloaded N/N layers to GPU`
- `CUDA0 model buffer size` / `CUDA1 model buffer size`
- `CUDA_Host model buffer size`

`CPU_Mapped model buffer size` is **not** a hard failure by itself. llama.cpp
routinely maps a small host-side buffer even when the bulk of the model is on
GPU (e.g. 166 MiB CPU_Mapped alongside 7200 + 10324 MiB CUDA buffers).
`cpu_fallback` is returned only when `CPU_Mapped` is present with **no** GPU
success signal anywhere in the logs.

**Docker invocation compatibility**

`build_docker_run_args()` uses the same flag set as the original `qz-up`:
`--gpus all`, `--cap-add IPC_LOCK`, `--ulimit memlock=-1:-1`, `-p`, `--mount`.
No `-e` or `--device` flags are added. The `qz-docker-root-helper` allowlist
only permits the original flag set; `-e` and `--device` cause rc=126 before
the container launches.

If a future deployment needs explicit `-e NVIDIA_VISIBLE_DEVICES` or `--device`
passthrough, that requires a helper allowlist update first — not a BackendManager
change alone.

### 15.7 Waiting for backend after qz-up

`qz-up` now exits as soon as the proxy is ready, not when the backend is healthy.
If you need to gate a subsequent step on backend readiness:

```bash
scripts/qz-up
# Poll until healthy or failed
until [[ "$(scripts/qz-backend status 2>/dev/null | python3 -c \
  "import json,sys; print(json.load(sys.stdin).get('backend_manager',{}).get('phase',''))" \
  2>/dev/null)" =~ ^(healthy|failed)$ ]]; do
  sleep 2
done
scripts/qz-backend status
```

---

## Related

- `scripts/qz-up` — starts proxy only; proxy autostarts backend
- `scripts/qz-down` — graceful stop via proxy; `--force` for hard cleanup
- `scripts/qz-backend` — thin curl wrapper for `/qz/backend/*` endpoints
- `proxy/qz_backend_manager.py` — BackendManager module
- `proxy/quantzhai_proxy.py` — proxy entry point; BackendManager instantiated in main()
- `proxy/qz_operational_store.py` — event/fact store for backend lifecycle events
- `proxy/qz_control_plane.py` — control-plane section builder; includes backend_manager
- `docs/current-task-hierarchy.md` — active task DAG
