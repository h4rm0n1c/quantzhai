# Bug: Zombie model slot — llama.cpp process dead but TurboQuant reports loaded

## Symptom

The proxy reports the model as loaded and healthy. Requests to `/qz/status`
show `selected_state: loaded`. TurboQuant's `/v1/models` endpoint reports
`"value": "loaded"` with valid args and preset. But actual inference requests
fail with connection refused or timeout.

Observed: kuato slot showed `loaded` on port 43127, but `curl http://127.0.0.1:43127/health`
returned exit code 7 (connection refused). The llama-server process for that
slot had died while TurboQuant's management layer retained stale loaded state.

## Root cause

TurboQuant manages multiple model slots and tracks their state internally.
When a llama-server subprocess dies unexpectedly (OOM, signal, crash), the
management layer may not detect or propagate the failure immediately. The
proxy trusts TurboQuant's reported state without independently verifying that
the slot's port is actually accepting connections.

## Impact

- All Codex sessions silently fail — requests time out or get connection errors
- The proxy continues reporting healthy status to monitors and `/qz/status`
- `qz-doctor` and `qz-top` may show no obvious problem
- Only detectable by attempting an actual generation request

## Fix direction

The proxy should independently verify liveness of the active model slot, not
just trust TurboQuant's metadata. Two approaches:

1. **Passive health check on request failure**: when a proxied request to the
   upstream fails with a connection error, the proxy should re-check the slot's
   actual port health before returning an error to the client. If the slot is
   dead, trigger a model reload/restart rather than returning a generic error.

2. **Periodic slot liveness probe**: the proxy's status polling loop already
   queries TurboQuant. Add a secondary probe that directly checks the active
   slot's port with a short timeout. If the slot's port is unreachable but
   TurboQuant reports it as loaded, mark `restart_required=true` and trigger
   a reload.

Option 1 is reactive and lower overhead. Option 2 detects the failure proactively
before the next request arrives. Both are better than the current silent failure.

## Workaround

Restart the stack:
```bash
./scripts/qz-down && ./scripts/qz-up
```

## Related

- `docs/edge-case-config-contract-plan.md` — proxy ownership of model setup
- `proxy/qz_model_router.py` — `restart_required` logic and health checks
