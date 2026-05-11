# LLM Signal System — Research Findings

Date: 2026-05-11

This document records the research pass referenced in `docs/llm-signal-system.md`.
It covers what others have done with signal injection, what Qwen3.6 specifically
does and doesn't respond to, and what this implies for our design.

---

## The single most important finding: Qwen3.6 has an infinite reasoning loop problem

Qwen3.6-35B-A3B has a documented and unresolved tendency to loop endlessly
in its reasoning channel — reconsidering the same steps rather than
executing them. Users report the model spending 100+ reasoning iterations on
simple tasks where other models finish in one.

Critically: **the llama.cpp `--reasoning-budget N` parameter does not reliably
work.** The server currently only honours two values: `-1` (unlimited) and `0`
(no reasoning). Specific numeric budgets are silently ignored.

This means our self-management signals for Qwen3.6 need to target the reasoning
loop directly, not just the conversation hop count. The model may spin in
reasoning even when the conversation hop budget is fine.

### What does work (partially)

llama.cpp has a `--reasoning-budget-message` flag — a natural language string
appended to the reasoning channel when the budget is reached:

> "OK, I've thought long enough. Let's answer."

This mimics a natural decision point. Success is inconsistent but it is the most
promising single mechanism available today. The proxy could potentially inject
this via the reasoning policy rather than waiting for llama.cpp to trigger it.

**Implication for signal design:** hop budget and reasoning budget are
different things. A conversation-level "3 hops remaining" signal may not help
if the model is looping inside reasoning on the current hop. Reasoning-level
signals are needed.

---

## What others do: framework limits vs prompt signals

### LangGraph — hard limits, not prompts

LangGraph handles operational constraints (hop budget, retry limits) through
**framework-level enforcement**, not by telling the model in the prompt. The
framework just stops after N attempts. The model is never told "you have 3
hops left."

Key quote from their best practices:
> "For operational constraints, use LangGraph graph-level enforcement rather
> than prompt-based signals — hard limits on retry attempts and total tool
> calls, separate error handling nodes."

**Implication:** LangGraph's answer to "how do you signal hop budget?" is "you
don't — you enforce it architecturally." This is different from what we're
considering. Worth asking whether the model response to a budget signal is
better than just hard-stopping.

### Anthropic — smallest set of high-signal tokens

Anthropic's context engineering guidance (from their engineering blog):
> "Identify the smallest possible set of high-signal tokens that maximise the
> likelihood of some desired outcome."

System prompts should contain high-level behavioural guidance at "the right
altitude — specific enough to guide behaviour effectively, yet flexible enough
to provide the model with strong heuristics."

They do NOT explicitly recommend budget/pressure signals in prompts. Their
approach to long-context management is **structured note-taking** — agents
maintain persistent memory files outside the context window for state.

**Implication:** Anthropic leans toward compact, high-altitude prompts rather
than operational signals. Their Claude models may be more responsive to
well-crafted system prompts than Qwen. Do not assume Anthropic's guidance
transfers directly to Qwen on llama.cpp.

### LangGraph progressive disclosure — best practice for token efficiency

The article "Stop Stuffing Your System Prompt" describes a three-tier
pattern to avoid signal degradation as prompts grow:

- Tier 1 (always present): lightweight catalog (~500 tokens)
- Tier 2 (on demand): full skill instructions (~2,000 tokens) when needed
- Tier 3 (fine-grained): specific reference files only when requested

Key observation: **as prompts grow, relevant instructions compete with
unrelated content and effectiveness drops.** This is directly applicable
to our signal system — if we inject context pressure and hop budget signals
into the system prompt on every turn, they become noise.

**Implication:** signals should be ephemeral and turn-scoped, not permanent
system prompt residents. Inject them when relevant, not always.

### ReAct — reasoning is NOT part of subsequent turn history

The ReAct (Reason + Act) pattern uses a Thought → Action → Observation loop.
Each turn, the model reasons, acts, and receives an observation. The **observation
feeds back** but the Thought (reasoning) from the previous turn typically does
NOT appear in subsequent turns.

This is consistent with what our capture audit found: Qwen's reasoning items
are dropped by the proxy before reaching the model on subsequent turns. ReAct
treats this as correct — the model re-reasons from observations rather than
replaying its prior reasoning.

More recent variants (ReflAct, Focused ReAct, PreAct) add structure:
- **ReflAct**: structured reflection on belief state and task goal at each step
- **Focused ReAct**: terminates repetitive behaviour before resource exhaustion
- **PreAct**: adds a prediction stage to enumerate possible outcomes before acting

The "Focused ReAct" variant is directly relevant — it addresses the same
infinite loop problem Qwen3.6 has. Worth reading the implementation.

**Implication:** dropping reasoning from history is correct per ReAct. But
variants like ReflAct suggest that a STRUCTURED SUMMARY of the reasoning
(belief state, task goal) fed back at the start of each hop could improve
convergence. This is the "compact reasoning summary" option from our design.

---

## Qwen3.6 specifics

### Thinking mode vs non-thinking mode

Qwen3.6 supports seamless switching between thinking mode (for complex
reasoning) and non-thinking mode. The proxy currently runs in summary mode
which transforms `reasoning_text.delta` into `reasoning_summary_text.delta`.

A new Qwen3.6 feature: **reasoning context retention** — optionally retaining
reasoning context from historical messages to improve decision consistency
and reduce redundant reasoning. This is Alibaba's own answer to the "should
reasoning appear in history?" question: yes, optionally.

This is significant. Our current proxy drops all reasoning from history (the
correct behaviour per ReAct). But Qwen3.6 itself supports retaining it. If
we selectively retain compact reasoning summaries in the model's history, we
may improve multi-turn task consistency in line with what the model was
designed for.

**Implication:** Qwen3.6's retention mode is worth experimenting with. The
proxy could optionally include the reasoning summary (not raw reasoning text)
in subsequent turn inputs as a lightweight context anchor.

### MoE architecture implications

Qwen3.6-35B-A3B is a Mixture of Experts model (256 experts, 8 active). MoE
models can activate different expert subsets for different types of input.
Meta-instructions ("you have 3 hops remaining") may activate different
experts than task content. Whether the relevant experts for self-regulation
respond well to these signals is unknown without empirical testing.

**Implication:** don't assume meta-instruction following works the same as
in dense models. Test empirically with the fuzz infrastructure.

---

## Conclusions for our design

### What this changes

1. **Reasoning loop is the immediate problem, not hop count.** Our model
   loops in reasoning. Hop-level signals won't fix reasoning-level looping.
   The `--reasoning-budget-message` mechanism is the most targeted lever we
   have today.

2. **Don't put signals in the system prompt permanently.** Signal degradation
   is real. Signals should be injected when relevant, dropped when not, and
   kept compact.

3. **Consider ephemeral in-turn context messages over system prompt additions.**
   LangGraph puts operational constraints in the framework. Anthropic keeps
   system prompts compact. The best injection point for hop budget and context
   pressure is probably a brief in-turn message at the start of each hop when
   the signal is actionable.

4. **Qwen3.6's own reasoning retention mode is worth trying.** The proxy
   currently drops all reasoning history. The model was designed to optionally
   retain it. A compact reasoning summary carried forward per hop aligns with
   both ReflAct and Qwen3.6's own design.

5. **Empirical testing over theory.** Qwen is MoE, llama.cpp budget controls
   don't work reliably, and prior art (Anthropic, LangGraph) optimised for
   different models. Run the fuzz battery with different signal formats and
   measure convergence rate.

### Revised signal priority

Previous priority: hop budget → context pressure → backend errors → search quality

Revised priority after research:

1. **Reasoning budget message** — target the reasoning loop problem directly.
   Most acute pain, most direct mechanism available. Relatively low
   implementation cost via llama.cpp or proxy-side reasoning truncation.

2. **Compact reasoning summary in history** — experiment with carrying a brief
   summary of the prior turn's reasoning into the next hop. Aligns with Qwen's
   own retention mode. Targets multi-turn decision consistency.

3. **Hop budget as ephemeral in-turn message** — inject "N hops remaining" at
   the start of each hop when budget is getting tight (not always). Keep it
   to one line. Measure whether model behaviour changes.

4. **Context pressure** — similar to hop budget. Inject when pressure is high,
   not as permanent system prompt resident.

---

## Sources

- [Qwen3.6-35B-A3B HuggingFace discussions — endless reasoning loops](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/discussions/19)
- [Qwen3.6-Plus blog — reasoning context retention](https://qwen.ai/blog?id=qwen3.6)
- [Anthropic — Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Stop Stuffing Your System Prompt — LangGraph progressive disclosure](https://pessini.medium.com/stop-stuffing-your-system-prompt-build-scalable-agent-skills-in-langgraph-a9856378e8f6)
- [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)
- [AG2 — Advanced ReAct Loops including ReflAct and Focused ReAct](https://docs.ag2.ai/latest/docs/blog/2025/06/12/ReAct-Loops-in-GroupChat/)
