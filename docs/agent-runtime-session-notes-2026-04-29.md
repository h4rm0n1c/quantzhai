# Agent Runtime Session Notes: 2026-04-29

## Purpose

This records the design decisions, discoveries, and roadmap items from the
2026-04-29 QuantZhai tuning session so they do not live only in chat history.

The session focused on compact model instructions, benchmark tooling, runtime
observability, streamed reasoning visibility, session resume behavior, local web
search quality, and time/date grounding.

## Compact Prompt Profiles

The `Qwen3.6Turbo-caveman` profile proved that a compressed instruction profile
can materially reduce prompt cost while still keeping useful Codex behavior.

Important behavior:

- The compact profile must start in compact mode at session start. The user
  should not need to say `caveman on` after launch.
- The prompt must still allow compact mode to be disabled for a session with
  plain language such as `normal mode` or `caveman off`.
- Compact chat style must never leak into code, tests, docs, commit messages,
  UI text, config, or other artifacts unless explicitly requested.
- First-turn behavior matters. A profile that only starts compressing after the
  user corrects it is not good enough.

Naming direction:

- Keep the existing caveman prompt as the first successful experiment.
- Add a future `grug` profile instead of continuing to expand the caveman name.
- Default future `grug` profile to ultra compression, while preserving a clean
  escape hatch back to normal mode.

Benchmark anchor:

- The default Codex system prompt remains the coherence benchmark.
- Compact prompts are only useful if they preserve enough Codex-level planning,
  tool discipline, artifact quality, git hygiene, and safety behavior to be
  useful in this repo.
- The external Caveman corpus and public compression discussion are useful
  compression benchmarks, but QuantZhai must not compromise Codex behavior just
  to minimize visible words.

## Benchmark Harness

The auto benchmark harness exists so QuantZhai can compare profiles without
manual command-driving.

Current benchmark prompt set includes:

```text
examine current dir, this stack runs you: evaluate, report.
```

This prompt is important because it tests:

- directory inspection
- stack comprehension
- tool use discipline
- response quality under higher reasoning
- compact-profile coherence

Metrics that matter:

- input-token ratio against the baseline profile
- input-token savings
- instruction-token ratio
- visible final-answer ratio
- API output-token ratio
- total-token ratio
- wall-time ratio
- benchmark case count

`qz-top` should continue surfacing the latest benchmark compression summary so
prompt tuning has live feedback.

Future benchmark needs:

- more prompts that exercise tool use, git hygiene, planning, and error recovery
- an approval workflow that lets Codex run safe benchmark commands after the
  user approves the command category once
- run-scoped captures that tie benchmark cases to proxy logs, thought captures,
  search routes, and final metrics

## Runtime Observability

`qz-top` and `qz-thoughts` are now the main local visibility tools.

`qz-top` should answer:

- is the backend healthy?
- is the proxy healthy?
- what container is running?
- what are GPU memory and utilization doing?
- what are prompt/generate throughput and recent backend activity?
- what was the latest benchmark compression result?

`qz-thoughts` should answer:

- what reasoning/thought text did the latest Responses request produce?
- what answer did it emit?
- what backend activity happened around it?
- how old is the capture?
- which model/response/source capture is being displayed?

Important discovery:

- Current local `/v1/responses` requests that use local tool/search recursion
  are buffered upstream with `stream=false`.
- The proxy then emits synthetic Responses SSE from the completed response.
- Because of that, `qz-thoughts` can show live backend activity while the model
  churns, but the reasoning text itself is not token-live on that path.

Roadmap consequence:

- Streaming and local tools cannot stay separate.
- The target is a streamed Responses state machine that can relay model deltas,
  detect completed function calls, execute supported local tools, append tool
  outputs, and continue streaming the next upstream hop.

## Session Resume Behavior

`qz-codex` must pass Codex subcommands through correctly.

The broken behavior was:

```bash
qz-codex caveman resume 019dd7a5-ca8b-7b31-994e-fcde3def5824
```

opening a fresh session instead of resuming the named session.

The required behavior is:

```bash
scripts/qz-codex caveman resume SESSION_ID
scripts/qz-codex caveman resume --last
scripts/qz-codex resume --last
```

Profile selection and Codex subcommand passthrough must both work. This matters
because profile testing is useless if session continuity silently disappears.

## Web Search Direction

The current local `web_search` tool is useful but too limited if it only means
"the model may call search twice per turn."

Do not fix this by blindly raising the per-turn search call limit. That risks
tool loops, context bloat, and low-signal result spam.

Better direction:

- Keep one public tool name: `web_search`.
- Add a smarter internal search mode that returns a compact research packet.
- Let one tool call fan out internally to several query variants and SearXNG
  profiles under a hard budget.
- Deduplicate and rank results.
- Fetch a small number of top pages under byte and time limits.
- Extract relevant spans.
- Return a compressed evidence pack to the model.
- Store full raw artifacts under `var/captures/search-runs/RUN_ID/`.

Suggested shape:

```json
{
  "action": "search",
  "query": "streaming tool calls Responses API local proxy",
  "mode": "quick",
  "max_context_tokens": 1600
}
```

Modes:

- `quick`: one focused query, small result set, minimal context.
- `normal`: a few query variants, dedupe, compact snippets.
- `deep`: broader fanout, page fetch, span extraction, larger but capped packet.

Budget controls should be enforced by the proxy, not merely requested in the
prompt.

Useful metrics for `qz-top`:

- search calls
- internal query fanout count
- pages fetched
- returned search tokens
- cache hits
- budget used
- failure/timeout count

Research sources reviewed during discussion:

- Agentic RAG Survey of Surveys: https://papers.cool/arxiv/2603.07379
- Search-o1: https://aclanthology.org/2025.emnlp-main.276/
- Adaptive RAG / INKER: https://www.sciencedirect.com/science/article/pii/S0306457325004753
- AdaCache ICLR 2026: https://iclr.cc/virtual/2026/poster/10010915
- OpenAI web search docs: https://platform.openai.com/docs/guides/tools-web-search

Key takeaway:

- The simple win is not "more calls." The simple win is "one call returns a
  budgeted, ranked, compressed evidence packet."

## Token And Context Awareness

QuantZhai should make token budget visible to both tooling and prompts.

Prompt-level guidance is useful but not sufficient. The proxy should enforce
hard budgets and report what happened.

Needed runtime concepts:

- per-request input budget
- per-tool returned-token budget
- per-search page-fetch budget
- compact capture summaries
- visible run metrics in `qz-top`
- benchmark summaries that compare instruction and input-token cost

The model can be instructed to prefer cheap context first, but the runtime must
prevent runaway loops.

## Time And Date Grounding

Agents need current time/date bearings when the user anchors a request to time.

Examples:

- today
- yesterday
- tomorrow
- latest
- current
- now
- deadline
- schedule
- log age
- benchmark run time
- release date

Recommended QuantZhai behavior:

- Inject a small stable session/date anchor, such as:

```text
Current date: 2026-04-29. Timezone: Australia/Perth.
```

- Avoid injecting changing seconds into every prompt because it can hurt prompt
  cache reuse.
- Provide an exact local time tool or runtime field for requests where the clock
  matters.
- Prompt rule: when the user references relative or current time, check the
  runtime date/time anchor before answering or acting.

Good split:

- stable date/timezone anchor in normal session context
- exact timestamp only when needed
- benchmark and monitor timestamps recorded in runtime artifacts

## Roadmap Order

Near-term order from this session:

1. Preserve current working caveman profile as the successful first compact
   prompt experiment.
2. Keep improving benchmark harness and metrics.
3. Keep `qz-top` and `qz-thoughts` useful for live local debugging.
4. Build streamed Responses with local tool-call continuation.
5. Add run-scoped captures so monitors and benchmarks can point at the same
   execution.
6. Add smarter budgeted search packets after the streaming/tool state machine is
   easier to test.
7. Develop the future `grug` compact profile against Codex coherence and
   benchmark results.
8. Add time/date grounding as a first-class runtime/prompt rule.
