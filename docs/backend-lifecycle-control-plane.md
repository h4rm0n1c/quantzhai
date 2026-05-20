# Backend Lifecycle Control Plane

Date: 2026-05-21
Issue: #65
Status: Slice A-design — design only.

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
    "--models-dir", "/models",
]
```

### 6.2 Backend args

Constructed from env vars exactly as in qz-up:

```python
backend_args = [
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
| **B-impl** | BackendManager module + proxy integration + new endpoints + qz-up/qz-down redesign + qz-backend script |
| **B.1-audit** | Compat, Docker command fidelity, state machine, telemetry |
| **C-doc** | Update README, docs/current-architecture-authority.md, operator guide |
| **D-smoke** | Cold-start smoke test (§12) |

---

## Related

- `scripts/qz-up` — current owner of Docker lifecycle (to be stripped)
- `scripts/qz-down` — current unconditional rm (to be made graceful)
- `proxy/quantzhai_proxy.py` — proxy entry point; BackendManager attached here
- `proxy/qz_operational_store.py` — event/fact store for lifecycle events
- `proxy/qz_config_report.py` — control-plane section builder; gains backend_manager
- `docs/current-task-hierarchy.md` — active task DAG
