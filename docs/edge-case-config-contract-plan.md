# Edge Case Handling and Configuration Contract Plan

## Status

Open planning item. Treat this as a living document.

Update it during the audit as new edge cases, bad error paths, stale state problems, and configuration confusion are found.

## Why this exists

QuantZhai currently works well enough to prove the local Codex/proxy/backend idea, but the edges are too fragile.

A single stale profile alias or removed GGUF file can make Codex unusable. Some error responses dump too much internal detail instead of giving a compact, actionable fix. Configuration, generated state, runtime state, model inventory, and user overrides are spread across the tree in a way that is hard to explain and easy to desynchronise.

This is not a novel class of problem. Long-lived server projects solved the broad shape decades ago: clear data paths, explicit configuration contracts, validation before use, predictable runtime state, useful logs, and user-facing errors that say what broke without vomiting every internal detail into the client.

QuantZhai should borrow that discipline without copying any one project's layout blindly.

## Current problems

### Fragile profile and backend routing

Profile aliases are intentional and useful.

Example:

```text
Codex-visible profile: prompt-compiler
Prompt override:        prompt-compiler.gguf / system_prompt_file
Backend target:         resolved symlink target GGUF stem
```

The failure mode is that `prompt-compiler` can remain visible to Codex while its symlink target points at a backend model that no longer exists in `var/models/`, or points outside the scanned model directory.

That leads to:

```text
requested model: prompt-compiler
matched profile: prompt-compiler.gguf
backend target:  removed-model-stem
result:          noisy 503 / unusable Codex session
```

This should not happen.

Invalid profiles should be hidden, marked unavailable, or rejected with a compact error before they can brick the session.

### Noisy and unhelpful errors

Some errors return large internal blobs, including full model catalogs. That is useful for debugging once, but bad as a normal user-facing error.

The proxy needs two levels of error output:

```text
client-facing error: compact, actionable, safe
server-side log:     detailed, diagnostic, correlated by request id
```

A stale alias error should say something like:

```json
{
  "error": "profile backend missing",
  "profile": "prompt-compiler",
  "reason": "symlink target not found in scanned GGUF models: /path/to/missing.gguf",
  "fix": "Update the profile symlink under var/models or restore the missing target GGUF file."
}
```

It should not include a giant catalog dump by default.

### Scattered configuration and state

Right now the rough categories are blurred:

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

Those things have different lifetimes and different responsibilities.

When they blur together, we get bugs where a generated Codex catalog becomes a second truth, or stale runtime state overrides the proxy's real policy.

### Script sprawl

The scripts directory is already carrying too much. We should not keep adding one-off helper scripts every time a workflow gets awkward.

The long-term direction is to reduce the number of scripts needed for normal use.

The core user-facing shell entry points should remain small and obvious:

```text
qz-up
qz-down
qz-codex
```

But that does not mean lazily folding all other script code into those three shell scripts.

Shared behaviour should move into importable Python modules or a coherent CLI surface, with the shell scripts acting as thin wrappers. The shell layer should launch, stop, and enter Codex. It should not become a pile of embedded business logic.

## Proposed configuration shape

Use a clearer configuration contract with explicit layers.

Proposed tree:

```text
config/
  default/
    model-overrides.json
    prompt-policy.json
    search-policy.json
    codex-catalog-policy.json

  example/
    single-gpu.json
    multi-gpu-3080-v100.json
    prompt-compiler-profile.json
    caveman-profile.json
    local-search.json

  user/
    model-overrides.json
    profiles.json
    prompt-policy.json
    search-policy.json
```

The exact filenames can change during audit. The important part is the contract:

```text
default/ = shipped defaults
example/ = copyable examples, never active unless selected
user/    = local overrides, active by default, not committed
```

Generated and runtime files should not live in the same conceptual layer as user config.

Suggested separation:

```text
config/default/     shipped baseline config
config/example/     documented examples
config/user/        local user overrides
var/generated/      generated Codex catalog/config views
var/state/          persistent runtime state
var/run/            live process/runtime state
var/cache/          disposable caches
var/logs/           logs
var/captures/       request/response captures
var/models/         local model files and profile symlinks
```

This is a proposed destination, not an immediate patch instruction.

## Required audit before implementation

Do not start the config restructure blindly.

First audit the current data paths.

Track at least:

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
searxng capabilities loading
```

For each path, record:

```text
source files read
runtime files written
cache files written
user-visible output
failure modes
current error message
preferred error message
whether recovery is safe
```

## Current data path audit

Date: 2026-05-07

This is the current state before the broader config refactor. It is a map of
where truth lives today, not the destination layout.

### Path ownership boundaries

Tracked source/default files:

```text
config/example/codex-config.toml
config/example/qwenzhai-models.json
config/default/benchmark-prompts.json
config/default/model-overrides.json
config/example/model-overrides.json
config/default/search-policy.json
```

Local user/runtime inputs:

```text
.env
config/user/model-overrides.json
var/model-overrides.json compatibility fallback
var/models/*.gguf
var/models/*.gguf symlinks used as profiles
QZ_* and SEARXNG_* environment variables
```

Generated files:

```text
var/model-inventory.json
var/codex-home/config.toml
var/codex-home/model-catalogs/qwenzhai-models.json
```

Runtime state:

```text
var/model-state.json
var/backend-state.json
var/run/qz-runtime-state.json
in-memory proxy telemetry bus
```

Debug/replay outputs:

```text
var/captures/*
var/logs/*
var/benchmarks/latest-summary.json
var/codex-home/sqlite/*
```

### Current path map

| Path | Source read | Runtime/generated write | User-visible output | Failure mode | Preferred recovery |
| --- | --- | --- | --- | --- | --- |
| model discovery | `QZ_MODEL_DIR`, default `var/models`; `config/default/model-overrides.json`; `config/user/model-overrides.json`; legacy `var/model-overrides.json` when user file is absent; optional `config/example/model-overrides.json` behind `QZ_LOAD_EXAMPLE_MODEL_OVERRIDES`; legacy `config/qz-model-overrides.*.json` files are read only as compatibility fallback | `var/model-inventory.json` | `/v1/models`, `/qz/status`, generated Codex catalog | missing model dir, bad GGUF metadata, stale cache, broken symlink | compact scan error, invalid profile hidden, cache is regenerated not trusted |
| profile alias resolution | scanned `*.gguf` entries, override aliases, symlink filename/stem | inventory entry fields `profile_symlink`, `profile_valid`, `profile_error`, `backend_target` | Codex model picker slug stays profile identity | old synthetic aliases or backend ids leak into Codex-visible names | no synthetic alias layer; profile name is model-dir filename/stem only |
| symlink profile target resolution | `var/models/<profile>.gguf` symlink target plus real scanned GGUF paths | inventory stores `symlink_target_path`, target backend id, validity | direct request either routes to target or fails compactly | target missing/outside scan bricks session or falls through to wrong backend | mark invalid before catalog generation; no silent fallback |
| prompt override loading | merged override manifest; inline prompt fields; prompt files resolved relative to repo root unless absolute; optional static `turn_harness`/`turn_harnesses` selected per profile from `turn_harness_definitions` | prompt contract telemetry, latest request contract capture, `metadata.qz_turn_harness` | forwarded request instructions, generated Codex `base_instructions`, newest eligible user turn after the first user turn | missing prompt file silently empties profile prompt in some generated paths; unknown turn harness names must not silently masquerade as active | effective config view must report loaded/missing/failed prompt files; turn harness metadata reports active/unknown/applied/skipped |
| profile reasoning visibility | per-model `reasoning_stream_format` or `hide_reasoning_stream`; optional `allow_client_reasoning_override` / `force_default_reasoning_level` | request metadata `qz_reasoning_stream_format`; prompt contract/runtime metrics | Codex-visible grey reasoning block, active reasoning level | roleplay/private-thought profiles leak internal text through summary-mode reasoning; client request silently raises profile reasoning effort | default remains proxy `summary`; private profiles set `hidden`; locked profiles ignore client `reasoning.effort` |
| Codex catalog generation | `config/example/codex-config.toml`, `var/model-inventory.json`, default/user overrides | `var/codex-home/config.toml`, `var/codex-home/model-catalogs/qwenzhai-models.json` | Codex model list, context window, prompt metadata | generated catalog becomes second truth or keeps stale profile/context | always regenerate from proxy catalog policy; never route from generated catalog |
| runtime status generation | live proxy catalog/router/backend status, `QZ_MODEL_STATE_PATH`, `QZ_BACKEND_STATE_PATH`, telemetry state | `/qz/status` response; telemetry events | `qz-top`, `qz-thoughts`, doctor checks, manual curl | early status reports env defaults as facts; Codex CLI `/status` may not reflect proxy-calculated token/context usage | keep source fields for context/model/load state; unknown beats fake certainty; audit whether Codex consumes usage through Responses `usage`, model catalog metadata, or another client-visible field |
| backend state persistence | proxy/backend observations and startup scripts | `var/backend-state.json` | `/qz/status.backend`, runtime snapshot | stale backend state outlives process | proxy live facts win; file is fallback/debug only |
| model-state persistence | selected model/profile from catalog/proxy | `var/model-state.json` | default selection after restart, status summary | removed last-selected profile can steer startup toward invalid entry | validate selected entry against current scan before use |
| capture writing | request/response/proxy events when `QZ_CAPTURE_MODE` enabled | `var/captures/latest-*.json`, `.raw`, `.txt`, `.log` | debug files and monitor fallback | captures mistaken for live truth, stale latest files mislead monitors | telemetry endpoints first; captures are replay/debug fallback only |
| logs | proxy/script diagnostics, Docker output when inspected | `var/logs/*`, capture log files | doctor/top/thoughts fallback detail | logs become parsing contract | logs are diagnostic, never authoritative status schema |
| search policy loading | `SEARXNG_POLICY`; `scripts/qz-env` default `config/default/search-policy.json`; optional per-model `search.policy_file` and `search.default_profile` in model overrides; proxy fallback checks `config/default/search-policy.json`, old docs path, then old proxy path | web route captures include selected policy metadata | web-search behaviour and route diagnostics | old local env can still point at docs path; bad per-model policy file could silently alter search if not surfaced | report docs-path policy as compatibility warning; bad per-model policy falls back to base policy and records error in route metadata |
| searxng capabilities loading | `SEARXNG_CAPABILITIES`; proxy fallback `proxy/searxng-capabilities.json` | web route captures/cache in memory | tool capability decisions | empty env means implicit proxy file fallback, hard to inspect | effective config view reports capability source and missing/disabled state |

### Current blur points

The biggest remaining contract risks are:

```text
search policy has moved to `config/default/search-policy.json`; old docs path is compatibility only. Per-model policy files are profile overrides, not model-emitted paths.
var/model-inventory.json is generated, but several scripts read it as a policy view.
var/codex-home/config.toml is generated from an example, then patched in place.
var/run/qz-runtime-state.json is a startup/status snapshot, not live truth.
prompt file load failures are not yet surfaced in one shared effective-config report.
```

### Smallest safe next move

Do not move model files, profile symlinks, or Codex-visible slugs.

The next safe config-layer move is to add one shared effective path/config report
fed by existing path rules. It should report:

```text
active value
source layer
source file/path
missing file warnings
generated/runtime/debug classification
```

Only after that report exists should files move toward `config/default/`,
`config/example/`, `config/user/`, `var/generated/`, `var/state/`, and
`var/cache/`. The first actual file move was search policy, because it was
active config living under `docs/`, and it does not affect model routing or
profile identity.

Initial inspection surface:

```text
GET /qz/config/effective
GET /qz/config/paths
```

This endpoint reports the active path, source layer, classification, existence
state, env override, and config warnings for current config/state/generated/debug
paths. It is read-only and must not become a routing authority.

## Minimal fixes before full refactor

Before the larger config restructure, prioritise the small safety fixes that stop current breakage.

### 1. Validate profile backend targets

Symlink profiles under `var/models/` must be validated against scanned GGUF entries or backend inventory.

A profile should expose fields like:

```text
profile_valid
profile_error
profile_symlink
backend_target
```

Invalid profiles should not be shown as healthy in the Codex model picker.

### 2. Return compact actionable errors

Replace giant catalog-dump 503 responses with compact errors for known failure classes.

Keep detailed dumps in logs or captures, correlated by request id.

### 3. Do not silently fallback unless configured

If a profile target disappears, do not quietly run a different model.

Silent fallback would make prompt/profile behaviour unpredictable.

Fail clearly when the symlink target is missing or outside scanned models.

### 4. Add an effective config view

Add a way to inspect the active merged configuration.

The exact surface is undecided, but the behaviour should be:

```text
show active value
show source file/layer
show overridden values where useful
show validation warnings
```

Do not add another permanent shell helper script just for this if it can be part of a coherent Python CLI or existing command surface.

## Guidance for creating profiles safely

A safe profile needs four separate concepts kept separate:

```text
profile identity:   what Codex sees and the user selects
prompt policy:      which system prompt/instructions apply
backend target:     what GGUF/backend model actually runs
runtime limits:     context length and profile-specific tuning
```

Example profile intent:

```json
{
  "turn_harness_definitions": {
    "roleplay-private-thoughts": "Profile reminder: Continue roleplay. Keep internal reasoning, planning, uncertainty, and self-checks private. Reply only in the established character format."
  },
  "models": {
    "prompt-compiler.gguf": {
      "label": "prompt-compiler",
      "runtime_context_length": 262144,
      "system_prompt_file": "config/user/prompts/prompt-compiler.md"
    },
    "roleplay-character.gguf": {
      "label": "roleplay-character",
      "system_prompt_file": "config/user/prompts/character.md",
      "prompt_append_files": ["config/user/prompts/roleplay-initial-harness.md"],
      "turn_harnesses": ["roleplay-private-thoughts"]
    }
  }
}
```

Rules:

```text
Create the profile as a symlink under var/models/.
Do not use the backend model id as the Codex-visible profile name unless that is truly the profile identity.
Do not expose a profile as healthy unless its backend target validates.
Do not let generated Codex metadata override proxy routing policy.
Do not hide missing prompt files; report them clearly.
```

## What to do when a profile target disappears

When a GGUF is removed, renamed, or moved:

```text
1. Detect that the profile backend target is missing.
2. Mark the profile invalid in the scanned inventory.
3. Hide or clearly mark it unavailable in generated Codex metadata.
4. Return a compact actionable error if a client still requests it.
5. Log the full diagnostic detail server-side.
```

Do not make Codex unusable.

Do not dump the entire catalog into the normal client response.

Do not silently select a different backend unless an explicit fallback policy exists.

## Script reduction policy

Avoid adding new shell scripts unless there is a strong reason.

The target user-facing shell surface should be small:

```text
qz-up
qz-down
qz-codex
```

Supporting logic should move toward:

```text
importable Python modules
coherent CLI entry points
clear config contracts
documented generated files
```

The goal is not to delete scripts by stuffing all their logic into the remaining three. The goal is to reduce accidental shell glue and make the behaviour testable, reusable, and easier to reason about.

Before adding or expanding a script, ask:

```text
Is this really a user-facing command?
Could this be a Python module function?
Does this duplicate proxy or config policy?
Will this become a second truth?
Does this need to survive the config refactor?
```

## Documentation requirements

This document must stay linked from:

```text
docs/README.md
AGENTS.md
```

Once the refactor is complete, update any script or documentation that reads, writes, generates, or describes configuration files.

That includes:

```text
README.md
AGENTS.md
docs/README.md
runtime observability docs
profile docs
search docs
qz-codex/qz-up/qz-down behaviour notes
any generated Codex catalog documentation
```

## Proxy ownership of model setup — architectural issue

**Status:** Documented. Must be addressed alongside the config/var cleanup and
script sprawl reduction. Do not start the broader refactor without a plan for
this.

### The problem

Critical setup work is currently split across shell scripts that run *before*
Codex starts. The proxy is passive — it serves whatever state the scripts
established at launch. Adding a new model, updating an override, or creating a
profile symlink requires restarting the entire stack for the change to take
effect.

The kuato model addition illustrated this directly: the GGUF landed, the
symlink was created, the Codex catalog was regenerated — but the running
llama.cpp backend didn't know about it until `qz-up` was restarted.

**Current split:**

```text
qz-up            starts llama.cpp + proxy (model inventory fixed at launch)
qz-codex-common  scans inventory, generates Codex catalog, sets env vars
                 (runs once per qz-codex invocation, not kept live)
proxy            passively serves whatever state was configured at startup
```

**Target split:**

```text
qz-up            starts llama.cpp + proxy
proxy            owns model inventory, catalog generation, override loading
                 rescans models/ at startup and on /qz/models/refresh
                 regenerates Codex catalog when inventory changes
                 hot-reloads config/user/model-overrides.json on demand
                 exposes /qz/catalog as a live view, writes catalog file
qz-codex-common  thin launcher — asks proxy for catalog state, sets
                 CODEX_HOME, execs codex
```

### What the proxy should absorb

- **Model inventory scanning** — currently `python3 proxy/qz_model_catalog.py
  scan` called by shell scripts at each Codex launch. The proxy should scan
  at startup and expose a `/qz/models/refresh` endpoint that rescans without
  restart.

- **Codex catalog generation** — currently `python3 proxy/qz_codex_catalog.py
  ...` called by `qz-codex-common`. The proxy should own the generated catalog
  file (`var/codex-home/model-catalogs/qwenzhai-models.json`), write it at
  startup and after each refresh, and expose its current state.

- **Override loading** — currently read once at proxy startup. The proxy should
  accept a reload signal (or watch the file) so new per-model overrides,
  labels, and turn harnesses take effect without restart.

- **Profile symlink validation** — already performed in the proxy, but the
  result is only surfaced to Codex via the catalog file that scripts generate.
  The proxy should regenerate the catalog file itself when validation state
  changes.

### What must stay in scripts

Codex reads the catalog **from a file** before the first request. The proxy
cannot inject the catalog into Codex at runtime — it must be a file on disk.
The proxy's job is to own generating and updating that file, not to serve it
over HTTP to Codex directly (at least until Codex supports a remote catalog
endpoint, which it currently does not).

`qz-up`, `qz-down`, and `qz-codex` stay as thin entry points. Process
management and environment bootstrapping belong in shell. Business logic
(scanning, catalog building, validation, override resolution) belongs in the
proxy.

### Why this must be done alongside config/var cleanup

The script-owned setup work is tangled with the current config/var layout.
Scripts know about specific paths (`var/model-inventory.json`,
`var/codex-home/model-catalogs/`, `config/user/model-overrides.json`). Moving
those paths without moving the setup ownership creates a broken intermediate
state. The two changes must be planned together.

### Acceptance criteria for this item

```text
Adding a new GGUF to var/models/ and calling /qz/models/refresh makes it
available in Codex without restarting qz-up.

Updating config/user/model-overrides.json and calling /qz/models/refresh
applies the new overrides without restart.

qz-codex-common contains no Python subprocess calls for catalog or
inventory work — it reads state from the proxy or from a file the proxy
already maintains.

The generated Codex catalog is always consistent with the proxy's current
model state.
```

## Next steps

1. Treat this plan as a living document.
2. Start with an audit of data paths, failure modes, and config/state files.
3. Prioritise minimal error-report improvements before the larger config restructure.
4. Hide or compactly fail invalid profiles before touching broader layout.
5. Design the config contract after the audit, not before.
6. Reduce script sprawl as part of the refactor, without moving shell mess into the three remaining entry scripts.

## Acceptance checks

Before this work is considered done:

```text
Removing a backend GGUF does not brick Codex.
Invalid profiles are hidden or clearly marked unavailable.
Direct requests for invalid profiles get compact actionable errors.
Generated Codex metadata matches proxy routing policy.
Effective config can be inspected with source/layer information.
User overrides are clearly separated from defaults and generated runtime files.
No new one-off shell scripts were added without explicit justification.
Profile creation guidance exists and matches actual behaviour.
```

## Related documents

- `docs/bugs/stale-profile-server-alias.md`
- `docs/observability-streaming-bugfix-agenda.md`
- `docs/runtime-observability-notes.md`
- `AGENTS.md`
