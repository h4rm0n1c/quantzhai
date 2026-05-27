# Compaction Codex Setup

Date: 2026-05-27
Status: **Stage 6** — prompt file, profile config, and direct-backend dogfood runbook.
Codex audit SHA: `46f30d02828bd4c52827e5f0482a6f2a982cce5b`

---

## What This Config Does

`config/default/prompts/compact-v0.md` is an anchored-update compaction prompt
for Codex inline compaction. When wired via `experimental_compact_prompt_file`,
Codex sends this prompt (instead of its default 5-bullet CONTEXT CHECKPOINT
COMPACTION prompt) as the user message for each inline compaction turn.

The prompt instructs the model to:
- Preserve exact technical atoms (paths, SHAs, commands, errors).
- Preserve negative constraints and user corrections verbatim.
- Update an existing anchored summary rather than produce freeform prose.
- Correct stale facts; mark uncertain items explicitly.

See `docs/compaction-anchored-schema-v0.md` for the full schema specification
and field rules.

---

## Where the Prompt File Lives

```text
config/default/prompts/compact-v0.md
```

Absolute path (substitute your own `$QZ_ROOT`):

```bash
# QZ_ROOT is typically the quantzhai repo root, e.g. ~/turboquant/quantzhai
echo $QZ_ROOT/config/default/prompts/compact-v0.md
```

---

## Wiring into Codex Config

### Global setting (config.toml)

Add to your Codex `config.toml` (typically `~/.codex/config.toml`),
substituting your `$QZ_ROOT`:

```toml
[experimental]
compact_prompt_file = "/your/path/to/quantzhai/config/default/prompts/compact-v0.md"
```

Source (audited): `codex-rs/config/src/config_toml.rs:449`
Field name: `experimental_compact_prompt_file`

### Per-profile setting (profile TOML)

Codex also supports `experimental_compact_prompt_file` in a named profile TOML:

```toml
# ~/.codex/profiles/quantzhai-high.toml  (example)
experimental_compact_prompt_file = "/your/path/to/quantzhai/config/default/prompts/compact-v0.md"
```

Source (audited): `codex-rs/config/src/profile_toml.rs:56`

This allows different compaction prompts per profile — for example, a caveman
profile may use a shorter schema, while the default profile uses `compact-v0.md`.

### Inline compact_prompt (alternative, no file)

The full prompt text can also be inlined directly:

```toml
compact_prompt = "..."
```

Source (audited): `codex-rs/config/src/config_toml.rs:175`

This is less convenient for editing and does not benefit from version-controlled
prompt files. Prefer `experimental_compact_prompt_file` for QuantZhai sessions.

---

## QuantZhai Compaction Config

Stage 5 adds a proxy-side config file:

```text
config/default/compaction.json
```

The default profile is safe and keeps heuristic `localcmp:v2:` compaction:

```json
{
  "version": 1,
  "profiles": {
    "default": {
      "mode": "heuristic",
      "survival_profile": "coding",
      "prompt_file": "config/default/prompts/compact-v0.md"
    }
  }
}
```

To test opt-in LLM compaction, select a profile that explicitly sets `mode` and
a direct backend URL:

```bash
QZ_COMPACTION_PROFILE=coding-llm
```

`QZ_COMPACTION_CONFIG=/path/to/compaction.json` can point at another JSON file.
Env vars always override the selected JSON profile:

```text
QZCOMPACT
QZ_LLM_COMPACT_BASE_URL
QZ_LLM_COMPACT_MODEL
QZ_LLM_COMPACT_TIMEOUT_SEC
QZ_LLM_COMPACT_MAX_INPUT_CHARS
QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS
QZ_LLM_COMPACT_PROMPT_FILE
QZ_LLM_COMPACT_DISABLE_REASONING
QZ_COMPACTION_PROFILE
QZ_COMPACTION_SURVIVAL_PROFILE
```

Stage 5 supports only `survival_profile: "coding"`. Unknown values fall back to
`coding`; survival weights, thresholds, regexes, plugins, and import paths are
not configurable.

---

## Warnings

- **This only affects Codex inline compaction prompt.** It does not change
  QuantZhai's `localcmp:v2:` heuristic compaction in `proxy/qz_responses.py`.
  Both paths are separate.

- **This does not enable auto-compaction by itself.** Auto-compaction still
  requires `model_auto_compact_token_limit` to be set in the Qwen model catalog,
  or the user to invoke `/compact` manually in the Codex TUI.

- **Do not enable multiple compaction paths blindly.** If both
  `model_auto_compact_token_limit` (Codex auto-compact) and
  `context_management.compact_threshold` (QuantZhai proxy auto-compact) are
  active simultaneously, both paths may fire with different thresholds. This
  interaction has not been tested. Leave `model_auto_compact_token_limit` unset
  until Stage 1 prompt is validated and Stage 3 eval confirms no regression.

- **Placeholder semantics are for future integration.** The `{{PREVIOUS_ANCHORED_SUMMARY}}`
  and `{{NEW_CONVERSATION}}` placeholders in `compact-v0.md` are for QuantZhai
  Stage 4+ proxy integration, where the proxy will populate them before the
  compaction turn. For current Codex inline use, these placeholders appear as
  literal text in the prompt. Codex appends the full session context from its
  conversation state.

- **LLM compaction backend URL must be direct.** If using the opt-in
  QuantZhai `localcmp:v3:` path, `QZ_LLM_COMPACT_BASE_URL` must point at the
  actual model backend, not the QuantZhai proxy or its `/v1` endpoint. For
  example, use `QZ_LLM_COMPACT_BASE_URL=http://127.0.0.1:8080` only when that
  is the direct llama.cpp/OpenAI-compatible backend. Do not set it to
  `CODEX_OSS_BASE_URL` or `http://127.0.0.1:18180`.

- **Stage 6 is dogfood/live tuning.** Do not run a live LLM smoke through the
  QuantZhai proxy URL; use only a clearly identified direct backend.

- **Stage 6.1 tunes thinking-mode backends narrowly.** The LLM compactor asks
  for final anchored output in `message.content` and, by default, sends
  `thinking_budget_tokens: 0` and `reasoning_budget_tokens: 0` on the
  compactor-only backend call. `reasoning_content` is not accepted as a summary.
  Set `QZ_LLM_COMPACT_DISABLE_REASONING=0` only if a direct backend rejects
  those fields; invalid or missing summaries still fall back to `localcmp:v2:`.
  See `docs/compaction-live-dogfood.md`.

---

## Suggested Manual Smoke Test

After wiring the config:

1. Start `qz-up` and launch `qz-codex` in a **disposable test repo**
   (e.g. `/tmp/linuxstreamtools` — safe smoke target).
2. Build enough conversation context to fill a few hundred tokens.
3. Invoke `/compact` from the Codex TUI.
4. Inspect the resulting compacted summary in the next Codex turn.
5. Verify the output follows the schema sections and preserves any exact paths
   or commands you mentioned during the session.

Live LLM smoke requires a clearly identified direct backend outside the
QuantZhai proxy. The Stage 6 runbook and first results live in
`docs/compaction-live-dogfood.md`.

---

## Related Documents

- `docs/compaction-anchored-schema-v0.md` — full schema specification and
  field rules.
- `docs/compaction-audit-and-strategy.md` — Stage 0 audit; Codex source
  evidence for compaction config fields; staged plan Stages 0–6.
- `docs/compaction-live-dogfood.md` — Stage 6 direct-backend runbook, smoke
  evidence, fallback observation, and tuning notes.
- `docs/fixtures/compaction/` — example input/output pairs for schema
  compliance testing.
- `docs/compaction-bridge-plan.md` — localcmp:v2: blob format and heuristic
  compaction delivery (separate from Codex inline compaction).
