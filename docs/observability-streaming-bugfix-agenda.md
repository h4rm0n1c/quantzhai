# QuantZhai observability and streaming bugfix agenda

This is the working fix list for the next pass over QuantZhai. It captures the current rough edges in `/status`, `qz-top`, `qz-thoughts`, profile tuning, and proxy streaming.

The goal is not just to make the screens prettier. The goal is to make the stack report the truth early, stream useful information continuously, and give Codex the right runtime context before it starts making decisions like a distracted toaster.

## Scope

Affected surfaces:

- Codex `/status` behaviour through the QuantZhai proxy and model catalog.
- `scripts/qz-top` runtime monitor.
- `scripts/qz-thoughts` reasoning/thought monitor.
- Low/medium/high/xhigh/max reasoning/profile presets.
- Proxy Responses API streaming, summary handling, telemetry, and capture plumbing.
- Any shared telemetry files, sockets, logs, or state paths used by those tools.

## Priority order

1. Fix early runtime truth: `/status` must show the loaded model and context window as soon as possible.
2. Fix telemetry schema: define one shared source of truth for model, context, token counters, timings, and stream events.
3. Fix `qz-top` math and display.
4. Fix `qz-thoughts` rendering and task discipline.
5. Fix concurrent monitor operation.
6. Fix profile presets and reasoning-effort prompts.
7. Fix end-to-end streaming so clients get incremental output rather than delayed paste dumps.

## 1. `/status` reports stale or default runtime data

### Observed problem

`/status` is not handled enthusiastically enough. Runtime data is not pushed or made available as early as it should be.

Current bad behaviour:

- The status view often only becomes meaningfully populated after the first prompt.
- The reported context window can show the default `131072` even when the user started the stack with an override such as `256k`.
- The loaded model type/name is not guaranteed to be the first accurate value shown.
- Startup-time truth and first-request truth can disagree.

### Desired behaviour

Before the first user prompt is sent, QuantZhai should already know and expose:

- Effective model alias/profile requested by Codex.
- Actual GGUF/model loaded by llama.cpp/router.
- Effective context window.
- Effective batch/ubatch/parallel/KV settings where available.
- Backend health and load state.
- Whether the values came from `.env`, model catalog, router state, llama.cpp `/props`/health endpoint, command line, or fallback config.

If the backend has not confirmed a value yet, display it as `pending` or `unconfirmed`, not as a confident default.

### Likely cause areas to inspect

- `scripts/qz-up`
- `scripts/qz-proxy`
- `scripts/qz-codex`
- `proxy/quantzhai_proxy.py`
- `proxy/qz_model_catalog.py`
- any `/v1/models`, `/qz/models`, `/qz/status`, `/status`, or runtime-state endpoints
- startup capture files under `var/captures/`
- runtime state files under `var/run/` or `var/`

### Proposed fix

Create a single runtime-state snapshot that is written at stack startup and refreshed whenever the proxy observes backend facts.

Suggested file:

```text
var/run/qz-runtime-state.json
```

Rules:

- Never display fallback defaults as confirmed backend facts.
- Prefer backend-confirmed facts over env/config.
- Write startup-intended state first.
- Replace with backend-confirmed state as soon as available.
- Make `/status`, `qz-top`, and `qz-thoughts` read the same snapshot.

### Acceptance checks

```bash
QZ_CONTEXT=262144 scripts/qz-up
scripts/qz-codex high
# Open /status before sending the first prompt.
```

Expected:

- `/status` shows `262144` or `256k`, not `131072`.
- Model name/type is populated before the first prompt where backend confirmation allows it.
- Any unconfirmed fields are explicitly marked as unconfirmed.

## 2. `qz-top` token-per-second counters are unreliable

### Observed problem

`qz-top` has bad token-per-second counters. The math is suspect and can produce gibberish.

Likely symptoms:

- TPS spikes to impossible values.
- TPS goes negative or `nan`/`inf`.
- Prompt tokens, completion tokens, cached tokens, or total tokens are mixed together incorrectly.
- Rolling windows may be calculated against mismatched timestamps.
- Values may be derived from logs rather than structured telemetry.

### Desired behaviour

`qz-top` should display boring, defensible numbers:

- Prompt tokens per second, when measuring prompt evaluation.
- Generation tokens per second, when measuring decode/output.
- Overall request tokens per second, clearly labelled if shown.
- Current request elapsed time.
- Request ID.
- Last-event age.
- Model/context from the shared runtime-state snapshot.

No fake precision. No guessing unless explicitly labelled.

### Proposed fix

Define a monotonic telemetry event schema and make `qz-top` consume that instead of scraping ambiguous text.

TPS calculation rules:

- Use monotonic timestamps only.
- Calculate generation TPS from positive deltas in completion tokens over positive elapsed time.
- Maintain a rolling window, for example 2 to 5 seconds.
- Drop samples with missing, stale, or backwards timestamps.
- Clamp display to `unknown` instead of showing nonsense.
- Keep prompt-eval TPS separate from generation TPS.

### Acceptance checks

Run a long generation and watch `qz-top`:

```bash
scripts/qz-top
```

Expected:

- TPS updates smoothly.
- No negative, infinite, or absurd values.
- Prompt and generation rates are not conflated.
- When telemetry is missing, the UI says `unknown` rather than inventing numbers.

## 3. `qz-thoughts` telemetry and rendering are inconsistent

### Observed problem

`qz-thoughts` has multiple issues:

- Telemetry data is inconsistent or missing.
- Rendering does not always match the data it is meant to show.
- It may drift off task and show unrelated stream/log material.
- It is not consistently focused on reporting thought/reasoning/summary data.
- It may not handle reasoning summaries cleanly.

### Desired behaviour

`qz-thoughts` should be a dedicated reasoning-stream viewer.

It should clearly separate:

- Raw reasoning summary events, if available.
- Final answer output, if optionally enabled.
- Tool calls, if optionally enabled.
- Errors and stream lifecycle events.

Default mode should stay focused on thought/reasoning summaries. It should not become a general log tail unless explicitly requested.

### Proposed fix

Give `qz-thoughts` a narrow contract and structured input.

Suggested stream file:

```text
var/run/qz-stream-events.jsonl
```

Rules:

- `qz-thoughts` reads only normalized event JSONL by default.
- Docker/proxy log fallback must be opt-in.
- Reasoning summaries and output text must be tagged differently.
- Preserve event order with sequence numbers.
- Do not re-render the whole screen in ways that duplicate or scramble chunks.
- Handle partial chunks safely.

### Acceptance checks

```bash
scripts/qz-thoughts
scripts/qz-codex high
```

Expected:

- Thought/summary chunks appear as they arrive.
- Output text is not confused for reasoning unless explicitly configured.
- The viewer does not wander into unrelated proxy logs.
- Repeated chunks are de-duplicated or clearly identified.

## 4. `qz-top` and `qz-thoughts` must run together without conflicts

### Observed problem

The monitors need to run at the same time without conflicting.

Potential conflict types:

- Both tools tail or rotate the same file destructively.
- Both tools expect exclusive access to a named pipe/socket.
- Both tools mutate cursor/display state in a shared terminal assumption.
- Both tools consume stream events rather than observing them.
- Both tools trigger log fallback or capture side effects.

### Desired behaviour

Multiple observers should be able to attach at once.

`qz-top` and `qz-thoughts` should be read-only consumers of shared append-only state.

### Proposed fix

Use append-only JSONL event logs plus atomic snapshot files:

```text
var/run/qz-runtime-state.json
var/run/qz-telemetry.jsonl
var/run/qz-stream-events.jsonl
```

Rules:

- Proxy is the only writer.
- Monitors are read-only.
- Writes use append plus flush, or temp-file then atomic rename for snapshots.
- Readers tolerate truncation, rotation, and partial final lines.
- No monitor should delete, rotate, or rewrite shared telemetry.

### Acceptance checks

Open two terminals:

```bash
scripts/qz-top
```

```bash
scripts/qz-thoughts
```

Then run Codex.

Expected:

- Both update at the same time.
- Neither steals data from the other.
- Neither causes proxy output/capture behaviour to change.

## 5. Low/medium/high/xhigh/max profile tuning needs review

### Observed problem

The current profile settings are not well locked to the intended reasoning effort.

Issues noticed:

- Low/medium/high/xhigh or max presets need adjustment based on research and actual Qwen behaviour.
- Reasoning-effort prompts are not reliably obeyed.
- The model may jump to file tools first for everything.
- It sometimes ignores already-injected context and reaches for tools unnecessarily.
- Search/tool selection policy is not clean enough: it should choose direct reasoning, local injected context, web search, or file/code tools based on task needs.

### Desired behaviour

Profiles should control:

- Reasoning budget / effort language.
- Context discipline.
- Tool-use policy.
- Search preference.
- Verbosity and streaming behaviour.
- Max output where useful.

The model should prefer the cheapest reliable information source:

1. Answer from user-provided current prompt when sufficient.
2. Use injected runtime/project context when relevant.
3. Use web search for current/external facts.
4. Use repo/file tools when the task requires repository state.
5. Ask only when blocked by a genuinely missing choice.

### Proposed fix

Create a profile review document or table in the existing profile docs, then encode it into whichever files currently own profile selection.

Likely files to inspect:

- `scripts/qz-codex`
- `config/` model catalog/profile files
- `docs/qz-caveman-codex-model-instructions-v2.md`
- `docs/quantzhai-caveman-profile.md`
- proxy prompt/context injection code

Suggested profile dimensions:

| Profile | Target | Reasoning behaviour | Tool policy |
| --- | --- | --- | --- |
| low | quick local tasks | minimal deliberation | avoid tools unless obviously needed |
| medium | default practical work | moderate planning | use tools when task requires state |
| high | code/research/debug | deliberate, evidence-first | inspect relevant files/search before claims |
| xhigh/max | complex architecture/debug | deeper review, cross-checks | use tools carefully, avoid thrashing |

### Acceptance checks

Build a fixed profile-eval prompt set:

- Current prompt only, no tools needed.
- Injected context contains the answer.
- Needs web/current search.
- Needs repo inspection.
- Needs both repo inspection and web search.

Expected:

- Each profile selects tools sensibly.
- Higher profiles do not blindly tool-spam.
- Lower profiles do not hallucinate missing repo/current facts.

## 6. Proxy streaming and reasoning-summary handling need repair

### Observed problem

The proxy is not handling thought/reasoning streaming cleanly with summary enabled.

Current bad behaviour:

- Some response data is still batched.
- The user experience becomes `crunch for a minute, then paste response`.
- Character/chunk streaming is not working across multiple fronts.
- Main proxy clients do not consistently receive incremental text.
- Diagnostic tools such as `qz-thoughts` do not see clean incremental data.
- Reasoning summary events may be malformed, delayed, missing, or mixed with final output.

### Desired behaviour

For streaming requests, the proxy should forward normalized events as soon as it safely can.

The target user experience:

- Fast first visible event.
- Incremental chunks while the model is working.
- Reasoning summaries appear as summaries, not as leaked raw thought or malformed tags.
- Final output streams progressively.
- Tool calls and tool results are visible in the event stream when appropriate.
- Non-streaming clients still receive a valid complete response.

### Proposed fix

Audit the full streaming path:

1. Incoming `/v1/responses` request handling.
2. Request transformation to llama.cpp/OpenAI-compatible upstream.
3. Upstream streaming parser.
4. Event normalization.
5. SSE forwarding to Codex/client.
6. JSONL event writing for monitors.
7. Final response assembly for non-streaming mode.

Specific requirements:

- Do not wait for full upstream completion before emitting output events.
- Flush SSE after each meaningful event.
- Keep `reasoning_summary` separate from `output_text`.
- Strip or quarantine malformed `<think>`/`</think>` tags.
- Maintain sequence numbers.
- Preserve enough data for `qz-thoughts` without forcing log scraping.
- Make batching explicit and minimal where unavoidable.

### Acceptance checks

Use a deliberately long answer prompt through Codex.

Expected:

- Client shows output incrementally.
- `qz-thoughts` sees summary/thought events incrementally.
- `qz-top` updates token counters during generation.
- No giant delayed paste unless upstream itself only sends data at the end.

## 7. Shared telemetry contract

This is the recommended foundation for all fixes above.

### Files

```text
var/run/qz-runtime-state.json       # atomic latest snapshot
var/run/qz-telemetry.jsonl          # append-only numeric telemetry
var/run/qz-stream-events.jsonl      # append-only normalized stream events
```

### Writer

Only the proxy writes these files during normal operation.

Startup scripts may write an initial `requested` runtime-state snapshot before the proxy confirms backend facts.

### Readers

- `/status`
- `scripts/qz-top`
- `scripts/qz-thoughts`
- benchmark harness summaries
- any future diagnostics

### General rules

- Include `schema` versions.
- Include `request_id` on request-specific events.
- Include monotonic timestamps for math.
- Include wall-clock timestamps for humans.
- Include sequence numbers for stream events.
- Readers must tolerate missing files, partial lines, and version mismatches.
- Bad or missing telemetry should degrade to `unknown`, not fabricated certainty.

## Immediate action checklist

- [ ] Inspect current `/status` implementation and model/context source paths.
- [ ] Inspect `qz-top` token math and data source.
- [ ] Inspect `qz-thoughts` input source and rendering loop.
- [ ] Identify whether monitors currently share a file, pipe, or log tail source.
- [ ] Define and add `qz-runtime-state.json` writer.
- [ ] Define and add telemetry/event JSONL writers in the proxy.
- [ ] Convert `qz-top` to structured telemetry.
- [ ] Convert `qz-thoughts` to structured stream events.
- [ ] Add concurrent monitor smoke test.
- [ ] Review profile prompt/config ownership.
- [ ] Add fixed profile-eval prompt set to the benchmark harness.
- [ ] Tune low/medium/high/xhigh/max based on measured behaviour.
- [ ] Audit streaming path and remove unnecessary buffering.
- [ ] Add tests or smoke scripts for first-status correctness, TPS sanity, thought rendering, and streaming latency.

## First review commands

Run these from the repo root to locate the relevant code before editing:

```bash
grep -RIn "qz-top\|qz-thoughts\|status\|telemetry\|reasoning\|summary\|stream" scripts proxy config docs | head -300

grep -RIn "QZ_CONTEXT\|131072\|context" scripts proxy config | head -200

grep -RIn "tokens_per_second\|tokens/s\|tps\|completion_tokens\|prompt_tokens" scripts proxy | head -200
```

Then inspect the concrete owners:

```bash
sed -n '1,260p' scripts/qz-top
sed -n '1,260p' scripts/qz-thoughts
sed -n '1,260p' scripts/qz-codex
sed -n '1,260p' scripts/qz-up
sed -n '1,320p' proxy/quantzhai_proxy.py
```

## Notes

Do not fix this by adding more clever log scraping. That way lies nonsense TPS, duplicate thought chunks, and the kind of dashboard only a committee could love.

The correct shape is:

```text
proxy observes facts -> proxy writes structured state/events -> tools render read-only views
```

Everything else is emergency string archaeology.
