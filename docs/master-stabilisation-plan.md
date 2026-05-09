# QuantZhai Master Stabilisation Plan

## Status

Open master plan. Phase 1 breakage fixes, telemetry schema base, monitor
fallback cleanup, concurrent monitor smoke, stream timing telemetry, qz-top
host-local VRAM split, request-scoped stream captures, reasoning-effort policy,
and the first config ownership/layering pass are live-smoked. The active
engineering focus now moves to the Responses stream/tool state machine and
golden replay fixtures before broader refactors.

This is the controlling map for the current QuantZhai stabilisation work. It ties together the known bug notes, observability agenda, and configuration-contract plan.

Update this document whenever the bug picture changes enough that the fix order or architecture contract changes.

## Core diagnosis

QuantZhai is not suffering from one isolated bug.

The stack is suffering from missing contracts between layers:

```text
config
model catalog
profile aliases
backend routing
prompt injection
runtime state
SSE streaming
telemetry
human monitors
Codex-facing generated metadata
```

When those layers blur together, bugs look random:

```text
A deleted GGUF bricks Codex.
A generated Codex model catalog becomes a second truth.
/status reports stale defaults.
qz-thoughts renders every tiny SSE delta as fake activity.
qz-top can invent nonsense token rates.
Streaming feels like delayed paste instead of live output.
```

These are contract, validation, and observability failures. The engine works. The wiring is still suspect.

## Current documented bugs

### 1. Stale profile symlink can brick Codex

Document:

```text
docs/bugs/stale-profile-server-alias.md
```

Status:

```text
Fixed. Keep this as a regression contract.
```

Problem:

```text
Codex-visible profile: prompt-compiler
Prompt override:        prompt-compiler.gguf / system_prompt_file
Backend target:         resolved symlink target GGUF stem
```

If the symlink target GGUF is removed or points outside scanned models while the profile remains visible, Codex can still select the profile. The proxy resolves the profile, cannot route to a valid backend target, and returns a huge 503 response.

Design rule:

```text
Profiles are valid only if their backend target is valid.
```

Implemented contract:

```text
qz_model_catalog.py:
  validate symlink target
  expose profile_valid/profile_error/profile_symlink/backend_target

scripts/qz-codex-common:
  hide invalid profiles or mark them unavailable

qz_model_router.py:
  returns compact actionable errors
  does not dump the full catalog into normal client errors
```

No silent fallback unless the profile explicitly opts into fallback.

Synthetic alias policy:

```text
Removed. Do not route or advertise QwenZhai-* or Qwen3.6Turbo-* budget aliases.
Model ids are real GGUF files from the model directory, plus symlink profiles in
that directory.
```

### 2. Responses SSE and `qz-thoughts` streaming are not trustworthy enough

Document:

```text
docs/bugs/responses-streaming-and-qz-thoughts.md
docs/responses-stream-tool-state-contract.md
```

Status:

```text
End-to-end stream audit complete. qz-thoughts delta coalescing, stream timing
telemetry, runtime summary-mode transformation, synthetic terminal DONE
forwarding, request_id correlation, request-scoped captures, reasoning-only
classification, artifact-in-reasoning aborts, tool-call buffering until
arguments are complete, malformed empty-tool history filtering, and downstream
client-disconnect classification are implemented and live-smoked. The formal
Responses stream/tool state contract and golden replay fixtures now cover
normal output, public function calls, native/custom apply_patch conversion,
multi-hunk patches, move/rename operations, invalid move operations,
reasoning-only stalls,
artifact-in-reasoning failures, and client disconnect cleanup.

A supported `codex exec --json --ephemeral` comparison against hosted
OpenAI-backed Codex showed that the basic shell-command lifecycle already
appears as `item.started`/`item.completed` through the QZ path. Remaining
streaming hardening should focus on proxy-local/private tool progress,
apply_patch handoff edge cases, TUI rendering, and `/status` token/context
relay rather than assuming simple shell-call lifecycle relay is missing.
```

Problem:

```text
Codex side:
  data may not be pushed as soon as safely possible
  output can feel like crunch-then-paste

qz-thoughts side:
  every tiny reasoning/output delta becomes a human activity row
  monitor shows loops like "thought +1 chars"
```

Design rule:

```text
A raw SSE delta is not automatically a useful human-facing activity row.
```

Required audit:

```text
raw upstream SSE
transformed forwarded SSE
telemetry events
qz-thoughts rendering
Codex-visible behaviour
```

Current contract:

```text
docs/responses-stream-tool-state-contract.md defines the state table for
upstream SSE, proxy state, Codex-visible events, telemetry, and captures.
Keep it in sync as tool lifecycle ownership moves out of the stream loop.
```

Implemented first fixes:

```text
qz-thoughts:
  coalesce thought and answer deltas
  keep live buffers
  only activity-log lifecycle events

proxy stream path:
  add timing telemetry around parse/transform/forward
  verify normal output_text events are forwarded immediately
  audit reasoning_text -> reasoning_summary_text conversion
  promote request_id into sse_event and stream_event_timing telemetry
  classify reasoning-only stalls without a default char cap
  buffer executable tool calls until arguments are complete
  drop malformed empty tool-call history before forwarding upstream
```

### 3. `/status`, `qz-top`, `qz-thoughts`, and streaming lack one shared telemetry truth

Document:

```text
docs/observability-streaming-bugfix-agenda.md
```

Problem:

```text
/status can show stale or default context/model data.
/status inside qz-codex does not visibly prove that proxy-calculated token
and context-window usage are being relayed back to Codex.
qz-top token-per-second math can be unreliable.
qz-thoughts has inconsistent telemetry/rendering.
multiple monitors need to run without conflicting.
profile effort presets need review.
proxy streaming needs end-to-end audit.
```

Target shape:

```text
proxy observes facts
proxy owns live structured state/events
tools render read-only views
```

Primary live surfaces:

```text
/qz/status
/qz/telemetry/recent
/qz/telemetry/stream
```

Status:

```text
Started. Telemetry events now carry a schema id, sequence number,
wall-clock timestamp, monotonic timestamp, and promoted request_id when the
payload or stream runtime provides one. /qz/status, /qz/telemetry/recent, and
/qz/telemetry/stream expose the same request/runtime truth for the audited
Responses streaming path.
```

Rules:

```text
Only the proxy owns normal runtime telemetry.
Monitors are read-only consumers.
JSON/JSONL files are optional replay/debug artifacts, not live truth.
Readers tolerate missing endpoints, missing files, partial lines, and schema changes.
Missing telemetry displays as unknown, not fake certainty.
```

### 4. Config, generated state, runtime state, and user overrides are smeared together

Document:

```text
docs/edge-case-config-contract-plan.md
```

Problem:

```text
source defaults
example config
user overrides
generated Codex config
model inventory
runtime state
captures
logs
cache
```

These categories currently blur together. That makes generated files act like source of truth and stale state override real proxy policy.

Status:

```text
Started. Current data paths are audited in docs/edge-case-config-contract-plan.md.
/qz/config/effective exposes a read-only effective path/config report.
Search policy moved from docs/ to config/default/search-policy.json with old-path compatibility fallback.
Model override defaults/examples moved into config/default/model-overrides.json and config/example/model-overrides.json with old-path compatibility fallback.
Codex config/catalog examples moved under config/example/, benchmark prompts
moved under config/default/, and config/user/ is present for local overrides.
Live smoke confirms /qz/config/effective, /qz/status, /qz/telemetry/recent,
/qz/telemetry/stream, qz-top, qz-thoughts, qz-doctor, and generated Codex
catalog preparation all still work against the current proxy. One host-local
compatibility path remains active when config/user/model-overrides.json is
absent: legacy var/model-overrides.json.
```

Proposed destination:

```text
config/default/     shipped baseline config
config/example/     copyable examples, never active unless selected
config/user/        local overrides, active by default, not committed

var/generated/      generated Codex catalog/config views
var/state/          persistent runtime state
var/run/            live process/runtime state
var/cache/          disposable caches
var/logs/           logs
var/captures/       request/response captures
var/models/         local model files and profile symlinks
```

But do not start with this broad refactor. Audit first.

## Core dependency chain

The system stabilises in this order:

```text
config contract
  -> model/profile catalog correctness
    -> prompt/backend routing correctness
      -> /status runtime truth
        -> telemetry correctness
          -> qz-top/qz-thoughts usefulness
            -> streaming UX and diagnostics
```

Do not attack the lower layers before the upper contracts are at least minimally safe.

## Fix order

### Phase 1: Stop current breakage

#### 1. Validate invalid profile/backend targets

Status:

```text
Fixed. Keep acceptance tests and bug note current.
```

Why first:

```text
A removed model file should not make Codex unusable.
```

Implement:

```text
catalog validation for symlink profile targets
invalid profile state in model inventory
hide invalid profiles from Codex catalog
compact actionable router errors
```

Acceptance:

```text
Removing a backend GGUF does not brick Codex.
Invalid profiles are hidden or clearly unavailable.
Direct invalid-profile requests get compact errors.
No silent fallback.
```

#### 2. Fix `qz-thoughts` delta spam

Status:

```text
Fixed. Remaining monitor work is shared telemetry schema/fallback cleanup.
```

Why second:

```text
It is visible, annoying, misleading, and safe to improve without changing wire semantics.
```

Implement:

```text
coalesce reasoning deltas into THOUGHT buffer
coalesce answer deltas into ANSWER buffer
one rolling status row per active stream
activity log only for lifecycle/tool/error events
```

Acceptance:

```text
No more activity floods like thought +1 chars.
THOUGHT/ANSWER panels still update live.
Raw captures still preserve raw SSE for debugging.
```

#### 3. Add stream timing telemetry

Status:

```text
Fixed first pass. Timing fields exist for parsed stream events.
```

Why third:

```text
We need proof before touching the SSE algorithm deeply.
```

Track:

```text
upstream event received
SSE event parsed
SSE event transformed
SSE event forwarded
telemetry event emitted
```

Acceptance:

```text
We can measure parse-to-forward delay.
We can tell whether Codex delay is proxy buffering or upstream behaviour.
```

### Phase 2: Create one runtime truth

#### 4. Fix early `/status` truth

Status:

```text
Started. /qz/status and telemetry summaries now expose source/state fields for
selected and backend context lengths, plus a restart_required_state so monitors
can distinguish confirmed facts from pending/default values.
```

Implement:

```text
startup-intended runtime snapshot
backend-confirmed runtime facts when available
pending/unconfirmed state for unknowns
```

Acceptance:

```text
/status does not confidently show stale 131072 when 262144 was requested.
Model/context are available before first prompt where possible.
Unknown values are labelled unknown/pending.
```

#### 5. Define shared telemetry/event schema

Status:

```text
Fixed base contract. Request telemetry, prompt contracts, runtime metrics, and
request captures share request_id/schema metadata. /qz/status,
/qz/telemetry/recent, and /qz/telemetry/stream expose the same status-summary
runtime truth, or an explicit unknown runtime sentinel when unavailable.
```

Implement:

```text
/qz/status
/qz/telemetry/recent
/qz/telemetry/stream
schema versions
request ids
monotonic timestamps
sequence numbers
```

Started:

```text
proxy/qz_telemetry.py:
  event schema qz.telemetry.event.v1
  state schema qz.telemetry.state.v1
  recent schema qz.telemetry.recent.v1
  stream schema qz.telemetry.stream.v1
  seq
  wall_ts / ts
  monotonic_ts
  request_id promoted from top-level, metadata, runtime_metrics,
    prompt_contract, and response payloads where available
  latest_request_id / latest_completed_request_id on state
  unknown runtime sentinel qz.runtime.summary.v1

proxy/qz_request_router.py:
  /qz/telemetry/stream aliases /qz/telemetry/events
  /qz/telemetry/recent includes schema plus state.runtime
```

Acceptance:

```text
/status, qz-top, and qz-thoughts read the same truth.
Multiple monitors can run concurrently.
Readers do not mutate shared telemetry.
```

Codex-facing status relay:

```text
qz-codex `/status` is separate from QuantZhai `/qz/status`, but it does ingest
the Codex-visible paths we control: generated model catalog context metadata and
final Responses `usage`. Keep those paths populated from the same runtime truth
used by /qz/status and qz-top. Do not inject proxy status into the model prompt
to work around UI display issues.
```

Current finding:

```text
Generated Codex model catalog entries carry context_window, max_context_window,
truncation_policy, reasoning levels, and system prompt metadata. Responses
turn.completed usage carries input/cached/output/reasoning token counts back to
Codex. QuantZhai /qz/status and /qz/telemetry expose live backend/runtime truth.

Codex CLI v0.125.0 TUI `/status` was checked against prompt-compiler after a
short streamed turn. It displayed `Token usage: 527 total (503 input + 24
output)` with `+10,066 cached` on exit, and `Context window: 100% left (10.6K
used / 249K)`. That confirms the client is consuming our catalog context window
and final Responses usage. It does not mean Codex TUI consumes /qz/status
directly; keep qz-top and /qz/status as the richer local runtime/backend
surfaces.
```

#### 6. Fix `qz-top` token math

Status:

```text
Fixed first pass. qz-top consumes structured telemetry, rejects non-finite or
non-positive timing/rate samples, keeps prompt/generation/total rates separate,
and treats latest as the latest valid sample instead of the maximum sample.
```

Implement:

```text
prompt-eval TPS separate from generation TPS
monotonic timestamps only
rolling windows
unknown instead of nonsense
```

Acceptance:

```text
No negative, infinite, NaN, or absurd TPS.
Missing telemetry is displayed as unknown.
```

### Phase 3: Refactor configuration properly

#### 7. Audit all current data paths

Track for each path:

```text
source files read
runtime files written
generated files written
user-visible output
failure modes
current error message
preferred error message
whether recovery is safe
```

Paths to audit:

```text
model discovery
profile alias resolution
symlink profile target resolution
prompt override loading
Codex catalog generation
runtime status generation
backend state persistence
model-state persistence
capture writing
logs
search policy loading
SearXNG capabilities loading
```

#### 8. Move toward explicit config layers

Only after the audit.

Target:

```text
config/default/
config/example/
config/user/
var/generated/
var/state/
var/run/
var/cache/
var/logs/
var/captures/
var/models/
```

Acceptance:

```text
User overrides are separate from shipped defaults.
Generated files are not treated as source config.
Effective config can be inspected with source/layer information.
Docs match actual behaviour.
```

#### 9. Reduce script sprawl

Rule:

```text
Do not keep adding one-off shell scripts.
```

Desired user-facing shell surface:

```text
qz-up
qz-down
qz-codex
```

But do not dump all logic into those three scripts.

Move shared behaviour toward:

```text
importable Python modules
coherent CLI entry points
clear config contracts
documented generated files
```

Acceptance:

```text
No new one-off shell script without explicit justification.
Shell wrappers remain thin.
Business logic becomes testable Python code or a coherent CLI surface.
```

## Do-not-do list

Do not:

```text
fix streaming by only lowering reasoning effort
fix qz-thoughts by hiding all thought telemetry globally
silently fallback to another backend model
add another helper shell script for every wart
let generated Codex metadata become the source of truth
move config files around before auditing data paths
show fallback defaults as confirmed runtime facts
fold all old script logic into qz-up/qz-down/qz-codex
```

## Immediate next engineering target

Start with:

```text
keep docs/responses-stream-tool-state-contract.md current
expand golden replay fixtures for the Responses stream/tool state contract
```

Reason:

```text
Recent failures were not caused by raw HTTP forwarding. They were state
contract failures: reasoning-only streams carrying artifact text, public tool
items appearing before arguments were complete, malformed empty tool history
being replayed upstream, and private tools being exposed when their required
runtime state did not exist. The next hardening step is a documented event
contract and replay tests that pin those transitions. Seed fixtures now cover
normal output, reasoning-only fallback, artifact-in-reasoning abort, long active
reasoning, public tool-call buffering, malformed empty tool history,
apply_patch rewrite, and web_search continuation.
```

Then do:

```text
audit and improve Codex-facing live lifecycle relay
```

Reason:

```text
QuantZhai now has proxy-side telemetry truth, but Codex CLI can still feel
two-step compared with hosted OpenAI: tool intent may be buffered internally,
tool execution may not show a prompt start/running/completed lifecycle, and
Codex `/status` may not show proxy-calculated token/context usage. Audit the
actual Responses events Codex consumes, then relay request, output item, tool
call, tool result, usage, and terminal status transitions in the shapes Codex
expects.
```

Current finding:

```text
Supported codex exec --json captures show basic shell_command lifecycle already
matches hosted shape through QZ, including long-running shell commands when
tested with -m prompt-compiler. A bad QZ capture used the persisted amber model
and should not be treated as coding/tool evidence.

The proxy-local web_search relay now emits Responses built-in web-search
progress events before proxy execution, then emits the completed public
web_search_call after local execution. It still suppresses the private upstream
function_call and arguments, so Codex is not asked to execute proxy-private
tools itself. Live `qz-codex exec --json -m prompt-compiler` smoke on
2026-05-09 confirmed Codex sees `item.started web_search`,
`item.completed web_search`, the final assistant message, and terminal usage.

Telemetry retention first pass is implemented: the proxy keeps a bounded
per-request lifecycle summary for important request/tool events and exposes it
through `/qz/status`, `/qz/telemetry/state`, and
`/qz/telemetry/request?request_id=<id>`. `qz-top` and `qz-thoughts` merge that
latest completed request summary with `/qz/telemetry/recent`.

Downstream client-disconnect handling now classifies write failures as
`client_disconnected`, closes the upstream stream through normal cleanup, and
does not emit synthetic completion after the failed write. Remaining relay gaps
are live-Qwen apply_patch edge cases, long-running TUI rendering, and any future
Codex `/status` token/context usage relay regressions.

Hermetic Codex apply_patch handoff is now pinned: the local fake upstream
streams an `apply_patch` function call through the proxy, Codex executes the
patch in a temp workspace, and `codex exec --json` exposes the public lifecycle
as `item.started file_change`, `item.completed file_change`, final
`agent_message`, and terminal `turn.completed usage`.

Live Qwen/TurboQuant apply_patch smoke on 2026-05-09 also passed through
`qz-codex exec -m prompt-compiler`: Codex created
`live-qwen-apply-patch-smoke.txt`, exposed `file_change` started/completed
events, returned final `agent_message` text `done`, and reported terminal usage.
The matching proxy request was `qz_req_1778312516589_af70`.
```

Then do:

```text
broaden tool lifecycle ownership beyond the first internal boundary
```

Reason:

```text
Streamed call state, public item conversion, completed-call routing,
proxy-local continuation shaping, bad-history filtering, tool declaration
normalization, and non-tool request-history normalization now have internal
boundaries. Capture mode interpretation and latest/request-scoped dual writes
now have a first runtime IO boundary. Keep behaviour stable while moving toward
one full tool/request/capture state machine.
```

Concrete target:

```text
A first proxy-local tool executor registry is implemented before adding more
proxy-executed tools. Tool support is split into two explicit classes:

- protocol adapters, such as `apply_patch`, where QuantZhai translates the
  shape and Codex keeps execution authority
- proxy-local executors, such as `web_search`, where QuantZhai executes locally
  and feeds hidden continuation state back to the model

Default rule: if Codex already has a safe built-in execution path, keep
execution with Codex and build a protocol adapter unless a separate documented
reason justifies proxy execution.

The streaming and non-streaming paths call the same interface for:

- deciding whether a completed function_call is proxy-local
- executing the tool with request_id, counters, cache, and telemetry context
- returning the Codex-visible display item and hidden upstream continuation
  items
- emitting tool_call_started/tool_call_completed or failure telemetry
- normalizing tool declarations, tool_choice, write_stdin exposure, and
  request-scoped tool policy metadata before forwarding to llama.cpp

web_search is now on that interface. Stream-specific lifecycle event emission
still lives in the stream runtime because it owns SSE sequencing and forwarded
byte accounting, but the stream runtime now reads tool lifecycle metadata from
the active registry instead of branching directly on the `web_search` name. Tool
request normalization now lives in `proxy/qz_tool_request.py`. Keep apply_patch
as a protocol adapter and Codex handoff path unless a separate security decision
explicitly adds proxy-side patch execution. Non-tool Responses input cleanup now
lives in `proxy/qz_request_normalization.py`, while `proxy/qz_responses.py`
keeps compatibility exports and local compaction helpers. Capture pairing now
lives behind `proxy/qz_runtime_io.py` helpers such as `write_dual_capture()` and
`open_dual_capture_append()`.
```

Success criteria:

```text
A new proxy-local tool should provide an executor implementation, tests for
streaming and non-streaming continuation, lifecycle-spec telemetry, multi-hop
continuation when it resumes upstream, and docs for its Codex-visible event
shape. Some streamed event wiring may remain in qz_responses_stream.py when a
tool needs custom SSE lifecycle events. Tool declaration adapters may still be
separate when a tool only needs shape conversion rather than proxy execution.
```

Then do:

```text
reduce script/config ownership duplication and continue var layout migration
```

Reason:

```text
The config layering pass is started, but qz-env, qz-codex-common, monitor
scripts, and helper wrappers still each own pieces of config/path/runtime
behaviour. After the stream/tool contract has regression coverage, move shared
behaviour toward importable Python helpers and continue separating generated,
runtime, cache, log, and capture state without changing model-dir profiles or
Codex-visible slugs.
```

## Current working principle

The stack should behave like this:

```text
source/user config
  -> validated effective config
    -> generated Codex view
      -> proxy routing and prompt policy
        -> backend state
          -> structured telemetry
            -> read-only status and monitor tools
```

Each arrow needs a contract.

If a layer cannot validate what it emits, the next layer should not be forced to guess.

## Related documents

```text
docs/bugs/stale-profile-server-alias.md
docs/bugs/responses-streaming-and-qz-thoughts.md
docs/edge-case-config-contract-plan.md
docs/observability-streaming-bugfix-agenda.md
docs/responses-stream-tool-state-contract.md
docs/runtime-observability-notes.md
docs/README.md
AGENTS.md
```
