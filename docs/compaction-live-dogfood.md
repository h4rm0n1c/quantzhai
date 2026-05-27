# Compaction Live Dogfood

Date: 2026-05-27
Status: **Stage 6 initial dogfood complete, v3 not yet accepted live**
Base commit tested: `f72150a`

This note records the first live opt-in LLM compaction run for issue #8.
It is a runbook and evidence summary, not a private capture dump.

## Backend Identification

Confirmed local endpoints:

- QuantZhai proxy: `http://127.0.0.1:18180`
- Temporary capture proxy: `http://127.0.0.1:18183`
- Direct llama.cpp backend: `http://127.0.0.1:18084`

The direct backend exposed `/v1/models` and `/health`, and the proxy status
reported upstream `http://127.0.0.1:18084`. The live LLM compactor was pointed
at `http://127.0.0.1:18084`, not at the QuantZhai proxy or a proxy `/v1`
endpoint.

Do not use `CODEX_OSS_BASE_URL`, `http://127.0.0.1:18180`, or
`http://127.0.0.1:18183` as `QZ_LLM_COMPACT_BASE_URL`.

## Smoke Setup

Disposable repo:

```text
/tmp/linuxstreamtools
```

Smoke repo commit:

```text
1864b99 Merge pull request #18 from h4rm0n1c/agent/docs/repo-map
```

Temporary proxy used for captured smoke:

```text
QZ_PROXY_PORT=18183
QZ_CAPTURE_MODE=full
python3 proxy/quantzhai_proxy.py \
  --listen 127.0.0.1 \
  --port 18183 \
  --upstream http://127.0.0.1:18084 \
  --reasoning-stream-format summary
```

Opt-in LLM compaction proxy run:

```text
QZ_PROXY_PORT=18183
QZ_CAPTURE_MODE=full
QZ_COMPACTION_PROFILE=coding-llm
QZ_LLM_COMPACT_BASE_URL=http://127.0.0.1:18084
QZ_LLM_COMPACT_TIMEOUT_SEC=120
python3 proxy/quantzhai_proxy.py \
  --listen 127.0.0.1 \
  --port 18183 \
  --upstream http://127.0.0.1:18084 \
  --reasoning-stream-format summary
```

An additional diagnostic run used:

```text
QZ_LLM_COMPACT_TIMEOUT_SEC=180
QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS=4096
```

Those were live-smoke env overrides only. Defaults and
`config/default/compaction.json` remain safe.

## Results

Default qz-codex smoke:

- Ran against `/tmp/linuxstreamtools` through the temporary proxy.
- Prompt: "please inspect this repo briefly and tell me the top-level files,
  then stop".
- Completed successfully and listed `AGENTS.md`, `LICENSE`, `README.md`,
  `docs/`, `obs_stuff/`, `streamlinkbgm/`, and `vban/`.
- `/tmp/linuxstreamtools` remained clean.

Forced heuristic compaction smoke:

- `tests/smoke_compaction_live.py --proxy-url http://127.0.0.1:18183`
  passed `10/10` checks.
- The forced compaction returned `localcmp:v2:`.
- The follow-up model response completed successfully.

Opt-in LLM compaction smoke:

- Used `QZ_COMPACTION_PROFILE=coding-llm`.
- Used direct `QZ_LLM_COMPACT_BASE_URL=http://127.0.0.1:18084`.
- Forced compaction returned `response.compaction`, but the accepted blob was
  `localcmp:v2:`, not `localcmp:v3:`.
- Fallback therefore worked: invalid/no LLM compactor output did not crash the
  stream and did not block compaction.

Live diagnostics:

- With the default output budget, the direct backend often returned no
  `choices[0].message.content` for the compactor prompt.
- A direct probe showed the backend can emit only `reasoning_content` when the
  completion budget is consumed before final content.
- With `QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS=4096`, one diagnostic run returned
  anchored text, but it stopped after `## Active Constraints & Guardrails` and
  failed the required-heading validator.

Local capture dirs from this run include:

```text
var/captures/requests/qz_req_1779867851415_2d50
var/captures/requests/qz_req_1779867859850_2e40
var/captures/requests/qz_req_1779867911679_10f0
var/captures/requests/qz_req_1779867911691_3450
var/captures/requests/qz_req_1779868663915_9130
var/captures/requests/qz_req_1779868998102_a250
var/captures/requests/qz_req_1779869125517_f110
```

Do not paste full capture bodies into issue comments; they may contain session
context.

## Tuning Applied

`config/default/prompts/compact-v0.md` now explicitly says:

- every schema heading must be emitted exactly once;
- output is invalid unless it reaches `## Next Actions`;
- empty sections should use `- none observed`.

This was based on live evidence that the model produced a partial anchored
summary that failed validation. No code path, default mode, routing behaviour,
native tool behaviour, lifecycle event shape, or compaction blob format changed.

## Next Step

Stage 6.1 should tune live v3 acceptance against this backend. The likely
fault line is the direct backend's thinking-mode behaviour for chat
completions: compactor calls can return reasoning-only output or stop before
the canonical headings complete. Investigate a narrowly gated compactor request
policy or prompt strategy that causes final `message.content` to contain the
complete anchored schema, while keeping v3 opt-in and preserving v2 fallback.
