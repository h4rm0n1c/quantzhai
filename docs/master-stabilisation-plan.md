# QuantZhai Master Stabilisation Plan

## Status

Open master plan. Phase 1 breakage fixes are complete enough to move the
active engineering focus to shared telemetry schema work.

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
```

Status:

```text
Partially fixed. qz-thoughts delta coalescing, stream timing telemetry,
runtime summary-mode transformation, and synthetic terminal DONE forwarding
are implemented. Remaining work belongs to the shared telemetry schema and
profile preset review.
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
```

### 3. `/status`, `qz-top`, `qz-thoughts`, and streaming lack one shared telemetry truth

Document:

```text
docs/observability-streaming-bugfix-agenda.md
```

Problem:

```text
/status can show stale or default context/model data.
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
payload provides one. /qz/telemetry/stream is available as a live SSE alias for
the older /qz/telemetry/events endpoint.
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
Started. Request telemetry, prompt contracts, runtime metrics, and request
captures now share request_id/schema metadata. latest-request-contract.json
captures the request id, prompt contract, runtime metrics, and selected backend.
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
  seq
  wall_ts / ts
  monotonic_ts
  request_id promoted from request payloads where available

proxy/qz_request_router.py:
  /qz/telemetry/stream aliases /qz/telemetry/events
  /qz/telemetry/recent includes schema
```

Acceptance:

```text
/status, qz-top, and qz-thoughts read the same truth.
Multiple monitors can run concurrently.
Readers do not mutate shared telemetry.
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
shared telemetry schema hardening
```

Reason:

```text
Phase 1 breakage fixes are done.
qz-top, qz-thoughts, /status, streaming diagnostics, and benchmarks need one
runtime event contract before deeper monitor or config work.
```

Then do:

```text
monitor fallback cleanup and concurrent monitor smoke
```

Reason:

```text
qz-top TPS first pass is done. Remaining monitor work is proving qz-top and
qz-thoughts can run together without file/log fallback becoming live truth.
```

Then do:

```text
stream timing telemetry
```

Reason:

```text
proves whether the proxy is actually delaying Codex
turns vibes into data
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
docs/runtime-observability-notes.md
docs/README.md
AGENTS.md
```
