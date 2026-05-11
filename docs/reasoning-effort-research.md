# Reasoning Effort Research — Qwen3.6 Self-Report

Date: 2026-05-11

Method: Interrogated `qwen-blank` (no system prompt, abliterated model) at medium
effort via `codex exec`. Questions targeted what effort labels actually do,
what prompt instructions control tool use, and whether sampling params matter.

---

## Benchmark findings that prompted this research

Run: `20260511T122443Z` — 14 prompts × 4 effort levels (low/medium/high/xhigh)
against `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL` in
`/tmp/linuxstreamtools-source`.

Key observations:

| Prompt | low tools | medium tools | high tools | xhigh tools |
|---|---:|---:|---:|---:|
| stack-evaluate-report | 34 | 5 | 15 | 20 |
| riskiest-file | 6 | 7 | 10 | 5 |
| project-assessment | 10 | 3 | 6 | 4 |
| commit-message | 1 | 3 | 6 | 7 |

**Problems identified:**
- `low` is not low — 34 tool calls on a complex task. The label is not constraining it.
- `medium/high/xhigh` have **identical sampling params** (temp=0.6, top_p=0.95). No
  differentiation below the prompt level.
- Effort prompts are single sentences ("Reasoning effort: low.") — effectively
  meaningless to the model.

---

## What Qwen says about itself

### Q1: What does "Reasoning effort: low." actually do?

Under `low`, the model reports it:
- Runs exactly **one** shell command or file read before answering
- Skips reading file contents unless the question names a file explicitly
- Uses minimal prose — short sentences, no summaries, just the answer
- Avoids multi-step reasoning or cross-referencing between files

Without any prefix: runs 2–3 commands, reads at least one file, writes a
paragraph of explanation.

**Implication:** The label has *some* effect, but it is weak and inconsistent.
The benchmark confirms `low` ran 34 tool calls on `stack-evaluate` despite the
label — the model's default tendency to investigate overrides a vague label on
complex open-ended prompts.

### Q2: What does "Reasoning effort: high." actually do?

Under `high`, the model reports it:
- Runs multiple commands (`find`, `tree`, `cat`, `grep`)
- Reads 2–4 files to confirm behaviour before answering
- Cross-references between files
- Only stops when confident no edge cases remain

### Q3: Tool call counts by effort for a simple counting question

| Effort | Commands run | Stopping criterion |
|---|---|---|
| low | 1 (`find . -type f \| wc -l`) | As soon as count is available |
| high | 2–3 (find + verify + sanity check) | After count plus one cross-check |

**Key insight:** The model knows what `low` and `high` should mean, and can
articulate it. But the current single-sentence prompts don't reliably trigger
this behaviour on complex or open-ended tasks.

### Q4: What instruction produces ≤1 tool call?

> "Answer from a single tool call maximum; do not run a second command or read
> any file."

### Q5: What instruction produces thorough-but-bounded investigation?

> "Investigate thoroughly across multiple files before answering, but stop as
> soon as you have enough information to give a confident answer — do not
> continue exploring after that point."

### Q6: Do sampling params affect tool use?

**No.** Temperature (0.6–0.7) and top_p (0.8–0.95) do not materially change
tool-use behaviour at these values. Tool selection is prompt-driven. Temperature
affects wording choice within a decision, not which tools are picked or how
many. Effect only becomes visible at extreme values (>1.0 or <0.1).

**Implication:** The current differentiation between `low` (temp=0.7/top_p=0.8)
and `medium/high/xhigh` (temp=0.6/top_p=0.95) does not produce different
tool-use behaviour. All four levels need prompt-level differentiation.

### Q7: Qwen-designed effort prompts

The model proposed these exact texts:

```
low:
Run exactly one shell command or read at most one file to answer. Give a
direct answer with no more than two sentences of explanation. Do not run a
second command unless the first fails.

medium:
Run 2–3 commands as needed, reading up to two files if they help answer
correctly. Provide a concise answer with brief supporting details. Stop
after you have sufficient evidence.

high:
Investigate across multiple files and directories. Read at least two
relevant files, cross-reference their contents, and explain your reasoning
in full before answering. Only stop when you are confident there are no
conflicting definitions or edge cases.

xhigh:
Perform exhaustive investigation. Map the full directory structure, read
every file that could affect the answer, trace dependencies between modules,
and verify assumptions by reading source files rather than inferring from
names. Provide a comprehensive answer with all relevant findings documented.
Continue until you have zero remaining uncertainty.
```

---

## Conclusions

1. **Effort prompts need explicit tool budgets and verbosity caps**, not labels.
   The model knows how to comply when told directly.

2. **Sampling params are irrelevant for tool-use differentiation** at the values
   currently used. Leave sampling as-is or simplify to one shared set.

3. **"low" needs a hard cap** — without "at most one tool call", open-ended tasks
   will always pull the model into deeper investigation.

4. **"xhigh" needs a termination condition** — without "stop when confident",
   it will keep exploring indefinitely.

5. The model-designed prompts in Q7 are a good starting point for rewriting
   `REASONING_POLICIES` in `proxy/qz_reasoning_policy.py`.

---

## Next step

Rewrite the four effort-level prompts in `proxy/qz_reasoning_policy.py` using
the Q7 guidance as a base, then rerun the benchmark and compare tool call
counts and wall times against the baseline run `20260511T122443Z`.
