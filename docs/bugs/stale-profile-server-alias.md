# Stale Profile `server_alias` Can Brick Codex Sessions

## Status

Open. This is a known robustness bug and should be fixed soon.

## Summary

QuantZhai supports profile-style model aliases, such as `prompt-compiler.gguf`, where the Codex-visible profile name is separate from the backend model actually loaded by the proxy.

That split is intentional and useful:

```text
Codex-visible profile: prompt-compiler
Prompt override:        prompt-compiler.gguf / system_prompt_file
Backend target:         server_alias -> real GGUF model stem
```

The current failure mode is that a profile can remain visible to Codex even when its `server_alias` points at a GGUF model that has been removed from `var/models/`.

When this happens, Codex can still select/send the profile name, the proxy resolves the profile, then the router tries to load/check a backend target that no longer exists. The result is a giant `503 Service Unavailable` response that can make Codex effectively unusable.

## Observed Failure

Example shape:

```text
unexpected status 503 Service Unavailable:
{
  "error": "no model available",
  "reason": "matched prompt-compiler; target Qwen3.6-35B-A3B-uncensored-heretic-APEX-I-Compact not ready (unknown)",
  "catalog": { ... huge model catalog dump ... }
}
```

Root cause:

```text
prompt-compiler.gguf still exists as a profile
var/model-overrides.json still has server_alias = removed model stem
removed backend model is no longer in var/models/
Codex menu/request can still select prompt-compiler
proxy routes prompt-compiler to stale server_alias
backend says unknown
proxy returns noisy 503
```

## Design Rule

Profiles are valid only if their backend target is valid.

A stale profile alias must not brick a Codex session.

Generated Codex catalogs are a view of proxy policy, not the authority. If the proxy cannot route a profile to a valid backend, Codex should not be encouraged to select it as if it were healthy.

## Required Fix

Implement validation for profile/backend target consistency.

### Catalog validation

`proxy/qz_model_catalog.py` should validate `server_alias` against scanned GGUF entries.

Suggested fields on each entry:

```text
backend_target: <resolved backend id or empty>
server_alias_valid: true | false | null
server_alias_error: <message or empty>
profile_valid: true | false
profile_error: <message or empty>
```

Rules:

```text
No server_alias:
  backend_target = entry stem
  profile_valid = true

server_alias points to an existing scanned model stem/key/filename/backend_id:
  backend_target = server_alias
  profile_valid = true

server_alias points nowhere:
  backend_target = ""
  profile_valid = false
  profile_error = "server_alias target not found: <alias>"
```

### Codex catalog generation

`scripts/qz-codex-common` should not expose invalid profiles in the generated Codex model picker.

Acceptable behavior:

```text
profile_valid=true:
  include in Codex catalog

profile_valid=false:
  hide from Codex catalog, or mark unavailable if Codex supports that cleanly
```

Do not show a dead profile as a healthy selectable model.

### Router behavior

`proxy/qz_model_router.py` should detect invalid profiles and return a compact actionable error.

Do not dump the entire catalog into the 503 response.

Preferred response shape:

```json
{
  "error": "profile backend missing",
  "profile": "prompt-compiler",
  "server_alias": "Qwen3.6-35B-A3B-uncensored-heretic-APEX-I-Compact",
  "reason": "server_alias target not found in scanned GGUF models",
  "fix": "Update var/model-overrides.json server_alias or restore the missing GGUF file."
}
```

### Fallback policy

Do not silently run a different backend model unless the profile explicitly opts into fallback.

Potential future override:

```json
{
  "models": {
    "prompt-compiler.gguf": {
      "server_alias": "preferred-model",
      "fallback_server_alias": "safe-model",
      "allow_backend_fallback": true
    }
  }
}
```

Until such a policy exists, fail clearly and compactly.

## Acceptance Tests

### Test 1: removed backend target does not appear healthy

1. Create or keep a profile alias:

```json
{
  "models": {
    "prompt-compiler.gguf": {
      "label": "prompt-compiler",
      "server_alias": "missing-model-stem",
      "system_prompt_file": "var/prompts/sillytavern_card_v2_runtime_prompt_compiler.md"
    }
  }
}
```

2. Ensure `missing-model-stem.gguf` does not exist in `var/models/`.
3. Regenerate the catalog:

```bash
python3 proxy/qz_model_catalog.py scan
```

Expected:

```bash
jq '.models[] | select(.key == "prompt-compiler.gguf") | {profile_valid, profile_error, server_alias_valid}' var/model-inventory.json
```

shows invalid profile state.

### Test 2: Codex menu does not offer dead profile as healthy

Run:

```bash
qz-codex
```

Open `/model`.

Expected:

```text
prompt-compiler is hidden or clearly unavailable, not shown as a healthy selectable model.
```

### Test 3: direct request gets compact error

If Codex or a client still sends:

```json
{"model":"prompt-compiler"}
```

Expected:

```text
HTTP 503 compact actionable error
no huge catalog dump
no silent fallback
```

### Test 4: valid profile still works

With `server_alias` pointing at an existing model:

```bash
jq '{model, instructions_head:(.instructions|.[0:180]), policy:.metadata.qz_prompt_policy}' var/captures/latest-forwarded.json
curl -s http://127.0.0.1:18180/qz/status | jq '.backend.selected_backend_id, .backend.loaded_model, .backend.selected_context_length'
```

Expected:

```text
forwarded model == server_alias backend
instructions start with the selected profile prompt
status reports loaded backend and correct context
```

## Related Rule

See `AGENTS.md` section: **Proxy Policy Is the Source of Truth**.

Any script that echoes proxy datapath information out to Codex must stay in sync with proxy behavior. That includes generated model catalogs, profile aliases, context windows, prompt sources, and status summaries.
