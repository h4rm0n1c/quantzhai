# Bug Report: P0 Backend Autostart / Preload Regression

Date: 2026-05-22
Symptom: Backend fails to autostart on proxy launch; BackendManager remains idle with empty launch model.

## Observed Evidence

- `scripts/qz-up` reports "Backend starting asynchronously under proxy control" (unconditional message).
- `GET /qz/model/status` reports:
    - `selected_key="default.gguf"`
    - `selected_source="status_snapshot"`
    - `configured_env_model="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-Q5_K_M.gguf"`
    - `launch_model_key=""`
    - `backend_phase="idle"`
    - `selected_model_ready=false`
    - `request_admission_state="unavailable"`
- `scripts/qz-backend status` reports:
    - `phase="idle"`
    - `container_running=null`
    - `last_start_requested_at=null`
    - `launch_model_key=""`
- `qz-top` reports:
    - `LOADED PROXY OFFLINE` (misleading label when backend is unreachable).
- `nvidia-smi` shows no VRAM usage by llama-server (container never started).

## Fault Line Analysis

1. **Idle BackendManager**: The `BackendManager` remains in `PHASE_IDLE`. This indicates that `begin_autostart()` was either never called or it returned early without transitioning to `PHASE_START_REQUESTED`.
2. **Empty Launch Model**: `launch_model_key` is empty in `BackendManager`. This proves `set_launch_model()` was never called with a valid model identity during startup.
3. **Preload Contract Failure**: `_preload_last_model()` in `quantzhai_proxy.py` is responsible for resolving the last selected model, calling `set_launch_model()`, and then `begin_autostart()`. If it fails to resolve a model, it returns early, leaving the backend idle.
4. **Authority Confusion**: `selected_key="default.gguf"` with `source="status_snapshot"` indicates that the proxy's in-memory model state was persisted by a status reconciliation check rather than a proper selection path. This often happens if a client hits `/qz/control-plane` or `/health` before the startup preload has completed.
5. **QZ_MODEL_KEY Ignored**: Despite `QZ_MODEL_KEY` being set in the environment, the proxy fell back to an alphabetical/default selection (or a stale persisted one), but still failed to promote that selection to the `BackendManager`.

## Root Cause Hypothesis

There is likely a race condition or a logic gap in `quantzhai_proxy.py` where:
- The proxy initializes and reports `ready=true`.
- `_preload_last_model` starts in a thread.
- A status request hits the proxy and triggers `status_snapshot()`.
- `_reconcile_status_state` sees a drift and writes a "default" selection to `var/model-state.json`.
- `_preload_last_model` reads this state but for some reason (perhaps catalog resolution failure) fails to call `set_launch_model`.
- Even if it doesn't fail, the sequence of operations between `ModelCatalog` initialization, `_preload_last_model`, and `ModelRouter` reconciliation is not sufficiently guarded.

## Resolution Plan

1. **Restore Startup Preload**: Ensure `_preload_last_model` correctly resolves the model (Persisted > Env Seed > Default) and ALWAYS sets the launch model if a valid one is found.
2. **Honest qz-up**: Update `qz-up` to only say "starting" if the backend manager is actually out of idle.
3. **Fix qz-top Labels**: Ensure "PROXY OFFLINE" is only shown when the proxy is truly down.
4. **Tighten Status/State**: Prevent "status_snapshot" from creating fake "loaded" states when no model is actually loaded in the backend.
5. **Improve qz-codex Preflight**: Ensure it checks active selection, not just catalog visibility.

---

## Follow-on Bug: Direct -m Loaded Model Not Observed

Date: 2026-05-22
Status: FIXED in commit following bf09e65.

### Symptom After Autostart Fix

After bf09e65, the GPU container starts and loads successfully (confirmed by
nvidia-smi VRAM, docker logs showing "offloaded 41/41 layers to GPU"), but:
- `backend_loaded_model=""` in `/qz/model/status`
- `selected_model_ready=false`
- `admission=rejected_model_not_loaded`
- qz-top shows `LOADED none`, `model=unloaded`
- qz-codex refuses because backend loaded model is `<none>`

### Root Cause

**Primary**: `_check_gpu_offload_from_logs()` in `qz_backend_manager.py` only
reads subprocess stdout from `docker logs`. The Docker daemon routes container-stderr
to client-stderr. llama-server writes ALL model-load diagnostics (including
"offloaded N/N layers to GPU" and "CUDA0 model buffer size") to **stderr**.
With only stdout being inspected, the GPU check sees no offload evidence → returns
`"unknown"` → after 5 retries → `"unknown_after_retries"` → `_gpu_blocking=True` →
`phase=FAILED`. With phase≠healthy, `_backend_loaded_model()` returns `""`.

**Secondary**: `selected_model_ready` in `qz_model_status.py` required
`state.last_load_result != "failed"`. In direct -m mode, BackendManager health IS
the load confirmation. A stale `last_load_result="failed"` from a previous run
must not block a currently-healthy backend.

**Tertiary**: `model_switch_state` didn't override to "loaded" when
`selected_model_ready=True`, so stale "idle" state appeared.

**Quaternary**: `_recommended_action` said "POST /qz/model/reload" during active
backend loading, when it should say "wait, loading in progress".

### Fix Summary

1. `qz_backend_manager.py`: Combine stdout+stderr in `_check_gpu_offload_from_logs`.
2. `qz_model_status.py`: Remove `last_load_result != "failed"` gate from
   `selected_model_ready` in direct mode.
3. `qz_model_status.py`: Override `model_switch_state` to "loaded" when
   `selected_model_ready=True`.
4. `qz_model_status.py`: `_recommended_action` detects loading phase, says wait
   not reload.
5. `quantzhai_proxy.py`: Normalize `selected_source="status_snapshot"` to
   `"fallback"` on proxy restart.
6. `scripts/qz-up`: Bounded 8s wait before printing "no launch model resolved".

### Direct -m Effective Loaded Model Semantics

In `backend_model_mode="direct"`:
- `backend_loaded_model` is populated from `launch_model_backend_id` when
  BackendManager `phase=="healthy"` and `backend_health_ok is True`.
- `selected_model_ready=True` requires: phase==healthy, health_ok, selected_identity,
  launch_matches_selected, not gpu_blocking, not launch_model_error.
  `last_load_result` is ignored (router-era artifact, not relevant to direct-m health).
- `backend_loaded_model_source="direct_launch"` when populated from BackendManager.
- During `phase=="running"` (loading): `request_admission_state="loading"` and
  recommended_action says "wait" not "reload".

### qz-up Startup Message

qz-up now waits up to 8 seconds for the backend phase to leave idle/start_requested
before printing the launch model message. This prevents the misleading
"no launch model resolved yet" message that appeared when reading stale status
immediately before the autostart daemon thread had run.
