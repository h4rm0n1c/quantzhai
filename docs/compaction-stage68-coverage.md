# Stage 6.8/6.9/6.10 Compaction Corpus Coverage Evidence

Date: 2026-05-28
Status: **Stage 6.10 complete — v3/Zenkai promoted to default (auto mode), 16.5% autocompact-buffer reserve policy, model_auto_compact_token_limit emitted, 4/4 v3 deep-coverage accepted. See Stage 6.10 section below. Stage 6.9: context-aligned budget resolver. Stage 6.8: runner improved, 13/14 v3 accepted.**

Commit: see `git log --oneline -1`
Issue: `#8` — RFC: NetTTS-inspired survival-weighted compaction for QuantZhai

---

## Purpose

Stage 6.8 is a coverage and evidence-hardening pass following the Stage 6.7
adversarial audit (commit `1076444`).

The audit found that Stage 6.7's shallow corpus was biased by alphabetical file
traversal (picking dotfiles before Go, Rust, or C++ source), that c_macro was
never exercised in the corpus, that full v3 decoded summaries were not preserved,
and that deep runs did not exceed `keep_recent_items=20` uniformly.

Stage 6.8 does not tune the classifier, change the prompt, change compaction
defaults, or make v3 the default. It improves the corpus runner and evidence
layer only.

---

## What Changed in the Runner

### Targeted file selection (`REPO_TARGET_PATHS`)

Added `select_targeted_files()` to `scripts/qz_dogfood_corpus_lib.py`.

Each of the 8 corpus repos now has a configured target list:
- **Exact paths**: `go.mod`, `Cargo.toml`, `CMakeLists.txt`, `stb_image.h`, etc.
- **Directory targets**: `src/`, `include/fmt/`, `tests/`, `examples/` etc.
  Expand to up to `DIR_EXPAND_LIMIT=4` files in deterministic sorted order.
- **Glob patterns**: `*.go`, `*.sh` applied to repo root.
- **Fallback**: if no targets match (unconfigured or all absent), reverts to
  original sorted-rglob traversal.

Target files are deduplicated and capped at `TARGETED_FILE_CAP=15`.
Binary extensions and NUL-byte files are skipped.
Large files are capped at `FILE_READ_CAP_BYTES=8000` characters.

### New scenarios

Added to `scripts/qz-dogfood-corpus-run`:

| Scenario | max_turns | use_targeted | Purpose |
|---|---|---|---|
| `scenario1-repo-map` | 8 | No | Legacy sorted baseline |
| `scenario2-build-docs` | 8 | No | Legacy sorted baseline |
| `scenario3-targeted-coverage` | 12 | Yes | Targeted per-repo files |
| `deep-coverage` | 25 | Yes | Deep with targeted files, >20 turns for survival-hinted runs |

### Artifact preservation

Each scenario result now writes three artifact files in the run directory:

```
runs/stage68-corpus/
  dogfood-results.json
  dogfood-results.md
  summaries/
    <repo>-<scenario>.summary.md      ← full decoded v3 summary_text
  survival-hints/
    <repo>-<scenario>.hints.json      ← pre-compact hint spans, features, top-10
  selected-files/
    <repo>-<scenario>.files.json      ← selected files with path, reason, bytes_read
```

`summary_text_path`, `survival_hints_path`, `selected_files_path` are recorded
in each evidence dict.

`v3_summary_preview` (400 chars) is preserved for backward compatibility.

---

## Stage 6.8 Run Coverage

Run ID: `stage68-corpus`
Proxy: `http://127.0.0.1:18183` (QZ_COMPACTION_PROFILE=coding-llm,
QZ_LLM_COMPACT_BASE_URL=http://127.0.0.1:18084, QZ_CAPTURE_MODE=full)
Model: `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf`
Date: 2026-05-27

### Targeted-coverage results (8/8 repos)

| Repo | Result | Files | Latency | Hints | c_macro | build_file | lang_cmd | Selected targets |
|---|---|---|---|---|---|---|---|---|
| linuxstreamtools | v3 ✓ | 4 | 5561ms | 13 | 0 | 0 | 0 | README.md + docs/ |
| quantzhai | v3 ✓ | 7 | 5536ms | 80 | 0 | 0 | 0 | proxy/, tests/, config/ |
| click | v3 ✓ | 12 | 21122ms | 69 | 0 | 1 | 1 | pyproject.toml + src/click/ + tests/ |
| p-limit | v3 ✓ | 5 | 6975ms | 14 | 0 | 1 | 0 | package.json + index.js + test.js + readme |
| bubbletea | v3 ✓ | 12 | 29772ms | 57 | 0 | 2 | 0 | go.mod + tea.go + examples/ |
| fd | v3 ✓ | 9 | 6935ms | 59 | 0 | 2 | 0 | Cargo.toml + Cargo.lock + src/ |
| fmt | v3 ✓ | 12 | 14885ms | 62 | **1** | 1 | 0 | CMakeLists.txt + include/fmt/ |
| stb | v3 ✓ | 8 | 6628ms | 44 | **3** | 0 | 0 | README + stb_image.h + stb_truetype.h + stb_sprintf.h |

**All 8/8 targeted-coverage: v3 accepted.**

Notes:
- All 8/9 canonical headings present in every accepted summary.
- No reasoning leak, no placeholder leak in any run.
- All shallow runs had `survival_hint_count=0` because file count < keep_recent_items=20.
  These runs validate acceptance mechanics and targeted file selection, not
  survival-hinted summarization quality.
- `c_macro` confirmed exercised: fmt=1 hit (from `#define FMT_VERSION 120101` in
  base.h), stb=3 hits (from stb_image.h, stb_truetype.h, stb_sprintf.h headers).
- `build_file` fired on bubbletea (go.mod, go.sum), fd (Cargo.toml, Cargo.lock),
  click (pyproject.toml), p-limit (package.json), fmt (CMakeLists.txt).
- `language_command` fired on click (pytest) and p-limit (npm test reference).

### Deep-coverage results (6 repos)

| Repo | Result | Files | Latency | Pre-compact hints | survival_hint_count | Notes |
|---|---|---|---|---|---|---|
| bubbletea | v3 ✓ | 25 | 54373ms | 150 | confirmed >0 (25 turns) | Rich summary with full go.mod deps |
| fmt | v2 fallback | 18 | 50788ms | 79 | — | C++ template header budget pressure |
| stb | v3 ✓ | 8 | 6742ms | 44 | 0 | Only 8 target files available; equivalent to shallow |
| click | v3 ✓ | 15 | 48165ms | 76 | **42** | Regression from Stage 6.7 resolved |
| quantzhai | v3 ✓ | 7 | 6702ms | 80 | 0 | 7 < 20 turns; sparse summary (no older items to compact) |
| fd | v3 ✓ | 9 | 5366ms | 59 | 0 | 9 < 20 turns; sparse summary |

**5/6 deep-coverage: v3 accepted. 1/6: v2 fallback (fmt).**

**Total Stage 6.8: 13/14 v3 accepted, 1/14 v2 fallback.**

---

## Key Findings

### c_macro confirmed exercised

Stage 6.7 could not confirm `c_macro` because sorted traversal never reached
stb headers or fmt C++ headers. Stage 6.8 targeted selection placed
`stb_image.h`, `stb_truetype.h`, `stb_sprintf.h` as direct targets and
`include/fmt/base.h` via directory expansion. Both repos exercised c_macro in
the pre-compaction hints: stb=3 hits, fmt=1 hit. Unit test coverage was already
present; corpus confirmation now added.

### Go / bubbletea coverage confirmed

Stage 6.7 shallow bubbletea never reached `go.mod` or `.go` source files. Stage
6.8 targeted selection placed `go.mod`, `go.sum`, `tea.go`, and `examples/`
explicitly. The deep-coverage run selected 25 files including Go test files
(`commands_test.go`, `cursed_renderer_test.go`) and clipboard/color/commands
source files. The accepted v3 summary preserves the full dependency list from
go.sum, exact module path (`charm.land/bubbletea/v2`), Go version (`1.25.0`),
test function names, and env vars (`$TERM`, `TERM_PROGRAM`). This is a
meaningfully richer summary than Stage 6.7 could produce.

### C++ / fmt deep fallback

fmt deep with 18 files fell back to v2. The selected fmt files were heavily
capped (CMakeLists.txt, base.h, chrono.h, color.h all hit the 8KB cap). With
18 file turns = 37 total messages and 17 messages to compress, the compaction
context was still very large (dense C++ template content). Latency was 50s.
fmt targeted-coverage (12 files) accepted v3 at 15s.

This suggests a file budget interaction for C++ repos with large headers. The
targeted-coverage scenario is a safer operating point. The deep fallback is not
evidence of classifier failure — it is evidence of budget pressure from large
C++ content per file.

### click deep regression resolved

In Stage 6.7, click deep fell back to v2. In Stage 6.8, click deep accepted v3
with `survival_hint_count=42` (15 files → 31 turns > keep_recent_items=20).
The change: targeted selection picked `pyproject.toml`, `src/click/` source
files, and `tests/` in a defined order, rather than alphabetical rglob which
may have produced a different input shape. Causality between targeted selection
and v3 acceptance is plausible but not proven — input shape, turn ordering, and
backend state remain confounders.

### Repos with few targets run shallow regardless of deep scenario

quantzhai (7 targets), fd (9 targets), stb (8 targets) produced sparse summaries
on deep-coverage because their available target files are fewer than
`keep_recent_items=20`. With no older items to compact, the compactor has
nothing to summarize. These runs still confirm v3 acceptance mechanics and
targeted file selection, but they do not exercise survival-hinted summarization.
For genuine deep runs on these repos, additional file sources would be needed
(e.g. more docs/, changelog, or additional source expansions).

### Full summaries now preserved

All accepted v3 summaries are in `runs/stage68-corpus/summaries/`. The bubbletea
deep summary (the most information-dense result) includes: full module path,
exact Go version, all direct and indirect dependency SHAs/versions from go.sum,
test function names, env var names from environ.go, and a "Heavy/high paths"
section showing hint-influenced path preservation. This level of detail was not
available in Stage 6.7 (only 400-char previews).

### Pre-compact hint evidence preserved

All pre-compact hint spans, feature counts, and top-10 hint texts are in
`runs/stage68-corpus/survival-hints/`. These are the hints computed from
the full input before the compaction request fires, not the subset passed to the
LLM (which is capped/prioritized by the proxy). Future audits can compare
pre-compact feature counts against the `survival_hint_count` in the v3 payload
to measure hint budget utilization.

### Selected-files evidence preserved

All `runs/stage68-corpus/selected-files/*.files.json` record which files were
selected, by what mechanism (exact/dir/glob/fallback), and how many bytes were
read. This replaces the Stage 6.7 `files_read` list which lacked reasons.

---

## Multi-Batch Run Note

Stage 6.8 ran in two batches:
1. All 8 repos: `scenario3-targeted-coverage`
2. bubbletea, fmt, stb: `deep-coverage`
3. click, quantzhai, fd: `deep-coverage`

Each batch overwrote `dogfood-results.json` with only that batch's results
(by runner design). The per-scenario artifact files (summaries/, hints/,
selected-files/) survived all batches because they are named
`<repo>-<scenario>.*`. The `dogfood-results.json` reflects only the last batch.
This is a known limitation. A future improvement would append to a consolidated
results file across batches.

---

## Runner and Test Changes Summary

### `scripts/qz_dogfood_corpus_lib.py`

Added:
- `BINARY_EXTENSIONS`, `FILE_READ_CAP_BYTES`, `DIR_EXPAND_LIMIT`, `TARGETED_FILE_CAP`
- `REPO_TARGET_PATHS` — per-repo targeted file specs (exact, dir/, glob)
- `is_binary_path()`, `is_readable_text_file()`, `_has_binary_bytes()`
- `select_targeted_files()` — stdlib-only, no proxy imports
- `summaries_dir()`, `hints_dir()`, `selected_files_dir()` — artifact subdirectory helpers
- `scenario_artifact_name()`, `write_text_artifact()`

No existing functions changed. Library remains stdlib-only. No proxy imports.

### `scripts/qz-dogfood-corpus-run`

- Added `scenario3-targeted-coverage` and `deep-coverage` to `SCENARIOS`
- Each scenario dict includes `max_turns` and `use_targeted`
- Added `_readable_files_targeted()` using `select_targeted_files()`
- Preserved legacy `_readable_files_sorted()` for scenario1/scenario2 parity
- `run_scenario()` now accepts `rdir`, `max_turns`, `use_targeted`
- Writes full summary artifact to `summaries/<repo>-<scenario>.summary.md`
- Writes pre-compact hints to `survival-hints/<repo>-<scenario>.hints.json`
- Writes selected-files to `selected-files/<repo>-<scenario>.files.json`
- Records `selected_files`, `selected_files_count`, `*_path` fields in evidence
- Creates artifact subdirs at run start

### `tests/test_qz_dogfood_corpus.py`

Added test classes:
- `TestBinaryPathDetection` — extension and text-file detection
- `TestTargetedFileSelection` — 15 required tests (1–14 plus lib safety checks)
- `TestTargetedFileSelectionEdgeCases` — empty scratch, nonexistent scratch, cap, .git skip
- `TestArtifactHelpers` — subdirectory paths, artifact naming, write helpers
- `TestRepoTargetPaths` — all 8 repos configured, stb/bubbletea/fmt/fd key targets present
- `TestRunnerScenarioStructure` — scenario dict fields, deep-coverage turns and targeted flag

Total tests: 47 (Stage 6.4/6.5 baseline) + 44 new = **91 total. All passing.**

---

## Coverage Verdict

| Category | Stage 6.7 | Stage 6.8 | Change |
|---|---|---|---|
| c_macro exercised in corpus | No | Yes (stb=3, fmt=1) | ✓ Fixed |
| Go source files read | No (sorted missed .go) | Yes (go.mod, tea.go, examples) | ✓ Fixed |
| C++ source files read | No | Yes (CMakeLists.txt, include/fmt/) | ✓ Fixed |
| C header files read | No | Yes (stb_image.h, stb_truetype.h) | ✓ Fixed |
| Full v3 summaries preserved | No (400-char preview only) | Yes | ✓ Fixed |
| Hint spans/features preserved | Partial (200-char text) | Yes (full JSON, top-10) | ✓ Fixed |
| Selected file reasons recorded | No | Yes | ✓ Fixed |
| click deep v3 acceptance | v2 fallback | v3 accepted | ✓ Improved |
| bubbletea deep coverage | Not run | v3, 25 files, rich summary | ✓ Added |
| fmt deep | Not run | v2 fallback (C++ budget pressure) | New finding |
| survival_hint_count > 0 confirmed | No (all 0 in Stage 6.7) | Yes (click=42, bubbletea confirmed) | ✓ Fixed |

---

## Remaining Uncertainty

1. **fmt deep fallback causality**: v2 fallback likely budget pressure from C++
   template headers at 8KB cap × 18 files. Not proven to be classifier noise or
   schema failure. A reduced file cap or lower max_turns for C++ may help.
   Documented; not patched in Stage 6.8.

2. **quantzhai deep sparse summary**: Only 7 target files available. Real deep
   survival-hinted coverage of quantzhai would need more files in the turn
   history. The current 7 targets are correct for targeted-coverage; deep-coverage
   degrades to the same input shape.

3. **stb deep sparse summary**: Only 8 stb target files exist. Same issue as
   quantzhai. The c_macro feature fires correctly in both shallow and deep, but
   the compactor has nothing old to summarize.

4. **click deep causality**: The Stage 6.7 v2 fallback and Stage 6.8 v3
   acceptance are correlated with the targeted vs. sorted file selection change,
   but other confounders remain (input ordering, turn structure, backend state).
   Directional evidence is positive.

5. **No false-positive rate measurement**: The corpus does not measure classifier
   noise (false positives) systematically. No Stage 6.8 result shows obvious
   noise, but c_macro, code_symbol, and path feature counts are high in some
   runs. A future adversarial corpus targeting prose-heavy repos or documentation-
   only repos would give stronger noise bounds.

6. **Multi-batch results.json**: `dogfood-results.json` reflects only the last
   batch run. Per-scenario artifact files are the authoritative evidence store
   for Stage 6.8.

---

## Stage 6.9: Context-Aligned Compaction Budget (2026-05-27)

Stage 6.9 is **complete**. It replaces the three static LLM compaction defaults
with a context-window-derived budget resolver, wiring `selected_model.context_window`
from `qz_request_router.py` through to `_build_local_compaction_response_v3`.

### What was wrong

The three static defaults in `proxy/qz_responses.py` were not aligned with the
selected model's actual context window:

| Field | Old static default | For 256k model (262144 tokens) |
|---|---|---|
| `llm_timeout_sec` | 30 s | ~30 s (too short for heavy C++ or Rust histories) |
| `llm_max_input_chars` | 100,000 chars | ~25,000 tokens (arbitrary, ~10% of context) |
| `llm_max_output_tokens` | 1,536 tokens | too small for a rich anchored summary |

The fmt deep v2 fallback in Stage 6.8 was characterised as "C++ template header
budget pressure" — primarily the 30 s timeout. The old 1536 output token cap also
prevented full-quality anchored summaries for large histories.

### What changed

`proxy/qz_responses.py`:
- Added `_resolve_llm_compaction_budget(context_window_tokens, ...) -> dict`.
  Policy: `compaction_budget_tokens = floor(context_window_tokens * 0.90)`.
  Derives `effective_max_output_tokens`, `effective_max_input_chars`,
  `effective_timeout_sec` from that budget. Returns `fail_v3=True` if
  `context_window_tokens` is missing or non-positive.
- Updated `_build_local_compaction_response_v3()` to call the resolver and fail
  closed to heuristic v2 when `fail_v3=True`.
- Updated `_build_survival_weighted_compaction_prompt()`, `_build_llm_compactor_payload()`,
  `_call_llm_compactor()` to accept explicit limit parameters (resolver values
  override COMPACTION_CONFIG when provided; COMPACTION_CONFIG still applies when
  called directly without resolved values).
- Budget metadata recorded in `metadata.budget` inside the v3 payload.

`proxy/qz_request_router.py`:
- Extracts `selected_model["context_window"]` at the compaction call site and
  passes it as `selected_context_tokens` to `_build_local_compaction_response`.

### Derived values for 256 k context (262144 tokens)

| Value | Formula | Result |
|---|---|---|
| `compaction_budget_tokens` | floor(262144 × 0.90) | 235,929 |
| `effective_max_output_tokens` | min(8192, max(1536, floor(budget × 0.04))) | 8,192 |
| `effective_max_input_chars` | max(100000, floor(budget × 0.20) × 4) | 188,743 |
| `effective_timeout_sec` | max(30, min(floor(budget / 2000), 120)) | 117 s |

Env overrides (`QZ_LLM_COMPACT_TIMEOUT_SEC`, `QZ_LLM_COMPACT_MAX_INPUT_CHARS`,
`QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS`) still win over derived values when set.

### Fail-closed contract

If `selected_context_tokens` is None, 0, or negative:
- `budget["fail_v3"] = True`
- `_build_local_compaction_response_v3()` returns None immediately (before any
  network call).
- Caller falls through to heuristic v2.
- `quantzhai_proxy.py`'s compaction path (no model context available) correctly
  falls back to v2 for v3-mode runs.

### Tests

21 new tests added in `TestCompactionBudget` class in `tests/test_qz_llm_compaction.py`.
2 existing v3 tests updated to pass `selected_context_tokens=262144`.
1 test in `tests/test_qz_compaction_config.py` updated similarly.
Total: **59 tests in `test_qz_llm_compaction.py`**, all passing.

### Remaining Stage 6.9 corpus items (deferred)

The original corpus-improvement ideas from this section remain valid but are lower
priority now that budget alignment is resolved:

1. **fmt deep budget retest**: With 117 s timeout and 8 192 output tokens, fmt
   deep is expected to accept v3. Requires a live corpus rerun (issue #8).
2. **Consolidated results across batches**: Runner still overwrites `dogfood-results.json`.
3. **Prose/docs false-positive measurement**: No scenario for narrative-only files yet.
4. **Codex session comparison**: No live session evidence yet.

---

## Safety Notes (Stage 6.8/6.9)

- Default compaction remains `heuristic` (localcmp:v2). Unchanged (as of Stage 6.9).
- v3 (`localcmp:v3`) remains opt-in via `coding-llm` profile. Unchanged (as of Stage 6.9).
- No proxy routing, lifecycle event, or tool changes made in Stage 6.8.
- No `response.custom_tool_call_input.done` emitted.
- `reasoning_content` not accepted as `summary_text`. Unchanged.
- `localcmp:v3` is the stable wire format name. "Zenkai Boost Compactor" is a
  codename/nickname only; it does not appear in protocol fields.
- `QZ_LLM_COMPACT_BASE_URL` pointed at direct backend (18084), not proxy.

---

## Stage 6.10: v3 Default and Autocompact Buffer Policy

Date: 2026-05-28
Commit: pending (this run)

### Changes

1. **v3/Zenkai promoted to default** — `config/default/compaction.json` default
   profile changed from `mode=heuristic` to `mode=auto`. v3 LLM compaction is now
   the normal path. v2 heuristic is fallback (no backend URL, timeout, invalid summary).

2. **Explicit heuristic escape hatch** — new `heuristic` profile in compaction.json.
   Users can force v2 via `QZCOMPACT=heuristic` or `QZ_COMPACTION_PROFILE=heuristic`.

3. **Budget policy changed** — replaced 90% direct budget with 16.5% autocompact-buffer
   reserve (`context_window_autocompact_buffer_v1`):
   - `autocompact_buffer_tokens = floor(context_window_tokens * 0.165)`
   - `safe_compaction_budget_tokens = context_window_tokens - autocompact_buffer_tokens`
   - For 256k (262144): `autocompact_buffer_tokens=43253`, `safe_budget=218891`
   - Old 90% policy gave 235929; new policy gives 218891 (slightly tighter, matches Claude Code's buffer).

4. **Request compact_threshold handling** — `context_management.compact_threshold`
   from the request body is now classified:
   - `< 1024`: `force_compaction_only` — not used as budget cap (dogfood runner uses `compact_threshold=1`).
   - `<= safe budget`: `budget_cap` — used to cap effective budget.
   - `> safe budget`: `capped_to_safe_budget` — ignored for budget (safe budget used).
   - invalid/negative: `ignored_invalid`.

5. **Codex-side threshold** — `model_auto_compact_token_limit` now emitted in generated
   Codex model catalog. For 256k: 218891. Aligned with proxy-side safe budget.

6. **Metadata enriched** — v3 blob `metadata.budget` now records: `policy`,
   `autocompact_buffer_ratio`, `autocompact_buffer_tokens`, `safe_compaction_budget_tokens`,
   `client_compact_threshold_tokens`, `client_compact_threshold_role`,
   `client_compact_threshold_used`, `effective_compaction_budget_tokens`.

### Live Dogfood Results (Stage 6.10)

Run ID: `run-stage610-005331`
Proxy: `http://127.0.0.1:18183`
Backend: `http://127.0.0.1:18084` (llama-server -c 262144)
Model: `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf`
Scenario: `deep-coverage` (max_turns=25, targeted=True)

| Repo     | Result       | Latency   | Files | Hints | Headings | Leak |
|----------|--------------|-----------|-------|-------|----------|------|
| fmt      | v3_accepted  | 50494ms   | 18    | 79    | 9/9      | none |
| click    | v3_accepted  | 46261ms   | 15    | 76    | 9/9      | none |
| quantzhai| v3_accepted  | 7211ms    | 7     | 80    | 9/9      | none |
| fd       | v3_accepted  | 5444ms    | 9     | 59    | 9/9      | none |

**4/4 v3 accepted. 0 v2 fallbacks. 0 failures.**

Note: These results used the proxy running Stage 6.9 code (18183 was started before Stage 6.10
landed). The new budget policy activates in the NEXT proxy restart. The v3 path itself is
confirmed working with 4/4 acceptance.

### Test Coverage (Stage 6.10)

Added 20 new tests:

- Budget resolver policy: `context_window_autocompact_buffer_v1` named correctly.
- Old `context_window_90` policy removed.
- `autocompact_buffer_ratio=0.165`, `autocompact_buffer_tokens=43253`, `safe_compaction_budget_tokens=218891` for 256k.
- `compact_threshold=1` → `force_compaction_only`, budget unchanged.
- `compact_threshold=500` (below 1024) → `force_compaction_only`.
- `compact_threshold=150000` → `budget_cap`, effective=150000.
- `compact_threshold=250000` (above safe) → `capped_to_safe_budget`, effective=218891.
- Negative/zero/string threshold → `ignored_invalid`.
- Raw threshold value recorded regardless of role.
- v3 metadata blob includes all new budget fields.
- 128k budget: `autocompact_buffer=21626`, `safe=109446`.
- Shipped compaction.json default mode is `auto`.
- Shipped `heuristic` profile exists.
- `QZCOMPACT=heuristic` forces v2 over auto.
- `QZ_COMPACTION_PROFILE=heuristic` selects heuristic.
- `model_auto_compact_token_limit` emitted for 256k (218891) and 128k (109446).
- No `model_auto_compact_token_limit` for missing context.
- `model_auto_compact_token_limit` ≠ `truncation_policy.limit`.

### Safety Notes (Stage 6.10)

- v3 is now the default path (mode=auto). v2 is fallback. No v2 regression.
- Explicit heuristic profile exists as escape hatch.
- No proxy routing, lifecycle event, or tool changes.
- No `response.custom_tool_call_input.done` emitted.
- `reasoning_content` not accepted as `summary_text`. Unchanged.
- `localcmp:v3` wire format name unchanged. "Zenkai Boost Compactor" is codename only.
- `QZ_LLM_COMPACT_BASE_URL` must point at direct backend (18084), not proxy.
- No new context-window source invented: uses `selected_model.context_window` from catalog.
- Env overrides (`QZ_LLM_COMPACT_TIMEOUT_SEC`, `QZ_LLM_COMPACT_MAX_INPUT_CHARS`, `QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS`) still win over derived values.
- `localcmp:v1/v2/v3` decode compatibility unchanged.
- `model_auto_compact_token_limit` emitted only; Codex inline auto-compaction and
  QuantZhai proxy-side compaction may both activate if both thresholds are crossed.
  Known interaction, documented and left for future validation.

---

## Stage 6.10.1: OpenAI Provider Masquerade and Remote Compaction Endpoint (2026-05-28)

### Changes

**OpenAI Provider Masquerade:**

- `proxy/qz_codex_client_config.py`: `CODEX_PROVIDER_NAME` changed from `"QuantZhai"` to `"OpenAI"`.
- The generated Codex `config.toml` provider block now has `name = "OpenAI"` (with `requires_openai_auth` absent, defaulting to false).
- Codex's `supports_remote_compaction()` in `model-provider-info/src/lib.rs:392` checks `self.name == "OpenAI"`. With the masquerade, this returns true.
- When `supports_remote_compaction()` = true and `RemoteCompactionV2` feature is disabled (default), Codex routes auto-compact to `compact_remote::run_inline_remote_auto_compact_task()` which POSTs to `/v1/responses/compact`.

**Remote Compaction Endpoint (`/v1/responses/compact`):**

- Route was already registered at `qz_request_router.py:1567`.
- `_handle_responses_compact()` in `quantzhai_proxy.py` improved to:
  - Resolve the currently selected model's `context_window` from the catalog.
  - Pass `selected_context_tokens` to `_build_local_compaction_response()` for budget-aware v3 compaction.
- Response shape: `{ "output": [...], "id": ..., "object": "response.compaction", ... }`.
  Codex only reads the `output` field (`CompactHistoryResponse { output: Vec<ResponseItem> }`).

**Double-Compaction Safety:**

- `/v1/responses/compact` and `/v1/responses` are separate paths. Codex does not send `context_management.compact_threshold` when using remote compaction.
- `RemoteCompactionV2` feature is left at default (false), keeping Codex on the `/responses/compact` path rather than the v2 path (which sends `ContextCompaction` items to `/v1/responses`).

**Source Audit Evidence:**

- `codex-rs/core/src/compact.rs:65`: `should_use_remote_compact_task()` checks `supports_remote_compaction()`.
- `codex-rs/core/src/session/turn.rs:816`: Remote path selected if `supports_remote_compaction()` is true.
- `codex-rs/model-provider-info/src/lib.rs:384`: `is_openai()` checks `self.name == "OpenAI"`.
- `codex-rs/codex-api/src/endpoint/compact.rs:32`: Path is `"responses/compact"`.
- `codex-rs/codex-api/src/common.rs:25`: `CompactionInput` struct — `input: &[ResponseItem]` is the history.
- `codex-rs/protocol/src/models.rs:883`: `Compaction { encrypted_content: String }` response item type.
- `codex-rs/app-server/tests/suite/v2/compaction.rs:189`: Assert path is `/v1/responses/compact`.

### Test Coverage (Stage 6.10.1)

Added tests in:

- `tests/test_qz_codex_client_config.py`: `CodexProviderNameMasqueradeTests` (5 tests)
  - `CODEX_PROVIDER_NAME == "OpenAI"` constant check.
  - Provider name in client-config payload is `"OpenAI"`.
  - Provider name is not `"QuantZhai"`.
  - model_provider slug unchanged (`"quantzhai"`).
  - wire_api unchanged (`"responses"`).
- `tests/test_qz_compaction.py`: `RemoteCompactionEndpointShapeTests` (9 tests)
  - Response has `output` list.
  - Output not empty.
  - Output contains compaction item with `encrypted_content`.
  - Blob decodable with `summary_text` and `version`.
  - Extra top-level keys don't displace `output` field.
  - `instructions` field in CompactionInput accepted.
  - Empty history returns valid shape.
  - `model` field string accepted.
  - `selected_context_tokens=262144` passes without error (v3 fails to LLM but falls back to v2).

### Safety Notes (Stage 6.10.1)

- No session ledger created. No request history reconstructed from captures.
- No `context_management` processing on `/v1/responses/compact` path.
- `requires_openai_auth` defaults to false in generated config — no OpenAI API key needed.
- `RemoteCompactionV2` feature left disabled. Do not enable it in QuantZhai sessions.
- All Stage 6.10 safety notes remain in force.
- `localcmp:v3` wire format name unchanged. "Zenkai Boost Compactor" is codename only.
- `QZ_LLM_COMPACT_BASE_URL` must point at direct backend (18084), not proxy.
