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
| capture writing | request/response/proxy events when `QZ_CAPTURE_MODE` enabled | `var/captures/latest-*.json`, `.raw`, `.txt`, `.log` | debug files and monitor fallback; `/qz/config/effective.capture` reports active capture mode and disabled state | captures mistaken for live truth, stale latest files mislead monitors when capture mode is `off` | telemetry endpoints first; captures are replay/debug fallback only; effective config must make disabled captures obvious |
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

### Current state of /qz/models/refresh

`/qz/models/refresh` already exists and already does half the job:
it calls `catalog.refresh()` which rescans `var/models/`, reloads the
manifest/overrides, and writes the inventory cache. What it does **not**
currently do is regenerate the Codex catalog file
(`var/codex-home/model-catalogs/qwenzhai-models.json`). That step is still
only triggered by the `qz_codex_catalog.py` shell subprocess in
`qz-codex-common`.

The fix is additive: after `catalog.refresh()`, call the same catalog
generation logic inline. One HTTP POST to `/qz/models/refresh` then does
the complete reload cycle — inventory rescan, override reload, Codex catalog
file update — and the model picker sees the change on its next open.

This is the standard pattern: nginx reload, systemd daemon-reload. The
endpoint exists; it just needs to do the full job.

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

## Profile-Bundle Config Design

Status: Design/documentation pass. No implementation in this section.

This section records the design direction for a future profile-bundle config
format. It does not replace or change the current `model-overrides.json` loader,
the current proxy routing, the current `memory_domain` contract, or Codex-visible
profile slugs.

### Why not foreign-key JSON soup

The alternative to profile bundles is to split each profile concept into linked
files. For example:

```text
profiles/prompt-compiler/backend.json
profiles/prompt-compiler/prompt.json
profiles/prompt-compiler/harness.json
profiles/prompt-compiler/memory.json
profiles/prompt-compiler/runtime.json
```

That layout is technically tidy but hostile to users, admins, and agents alike.

Problems:

```text
Understanding one profile requires reading five files.
Adding a profile requires creating five files and linking five cross-references.
Renaming a profile requires finding and updating every reference across the tree.
Portable export requires knowing exactly which files belong together.
No single file represents a deployable profile unit.
A future persona or project bundle cannot be expressed as one copyable artifact.
```

Foreign-key config makes the admin experience look like a relational schema
migration. Config is not a relational schema.

### Why not one giant config JSON

The opposite extreme is to describe all profiles, all harnesses, all defaults,
and all metadata in one monolithic file:

```text
config/user/quantzhai.json
```

That layout is also hostile:

```text
Adding Alice's profile requires editing the single shared file.
Removing Alice's profile requires surgery on the same file, with risk of
  damaging unrelated profiles.
The file grows without bound as profiles accumulate.
Portable export is all-or-nothing; you cannot extract one profile cleanly.
Future persona or project profiles cannot be packaged as standalone units.
```

### Proposed future shape: qz.profiles.v1

The preferred direction is a shallow, cohesive profile-bundle format.

Top-level keys:

```text
schema           required — version marker: "qz.profiles.v1"
defaults         optional — values applied when a profile omits them
shared_harnesses optional — named inline harness text, reusable across profiles
profiles         required — map of profile-slug to profile-bundle objects
```

A profile bundle may contain:

```text
backend    backend GGUF target and target policy
runtime    context_length, reasoning_level, and tuning overrides
prompts    system_file, append_files, turn_harnesses
behavior   reasoning_stream_format, privacy/display policy
memory     memory binding and policy (domain, enabled, mode)
metadata   notes, label, portability/export hints
```

A profile should be understandable by reading one profile object.

Example `config/user/profiles.json`:

```json
{
  "schema": "qz.profiles.v1",
  "defaults": {
    "system_prompt_file": "prompts/codex-core-qwenified.md",
    "runtime": {
      "default_reasoning_level": "medium"
    }
  },
  "shared_harnesses": {
    "caveman-ultra-lock": "Caveman ultra locked. Persist. No drift/filler/repeated drafts. Preserve exact tech terms, paths, commands, code, errors, versions, URLs, quotes. Code/artifacts normal style unless asked. Normal clarity for danger/confusion, then resume.",
    "roleplay-private-thoughts": "Profile reminder: Continue roleplay. Keep internal reasoning, planning, uncertainty, and self-checks private. Reply only in the established character format."
  },
  "profiles": {
    "prompt-compiler": {
      "backend": {
        "gguf": "prompt-compiler.gguf",
        "target_policy": "symlink_or_file"
      },
      "runtime": {
        "context_length": 262144,
        "default_reasoning_level": "medium"
      },
      "prompts": {
        "system_file": "config/user/prompts/prompt-compiler.md"
      },
      "memory": {
        "domain": "coding"
      },
      "metadata": {
        "label": "prompt-compiler",
        "notes": "Prompt engineering profile. Create var/models/prompt-compiler.gguf as a symlink to a real GGUF."
      }
    },
    "caveman": {
      "backend": {
        "gguf": "caveman.gguf",
        "target_policy": "symlink_or_file"
      },
      "runtime": {
        "context_length": 262144,
        "default_reasoning_level": "medium",
        "allow_client_reasoning_override": true
      },
      "prompts": {
        "append_files": ["config/default/prompts/caveman-mode.md"],
        "turn_harnesses": ["caveman-ultra-lock"]
      },
      "memory": {
        "domain": "coding"
      },
      "metadata": {
        "label": "caveman",
        "notes": "Compact Codex profile. Create var/models/caveman.gguf as a symlink to a real GGUF."
      }
    },
    "roleplay-character": {
      "backend": {
        "gguf": "roleplay-character.gguf",
        "target_policy": "symlink_or_file"
      },
      "runtime": {
        "default_reasoning_level": "low",
        "allow_client_reasoning_override": false
      },
      "prompts": {
        "system_file": "config/user/prompts/character.md",
        "append_files": ["config/user/prompts/roleplay-initial-harness.md"],
        "turn_harnesses": ["roleplay-private-thoughts"]
      },
      "behavior": {
        "reasoning_stream_format": "hidden"
      },
      "memory": {
        "domain": "roleplay"
      },
      "metadata": {
        "label": "roleplay-character",
        "notes": "Private roleplay profile. Keep prompts under config/user/."
      }
    }
  }
}
```

Profile slug (`"prompt-compiler"`, `"caveman"`, etc.) is the Codex-visible
profile identity. It replaces the GGUF filename as the primary config key.

The backend GGUF target (`backend.gguf`) is the routing target. It is distinct
from the profile slug, mirroring the current symlink-profile design. The proxy
maps profile slug to backend target at routing time, not at config load time.

### Optional profiles/*.json include-directory design

The profile-bundle format should support both single-file and directory-based
layout per config layer.

**Single-file layout:**

```text
config/default/profiles.json
config/example/profiles.json
config/user/profiles.json
```

**Directory-based layout:**

```text
config/default/profiles/
config/example/profiles/
config/user/profiles/
```

Each `*.json` file in a `profiles/` directory is a valid profile config. Files
may contain one profile bundle or a small related group.

Example directory layout:

```text
config/user/profiles/alice.json          Alice personal profile bundle
config/user/profiles/project-netts.json  Project Netts profiles
config/default/profiles/caveman.json     Default caveman profile shape
config/example/profiles/roleplay.json    Example roleplay profile shape
```

Load order and precedence:

```text
1. config/default/profiles.json
2. config/default/profiles/*.json  sorted alphabetically
3. config/user/profiles.json
4. config/user/profiles/*.json     sorted alphabetically
```

User layer wins over default layer. Within the same directory, later files
sorted alphabetically win on duplicate profile slugs.

Rules:

```text
config/example/ is never active unless QZ_LOAD_EXAMPLE_MODEL_OVERRIDES is set.
Duplicate profile slugs in the same layer should warn or error, not silently
  roulette to whichever file was loaded last.
Duplicate profile slugs across layers (user wins over default) are expected
  and are intentional overrides.
A missing profiles/ directory is not an error.
A missing profiles.json is not an error.
Both layouts (single-file and directory) may coexist; they are merged.
Adding or removing a profile must not require editing any file other than the
  profile file itself.
Cross-layer overrides should be visible in /qz/config/effective.
```

The directory layout gives the Apache/nginx-style pattern: drop `alice.json`
into `config/user/profiles/`, and Alice's profile is live without touching any
existing file. Remove the file to remove the profile.

One profile per file is recommended for portability. Small related groups (e.g.,
`alice-coding` and `alice-roleplay` in one `alice.json`) are allowed when the
profiles share enough context to belong together and will always be deployed
together.

### Mapping from current model-overrides.json to profile bundles

Current `config/example/model-overrides.json` shape (showing one entry):

```json
{
  "system_prompt_file": "prompts/codex-core.md",
  "turn_harness_definitions": {
    "caveman-ultra-lock": "...",
    "roleplay-private-thoughts": "..."
  },
  "models": {
    "prompt-compiler.gguf": {
      "label": "prompt-compiler",
      "runtime_context_length": 262144,
      "system_prompt_file": "config/example/prompts/prompt-compiler.md"
    }
  }
}
```

Equivalent future `qz.profiles.v1` shape:

```json
{
  "schema": "qz.profiles.v1",
  "defaults": {
    "system_prompt_file": "prompts/codex-core.md"
  },
  "shared_harnesses": {
    "caveman-ultra-lock": "...",
    "roleplay-private-thoughts": "..."
  },
  "profiles": {
    "prompt-compiler": {
      "backend": { "gguf": "prompt-compiler.gguf" },
      "runtime": { "context_length": 262144 },
      "prompts": { "system_file": "config/example/prompts/prompt-compiler.md" },
      "metadata": { "label": "prompt-compiler" }
    }
  }
}
```

Field mapping:

```text
models["prompt-compiler.gguf"]     → profiles["prompt-compiler"] + backend.gguf
label                              → metadata.label
runtime_context_length             → runtime.context_length
system_prompt_file (per-model)     → prompts.system_file
prompt_append_files                → prompts.append_files
turn_harnesses                     → prompts.turn_harnesses
turn_harness_definitions (top)     → shared_harnesses (top)
system_prompt_file (top-level)     → defaults.system_prompt_file
memory_domain                      → memory.domain (preferred) or flat memory_domain
default_reasoning_level            → runtime.default_reasoning_level
allow_client_reasoning_override    → runtime.allow_client_reasoning_override
reasoning_stream_format            → behavior.reasoning_stream_format
disable_system_prompt              → prompts.disable (or behavior.disable_system_prompt)
notes                              → metadata.notes
```

During the transition, the loader should accept both formats. The proxy detects
`"schema": "qz.profiles.v1"` to activate bundle loading; the existing `models`
key loading remains active for `model-overrides.json` compatibility.

### Singular memory_domain preservation

The `memory_domain` contract from `docs/codex-context-memory-contract.md` must
be preserved exactly. Do not change, extend, or weaken it as part of this
profile-bundle work.

Current semantics that must be carried forward:

```text
memory_domain is explicit config only.
Missing memory_domain == isolated. No fallback, no inference, no gremlin.
Same explicit memory_domain across profiles/models = intentional shared scope.
profile_id + memory_domain = profile-private scope.
workspace_id + memory_domain = workspace scope.
Backend model id is distinct from profile id.
Do not infer memory_domain from model name, profile name, client name,
  tool declarations, prompt text, or request path.
```

In profile bundles, the **preferred future representation** is:

```json
"memory": {
  "domain": "coding"
}
```

The flat backwards-compatible form remains valid in both `model-overrides.json`
and `profiles.json`:

```json
"memory_domain": "coding"
```

When loading a profile bundle, the resolver maps `memory.domain` to
`memory_domain` before the scope decision so all existing scope logic sees the
same field name. There is no behavioral difference between the two forms.

Multiple profiles sharing the same domain is intentional and correct:

```json
{
  "profiles": {
    "prompt-compiler": { "memory": { "domain": "coding" } },
    "caveman":         { "memory": { "domain": "coding" } }
  }
}
```

`prompt-compiler` and `caveman` share the `coding` memory domain. The domain is
a namespace, not an owner. Multiple profiles may bind to the same domain.

**Do NOT introduce `domains: []` (a multi-domain array)** as part of this design
pass. The singular domain binding is the current contract. A multi-domain
extension requires an explicit separate design review before it can enter the
codebase.

### Memory config vs memory state/cache/run split

Profile bundles configure memory policy. They do not store memory state.

**Memory config in the profile bundle:**

```text
memory.domain    explicit domain label, e.g. "coding" or "roleplay"
memory.enabled   whether memory tools are active for this profile (optional)
memory.mode      read_write / read_only / isolated (optional, default from domain policy)
```

**Memory state outside config (var/, never in git):**

```text
var/state/memory/<domain>/    durable memory records for this domain
var/cache/memory/<domain>/    recall cache, disposable
var/run/memory/<domain>/      live in-flight memory state
```

This split is load-bearing:

```text
Profile bundles can be committed, shared, or exported without leaking state.
Memory state stays local, private, and outside version control.
Changing memory policy (domain, mode) in the bundle does not destroy existing state.
A domain can be shared by many profiles; the state is owned by the domain label,
  not by any single profile file.
```

### Portability and export concept

A profile bundle should eventually support a portable export/package concept.

A package may contain:

```text
profile bundle JSON
referenced prompt files (prompts.system_file, prompts.append_files)
optional memory state snapshot for the explicit domain
manifest describing included prompts, domain, and whether state is included
```

Memory state in a package is an explicit opt-in choice, not automatic. An export
describes which domain it carries, not all domains.

Example package manifest (design placeholder — implementation deferred):

```json
{
  "schema": "qz.profile.package.v1",
  "profile_slug": "alice",
  "domain": "alice",
  "includes_prompt_files": ["prompts/alice-system.md"],
  "includes_memory_state": true,
  "memory_state_domain": "alice",
  "notes": "Alice persona bundle with personal memory state."
}
```

Actual memory contents live under `var/state/memory/` or equivalent, not under
`config/`. The config directory should contain only policy and prompt text that
is safe to commit.

### Compatibility expectations

During the transition from `model-overrides.json` to `qz.profiles.v1`:

```text
config/user/model-overrides.json continues to work for all existing setups.
profiles.json and profiles/*.json are additive new input paths.
The loader merges all active config sources in precedence order.
A setup with no profiles.json and no profiles/ directory is valid.
A setup with only model-overrides.json (no profiles.json) continues to work.
A setup with only profiles.json (no model-overrides.json) is the future target.
The proxy resolves memory_domain from memory.domain or flat memory_domain,
  preferring the bundle subobject form when both exist.
The proxy routes using the backend GGUF target from the bundle, not the slug.
Profile slug remains the Codex-visible profile identity.
Cross-layer overrides (user profile overrides default profile of same slug)
  are visible in /qz/config/effective.
```

Do not force a migration. The goal is to make the new format available first,
then migrate example configs as a follow-up, then document the migration path for
users.

### Next smallest implementation slice (profile-bundle)

The smallest safe implementation step is to add `memory_domain` plumbing to the
existing model-overrides loader. This does not require moving to `qz.profiles.v1`
or changing any file layout.

Steps:

```text
1. In qz_model_catalog.py, read memory_domain from each model overrides entry.
2. Store memory_domain on the catalog entry alongside label and context_length.
3. Expose memory_domain in /v1/models response entries.
4. Expose memory_domain in /qz/status model entries.
5. Surface memory_domain in /qz/config/effective per-model or per-profile output.
6. Pass memory_domain through to resolve_memory_domain() in qz_codex_metadata.py.
7. Add tests: missing memory_domain resolves to "isolated"; explicit memory_domain
   passes through correctly; same domain across two model entries is valid.
```

This slice:

```text
Does not change any config file layout.
Does not change profile slugs or GGUF routing.
Does not require implementing qz.profiles.v1.
Unblocks Phase 1 SQLite memory_domain scope binding.
Confirms the plumbing path before the larger profile-bundle refactor.
```

The `qz.profiles.v1` schema and `profiles/*.json` directory loader can be
implemented as a follow-up PR once the memory_domain plumbing tests are green.

## Generated artifact staleness design (#5 Slice C-design)

Date: 2026-05-19. Status: design only — no runtime implementation yet.

This section defines what "stale" means for the three generated artifacts reported
by `/qz/config/effective`, and specifies the proposed warning codes, staleness
conditions, and acceptance criteria for a future Slice C implementation.

---

### Artifact inventory and generation paths

| Artifact | Record name | Default path | Generator | Triggered by |
|---|---|---|---|---|
| `model_inventory_cache` | `model_inventory_cache` | `var/model-inventory.json` (or `$QZ_MODEL_INVENTORY_CACHE`) | `ModelCatalog.refresh()` → `write_cache()` in `qz_model_catalog.py` | Proxy startup, `POST /qz/models/refresh`, `POST /qz/models/select` |
| `codex_model_catalog` | `codex_model_catalog` | `$CODEX_HOME/model-catalogs/qwenzhai-models.json` (default: `var/codex-home/model-catalogs/qwenzhai-models.json`) | `qz_codex_catalog.generate(inventory_path, catalog_dst, config_dst)` via `_refresh_codex_catalog()` in `qz_request_router.py` | `POST /qz/models/refresh`, proxy startup (once at model-selection time) |
| `codex_config` | `codex_config` | `$CODEX_HOME/config.toml` (default: `var/codex-home/config.toml`) | Same `generate()` call as `codex_model_catalog` — patches `config.toml` in-place | Same as `codex_model_catalog` (both updated atomically) |

All three are classified as `generated` / cache / view. They are not source of truth
for routing decisions.

---

### Input dependencies

**`model_inventory_cache`**

```text
Inputs (code-confirmed):
  QZ_MODEL_DIR scan results        — model files found in var/models/
  Merged manifest from load_manifest():
    config/default/model-overrides.json (or first-found fallback)
    config/user/model-overrides.json   (or $QZ_MODEL_OVERRIDES)
    config/example/model-overrides.json (only if QZ_LOAD_EXAMPLE_MODEL_OVERRIDES set)
  Profile symlinks in var/models/  — resolved during scan_models()
  Previously selected model key    — from var/model-state.json
```

**`codex_model_catalog`**

```text
Inputs (code-confirmed):
  model-inventory.json             — direct input to generate()
  assemble_instruction_stack()     — prompt policy for base_instructions field
    → reads the same default/user overrides to build prompt
    → prompt file contents change the catalog content
  Note: catalog does NOT re-read model_dir directly; all model data comes from inventory
```

**`codex_config`**

```text
Inputs (code-confirmed):
  Existing config.toml content     — read in full, then patched
  catalog_dst path string          — embedded as model_catalog_json = "..."
  Static cleanup rules             — removes stale model_context_window/model_max_output_tokens entries
  Initial creation: qz-codex-common copies config/example/codex-config.toml on first run
```

`codex_model_catalog` and `codex_config` are always written together in one `generate()` call.
If either write fails, the other may be inconsistent.

**`qz-codex-common` note:** `qz-codex-common` calls `POST /qz/models/refresh` and checks
the response; it does NOT read `model-inventory.json` directly. Script is a thin client,
not a catalog authority.

---

### V1 staleness rule proposals

**Rule 1: `stale_model_inventory_cache`**

```text
Condition:
  model-inventory.json exists AND
  max(mtime of default_overrides, mtime of model_overrides_user) > mtime of model-inventory.json

Note on model_dir mtime: model_dir mtime is NOT used for staleness. Directory mtime
is updated by ls, find, stat, and other read operations, causing excessive false positives.
Override file comparison is sufficient and much more reliable.

False positives: nearly none — override files are only written on explicit user edit.
False negatives: new/removed GGUF files in model_dir are not detected without a scan.
  This is acceptable: /qz/models/refresh is cheap and the operator can trigger it.

Severity: advisory — Codex is usable but may show a stale model list.
Remediation: POST /qz/models/refresh
```

**Rule 2: `stale_codex_catalog`**

```text
Condition:
  codex_model_catalog exists AND model-inventory.json exists AND
  mtime(model-inventory.json) > mtime(codex_model_catalog)

Note: model-inventory.json is only written by ModelCatalog.refresh(). Its mtime
reliably reflects the last successful scan. Comparing to catalog mtime is robust.

False positives: very low — both are only written by refresh flows.
False negatives: prompt policy changes (system_prompt_file content change) without
  triggering inventory refresh will not be detected. Acceptable for v1.

Severity: advisory — Codex model list may be stale but proxy routing is live.
Remediation: POST /qz/models/refresh
```

**Rule 3: `stale_codex_config`**

```text
Condition:
  codex_config exists AND codex_model_catalog exists AND
  mtime(codex_model_catalog) > mtime(codex_config)

Rationale: both are updated atomically by generate(). If catalog is newer than config,
a write failure occurred. This is a defensive check, not a common case.

False positives: rare — only if generate() partially failed.
False negatives: config.toml is hand-edited after generation.
  Acceptable: that scenario is unusual and the operator owns that edit.

Severity: advisory — Codex may not find the updated catalog.
Remediation: POST /qz/models/refresh
```

---

### Proposed warning names and payloads

```python
# stale_model_inventory_cache
{
    "warning": "stale_model_inventory_cache",
    "path": str(inventory_path),
    "stale_against": ["model_overrides_default", "model_overrides_user"],
    "artifact_mtime_ms": 1716000000000,       # model-inventory.json mtime
    "newest_input_mtime_ms": 1716000001000,   # newest override file mtime
    "remediation": "POST /qz/models/refresh",
}

# stale_codex_catalog
{
    "warning": "stale_codex_catalog",
    "path": str(catalog_path),
    "stale_against": ["model_inventory_cache"],
    "artifact_mtime_ms": 1716000000000,       # qwenzhai-models.json mtime
    "newest_input_mtime_ms": 1716000001000,   # model-inventory.json mtime
    "remediation": "POST /qz/models/refresh",
}

# stale_codex_config
{
    "warning": "stale_codex_config",
    "path": str(config_path),
    "stale_against": ["codex_model_catalog"],
    "artifact_mtime_ms": 1716000000000,       # config.toml mtime
    "newest_input_mtime_ms": 1716000001000,   # qwenzhai-models.json mtime
    "remediation": "POST /qz/models/refresh",
}
```

---

### Authority / routing constraints

```text
Staleness warnings must not change routing.
  The proxy's live in-memory catalog state is always authoritative.
  model-inventory.json is a cache, not a live routing fact.
  codex_model_catalog is a view, not a routing authority.
  codex_config is a Codex-client hint, not a proxy policy fact.

Generated artifacts must remain labelled "generated" / cache / view.
  The staleness warnings are advisory operator signals only.
  A stale artifact does not mean the proxy is broken — it means a refresh is helpful.

/qz/models/refresh is the remediation path for all three warnings.
  Staleness warnings must never trigger refresh automatically.
  The operator (or qz-codex-common) must call the endpoint explicitly.
```

---

### Future Slice C implementation boundary

**Slice C may:**

```text
- Add _artifact_staleness_check(artifact_path, input_paths) pure helper
    Compares artifact mtime to max(input mtimes).
    Returns {stale: bool, artifact_mtime_ms: int, newest_input_mtime_ms: int} or {}.
    Safe failure: returns {} if stat fails.
    Reuses _file_meta() mtime_ms fields from existing path records.
- Add the three staleness warnings to the warnings array in effective_config_payload()
    Conditions follow the rules defined above.
    Payloads follow the proposed shapes above.
- Add focused tests for each warning:
    stale/fresh/missing cases for each artifact
    missing input does not create misleading stale warning
    payload bounded and contains remediation
    generated/cache classification unchanged
    no model files hashed or scanned differently
```

**Slice C must not:**

```text
- Change generation paths or file locations
- Move generated artifacts to var/generated/
- Run /qz/models/refresh automatically
- Rewrite qz-codex-common
- Mutate config files
- Create new files at runtime
- Change routing semantics
- Promote generated files to source-of-truth status
```

---

### Test plan for Slice C

Exact tests future Slice C should add:

```text
test_model_inventory_cache_stale_when_override_newer_than_inventory
  override file mtime > inventory mtime → stale_model_inventory_cache warning

test_model_inventory_cache_fresh_when_inventory_newer_than_overrides
  inventory mtime > override file mtimes → no stale_model_inventory_cache warning

test_model_inventory_cache_missing_produces_existing_warning_not_stale
  missing inventory → missing_codex_catalog (existing) not stale_model_inventory_cache

test_codex_catalog_stale_when_inventory_newer_than_catalog
  inventory mtime > catalog mtime → stale_codex_catalog warning

test_codex_catalog_fresh_when_catalog_newer_than_inventory
  catalog mtime > inventory mtime → no stale_codex_catalog warning

test_codex_catalog_missing_input_does_not_create_misleading_stale_warning
  inventory missing → stale check skipped entirely for catalog

test_codex_config_stale_when_catalog_newer_than_config
  catalog mtime > config mtime → stale_codex_config warning

test_codex_config_fresh_when_config_newer_than_catalog
  config mtime > catalog mtime → no stale_codex_config warning

test_staleness_warning_payload_bounded
  warning contains: warning, path, stale_against, artifact_mtime_ms,
    newest_input_mtime_ms, remediation
  no file contents in payload

test_staleness_warning_does_not_promote_generated_artifact_to_authority
  generated paths still classified as "generated" after staleness check

test_models_refresh_not_called_by_effective_config
  /qz/config/effective payload construction does not call ModelCatalog.refresh()
  (must not trigger side effects)

test_no_model_files_hashed
  GGUF files and model-inventory.json are large; confirm no sha256_12 on them
  (size_bytes > 65536 → hash_skipped or absent)
```

---

### Implementation recommendation

**Implement Slice C next** — the design is clear, dependencies are confirmed from code,
and the mtime comparison rules are straightforward. No further verification pass is needed.

The implementation is small: one pure helper, three warning conditions added to
`effective_config_payload()`, and twelve focused tests. No path moves, no script rewrites.

### Slice C — COMPLETE

Added to `proxy/qz_config_report.py`:
- `_artifact_staleness_check(artifact_path, input_paths) -> dict` — pure helper;
  returns `{artifact_mtime_ms, newest_input_mtime_ms}` when stale, `{}` otherwise.
  Safe failure returns `{}`. No writes, no refresh calls, no content reading.
- `codex_catalog_path` / `codex_config_path` as named variables (reused in records + warnings).
- Three staleness warning blocks: `stale_model_inventory_cache`, `stale_codex_catalog`,
  `stale_codex_config` — advisory only, remediation is `POST /qz/models/refresh`.

17 new tests in `GeneratedArtifactStalenessTests` covering helper unit tests, all three
stale/fresh scenarios, missing-vs-stale separation, payload boundedness, no authority
promotion, and no model-file hashing. 2518 total tests passing.

### Slice C.1 — COMPLETE

Audit confirmed helper purity, warning isolation, and authority boundaries were clean.
One precision issue fixed: `stale_against` for `stale_model_inventory_cache` now lists
only override files that actually exist on disk. Previously the list was hardcoded to
both names regardless of which files were present. 3 new tests lock the behaviour.
2521 total tests passing. No routing/ownership/path changes.

### #5 close-out (2026-05-19)

**Issue #5 CLOSED.** All original acceptance criteria satisfied:

```text
Effective config reports source layer, path, classification, missing/stale warnings.  PASS
/qz/models/refresh regenerates Codex catalog without qz-up restart.                  PASS
Updating user model overrides and calling refresh applies them without restart.       PASS
Generated catalog remains consistent with proxy model state.                          PASS
No new permanent one-off shell scripts added.                                         PASS
```

qz-codex-common is already a thin client (calls POST /qz/models/refresh; no local
catalog generation; no model-inventory.json reads). Remaining script-owned duties
(directory setup, initial config.toml copy, model_provider read) are legitimate
launcher mechanics, not ownership duplication.

Follow-up issues opened:
- Generated artifact path migration design (var/generated/)
- qz-codex-common thinning design (model_provider proxy endpoint, config.toml handling)

---

## qz-codex-common thinning design (#57 Slice A-design)

Date: 2026-05-19. Status: design only — no runtime implementation.

This section defines what qz-codex-common still owns, whether `model_provider`
TOML parsing should move to a proxy endpoint, and what a future implementation
slice may or must not do.

---

### Current qz-codex-common ownership map

| Responsibility | Current owner | Classification |
|---|---|---|
| CODEX_HOME directory creation (`mkdir -p`) | `qz-codex-common` | Legitimate bootstrap |
| Initial `config.toml` copy from template | `qz-codex-common` | Legitimate bootstrap |
| `POST /qz/models/refresh` call | `qz-codex-common` → proxy | Correct — proxy owns catalog state |
| `model_provider` read from `config.toml` | `qz-codex-common` (Python regex) | **Launcher-local Codex client config** |
| `CODEX_HOME`, `CODEX_SQLITE_HOME`, `CODEX_OSS_BASE_URL` env setup | `qz-codex-common` | Legitimate launcher |
| Codex CLI invocation (`-c "model_provider=..."`) | `qz-codex-common` | Legitimate launcher |
| `/qz/control-plane` health display on error | `qz-codex-common` | Legitimate diagnostics |

There is **no authority duplication** remaining. The proxy owns catalog state;
`qz-codex-common` owns Codex CLI launch mechanics.

---

### model_provider current flow

`config/example/codex-config.toml` (template, tracked in git):
```toml
model_provider = "quantzhai"
[model_providers.quantzhai]
name = "QuantZhai"
base_url = "http://127.0.0.1:18180/v1"
wire_api = "responses"
env_key = "LOCAL_QWEN_API_KEY"
...
```

1. On first `qz-codex` launch, `qz-codex-common` copies this template to
   `var/codex-home/config.toml`. `model_provider = "quantzhai"` is set there.

2. `POST /qz/models/refresh` → `qz_codex_catalog.generate()` patches `config.toml`
   in-place: adds/updates `model_catalog_json = "..."`, removes stale context-window
   lines. **It does NOT touch `model_provider`.**

3. `qz_prepare_codex_home()` extracts `model_provider` via Python regex (reads lines
   before the first `[section]` header, matches `model_provider = "..."`).

4. The extracted value is stored as `QZ_CODEX_MODEL_PROVIDER` and passed to Codex CLI
   as `-c "model_provider=\"$QZ_CODEX_MODEL_PROVIDER\""`.

**Key finding:** `model_provider` is a **Codex CLI client concept** — it tells the
Codex CLI which `[model_providers.*]` block to use and which API base URL to hit.
The proxy has no awareness of it and no reason to expose it. There is no ownership
duplication; the parsing is local Codex client config plumbing.

---

### Option comparison

**Option A: Add `/qz/config/model_provider` endpoint**

Pros: script stops parsing TOML; provider surfaced as proxy observation.
Cons: Creates a weird ownership inversion — proxy would read a Codex CLI config file
it doesn't otherwise consume. `model_provider` is not proxy policy; having the proxy
expose it implies authority it doesn't have.
**Not recommended.**

**Option B: Extend `/qz/config/effective` with a `codex` block**

Pros: No new endpoint; provider visible in existing observability report; parsing
in Python (more robust than bash regex).
Cons: Requires `/qz/config/effective` to read and parse `config.toml` TOML format.
Script would need to query the endpoint and extract the value (adds proxy dependency
to an operation that currently works without a proxy call). More complex for a minor
improvement.
**Acceptable as a future option if TOML parsing becomes brittle, but not urgent.**

**Option C: Keep TOML parsing, document as launcher-local**

Pros: No code churn. `model_provider` is genuinely a Codex CLI concept. The Python
snippet is simple and stable (reads lines before first `[section]` header). Operator
can override with `QZ_CODEX_MODEL_PROVIDER` env var without any proxy interaction.
Cons: Regex remains, but it is minimal and well-isolated.
**Recommended for now.**

---

### Recommendation: Option C — keep launcher-local, document the boundary

`model_provider` is a Codex CLI client setting, not proxy policy. The TOML parsing
is an acceptable launcher implementation detail. It should be clearly commented in
the script as client-config plumbing, not as policy.

**If the parsing ever becomes brittle** (e.g., config.toml format changes, TOML
quoting edge cases emerge), the lowest-risk upgrade path is **Option B**: extend
`/qz/config/effective` to include a bounded `codex` observability block parsed from
`config.toml`. This keeps parsing in Python, in a tested endpoint, and does not
invert ownership.

The `QZ_CODEX_MODEL_PROVIDER` env var override is already supported and documented
as the operator escape hatch.

---

### Initial config.toml copy analysis

`qz-codex-common` copies `config/example/codex-config.toml` to
`var/codex-home/config.toml` on first run. This is a **legitimate bootstrap step**:

- `qz_codex_catalog.generate()` patches but does NOT create `config.toml`; if the
  file is absent, `generate()` starts with an empty string and only writes
  `model_catalog_json = "..."`. The full provider config block would be missing.
- Having the proxy create `config.toml` from template would require the proxy to
  own Codex client config initialization — a concern outside its remit.

**Leave the initial copy as-is.** It is not authority duplication.

---

### Future Slice B boundary (if ever warranted)

A future Slice B should only be implemented if the TOML parsing becomes brittle or
an operator explicitly requests provider visibility in the effective config report.

**Slice B may:**
```text
- Add a bounded "codex" block to /qz/config/effective output:
    {
      "codex": {
        "model_provider": "quantzhai",
        "source_path": ".../var/codex-home/config.toml",
        "source_layer": "generated",
        "config_state": "file"  (or "missing"/"malformed")
      }
    }
  Parsing in Python, safe failure returns bounded warning.
- Update qz-codex-common to read from this endpoint instead of local TOML.
  Fallback to TOML parsing if proxy is unavailable (since proxy is required anyway,
  fallback may be optional).
- Add focused tests for the new block.
```

**Slice B must not:**
```text
- Remove config.toml bootstrap copy
- Move CODEX_HOME
- Move generated artifacts (see #56)
- Rename model_provider setting name or slugs
- Change model routing
- Rewrite qz-codex-common
- Add a standalone /qz/config/model_provider endpoint
```

---

### Test plan for future Slice B

```text
test_effective_config_codex_block_present
  codex block present in /qz/config/effective payload

test_effective_config_codex_model_provider_reads_from_config_toml
  config.toml with model_provider = "quantzhai" → codex.model_provider == "quantzhai"

test_effective_config_codex_block_missing_config_toml
  config.toml absent → codex.config_state == "missing", bounded warning

test_effective_config_codex_block_malformed_config_toml
  config.toml unparseable → safe failure, bounded warning, no exception

test_effective_config_codex_block_does_not_expose_api_keys
  no env_key values or secrets in codex block

test_shell_syntax_qz_codex_common_unchanged
  bash -n scripts/qz-codex-common passes

test_no_model_routing_change
  proxy routing unaffected by codex block addition
```

---

### Non-goals

```text
Not #56 var/generated path migration.
Not a rewrite of qz-codex-common.
Not a Codex config format migration.
Not a model routing redesign.
Not operational persistence (#51/#46).
Not BrainCase/LimbiCore.
Not #37 stream seam work.
Not a standalone /qz/config/model_provider endpoint.
Not removing the config.toml bootstrap copy.
```

---

## Next steps

1. Treat this plan as a living document.
2. Start with an audit of data paths, failure modes, and config/state files.
3. Prioritise minimal error-report improvements before the larger config restructure.
4. Hide or compactly fail invalid profiles before touching broader layout.
5. Design the config contract after the audit, not before.
6. Reduce script sprawl as part of the refactor, without moving shell mess into the three remaining entry scripts.
7. Add `memory_domain` plumbing to the existing model-overrides loader (next smallest implementation slice above).
8. Implement `qz.profiles.v1` loader and `profiles/*.json` directory support after the plumbing is green.

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
memory_domain plumbing: missing domain resolves to isolated; explicit domain passes through.
memory_domain plumbing: same domain across multiple profiles is supported.
qz.profiles.v1 loader: adding profiles/*.json file does not require editing any other file.
qz.profiles.v1 loader: duplicate slugs in same layer warn or error.
qz.profiles.v1 loader: user layer overrides default layer for same slug.
Profile bundle memory config (memory.domain) maps correctly to existing memory_domain semantics.
Memory state remains under var/; no state leaks into config/.
/qz/config/effective shows cross-layer profile overrides.
```

## Related documents

- `docs/bugs/stale-profile-server-alias.md`
- `docs/observability-streaming-bugfix-agenda.md`
- `docs/runtime-observability-notes.md`
- `docs/codex-context-memory-contract.md` — authoritative memory_domain semantics; must be preserved exactly when implementing profile bundles
- `AGENTS.md`
