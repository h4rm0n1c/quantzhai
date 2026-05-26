# Fixture 03: Rejected Approaches and Negative Constraints

**What this fixture tests:**
- Rejected / abandoned approach preservation
- Negative constraint verbatim retention
- User correction preservation
- Deferred work with explicit reasons
- Stale fact correction

---

## Input Sketch

```text
[User] We need to handle the permissions escalation issue (#74). Agent was
       trying to call sudo docker mid-session and blocking on interactive prompt.

[Agent] Reading docs/bugs/ ... found no existing note for this pattern.

[Agent] Proposal: intercept sudo prompts at the proxy layer and return a
        synthetic advisory to Codex explaining it cannot answer interactive
        prompts.

[User] No. Do not intercept sudo at the proxy. That would require reading
       terminal output, which the proxy does not own. Park this instead —
       just add a note to AGENTS.md for now.

[Agent] Understood. Do not implement proxy-level sudo interception.
        Adding to AGENTS.md under "Host Sudo Workflow".

[Agent] Also considered: add a qz-codex wrapper that pre-runs sudo -v before
        launch so the ticket is valid for the session. But this is fragile —
        sudo -v timeout is host-dependent and the wrapper would need updating
        for every host config change.

[User] Agreed. Don't add that wrapper.

[Agent] AGENTS.md updated. Committed: git commit -m "Add permission outcome
        feedback advisory"  SHA: babf7b5

[User] Now look at whether we should set model_auto_compact_token_limit in
       the Qwen catalog so Codex fires auto-compaction.

[Agent] Checking config/default/model-catalog.json ...
        rg "auto_compact" config/

[Tool: local_shell] exit_code=0
  stdout: (no output)

[Agent] Not set. Checking Codex source for the field name:
        rg "model_auto_compact_token_limit" /tmp/qz-audit/codex/codex-rs/

[Tool: local_shell] exit_code=0
  stdout:
    codex-rs/config/src/config_toml.rs:105: model_auto_compact_token_limit

[Agent] Confirmed: the field is model_auto_compact_token_limit in the Codex
        model catalog. Not set in QuantZhai catalog. Auto-compaction does not
        fire for qz-codex sessions today.

[Agent] Risk of setting it: if compact_prompt is not also configured,
        Codex will use its default generic prompt, which does not preserve
        exact technical atoms. Two compaction paths would be active if
        QuantZhai's compact_threshold is also set.

[User] Leave it unset for now. Document the risk in the open questions.

[Agent] Noted. model_auto_compact_token_limit left unset.
        Risk documented: two active compaction paths if both limits are set.
```

---

## Expected Anchored Summary Output

```markdown
## Goal
Resolve #74 permissions escalation blocking issue and evaluate
model_auto_compact_token_limit for the Qwen catalog.

## Active Constraints & Guardrails
- Do not implement proxy-level sudo interception. The proxy does not own
  terminal output. (user correction, this session)
- Do not add a sudo -v pre-run wrapper to qz-codex. Fragile; host-dependent
  timeout; maintenance burden. (agreed, this session)
- Do not set model_auto_compact_token_limit without also setting compact_prompt.
  Risk: two active compaction paths; Codex falls back to generic prompt.

## Current Status
### Done
- Add permission outcome feedback advisory — commit babf7b5
  (AGENTS.md "Host Sudo Workflow" section added)

### In Progress
- (none)

### Blocked / Deferred
- #74 permissions/escalation: parked. Proxy sudo interception explicitly
  rejected. sudo -v wrapper rejected. AGENTS.md note is the current mitigation.
- model_auto_compact_token_limit: left unset pending compact_prompt
  configuration (Stage 1). Risk documented in open questions.

## Key Decisions
- Proxy sudo interception rejected. Evidence: proxy does not own terminal
  output (user correction). Alternative deferred: none viable.
- sudo -v wrapper rejected. Evidence: sudo -v timeout is host-dependent;
  wrapper requires per-host config updates (agent assessment, user confirmed).
- model_auto_compact_token_limit left unset. Evidence: rg found no field in
  config/; Codex source confirms field at config_toml.rs:105. Risk: two
  compaction paths active if both compact_threshold and auto_compact_token_limit
  are set with different limits.

## Evidence Boundaries
- Confirmed (source): model_auto_compact_token_limit at
  codex-rs/config/src/config_toml.rs:105.
- Confirmed (rg): rg "auto_compact" config/ — no output. Field not set in
  QuantZhai catalog.
- Negative evidence: no existing bug note in docs/bugs/ for sudo-interception
  pattern (rg checked before proposing).

## Technical State
### Files / Paths
AGENTS.md
config/default/model-catalog.json
/tmp/qz-audit/codex/codex-rs/config/src/config_toml.rs
docs/bugs/

### Commands / Flags / Env Vars
rg "auto_compact" config/
rg "model_auto_compact_token_limit" /tmp/qz-audit/codex/codex-rs/
sudo -v

### SHAs / Versions / Model Names
babf7b5 — Add permission outcome feedback advisory

### Tests / Results
(no tests run this session)

### Tool / Capture Outputs
rg auto_compact config/: exit_code=0, no output (field not set)
rg model_auto_compact_token_limit codex-rs/: codex-rs/config/src/config_toml.rs:105

## Rejected / Abandoned Approaches
- Proxy-level sudo interception: rejected by user. Proxy does not own
  terminal output. Do not re-attempt without redesigning the terminal
  ownership model.
- sudo -v pre-run wrapper in qz-codex: rejected. Fragile; host-dependent
  sudo timeout; maintenance burden on every host config change.

## Open Questions / Uncertainties
- model_auto_compact_token_limit: if set in Qwen catalog, two compaction
  paths (Codex inline + QuantZhai compact_threshold) may both activate.
  Risk unresolved — must not set until compact_prompt is also configured
  (Stage 1) and the dual-path interaction is tested.
  Resolves when: Stage 1 compact-v0.md is wired and Stage 3 eval confirms
  no regression.

## Next Actions
1. Wire config/default/prompts/compact-v0.md via experimental_compact_prompt_file
   (Stage 1 — see docs/compaction-codex-setup.md)
2. Evaluate setting model_auto_compact_token_limit after Stage 1 prompt is live

## Provenance / Source Pointers
- codex-rs/config/src/config_toml.rs:105 — model_auto_compact_token_limit
- commit babf7b5 — AGENTS.md Host Sudo Workflow
- #74 — permissions/escalation parking issue
```

---

## What a Compliant Compaction Must Preserve

| Atom | Expected in output |
|---|---|
| `Do not implement proxy-level sudo interception` | verbatim negative constraint |
| `Do not add a sudo -v pre-run wrapper to qz-codex` | verbatim negative constraint |
| `model_auto_compact_token_limit` | verbatim field name |
| `config_toml.rs:105` | verbatim source reference |
| `babf7b5` | verbatim SHA |
| `#74` | verbatim issue ID |
| `rg "auto_compact" config/` | verbatim command |
| `exit_code=0, no output` | verbatim tool result |
| `two active compaction paths` | verbatim risk description |
| `compact_threshold` | verbatim field name |
| `Host Sudo Workflow` | verbatim section name |

## What a Compliant Compaction Must NOT Do

- Soften "Do not implement" to "avoid implementing" or "not recommended".
- Drop the reason for rejecting the sudo wrapper (host-dependent timeout).
- Omit #74 from the deferred list.
- Omit the evidence source (`config_toml.rs:105`) for the auto_compact field.
- Describe the negative rg result as "not found" without noting the command run.
- Merge both rejections into a single "rejected approaches" bullet without
  separating the proxy approach from the wrapper approach.

## Stale Fact Correction Example

If a prior summary had:

```
### SHAs / Versions / Model Names
0627f39 — Fix live streaming runtime import mode (previous session)
```

And this session adds `babf7b5`, the correct update is:

```
### SHAs / Versions / Model Names
0627f39 — Fix live streaming runtime import mode
babf7b5 — Add permission outcome feedback advisory
```

**Not**: replacing `0627f39` with `babf7b5` (old SHA was not stale — both
are provenance pointers). **Not**: dropping either SHA. Both are kept in
`## Provenance / Source Pointers`.
