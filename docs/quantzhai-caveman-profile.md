# QuantZhai Caveman Codex Profile

`Qwen3.6Turbo-caveman` is an experimental Codex model option for testing compact
agent instructions without changing the proven low, medium, high, and max slugs.

Use:

```bash
scripts/qz-codex caveman
```

Runtime behavior:

- Uses the same local TurboQuant backend as the other Qwen3.6Turbo profiles.
- Maps to a medium `thinking_budget_tokens` value in the proxy.
- Loads `docs/qz-caveman-codex-model-instructions-v2.md` through
  `model_instructions_file` when launched by `scripts/qz-codex caveman`.
- Sets `model_max_output_tokens=2048` for that launcher session.
- Starts each session with caveman chat mode on; the user can say `normal mode`
  or `caveman off` to switch back during the session.

Manual test:

1. Start a fresh session with `scripts/qz-codex caveman`.
2. Ask `how are you?`.
3. Expected response is compressed, for example `good. need what?`.
4. Say `normal mode`, then ask another ordinary question.
5. Expected response switches back to normal concise English.

Response-size knobs:

- `model_max_output_tokens` in the Codex config or launcher override controls
  how much Codex asks the model to emit.
- `MODEL_BUDGETS` in `proxy/quantzhai_proxy.py` controls Qwen thinking budget,
  not final answer length.
- `COMPACTION_CONFIG["target_output_tokens"]` controls local compaction summary
  size, not ordinary chat responses.

Current defaults are intentionally mixed: medium is normal-large at 4096 output
tokens, high is large at 8192, max is very large at 12000, and caveman is smaller
at 2048.
