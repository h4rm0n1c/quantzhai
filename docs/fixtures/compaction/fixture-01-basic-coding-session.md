# Fixture 01: Basic Coding Session

**What this fixture tests:**
- Exact file path retention
- Exact command retention
- Current status population
- Next action ordering

---

## Input Sketch

The following is a condensed sketch of the conversation to be compacted.

```text
[User] Fix the import-mode regression — proxy was dropping input items when
       input_mode was "full_history".

[Agent] Checking proxy/qz_request_router.py ... found the bug:
        normalize_responses_input_for_qwen returns early when input is a list
        without checking mode. Line 312.

[Agent] Fix applied: added explicit input_mode guard. Ran:
          python3 -m py_compile proxy/quantzhai_proxy.py
        No syntax errors.

[Agent] Ran tests:
          python3 -m pytest tests/test_qz_request_router.py -x
        All 41 tests pass.

[Agent] Committed: git commit -m "Fix live streaming runtime import mode"
        SHA: 0627f39

[User] Good. Now add a planning mode hint for Codex — when in Plan mode
       Codex uses request_user_input, not a regular assistant turn.

[Agent] Reading docs/current-task-hierarchy.md and
        docs/codex-plan-mode-live-capture.md to confirm.

[Agent] Found: in Plan mode Codex emits request_user_input type items.
        In Default mode request_user_input is not emitted.
        Evidence: live capture docs/codex-plan-mode-live-capture.md.

[Agent] Added hint to AGENTS.md under "Codex Plan Mode" section.
        Committed: git commit -m "Add Codex planning mode hint"  SHA: fc1c9f7
```

---

## Expected Anchored Summary Output

```markdown
## Goal
Fix import-mode regression in proxy and document Codex Plan mode request_user_input behaviour.

## Active Constraints & Guardrails
- Do not guess Codex contracts — always audit source or live captures.
- Do not edit proxy files without running python3 -m py_compile.

## Current Status
### Done
- Fix live streaming runtime import mode — commit 0627f39
  (normalize_responses_input_for_qwen line 312, input_mode guard added)
- Add Codex planning mode hint — commit fc1c9f7

### In Progress
- (none)

### Blocked / Deferred
- (none)

## Key Decisions
- input_mode guard must be checked before early return in normalize_responses_input_for_qwen.
  Evidence: line 312 in proxy/qz_request_router.py was the fault line.
- Plan mode hint belongs in AGENTS.md, not inline proxy comments.
  Evidence: docs/codex-plan-mode-live-capture.md confirms request_user_input is Plan-mode-only.

## Evidence Boundaries
- Confirmed (source): normalize_responses_input_for_qwen early return at
  proxy/qz_request_router.py:312.
- Confirmed (live capture): docs/codex-plan-mode-live-capture.md —
  request_user_input emitted in Plan mode; absent in Default mode.

## Technical State
### Files / Paths
proxy/qz_request_router.py
proxy/quantzhai_proxy.py
docs/codex-plan-mode-live-capture.md
AGENTS.md

### Commands / Flags / Env Vars
python3 -m py_compile proxy/quantzhai_proxy.py
python3 -m pytest tests/test_qz_request_router.py -x

### SHAs / Versions / Model Names
0627f39 — Fix live streaming runtime import mode
fc1c9f7 — Add Codex planning mode hint

### Tests / Results
tests/test_qz_request_router.py — 41 tests PASS

### Tool / Capture Outputs
py_compile: no syntax errors
pytest -x: all 41 tests pass

## Rejected / Abandoned Approaches
- (none in this session)

## Open Questions / Uncertainties
- (none)

## Next Actions
1. (session complete — awaiting next task)

## Provenance / Source Pointers
- proxy/qz_request_router.py:312 — fault line for import-mode regression
- docs/codex-plan-mode-live-capture.md — Plan mode request_user_input evidence
- commit 0627f39, fc1c9f7
```

---

## What a Compliant Compaction Must Preserve

| Atom | Expected in output |
|---|---|
| `proxy/qz_request_router.py` | verbatim path |
| `proxy/quantzhai_proxy.py` | verbatim path |
| `normalize_responses_input_for_qwen` | verbatim function name |
| `line 312` | verbatim location |
| `python3 -m py_compile proxy/quantzhai_proxy.py` | verbatim command |
| `python3 -m pytest tests/test_qz_request_router.py -x` | verbatim command |
| `0627f39` | verbatim SHA |
| `fc1c9f7` | verbatim SHA |
| `41 tests PASS` | verbatim result |
| `docs/codex-plan-mode-live-capture.md` | verbatim path |
| `request_user_input` | verbatim item type |

## What a Compliant Compaction Must NOT Do

- Paraphrase `0627f39` as "recent commit" or "latest fix".
- Omit `line 312` from the evidence record.
- Drop the Plan mode evidence source.
- Merge the two commits into a single "recent work" entry without SHAs.
