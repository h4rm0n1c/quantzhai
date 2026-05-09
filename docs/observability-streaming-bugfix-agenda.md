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

Create a single runtime-state snapshot owned by the proxy. Startup may record
requested state, but live truth should come from `/qz/status` as the proxy
observes backend facts.

Primary surface:

```text
/qz/status
```

Rules:

- Never display fallback defaults as confirmed backend facts.
- Prefer backend-confirmed facts over env/config.
- Write startup-intended state first.
- Replace with backend-confirmed state as soon as available.
- Make `/status`, `qz-top`, and `qz-thoughts` read the same proxy-owned
  snapshot.
- Treat any `var/run/` snapshot file as cache/debug fallback, not live truth.

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

Status:

```text
Fixed first pass. qz-top now uses structured proxy telemetry for rates, rejects
non-finite or non-positive samples, keeps prompt/generation/total rates
separate, and displays latest as the newest valid sample rather than the
highest observed sample.
```

VRAM display has a host-local first pass. `qz-top` now shows `USED`, low-water
`BASE`, live `DELTA`, `FREE`, and `TOTAL` from `nvidia-smi`, but those values
are not yet proxy/backend telemetry. `DELTA` is only an approximation for
cache/buffer pressure until the backend reports model, KV-cache, and scratch
buffer allocations explicitly.

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
- VRAM source and confidence, so host-local GPU probes are not confused with
  backend-confirmed allocation telemetry.
- Current GPU VRAM split:
  - `used`: current device memory used.
  - `model_base`: backend-confirmed model allocation when available; otherwise
    monitor low-water baseline.
  - `kv_cache`: backend-confirmed KV/cache allocation when available.
  - `scratch_buffers`: backend-confirmed compute/scratch buffers when available.
  - `delta`: current used minus model base when exact backend split is missing.
  - `free` and `total`.

### Codex TUI `/status` note

`qz-codex` launches Codex with a generated model catalog. That catalog carries
`context_window`, `max_context_window`, truncation policy, reasoning levels, and
prompt metadata, and the Responses terminal event carries usage counts back to
the client. QuantZhai's own live runtime truth is exposed through `/qz/status`
and `/qz/telemetry/*`.

Do not assume the Codex TUI `/status` command reads `/qz/status`. Treat that as
unproven unless a supported Codex CLI hook or event field is identified. Until
then, `qz-top` and `/qz/status` are the authoritative local runtime views.

No fake precision. No guessing unless explicitly labelled.

### Proposed fix

Define monotonic telemetry events in the proxy and make `qz-top` consume the
proxy telemetry endpoints instead of scraping ambiguous text or treating files
as the live channel.

Primary live inputs:

```text
/qz/status              # current runtime snapshot
/qz/telemetry/recent    # bounded recent history
/qz/telemetry/stream    # live SSE telemetry stream
```

Backend telemetry update still needed:

- Add a proxy/backend `vram_snapshot` or `gpu_snapshot` event with schema id,
  monotonic timestamp, wall timestamp, and source fields.
- Prefer backend-reported allocation classes when TurboQuant/llama.cpp exposes
  them: model weights, KV/cache, scratch/compute buffers, other/process
  overhead.
- Preserve qz-top's direct `nvidia-smi` probe as a fallback/host view and label
  it as `source=nvidia-smi`.
- Make `/qz/status` and `/qz/telemetry/recent` expose the same VRAM snapshot so
  `qz-top --once`, live qz-top, and any future remote monitor agree.
- Display confidence/state: `confirmed` for backend allocation split,
  `estimated` for low-water delta, `unknown` when neither source is available.

JSON/JSONL files may be written for replay, audit, and offline debugging, but
they are not the primary freshness contract for the live dashboard.

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

Give `qz-thoughts` a narrow contract and structured live input from the proxy.

Primary live input:

```text
/qz/telemetry/stream
```

Rules:

- `qz-thoughts` reads normalized proxy telemetry by default.
- `/qz/telemetry/stream` should be SSE so thought/summary chunks arrive without
  polling a file.
- `--file` is the only capture replay path and remains useful for raw captures
  and regression fixtures.
- JSONL stream files are optional debug/audit artifacts, not live truth.
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

`qz-top` and `qz-thoughts` should be read-only consumers of proxy-owned
telemetry. Attaching one monitor must not consume or mutate events for another.

### Proposed fix

Use the proxy telemetry bus as the primary fan-out point:

```text
/qz/status
/qz/telemetry/recent
/qz/telemetry/stream
```

Rules:

- Proxy owns live telemetry and fan-out.
- Monitors are read-only.
- Live monitors prefer proxy endpoints over files.
- JSON/JSONL under `var/` may exist for replay/debug, but readers must treat it
  as stale-prone fallback unless explicitly opened with `--file`.
- No monitor should delete, rotate, rewrite, or become the source of shared
  telemetry.

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

Current supported captures show ordinary shell-command lifecycle already reaches
Codex in the expected public shape: `command_execution status=in_progress`,
then `status=completed`, then `turn.completed usage`. The remaining hard case is
proxy-local tools. Local `web_search` emits `tool_call_started` and
`tool_call_completed` telemetry to QZ monitors, but Codex only receives the
public `web_search_call` after local execution completes. Before changing this,
capture whether Codex will render a display-only `web_search_call
status=in_progress` without treating it as a runnable private function call.

### Acceptance checks

Use a deliberately long answer prompt through Codex.

Expected:

- Client shows output incrementally.
- `qz-thoughts` sees summary/thought events incrementally.
- `qz-top` updates token counters during generation.
- No giant delayed paste unless upstream itself only sends data at the end.

### 2026-05-07 audit status

Done for the audited Responses streaming path. A capture-enabled proxy was run
against the live TurboQuant backend and checked through client SSE,
`/qz/telemetry/stream`, `/qz/telemetry/recent`, `qz-top`, and `qz-thoughts`.

Result:

- Forwarded stream emitted `response.completed` once and `[DONE]` once.
- Summary mode converted upstream `response.reasoning_text.delta` into client
  `response.reasoning_summary_text.delta`; no raw reasoning text leaked.
- `sse_event` and `stream_event_timing` telemetry carry `request_id`.
- Forwarding delay stayed low: average `parsed_to_forwarded_ms` was `0.172`,
  max was `2.717`.
- `qz-top` and `qz-thoughts` read the proxy telemetry surfaces concurrently.

Remaining issue:

- The backend can emit reasoning-only completions with no `output_text` deltas.
  That leaves `qz-thoughts` ANSWER empty even though transport is behaving.
  Treat this as profile/reasoning preset work or report it explicitly as a
  reasoning-only completion.

## 7. Shared telemetry contract

This is the recommended foundation for all fixes above.

### Live endpoints

```text
/qz/status                          # current runtime snapshot
/qz/telemetry/recent                # bounded recent telemetry history
/qz/telemetry/stream                # SSE event stream for live monitors
```

Compatibility note:

```text
/qz/telemetry/events remains available as the older live SSE endpoint name.
New readers should use /qz/telemetry/stream.
```

### Optional files

```text
var/run/qz-runtime-state.json       # optional latest snapshot/cache
var/run/qz-telemetry.jsonl          # optional append-only audit/replay log
var/run/qz-stream-events.jsonl      # optional normalized stream replay log
```

Files are fallback/debug surfaces. They must not be the source of live truth for
`qz-top` or `qz-thoughts`.

### Owner

Only the proxy owns normal runtime telemetry.

Startup scripts may write an initial `requested` runtime-state snapshot before the proxy confirms backend facts.

### Readers

- `/status`
- `scripts/qz-top`
- `scripts/qz-thoughts`
- benchmark harness summaries
- any future diagnostics

### General rules

- Include `schema` versions.
- Include `request_id` on request-specific events, including transformed SSE
  event telemetry and stream timing telemetry.
- Include monotonic timestamps for math.
- Include wall-clock timestamps for humans.
- Include sequence numbers for stream events.
- Readers must tolerate missing endpoints, missing files, partial lines, and
  version mismatches.
- Bad or missing telemetry should degrade to `unknown`, not fabricated certainty.

Current first schema:

```text
event schema:  qz.telemetry.event.v1
state schema:  qz.telemetry.state.v1
recent schema: qz.telemetry.recent.v1
stream schema: qz.telemetry.stream.v1
unknown runtime schema: qz.runtime.summary.v1

event fields:
  schema
  seq
  type
  ts / wall_ts
  monotonic_ts
  request_id
  payload

state/recent/stream runtime:
  runtime.schema == qz.status.summary.v1 when live status is available
  runtime.schema == qz.runtime.summary.v1 and state == unknown otherwise
  /qz/telemetry/stream opens with telemetry_stream_open carrying runtime
```

## Immediate action checklist

- [ ] Inspect current `/status` implementation and model/context source paths.
- [x] Inspect `qz-top` token math and data source.
- [x] Inspect `qz-thoughts` input source and rendering loop.
- [x] Identify every monitor fallback that still treats files/logs as live truth.
- [x] Define first `/qz/telemetry/stream` SSE fan-out contract.
- [x] Keep JSON/JSONL telemetry as optional replay/debug output only.
- [x] Convert `qz-top` to structured telemetry.
- [ ] Add backend/proxy VRAM snapshot telemetry for qz-top `USED`/`BASE`/cache/buffer split.
- [x] Convert `qz-thoughts` to structured stream events.
- [x] Fix first-pass `qz-top` TPS sanity.
- [x] Add concurrent monitor smoke test.
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
