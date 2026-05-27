# Compaction Codex Setup

Date: 2026-05-28
Status: **Stage 6.10.1** — OpenAI provider masquerade active. `CODEX_PROVIDER_NAME = "OpenAI"` causes Codex to use `POST /v1/responses/compact` remote compaction path (Zenkai v3 / heuristic v2 fallback). `model_auto_compact_token_limit` emitted at safe budget (16.5% reserve). Stage 6.10 promoted v3/Zenkai to default (mode=auto). v2 heuristic preserved as fallback.
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

**For QuantZhai sessions**, the active Codex config is managed by `qz-codex` and lives at:
```
$HOME/.qz-codex/codex-home/config.toml
```
This is the `CODEX_HOME` set by `scripts/qz-codex-common`. Do **not** edit `~/.codex/config.toml` for QuantZhai sessions — it is the Codex system default and is not the active config.

For non-QuantZhai / manual Codex use only, add to `~/.codex/config.toml`:

```toml
[experimental]
compact_prompt_file = "/your/path/to/quantzhai/config/default/prompts/compact-v0.md"
```

Source (audited): `codex-rs/config/src/config_toml.rs:449`
Field name: `experimental_compact_prompt_file`

### Per-profile setting (profile TOML)

Codex also supports `experimental_compact_prompt_file` in a named profile TOML.

For non-QuantZhai / manual Codex use only (`~/.codex/profiles/`):

```toml
# ~/.codex/profiles/quantzhai-high.toml  (example — non-QuantZhai use only)
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

- **`model_auto_compact_token_limit` is now emitted (Stage 6.10).** The generated
  Codex model catalog now includes `model_auto_compact_token_limit` set to
  `safe_compaction_budget_tokens` (= `context_window - floor(context_window * 0.165)`).
  For a 256k context this is 218891 tokens. This enables Codex inline auto-compaction
  at the same threshold as QuantZhai's proxy-side budget policy.

- **Stage 6.10.1: OpenAI provider masquerade enables remote compaction.** `CODEX_PROVIDER_NAME`
  is now `"OpenAI"` in `proxy/qz_codex_client_config.py`. Codex's `supports_remote_compaction()`
  gate checks `provider.name == "OpenAI"`, which routes auto-compaction (triggered by
  `model_auto_compact_token_limit`) to `POST /v1/responses/compact` instead of inline local
  compaction. QuantZhai handles this endpoint with Zenkai v3 / heuristic v2 fallback.
  The generated `config.toml` provider block has `name = "OpenAI"` with `requires_openai_auth`
  absent (defaults to false) — no real OpenAI credentials are needed.

  **Double-compaction is not a risk with the remote path.** The `/v1/responses/compact` and
  `/v1/responses` paths are separate. Inline proxy compaction (triggered by
  `context_management.compact_threshold`) is not sent by Codex when it uses remote compaction.
  The `RemoteCompactionV2` feature flag (default: false) is left disabled — this keeps Codex
  on the `compact_remote::run_inline_remote_auto_compact_task` path, which calls
  `/v1/responses/compact` (not the v2 path which sends `ContextCompaction` items to
  `/v1/responses`). Do not enable `RemoteCompactionV2` in QuantZhai sessions.

- **Both compaction paths may be active simultaneously (Stage 6.10 note, now superseded for auto-compact).**
  With Stage 6.10.1 remote compaction active, Codex's auto-compact trigger uses
  `/v1/responses/compact`. The `context_management.compact_threshold` proxy-side path remains
  available for explicit inline compaction but is not triggered by auto-compact. Monitor for
  unexpected `context_management.compact_threshold` requests if proxy-side auto-compact is
  separately configured.

- **Placeholder semantics are for future integration.** The `{{PREVIOUS_ANCHORED_SUMMARY}}`
  and `{{NEW_CONVERSATION}}` placeholders in `compact-v0.md` are for QuantZhai
  Stage 4+ proxy integration, where the proxy will populate them before the
  compaction turn. For current Codex inline use, these placeholders appear as
  literal text in the prompt. Codex appends the full session context from its
  conversation state.

- **LLM compaction backend URL resolves automatically (Stage 6.10.2).** Zenkai v3 LLM
  compaction now defaults to `http://$QZ_SERVER_HOST:$QZ_SERVER_PORT` (the active
  llama-server backend, same URL as the proxy's `--upstream` argument). `QZ_LLM_COMPACT_BASE_URL`
  is only needed as an explicit debug override. Do NOT set it to the QuantZhai proxy URL
  (`http://127.0.0.1:18180`) or `CODEX_OSS_BASE_URL` — the recursion guard will reject it.
  The hardcoded `llm_base_url` has been removed from `config/default/compaction.json`.

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

**Stage 6.10.2 smoke sequence (remote compaction path):**

1. `./scripts/qz-down` — stop proxy and backend.
2. `./scripts/qz-up` — start proxy and backend.
3. Launch `./scripts/qz-codex` (interactive) in a **disposable test repo** to regenerate
   the client-local CODEX_HOME config and catalog.
   The active config is: `$HOME/.qz-codex/codex-home/config.toml`
   (not `~/.codex/config.toml`).
4. Verify the config was written correctly:
   ```bash
   grep -A5 '\[model_providers.quantzhai\]' "$HOME/.qz-codex/codex-home/config.toml"
   # Expected: name = "OpenAI"
   ```
5. Verify `model_auto_compact_token_limit` is in the client-local model catalog:
   ```bash
   jq '.models[].model_auto_compact_token_limit // empty' \
     "$HOME/.qz-codex/codex-home/model-catalogs/qwenzhai-models.json" | head -3
   # Expected: 218891 (for 256k context)
   ```
6. Smoke the remote compaction endpoint directly:
   ```bash
   curl -s -X POST http://127.0.0.1:18180/v1/responses/compact \
     -H 'Content-Type: application/json' \
     -d '{"model":"Qwen3.6Turbo-27B","input":[
       {"type":"message","role":"user","content":[{"type":"input_text","text":"Describe Paris."}]},
       {"type":"message","role":"assistant","content":[{"type":"output_text","text":"Paris is the capital of France."}]}
     ],"instructions":"Summarise.","tools":[],"parallel_tool_calls":false}' \
     | jq '{type:.output[0].type, blob:.output[0].encrypted_content[:20]}'
   # Expected: {"type": "compaction", "blob": "localcmp:v2:" or "localcmp:v3:"}
   ```
7. Launch a Codex session and build context until auto-compact fires (near 218891 tokens).
   Verify proxy logs show a POST to `/v1/responses/compact` (not inline compaction).

**Older inline compaction smoke (still valid for /compact TUI):**

1. Build enough conversation context to fill a few hundred tokens.
2. Invoke `/compact` from the Codex TUI.
3. Inspect the resulting compacted summary in the next Codex turn.
4. Verify the output follows the schema sections and preserves any exact paths
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
