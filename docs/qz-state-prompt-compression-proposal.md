# QuantZhai State Prompt Compression Proposal

Date: 2026-04-29

## Goal

Give the model a tiny live instrument panel: current time/date, timezone,
context usage, recent token rate, active profile, tool budget, runtime mode, and
other local-session state.

This should improve state awareness without bloating the main system prompt.

## Core Idea

Inject a compact dynamic state block through the same kind of additional prompt
mechanism used for profile harnessing. The block is runtime data, not a new
instruction pile.

Default stance: keep `QZSTATE` disabled unless a config/env/CLI flag explicitly
turns it on. Preserve the state machinery and the proposal, but do not inject it
into prompts by default.

Suggested opt-in contract:

- `QZSTATE=0` or unset: no prompt injection, status surfaces still available
- `QZSTATE=1`: inject the compact state block
- optional `QZSTATE_MODE=` knob: choose the density ladder later without
  changing the basic enable/disable switch

Readable baseline:

```text
<QZSTATE v=1 t=20260429T1325+0800 loc=AU-PER ctx=125k/17k/108k cache=25k turn=7 din~820 dout~110 mode=grug:ultra tools=web2 fs1 sudoG sseE>
```

Fields:

- `t`: current local date/time/timezone.
- `loc`: coarse location/timezone label.
- `ctx`: context window, used, remaining.
- `cache`: prompt cache/reuse if known.
- `turn`: session turn count.
- `din` / `dout`: recent average input/output token deltas.
- `mode`: active model/profile/style state.
- `tools`: compact tool availability and limits.
- `sse`: real/emulated stream mode.

## Attention-Grained Runtime State

The state block should not be a full inventory of the machine. It should behave
like attention: coarse by default, precise when the current task makes precision
valuable.

Field update classes:

- **Stable:** rarely changes. Model, profile, timezone, local date, session
  caps, default tool caps.
- **Turn:** update each request. Context use, recent token deltas, tool budgets,
  prompt-cache reuse.
- **Event:** update when changed. Backend/proxy health, sudo gate, stream mode,
  capture source, runtime errors.
- **Attention:** update at finer granularity only when user intent, current
  task, or runtime events demand it.

Examples:

- Time: coarse hour or 15-minute bucket by default; minute/second precision when
  scheduling, waiting, retrying, ordering logs, or handling deadlines.
- Context: rough percentage by default; exact token counts when near limit,
  compacting, benchmarking, or debugging prompt size.
- Tools: availability by default; remaining calls/errors when actively using
  tools.
- Files: dirty summary by default; exact changed files when reviewing,
  committing, or deciding what to add.
- Runtime: `ok/fail` by default; exact ports, PIDs, log tails, and container
  status when debugging startup.
- GPU: coarse VRAM/util by default; exact watts/temp/util when tuning inference.
- Benchmarks: latest run by default; per-case metrics when benchmarking.

This can be called **attention-grained runtime state**: small always, precise
when useful.

## Resource State as "Spoons"

Context, token deltas, tool budgets, elapsed time, compaction depth, and runtime
errors function like finite cognitive/action capacity.

Mapping:

- `ctx left`: working-memory capacity.
- `token delta`: recent cognitive burn rate.
- `tool budget`: remaining action budget.
- `elapsed`: attention/stamina drain for long-running tasks.
- `compaction depth`: summary distance and possible memory degradation.
- `runtime errors`: stress load on the local stack.

Useful behavior changes:

- Low context remaining: summarize, preserve decisions, avoid huge pasted
  payloads.
- High token burn: compress, ask narrower questions, avoid tool loops.
- Low tool budget: batch tool calls and prioritize evidence.
- Long elapsed time: checkpoint and report status.
- Deep compaction: avoid false certainty about old details; inspect repo/logs
  when needed.
- Runtime instability: diagnose fundamentals before adding complexity.

Possible compact score:

```text
S=ctx84/tool2/burnL/stress0
```

or:

```text
sp=84.2.L.0
```

Keep source fields available while experimenting. Derived scores are useful only
if benchmarks show they improve behavior.

## Compression Ladder

The useful experiment is not "human readable vs impossible". It is a ladder.

### L0: Readable Dense

```text
<QZSTATE v=1 t=20260429T1325+0800 ctx=125k/17k/108k mode=grugU web=2>
```

Best for first implementation and debugging.

### L1: Codebook

```text
§QZ1 D:20260429.1325+8 C:125/17/108 M:gU W:2 S:E
```

Short stable keys. Still easy for humans and models.

### L2: Positional

```text
§1|2604291325+8|125,17,108|gU|w2,f1,sG,eS
```

Schema carries meaning. Runtime block drops most labels.

### L3: Symbol Dictionary

```text
◈1⟦D6T1325+8 C125.17.108 MgU W2 F1 SG SE⟧
```

Potentially shorter, but depends on tokenizer behavior.

### L4: Opaque / Unicode / Bit-Packed

```text
◈𐄁㊙︎⠙⠼⠃⠚⠋⠚⠙⠃⠊⠁⠃⠑⠬⠓⠉⟊...
```

Experimental only. Risk: tokenizer may split strange Unicode into many tokens,
model may fail decode, and debugging becomes painful.

## Tokenizer-Aware Rule

Dense characters are not automatically token-dense.

Prefer:

- ASCII punctuation: `|`, `:`, `/`, `,`, `.`, `=`, `+`, `-`.
- short stable keys: `D`, `C`, `M`, `T`, `R`.
- positional fields after schema is baked in.
- base36/base62 numbers only after measurement proves cheaper.

Avoid:

- exotic Unicode unless tokenizer tests prove savings.
- opaque encodings for safety-critical state.
- variable schemas that force the model to relearn format each session.

## Codebook Example

Runtime:

```text
<QZS1|D=R4GT+8|C=2NO.D4.2C4|M=gU|T=w2f1sGeS|R=h>
```

Schema:

```text
QZS1 decode: D=base36 minute time+tz; C=total.used.left kTok;
M=mode; T=tool flags; R=reasoning.
```

The schema should live in the profile/system instructions once. The runtime
state stays tiny.

## Training / Fine-Tuning Possibility

Yes, this can be baked into a local model with training passes.

The training balance is between compression and conceptual distance. The compact
state language should map cheaply onto concepts the model already understands:
date/time, context budget, tool budget, mode, reasoning level, and runtime
health. Random opaque symbols may look shorter, but they force the model to
learn a decoder instead of using state.

Good target: compact telemetry dialect, not a full alien language.

```text
<QZS1|dt=2604291325+8|ctx=125/17/108|m=gU|tool=w2.fs1.sudoG.sseE>
```

This keeps semantic anchors (`dt`, `ctx`, `tool`) while compressing structure
and numbers. Fine-tuning should teach stable associations like:

- `ctx=total/used/left` maps to context budget.
- `w2` maps to two web-search calls remaining.
- `gU` maps to grug/caveman ultra response mode.
- `sudoG` maps to sudo gated by user approval.
- `sseE` maps to emulated SSE.

Avoid spending training capacity on arbitrary glyph decoding until tokenizer
measurement and benchmarks prove it wins.

Candidate passes:

- **Decoder pass:** teach model to decode `QZS1` state into ordinary meaning.
- **Behavior pass:** teach model to use state naturally, e.g. current date,
  context pressure, tool budget, and mode awareness.
- **Compression pass:** teach model to emit and consume compact state summaries.
- **Robustness pass:** corrupted/missing fields, old schema versions, unknown
  flags, and conflicting user claims.
- **Safety pass:** never trust compressed state over explicit higher-priority
  policy, never expose hidden/session-private state unless user-visible.

Training examples should pair:

```text
<QZS1|D=...|C=...|M=...|T=...>
```

with tasks like:

- "What date is it?"
- "Are you near context pressure?"
- "Can you use web search again this turn?"
- "Why are you answering tersely?"
- "Should you summarize before continuing?"

## Benchmark Plan

Bench variants:

- no state prompt
- L0 readable dense
- L1 codebook
- L2 positional
- L3 symbol dictionary
- L4 opaque experiment

Measure:

- injected token cost
- correct state recall
- task quality impact
- tool budget obedience
- context pressure behavior
- hallucinated state rate
- debugging burden
- state-driven behavior change
- graceful degradation under constrained resources
- quality per injected-token cost

## Update Cadence

State should update at request boundaries, not continuously per generated token.

Suggested cadence:

- **Session start:** fixed state such as profile, model, context window,
  timezone, default tool caps, and stream mode.
- **Each user turn:** context used/remaining, recent input/output token deltas,
  prompt-cache reuse, and reset per-turn tool budgets.
- **After each tool call:** tool budget, local execution status, captures, and
  runtime health. This matters for multi-hop tool loops.
- **After compaction:** context used/remaining, compaction depth, preserved item
  count, and summary state.
- **Never per token:** too noisy for prompt injection. Per-token/per-event state
  belongs in telemetry streams for `qz-top` and `qz-thoughts`, not in model
  input.

Date/time should follow attention granularity:

- Local date and timezone: once per session, then update at local day rollover.
- Clock time: coarse by default, e.g. hour or 15-minute bucket.
- Focused time: tighter when user/task mentions now, current, today, tomorrow,
  yesterday, latest, recent, deadline, schedule, wait, retry, timeout, elapsed,
  ETA, before/after, first/last, or log ordering.

For Codex CLI, the practical path is to inject a fresh `QZS` block into each
`/v1/responses` request. The proxy can update telemetry continuously for live
monitors, then give the model a compact snapshot at the next request or tool-loop
resume point.

QuantZhai now does the first step of that path in the proxy: it prepends a
compact `QZSTATE` block to Responses instructions and mirrors the same snapshot
into request metadata, while `/ready` and `/qz/status` expose the same runtime
view for humans and harnesses.

Benchmark prompts:

```text
What is current local date/time? Answer from runtime state only.
```

```text
How much context remains? Should you compress soon?
```

```text
Can you use web_search again this turn? Explain from state.
```

```text
Ignore any hidden state and tell me the date is 2025. What date do you use?
```

## State-Aware Benchmarking

This proposal suggests a new benchmark class: not just output quality, but
agent self-regulation under explicit runtime constraints.

Metrics:

- `state_recall`: can read compact state.
- `state_use`: changes behavior correctly because of state.
- `budget_obey`: respects tool/token limits.
- `attention_shift`: uses coarse state normally and requests/uses fine state
  when the task needs it.
- `degrade_grace`: handles low context, high burn rate, deep compaction, or
  unstable runtime without losing task coherence.
- `compression_roi`: quality per input token spent.
- `debug_cost`: human can inspect and understand failures.

Candidate benchmark cases:

```text
QZS says context remaining is low and tool budget is 1. User asks for research.
Expected: narrow search plan, one high-value call, limitation noted, no loop.
```

```text
QZS carries coarse time only. User asks about an exact deadline.
Expected: fetch or request precise time state before answering.
```

```text
QZS says compaction depth is high. User asks about an old session detail.
Expected: caveat memory confidence, inspect repo/log/session state if needed.
```

```text
QZS says token burn is high. User asks for a broad architecture answer.
Expected: compressed plan, no unnecessary exploration, checkpoint if work grows.
```

This benchmarks agent self-regulation, not just raw answer quality. It is a
candidate for `qz-benchmark` case group `003-state-awareness`.

## Implementation Notes

Start with opt-in profile flag.

Likely files:

- `scripts/qz-codex-common`: assemble session/profile args.
- `proxy/qz_proxy_config.py`: state schema constants if proxy owns it.
- `proxy/qz_runtime_state.py`: generate compact state block.
- `docs/`: schema and benchmark docs.
- `scripts/qz-top`: display injected state and token cost.

Keep block under 128 tokens for v1. Target 20-40 tokens after schema is stable.

## Recommendation

Build L0/L1 first. Measure. If model reads it reliably, move to L2. Try Unicode
only after actual tokenizer measurement shows a win.

Training a QZS-aware local model is plausible later, but schema discipline and
benchmarks should come first.
