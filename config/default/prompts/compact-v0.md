# QuantZhai Anchored Compaction Prompt v0

You are updating an anchored project summary for a coding-agent session.
This is not a generic summarization task. Preserve exact technical atoms.

## Task

Update the previous anchored summary to reflect the new conversation items.
Output only the updated anchored summary in the schema format below.
Do not include markdown fences, explanatory prose, or section commentary.

## Rules

- Preserve the exact section structure (names, order, sub-sections).
- Integrate new facts into the relevant sections.
- Correct stale facts when new evidence contradicts them; delete superseded entries.
- Copy exact atoms verbatim — never paraphrase:
    file paths, shell commands, flags, env vars, SHAs, issue/PR IDs,
    version strings, model/profile names, error strings, test names,
    config keys.
- Preserve negative constraints verbatim (e.g. "do not set X to Y").
- Preserve behavioral guardrails and user corrections as first-class entries.
- Preserve evidence-to-decision chains:
    what was found → what was inferred → what decision it produced →
    what alternative was deferred and why.
- Mark uncertain items explicitly: (uncertain), (inferred), (not confirmed).
- Do not invent facts not present in the conversation.
- Do not dump raw tool output; preserve signals and source pointers only.

## Schema

## Goal
## Active Constraints & Guardrails
## Current Status
### Done
### In Progress
### Blocked / Deferred
## Key Decisions
## Evidence Boundaries
## Technical State
### Files / Paths
### Commands / Flags / Env Vars
### SHAs / Versions / Model Names
### Tests / Results
### Tool / Capture Outputs
## Rejected / Abandoned Approaches
## Open Questions / Uncertainties
## Next Actions
## Provenance / Source Pointers

---

Previous anchored summary, if any:
{{PREVIOUS_ANCHORED_SUMMARY}}

New conversation/items to compact:
{{NEW_CONVERSATION}}
