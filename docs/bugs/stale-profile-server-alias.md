# Stale Profile Symlink Can Brick Codex Sessions

## Status

Fixed. Current contract is symlink-based profile routing with compact errors for invalid profiles, no retained `server_alias` override, and `qz-doctor` runtime checks.

## Summary

QuantZhai supports profile-style model aliases, such as `prompt-compiler.gguf`, by letting the user create a symlink in `var/models/`.

The contract is:

```text
Codex-visible profile: var/models/prompt-compiler.gguf
Prompt override:        prompt-compiler.gguf / system_prompt_file
Backend target:         resolved symlink target GGUF stem
```

The profile filename is the identity Codex sees and the user selects. The proxy resolves the symlink target internally and routes backend requests to the real scanned GGUF model stem.

Overrides may add metadata such as `label`, `runtime_context_length`, `system_prompt_file`, or `system_prompt`. Overrides must not carry a backend-name override. The backend is always the resolved symlink target.

## Failure Mode

A profile can remain visible to Codex when its symlink target has been removed, moved outside the scanned model directory, or no longer resolves to a scanned GGUF.

Example:

```text
var/models/prompt-compiler.gguf -> missing-or-external.gguf
Codex menu/request selects prompt-compiler
proxy cannot resolve prompt-compiler to a scanned backend target
old behaviour returns noisy 503 / unusable Codex session
```

Current behaviour hides invalid profiles from generated Codex catalogs where possible, and direct invalid-profile requests return compact actionable errors.

## Design Rule

Profiles are valid only if their symlink target resolves to a real GGUF that is also scanned under `var/models/`.

A stale profile alias must not brick a Codex session.

Generated Codex catalogs are a view of proxy policy, not the authority. If the proxy cannot route a profile to a valid backend, Codex should not be encouraged to select it as if it were healthy.

## Required Behaviour

### Catalog validation

`proxy/qz_model_catalog.py` validates symlink profiles against scanned GGUF entries.

Fields on each entry:

```text
profile_symlink: true | false
backend_target: <resolved backend id or empty>
profile_valid: true | false
profile_error: <message or empty>
source_path: <profile symlink path or file path>
path: <resolved GGUF path>
```

Rules:

```text
Regular GGUF:
  backend_target = entry stem
  profile_valid = true

Symlink points to an existing scanned GGUF:
  backend_target = resolved target stem
  profile_valid = true

Symlink points outside scanned models or nowhere:
  backend_target = ""
  profile_valid = false
  profile_error = "symlink target not found in scanned GGUF models: <path>"
```

### Codex catalog generation

`scripts/qz-codex-common` does not expose invalid profiles in the generated Codex model picker.

Acceptable behaviour:

```text
profile_valid=true:
  include in Codex catalog

profile_valid=false:
  hide from Codex catalog, or mark unavailable if Codex supports that cleanly
```

### Router behaviour

`proxy/qz_model_router.py` detects invalid profiles and returns a compact actionable error.

Do not dump the entire catalog into the 503 response.

Preferred response shape:

```json
{
  "error": "profile backend missing",
  "profile": "prompt-compiler",
  "reason": "symlink target not found in scanned GGUF models: /path/to/missing.gguf",
  "fix": "Update the profile symlink under var/models or restore the missing target GGUF file."
}
```

### Fallback policy

Do not silently run a different backend model.

If a symlink profile target disappears, fail clearly and compactly. Silent fallback would make prompt/profile behaviour unpredictable.

## Acceptance Tests

`scripts/qz-doctor` now checks this contract directly:

```text
profile catalog contract
codex config has no stale static model limits
live profile/backend/context contract
recent prompt contract telemetry
prompt contract smoke request, when QZ_DOCTOR_PROMPT_SMOKE=1
```

### Test 1: valid symlink profile routes to target backend

1. Create a real GGUF under `var/models/`.
2. Create a symlink profile:

```bash
ln -s real-backend.gguf var/models/prompt-compiler.gguf
```

3. Add optional metadata only:

```json
{
  "models": {
    "prompt-compiler.gguf": {
      "label": "prompt-compiler",
      "system_prompt_file": "var/prompts/sillytavern_card_v2_runtime_prompt_compiler.md"
    }
  }
}
```

4. Regenerate the catalog:

```bash
python3 proxy/qz_model_catalog.py scan
```

Expected:

```text
prompt-compiler.gguf remains the Codex-visible profile
backend_target is the real target GGUF stem
profile_valid is true
```

### Test 2: stale profile does not appear healthy

If `var/models/prompt-compiler.gguf` points outside scanned models or to a missing file:

```bash
python3 proxy/qz_model_catalog.py scan
```

Expected:

```text
profile_valid is false
profile_error explains the missing symlink target
backend_target is empty
```

### Test 3: Codex menu does not offer dead profile as healthy

Run:

```bash
qz-codex
```

Open `/model`.

Expected:

```text
prompt-compiler is hidden or clearly unavailable, not shown as a healthy selectable model.
```

### Test 4: direct request gets compact error

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

### Test 5: qz-doctor catches stale runtime/config drift

Run:

```bash
scripts/qz-doctor
QZ_DOCTOR_PROMPT_SMOKE=1 scripts/qz-doctor
```

Expected:

```text
profile catalog contract passes
live profile/backend/context contract passes
prompt contract smoke request passes when proxy is current
stale Codex model_context_window/model_max_output_tokens overrides fail clearly
```

If the prompt smoke fails after a pull, restart the proxy:

```bash
scripts/qz-proxy
QZ_DOCTOR_PROMPT_SMOKE=1 scripts/qz-doctor
```

## Related Rule

See `AGENTS.md` section: **Proxy Policy Is the Source of Truth**.

Any script that echoes proxy datapath information out to Codex must stay in sync with proxy behavior. That includes generated model catalogs, profile aliases, context windows, prompt sources, and status summaries.
