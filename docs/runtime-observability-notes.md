# Runtime Observability Notes

Date: 2026-04-29

## What We Discovered

- When an outgoing `exec_command` function_call carries
  `sandbox_permissions: "require_escalated"` in its arguments, the proxy emits
  a `tool_escalation_requested` telemetry event before the call is forwarded.
  Payload includes `tool`, `call_id`, `sandbox_permissions`, `cmd_preview`
  (truncated to 80 chars), and `justification` (truncated to 200 chars).
  `qz-thoughts` renders this as an `escalation` activity row distinct from
  normal `tool` rows. This is the structured slice of issue #28 (Slice 1).
  Textual stderr classification (Slice 2) is deferred until a real denied-command
  capture is available.

- `qz-top` recent activity can fail silently if it asks the sudo Docker helper
  for more log lines than the helper allows. The helper boundary is
  `docker logs --tail <= 1000`, so monitor defaults need to stay inside that
  limit.
- Codex sandbox networking can produce false negatives for local proxy tests:
  a proxy started inside the sandbox may not be reachable from host curls, and
  host services may not be visible from sandboxed curls. Live stack validation
  should run `qz-proxy`, curl probes, `qz-top --once`, and `qz-thoughts --once`
  on the host network.
- Host live tests need the proxy and model server kept alive in background
  sessions; one-shot sandbox commands can die before probe commands finish.
- When validating live telemetry or throughput, keep `qz-up` running in a
  detached/background terminal and probe it from a separate shell. Do not rely
  on a one-shot sandbox launch to stay alive long enough for real requests.
- The local `/v1/responses` tool/search path now uses streamed upstream SSE for
  streaming requests. The non-stream path still buffers by design.
- Because the stream is now real on the streaming path, `qz-thoughts` can show
  live backend activity and streamed thought/reasoning text instead of only
  buffered captures.
- The proxy telemetry path works for streamed Responses SSE: a streamed
  `/v1/responses` request emits `sse_event` telemetry, `qz-top` reports the
  resulting throughput, and `qz-thoughts` can reconstruct the latest thought
  and answer without reading capture files.
- `qz-top` live throughput now comes from a dedicated `throughput_sample`
  telemetry event and the proxy's `latest_throughput` state, not from the
  recent request window. That keeps the dashboard stable when health/status
  polling is noisy.
- The proxy now emits a fresh `status_snapshot` telemetry event on `/ready`,
  `/qz/status`, and new `/v1/responses` requests, so monitors can see the
  current load/ready state without depending on stale request state.
- `/ready` and `/qz/status` reconcile `var/model-state.json` and
  `var/backend-state.json` from the live backend model inventory when available.
  Persisted state is a startup/fallback cache, not a higher-priority truth than
  the running server.
- `/qz/status` reports source/state metadata for context facts. Selected
  context is an intended value from catalog/env/defaults; backend context is
  confirmed only when live backend inventory reports it, otherwise cached or
  default. `restart_required_state` tells monitors whether the restart decision
  is confirmed or pending.
- When capture mode is enabled, `var/captures/latest-request-contract.json`
  records the request id, prompt contract schema, runtime metrics schema,
  requested model, selected backend, and prompt-policy summary for the latest
  normalized Responses request.
- `qz-codex` now prefers the model already loaded by the proxy at launch, then
  syncs Codex to that loaded backend model so startup does not clobber the
  current server state. If nothing is loaded yet, it falls back to the profile
  target.
- The proxy now persists the last selected model in `var/model-state.json` and
  uses it on startup to preload the most recent llama.cpp model before the next
  session arrives.
- Model switching now uses `QZ_MODEL_LOAD_TIMEOUT` end to end, so larger GGUF
  loads can finish before the launcher gives up and starts a session on the
  wrong backend.
- `QZ_CONTEXT` stays the base default for the backend process start. The proxy
  should treat per-model context as an exact override chosen before launch, not
  as a live `/models/load` setting.
- Startup model warmup should target the selected backend model id, not the
  raw catalog filename, and skip a reload when the router already reports that
  model as `loaded` or `loading`.
- Reasoning effort is the tuning knob for this profile. Hard response-token
  caps are stale Chat Completions-era tuning and should not be used to shape
  Codex reasoning behavior.
- Real live thought viewing needs a streamed Responses runtime that can pause
  at tool calls, execute local tools, append tool results, and continue with
  another streamed upstream request.

## What Changed

- `scripts/qz-top` now keeps log scanning inside the sudo helper's supported
  tail limit and surfaces log/helper failures as recent activity instead of
  making the stack look broken.
- `scripts/qz-proxy` starts the proxy in a detached session when possible, so
  the proxy survives after the launcher exits under command runners or terminal
  wrappers.
- `scripts/qz-up` now starts `qz-proxy` immediately after launching the backend
  container, before waiting for llama.cpp `/health`. Proxy control-plane routes
  such as `/health`, `/qz/config/effective`, `/qz/telemetry/recent`, and
  `/qz/status` are intended to answer while the backend is still loading or
  unavailable. If the backend health wait times out or the container exits,
  `qz-up` exits non-zero but leaves the proxy running for diagnostics.
- `qz-proxy` binds its HTTP listener before model catalog and search policy
  initialization. `/health`, `/qz/config/effective`, and telemetry routes expose
  `proxy_initialization` while startup work runs in the background; model and
  data-plane routes return a clear initializing 503 until the catalog is ready.
  The launcher prints `proxy listening` only after `/health` responds.
- `scripts/qz-up` has convenience modes:
  - `--hold` starts the proxy and then opens `qz-top` while backend readiness
    continues to settle.
  - `--codex PROFILE` starts the stack and then launches Codex with the selected
    profile.
- `scripts/qz-thoughts` was added as a curses-style monitor for streamed
  thought/output activity and live backend state.
- `scripts/qz-thoughts` now uses proxy telemetry by default, isolates the
  latest response window, and filters health/telemetry poll noise from its
  activity view. Raw capture replay is explicit with `--file`; the monitor no
  longer reads latest capture files as live truth.
- `scripts/qz-thoughts` now labels newer stream lifecycle telemetry explicitly:
  empty-answer repair start/done/failure, reasoning-only fallback, private
  tool-call aborts, stream fallback completion, web-search route details, and
  retained latest-completed events merged with recent telemetry. It also shows
  capture-mode hints from `/qz/config/effective` and marks proxy unavailable
  and reconnected states while the live telemetry stream reconnects.
- `scripts/qz-top` GPU rows now separate current VRAM `USED` from a per-GPU
  low-water `BASE` and live `DELTA`. `DELTA` is useful for cache/buffer pressure
  testing, but it is an approximation until the backend reports exact model,
  KV-cache, and scratch-buffer allocations.
- Remaining qz-top telemetry work: add proxy/backend `vram_snapshot` or
  `gpu_snapshot` events and surface them through `/qz/status` and
  `/qz/telemetry/recent`. The target split is current used, confirmed model
  base, confirmed KV/cache, confirmed scratch buffers, fallback delta, free,
  total, source, and confidence state.
- `README.md` documents the new launcher and monitor entry points.

## Roadmap Impact

- Multi-hop streamed Responses with tool-call continuation is now a first-class
  proxy roadmap item.
- Captures should become run-scoped so `qz-top`, `qz-thoughts`, benchmark runs,
  and proxy request logs can point at the same execution instead of fighting
  over latest-only files.
- The architecture split should include a testable streaming state machine,
  incremental capture writer, and fixtures for streamed tool continuation,
  buffered fallback, malformed events, and cancellation.
- Runtime monitors should eventually display search budget use, pages fetched,
  returned search tokens, cache hits, and exact run timestamps.
- Per-model context window support should live in the proxy orchestration path:
  compare the selected override to the running backend, restart the container
  only when the requested context changes, wait for `/health`, then send the
  model load request and drain any queued work.
- `QZ_CONTEXT` stays the base default for the backend process start. The proxy
  now reads per-model `runtime_context_length` from the live model catalog or
  override file, persists the current backend context in
  `var/backend-state.json` via `QZ_BACKEND_STATE_PATH`, and uses live backend
  inventory before persisted state when deciding whether a restart is needed.
- The restart decision belongs in the proxy, not in `llama.cpp` model loading.
  When the selected model's runtime context differs from the running backend,
  the proxy should stop the container, relaunch it with the chosen `-c` value,
  wait for health, then load the model and release queued work.
- Backend restarts must not be blind retries. If a startup fails, the proxy
  should surface the cause and stop rather than looping restarts on the same
  broken state.
- Agents should receive a stable current date/timezone anchor, with exact clock
  time fetched only when the task needs it, so time-aware work is grounded
  without destroying prompt-cache reuse.
- QuantZhai's concurrency target is single-user local performance, not
  multi-user serving. Researching Linux process schedulers and interactive
  scheduling patterns may still provide useful hints for prioritizing the
  foreground Codex session, proxy streaming, monitors, tool subprocesses, and
  backend inference without adding a complex application-level scheduler.
- Classic Mac OS cooperative multitasking may also be worth reviewing as a
  low-confidence analogy: not as an implementation model, but for ideas around
  explicit yielding, foreground task priority, and keeping a single-user
  interactive system responsive under constrained resources.

See also: `docs/agent-runtime-session-notes-2026-04-29.md`.
