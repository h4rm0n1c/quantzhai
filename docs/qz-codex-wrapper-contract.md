# qz-codex Wrapper Contract

Status: **Active** — see commit `fix: make qz-codex follow selected model and wait for loading`

---

## Overview

`qz-codex` is a thin wrapper around the Codex CLI.  Its job is to bootstrap
the Codex config/catalog from the QuantZhai proxy, resolve the right model to
use, and then launch Codex.

The wrapper must not create spurious failures when the QuantZhai model and the
Codex config happen to be out of sync.  It must also handle genuine loading
states gracefully.

---

## 1. Effective model resolution

When the user does not pass `-m`/`--model` explicitly, `qz-codex` resolves the
effective model in this order:

```
A. Explicit -m/--model from the command line  →  use it exactly (strict)
B. QuantZhai already has a selected model     →  use that model
C. No selected model (fresh install)         →  bootstrap (see §3)
```

Rule B removes two historic failure modes:
- A hardcoded/stale fallback model in the wrapper that differs from the active
  QuantZhai selection.
- A stale `config.toml` model from a previous interactive session being used
  as the effective model for `exec`.

For `exec` mode without an explicit `-m`, the resolved model is injected as
`-m` so Codex does not fall back to its persisted `config.toml` selection.

---

## 2. Loading wait/poll

When the effective model is already selected by QuantZhai but the backend is
still loading, `qz-codex` waits:

```
effective model == selected model
  AND request_admission_state in {"starting", "loading"}
  → print: "[model] is loading — waiting up to Xs..."
  → poll /qz/model/status every 3 seconds
  → when selected_model_ready = true:  launch Codex
  → on timeout:  fail with a clear message
```

This path does **not** require `QZ_CODEX_AUTO_SELECT_MODEL=1` — it is not a
mismatch; it is a normal load-in-progress.

**Timeout** is controlled by `QZ_CODEX_READY_TIMEOUT` (default 300 s).

---

## 3. First-run bootstrap

When `/qz/model/status` returns no selected model:

1. POST `/qz/model/select-and-restart` with `{"source": "qz_codex"}` and no
   explicit `"model"` field — the proxy picks `catalog.selected` or
   `catalog.entries[0]`.
2. Extract the resulting `selected_backend_id` or `selected_key`.
3. Use that as the effective model, then enter the wait/poll loop (§2) until
   the backend is ready.

---

## 4. Hard failure — no wait

If `request_admission_state` is `"failed"` or `"failed_gpu_not_available"`,
`qz-codex` fails immediately with diagnostics (last error type, failed
candidate, rollback info).  It does not spin-wait.

---

## 5. Explicit model requests remain strict

When the user passes `-m`/`--model`:

- If the requested model matches the active QuantZhai backend → proceed (with
  wait/poll if loading).
- If there is a mismatch and `QZ_CODEX_AUTO_SELECT_MODEL=0` (default) →
  print a literal `curl` command the operator can paste, then fail with exit 1.
- If there is a mismatch and `QZ_CODEX_AUTO_SELECT_MODEL=1` → POST
  `/qz/model/select-and-restart` (source=qz_codex, model=requested) and
  re-check.

Explicit model args are never silently overridden by the effective-model
resolution logic.

---

## 6. Key functions

| Function | File | Role |
|---|---|---|
| `qz_resolve_effective_model` | `scripts/qz-codex-common` | B/C resolution; bootstrap |
| `qz_codex_exec_preflight` | `scripts/qz-codex-common` | match check, wait/poll, mismatch handling |
| `qz_exec_model_from_args` | `scripts/qz-codex-common` | extract explicit -m arg from CLI |

---

## 7. `/qz/model/status` fields used

| Field | Used for |
|---|---|
| `selected_backend_id` | effective model identity (authority) |
| `selected_key` | effective model identity (fallback) |
| `selected_model_ready` | active-match check |
| `request_admission_state` | ready / loading / failed routing |
| `backend_loaded_model` | active-match candidates |
| `selected_loaded_mismatch` | candidate filtering |

---

## 8. Non-goals

- Do not add audit gates on the hot path.
- Do not invent new endpoints; reuse `/qz/model/status` and `/qz/model/select-and-restart`.
- Do not require a proxy restart when the selected model is already loaded.
- Do not override an explicit `-m` request with the effective-model resolution.
