# Proxy Capability Roadmap

Date: 2026-04-28

## Purpose

Map what the QuantZhai proxy currently covers for local Codex use, what is missing, and what needs to improve before the proxy becomes a more general local agent adapter.

This document is about the proxy specifically, not the Docker launcher, model build, or repo packaging.

## Current Job

The proxy sits between Codex and a local llama.cpp-compatible server. Its job is to make a local Qwen/TurboQuant backend look enough like the APIs Codex expects.

It currently covers:

- OpenAI Responses-style requests.
- Legacy Chat Completions proxying.
- Local model aliases and Qwen reasoning policies, with profile metadata
  driving effort prompts and sampler params.
- Basic Ollama-compatible discovery endpoints used by Codex setup paths.
- Streaming adaptation.
- Local compaction.
- Profile-aware `web_search`.
- Protocol adaptation for Codex `apply_patch` create, update, delete, and
  move/rename calls. Codex remains the filesystem writer.
- Capture files for debugging.
- Local terminal monitors for stack health, throughput, backend activity, and
  live Responses thought/output telemetry.

It is not yet a complete OpenAI API implementation, a complete Ollama implementation, or a general tool runtime.

## API Surface

Current endpoints:

- `GET /health`
- `GET /v1/models`
- `POST /v1/responses`
- `POST /v1/responses/compact`
- legacy `POST /v1/chat/completions`
- legacy `POST /chat/completions`
- Ollama compatibility probes such as `/api/tags`, `/api/version`, `/api/ps`, `/api/pull`, and `/api/show`
- Raw fallback proxying for other GET/POST paths

What works well:

- Enough compatibility for the current local Codex workflow.
- Model metadata and setup probes keep Codex from failing early.
- The proxy can hide llama.cpp response quirks from Codex.

What is weak:

- The compatibility surface is empirical, based on what the current Codex flow needed.
- It is not a versioned contract.
- Ollama support is a shim, not full Ollama behavior.

Maturity: working beta.

Current API direction:

- Responses is the primary compatibility target for Codex/OpenAI-style agent
  clients.
- Chat Completions routes are deprecated compatibility shims and pending
  removal after local clients are confirmed not to need them.
- New streaming/tool work should target Responses SSE, not legacy completion
  expansion.

## Model Aliases And Reasoning Policy

Current behavior:

- Exposes real GGUF model files from the model directory.
- Exposes profile entries only as symlinks under the model directory.
- Does not provide synthetic `QwenZhai-*`, `Qwen3.6Turbo-*`, or reasoning-budget
  alias model ids.
- Keeps low, medium, high, and xhigh profile reasoning choices aligned with the
  selected catalog entry's own reasoning policy metadata rather than the
  backend model filename.
- Applies Qwen-aware sampler defaults and compact effort prompts.
- Strips `thinking_budget_tokens`; per-request hard reasoning-token caps are
  not part of the Codex runtime policy.
- Uses `QZ_REASONING_BUDGET` for the llama.cpp server-side reasoning budget.

What works well:

- Simple local model names for Codex.
- Useful reasoning effort control without changing the backend manually.

What is weak:

- Model catalog and reasoning-policy metadata still need a formal schema.
- There is no per-backend override contract yet.

Maturity: stable enough for the current stack.

## Responses Adapter

Current behavior:

- Normalizes Responses input into upstream chat/completions-style payloads.
- Canonicalizes message roles and content.
- Drops historical reasoning and old tool-call artifacts that confuse the local model.
- Removes harness/meta blocks that should not be sent to Qwen.
- Normalizes supported tool declarations.

What works well:

- This is the core reason local Codex can run usefully.
- It cleans up a lot of traffic that would otherwise degrade local model behavior.

What is weak:

- The adapter is packed into one large Python file.
- The behavior needs fixture tests.
- Some compatibility choices are inferred from observed traffic instead of documented as a stable contract.

Maturity: useful beta.

## Streaming

Current behavior:

- Can pass through upstream SSE.
- Can transform reasoning visibility into raw, summary, or hidden modes.
- Can synthesize Responses-style stream events from non-streaming upstream responses.
- Emits local Codex rate-limit style events and headers.
- Correlates stream telemetry and request-scoped captures by request id.
- Classifies reasoning-only stalls and artifact/tool-shaped payloads that arrive
  only through the reasoning channel.
- Buffers executable public tool-call events until function-call arguments are
  complete.

What works well:

- Streaming is good enough for interactive local Codex sessions.
- Reasoning display control is useful when working with Qwen.
- Normal no-tool output and reasoning summary transforms have been live-audited
  with low parse-to-forward latency.

What is weak:

- Streaming transforms are fragile without replay fixtures.
- The local `/v1/responses` tool/search loop now streams upstream output for
  streamed requests, then pauses on a tool call, executes the local tool,
  appends the tool result, and continues streaming.
- The non-stream path still buffers when the client does not request SSE.
- Fake rate-limit metadata satisfies client expectations but is not real accounting.
- Some event shapes may lag behind Codex/OpenAI changes.

Maturity: working beta, needs regression tests.

## Streaming Discovery: Tool-Call State

Discovered during `qz-thoughts` work on 2026-04-29:

- The current `/v1/responses` local runtime needs a stronger public/private
  tool-call state contract when it manages local tool/search recursion.
- The proxy now keeps streamed SSE on the live path and uses captures only for
  debugging or replay.
- `scripts/qz-thoughts` can show streamed thought/output activity and live
  backend state from proxy telemetry when the stream path is active.
- The target is not "streaming or tools"; the target is streamed Responses with
  tool-call continuation.

Target runtime shape:

1. Forward the initial upstream request with streaming enabled.
2. Relay model deltas to Codex and capture them incrementally.
3. Accumulate output items and function-call arguments locally.
4. When a function call completes, execute the supported local tool.
5. Append the tool result to the conversation and issue the next streamed
   upstream request.
6. Continue until the final assistant response completes.
7. Present the chain to Codex as one coherent Responses lifecycle where the
   protocol allows it, with a buffered fallback for unsupported event cases.

Required details:

- Correct `response.output_item.*` ordering.
- Correct function-call argument delta/done parsing.
- No duplicated reasoning, message, or tool-call items between hops.
- No public runnable tool item before arguments are complete.
- No hidden/private tool call unless its required runtime state exists.
- Malformed empty tool-call history is filtered before upstream replay.
- Cancellation and client-disconnect handling: implemented for downstream
  stream write failures, with upstream cleanup and `client_disconnected`
  telemetry.
- Request-scoped captures for streamed requests when capture mode is enabled,
  with any future run-level grouping layered on top.
- Golden SSE replay fixtures for normal streaming, tool continuation,
  malformed events, and fallback buffering.

## Tool Handling

QuantZhai has two tool-handler classes:

- **Protocol adapters** translate between Codex's client-facing tool shape and
  the model-facing function-call shape Qwen can reliably emit. They do not
  execute the tool in the proxy. `apply_patch` is the current example: Codex
  remains responsible for workspace writes, sandboxing, approvals, and result
  history.
- **Proxy-local executors** translate the declaration, execute the tool inside
  QuantZhai, append private upstream continuation items, and expose a safe
  Codex-visible progress/result shape. `web_search` is the current example.

Rule: if Codex already provides a safe built-in execution path for a tool,
QuantZhai should prefer protocol adaptation and keep execution with Codex unless
there is a strong, documented reason to move execution into the proxy.

Current behavior:

- Normal function tools can pass through.
- `web_search` is implemented locally.
- Unsupported tools are dropped and recorded.
- Native and custom `apply_patch` declarations are translated into a model-friendly function schema.
- Valid model `apply_patch` function calls are translated back into native `apply_patch_call` items.
- If Codex does not explicitly declare native `apply_patch`, the proxy treats the tool as custom output style so the current CLI harness keeps working.
- Current Codex CLI custom `apply_patch` calls are translated back into `custom_tool_call` patch envelopes.
- Invalid model `apply_patch` calls become assistant error messages rather than leaking private JSON function-call arguments back to Codex.
- `apply_patch_call_output` history is translated back into function-call output history for llama.cpp.
- `write_stdin` is hidden from upstream unless prior request history contains a
  live exec session id.
- Empty-argument function-call history and matching parse-error outputs are
  dropped before replay to llama.cpp.
- Executable public function calls are buffered until arguments are complete.
- Streamed tool-call state and guard accounting now live behind
  `proxy/qz_tool_lifecycle.py`.
- Completed function calls now pass through the active proxy tool registry,
  which separates proxy-local execution from Codex-visible public tool items
  and applies protocol-adapter output conversion.
- Proxy-local tool continuation now returns an explicit split between the public
  item Codex sees and the private upstream replay items the model needs for the
  next hop.
- Tool adapters now expose `ToolLifecycleSpec`, covering execution mode,
  Codex-visible item type, telemetry name, continuation hop budget, and optional
  SSE lifecycle event stages.
- Streamed proxy-local lifecycle handling now asks the active registry for
  start/done lifecycle SSE chunks instead of hard-coding `web_search` event
  prefixes or stages.

Missing or incomplete:

- No proxy-side patch executor exists.
- Tool-call continuation does not yet have a documented state table or broad
  golden replay coverage.
- No shell/exec tool runtime.
- No computer-use tool runtime.
- No code interpreter runtime.
- No MCP/app tool bridge.
- The generic bridge exists for current tools and has fixture coverage for
  proxy-local multi-hop continuation, normal public function passthrough,
  protocol-adapted `apply_patch`, and proxy-local lifecycle telemetry.

What works well:

- Unsupported tool dropping is explicit enough to debug.
- `web_search` now has a real local implementation.
- The patch-tool protocol path is smoke-tested with fake upstreams and local Codex CLI.

What is weak:

- Tool handling has a shared contract, request normalization has an explicit
  module boundary, proxy-local lifecycle events are registry-owned, and capture
  latest/request-scoped writes now go through a runtime IO policy helper.
- There is no shared tool-call lifecycle for request normalization, execution, result injection, streaming, and capture.
- Tool execution and streaming share specs and proxy-local lifecycle chunk
  builders, but are not yet one state machine.
- The adapter/executor split is now the design rule, but not every code path is
  routed through that boundary yet.

Maturity:

- Function pass-through: partial.
- `web_search`: beta.
- `apply_patch`: alpha protocol adapter, smoke-tested.
- General tool runtime: missing.

Live smoke note:

- The Codex exec apply_patch smoke now uses a real SSE-shaped upstream response and includes a minimal usage block on `response.completed`, so the end-to-end temp-workspace edit path is verified against the current proxy contract.
- That hermetic Codex exec smoke also pins the public JSONL lifecycle Codex
  exposes for the patch handoff: `item.started file_change`,
  `item.completed file_change`, final `agent_message`, and terminal
  `turn.completed usage`.
- A direct live Qwen/TurboQuant Responses smoke on 2026-05-09 produced streamed
  `apply_patch` function-call arguments and the proxy returned a completed
  custom `apply_patch` envelope. Capture:
  `var/captures/requests/qz_req_1778256346716_c050`.
- A live `qz-codex exec` smoke on 2026-05-09 produced a completed
  `custom_tool_call` and Codex created
  `live-codex-apply-patch-smoke.txt` in the temp workspace. Capture:
  `var/captures/requests/qz_req_1778257008620_8190`.
- A later live `qz-codex exec -m prompt-compiler --json --ephemeral` smoke on
  2026-05-09 created `live-qwen-apply-patch-smoke.txt` through apply_patch.
  Codex JSONL exposed `item.started file_change`, `item.completed file_change`,
  final `agent_message` text `done`, and terminal usage. Matching proxy request:
  `qz_req_1778312516589_af70`.

## Search

Current behavior:

- Local `web_search` supports `search`, `open_page`, and `find_in_page`.
- SearXNG base URL is configurable.
- Policy-driven profiles exist for broad, coding, sysadmin, research, news, AI/model, and reference searches.
- Model overrides can select a search policy file and default policy profile.
- Low-result fallback routing exists.
- The latest route is captured under `var/captures/latest-web-search-route.json`.

What works well:

- Search is now useful enough for normal local-agent work.
- Profile routing avoids treating every search like a coding search.
- Debug captures make routing decisions inspectable.
- Local agent profiles can carry their own search scope without adding new
  public tool names.

What is weak:

- Ranking, dedupe, and source scoring are first-pass.
- The search code should eventually leave the monolithic proxy file.
- Smoke tests are manual.

Maturity: good enough beta, parked for now.

Next search direction from the 2026-04-29 tuning session:

- Do not simply raise the per-turn search-call limit.
- Add a budgeted search-packet mode where one `web_search` call can fan out
  internally to query variants, profiles, dedupe, ranking, page fetch, and span
  extraction.
- Return a compressed evidence packet to the model and store larger artifacts
  under run-scoped captures.
- Surface search call count, fanout count, pages fetched, returned tokens,
  cache hits, and budget use in runtime monitors.

See also: `docs/agent-runtime-session-notes-2026-04-29.md`.

## Compaction

Current behavior:

- Implements local `/v1/responses/compact`.
- Produces local compaction records using a `localcmp:v1:` prefix.
- Microcompacts old tool output to keep context manageable.

What works well:

- Practical for long local sessions.
- Keeps Codex moving without depending on hosted compaction.

What is weak:

- The field name may imply encryption, but the local payload is base64-encoded JSON, not cryptographic encryption.
- Format compatibility needs tests.

Maturity: useful beta.

## Observability

See also: `docs/runtime-observability-notes.md`.

Current behavior:

- Writes request, forwarded request, upstream response, dropped tools, and search route captures under `var/captures`.
- When capture mode is enabled, writes request-scoped streamed captures under
  `var/captures/requests/<request_id>/` and keeps `latest-*` files as
  convenience views only.
- `scripts/qz-top` shows stack health, profile settings, container status, GPU
  state, throughput, recent backend activity, and latest benchmark compression
  summary.
- `scripts/qz-thoughts` shows coalesced live thought/output telemetry and
  backend activity in a curses-style view. Raw capture replay is explicit.
- Runtime state and logs are intended to live under `var/`.
- `var/` is ignored by git.

Time/date grounding direction from the 2026-04-29 tuning session:

- The runtime should give agents a cheap current date/time bearing when the user
  anchors work to today, yesterday, latest, current, now, deadlines, schedules,
  logs, or benchmark times.
- Prefer a stable session anchor such as current date and timezone so prompt
  caching is not invalidated every second.
- Use exact timestamps only when needed by the task.
- Record exact run timestamps in benchmark and monitor artifacts.

What works well:

- Captures have already made smoke testing and debugging much faster.
- Keeping runtime state out of tracked files is the right default.

What is weak:

- There is no redaction layer.
- There is no structured run ID grouping across related request captures yet.
- `latest-*` captures still exist as convenience views and can be overwritten by
  later traffic.
- Log inspection must respect the sudo helper's `docker logs --tail <= 1000`
  boundary; monitors should clamp requested tails rather than surfacing helper
  failures as broken stack state.

Maturity: useful but ad hoc.

## Safety Boundary

Current behavior:

- QuantZhai mostly relies on Codex and the host environment for approval and workspace safety.
- The proxy does not currently execute filesystem-mutating tools.
- Docker isolates the model server path.

What works well:

- The current boundary is acceptable while the proxy is mainly an adapter.
- Avoiding local patch execution avoids a large class of path and permission risks.

What is weak:

- If QuantZhai starts executing tools directly, it needs explicit workspace-root validation, path canonicalization, redaction, and deny rules.
- The current proxy structure does not yet make those safety checks reusable.

Maturity: acceptable for adapter behavior; not ready for proxy-side filesystem tools.

## Backend Abstraction

Current behavior:

- The working backend is the current llama.cpp/TurboQuant server path.
- Fox is documented as a possible future backend only after parity with `thetom/llama.cpp-turboquant`.

What works well:

- The current backend works with the known local Qwen GGUF and Docker image.

What is weak:

- There is no formal backend adapter interface yet.
- llama.cpp assumptions are mixed into the proxy implementation.

Maturity: working single-backend implementation.

## What QuantZhai Does Well

- Makes local Codex usable against a llama.cpp/TurboQuant backend.
- Hides enough OpenAI/Responses/Ollama shape mismatch to keep the agent running.
- Provides practical model aliases and reasoning budgets.
- Keeps search local and configurable.
- Captures enough state to debug real failures.
- Keeps runtime data out of git by default.

## What QuantZhai Does Badly

- Too much logic lives in `proxy/quantzhai_proxy.py`.
- Too much compatibility is untested.
- Tool handling is not generalized.
- Streaming and Responses behavior need golden fixtures.
- Responses stream/tool lifecycle needs one documented state contract.
- Capture files are useful but not systematic.
- Safety boundaries are not strong enough for proxy-side filesystem tools.
- Config is still more script-shaped than product-shaped.

## Maturity Snapshot

Stable enough for current use:

- Launch environment.
- Local model aliasing.
- Basic model discovery.
- Chat completions proxying.
- Current Codex local workflow.

Working beta:

- Responses adapter.
- Streaming adapter for pass-through, summary transforms, request-scoped
  captures, and synthetic buffered output.
- Local compaction.
- Profile-aware search.
- Capture-based debugging.
- `qz-top` and `qz-thoughts` monitors.

Partial:

- Ollama compatibility.
- Function-tool passthrough.
- Rate-limit compatibility metadata.
- Tool normalization and lifecycle state.

Alpha:

- `apply_patch` protocol adapter.

Missing:

- General tool runtime.
- Proxy-side shell/code/computer tool support.
- MCP/app bridge.
- Formal backend abstraction.
- Automated compatibility test suite.
- Packaged Python module structure.

## Near-Term Roadmap

1. Document the Responses stream/tool state table: upstream event, proxy state,
   Codex-visible event, telemetry, and capture output. Current contract:
   `docs/responses-stream-tool-state-contract.md`. It now includes an
   evidence-backed state table and coverage matrix.
2. Expand golden replay fixtures. Seed fixtures now cover normal output,
   reasoning-only fallback, artifact-in-reasoning abort, long active reasoning,
   public tool-call buffering, malformed empty tool history, apply_patch
   native/custom rewrite, invalid apply_patch rejection, completed-without-DONE
   terminal closure, web_search continuation, proxy-local web_search
   in-progress/searching/completed lifecycle events, multi-hop proxy-local
   continuation with raw fixture replay, client-disconnect cleanup, and
   terminal usage normalization for Codex `/status`, plus continuation final-hop
   empty-close, bare-DONE, malformed-terminal recovery, large multi-hunk
   apply_patch variants, rename-alias move variants, private tool-call timeout
   aborts, and answer-delta write ordering. Remaining coverage:
   traversal/absolute-path move negatives if a local patch harness is added,
   plus new negative fixtures only when a real bad upstream shape appears.
3. Broaden the tool lifecycle boundary to cover request normalization,
   history filtering, adapter ownership, and telemetry naming. Completed-call
   public/proxy-local decisions, proxy-local continuation shaping, malformed
   history filtering, stream telemetry payload shaping, and tool request
   normalization now have explicit ownership boundaries. Proxy-local telemetry
   payloads, terminal-suppression labels, and continuation-limit fallback text
   are registry-owned. A test-only proxy-local executor now proves the generic
   registry path for streaming and non-streaming continuation is not
   web_search-only. Current weak spot: repeat the live/golden checklist for the
   next real proxy-executed tool.
4. Run a live Qwen/TurboQuant patch workflow and capture whether it emits valid
   patch operations. Done on 2026-05-09 with request
   `qz_req_1778312516589_af70`.
5. Add broader golden tests for Responses normalization. Seed fixtures now pin
   mixed replay-history cleanup and tool declaration normalization, including
   stale harness/reasoning drops, native/custom apply_patch history adaptation,
   `write_stdin` gating, web_search translation, and apply_patch tool policy.
6. Split `proxy/quantzhai_proxy.py` into a conventional Python package.
7. Add a backend adapter boundary before Fox or Rust work.
8. Revisit search once the proxy shape is easier to test.

## Open Questions

- Should unsupported tools be dropped, converted into no-op tool messages, or surfaced as model-visible limitations?
- Should request-scoped captures also be grouped under a higher-level run id?
- Should `qz-thoughts` default to raw reasoning, summary reasoning, hidden
  reasoning with activity only, or a profile-controlled mode?
- What exact Responses event sequence does Codex tolerate across streamed
  tool-call continuation hops?
- How much config should move from scripts into tracked sample config?
- Should QuantZhai ever execute filesystem tools directly, or should it always delegate writes back to Codex?

## Known Blind Spots

Things not tracked elsewhere that should stay on the radar. None are blocking
current use; all are worth addressing before the project is considered stable.

**Codex CLI version drift.**
The proxy is tuned against Codex CLI v0.125.0. New SSE event shapes, tool types,
or catalog fields in a later Codex release could silently break the proxy. There
is no version pin, no canary test against a newer binary, and no compatibility
matrix. Track Codex releases and run the smoke suite when upgrading.

**Fake rate-limit metadata.**
The proxy emits fabricated rate-limit headers to satisfy Codex. This is not real
token accounting. If Codex starts enforcing or displaying these values in ways
the user cares about, the fabrication will become misleading. Marked as "not real
accounting" in the maturity notes but no action is planned.

**Compaction format compatibility.**
Local compaction (`/v1/responses/compact`) is described as "useful beta" with
"format compatibility needs tests." There are no compaction-specific unit or
regression tests. A silent format change could degrade long sessions without a
clear error.

**Date and time grounding for agents.**
The model has no reliable way to get the current date and time during a session.
Tasks anchored to "today", "latest", "current week", or deadlines require the
model to infer or ask the user. A cheap session-scoped date/time injection in the
system prompt or QZSTATE block would close this without invalidating prompt
caches on every second.

**web_search has not been fuzz-tested.**
apply_patch was fuzz-tested against 40 prompts and produced actionable fixes.
web_search has not been. We do not know what argument shapes Qwen actually emits
for search calls, whether any shapes are broken in the proxy, or what the
dominant failure modes look like. Apply the same capture-and-diagnose methodology
used for apply_patch.

**Reasoning budget tuning.**
`QZ_REASONING_BUDGET` exists but optimal values for different task types have not
been explored. Context was bumped to 256k but there is no validation that the
model behaves coherently at that length for multi-turn coding tasks versus
shorter contexts. Track token use and quality across task types before treating
256k as the right default for all profiles.

**Unsupported tool policy.**
Currently unsupported tools are silently dropped. The model has no signal that a
tool call never returned. The right behavior (drop silently, inject a no-op
result, surface as a model-visible limitation) has not been decided. Silent drops
are the current pragmatic choice; document the decision when it becomes load
bearing.

**Proxy authentication.**
The proxy binds to 127.0.0.1 by default but has no authentication. An operator
who accidentally exposes port 18180 gives anyone on the network unrestricted
access to the LLM API. Consider a `QZ_PROXY_SECRET` option or at minimum a
prominent warning in the README about the authentication boundary.
