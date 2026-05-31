# Bug: Dual model load contract violation — two models in VRAM simultaneously

## Symptom

The proxy's `POST /qz/model/select-and-restart` can leave two models active
in the llama.cpp router simultaneously. `/v1/models` shows both `"loaded"` /
`"loading"` at the same time. VRAM is exceeded (17GB + 17GB > 26GB), causing
OOM and random HTTP 500 errors from the upstream server. The proxy reports
`selected_model_ready: True` for one model while the router still has the
previous model active.

## Root cause

Two synergistic bugs:

### 1. Proxy: unload-before-load race (`qz_request_router.py:_direct_mode_reload`)

The `_direct_mode_reload` method sends `unload_model_http()` then immediately
calls `load_model_http()` without waiting for the router to confirm the old
model is actually gone. The router's `POST /models/unload` returns
`{"success": true}` immediately, but the unload takes time (VRAM release,
child process cleanup). The new model's `POST /models/load` arrives before the
old model finishes unloading. Both models end up resident.

Additional sub-bugs that amplified the race:
- `get_loaded_model_ids()` only checked `loaded` state; models in `loading`
  state from an in-progress startup preload were not included in the unload
  set, so they were never sent an unload request.
- The return value of `unload_model_http()` was never checked.
- No retry mechanism: if the router ignored the first unload (which it
  sometimes does), no second attempt was made.

### 2. Server: `wait_until_loading_finished` has no timeout (`server-models.cpp`)

The server's `wait_until_loading_finished()` uses `cv.wait()` with a bare
predicate — no timeout. If the child process hangs during model load (CUDA
OOM, warmup crash, pipe buffer deadlock), the parent blocks forever on the
condition variable. The model stays permanently in `LOADING` state. The proxy's
`unload_model_http()` call to `POST /models/unload` can still kill the child,
but the `unload()` handler doesn't notify `cv_stop` in the LOADING path
(line 908-912), causing a secondary deadlock in the stopping thread that
prevents `update_status(UNLOADED)` from ever running.

## Impact

- All subsequent `select-and-restart` calls fail or produce contract violations
- Upstream returns HTTP 500 as the router OOMs
- The only recovery is `qz-down --force` (container kill)
- Benchmarking and model comparison are impossible while the router is poisoned

## Fix applied 2026-05-31

### Proxy fix (`qz_request_router.py:_direct_mode_reload`)

Three changes, all in the unload-before-load sequence:

1. **Check return value**: `unload_model_http()` result is now checked;
   a failed unload raises `RuntimeError` immediately.

2. **Poll until confirmed dead**: After unload, poll `get_active_model_ids()`
   up to 30s (per retry) waiting for the model to disappear from the router's
   `"loaded"` / `"loading"` states.

3. **Retry on timeout**: The unload-poll cycle runs up to 3 times. If the
   router returns `{"success": true}` but the model stays active, a second
   `unload_model_http()` is sent. Only after 3 retries (90s total) does the
   proxy give up — and raises `RuntimeError` instead of falling through to
   `load_model_http()`.

   Previously: one unload call, 60s poll, then load regardless.

4. **Use `get_active_model_ids()` instead of `get_loaded_model_ids()`**:
   `get_active_model_ids()` returns models in both `loaded` AND `loading`
   states. Models mid-startup-preload are now correctly included in the
   unload set.

### Server fix (`server-models.cpp:wait_until_loading_finished`)

Changed `cv.wait()` to `cv.wait_for(300s)`. If a model doesn't finish loading
within 300 seconds, the status transitions to `UNLOADED` and the caller
receives a timeout error instead of blocking indefinitely.

### Startup flag fix

Set `QZ_SPEC_DEFAULT=0` in the proxy environment. The `--spec-default` flag
enables ngram speculative decoding by default, which can conflict with MoE
models that have their own MTP draft heads. Removing it prevents one source
of model loading hangs.

## Acceptance criteria

After any `POST /qz/model/select-and-restart`:

```bash
# Must show 0 or 1 models in loaded/loading state
curl -s http://127.0.0.1:18084/v1/models | python3 -c "
import json,sys
d=json.load(sys.stdin)
active = [m for m in d.get('data',[]) 
          if m.get('status',{}).get('value') in ('loaded','loading')]
assert len(active) <= 1, f'Contract violation: {len(active)} models active'
print(f'OK: {len(active)} model(s) active')
"
```

## Related

- `proxy/qz_request_router.py` — `_direct_mode_reload()` (line 2716)
- `proxy/qz_backend_manager.py` — `load_model_http()`, `unload_model_http()`,
  `get_active_model_ids()`, `get_loaded_model_ids()`
- `tools/server/server-models.cpp` — `wait_until_loading_finished()` (line 977),
  `unload()` (line 901), monitoring thread (line 794)
- `tools/server/server-models.h` — `SERVER_MODEL_STATUS_LOADING`, `is_running()`
- `docs/model-ecosystem-research/download-list.md` — model download records
- `docs/bugs/zombie-model-slot.md` — related stale-state bug
