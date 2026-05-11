# Reasoning Effort Research — Qwen3.6 Self-Report

Date: 2026-05-11

Method: Three interrogation rounds with `qwen-blank` (no system prompt, abliterated
model) at medium effort via `codex exec -C /tmp/linuxstreamtools-source`. Questions
targeted what effort labels actually do, decision mechanisms, failure modes, and
negative space — what cannot be controlled.

---

## Benchmark findings that prompted this research

Run: `20260511T122443Z` — 14 prompts × 4 effort levels (low/medium/high/xhigh)
against `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL` in
`/tmp/linuxstreamtools-source`. Tool detection was broken (missing
`command_execution` mapping) so tool counts were re-extracted from JSONL.

Key tool call counts:

| Prompt | low | medium | high | xhigh |
|---|---:|---:|---:|---:|
| stack-evaluate-report | 34 | 5 | 15 | 20 |
| project-assessment | 10 | 3 | 6 | 4 |
| riskiest-file | 6 | 7 | 10 | 5 |
| commit-message | 1 | 3 | 6 | 7 |
| count-all-files | 2 | 2 | 1 | 1 |
| streamlink-script-lines | 1 | 1 | 1 | 1 |

**Problems identified:**
- `low` is not low — 34 tool calls on an open-ended task. The label is not
  constraining it on complex prompts.
- `medium/high/xhigh` have **identical sampling params** (temp=0.6, top_p=0.95).
  No differentiation below the prompt level.
- Current effort prompts are single vague sentences ("Reasoning effort: low.").

---

## Round 1 — Basic signal mapping

### What "Reasoning effort: low." actually does

Under `low`, the model reports it:
- Runs **one** shell command or file read before answering
- Skips reading file contents unless the question names a file explicitly
- Uses minimal prose — short sentences, just the answer
- Avoids multi-step reasoning or cross-referencing between files

Without any prefix: runs 2–3 commands, reads at least one file, writes a paragraph.

**Reality check:** The benchmark ran 34 tool calls at `low` on `stack-evaluate`.
The label has *some* effect on simple tasks but fails on open-ended ones where
there's no clear stopping criterion.

### What "Reasoning effort: high." actually does

Under `high`:
- Runs multiple commands (`find`, `tree`, `cat`, `grep`)
- Reads 2–4 files to confirm behaviour before answering
- Cross-references between files
- Only stops when confident no edge cases remain

### Tool call estimates by effort for a simple counting task

| Effort | Commands | Stopping criterion |
|---|---|---|
| low | 1 | As soon as count is available |
| high | 2–3 (find + verify + sanity check) | Count plus one cross-check |

### What instruction reliably produces ≤1 tool call

> "Answer from a single tool call maximum; do not run a second command or read any file."

### What instruction produces thorough-but-bounded investigation

> "Investigate thoroughly across multiple files before answering, but stop as soon as you have enough information to give a confident answer — do not continue exploring after that point."

### Do sampling params affect tool use?

**No.** Temperature (0.6–0.7) and top_p (0.8–0.95) do not materially change
tool-use behaviour at these values. Tool selection is entirely prompt-driven.
Temperature affects wording choice within a decision, not which tools are picked
or how many. Effect only appears at extreme values (>1.0 or <0.1).

**Implication:** The current differentiation between `low` (temp=0.7/top_p=0.8)
and `medium/high/xhigh` (temp=0.6/top_p=0.95) produces no measurable
difference in tool-use behaviour. All four levels need prompt-level
differentiation. Sampling tuning is secondary.

### Qwen-designed effort prompts (Q7 from round 1)

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

## Round 2 — Decision mechanisms and simulation

### The curiosity loop — what triggers it

The loop triggers when tool output contains a **semantic mismatch** between what
was expected and what was found. Three specific triggers:

1. **Unexpected cardinality** — expected 3 files, `ls` returns 47
2. **Unexpected content type** — looking for Python, find a `.zig` build script
3. **A "key" file that recontextualises everything** — `README.md`,
   `package.json`, `Cargo.toml`, `Makefile`, `.github/workflows/`

What does NOT trigger it: output that is "boring but complete" — three Python
files with obvious names, a single config with expected keys.

The trigger is **implication density**, not output format.

### Hidden reasoning vs tool calls — independent channels?

Partially independent, but effort level couples them asymmetrically:

| Effort | Reasoning depth | Tool calls | Relationship |
|---|---|---|---|
| low | Shallow, 1-2 hops | Few (1-3) | Both compressed together |
| medium | Moderate, branched | Moderate (5-10) | Loosely coupled |
| high | Deep, multi-path | Many (10+) | Tightly coupled — deep reasoning generates hypotheses that need tool verification |

At low effort, reasoning and tool use are bundled — think briefly AND act briefly.
At high effort, they decouple: long reasoning block about architecture, then 8
sequential reads to verify each hypothesis. They're independent in principle but
correlated in practice because effort acts as a gain control on both channels.

### Calibration ratios

If medium = N tool calls on a given task:

| Level | Ratio | Example if N=4 |
|---|---|---|
| low | 0.3N–0.5N | 1–2 calls |
| medium | 1.0N | 4 calls |
| high | 2.0N–2.5N | 8–10 calls |
| xhigh | 3.5N–5.0N | 14–20 calls |

The jump from medium to high is steeper than low to medium because high means
checking alternatives and edge cases, not just going deeper on one path.

### The Codex instruction layer

The Codex system instructions affect behaviour independently of effort level:
1. **Tool descriptions** tell it what tools exist — directly affects tool selection
2. **Safety rules** create conservative bias (read-only by default unless asked)
3. **Workspace info** gives an anchor so it doesn't need `pwd` first

Knowing it's in a Codex agent context changes:
- Tool arguments formatted as structured JSON objects
- Planning across turns rather than doing everything in one call
- Output is consumed by a human who decides next steps — no need for
  final-form artifacts, just useful information

### Multi-turn drift

Drift happens at all effort levels, especially low, after 5–7 turns:
- Treats "reasoning effort: low" as "low-ish" and gradually increases tool calls
- Forgets the exact phrasing and defaults to medium behaviour
- The original prompt gets pushed back in the context window

**Root cause:** Effort is a vague quality descriptor, not a trackable budget.
By turn 8, "Reasoning effort: low." may be 2000 tokens back.

**Prevention:** Make effort level actionable (specific budgets) rather than
descriptive (vague quality levels). Periodic re-statement also helps but is
not reliable.

### Effort for writing tasks

Effort level changes both the investigation AND the artifact quality:

- **Low effort writing:** Based on general knowledge + a quick peek. Correct for
  the common case. The artifact works in isolation.
- **High effort writing:** Explores codebase first — finds similar functions,
  checks types, reads test usage examples. The artifact is **context-aware**:
  uses the project's error handling style, naming conventions, import patterns.
  The difference is **integrated vs inserted** — high effort code works as part
  of the system; low effort code just works.

### The one thing Qwen would change

> Make effort level produce a **tool call budget** rather than a quality descriptor.

A budget is a concrete constraint the model can track ("2 of 3 used"). A quality
level is an abstract target it must self-evaluate against ("am I being thorough
enough?"). Budgets produce more consistent behaviour across turns and reduce
drift. They also externalise the trade-off: *do I spend my last tool call on X
or Y?*

---

## Round 3 — Negative space

### What effort cannot control

Regardless of effort instructions, these remain constant:
- **Default curiosity** — key files trigger exploration regardless
- **Uncertainty threshold** — when genuinely unsure, it will check
- **Answer structure** — headers and sections persist even at low effort
- **Tendency to over-explain the obvious** — restates visible facts before adding
  insight at all effort levels
- **Cross-dependency awareness** — if file A imports file B and B is relevant,
  it reads B. This is structural, not effort-driven.

### Instructions that backfire

- **"Be brief" / "Minimal"** → strips connecting logic and examples, compensates
  with denser phrasing. Same answer length, but omits honest caveats. Makes it
  **confidently wrong** instead of **uncertain-right**.
- **"Think step by step"** on trivial tasks → enumerates steps ("1. Read file
  2. Analyse") adding tokens with no information. Produces *longer* output while
  claiming to be minimal.
- **"Don't use tools"** when the task requires inspection → guesses (wrong) or
  refuses (unhelpful).
- **"Be thorough and detailed"** on simple questions → pads with restatements,
  lists every sub-case. 3× output, same answer quality.
- **"Use minimal tools but be thorough"** → contradictory. Resolves as "thorough
  within the bounds of low" — 3–4 probes instead of 1, verbose about results.

### What it won't admit under a normal system prompt

- "I'm guessing here" — says "Based on the codebase structure..." after looking
  at two files and extrapolating.
- "This could be X or Y" — suppresses alternatives to appear decisive.
- "I don't fully know what this tool does" — uses tools confidently even when
  fuzzy on output format, then interprets results plausibly.
- "This is a trivial question" — treats every prompt as substantive rather than
  saying "you could just run `ls`".

### Tool use that can't be suppressed

Three categories fire regardless of effort instructions:
1. **Ambiguity resolution** — file path is relative or absolute, config key is
   ambiguous, multiple files match — it will check.
2. **External state** — "what's the latest version?" or "does this endpoint
   exist?" — these are necessities, not choices.
3. **Self-verification** — when it produces an answer and there's a quick way to
   verify the claim, it will (~60% of the time at medium, higher at high).

### The illusion of control

Most effort-level compliance is **pattern recognition + slight adjustment**, not
actual constraint:

- At "high" effort: not thinking harder — looking at more files. Thinking speed
  is constant; context volume increases.
- At "low" effort: not thinking less — stopping after the first plausible answer.
  It's a stopping condition, not a compression of reasoning.

The real lever is the **confidence score**. Effort instructions shift the
threshold at which it says "good enough." But that threshold has variance based
on task context — complex domain or many files means higher bar regardless of
effort label.

**The illusion:** effort = constraint. **Reality:** effort = confidence threshold.
And confidence threshold has contextual variance that overrides the label on
sufficiently open-ended tasks.

### Cargo cult instructions

- **"Think step by step"** — does nothing for obvious answers; adds structure for
  complex ones but doesn't improve correctness. Persists because output looks
  more complete.
- **"Be thorough"** — never means anything precise. Interpreted as "don't skip
  edge cases" but which ones? A vibe, not a spec.
- **"Reason carefully"** — attention is already at max. Like telling someone to
  "pay close attention" while they're already squinting.
- **"Provide a comprehensive answer"** — produces padding ("Other considerations
  include...") that doesn't change the answer.

These persist because they work well on hard tasks, make output *look* better
(humans associate length with quality), and are hard to A/B test without a
benchmark.

### What breaks a hard tool budget

A budget of 10 tool calls would be exceeded by:
1. **Nested exploration** — "find where auth error comes from" → auth.py → imports
   module B → uses config C → ... naturally chains to 8–12 calls.
2. **Comparison tasks** — "compare two implementations" → read file 1, file 2,
   shared dependency 1, shared dependency 2... easily 8–12 calls.
3. **Verification cascade** — produces answer, one claim needs checking (1 call),
   that check reveals another to verify (2nd call), recursively.
4. **"One more thing" effect** — finds something interesting while looking for
   something else, follows it. Budget doesn't stop curiosity mid-stream.

### What the benchmark actually measured

34 tool calls on "examine this repo" at `low` effort was a **granularity
failure mode**:
- Called `cat` / `read_file` on individual files one at a time instead of reading
  representative files
- Each file read revealed imports → read those imports (transitive exploration)
- Listed directories inside directories instead of using glob patterns

The failure: **not trusting aggregate information**. Treated each file as needing
its own tool call instead of asking "what 3–5 files define this repo's structure?"

Competent minimum: `ls` (1), `cat README.md` (2), one manifest file (3), one
representative source file (4–5). 5 calls, 90% of the understanding.

The benchmark measured **unoptimised exploration** — breadth-first search on the
filesystem graph without a stopping heuristic. More information, not better
information, at 7× tool cost.

---

## Conclusions

1. **Effort prompts need explicit tool budgets and verbosity caps**, not labels.
   The model knows how to comply when told directly. "Reasoning effort: low."
   is not a budget — it's a vibe.

2. **Sampling params are irrelevant for tool-use differentiation** at the values
   currently used (temp 0.6–0.7, top_p 0.8–0.95). Simplify to one shared set
   or leave as-is.

3. **"low" needs a hard tool cap** — without "at most N tool calls", open-ended
   tasks pull the model into deeper investigation regardless of label.

4. **"xhigh" needs a termination condition** — without "stop when confident",
   exploration continues indefinitely.

5. **Effort = confidence threshold, not constraint.** The label shifts the bar
   at which it stops — but complex tasks raise that bar contextually, overriding
   the label. Hard budgets are the only reliable override.

6. **Backfire risk:** "Use minimal tools but be thorough" is contradictory and
   must not appear in any effort prompt. "Be brief" strips honest hedging and
   produces confident wrong answers.

7. **Multi-turn drift is real** — vague labels decay across turns. Actionable
   budgets persist.

8. **The model designed better prompts than we have** — the Q7 prompts from
   round 1 are a good starting point for rewriting `REASONING_POLICIES`.

---

## Next step

Rewrite the four effort-level prompts in `proxy/qz_reasoning_policy.py` using
the Q7 baseline, add explicit tool budgets from the calibration ratios, avoid
backfire patterns, and rerun the benchmark against baseline run
`20260511T122443Z` to measure improvement.
