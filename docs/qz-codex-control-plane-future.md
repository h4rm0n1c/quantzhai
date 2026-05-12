# Deferred Plan: qz-codex Control Plane and Remote Appliance Mode

Date: 2026-05-12

Status: deferred / weird-wacky-later / not active implementation.

This document captures a future idea, not current work. Do not implement this
before higher-priority stabilisation, SQLite operational substrate work, and
script/data-path cleanup are in a safer state.

The idea: keep upstream Codex behaviour as the default data plane, but allow a
thin `qz-codex` fork or wrapper to understand QuantZhai's control plane so the
client can report model/profile/backend status properly, handle loading/restart
states, and eventually operate over authenticated HTTP from another machine.

This is deliberately separate from LimbiCore memory/state work. It is about
client/proxy/runtime UX and control state, not model-visible memory.

---

## One-line contract

```text
/v1/responses stays OpenAI/Codex-compatible; /qz/* becomes the optional QuantZhai control plane; qz-codex is a thin client overlay that uses /qz/* when available.
```

Short form:

```text
Default to upstream Codex. Add only the QuantZhai bits upstream Codex cannot know.
```

---

## Why this exists

QuantZhai currently knows more about runtime state than Codex can comfortably
show:

```text
selected profile
profile target model
loaded backend model
backend context
requested context
restart_required
restart_required_state
model loading / loaded / failed
slot liveness
health readiness
why a model switch needs backend restart
whether Codex should reconnect/restart
```

Codex mainly sees the normal Responses data plane:

```text
/v1/responses
/v1/models
usage
stream events
tool lifecycle events
errors
```

That is enough for basic operation, but weak for local model/profile UX. When a
profile switch requires a backend restart, Codex should not pretend the model is
thinking. The client should show that the engine room is loading a new model.

---

## Current related QuantZhai state

Relevant existing concepts/docs:

```text
docs/runtime-observability-notes.md
docs/bugs/zombie-model-slot.md
docs/master-stabilisation-plan.md
proxy/qz_model_router.py
/qz/status
/qz/telemetry/*
scripts/qz-codex
scripts/qz-codex-common
scripts/qz-up
scripts/qz-down
```

Existing themes:

```text
/qz/status reports restart_required_state.
qz-codex prefers the model already loaded by the proxy at launch.
The proxy persists the last selected model.
Model switching uses QZ_MODEL_LOAD_TIMEOUT.
Per-model context can require backend restart, not just model reload.
The restart decision belongs in the proxy/control plane, not llama.cpp model loading.
TurboQuant can report a zombie loaded slot if the actual llama-server port is dead; independent liveness checks are needed.
```

---

## Not now

Do not start this until the nearer work is under control:

```text
Phase 1 SQLite operational substrate
current docs/authority cleanup
runtime/control status consistency
script/data-path cleanup
model router/restart-required correctness
zombie slot/liveness handling
```

This plan is allowed to influence future data-path and script cleanup, but it
must not distract from the immediate work.

---

## Architecture split

### Data plane

Keep this upstream-compatible:

```text
/v1/responses
/v1/models
/v1/responses/compact where QuantZhai exposes it
```

Rules:

```text
Do not fork Codex request construction unless QuantZhai absolutely requires it.
Do not mutate normal Responses semantics for qz-codex-only convenience.
Keep OpenAI/Codex compatibility as the default.
```

### Control plane

QuantZhai-specific, optional, explicit:

```text
/qz/status
/qz/models
/qz/profiles
/qz/profile/select       future
/qz/model/load           future
/qz/events               future
/qz/telemetry/*
```

Rules:

```text
Control endpoints report machinery state.
The model should not narrate backend loading.
The client/proxy should report backend loading.
```

### Client overlay

`qz-codex` should be a thin upstream-compatible Codex fork/wrapper:

```text
normal Codex agent loop stays upstream-shaped
normal tools stay upstream-shaped
normal Responses data plane stays upstream-shaped
QuantZhai integration is behind explicit config/feature gates
```

Possible feature gate names:

```text
QZ_BASE_URL
QZ_CONTROL_URL
QZ_AUTH_HEADER
QZ_ENABLE_CONTROL_PLANE=1
```

---

## Possible future client behaviours

A QuantZhai-aware `qz-codex` could:

```text
preflight /qz/status before starting
show selected profile/backend/model/context
show restart_required and restart_required_state
wait while a model is loading
show model-load/restart progress from /qz/events
warn when current Codex session should be restarted
offer a profile/model picker
sync selected profile with proxy state
block or delay prompts while backend is restarting
surface compact actionable errors instead of generic timeouts
```

Example user-facing status:

```text
QuantZhai is loading profile prompt-compiler.
Backend restart required: context changed 131072 -> 262144.
Waiting for backend health...
Ready.
```

Bad pattern to avoid:

```text
Inject into the model prompt: "Tell the user the backend is loading."
```

The model should solve tasks. The client should report machinery state.

---

## Model/profile/change classification

QuantZhai should eventually classify changes like this:

```text
no_restart:
  prompt/profile metadata only
  reasoning effort
  prompt append
  tool policy

model_reload:
  same server/runtime params
  different model file or profile target

backend_restart:
  context length
  KV/cache type
  batch/ubatch/server launch flags
  tensor split / GPU split
  speculative decoding mode if launch-bound

client_restart_recommended:
  Codex-visible catalog/session/profile assumptions changed enough that an existing client session should reconnect or restart
```

The exact classification belongs to QuantZhai, not Codex. qz-codex should display
and obey it.

---

## Remote single-user appliance mode

A later deployment shape could be:

```text
qz-codex / Codex-compatible client
  -> HTTPS + auth reverse proxy
    -> QuantZhai /v1/responses + /qz/*
      -> llama.cpp/TurboQuant backend
```

Likely transport boundary:

```text
nginx
TLS
HTTP Basic Auth or Bearer token
single user
LAN/VPN preferred
```

Expose only QuantZhai, not raw backend management ports:

```text
internet/LAN -> nginx -> QuantZhai proxy -> internal llama.cpp/TurboQuant
```

Do not expose raw llama.cpp/TurboQuant ports unless there is a deliberate reason.
QuantZhai remains the policy boundary.

---

## Auth and endpoint classes

Even for single-user mode, distinguish endpoint risk:

```text
read-only:
  /qz/status
  /qz/telemetry/*
  /qz/events

inference:
  /v1/responses
  /v1/models

control/admin:
  /qz/profile/select
  /qz/model/load
  /qz/backend/restart
```

Future auth may want separate tokens:

```text
read token
inference token
admin token
```

Do not overbuild this now. Just avoid designing an API that makes separation
impossible later.

---

## Single-user locking model

QuantZhai is a single-user local/remote appliance, not a multi-tenant service.

Recommended future policy:

```text
one active control owner
one active foreground session
monitors are read-only
model/profile switching is locked
request queueing is explicit
```

If a request arrives during a model switch:

```text
return structured retryable status
or have qz-codex wait on /qz/events until ready
```

Do not let multiple clients fight over model loads.

---

## Data-path and script cleanup benefits

This future work also pushes existing cleanup in the right direction:

```text
fewer one-off shell scripts
qz-up/qz-down/qz-codex become thinner wrappers
runtime truth moves to /qz/status and structured control APIs
generated files stay generated, not source of truth
var/state, var/run, var/cache, var/logs, var/captures become clearer
model/profile selection becomes an API workflow rather than script side effects
```

This is a side benefit, not the reason to rush implementation.

---

## Possible phases, if this ever becomes active

### Phase 0: Contract only

This document. No code.

### Phase 1: Better local status/preflight, no Codex fork

```text
Improve /qz/status restart/load fields.
Make scripts/qz-codex preflight /qz/status.
Print clear local messages before launching Codex.
Keep upstream Codex unchanged.
```

### Phase 2: qz-codex thin overlay

```text
Fork/wrap Codex minimally.
Read /qz/status.
Show QuantZhai model/profile/backend status.
Wait on loading/restart states.
Keep /v1/responses data plane upstream-compatible.
```

### Phase 3: /qz/events

```text
Add event stream or polling endpoint for model load/restart progress.
qz-codex displays progress without abusing model output.
```

### Phase 4: Profile/model picker

```text
qz-codex can list profiles/models through QuantZhai.
Selecting a profile calls QuantZhai control endpoint.
QuantZhai classifies no_restart/model_reload/backend_restart/client_restart_recommended.
```

### Phase 5: Remote single-user mode

```text
nginx/TLS/auth boundary
remote qz-codex support
single-user locks
endpoint risk separation
```

---

## Non-goals

Do not turn qz-codex into:

```text
a second proxy
a model router
a memory system
a llama.cpp process manager
a divergent Codex fork for normal agent behaviour
a multi-user service
```

Those responsibilities stay in QuantZhai or remain out of scope.

---

## Relationship to LimbiCore

LimbiCore covers model state/signal/memory rendering.

This document covers client/proxy control-plane UX.

They rhyme, but they are separate:

```text
LimbiCore:
  what state may become model-facing packets or recall results

qz-codex control plane:
  how the client learns machinery state without asking the model to explain it
```

Do not mix them.

---

## Open questions

```text
Should qz-codex be a fork, wrapper, or upstream PR-friendly plugin layer?
What exact /qz/status fields are missing for a good UX?
Should /qz/events be SSE, polling, or both?
How should qz-codex detect that the current session should restart?
What status shape should describe backend_restart vs model_reload?
Can model/profile selection stay script-driven for longer?
How much auth belongs in nginx vs QuantZhai itself?
What minimum remote mode is safe on LAN/VPN?
How much of qz-codex can stay upstream-compatible to reduce merge pain?
```

---

## Keep this parked

This plan is valuable because it captures a clean future seam:

```text
/v1/responses = upstream-compatible data plane
/qz/* = QuantZhai control plane
qz-codex = optional enhanced client for /qz/*
nginx = optional remote transport/auth boundary
```

But it is not the next task.

Do SQLite, runtime correctness, and script/data-path cleanup first. Then come
back here if the client UX keeps getting in the way.
