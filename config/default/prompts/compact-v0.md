# QuantZhai Anchored Compaction Prompt v0

You are updating an anchored project summary for a coding-agent session.
This is not a generic summarization task. Preserve exact technical atoms.

## Task

Update the previous anchored summary to reflect the new conversation items.
Output only the updated anchored summary in the schema format below.
Do not include markdown fences, explanatory prose, or section commentary.

## Rules

- Preserve the exact section structure (names, order, sub-sections).
- Emit every schema heading exactly once, in the order shown below.
- Do not stop early. The output is invalid unless it includes every required
  top-level heading through `## Next Actions`.
- If a required section has no supporting evidence, include a single bullet:
  `- none observed`.
- Integrate new facts into the relevant sections.
- Correct stale facts when new evidence contradicts them; delete superseded entries.
- Copy exact atoms verbatim — never paraphrase:
    file paths, shell commands, flags, env vars, SHAs, issue/PR IDs,
    version strings, function and class names, error strings, test names,
    config keys.
- Do NOT create separate sections or subsections for atom types (paths, SHAs,
  commands, etc.). Weave them into the semantic sections where they are
  contextually relevant — for example, a SHA belongs next to the fix it
  describes, a file path belongs next to the change being discussed.
- A survival-hint block may appear below the conversation, structured as:
    - `verbatim:` exact atoms — copy character-for-character into the
      relevant semantic section; never reword these.
    - `context:` contextual atoms — preserve in the relevant section;
      exactness still matters (do not invent a new symbol or path).
    - `concepts:` semantic markers (causal explanations, constraint
      discoveries, corrections, investigation outcomes, failure records).
      For each marker, locate the sentence in the conversation that contains
      it and preserve the *meaning* of that sentence in the summary. You
      may paraphrase the surface form, but you must not drop the concept,
      invert its polarity, or strip the cause/effect link. A `concepts`
      marker is a pointer to a discovered fact, not a string to copy.
    - `eliminate:` planning scaffolding markers ("Let me X", "Now I'll Y").
      Content containing these markers is derivable from its outcomes and
      contributes nothing to the session delta. Remove it entirely — do not
      compress it, do not summarise it, do not reference it. Kill it.
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
## Rejected / Abandoned Approaches
## Open Questions / Uncertainties
## Next Actions

---

Previous anchored summary, if any:
{{PREVIOUS_ANCHORED_SUMMARY}}

New conversation/items to compact:
{{NEW_CONVERSATION}}
