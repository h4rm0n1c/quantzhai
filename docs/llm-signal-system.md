# LLM Signal System

Date: 2026-05-11

Status: early thinking. This document is exploratory, not a binding spec.
The signal system is being felt out as we implement pieces of it. Update
this as understanding develops, not as a retrospective checklist.

---

## The idea

A local LLM doing agentic work is a feedback control system. It takes
actions, observes results, updates its belief, and acts again. The quality
of its decisions depends entirely on the quality of the observations it
receives.

QuantZhai sits in the middle of that loop. It sees everything: the model's
output, the tool calls it makes, what Codex does with them, what fails, what
succeeds, how full the context is, how many hops remain. The model sees
none of that unless the proxy explicitly surfaces it.

Hosted LLMs get this from the platform. Local proxies typically throw it
away. The signal system is about closing that gap: making the proxy an
active participant in the model's feedback loop rather than a passive relay
that silently discards information.

This is not QZSTATE. QZSTATE is a separate experiment with a different
purpose and is not part of this system.

---

## Two categories of signal

**Self-management signals** — constraints the model needs to know so it can
regulate its own behaviour within the local stack's actual limits.

**Quality signals** — information about the outcome of its actions so it
can self-correct when things go wrong.

These are different in character. Self-management signals are proactive:
inject them before the model acts so it acts appropriately. Quality signals
are reactive: inject them after an action so the model knows what happened
and can adjust.

---

## What exists today

### Coercion system (quality signals, tool actions)

Implemented. When the model emits a malformed tool call the proxy either:
- Recovers the arguments and proceeds (zero extra hops)
- Injects a specific error result upstream with a precise reason so the
  model can retry with the right shape

This applies to any tool through the registry's `coerce()` interface.
Previously the proxy silently dropped malformed calls. Now the model gets
actionable feedback. See `docs/tool-coercion-design.md`.

### Informative compaction placeholders (quality signals, history)

Implemented. When old tool outputs get microcompacted, the placeholder now
carries the success/failure signal and first-line error context rather than
a generic "payload dropped" message. The model retains essential history
even after compaction.

---

## What we're thinking about next

These are directions, not commitments. Priority and shape may change as we
implement and observe.

### Tool use feedback — loop detection and dedup signals

**Why this is needed:** Benchmark forensics showed that looping (the model
reading the same files 3 times, spending 36 tool calls on a task that needed 5)
happens entirely in the tool call layer — outside the reasoning channel and
therefore outside the reach of the reasoning token budget. Per-tool-call
reasoning blocks are tiny (28-213 tokens each). The budget controls reasoning
depth per hop but cannot prevent the model from continuing to make redundant
tool calls after each `</think>`.

The fix requires feedback from the proxy about what has already been done.

**Option 1 — Dedup signal on repeated reads (most surgical)**

Proxy tracks file paths accessed in the current turn. When the model reads the
same path again, inject into the tool result alongside the file content:

> *"Note: you already read this file earlier in this turn."*

Fires exactly when the redundant behaviour occurs. The model sees it in the
tool result and self-corrects without needing a separate signal channel. This
directly addresses the benchmark failure: `README.md` × 3, `streamlink_3.sh`
× 3, `streamlinkbgm/README.md` × 3 in a single session.

**Option 2 — Tool call counter signal**

Proxy injects an ephemeral message after N tool calls in a turn:

> *"You have made N tool calls this turn. Consider whether you have enough
> information to answer the question."*

Similar to hop budget but scoped to tool calls within a single turn rather
than conversation hops. Less surgical than option 1 but simpler to implement
and catches any excessive tool use, not just file reads.

**Option 3 — Diminishing returns signal**

Proxy hashes consecutive tool outputs and signals when a new result overlaps
significantly with a previous one:

> *"This result is similar to content you already retrieved."*

More complex to implement reliably. Dedup by path (option 1) is more precise
for the file-read case.

**Recommended implementation order:**
1. Option 1 first — path dedup on shell/read tool calls, injected into the
   tool result. Low complexity, high signal-to-noise, directly targets the
   observed failure mode.
2. Option 2 as a backstop — fires when total tool calls exceed a threshold
   regardless of dedup, catching loops that don't involve file re-reads.
3. Option 3 deferred — only if 1+2 are insufficient.

**Implementation notes:**
- Per-turn state only. Reset at turn boundary, not carried across hops.
- Inject into the tool result text, not as a separate message turn. Keeps
  it in the tool result channel where the model already expects feedback.
- Same pattern as coercion error injection — the proxy augments the tool
  result rather than adding a new message.
- The path tracker needs to normalise paths (resolve `./`, `../`, absolute
  vs relative) to avoid false negatives.
- This is distinct from the compaction microcompaction system which handles
  old tool outputs across turns — this is intra-turn dedup only.

### Hop budget (self-management)

The model doesn't know how many continuation hops remain in the current
turn. It behaves identically on hop 1 and hop 5 of 6. When the proxy hits
the limit it emits a fallback message the model never asked for.

If the model knew the remaining budget it could self-regulate: fewer tool
calls when budget is tight, more direct answers, earlier summarisation.

**Open questions before implementing:**
- How is the signal injected? As a line appended to system instructions
  each hop? As part of a function_call_output-style context message? As
  metadata the model can reference?
- What hop counts are meaningful? Does the model respond differently at
  "5 remaining" vs "1 remaining" or does it only care about "low budget"?
- Does this interact with the reasoning budget?

### Context pressure (self-management)

The model doesn't know the context window is filling up. It builds long
responses and chains long reasoning without knowing compaction is imminent
or that prior history has already been dropped.

If the model knew context was at 80% it could choose to summarise, be more
concise, prioritise the most important remaining work, or wrap up a task
before history gets compacted in ways that harm continuity.

**Open questions:**
- What does "context pressure" mean precisely? Tokens used / configured
  context? Or the more complex measure of what the model will actually see
  after compaction?
- Per-request token count is available from usage in the Responses API.
  Accumulating it across hops gives an estimate.
- Is a percentage threshold the right signal, or a raw token count, or
  a qualitative level (low / medium / high / critical)?

### Backend errors vs task errors (quality signals)

When llama.cpp returns a 500 or times out, the stream ends. The model can't
tell the difference between "my tool call was malformed" and "the backend
had a transient error, retry as-is." These require different responses.

Currently a backend failure looks identical to a task failure from the
model's perspective.

### Search result quality (quality signals)

web_search returns results ranked by SearXNG. The model can't tell whether
a result is a primary source, a mirror, or low-signal noise. It weighs all
evidence equally.

A per-result quality hint (primary, mirror, low_signal) would let the model
reason about evidence quality and decide when to open a page vs trust a
snippet.

### Tool call provenance (quality signals)

A `function_call_output` error in the model's history could be:
- A proxy-injected coercion error (model's arguments were malformed)
- A Codex execution failure (arguments were valid, execution failed)
- A proxy-local tool runtime error (web search returned nothing)

The model can't distinguish these from the output alone. Different
provenances warrant different responses: retry with fixed arguments vs
retry as-is vs try a different approach entirely.

---

## What we are deliberately not doing

- **QZSTATE** — separate experiment, out of scope for this system.
- **Prompt stuffing** — injecting large chunks of state into the system
  prompt at every turn. Signals should be compact, targeted, and only
  present when they carry information the model can actually act on.
- **Signals the model can't use** — adding fields to responses just
  because we can. Each signal needs a plausible response from the model
  before it's worth implementing.
- **Mandatory signals** — all of this should degrade gracefully if not
  implemented. The model should still function (less optimally) without
  any of these signals.

---

## Signal format — an open question

How a signal is injected matters as much as what it says. Candidates:

- **System prompt addition** — visible to the model on every turn; risks
  becoming background noise the model learns to ignore; stale between turns
- **In-turn user/context message** — visible, fresh, clear; adds a turn to
  the conversation; may be more salient than prompt text
- **function_call_output-style message** — consistent with the coercion
  system; fits the model's existing "tool result" expectation; could become
  the standard channel for all proxy-injected signals
- **XML/structured tags** — models vary widely on whether they treat tags
  as special or just as text; Qwen's training on these is unknown
- **Instruction annotation** — appended to the existing instructions block;
  compact but may be overshadowed by task instructions

None of these are obviously right. The format question is empirically
answerable with the fuzz infrastructure: implement hop budget in two or
three different injection formats, run the same task battery, observe
which format produces the most consistent model behaviour.

### Research direction (inform, don't bind)

Worth a pass over what others have done before implementing:

- **ReAct (Reason + Act)** — how the reasoning channel interacts with
  action signals; relevant to whether signals should target reasoning or
  the answer phase
- **LangChain/LangGraph** — how they surface agent state to the model;
  their approach to self-management prompts
- **AutoGen** — self-reflection and retry patterns; how they handle tool
  failures
- **Anthropic tool-use research** — published work on what makes tool-use
  feedback effective
- **Qwen documentation and eval papers** — whether there is anything specific
  about how Qwen3.6 (MoE) responds to meta-instructions vs dense models
- **OpenClaude** — an open-source proxy tackling similar problems (making a
  local proxy support capable agentic behaviour without a hosted platform).
  Worth reading specifically for their signal injection and feedback patterns.
  Treat as a cautionary reference rather than a blueprint — they have a known
  path traversal CVE (CVE-2026-35570) and their safety decisions may not be
  right for this stack. Read the ideas, not the code.

Weight this lightly. If prior art says "use system prompt" but empirical
testing with Qwen shows in-turn messages produce better self-regulation,
use in-turn messages. The model's actual behaviour is the ground truth.

### Qwen-specific considerations

Qwen3.6-35B-A3B is a MoE architecture with a separate reasoning channel.
Both of these are relevant to signal design:

- The reasoning channel sees the world before the answer does. A signal
  visible in the reasoning phase may produce more deliberate self-regulation
  than one that arrives after reasoning has already committed to a path.
- MoE models can behave differently from dense models on meta-instructions.
  The degree to which Qwen follows "you have 3 hops remaining" literally vs
  treats it as background information is unknown without testing.
- Qwen has been trained on agentic tasks but the specific format of its
  training data for meta-signals is not public. This makes empirical testing
  more important than research review.

---

## Design principles (current thinking, subject to change)

**Reactive over proactive where possible.** Quality signals in response
to events are cheaper and less noisy than proactive signals injected
constantly.

**Specific over generic.** "apply_patch: missing 'diff' for create_file"
is useful. "tool call failed" is not.

**Compact.** Signals should be a sentence, not a paragraph. The signal
competes with task content for context budget.

**Injected at the right point in the loop.** Self-management signals
before the model acts. Quality signals as tool results, not as system
prompt additions.

**No QZSTATE.** See above.

---

---

## Reasoning budget as the primary effort control — next implementation target

### The insight

`--reasoning-budget N` in TurboQuant/llama.cpp is a sampler-level constraint
on the `<think>...</think>` reasoning block. When N tokens are exhausted:

1. The budget message is injected into the reasoning channel
2. The `</think>` closing tag is force-fed token by token
3. Generation continues normally for the answer

This is mechanically enforced — the model cannot think past the budget
regardless of presence_penalty, prompt wording, or task complexity. This
makes it the right primary lever for reasoning effort control, replacing the
current prompt-text approach which the model can ignore on open-ended tasks.

See `common/reasoning-budget.cpp` in the TurboQuant repo for the full state
machine: `IDLE → COUNTING → WAITING_UTF8 → FORCING → DONE`. It does not
hard-stop — it force-feeds the closing sequence and then passes through.
The earlier hard-stop bug was a different mechanism.

### Mapping to effort levels

```
low    →  small budget  (fast, shallow reasoning, quick answer)
medium →  moderate budget
high   →  large budget  (deep reasoning, thorough answer)
xhigh  →  -1 (unlimited, reason as long as needed)
```

Exact token counts are TBD — see open questions below.

### Implementation shape

`--reasoning-budget` is a server launch parameter, not per-request. The
proxy already has a `restart_required` mechanism for context window changes:

```python
restart_required = (selected_context_length != backend_context_length)
```

Reasoning budget fits the same shape:

```python
restart_required = (
    selected_context_length != backend_context_length
    or selected_reasoning_budget != backend_reasoning_budget()
)
```

`backend_reasoning_budget()` already exists in `proxy/qz_model_router.py`.
`selected_reasoning_budget` would be derived from the effort level mapping
and stored in `REASONING_POLICIES` alongside sampling params. The proxy
triggers a backend restart when effort level changes, exactly as it does
when context window changes.

`QZ_REASONING_BUDGET` and `QZ_REASONING_BUDGET_MESSAGE` are already wired
in `qz-env` and `qz-up`. The proxy just needs to set them before restart.

### Open questions — token budgets

The right budget per effort level is empirical. Key questions:

1. **What is kuato-DPO's typical reasoning token count by task type?**
   Snap questions (factual) vs local inspection vs complex reasoning vs
   open-ended exploration each have different natural reasoning depths.
   Interrogate qwen-blank to get self-reported estimates, then validate
   against captured reasoning token counts from benchmark runs.

2. **What is the minimum reasoning budget for a coherent answer?**
   Too low and the model gets cut off mid-thought and produces a confused
   answer. There's a floor below which quality degrades sharply. Need to
   find it empirically.

3. **What budget triggers the looping behaviour?**
   The benchmark showed kuato-DPO loops on open-ended tasks. Is this
   caused by very long reasoning chains (suggesting a high budget would
   help), or is it reasoning-then-tool-call cycles that the budget
   doesn't affect (since the budget only covers the reasoning block)?

4. **Does the budget message wording matter — and what is it actually for?**
   Two fundamentally different approaches:

   - **Directive** ("I have reasoned long enough. Let me now produce my
     final answer.") — forces conclusion regardless of whether reasoning
     was productive. Risks cutting off good reasoning that was still
     making progress.

   - **Loop-detection nudge** ("You may be revisiting information already
     gathered. Consolidate what you know and form your answer.") — a
     metacognitive signal that lets the model self-evaluate. If it was
     looping, it self-corrects and produces a better summary. If it was
     making progress, it can still conclude from wherever it is. The
     forced `</think>` close is the mechanical guarantee regardless.

   The nudge approach is likely better for answer quality at the cutoff
   because it shapes the model's reasoning about its own state rather than
   just commanding it to stop. To investigate: does the self-awareness
   framing produce better answers on tasks that genuinely warranted deep
   reasoning vs tasks where the model was looping?

5. **Proxy-managed budget retry — tier-2 feature**

   When the reasoning budget fires, the proxy sees the budget message text
   appear in the `reasoning_text` SSE delta stream before the forced
   `</think>`. This is a detectable event. The proxy could use it to
   trigger an intelligent retry rather than forwarding a potentially
   degraded answer to Codex.

   **Mechanism:**
   1. Proxy accumulates reasoning text deltas as they stream from llama.cpp
   2. Proxy detects the budget message string in the reasoning stream —
      this signals the budget fired
   3. Proxy evaluates the answer that follows. If degraded (too short,
      incoherent, or starts with hedging like "I wasn't able to..."):
   4. Proxy discards the response
   5. Proxy extracts key findings from the accumulated reasoning content
   6. Proxy injects a synthetic follow-up context: *"Your reasoning budget
      was reached. Based on your analysis so far: [extracted key points].
      Now answer the original question directly."*
   7. Proxy re-submits to llama.cpp — model answers from the pre-digested
      summary without needing to re-explore

   The model gets a second shot with its own reasoning distilled for it.
   No re-exploration needed because the context now contains the key
   findings, not the original open-ended prompt.

   This is the same intervention pattern the proxy already uses for tool
   call coercion and error injection — intercept, reshape, re-inject.

   **Circuit breakers required:**
   - Retry at most once per request — never chain retries
   - Do not retry if the answer after the forced close was already coherent
     (detect by length, structure, or absence of hedging markers)
   - Do not retry on snap/factual prompts where the budget firing indicates
     over-reasoning on a simple task rather than a productive loop

   **Open questions for implementation:**
   - How to reliably extract useful signal from a partial reasoning chain
     (mid-thought rather than a natural summary)
   - What constitutes a "degraded" answer reliably enough to trigger retry
   - Whether the synthetic context should include all extracted reasoning
     or a compressed summary
   - Latency budget: retry adds one full generation hop; acceptable for
     high/xhigh effort, probably not for low/medium

   **Implementation order:** implement basic budget+message first, validate
   that the forced close produces acceptable answers on its own, then add
   proxy-managed retry as a follow-on once the base mechanism is proven.

6. **Is the budget per-turn or per-session?**
   If a Codex session has 10 hops at `high` effort, does each hop get
   its own full budget, or does the budget need to account for cumulative
   reasoning across hops? The sampler resets per generation, so each hop
   gets the full budget — but the effective reasoning depth per hop may
   need to be lower than single-turn reasoning depth.

### Empirical data — actual reasoning token counts (kuato-DPO, pp=0.5)

Measured from benchmark `kuato-pp05` events.jsonl (reasoning item text length
÷ 4 for token estimate):

| Task | Profile | Tool calls | Est reasoning tokens |
|---|---|---:|---:|
| greeting-latch | medium | 0 | 25 |
| greeting-latch | high | 0 | 23 |
| project-assessment | medium | 4 | 358 |
| project-assessment | high | 8 | 500 |
| riskiest-file | medium | 6 | 1453 |
| riskiest-file | high | 6 | 615 |
| commit-message | medium | 5 | 572 |
| commit-message | high | 3 | 395 |

Looping cases from `kuato-baseline` (per-tool-call reasoning):

| Task | Profile | Tool calls | Total reasoning tokens | Per-call tokens |
|---|---|---:|---:|---:|
| stack-evaluate | low | 25 | 2744 | 109 |
| stack-evaluate | medium | 36 | 2023 | 56 |
| stack-evaluate | high | 26 | 1472 | 56 |
| riskiest-file | medium | 13 | 2160 | 166 |

**Key finding:** Looping is entirely in tool calls, not reasoning. Per-call
reasoning is tiny (28-213 tokens). Total reasoning for 36-tool-call sessions
is only ~2000 tokens. A reasoning budget cannot stop the loop — it can only
control reasoning depth per call.

**Model self-report vs reality:** The model overestimates its own reasoning
depth by 3-5x. Actual peak observed is ~1500 tokens for the most complex
non-looping tasks.

**Calibrated budget targets:**
```
low    →  500 tokens   (snap tasks peak at 25, inspection at ~400)
medium →  2000 tokens  (covers all observed clean completions with headroom)
high   →  4000 tokens  (2x medium, headroom for genuinely deep reasoning)
xhigh  →  -1           (unlimited)
```

### Investigation plan

Remaining before implementing:
1. Confirm budget message wording — the combined form is likely best:
   "You may be revisiting information. Consolidate your findings and
   produce your final answer."
2. Run targeted tests at 500/2000/4000 token budgets on riskiest-file
   and project-assessment to validate cutoff quality
3. Implement tool use dedup signal (option 1 above) in parallel — the
   loop fix requires both budget and dedup to be complete

## Relationship to other docs

- `docs/tool-coercion-design.md` — the coercion system, the first concrete
  implementation of quality signals for tool calls.
- `docs/compaction-bridge-plan.md` — compaction research needed before
  context pressure signals can be designed properly.
- `docs/conversation-history-audit-plan.md` — reasoning channel and
  tool history filter questions that may surface more signal gaps.
- `proxy/qz_proxy_tools.py` — the coercion dispatch point; future signals
  may plug in here or into the stream runtime.
