# Model Selection and Compaction Correction Plan

Status: proposed corrective plan

## Why this exists

Recent work drifted in two directions at once:

1. The compaction hot path accumulated extra policy/audit behavior that made normal `v3` acceptance too fragile.
2. The model-selection state shape allowed observational data to overwrite selection authority, which poisoned startup preload and broke `qz-codex` preflight.

This document is the corrective contract.

---

## Problem summary

### A. Compaction hot path drift

The compaction runtime is supposed to be a continuity mechanism.
It is **not** supposed to be a second judge, semantic audit engine, or research harness on the default hot path.

The intended runtime behavior is:

- prefer `v3` when the backend is healthy and the model returns a structurally valid anchored summary
- use `v2` only as last-ditch fallback
- keep fallback reasons minimal and stable

### B. Model-state authority corruption

`qz.model_state.v1` currently mixes three kinds of state in one file:

- **authority**: what model the proxy intends to load
- **observation**: what happened last time
- **recovery**: what last worked / what last failed

That can only work if authority is treated as sacred.

It is not.

The observed poisoned state shape was:

```json
{
  "selected_key": "default.gguf",
  "selected_backend_id": "default",
  "selected_label": "default",
  "selected_source": "status_snapshot",
  "selected_reason": "status reconciliation",
  "last_good_key": "",
  "last_good_backend_id": "",
  "last_load_result": "failed",
  "last_loaded_model": "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS"
}
```

This means:

- observational `status_snapshot` data overwrote selected-model authority
- startup preload trusted that poisoned authority and restored `default.gguf`
- `qz-codex` then correctly refused to proceed because the requested model was not the active selected/loaded backend model

---

## Correct runtime contracts

## 1. Compaction runtime contract

Hot path contract:

- `v3` is the preferred path
- `v2` is last-ditch fallback only
- no semantic audit framework on the default hot path
- no duplicate policy layer on top of the compactor

Public fallback reasons must remain exactly:

- `no_source`
- `v3_unavailable`
- `llm_failed`
- `invalid_summary`

Anything more detailed is internal-only and must not become part of the public/runtime contract.

### Explicit non-goals for the hot path

Do not add any of the following as default runtime gates:

- atom-audit hard fail
- structure-audit hard fail
- user-correction-audit hard fail
- error/fix retention gate
- semantic acceptance framework
- strict-mode behavior enabled by default

Those belong in offline analysis, opt-in tooling, or warning-only metadata if reintroduced later.

## 2. Model-selection authority contract

Persisted authority must be minimal.

### Single authority field

The system should converge on:

- `selected_backend_id` as the only persisted selection authority

### Metadata only

These may be kept, but must never drive startup loading decisions:

- `selected_source`
- `selected_at`

### Derived, not authoritative

These should be derived from the model catalog at runtime rather than persisted as parallel authority:

- `selected_key`
- `selected_label`
- `selected_reason`

### Observation only

These must never drive startup directly:

- `last_loaded_model`
- `last_load_result`
- `last_load_error`
- `last_load_error_type`

### Recovery only

Keep recovery narrow:

- `last_good_backend_id`
- optionally `last_good_at`
- failed candidate fields only if they are actually used for recovery / operator messaging

---

## Correct behavioral rules

### Rule 1: `status_snapshot` is observational only

`status_snapshot` must never rewrite selection authority.

If a canonical selected model already exists, a status snapshot may update observation fields only.
It must not overwrite:

- `selected_backend_id`
- derived selected identity metadata

### Rule 2: startup preload restores authority, not observation

Startup preload must restore from persisted authority.
It must not trust observational fields as primary authority.

### Rule 3: startup self-heal is allowed only for poisoned legacy state

If the persisted state is already poisoned, startup may recover in this order:

1. `last_good_backend_id`
2. one-time salvage from `last_loaded_model` **only** when:
   - authority is observational/poisoned
   - `last_good_backend_id` is empty
   - `last_loaded_model` uniquely matches a catalog/backend entry

After such recovery, the state file must be rewritten back into canonical shape so the poison is removed rather than carried forward.

### Rule 4: successful healthy loads must record a usable recovery point

A confirmed healthy load must persist `last_good_backend_id`.
Otherwise recovery is crippled, as seen in the poisoned file above.

---

## Required fixes

## A. Immediate fixes

1. Stop `status_snapshot` from overwriting selection authority.
2. Make startup preload recover from already-poisoned state.
3. Ensure successful healthy loads persist `last_good_backend_id`.
4. Keep `qz-codex` preflight strict; it is correct to refuse mismatched state.

## B. Shape cleanup

Simplify the persisted model-state schema so authority is narrow and obvious.
Reduce overlap between selected, observed, and recovery fields.

## C. Documentation cleanup

Docs must clearly separate:

- authority
- observation
- recovery

and must state that observational reconciliation cannot overwrite canonical selection.

---

## Acceptance criteria

### Compaction

- healthy backend + simple `/v1/responses/compact` smoke returns `localcmp:v3:`
- `output_len == 1`
- fallback reasons remain exactly the four-item contract

### Model selection

- after explicit model selection and proxy restart, startup preload restores that model
- `/qz/model/status` reports the same selected model and ready state
- `qz-codex` preflight passes without demanding a reselect for the already-selected model

### Poisoned-state recovery

Given a file shaped like:

- authority overwritten by `status_snapshot`
- `last_good_backend_id` empty
- `last_loaded_model` set to the real model

startup performs one-time salvage, rewrites canonical state, and does not fall back to `default.gguf`

---

## Non-goals

- no more compaction audit framework on the hot path
- no expanded fallback taxonomy
- no new prompt complexity unless proven necessary
- no status/probe path allowed to overwrite selection authority again

---

## Current priority order

1. preserve model-selection authority
2. make startup preload deterministic
3. keep `v3` compaction working normally
4. simplify the state shape so this class of bug cannot recur easily
