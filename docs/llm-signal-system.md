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

## Relationship to other docs

- `docs/tool-coercion-design.md` — the coercion system, the first concrete
  implementation of quality signals for tool calls.
- `docs/compaction-bridge-plan.md` — compaction research needed before
  context pressure signals can be designed properly.
- `docs/conversation-history-audit-plan.md` — reasoning channel and
  tool history filter questions that may surface more signal gaps.
- `proxy/qz_proxy_tools.py` — the coercion dispatch point; future signals
  may plug in here or into the stream runtime.
