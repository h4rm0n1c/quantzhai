# Stage 6.7 compaction dogfood adversarial audit

## Status

Audit complete. Docs-only. No runtime behavior changed.

Audited Stage 6.7 commit: `684a147`

Issue context: `#8` RFC, Stage 6.7 comment `issuecomment-4554545206`

Safety notes:

- Default compaction remains v2.
- v3 remains opt-in.
- This audit did not change `proxy/qz_responses.py`, native tool routing, lifecycle event shapes, validation, or survival classifier behavior.

## Summary verdict

Stage 6.7 supports the basic outcome counts: 19 scenarios were recorded across 8 repositories, all 16 shallow scenarios accepted v3, and 2 of 3 deep scenarios accepted v3. It also supports that several new classifier features fired in the corpus, especially `build_file`, `language_command`, and `repo_dir`.

The stronger conclusions are overconfident. The available evidence does not prove "no classifier noise", does not prove the quantzhai deep improvement was caused by classifier tuning alone, and does not justify ruling out Stage 6.8. A narrower reading is better:

- v3 did not regress at the aggregate scenario-count level.
- One deep scenario, click, regressed from v3 accepted to v2 fallback.
- New hints look plausibly useful, but the corpus did not actively measure false positives.
- The shallow corpus is weak evidence because the runner left no older items to compact.
- Stage 6.8 is not required for immediate classifier code tuning, but a coverage/runner audit pass is still justified.

## Claims table

| Claim | Audit status | Evidence checked | Caveat |
| --- | --- | --- | --- |
| 19 scenarios across 8 repos / 6 languages | Supported | Stage 6.7 raw results contain 16 shallow results and 3 deep results; `config/dogfood/repos.json` lists 8 repos and 6 language labels. | The shallow scenarios are duplicated per repo with similar input shape. |
| 16/16 shallow v3 accepted | Supported | `stage67-corpus/dogfood-results.json` has 16 `v3_accepted` results. | Shallow `v3_payload.survival_hint_count` is 0 for every row because the history fits inside `keep_recent_items=20`; this mostly validates acceptance mechanics, not survival-hint summarization quality. |
| 2/3 deep v3 accepted | Supported | `stage67-corpus/deep-results.json`: quantzhai and fd accepted v3; click fell back to v2. | No full decoded summaries were stored, only 400-character previews. |
| quantzhai deep improved from v2 fallback to v3 accepted | Partially supported | Stage 6.7 raw deep result is v3 accepted; Stage 6.5 docs say quantzhai deep was v2 fallback. | Stage 6.5 deep raw JSON was not found. Causality is not proven; run variance, prompt/input shape, timeout variance, backend state, and runner differences remain possible. |
| fd deep had 24x more hints | Supported as an external hint-count comparison | Stage 6.7 fd deep feature counts sum to 73; Stage 6.5 docs report 3 hints. | The v3 payload reported `survival_hint_count=19`, so the LLM saw a capped/prioritized subset. More hints may be useful signal or budget pressure; this was not measured directly. |
| New features confirmed: `build_file`, `language_command`, `qualified_symbol`, `repo_dir` | Partially supported | Stage 6.7 shallow/deep feature counts show `repo_dir` in quantzhai, `language_command` in p-limit/click/fd, `build_file` in click/fd, and `qualified_symbol` in p-limit. | Confirmation is "feature fired", not "feature improved summary quality". Go/C++/C shallow coverage did not hit the intended build/source files. |
| `c_macro` exercised | Unsupported for Stage 6.7 corpus | Stage 6.7 raw corpus results show no `c_macro` hits. Unit tests cover it. | stb files that would exercise `#define`/`STB_*_IMPLEMENTATION` were not read by the shallow runner. |
| No classifier noise | Unsupported as stated | Unit tests include targeted negative cases for generic prose words; raw corpus showed no obvious bad feature explosion. | No false-positive rate was measured, decoded hints were only partially inspectable, and the corpus was small and runner-shaped. Better wording: no obvious classifier noise observed. |
| No v3 reliability regression | Partially supported | Aggregate acceptance count stayed high: 18/19 v3 accepted in Stage 6.7 including shallow and deep. | click deep regressed from v3 accepted in Stage 6.5 docs to v2 fallback in Stage 6.7. Deep latencies were also higher. |
| Fallback works | Supported | click deep result is `v2_fallback` with no v3 payload. | This proves the fallback path for that run, not quality of fallback summary. |
| Stage 6.8 not needed for classifier tuning | Partially supported | No tiny factual classifier bug was found in this audit; tests cover the newly added regex atoms. | Stage 6.8 should not be ruled out. It should shift from classifier tuning to coverage, runner bias, click fallback, and full-summary capture. |

## Raw evidence checked

Primary local artifacts:

- `~/turboquant/qz-dogfood-corpus/runs/stage67-corpus/dogfood-results.json`
- `~/turboquant/qz-dogfood-corpus/runs/stage67-corpus/dogfood-results.md`
- `~/turboquant/qz-dogfood-corpus/runs/stage67-corpus/deep-results.json`
- `~/turboquant/qz-dogfood-corpus/runs/stage67-corpus/stage-results.json`
- `~/turboquant/qz-dogfood-corpus/runs/stage67-corpus/cleanup-results.json`
- `~/turboquant/qz-dogfood-corpus/runs/stage65-corpus/dogfood-results.json`
- Stage 6.7 request captures under `var/captures/requests/qz_req_1779884944656_a5b0`, `qz_req_1779884990703_d010`, and `qz_req_1779885040988_a5b0`

Repository files checked:

- `docs/compaction-live-dogfood.md`
- `docs/compaction-corpus-dogfood.md`
- `docs/compaction-audit-and-strategy.md`
- `scripts/qz-dogfood-corpus-run`
- `scripts/qz_dogfood_corpus_lib.py`
- `proxy/qz_survival_weight.py`
- `tests/test_qz_survival_weight.py`
- `tests/test_qz_dogfood_corpus.py`
- `config/dogfood/repos.json`

GitHub evidence checked:

- `gh issue view 8 --repo h4rm0n1c/quantzhai`
- `gh issue view 8 --repo h4rm0n1c/quantzhai --comments`

## Runner-bias findings

The corpus runner strongly shapes the evidence.

`scripts/qz-dogfood-corpus-run` reads files with `sorted(src.rglob("*"))` and stops after a hard limit. The shallow run uses `max_turns=8`, which means many repos are represented by alphabetically early files such as dotfiles, GitHub templates, and workflows rather than source/build files. That explains why:

- bubbletea shallow did not exercise Go files or `go.mod`.
- fmt shallow did not exercise CMake build files or C++ source.
- stb shallow did not exercise header macros.
- fd shallow did not reach `Cargo.toml` or Rust source.

The shallow scenario is also weak compaction evidence. Each read file creates a user/assistant pair, and the shallow runner reads 8 files plus the scenario prompt. That produces 17 messages, while `keep_recent_items=20`; the v3 payload reports `survival_hint_count=0` for every shallow row. Those runs accepted v3, but they did not prove that v3 could summarize older survival-hinted content.

The deep scenarios are more meaningful, but they are only three repos. Their full decoded summaries were not preserved in the raw artifacts, only previews. Stage 6.5 deep raw JSON was not found, so some Stage 6.5 to 6.7 comparisons rely on the prior documentation rather than independently comparable raw records.

## Survival classifier findings

Feature classifications from this audit:

| Feature | Audit classification | Evidence | Caveat |
| --- | --- | --- | --- |
| `build_file` | Probably useful | Fired in click deep (`pyproject.toml`) and fd deep (`Cargo.lock`, `Cargo.toml`, `Makefile`). Unit tests cover JS, Python, Go, Rust, CMake, and Makefile atoms. | Not exercised by the shallow Go/C++/C repos because selected files missed build files. |
| `language_command` | Probably useful | Fired in p-limit shallow (`npm test`), click deep (`pytest`), and fd deep (`cargo build`). Unit tests cover npm/pnpm/yarn/go/cargo/cmake/ctest/pytest. | More coverage is needed to know whether command matching creates budget pressure in broad docs. |
| `qualified_symbol` | Uncertain | Fired once in p-limit shallow; unit tests cover `Update()` and `View()`. | Bubbletea shallow missed Go source, so the intended Go TUI symbols were not exercised. |
| `repo_dir` | Probably useful but broad | Fired in quantzhai shallow and deep. | Directory words with trailing slash are common in repo docs. Current tests prevent bare generic words, but corpus false-positive rate was not measured. |
| `c_macro` | Not exercised in Stage 6.7 corpus | Unit tests cover `#define` and `STB_*_IMPLEMENTATION`. | Stage 6.7 did not read stb header files that would prove corpus behavior. |

The fd deep hint increase is ambiguous. The 73 external hints include plausible atoms from real Rust project files, but the v3 compactor consumed only 19 hints after prioritization. That result accepted v3, so there is no direct failure evidence, but the audit cannot call the extra hints cleanly beneficial without inspecting the full final summary and budget behavior.

The quantzhai deep feature sum was 88, but the accepted v3 payload reported `survival_hint_count=30`. The summary preview looked relevant, but the improvement from v2 fallback cannot be attributed to classifier tuning alone.

The click deep regression matters. It had 37 external hints, including new `build_file` and `language_command` hits, and fell back to v2. This does not prove the new hints caused fallback, but it is enough to reject an unqualified "no reliability regression" claim for deep scenarios.

## V3 quality findings

The shallow summaries cannot support a strong quality conclusion. They all had accepted v3 metadata, headings, no placeholder leak, and no reasoning leak, but the compactor had no older items to summarize. These runs are useful schema/acceptance checks.

Deep quality evidence is mixed:

- quantzhai deep preview is relevant. It preserved the QuantZhai goal and key repo/runtime constraints.
- fd deep preview preserved contribution constraints, but began with `Goal - none observed`; this may be appropriate for a synthetic file-reading prompt, but it limits usefulness for a next agent turn.
- click deep produced no v3 summary because it fell back.

Full decoded v3 summaries were not available in the Stage 6.7 raw artifacts. The audit therefore could not fully verify invented files/commands, over-dumping, over-sparsity, or final next-agent usefulness.

## Overclaims and wording corrections

Recommended wording changes for future summaries:

- Replace "No classifier noise" with "No obvious classifier noise observed in this small corpus and the targeted unit negatives."
- Replace "This confirms the classifier tuning reduced the compactor input burden enough for v3 to succeed" with "This run correlates with quantzhai v3 acceptance, but causality is unproven."
- Replace "No v3 reliability regression" with "Aggregate v3 acceptance stayed high, but click deep regressed to v2 fallback."
- Replace "Stage 6.8 not needed" with "No immediate classifier code change is justified; a Stage 6.8 coverage/runner audit remains useful."
- Keep "fallback works" as a narrow claim tied to click deep Stage 6.7.

## Recommended next step

Do not tune the survival classifier from Stage 6.7 alone. The current tests and corpus evidence are sufficient to avoid an immediate classifier patch, but not sufficient to close the investigation.

Recommended Stage 6.8 shape:

1. Improve runner coverage without changing runtime compaction behavior:
   - select representative build files and source roots per language;
   - explicitly include Go `go.mod`/source, C++ `CMakeLists.txt`, and stb headers;
   - keep the existing sorted-file run as a bias baseline.
2. Persist enough evidence to audit quality:
   - full decoded v3 summary;
   - exact survival hints passed to the compactor;
   - v3/v2 fallback reason and budget/cap metadata.
3. Re-run targeted deep scenarios:
   - click deep fallback;
   - fd deep hint pressure;
   - quantzhai deep repeatability.
4. Treat Stage 6.8 as coverage and evidence hardening, not as assumed classifier tuning.

## Bottom line

Stage 6.7 is useful evidence that opt-in v3 remains promising and that the new regex atoms can fire in real repo text. It is not strong enough to support zero-noise, causal-improvement, or no-further-audit conclusions.
