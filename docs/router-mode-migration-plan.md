# Router Mode Migration Plan

**Status**: Core migration complete. Remaining items are P2 cleanup (dead code, stale docs).
**Last updated**: 2026-05-29
**Tracking**: `docs/README.md` entry, AGENTS.md.

## Goal

Fully migrate QuantZhai from the "restart and load model via `-m`" mentality to llama.cpp's router mode (`--models-dir` + HTTP `/models/load` + `/models/unload`).

## Current State (post-migration)

Router mode is live and confirmed working. Seamless in-session model switches verified end-to-end:
- Container stays alive; no docker rm/run on model switch
- `load_model_http()` / `unload_model_http()` drive all model transitions
- One model loaded at a time (unload before load — VRAM constraint)
- GPU offload confirmed via `/v1/models` inventory check (not docker logs)
- Model selection persists across proxy restarts (`source="qz_codex"` in SELECTED_SOURCES)
- Hold-open unconditional for `/v1/responses` stream=True

The P0/P1 bugs from the original plan are fixed. P2 cleanup (dead code removal, stale doc text) remains deferred.

## Problem Layers

### Layer 1 (P0): False Success in Auto-Trigger

**`_auto_trigger_model_switch_nonblocking()`** (`qz_request_router.py:648-680`) can return `True` without actually triggering a switch.

The running/healthy branch only spawns `load_model_http()` if both `filename` is truthy and `load_model_http` is callable. If either condition fails, the thread is not spawned but the method proceeds to `_do_select_model(requested)` and returns `True`.

**Fix**: Track the return value of the conditional block. If neither `start()` was called nor `load_model_http()` was spawned, return `False`.

### Layer 2 (P1): Retrigger-Happy on Repeated Requests

The auto-trigger fires whenever `not request_matches_active` and `requested_key or requested_backend` is truthy and hold-open is enabled. There is no guard for "switch already in progress for this exact target."

**Fix**: Add a dedup guard — track the in-flight target model key/id and skip the auto-trigger if it matches the current pending target.

### Layer 3 (P0): Admission State False Negative

**`_request_admission_state()`** (`qz_model_status.py:249-279`) has a classification gap:

| State | `backend_phase` | `router_status` | Classification |
|-------|----------------|-----------------|----------------|
| Container running, model loading via HTTP | `healthy` | `"loading"` or `"unknown"` or `"unloaded"` | **`unavailable`** (fallthrough) |
| Container starting with old `-m` model | `running` | N/A | `loading` |

When `backend_phase == "healthy"` and `router_status != "loaded"`, the function falls through to `return "unavailable"`. Since `_is_terminal_responses_wait` treats `"unavailable"` as terminal, the hold-open loop collapses and returns 503 even when the model IS loading.

**Fix**: Add a branch: when `backend_phase == "healthy"` and `selected_identity` is set and `launch_model_path_basename` is set but `router_status != "loaded"`, return `"loading"`.

### Layer 4 (P1): Stale Operator Recommendations

**`_recommended_action()`** (`qz_model_status.py:299-303`) tells the operator to "POST /qz/model/reload or /qz/model/select-and-restart" when the selected and loaded models differ. But in router mode the router automatically triggers a switch from the request path, so this advice is confusing when the switch is already in flight.

**Fix**: Check `model_switch_state`. If it's `"loading"` or `"restarting"`, change the hint to "Model switch is in progress. Wait for completion." Only suggest manual reload if no switch is in progress.

### Layer 5 (P2): Multi-Read Race in Status Build

**`build_model_status()`** calls `get_models_status()` (a network call to the running llama-server) mid-function, after other reads (catalog, model state, BackendManager snapshot) and before remaining assembly. The status payload can be assembled from slightly different moments in time.

**Fix**: Snapshot the router status early, alongside other reads. Or document the limitation.

### Layer 6 (P1): Dead Code — `restart()`, `_do_restart()`, `_do_stop()`

`BackendManager.restart()` (`qz_backend_manager.py:397-429`) and `_do_restart()` (line 825) are no longer called by the model switch path. They still:
- Require `_launch_model_path_basename` (contradiction in router mode)
- Do `docker stop + rm + run` — unnecessary for HTTP model switching

**Fix**: Remove these methods. Fix `_do_restart_backend()` (the only remaining caller) to use `start()` + `load_model_http()`.

### Layer 7 (P0): Hold-Open Gated Behind Env Var

`_hold_open_loading_enabled()` (`qz_request_router.py:443-445`) gates all hold-open behavior behind `QZ_HOLDOPEN_LOADING`. In router mode, `/v1/responses` should never return 503 for model loading — the hold-open path is the only correct behavior.

**Fix**: Remove `_hold_open_loading_enabled()` and all `hold_open_loading` variable gates. Hold-open is unconditional for `/v1/responses`.

### Layer 8 (P2): Stale Documentation

Multiple docs still describe the old `-m` + container-restart approach as current reality.

### Layer 9 (P1): `_do_restart_backend()` Still Uses `mgr.restart()`

The recovery trigger action `_do_restart_backend()` (`qz_request_router.py:1512-1536`) still calls `mgr.restart()`. This is the only remaining caller of `restart()` in the model management flow.

**Fix**: Change to the same pattern as `_direct_mode_reload` — if backend is down, `mgr.start()`; if running, `mgr.load_model_http()`.

### Layer 10 (P2): `_TRIGGER_WARNINGS` Stale Text

`_TRIGGER_WARNINGS` (`qz_request_router.py:169-175`) references the old approach. `reload_selected_model` text says "restarted with -m".

**Fix**: Update text to reflect router-mode semantics.

## Execution Priority

| Layer | Priority | File(s) | Risk |
|-------|----------|---------|------|
| P3 (admission state) | **P0** | `qz_model_status.py` | High — false terminal state kills hold-open |
| P1 (false success) | **P0** | `qz_request_router.py` | Medium — silent no-op |
| P7 (hold-open gate) | **P0** | `qz_request_router.py` | Low — was already removed once |
| P2 (retrigger) | **P1** | `qz_request_router.py` | Low — dedup guard |
| P4 (stale recommendations) | **P1** | `qz_model_status.py` | Low — advisory text only |
| P6 (dead code) | **P1** | `qz_backend_manager.py` | Medium — callers must be updated |
| P9 (recovery action) | **P1** | `qz_request_router.py` | Medium — recovery path semantics |
| P5 (multi-read race) | **P2** | `qz_model_status.py` | Low — document or restructure |
| P10 (warnings text) | **P2** | `qz_request_router.py` | Low — text only |
| P8 (docs) | **P2** | Multiple `.md` files | Low — text only |

## Files to Change

| File | Changes |
|------|---------|
| `proxy/qz_backend_manager.py` | Remove `restart()`, `_do_restart()`, `_do_stop()`, `stop()`, `build_docker_stop_args()` if no external callers |
| `proxy/qz_request_router.py` | Fix `_auto_trigger_model_switch_nonblocking` return; add dedup guard; remove `_hold_open_loading_enabled()` and all gating; fix `_do_restart_backend()`; fix `_TRIGGER_WARNINGS` |
| `proxy/qz_model_status.py` | Fix `_request_admission_state` for `healthy` + `unloaded` → `loading`; fix `_recommended_action` for in-flight switches; snapshot router status early |
| `docs/backend-lifecycle-control-plane.md` | Update `-m` refs, restart descriptions |
| `docs/current-stocktake.md` | Same |
| `docs/current-task-hierarchy.md` | Same |
| `docs/backend-autostart-preload-bug.md` | Update "direct -m" semantics |
| `docs/proxy-model-selection-authority.md` | Update restart-based flow descriptions |
| `docs/end-to-end-smoke-plan.md` | Fix `-m` smoke check |
| `docs/client-facing-availability-and-503-minimisation-audit.md` | Remove `QZ_HOLDOPEN_LOADING` refs |
| `tests/test_qz_backend_manager.py` | Remove `restart()` tests, update `_do_start` tests |
| `tests/test_qz_request_router.py` | Update auto-trigger tests, hold-open tests |
| `tests/test_qz_model_status.py` | Update admission state tests |
| `tests/test_qz_model_endpoints.py` | Fix `_FakeBackendManager` |

## Completion Checklist

- [x] P0: Fix admission state false negative (`_request_admission_state` healthy+unloaded → loading)
- [x] P0: Fix auto_trigger_model_switch_nonblocking false success (dedup via `is_load_in_flight`)
- [x] P0: Remove QZ_HOLDOPEN_LOADING gate (hold-open unconditional)
- [x] P0: GPU offload detection via `/v1/models` inventory (not docker logs)
- [x] P0: Unload current model before loading new one (prevents dual-load VRAM exhaustion)
- [x] P0: Fix selection source poisoning (`_do_select_model` writes `qz_codex` not `recovery_select_model`)
- [x] P0 tests pass (35 admission+holdopen, 188 backend+model_status)
- [x] P1: Add dedup guard for in-flight model switch (uses `is_load_in_flight` + snapshot)
- [x] P1: Fix `_recommended_action` in-flight advice
- [x] P1 tests pass
- [ ] P2: Remove dead code: `restart()`, `_do_restart()`, `_do_stop()`, `_hold_open_loading_enabled()`
- [ ] P2: Fix `_do_restart_backend()` to use start/load pattern
- [ ] P2: Fix `_TRIGGER_WARNINGS` stale text
- [ ] P2: Update stale documentation (backend-lifecycle-control-plane.md, proxy-model-selection-authority.md)
- [x] All P0/P1 tests pass (`python3 -m pytest` — 4187 pass)
- [x] Live end-to-end: seamless in-session model switch confirmed 2026-05-29
