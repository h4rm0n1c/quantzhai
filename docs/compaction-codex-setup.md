# Compaction Codex Setup

Date: 2026-05-27
Status: **Stage 1** — config/docs only. No runtime changes.
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

No live runtime smoke is required for Stage 1. Stage 3 (eval harness) will
provide structured evaluation against the fixture examples.

---

## Related Documents

- `docs/compaction-anchored-schema-v0.md` — full schema specification and
  field rules.
- `docs/compaction-audit-and-strategy.md` — Stage 0 audit; Codex source
  evidence for compaction config fields; staged plan Stages 0–6.
- `docs/fixtures/compaction/` — example input/output pairs for schema
  compliance testing.
- `docs/compaction-bridge-plan.md` — localcmp:v2: blob format and heuristic
  compaction delivery (separate from Codex inline compaction).
