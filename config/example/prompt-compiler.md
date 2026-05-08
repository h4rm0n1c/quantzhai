# Prompt Compiler Example

This is an inactive example for a Codex-visible `prompt-compiler` profile using
the current QuantZhai model-dir profile contract.

Required shape:

```text
var/models/prompt-compiler.gguf -> real-backend-model.gguf
config/user/model-overrides.json or selected example overrides
config/example/prompts/prompt-compiler.md or copied user prompt file
```

Example override entry:

```json
{
  "models": {
    "prompt-compiler.gguf": {
      "label": "prompt-compiler",
      "runtime_context_length": 131072,
      "system_prompt_file": "config/example/prompts/prompt-compiler.md"
    }
  }
}
```

Example setup:

```bash
cd /path/to/quantzhai
ln -s Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL.gguf var/models/prompt-compiler.gguf
```

For real local use, copy the override into `config/user/model-overrides.json`
and either keep the prompt path pointing at this example file or copy the prompt
to a user-owned path such as `config/user/prompts/prompt-compiler.md`.

Do not add `server_alias`, `backend_id`, or old synthetic profile aliases. The
backend target is always the resolved symlink target under `var/models/`.
