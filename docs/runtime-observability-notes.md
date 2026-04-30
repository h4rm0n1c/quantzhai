# Runtime Observability Notes

Date: 2026-04-29

## What We Discovered

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
- Startup model warmup should target the selected backend model id, not the
  raw catalog filename, and skip a reload when the router already reports that
  model as `loaded` or `loading`.
- Small `max_output_tokens` caps can produce reasoning-only responses on this
  profile. That is model/profile tuning behavior, not a monitor failure, and it
  should be measured when comparing grug/caveman prompt variants.
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
- `scripts/qz-up` has convenience modes:
  - `--hold` starts the stack and then opens `qz-top`.
  - `--codex PROFILE` starts the stack and then launches Codex with the selected
    profile.
- `scripts/qz-thoughts` was added as a curses-style monitor for streamed
  thought/output activity and live backend state.
- `scripts/qz-thoughts` now uses proxy telemetry first, isolates the latest
  response window, and filters health/telemetry poll noise from its activity
  view.
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
