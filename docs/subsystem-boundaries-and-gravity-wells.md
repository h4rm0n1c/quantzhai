# Subsystem Boundaries and Gravity Wells

Date: 2026-05-28
Status: **Active**

---

## 1. Why this document exists

Four recent bug reports had the same root cause: logic that belonged in one
subsystem had migrated into another until neither subsystem was coherent.

- The compaction hot path tried to become a second judge. It accumulated semantic
  audit gates that rejected structurally valid `v3` summaries on the default code
  path.
- Model-state authority was overwritten by observation. A `status_snapshot` write
  path set `selected_backend_id = "default"` and the system trusted that value at
  startup, breaking every subsequent `qz-codex` launch.
- The backend manager declared startup failure too early. It returned `PHASE_FAILED`
  when the first GPU log scan came back non-GPU, even though the model was still
  loading and would have been healthy 20 seconds later.
- The `qz-codex` wrapper accreted control-plane behavior. Rather than consuming the
  proxy's model status, earlier versions of the wrapper tried to maintain their own
  fallback model list and their own loading policy.

Each of these bugs happened because a subsystem absorbed work that did not belong
to it. The fixes were smaller than the drift. This document names the subsystems,
draws the lines, and writes down the rules that keep lines from blurring.

---

## 2. Core principle: one subsystem, one authority

**Every important truth in the system must have exactly one primary owner.**

- The proxy's persisted selection (`qz.model_state.v1`) is the authority for which
  model is selected. Nothing else decides that.
- The backend manager's `phase` field is the authority for backend lifecycle state.
  Nothing else decides that.
- The compaction hot path's job is to run compaction and report one of four outcomes.
  It is not a judge of whether the summary is "good enough" beyond structural
  validity.
- The `qz-codex` wrapper knows what model to launch Codex with. It asks the proxy
  for that information. It does not maintain a parallel truth.

**Observation must not silently become authority.**

Reading what happened last time is useful for recovery hints and operator messages.
It must not drive startup or routing decisions. The `last_loaded_model` field is an
observation. `selected_backend_id` is authority. They are different things even when
they happen to agree.

**Wrappers translate and orchestrate; they do not invent a second policy layer.**

A wrapper that consumes a status endpoint is healthy. A wrapper that shadows the
endpoint's logic with its own cached state, its own fallbacks, or its own failure
judgments is drifting.

---

## 3. Subsystem map

### A. Compaction runtime

**Primary file:** `proxy/qz_responses.py`

**Owns:**
- compaction orchestration (choosing `v3` vs `v2`)
- constructing and validating the compaction payload
- reporting one of the four public fallback reasons on failure

**Must not own:**
- semantic correctness audits (atom audit, structure audit, correction audit)
- acceptance gates that reject valid summaries on the default hot path
- expanded fallback taxonomies beyond the four-item vocabulary
- research-style quality scoring on live request paths

**Four public fallback reasons — fixed:**
```
no_source
v3_unavailable
llm_failed
invalid_summary
```
Anything more specific is internal and must not become a public runtime branch.

**Current gravity well:**
`proxy/qz_responses.py` concentrates all compaction orchestration plus the fallback
selection logic. That concentration is acceptable. What is not acceptable is adding
semantic judgment layers on top of the structural validation that already exists.
The file will keep attracting "just one more validation step" proposals. Reject them
unless the validation is purely structural (e.g. checking that the required JSON
fields exist and are non-empty).

**Anti-drift rule:** The hot path may check structure. It must not grow semantic
judgment. If a compaction quality question needs answering, answer it offline or
surface it as a warning-only metadata field — not as a hard gate.

---

### B. Model-selection authority

**Primary files:** `proxy/qz_model_state.py`, `proxy/qz_model_router.py`,
`proxy/qz_model_status.py`

**Owns:**
- the canonical selected model (`selected_backend_id`)
- persistence of that selection to `qz.model_state.v1`
- resolving selection to a catalog entry at startup

**Must not own:**
- backend-loaded observations (what model the running container actually has)
- wrapper preference guesses (what model `qz-codex` thinks is "probably right")
- status-snapshot reconciliation that overwrites selection authority

**Authority, observation, and recovery are three distinct things:**

| Field | Category | May drive startup? |
|---|---|---|
| `selected_backend_id` | authority | yes — it is the only persisted authority |
| `selected_source`, `selected_at` | metadata | no |
| `selected_key`, `selected_label` | derived | no — derive from catalog at runtime |
| `last_loaded_model`, `last_load_result` | observation | no |
| `last_good_backend_id` | recovery | only as a fallback, not primary |

**Current gravity well:**
The persisted state shape (`qz.model_state.v1`) still carries authority, observation,
and recovery fields in a single file. Any code that reads this file is tempted to
trust all three kinds of field equally. The file format does not enforce the
distinction, so enforcement must come from the code and the tests.

**Anti-drift rule:** A write path that touches `selected_backend_id` must be
explicitly justified. A `status_snapshot` or health-check path has no business
writing `selected_backend_id`. If a PR adds a write to that field from an
observational path, reject it.

---

### C. Startup preload / self-heal

**Primary files:** `proxy/quantzhai_proxy.py` (startup sequence),
`proxy/qz_model_router.py` (`_preload_last_model`)

**Owns:**
- reading the persisted authority field at startup and passing it to BackendManager
- one-time self-heal from a poisoned legacy state (see below)
- rewriting the state file back to canonical shape after self-heal so the poison
  does not persist

**Must not own:**
- permanent policy decisions based on observational fields
- wrapper defaults (qz-codex must not second-guess what startup loaded)
- creation of new observational "authority" for use in future startups

**Allowed self-heal order:**
```
1. Use last_good_backend_id if present and catalog-matched.
2. One-time salvage from last_loaded_model only when:
   a. selected_backend_id is observational/poisoned (source = "status_snapshot")
   b. last_good_backend_id is empty
   c. last_loaded_model uniquely matches a catalog entry
3. After salvage: rewrite state to canonical form. Remove the poison.
```

Step 2 is a one-time escape hatch, not a permanent recovery strategy. Once used,
the canonical state must be written back.

**Current gravity well:**
The self-heal logic can grow into a "provenance maze" where each startup tries to
infer the "real" model from increasingly indirect evidence. The fix is to keep
self-heal narrow: two steps, both bounded, then stop. If neither works, fail cleanly
with a clear message.

**Anti-drift rule:** Any new self-heal branch must be accompanied by a test that
shows it rewrites the state back to canonical form. Self-heal that does not clean
up after itself is not self-heal — it is permanent exception handling.

---

### D. Backend lifecycle manager

**Primary file:** `proxy/qz_backend_manager.py`

**Owns:**
- Docker container start / stop / restart
- health-check polling loop
- GPU offload verification from container logs (with a bounded grace window)
- truthful `phase` transitions based on container and health state

**Must not own:**
- model-selection authority (which model to run is decided upstream)
- wrapper policy (qz-codex does not ask BackendManager what model to use)
- compaction behavior

**Phase transitions must be honest about timing:**
- A provisional observation (early log scan returns non-GPU) must not become a
  terminal failure while the container is still running.
- The grace window exists to give the model time to write its GPU log lines.
- After the grace window, either the container has exited (hard failure) or the
  retry deadline has passed (failure after bounded wait). Both are honest.

**Current gravity well:**
`proxy/qz_backend_manager.py` combines lifecycle control, log parsing, GPU gate
logic, and failure promotion in one file. That concentration is manageable while
the file stays focused on "what is the backend doing right now." It becomes a
problem if it starts absorbing model-selection policy, compaction routing,
observability aggregation, or wrapper-side health caching.

**Anti-drift rule:** The manager sets `phase`. The manager does not set the
selected model, the compaction strategy, or the Codex launch model. Any PR that
adds a new field to the BackendManager snapshot for the purpose of influencing
model selection is in the wrong file.

---

### E. qz-codex wrapper

**Primary files:** `scripts/qz-codex`, `scripts/qz-codex-common`

**Owns:**
- CLI argument parsing
- effective-model resolution (explicit arg → proxy selected → bootstrap)
- first-run bootstrap via `POST /qz/model/select-and-restart`
- waiting on genuine `starting`/`loading` states before launching Codex
- failing fast on genuine `failed`/`failed_gpu_not_available` states

**Must not own:**
- authoritative knowledge of which model is selected (ask `/qz/model/status`)
- backend health policy (consume `request_admission_state`, do not re-derive it)
- a duplicate of the proxy's model-state shape
- hardcoded model name lists or fallback catalogs

**Effective-model resolution is strictly ordered:**
```
A. Explicit -m/--model from CLI  →  use exactly; never override
B. QuantZhai has a selected model →  use that
C. No selected model             →  bootstrap (one POST, then wait/poll)
```

Once C runs successfully, it becomes a case-B situation. The wrapper does not keep
a local record of what it bootstrapped; it re-reads the proxy next time.

**Current gravity well:**
`scripts/qz-codex-common` is becoming a large behavioral hub. It now contains
effective-model resolution, loading-wait logic, mismatch handling, bootstrap flow,
and exec preflight. That is already a lot for a shell script library. Each new
"edge case" the wrapper handles is a step toward it becoming a second control plane.

**Anti-drift rule:** If `qz-codex-common` needs to know something the proxy already
knows, add a `/qz/*` endpoint and read it — do not encode the answer in the wrapper.
The wrapper is a consumer of proxy state, not a second implementation of it.

---

## 4. Current gravity wells

### `proxy/qz_responses.py`

This file owns Responses API request handling, SSE forwarding, compaction
orchestration, and tool-call buffering. It attracts unrelated logic because it is
the main entry point for most Codex requests. Every new "do something extra on
request" idea lands here by default. The risk is compaction policy drift (adding
semantic gates), telemetry aggregation that belongs in `qz_telemetry.py`, and
model-status decisions that belong in `qz_model_status.py`.

**Reject by default:** semantic compaction validators, new request-routing branches
for model selection, inline telemetry that does not belong to the response path.

### `proxy/qz_backend_manager.py`

This file owns Docker lifecycle, health polling, and GPU verification. It attracts
logic because it is the only place that knows the backend is up and what it is doing.
New "check if the backend is ready for X" ideas tend to land here as extra fields
on `BackendState` or as extra checks in the health loop.

**Reject by default:** model-selection fields (which model should run is not
BackendManager's question), compaction readiness checks, wrapper-visible policy
hints that should be expressed as `phase` or `request_admission_state`.

### Model-state / model-router / startup preload cluster

`qz_model_state.py`, `qz_model_router.py`, and the startup preload sequence in
`quantzhai_proxy.py` form a cluster that is collectively responsible for authority,
observation, recovery, and catalog resolution. Because they are closely related,
it is easy to blur lines between them. A read from `qz_model_state` that was meant
to be observational can quietly gain authority over routing.

**Reject by default:** new write paths to `selected_backend_id` from health-check
or status-reconciliation code; new fields that mix authority and observation in the
same JSON key; self-heal branches that do not rewrite state back to canonical form.

### `scripts/qz-codex-common`

This shell library owns effective-model resolution and Codex launch preflight. It
attracts logic because `qz-codex` is often the first thing a developer reaches for
when debugging. New "do something before Codex starts" ideas — retrying a failed
backend, re-selecting a model, checking VRAM, patching config.toml — tend to land
here.

**Reject by default:** backend restart logic (use `/qz/backend/restart`), model
catalog logic (use `/qz/model/status`), config.toml mutation beyond what the
current wrapper contract requires, any logic that duplicates what a `/qz/*`
endpoint already provides.

---

## 5. Allowed vs disallowed changes by subsystem

| Subsystem | Allowed | Disallowed | Review trigger |
|---|---|---|---|
| Compaction runtime | Structural payload validation; fallback orchestration between `v3` and `v2`; public error surface for the four allowed fallback reasons | Semantic audit gates on the hot path; new public fallback reasons; reject logic keyed on summary "quality" | "one more validation step"; new `if reason == ...` branch in fallback handler |
| Model-selection authority | Write `selected_backend_id` from explicit operator or user selection; derive `selected_key`/`selected_label` from catalog | Write `selected_backend_id` from health checks, status snapshots, or observation; trust `last_loaded_model` as primary authority | Any code that calls a model-state write from a health-check or compaction path |
| Startup preload / self-heal | Read authority; run one-time bounded self-heal; rewrite state to canonical form | Multi-step inference chains; self-heal paths that do not write canonical state back; reading `last_loaded_model` as primary authority in normal (non-poisoned) startup | Self-heal branch with no "rewrite back to canonical" follow-up |
| Backend lifecycle manager | Phase transitions; health polling; GPU grace window retries; container-running checks | Model-selection policy; compaction routing; wrapper-visible hints beyond `phase` and `gpu_offload_state` | New field on `BackendState` that changes wrapper behavior; non-lifecycle logic in `_do_start`/`_do_stop` |
| qz-codex wrapper | Effective-model resolution; first-run bootstrap; loading wait/poll; hard-fail on genuine failure states | Backend restart logic; duplicate model catalog; config.toml mutation beyond current contract; retry policy that belongs in the proxy | New `if request_admission_state == ...` branch that re-implements proxy logic; hardcoded model names |

**Common smells that should trigger a pause:**
- "just one more fallback reason" — almost always means expanding the public contract unintentionally
- "just one more status field" — check whether the field belongs in the proxy's existing endpoints
- "just one more wrapper-side special case" — the wrapper should read the proxy; it should not know more than the proxy
- "just one more semantic validation step in the hot path" — belongs offline or in a warning-only metadata field

---

## 6. Review checklist for future changes

Before touching any of the subsystems named above, answer these:

1. **What truth is being changed?**
   Name the specific fact: selected model, backend phase, compaction fallback
   reason, etc.

2. **Which subsystem owns that truth?**
   Check the subsystem map above. If the file you are editing is not listed as the
   owner, explain why the change belongs there anyway.

3. **Is observation being promoted to authority?**
   If the change reads a `last_*`, `loaded_*`, or `status_snapshot` field and uses
   it to drive a routing or startup decision, that is the observation-becomes-
   authority pattern. Reject it unless the canonical authority is absent and you
   are running a bounded one-time self-heal that rewrites state afterward.

4. **Is the hot path gaining policy it did not previously own?**
   "Hot path" means: the per-request code that runs on every Codex request. Adding
   a gate, a validator, or a fallback branch to the hot path is a high-cost change.
   The cost must be justified by a concrete bug, not a theoretical improvement.

5. **Could this logic live in offline tooling or diagnostics instead?**
   Semantic validation of compaction quality, detailed GPU log analysis, model VRAM
   estimation — these belong in scripts, smoke tests, or warning-only metadata. Not
   in the request path.

6. **Is this patch increasing overlap between subsystems?**
   If two subsystems will both contain logic about the same question after your
   change, one of them is the wrong place for it. Decide which one owns the answer
   and remove it from the other.

---

## 7. Refactor priorities (not implementation)

These are structural cleanup targets ordered by risk of future drift, not by
urgency. None of these require code changes today; they are the places where the
next round of bugs is most likely to originate.

1. **Narrow the persisted model-state shape further.**
   `qz.model_state.v1` should converge toward `selected_backend_id` as the only
   authority field. The observation and recovery fields are useful but must be
   clearly marked as such in the schema and validated as such in every write path.

2. **Reduce compaction orchestration concentration in `proxy/qz_responses.py`.**
   The file already handles too many concerns. Compaction orchestration could move
   to its own module without changing behavior. That refactor is low risk and would
   make "is this a compaction change or a request-handling change?" obvious.

3. **Keep backend manager startup verification bounded and honest.**
   The grace window is now bounded. The next drift risk is someone adding a new
   "provisionally OK but let's check one more thing" state. The GPU check should
   stay as the only post-health verification step. Any new verification must be
   bounded by the same retry/deadline mechanism, not appended as a serial gate.

4. **Keep `qz-codex-common` from becoming a second control plane.**
   The wrapper is already large. The boundary is: effective-model resolution and
   launch preflight are appropriate; anything that requires the wrapper to maintain
   its own state, its own retry policy, or its own catalog is not.
