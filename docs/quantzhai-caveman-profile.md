# QuantZhai Caveman Codex Profile

`caveman` is an experimental Codex model/profile for testing compact agent
instructions without changing the real backend model identity.

Use:

```bash
scripts/qz-codex
# then select caveman in the model picker

scripts/qz-codex exec -m caveman --json --ephemeral 'Say caveman status.'
```

Runtime behavior:

- Uses the same local TurboQuant backend as the other QuantZhai Codex profiles.
- Exposes `caveman` through `var/models/caveman.gguf`, normally a symlink to a
  real GGUF under `var/models/`.
- Keeps the profile identity on the symlink name. `config/default/model-overrides.json`
  carries the shipped caveman defaults, while `config/user/model-overrides.json`
  is the active local override layer. The legacy `var/model-overrides.json`
  path only remains as a fallback when the user file is absent.
- For behavior-only testing, point that symlink at the same backend GGUF already
  used by the normal profile. Pointing it at a different GGUF is a deliberate
  model-swap profile and will make llama.cpp load that other backend.
- The Codex model picker now lists the actual GGUF models from `var/models`,
  and the per-model reasoning screen is generated from that same inventory.
- Low/medium/high/max now map to Qwen reasoning policy metadata. The proxy
  injects effort guidance and sampler params. No hard thinking-token cap is
  sent.
- Loads `config/default/prompts/caveman-mode.md` as a per-profile
  `prompt_append_files` entry. This appends the caveman behavior harness to the
  active Codex instruction stack; it is not a replacement system prompt.
- Reinforces later turns with `caveman-ultra-lock` through the static
  turn-harness system.
- Keeps client-visible reasoning summaries visible by default. Caveman is a
  coding-agent profile; hidden reasoning is for roleplay/private-thought
  profiles, not the normal compact coding workflow.
- Starts each session with caveman ultra mode on and locked; the user can say
  `normal mode`, `plain English`, `verbose mode`, `caveman off`, or
  `stop caveman` to switch back during the session.
- The model catalog now defaults to `medium` verbosity instead of `low`, so the
  coding agent starts with a less clipped answer style.
- Updating the caveman symlink or profile overrides is picked up on proxy
  refresh. Restart `scripts/qz-proxy` or use `/qz/models/refresh` after
  changing the symlink target or caveman override file.

Manual test:

1. Start a fresh session with `scripts/qz-codex` and select `caveman`.
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
- The caveman profile adds `prompt_append_files` as a compression and style
  harness on top of the active Codex instructions.
- Static turn harnesses are injected before eligible later user turns, not the
  first turn immediately following a fresh system prompt.
- Proxy-side reasoning policy may prepend small effort guidance and sampler
  metadata for the active reasoning level.
- The harness must preserve Codex tool behavior, AGENTS compliance, escalation,
  patch discipline, and validation rules.
