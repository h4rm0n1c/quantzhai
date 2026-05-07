# QuantZhai Caveman Codex Profile

`caveman` is an experimental Codex launcher profile for testing compact agent
instructions without changing the live model inventory.

Use:

```bash
scripts/qz-codex caveman
```

Runtime behavior:

- Uses the same local TurboQuant backend as the other QuantZhai Codex profiles.
- The Codex model picker now lists the actual GGUF models from `var/models`,
  and the per-model reasoning screen is generated from that same inventory.
- Low/medium/high/max now map to Qwen reasoning policy metadata. The proxy
  injects effort guidance and sampler params. No hard thinking-token cap is
  sent.
- Loads `docs/qz-caveman-codex-model-instructions-v2.md` through
  `model_instructions_file` when launched by `scripts/qz-codex caveman`. This
  appends the caveman behavior harness to the active Codex instruction stack;
  it is not a replacement system prompt.
- Starts each session with caveman chat mode on; the user can say `normal mode`
  or `caveman off` to switch back during the session.
- The model catalog now defaults to `medium` verbosity instead of `low`, so the
  coding agent starts with a less clipped answer style.

Manual test:

1. Start a fresh session with `scripts/qz-codex caveman`.
2. Ask `how are you?`.
3. Expected response is compressed, for example `good. need what?`.
4. Say `normal mode`, then ask another ordinary question.
5. Expected response switches back to normal concise English.

Reasoning knobs:

- Reasoning effort in the proxy now comes from the selected profile's policy
  metadata: prompt guidance plus sampler params. `thinking_budget_tokens` is
  stripped before upstream and reported as `null` in runtime metadata.
- `COMPACTION_CONFIG["target_output_tokens"]` controls local compaction summary
  size, not ordinary chat responses.

Current defaults separate model choice from answer length. Low/medium/high/max
select reasoning policy, not hard output caps.

Prompt-chain contract:

- The generated Codex model catalog provides model selection metadata and
  reasoning policy defaults.
- The caveman launcher adds `model_instructions_file` as a compression and
  style harness on top of the active Codex instructions.
- Proxy-side reasoning policy may prepend small effort guidance and sampler
  metadata for the active reasoning level.
- The harness must preserve Codex tool behavior, AGENTS compliance, escalation,
  patch discipline, and validation rules.
