# Compaction Corpus Dogfood (Stage 6.4)

Date: 2026-05-27
Status: **Stage 6.8 — runner improved, targeted file selection, full artifacts preserved. 8/8 targeted-coverage v3, 5/6 deep v3. See `docs/compaction-stage68-coverage.md`.**

## Purpose

Stage 6.4 builds a repeatable multi-repo staging harness for opt-in v3 compaction
dogfood. This prevents overfitting to a single repo shape (linuxstreamtools) and
expands test coverage across language ecosystems, project shapes, and code
structures.

Stage 6.5 runs actual multi-repo compaction dogfood using staged scratch repos
as workspace targets and assesses survival classifier anti-overfit generalization.

Both stages make no compaction runtime changes, no default-mode changes.
v3 remains opt-in only.

## Why linuxstreamtools Alone Is Insufficient

The current dogfood target, `h4rm0n1c/linuxstreamtools`, is:

- Owned shell/media/docs repo with the user's own coding style.
- Single language (mixed shell/docs).
- Small and flat structure.

A broader corpus is needed to confirm that v3 compaction quality, survival-weight
scoring, and anchored schema work across:

- Python libraries with tests (click)
- Go TUI frameworks (bubbletea)
- Rust CLI tools with Cargo (fd)
- C++ libraries with CMake (fmt)
- Single-header C libraries (stb)
- JavaScript npm packages (p-limit)
- The actual target system (quantzhai)

## Corpus

| ID | URL | Language | Role |
|---|---|---|---|
| linuxstreamtools | https://github.com/h4rm0n1c/linuxstreamtools | mixed | internal-baseline |
| quantzhai | https://github.com/h4rm0n1c/quantzhai | python | target-system |
| click | https://github.com/pallets/click | python | external |
| p-limit | https://github.com/sindresorhus/p-limit | javascript | external |
| bubbletea | https://github.com/charmbracelet/bubbletea | go | external |
| fd | https://github.com/sharkdp/fd | rust | external |
| fmt | https://github.com/fmtlib/fmt | cpp | external |
| stb | https://github.com/nothings/stb | c | external |

## Filesystem Layout

```
~/turboquant/qz-dogfood-corpus/        (persistent corpus root)
  repos.json                           (repo definitions)
  cache/                               (bare mirror clones, persistent)
    linuxstreamtools.git/
    quantzhai.git/
    click.git/
    p-limit.git/
    bubbletea.git/
    fd.git/
    fmt.git/
    stb.git/
  runs/                                (per-run results, persistent)
    <run-id>/
      prepare-results.json
      prepare-results.md
      stage-results.json
      stage-results.md
      cleanup-results.json
      cleanup-results.md

/tmp/qz-dogfood-work/                  (scratch work root, disposable)
  <run-id>/
    linuxstreamtools/
    quantzhai/
    click/
    p-limit/
    bubbletea/
    fd/
    fmt/
    stb/
```

## Environment Knobs

| Variable | Default | Description |
|---|---|---|
| `QZ_DOGFOOD_CORPUS_ROOT` | `~/turboquant/qz-dogfood-corpus` | Persistent corpus root |
| `QZ_DOGFOOD_WORK_ROOT` | `/tmp/qz-dogfood-work` | Scratch work root |
| `QZ_DOGFOOD_RUN_ID` | UTC timestamp (`run-YYYYMMDD-HHMMSS`) | Current run identifier |
| `QZ_DOGFOOD_KEEP_SCRATCH` | `0` | If `1`, skip scratch cleanup |
| `QZ_DOGFOOD_REPOS` | (all repos) | Comma-separated repo IDs to select |

## Scripts

### `scripts/qz-dogfood-corpus-prepare`

Creates or updates bare mirror clones in the cache directory. Resolves refs
and records SHAs. Writes `prepare-results.json`/`.md` to the run directory.

```text
# Prepare all repos
scripts/qz-dogfood-corpus-prepare

# Prepare a single repo
QZ_DOGFOOD_REPOS=linuxstreamtools scripts/qz-dogfood-corpus-prepare

# Prepare with custom config
scripts/qz-dogfood-corpus-prepare --config /path/to/repos.json
```

### `scripts/qz-dogfood-corpus-stage`

Clones scratch repos from local mirrors into the work root. Checks out
detached SHAs. Refuses to overwrite existing scratch dirs unless `--force`.

```text
# Stage all repos for a run
QZ_DOGFOOD_RUN_ID=run-multi-001 scripts/qz-dogfood-corpus-stage

# Stage specific repos with force overwrite
QZ_DOGFOOD_REPOS=click,p-limit scripts/qz-dogfood-corpus-stage --force
```

### `scripts/qz-dogfood-corpus-status`

Prints cache and scratch status for all selected repos.

```text
QZ_DOGFOOD_RUN_ID=run-multi-001 scripts/qz-dogfood-corpus-status
```

### `scripts/qz-dogfood-corpus-clean`

Records dirty status then removes scratch repos. Does not delete cache or
run results. Respects `QZ_DOGFOOD_KEEP_SCRATCH=1`.

```text
# Clean a specific run
QZ_DOGFOOD_RUN_ID=run-multi-001 scripts/qz-dogfood-corpus-clean

# Clean all runs
scripts/qz-dogfood-corpus-clean --all

# Dry run
scripts/qz-dogfood-corpus-clean --all --dry-run
```

## Safety Rules

- Cache repos are bare mirrors. They must not be used directly for dogfood.
- Scratch repos are disposable clones. They may be deleted after a run.
- If a scratch repo becomes dirty, status is recorded before cleanup.
- No script imports or invokes proxy compaction modules.
- No script changes compaction runtime behaviour.
- No script sets or changes default compaction mode.
- No proxy URL is used as compactor backend.
- All scripts use Python standard library only (no pip dependencies).

## Stage 6.5: Multi-Repo Dogfood Results

Date: 2026-05-27

Stage 6.5 ran actual multi-repo opt-in v3 compaction dogfood using the staged
scratch repos as workspace targets. Details are in `docs/compaction-live-dogfood.md`.

**Summary**:
- 16/16 shallow scenarios (8 repos × 2) produced accepted `localcmp:v3:` blobs.
- 2/3 deep scenarios (click, fd) produced accepted v3 with non-sparse summaries.
- 1/3 deep scenario (quantzhai, 76 hint spans) fell back to v2 at 50s — safety path confirmed.
- Zero reasoning_content leakage. Zero placeholder leakage. All headings present.
- Survival classifier anti-overfit assessed: env_var overfit to shell/Python (14-20 hits vs 0-1 on others), code_symbol cross-language catch-all (all 8 repos), missing Go/JS/Rust/C++ specific atoms.
- No classifier or prompt tuning changes justified — overfit evidence is documented for future improvement.
- 19 total productions, all scratch repos clean.

### Stage 6.6: Classifier Tuning (2026-05-27)

Based on the Stage 6.5 anti-overfit evidence, the survival classifier was
narrowly tuned with 5 new feature types:

- `build_file` (heavy/high): `package.json`, `Cargo.toml`, `go.mod`, `CMakeLists.txt`
- `repo_dir` (medium/medium): `src/`, `tests/`, `docs/`, `include/` with slash context
- `language_command` (heavy/high): `npm test`, `cargo build`, `go test ./...`, `cmake --build`
- `c_macro` (heavy/high): `#define`, `STB_IMAGE_IMPLEMENTATION`-style macros
- `qualified_symbol` (medium/medium): `Update()`, `View()` PascalCase function calls

**What remains**:
- Go/JS/Rust/C++ language-specific atoms (struct names, module paths, class names)
  still fall through to `code_symbol` — no per-language profile yet.
- `stb_image.h` without directory context is not path-detected — acceptable heuristic.
- Per-language coding profiles not justified by current evidence.

### Runner

Created `scripts/qz-dogfood-corpus-run` for dogfood execution.
All 8 scratch repos remain clean at `/tmp/qz-dogfood-work/stage65-corpus/`.
Results: `~/turboquant/qz-dogfood-corpus/runs/stage65-corpus/dogfood-results.json`.

## Stage 6.7: Post-Tuning Corpus Rerun (2026-05-27)

Classifier tuning verified. Results in `docs/compaction-live-dogfood.md::Stage 6.7`.

**Summary** (audit-corrected — see `docs/compaction-stage67-audit.md`):
- 16/16 shallow v3 accepted (schema/acceptance validation; all had survival_hint_count=0).
- 2/3 deep v3 accepted (quantzhai and fd); click deep regressed to v2 fallback.
- New features confirmed fired in corpus text (build_file, language_command, qualified_symbol, repo_dir).
- c_macro not exercised (sorted traversal never reached stb headers or fmt C++ files).
- Full decoded summaries not preserved; only 400-char previews.
- Stage 6.8 runner-coverage audit justified; see conclusions in Stage 6.8.

## Stage 6.8: Runner Coverage and Evidence Hardening (2026-05-27)

Coverage and evidence-hardening pass. See `docs/compaction-stage68-coverage.md` for full details.

**Summary**:
- Added per-repo targeted file selection (REPO_TARGET_PATHS) to `scripts/qz_dogfood_corpus_lib.py`.
- Added `scenario3-targeted-coverage` (targeted, 12 turns) and `deep-coverage` (targeted, 25 turns).
- Added full artifact preservation: summaries/, survival-hints/, selected-files/ per scenario.
- **8/8 targeted-coverage: v3 accepted.** All 8 repos confirmed.
- **5/6 deep-coverage: v3 accepted.** fmt deep v2 fallback (C++ header budget pressure).
- **Total Stage 6.8: 13/14 v3 accepted.**
- c_macro confirmed exercised: stb=3 hits, fmt=1 hit.
- Go coverage confirmed: bubbletea deep selected go.mod, tea.go, examples/ → rich summary.
- C++ coverage confirmed: fmt CMakeLists.txt + include/fmt/ headers.
- click deep regression (Stage 6.7 v2 fallback) resolved: Stage 6.8 v3 accepted, survival_hint_count=42.
- All scratch repos clean.
- Default compaction unchanged (heuristic v2). v3 remains opt-in.

## Tests

```text
tests/test_qz_dogfood_corpus.py
```

91 tests (Stage 6.4/6.5 baseline 47 + Stage 6.8 additions 44) covering:
config loading, selection, env knobs, mirror create/update, ref resolution,
scratch staging, status detection, dirty recording, cleanup, script structure,
import safety, write helpers, binary path detection, targeted file selection,
artifact helpers, REPO_TARGET_PATHS config, runner scenario structure.

Run:

```text
python3 -m pytest tests/test_qz_dogfood_corpus.py -v
```

## Files

- `config/dogfood/repos.json` — repo definitions
- `scripts/qz_dogfood_corpus_lib.py` — shared helpers
- `scripts/qz-dogfood-corpus-prepare` — prepare mirrors
- `scripts/qz-dogfood-corpus-stage` — stage scratch clones
- `scripts/qz-dogfood-corpus-status` — show cache/scratch status
- `scripts/qz-dogfood-corpus-clean` — clean scratch repos
- `tests/test_qz_dogfood_corpus.py` — 47 tests
- `docs/compaction-corpus-dogfood.md` — this document
