# Proxy-owned Model Selection Authority

Date: 2026-05-21
Issue: #65 follow-up — model-selection cleanup
Status: A-design — audit + final design. No code changes.

---

## 1. Problem

Model selection is split across too many places that drift apart in practice:

- `QZ_MODEL_KEY` in `.env`
- `var/model-state.json` (`QZ_MODEL_STATE_PATH`)
- `var/backend-state.json` (`QZ_BACKEND_STATE_PATH`)
- manifest `default_key` + per-entry `default` flag
- `qz-codex -m / --model / --profile`
- Codex CLI persisted picker state
- `backend.loaded_model` from `/qz/control-plane`
- `models.selected` / `selected_backend_id` from `/qz/control-plane`
- `load_last_selected_model()` falling back to `loaded_model`
- backend autostart not knowing about model identity at all

After `#65 D` moved backend lifecycle into the proxy, the proxy *runs* the
container but model selection is still scattered. There is no single answer
to "which model does the operator want loaded, and is it actually loaded?"

## 2. Audit — what each path currently does

### 2.1 `QZ_MODEL_KEY` (env override every refresh)

`proxy/qz_model_catalog.py:852-854` reads `QZ_MODEL_KEY` on every
`ModelCatalog.refresh()`. When set, it bypasses `last_selected` entirely:

```python
requested = query or os.environ.get("QZ_MODEL_KEY")
last_selected = "" if requested else load_last_selected_model(self.root)
self.selected, self.reason = choose_default(self.entries, self.manifest,
                                            requested, last_selected)
```

**Effect before the cleanup:** `.env`'s `QZ_MODEL_KEY` could override operator
selection on every proxy restart. The canonical runtime mutation endpoint is
now `/qz/model/select-and-restart`; legacy `/qz/models/select` returns 410.

### 2.2 `var/model-state.json` writers/readers

**Writers** (`proxy/qz_model_router.py`):
- `_persist_model_state()` at lines 220–234 — writes `selected_key`,
  `selected_backend_id`, `selected_label`, `selected_path`, `selected_reason`,
  `source`, `updated_at`.
- Called from `resolve_model_selection`, `_reconcile_status_state`, and reload
  paths.

**Readers**:
- `proxy/qz_model_catalog.py:41-53` `load_last_selected_model()` — reads
  `selected_key`, `selected_backend_id`, **and `loaded_model`** as equivalent
  authority. The `loaded_model` fallback means a stale observation field can
  silently become "the selected model" after the operator's selection is lost.
- `proxy/quantzhai_proxy.py` `_preload_last_model()` — reads
  `selected_key`/`selected_backend_id`, resolves the launch filename, calls
  `BackendManager.set_launch_model(...)`, then begins autostart. `QZ_MODEL_KEY`
  is a seed only when no valid persisted selection exists.

### 2.3 `var/backend-state.json`

Written by `_persist_backend_state()` at `proxy/qz_model_router.py:249-270`
with fields including `selected_key`, `selected_backend_id`, `loaded_model`,
`state`, `error`, `health_status`, `restarted`. Currently both a selection
record AND an observation record on the same disk file — these should not be
collapsed.

### 2.4 Manifest `default_key` and per-entry `default`

`proxy/qz_model_catalog.py:752-760` in `choose_default()`:

```python
default_key = manifest.get("default_key")
if isinstance(default_key, str) and default_key:
    match = match_model(valid_entries, default_key)
    if match is not None:
        return match, f"default_key={default_key}"

for entry in valid_entries:
    if entry.get("default"):
        return entry, f"default flag on {entry_identity(entry)}"
```

These are catalog config — fine as a *last* fallback, but currently sit on
the same precedence ladder as runtime selection.

### 2.5 Existing `/qz/models/*` endpoints

Current endpoint status:

- `POST /qz/models/refresh` (line 1463)
- `POST /qz/models/load`     (legacy removed, 410 Gone)
- `POST /qz/models/select`   (legacy removed, 410 Gone)
- `GET /qz/model/status`
- `POST /qz/model/select`
- `POST /qz/model/reload`
- `POST /qz/model/select-and-restart`

`/qz/model/select-and-restart` is the canonical runtime mutation path.

### 2.6 qz-codex preflight (visibility only)

`scripts/qz-codex:65-82` does:

```bash
"${QZ_ROOT}/scripts/qz-wait-ready" --catalog --model "$_preflight_model"
```

`qz-wait-ready` only checks that the model appears in `/v1/models`. That is
**catalog visibility, not active backend selection**. A model can be in the
catalog and not be the one llama-server has loaded — exec proceeds and the
request hits a different model.

### 2.7 `qz_proxy_loaded_model()` falls back to `loaded_model`

`scripts/qz-codex-common:237-269` `qz_proxy_loaded_model()` reads
`/qz/control-plane`, prefers `models.selected`, then `selected_backend_id`,
**then `backend.loaded_model` as last resort.** This conflates "what the
operator picked" with "what the backend currently has", reinforcing the
authority confusion in 2.2.

### 2.8 Codex CLI picker state

Codex CLI persists its last-used model in `~/.qz-codex/codex-home/`. That state
is a client UI convenience. It is currently *not* wired into proxy authority
(good), but agents have sometimes inferred otherwise. Document this explicitly:
**Codex picker state is client UI only; the proxy never reads it.**

### 2.9 Backend autostart

`BackendManager` launches the llama-server container with
`-m /models/<selected>.gguf`. The selected model is resolved before backend
autostart from persisted `qz.model_state.v1`, then `QZ_MODEL_KEY` only as a
first-run seed, then catalog default. Model switching is a backend restart
with a new `-m`.

`scripts/qz-up` does **not** select a model — confirmed by grep. Keep it
that way.

## 3. Symptoms this audit explains

- `qz-codex exec -m MODEL` succeeds despite the backend actually serving a
  different model (visibility ≠ active selection — 2.6).
- Operator selection is silently overridden after a proxy restart if
  `QZ_MODEL_KEY` is treated as authority instead of a one-shot seed (2.1).
- An old `loaded_model` from `var/backend-state.json` becomes "the selected
  model" after a state-file edit or partial corruption (2.2).
- Too-large model fails after partial GPU load → backend HTTP `/health`
  returns 200 → looks healthy → request goes to a backend that never created
  a context (no current classification).
- Adding more shell scripts to paper over this would make `scripts/` worse;
  see §11.

## 4. Final precedence (proxy is authority)

1. **Explicit proxy selection** (highest):
   - `POST /qz/model/select`
   - `POST /qz/model/select-and-restart`
   - `qz-codex` auto-select only when `QZ_CODEX_AUTO_SELECT_MODEL=1`
2. **Persisted proxy-owned selection state**:
   - `selected_key` / `selected_backend_id` from `var/model-state.json`
   - Must NOT include `loaded_model` as a fallback.
3. **`QZ_MODEL_KEY` as initial seed only**:
   - Consulted only when no valid persisted selection exists.
   - Once an operator selection is persisted, `QZ_MODEL_KEY` must not
     override it on subsequent restarts.
4. **Catalog config**:
   - manifest `default_key`
   - per-entry `default` flag
5. **Safe fallback** (lowest):
   - report "no selected model" or a single-model auto-pick with a clear
     `selected_source=fallback` reason.
   - never silently choose a bogus model.

### Hard rule

`QZ_MODEL_KEY` is a one-shot seed for first run / unset state. It is **not**
operator authority and must not override persisted proxy selection.

## 5a. Post-Slice F polish — failed-model recovery and catalog freshness

This section captures the post-cleanup polish committed on 2026-05-22.

### Failed-model recovery (qz.model_state.v1 extension)

The schema gains two field groups for explicit failure recovery:

- `last_good_*` — the most recently confirmed-loaded selection
  (`last_good_key`, `last_good_backend_id`, `last_good_label`,
  `last_good_source`, `last_good_loaded_at`)
- `failed_candidate_*` — the most recent failed attempt
  (`failed_candidate_key`, `failed_candidate_backend_id`,
  `failed_candidate_label`, `failed_candidate_at`)

Both are observation-only.  Selection authority remains `selected_*`.

Behaviour:

- **Successful load** → `mark_load_success` clears `last_load_error*`,
  updates `last_good_*` from current selection, and clears
  `failed_candidate_*`.
- **Failed load** → `mark_load_failure` records `failed_candidate_*`,
  sets `last_load_result=failed`, and (when
  `rollback_to_last_good=True` and `last_good_*` exists) rolls
  `selected_*` back to `last_good_*` with
  `selected_source=last_good_source or "fallback"` and a canonical
  `selected_reason="rolled back from failed candidate <id>"`.

`/qz/model/select-and-restart` accepts `{"model": ..., "rollback_on_failure": true|false}`
(default `true`).  Failed-candidate responses include the recovery surface:

```json
{
  "rollback_performed": true,
  "recovery_available": true,
  "last_good_key": "kuato.gguf",
  "last_good_backend_id": "kuato",
  "failed_candidate_key": "too-large.gguf",
  "failed_candidate_backend_id": "too-large",
  "recommended_recovery_action": "Active selection rolled back to last_good model kuato.gguf. ..."
}
```

`/qz/control-plane` `operator_hints` gain "rolled back to last-good …" or
"no last-good model is available" when the failed-candidate context applies.

qz-codex auto-select (`QZ_CODEX_AUTO_SELECT_MODEL=1`) prints the classified
failure block on failure: `last_load_error_type`, `failed_candidate`,
`last_good` (or `<none>`), and `recommended_recovery_action`.

### Codex catalog freshness

`/qz/codex/client-config` `model_catalog` block gains:

- `mtime_ms` — catalog file mtime (already present)
- `freshness.catalog_mtime_ms`
- `freshness.source_mtime_ms` (model inventory)
- `freshness.catalog_age_seconds`
- `freshness.stale` (boolean)
- `freshness.reason` — `catalog_missing` or `source_newer_than_catalog`
- `freshness.remediation` — `POST /qz/codex/model-catalog/refresh`
- `refresh_url` — absolute URL to the catalog-only refresh endpoint

New endpoint: `POST /qz/codex/model-catalog/refresh` runs
`ModelCatalog.refresh()` + the existing Codex catalog generator and
returns the new client-config in the response body.  It is
metadata-only — does not touch backend selection or trigger loads.

qz-codex bootstrap (`_qz_http_bootstrap`) checks `freshness.stale` from
the initial `/qz/codex/client-config` GET.  When stale and
`QZ_CODEX_REFRESH_CATALOG=1` (default), it POSTs the refresh URL once
and re-fetches client-config before downloading the catalog.  Setting
`QZ_CODEX_REFRESH_CATALOG=0` suppresses the auto-refresh.

No new shell scripts.  `scripts/qz-model*` invariant intact.

### Direct backend mode + observability surface

BackendManager now has one production launch path: the container starts with
`-m /models/<selected>.gguf` for a single bound model.  Model switches become
`docker rm -f` + `docker run` with a new `-m`, which is fast and robust under
VRAM pressure. `QZ_BACKEND_MODEL_MODE` is deprecated compatibility only; the
router/`--models-dir` load/unload path is not supported.

`/qz/model/select-and-restart` in direct mode:
1. Validates and persists the selection.
2. Calls `BackendManager.set_launch_model(key, backend_id, path_basename)`.
3. Calls `BackendManager.restart()` to launch a new container with `-m`.
4. Polls `phase` until healthy/failed (bounded by `QZ_MODEL_LOAD_TIMEOUT`).
5. Runs the existing log classifier + recovery (rollback to `last_good_*`).

Legacy `/qz/models/select` and `/qz/models/load` return `410 Gone` with the
instruction to use `/qz/model/select-and-restart`. `/qz/models/refresh`
remains catalog metadata refresh only and does not mutate runtime model state.

`/qz/model/status` and `/qz/control-plane.models` gain:
`backend_model_mode`, `launch_model_*`, `model_switch_state`
(`idle/selecting/restarting/loading/loaded/failed/rolled_back`),
`active_load_operation` (`none/backend_restart/rollback_restart`),
plus `last_good_*` / `failed_candidate_*` already added by the post-F
polish. They also expose `selected_model_ready`, `request_admission_state`,
and runtime death fields. qz-top renders a compact `MODEL` panel for
ready/admission/switch/op/gpu plus an `RCVRY` row for failed-candidate context.

See `docs/backend-lifecycle-control-plane.md §15.9` for the full direct launch
contract.

## 5. `qz.model_state.v1` schema

`var/model-state.json` (proxy-owned, selection authority only):

```json
{
  "schema": "qz.model_state.v1",
  "selected_key": "kuato",
  "selected_backend_id": "kuato",
  "selected_label": "Kuato Q4",
  "selected_source": "operator",
  "selected_at": "2026-05-21T10:00:00Z",
  "selected_reason": "POST /qz/model/select",
  "runtime_context_length": 262144,
  "last_load_result": "loaded",
  "last_load_error": null,
  "last_load_error_type": null,
  "last_loaded_model": "kuato"
}
```

### Field semantics

| Field | Authority? | Notes |
|---|---|---|
| `selected_key` | yes | Catalog key chosen by operator/seed |
| `selected_backend_id` | yes | Backend identity for that key |
| `selected_label` | no | Display only |
| `selected_source` | no (provenance) | One of: `operator`, `qz_codex`, `env_seed`, `config_default`, `fallback`, `migration` |
| `selected_at` | no | ISO8601 of last selection write |
| `selected_reason` | no | Free text: endpoint name + caller hint |
| `runtime_context_length` | no | Active context length |
| `last_load_result` | no | `loaded` / `failed` / `unknown` |
| `last_load_error` | no | Concise error string when `last_load_result=failed` |
| `last_load_error_type` | no | `insufficient_vram` / `context_creation_failed` / `unknown` |
| `last_loaded_model` | **observation only** | NEVER consulted as selection authority |

### Rules

- `selected_key` / `selected_backend_id` are the authority.
- `last_loaded_model` is observation only.
- `loaded_model` from any old state file must not become selection authority.
- An invalid persisted selection (key not in catalog) falls back to the
  next precedence step and `selected_source=fallback`.
- Migration from current file shape:
  - If file has `selected_key` or `selected_backend_id`, use them.
  - If file has only `loaded_model`, **drop it** and treat as no selection.
  - Write `schema: qz.model_state.v1` on first write after migration.
  - Migration writes record `selected_source=migration`.

### `selected_source` values

| Value | When written |
|---|---|
| `operator` | explicit `POST /qz/model/select` or `select-and-restart` from a non-qz-codex caller |
| `qz_codex` | qz-codex auto-select path (`QZ_CODEX_AUTO_SELECT_MODEL=1`) |
| `env_seed` | first-run seed from `QZ_MODEL_KEY` |
| `config_default` | manifest `default_key` or per-entry `default` flag |
| `fallback` | safe single-model or alphabetical fallback |
| `migration` | written by the migration path when promoting old state |

## 6. New endpoints

Existing `/qz/models/*` endpoints stay (backwards compat). New singular
endpoints expose the cleaned-up authority model:

### `GET /qz/model/status`

```json
{
  "schema": "qz.model_status.v1",
  "configured_env_model": "kuato",
  "selected_key": "kuato",
  "selected_backend_id": "kuato",
  "selected_label": "Kuato Q4",
  "selected_source": "operator",
  "selected_at": "2026-05-21T10:00:00Z",
  "backend_loaded_model": "kuato",
  "selected_loaded_mismatch": false,
  "model_visible": true,
  "profile_valid": true,
  "backend_phase": "healthy",
  "backend_gpu_state": "gpu",
  "last_load_result": "loaded",
  "last_load_error": null,
  "last_load_error_type": null,
  "recommended_action": null
}
```

`recommended_action` is non-null when the operator should act:
`"call POST /qz/model/select-and-restart"`,
`"selected model too large, reduce QZ_CONTEXT or pick a smaller model"`,
etc.

### `POST /qz/model/select`

Body: `{"model": "<key|backend_id>"}`

- Validates model is in catalog and profile is valid.
- Writes `var/model-state.json` with `selected_source=operator`
  (or `qz_codex` if called from auto-select path with that header).
- Does **not** restart the backend.
- Returns the `/qz/model/status` payload after the write.

### `POST /qz/model/reload`

- Uses the current proxy-selected model to reload/restart the backend.
- Returns status after reload.
- 409 if no selected model exists.

### `POST /qz/model/select-and-restart`

Body: `{"model": "<key|backend_id>"}`

- Validates model.
- Writes selected state.
- Restarts backend via `BackendManager.restart()`.
- Polls until backend reports loaded/healthy or failed (timeout = current
  model-load timeout).
- On failure: returns `last_load_result=failed`, classified error type,
  and a clear failure message.

## 7. Backend autostart with proxy authority

`_preload_last_model()` becomes:

1. Read `var/model-state.json`.
2. If `selected_key` exists and matches a valid catalog entry → use it
   (`selected_source` left as-is).
3. Else if `QZ_MODEL_KEY` is set → seed with it; write state with
   `selected_source=env_seed`.
4. Else if `manifest.default_key` or per-entry `default` → use it; write
   state with `selected_source=config_default`.
5. Else → safe fallback; write state with `selected_source=fallback`.
6. Call `BackendManager.set_launch_model(...)`.
7. Begin autostart; direct `-m` launch binds llama-server to the selected model.
8. Watch backend logs for load result; record in `last_load_*` fields.

`qz-up` remains startup-only. It may *print* selected model status but must
not write model state itself.

## 8. qz-codex changes

### 8.1 Replace visibility-only preflight

Today `scripts/qz-codex:65-82` calls `qz-wait-ready --model MODEL` which
only checks `/v1/models` visibility. Replace with:

```text
1. GET /qz/model/status
2. If MODEL == selected_key AND MODEL == backend_loaded_model AND no mismatch:
     continue
3. Else if QZ_CODEX_AUTO_SELECT_MODEL=1:
     POST /qz/model/select-and-restart {"model": MODEL}
     poll status until loaded/healthy or failed
     on failure: print classified error and exit non-zero
4. Else:
     print mismatch message (below) and exit non-zero
```

### 8.2 Mismatch message

```text
qz-codex: requested model MODEL is not the active backend model.
  Active selected model: <selected_key>
  Backend loaded model:  <backend_loaded_model>
  Selected source:       <selected_source>
Set QZ_CODEX_AUTO_SELECT_MODEL=1 to let qz-codex select/restart automatically,
or call the proxy endpoint manually:
  curl -sS -X POST -H 'Content-Type: application/json' \
       -d '{"model":"MODEL"}' \
       http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/model/select-and-restart
```

Print the literal `curl` command (filled with actual host/port) so the
operator can paste it. No shell wrapper needed.

### 8.3 Interactive qz-codex

Interactive sessions (no `-m`) keep using `qz_proxy_loaded_model()` for the
Codex `-c model="…"` arg. The Codex picker remains client UI state only —
it is never read by the proxy.

## 9. `/qz/control-plane` cleanup

Add to `models` section:

- `configured_env_model` — current `QZ_MODEL_KEY` value (informational)
- `selected_source` — provenance from `qz.model_state.v1`
- `selected_loaded_mismatch` — boolean
- `selection_reason` — promote existing `selected_reason`

Add to `backend` section:

- `model_load_failed` — bool
- `load_error`        — concise string when failed
- `load_error_type`   — `insufficient_vram` / `context_creation_failed` / `unknown`
- `insufficient_vram` — bool convenience flag
- `selected_model_failed` — bool

Add to `backend_manager` section:

- `selected_model` — the model the BackendManager has on record as last selected
  for launch (informational; the backend itself is model-agnostic at launch).

Add to `operator_hints`:

- `Selected model differs from loaded backend model.` — when mismatch.
- `QZ_MODEL_KEY is only a seed; persisted proxy selection is active.` — when
  `configured_env_model != selected_key`.
- `Selected model failed to fit VRAM/context. Pick a smaller model or reduce QZ_CONTEXT.`
  — when `load_error_type=insufficient_vram`.
- `qz-codex must select/restart through proxy or fail.` — when a recent
  qz-codex request asked for a non-active model.

## 10. Load/fit failure classification

`proxy/qz_model_load_failure.py` scans recent container logs and classifies
the latest relevant failure.  `BackendManager.fetch_recent_logs()` returns the
log buffer (or `None` when the container is gone) so endpoints don't need to
shell out themselves.

Substring patterns:

| Pattern | `load_error_type` |
|---|---|
| `cudaMalloc failed` | `insufficient_vram` |
| `failed to allocate CUDA buffer` | `insufficient_vram` |
| `failed to allocate buffer for kv cache` | `insufficient_vram` |
| `failed to fit params to free device memory` | `insufficient_vram` |
| `alloc_tensor_range: failed to allocate` | `insufficient_vram` |
| `common_init_from_params: failed to create context` | `context_creation_failed` |
| `common_init_result: failed to create context` | `context_creation_failed` |
| `failed to create context with model` | `context_creation_failed` |
| `failed to initialize the context` (standalone) | `context_creation_failed` |

**Promotion rule:** when a VRAM pattern matches at or before a context-creation
pattern in the same log buffer, the failure is reported as `insufficient_vram`
— the context error is the downstream effect of the VRAM allocation failure.

On detection:
- `last_load_result = "failed"`
- `last_load_error = <one-line excerpt>`
- `last_load_error_type = <classification>`
- `model_load_failed = true` in control-plane backend section
- `selected_model_failed = true`
- Backend HTTP `/health` returning 200 is **not** sufficient to mark the
  selected model loaded. Inspect logs after select/reload before claiming
  the selected model is active.

## 11. Cleanup checklist — broken implicit behaviour to remove

- Drop `loaded_model` from `load_last_selected_model()` precedence
  (`proxy/qz_model_catalog.py:49`).
- Make `QZ_MODEL_KEY` consulted only when no persisted selection exists
  (`proxy/qz_model_catalog.py:852-854`).
- Replace `qz-codex` visibility-only preflight with `/qz/model/status` check.
- Drop the `backend.loaded_model` last-resort branch in
  `qz_proxy_loaded_model()` for selection-authority decisions (still fine
  for display).
- Catalog `selected` flag in `/v1/models` must not imply "backend loaded".
- Migrate `var/model-state.json` to schema `qz.model_state.v1`.
- Split observation fields (`last_loaded_model`, `last_load_*`) cleanly
  from authority fields in `var/model-state.json`.
- `qz-up` confirmed model-selection-clean; document the invariant.

## 12. Script-sprawl invariant — no new model-selection scripts

**Do not add** any of:

- `scripts/qz-model`
- `scripts/qz-select-model`
- `scripts/qz-model-status`
- `scripts/qz-load-model`

All operator model commands go through the proxy. If a curl example is
needed in docs or error messages, embed the literal curl invocation — that
is cheaper, more explicit, and does not grow `scripts/`.

`qz-up` = startup only. `qz-down` = shutdown only. `qz-backend` = backend
lifecycle only. `qz-codex` is the model-selection client integration point.

A future CLI-consolidation issue may collapse `qz-backend` / `qz-codex` /
etc. into one `scripts/qz` entrypoint with subcommands. That is **out of
scope** for this work; do not anticipate it by adding wrappers.

## 13. Slice plan

| Slice | Content |
|---|---|
| **A-design** | ✅ this document; audit of current paths; final precedence; schema; endpoint shape; no-new-scripts invariant |
| **B-state** | ✅ `proxy/qz_model_state.py` with `qz.model_state.v1` reader/writer/migration; atomic writes; `state_from_selection` validates `SELECTED_SOURCES`; `update_load_observation` keeps authority fields untouched; `load_last_selected_model` no longer reads `loaded_model`; `_persist_model_state` routes through the new module and observation fields are preserved across selection writes; 32 new tests; 2988 total pass |
| **C-endpoints** | ✅ `proxy/qz_model_status.py` builds `qz.model_status.v1`; new endpoints `GET /qz/model/status`, `POST /qz/model/select`, `POST /qz/model/reload`, `POST /qz/model/select-and-restart`; `ModelCatalog.refresh()` now implements the canonical precedence (persisted selection beats `QZ_MODEL_KEY`); `/qz/control-plane` exposes `configured_env_model`, `selected_source`, `selected_at`, `backend_loaded_model`, `selected_loaded_mismatch`, `selection_reason`, `model_visible`, `profile_valid`, `restart_required`, and backend load-failure surface; operator hints emit QZ_MODEL_KEY-seed warning + mismatch + insufficient-VRAM; 44 new tests; 3032 total pass |
| **D-qz-codex** | ✅ `qz_codex_exec_preflight` helper in `scripts/qz-codex-common` GETs `/qz/model/status`, compares against selected_key / selected_backend_id / backend_loaded_model with `selected_loaded_mismatch` honoured; mismatch + no `QZ_CODEX_AUTO_SELECT_MODEL` → exit 1 with literal `curl -sS -X POST … /qz/model/select-and-restart`; mismatch + `QZ_CODEX_AUTO_SELECT_MODEL=1` → POST `/qz/model/select-and-restart` with `source=qz_codex`; old `qz-wait-ready --catalog --model` preflight removed; 17 new tests (structural + behavioural with mock proxy); 3049 total pass |
| **E-load-failure** | ✅ `proxy/qz_model_load_failure.py` classifies recent container logs into `insufficient_vram` / `context_creation_failed` (VRAM-before-context promotion rule); `BackendManager.fetch_recent_logs()` exposes the log buffer; `/qz/model/reload` and `/qz/model/select-and-restart` run the classifier after the resolve+load path and update `last_load_*` observation fields; failure responses are **HTTP 409** with the classified `last_load_*` payload (selection authority preserved); successful reload clears previous load_error; 22 new tests; 3071 total pass |
| **F-smoke** | ✅ Full cold-start smoke on real hardware (kuato.gguf good / 27B Q5_K_M too-large) exercised every endpoint and surface; uncovered and fixed 4 bugs: classifier false-positives (`common_fit_params … n_gpu_layers already set by user`, `cudaMalloc … retrying without pipeline parallelism`), stale `last_load_*` across proxy restart, `selected_source` downgrade by legacy `_persist_model_state` (now preserves canonical sources when `selected_backend_id` matches), and qz-codex exit-code propagation (`! cmd; then exit $?` always exited 0 — fixed to `|| exit $?`); 5 new regression tests; 3081 total pass. Results recorded in §16. |

## 16. Slice F smoke results (2026-05-22)

Hardware: 2× GPU — RTX 3080 (10 GiB) + V100 (16 GiB); QZ_DOCKER_CMD via
`sudo -n /usr/local/sbin/qz-docker-quantzhai` allowlist.

Models exercised:
- **known-good**: `kuato.gguf` → OpenYourMind-Qwen3.6-35B-A3B-Kuato Q4_K_S (~20 GiB)
- **known-too-large** on this hardware: `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-Q5_K_M.gguf`

| Smoke | Result |
|---|---|
| A — cold start | ✅ Proxy + backend autostart + `/qz/model/status` + control-plane fields. Migration from pre-v1 state file worked (`selected_source=migration`). |
| B — POST select-and-restart kuato | ✅ HTTP 200, ok=true, `last_load_result=loaded`. (Initial run exposed the classifier false-positive — fixed.) |
| C — `qz-codex exec -m kuato.gguf` | ✅ Codex returned `codex-ok` from the active model. |
| D — qz-codex mismatch without auto-select | ✅ Exit 1 (after fixing `! cmd; exit $?` bug), stderr includes literal `curl -sS -X POST … /qz/model/select-and-restart`. |
| E — `QZ_CODEX_AUTO_SELECT_MODEL=1` | ✅ qz-codex POSTed select-and-restart with `source=qz_codex`; the chosen second model was too large on this hardware so the endpoint returned 409 (same code path as Smoke F). |
| F — too-large model | ✅ HTTP 409, `last_load_result=failed`, `last_load_error_type=insufficient_vram`, `recommended_action` mentions VRAM/QZ_CONTEXT, control-plane `backend.{model_load_failed, insufficient_vram, selected_model_failed}=true`, operator hint emitted, selection authority preserved. |
| G — recover to kuato | ✅ HTTP 200, `last_load_*` cleared, `selected_loaded_mismatch=false`. |
| H — QZ_MODEL_KEY seed-only | ✅ `QZ_MODEL_KEY=Qwen3.6-27B-…Q5_K_M.gguf` in `.env`; persisted kuato wins across restart; `configured_env_model` distinct from `selected_key`; operator hint "QZ_MODEL_KEY is only a seed" emitted; **`selected_source=operator` survived two restart cycles**. |
| I — shutdown + restart | ✅ `scripts/qz-down` graceful path stopped proxy + removed container; `scripts/qz-up` restored kuato with `selected_source=operator` intact. |

Bugs found and fixed during the smoke:

1. **Classifier false-positives.** `common_fit_params: failed to fit params to free device memory: n_gpu_layers already set by user to 999, abort` is informational under user-override of `-ngl`; `cudaMalloc failed: out of memory` followed by `sched_reserve: compute buffer allocation failed, retrying without pipeline parallelism` is recovered by llama.cpp. The classifier now ignores lines containing known informational qualifiers and applies a latest-success-wins rule (same shape as the GPU offload check in `BackendManager`).
2. **Stale `last_load_*` across proxy restart.** Historical router preload posted the persisted selection to the legacy `/qz/models/select` endpoint at startup; that handler did not run `_record_load_observation`, so stale failure observations from previous runs survived a restart. The current direct launch path sets the BackendManager launch model before autostart and the legacy endpoint returns 410.
3. **`selected_source` downgrade.** `qz_model_router._persist_model_state` historically wrote legacy source strings (`resolve_model_selection`, `status_snapshot`, `load_backend_model`) that overwrote canonical `operator` / `qz_codex` / `migration` tags. The writer now preserves an existing canonical source when `selected_backend_id` is unchanged.
4. **qz-codex exit-code propagation.** `if ! qz_codex_exec_preflight; then exit $?; fi` — the `!` inverts the function's exit code, so `$?` inside the `then` branch is always 0 and the script ran Codex anyway. Replaced with `qz_codex_exec_preflight … || exit $?` so a mismatch actually halts the command.

Audit findings (not blocking; logged for follow-up):

- **Preload and status-reconcile paths still write legacy source strings.** Once an operator selection has set a canonical source, subsequent legacy writes preserve it. But before any explicit operator/qz_codex selection, the state file shows `selected_source=resolve_model_selection` or `status_snapshot`. Cosmetic only — control-plane and `/qz/model/status` work correctly.
- **27B Q5_K_M took >120s to evict kuato during auto-select.** When the current model occupies most of VRAM, llama-router's unload-then-load sequence can exceed `QZ_MODEL_LOAD_TIMEOUT=120`. The endpoint correctly returned a load failure; the smoke worked around it by force-restarting between models. Not a model-selection bug — a llama-router/timeout tradeoff.

## 14. F-smoke acceptance

From fully stopped:

```text
1. scripts/qz-down --force
2. scripts/qz-up
3. wait for backend healthy
4. POST /qz/model/select-and-restart {"model":"kuato"}
5. GET /qz/model/status → selected=kuato, loaded=kuato, mismatch=false
6. qz-codex exec -m kuato 'echo done'              → success
7. qz-codex exec -m other 'echo'                   → fail with clear message + literal curl
8. QZ_CODEX_AUTO_SELECT_MODEL=1 qz-codex exec -m other 'echo'
                                                    → select+restart, succeed
9. POST /qz/model/select-and-restart {"model":"too-large"}
                                                    → load_error_type=insufficient_vram
                                                    → selected_model_failed=true
                                                    → operator_hint visible
```

## 15. Non-goals

- BrainCase, memory, web_search/retrieve untouched.
- No new shell scripts for model selection.
- No automatic model-fit solver — just classify failures clearly.
- Do not restore Docker ownership to `qz-up`.
- Do not change context/KV/tensor-split defaults.
- `qz-codex` must not silently mutate backend unless `QZ_CODEX_AUTO_SELECT_MODEL=1`.

## Related

- `proxy/qz_model_catalog.py` — `load_last_selected_model`, `choose_default`, `ModelCatalog`
- `proxy/qz_model_router.py` — `_persist_model_state`, `_persist_backend_state`, selection plumbing
- `proxy/quantzhai_proxy.py` — `_preload_last_model`
- `proxy/qz_request_router.py` — existing `/qz/models/*` endpoints
- `scripts/qz-codex` / `scripts/qz-codex-common` — qz-codex preflight + `qz_proxy_loaded_model`
- `docs/backend-lifecycle-control-plane.md` — backend lifecycle context
- `docs/state-and-memory-architecture-review-deepseek.md` — prior architecture audit
- `docs/backend-control-plane-audit.md` — existing control-plane shape
