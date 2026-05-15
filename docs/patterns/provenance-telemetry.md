# Provenance Telemetry Pattern

Date: 2026-05-15
Status: Adopted — active doctrine for QuantZhai telemetry/status fields.

Derived from the #6 VRAM telemetry work. Use this pattern for any future
telemetry field, status value, or dashboard component that reports a
measurement, estimate, or configuration value.

---

## Purpose

Dashboards and status endpoints must not lie about precision.

The goal is:
- **Report what you actually know**, at the precision you actually have it.
- **Label the source** so consumers can decide how much to trust the value.
- **Distinguish measurement from estimate from configuration from calibration.**
- **Make fallback chains explicit** rather than silently upgrading an estimate
  to look like a confirmed measurement.

The anti-goal is a single number that looks authoritative but could be a
formula guess, a config default, or a stale baseline — without any indication
which it is.

---

## Required fields for each telemetry component

| Field | Required | Notes |
|---|---|---|
| `mib` / value | yes | The numeric value, or `null` if unknown. |
| `source` | yes | Where the value came from (see examples below). |
| `confidence` | yes | Vocabulary term from the list below. |
| `estimated` | yes | `true` if this is not a directly measured fact. |
| `backend_confirmed` | yes | `true` only for values from a backend allocator metric. |
| `subtractive` | yes | `true` if this component is subtracted in residual math. |
| `notes` | recommended | Human-readable explanation of caveats, derivation, or gaps. |
| `alternate_estimates` | when applicable | Other estimates for comparison (e.g. GGUF formula vs runtime budget). |

---

## Confidence vocabulary

Use exactly these terms. Do not invent compound or ambiguous terms.

| Term | Meaning |
|---|---|
| `backend-confirmed` | llama.cpp or TurboQuant `/metrics` returned this value directly. |
| `host-observed` | nvidia-smi or OS measurement on this host. |
| `host-observed-residual` | Derived from host-observed total minus subtractive components. |
| `runtime-configured` | Set explicitly in QZ_* env or model launch args. |
| `estimated-from-runtime-cache-budget` | KV_ALLOC from `QZ_CACHE_RAM` or `--cache-ram`; configured, not allocator-confirmed. |
| `estimated-from-gguf-size` | GGUF file size used as a weight-footprint proxy; provenance only. |
| `estimated-from-gguf-metadata` | Computed from GGUF block/head/dim metadata formula. |
| `calibrated-from-host-observed-baseline` | Derived from `process_used - KV_ALLOC` when backend is idle (no active tokens). |
| `calibrated-from-host-observed-runtime` | Same derivation during active inference (may include live request buffers). |
| `estimated-runtime-occupancy` | KV_USED = KV_ALLOC × (used_tokens / limit_tokens). |
| `derived-clamped` | Residual or component clamped at 0 because estimates exceeded measured total. |
| `unknown` | No data source; value is null. |

Never use: `estimated`, `confirmed`, `measured`, `real`, `actual`, or any
other non-vocabulary term as a confidence value. If a new term is genuinely
needed, add it to this list first, explain why, and update all consumers.

---

## Arithmetic rule: subtractive vs provenance-only

**Only subtract components with `subtractive=true` from residual calculations.**

A `subtractive=false` component is provenance — it provides context and
transparency, but is not used in math.

```
residual = process_used_mib
         − sum(c.mib for c in components if c.subtractive == true)
```

Mixing provenance-only components into residual math produces a double-count
or a false overallocated result.

---

## Source priority rule

When multiple sources are available for the same fact, pick the highest
priority that is actually present:

```
1. backend metric (backend-confirmed)
2. host observation (host-observed)
3. runtime configuration (runtime-configured / estimated-from-runtime-cache-budget)
4. calibration from observations (calibrated-from-host-observed-*)
5. formula estimate (estimated-from-gguf-metadata)
6. provenance proxy (estimated-from-gguf-size)
7. unknown
```

Record the chosen source and confidence. When a lower-priority estimate is
also available and materially different (>20% or >512 MiB), record it in
`alternate_estimates` for operator visibility.

---

## Naming conventions

**Distinguish what the component represents:**

| Name style | Meaning |
|---|---|
| `MODEL_RUNTIME` | Loaded model footprint: weights + load-time overhead. May be calibrated from host observation. |
| `MODEL_FILE` | GGUF file size on disk. Provenance only; not necessarily equal to GPU allocation. |
| `KV_ALLOC` | Estimated reserved KV cache capacity for the full configured context. |
| `KV_USED` | Estimated KV cache occupancy for current context tokens. Subset of KV_ALLOC. |
| `SCRATCH` | Scratch/work buffers. Unknown unless allocator metric is exposed. |
| `OTHER` | Residual: process VRAM minus subtractive components. Not scratch. |

**Do not name residual "SCRATCH"** — residual includes all unaccounted overhead
(scratch, fragmentation, loader pages, CUDA runtime, etc.) and claiming it is
one specific thing is false precision.

**Do not collapse FILE and RUNTIME into one MODEL component** when both are
available — they answer different questions.

**Distinguish ALLOC from USED** for any cache — showing only USED is
misleading when the cache is preallocated.

---

## Display markers for `qz-top`

Live compact COMP line uses single-character markers:

| Marker | Meaning |
|---|---|
| `✓` | `backend_confirmed=true` |
| `~` | `estimated=true` and value is not null |
| `?` | value is null / unknown |

These markers are appended to the value string: `MODEL=13.9GiB~  KV_ALLOC=8.0GiB~  CTX=?/262144✓`.

---

## Anti-patterns

Avoid these. They have each caused real confusion in this repo.

| Anti-pattern | Why it is wrong |
|---|---|
| GGUF file size used directly as GPU model weight VRAM | File size ≠ allocated GPU bytes. Use as provenance; calibrate runtime estimate separately. |
| KV_USED double-counted outside KV_ALLOC | KV_USED is occupancy within KV_ALLOC. Subtracting both counts the same allocation twice. |
| Residual labelled SCRATCH | Residual = process minus components. SCRATCH is one specific type; residual contains anything unaccounted. |
| Unknown quant dtype silently treated as f16 | Produces false formula estimates. Mark `formula_safe=false` and surface the unknown type. |
| BASE/DELTA treated as model/cache split | GPU VRAM baseline and delta are approximations useful for pressure testing; they do not map cleanly to model weight vs KV cache. |
| One number with no source or confidence | Makes the dashboard look informative when it is guessing. |
| Mixing `estimated` and `backend-confirmed` into one bucket | Hides the quality difference between a metric and a formula. |
| Treating `calibrated` values as `backend-confirmed` | Calibration (process_used - KV_ALLOC) is an operational approximation, not an allocator report. |

---

## VRAM split example: current QuantZhai pattern

This is the live-validated split as of 2026-05-15 with QZ_CACHE_RAM=8192 MiB
and backend process VRAM ≈ 21.9 GiB:

```
Component      Value      Confidence                              Subtractive  Notes
─────────────────────────────────────────────────────────────────────────────────────────
MODEL_RUNTIME  13.9 GiB   calibrated-from-host-observed-baseline  true        process_used - KV_ALLOC
MODEL_FILE     18.5 GiB   estimated-from-gguf-size                false       provenance; GGUF file size on disk
KV_ALLOC        8.0 GiB   estimated-from-runtime-cache-budget     true        QZ_CACHE_RAM=8192
KV_USED             ?     unknown                                 false       no context occupancy signal available
SCRATCH             ?     unknown                                 false       no allocator metric exposed by TurboQuant
OTHER           0.0 MiB   host-observed-residual                  —           process_used - MODEL_RUNTIME - KV_ALLOC
```

Interpretation:
- **MODEL_RUNTIME** is the calibrated loaded-model footprint. It includes model
  weights plus any load-time GPU overhead that cannot yet be separated. It is
  not exact allocator introspection.
- **MODEL_FILE** (18.5 GiB) is larger than MODEL_RUNTIME (13.9 GiB) because
  the GGUF file size overstates the actual GPU allocation (quantized weights
  compress differently in VRAM than on disk, and metadata/padding is not in GPU
  VRAM). This difference is expected, not an error.
- **KV_ALLOC** comes from `QZ_CACHE_RAM` (runtime config), not from an
  allocator metric. When TurboQuant exposes `kv_cache_size_bytes` in
  `/metrics`, that will become the `backend-confirmed` source.
- **OTHER ≈ 0** is the expected result when MODEL_RUNTIME and KV_ALLOC
  together account for process_used. When calibration is not possible (e.g.
  KV_ALLOC unknown), OTHER will absorb the unaccounted portion.
- **consistency.status = "calibrated"** when MODEL_RUNTIME is used.

---

## Source/confidence for the KV_ALLOC priority chain

KV_ALLOC uses this exact priority (implemented in `proxy/qz_vram_snapshot.py`):

```
1. backend metric kv_cache_size_bytes     → backend-confirmed
2. QZ_CACHE_RAM or --cache-ram            → estimated-from-runtime-cache-budget
3. GGUF metadata formula                  → estimated-from-gguf-metadata
   (requires formula_safe=true: both key and value quant bytes_per_element known)
4. unknown
```

When the budget (step 2) is used and the formula (step 3) can also compute,
the formula result is stored in `kv_alloc_comp.alternate_estimates` for
operator comparison.

---

## Quant dtype registry rule

The `_KV_QUANT_REGISTRY` in `proxy/qz_vram_snapshot.py` stores
`bytes_per_element` (effective, including scale/header overhead) for known
GGML/GGUF/TurboQuant types. Computed from struct size assertions in
`ggml-common.h`.

**If a quant dtype is not in the registry:**
- `bytes_per_element = None`
- `confidence = "unknown"`
- `formula_safe = False`
- The GGUF formula is disabled for that component
- The unknown type is shown in notes but is **not silently replaced with f16**

Adding a new quant type requires: look up block size and struct size from
source, compute `type_size_bytes / blck_size`, document the source file and
struct name, and add to the registry with appropriate confidence.

---

## Remaining gaps (as of 2026-05-15)

- TurboQuant does not yet expose `model_size_bytes` or `kv_cache_size_bytes` in
  `/metrics`. When it does, those will become `backend-confirmed` values and
  MODEL_RUNTIME calibration will no longer be needed.
- `KV_USED` is unknown when no context occupancy signal is available (no active
  tokens, no Prometheus KV cell metrics).
- `SCRATCH` is unknown until a backend allocator metric is exposed.
- GGUF formula for `iq3_s`, `iq3_m`, `iq2_m`, and `turbo1` has `bytes_per_element=None`
  because required struct constants are not in the currently-accessible source.
- Exact model weight vs load-time overhead split within MODEL_RUNTIME requires
  backend allocator introspection.

---

## See also

- `proxy/qz_vram_snapshot.py` — implementation of the component assembly
- `scripts/qz-top` — live display with confidence markers
- `tests/test_qz_vram_snapshot.py` — regression tests for all component paths
- `docs/runtime-observability-notes.md` — current qz.vram.snapshot.v1 semantics
- `docs/backend-service-recovery-semantics.md` — #6 history and cross-references
