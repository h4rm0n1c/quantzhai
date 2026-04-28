# QuantZhai Benchmark Harness

`scripts/qz-benchmark` runs fixed prompts through `codex exec --json` and writes
repeatable benchmark artifacts under `var/benchmarks/`.

Default run:

```bash
scripts/qz-up
scripts/qz-benchmark high caveman
```

Focused run:

```bash
scripts/qz-benchmark --only stack-evaluate-report high caveman
```

The required stack-evaluation prompt is:

```text
examine current dir, this stack runs you: evaluate, report.
```

Runtime behavior:

- Uses the same Codex home, model catalog, and local proxy wiring as
  `scripts/qz-codex`.
- If the backend is healthy but the proxy is down, starts a temporary proxy for
  the benchmark run. Use `--no-manage-proxy` to require an already-running
  proxy.
- Defaults to `--sandbox read-only`, `--approval never`, and `--ephemeral` for
  safe non-interactive runs.
- Captures Codex JSONL events, final answers, copied proxy captures, per-case
  `result.json`, and run-level `summary.json` / `summary.md`.
- Writes `var/benchmarks/latest-summary.json` so `scripts/qz-top` can show the
  latest compression summary.

Compression metrics:

- Baseline profile is `high` when present, otherwise the first profile in the
  command.
- Primary metric is input-token ratio:
  `profile_input_tokens / baseline_input_tokens`.
- Input savings is `1 - input_ratio`.
- Instruction-token ratio tracks the prompt/system-instruction compression
  directly.
- Final-answer ratio tracks visible response brevity.
- API output-token ratio is recorded separately because it can include hidden
  reasoning tokens and may not match visible answer length.
- Total-token ratio and wall-time ratio are also recorded.

Prompt fixture:

```text
config/qz-benchmark-prompts.json
```

Current prompts test:

- repository comprehension and reporting
- startup style latch overhead
- bounded read-only inspection
- artifact-boundary behavior

Generated benchmark data is runtime state and must stay out of git.
