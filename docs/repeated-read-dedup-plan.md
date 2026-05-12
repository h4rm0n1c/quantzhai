# Repeated-Read Signal — Implementation Plan

Date: 2026-05-12

Status: approved plan. Implements Option 1 from `docs/llm-signal-system.md`,
with one important refinement: the signal must seed state from the incoming
Responses history, not only from calls seen in the current proxy loop.

Review update: v1 intentionally reconstructs prior read/write state from
`body["input"]`, but does not reconstruct prior warning state. A repeated read
may therefore be signalled once per Responses request boundary. This is
acceptable for v1 because the signal is advisory and avoids hidden session
state.

This feature is deliberately named **repeated-read signal**, not "dedup". The
goal is convergence feedback for the model, not blind suppression of tool calls.

---

## 1. Problem statement

Benchmark forensics showed the model can waste large numbers of tool calls by
reading the same files repeatedly. The loop happens mostly in the tool-action
layer, not inside a single long reasoning block. Reasoning budget control can
limit depth per hop, but it cannot stop the model from repeatedly deciding to
call another tool after each `</think>`.

The proxy sees the tool history. The model only benefits from that history if
the proxy turns it into a small, timely signal.

Target behaviour:

```text
Model asks to read README.md.
Proxy sees README.md was already read earlier in this conversation/input.
Proxy injects a short advisory tool result:
  Note: you already read README.md earlier in this conversation. Use the
  existing context unless you believe the file changed.
Model should usually stop re-reading and answer from existing context.
```

The signal must be advisory. The model may still re-read if it decides the file
probably changed or the earlier context is insufficient.

---

## 2. Current tool lifecycle path

The proxy processes tool calls through two main paths.

Streaming path:

```text
proxy/qz_responses_stream.py
ResponsesStreamRuntime.run()

SSE from upstream llama.cpp
  -> StreamToolCallState.observe() detects a completed function_call
  -> proxy_tool_registry.completed_call_decision()
     -> kind="public"      Codex-native/protocol-adapter public item
     -> kind="proxy_local" proxy executes locally and continues the hop loop
     -> kind="error"       proxy injects a function_call_output upstream
```

Non-streaming path:

```text
proxy/qz_request_router.py
RequestRouter._run_responses_locally()

Upstream JSON response
  -> for each output item
  -> proxy_tool_registry.completed_call_decision()
     -> kind="public"      return public trace to client
     -> kind="proxy_local" execute locally and continue
     -> kind="error"       append function_call_output and continue
```

Central decision point:

```text
proxy/qz_proxy_tools.py
ProxyLocalToolRegistry.completed_call_decision()
```

Current decision order:

```text
1. dropped tool -> error result
2. proxy-local executor -> proxy_local or error
3. protocol adapter, e.g. apply_patch -> public or error
4. Codex-native tool -> public passthrough
5. unknown tool -> error result
```

Codex-native tools are defined in `proxy/qz_tools.py`:

```text
exec_command
write_stdin
shell_command
computer
```

For Codex-native tools, QuantZhai normally forwards the function call to Codex.
Codex executes locally and sends the function_call_output in a later request.
That means a same-run-only state set misses the main benchmark failure.

---

## 3. Key refinement: seed state from incoming input history

The original v1 plan used only per-run state. That is too weak.

Codex replays prior function calls and tool outputs in `body["input"]`. The proxy
can reconstruct much of the read/write state from the current request without a
persistent session id.

New rule:

```text
Every local Responses run starts by building RepeatedReadState from
body["input"].
```

This catches common cross-request Codex-native repeated reads because the prior
`exec_command`/`shell_command` call is usually present in the replayed input
history.

The seed pass must stay cheap:

```text
Scan only function_call and function_call_output-style items.
Skip message blobs, assistant prose, user text, and large output payload bodies.
Extract command arguments and minimal call metadata only.
```

Persistent session state is still deferred. First use what the client already
sends.

---

## 4. State object

Add a small explicit state object. Do not use loose local sets.

File:

```text
proxy/qz_file_signal.py
```

State:

```python
@dataclass
class RepeatedReadState:
    read_paths: set[str] = field(default_factory=set)
    written_paths: set[str] = field(default_factory=set)
    warned_paths: set[str] = field(default_factory=set)
```

Meanings:

```text
read_paths
  Normalised paths seen in prior or current read-like tool calls.

written_paths
  Normalised paths known to have been changed since read history. If a path was
  written, suppress repeated-read signalling for that path.

warned_paths
  Paths already warned about in this request/run. First repeat gets a signal;
  a later repeat is allowed through to avoid trapping the model in a denial
  loop.
```

Scope:

```text
Default: per request/run, seeded from incoming body["input"].
Deferred: persistent per-session cache once a stable session/conversation key is
identified.
```

Explicit v1 decision:

```text
Input-history seeding reconstructs prior successful read/write operations, not
prior warning state.

warned_paths is per-run only for v1. If the previous Responses request already
received a repeated_read_signal for README.md, a later request may signal
README.md again when the same repeated read is attempted.

That is intentional for the first patch because:
- there is no hidden persistent state
- compaction escape behaviour remains simple
- the signal is advisory, not a hard denial
- repeated signalling across request boundaries is annoying but safe

Future upgrade: parse prior repeated_read_signal tool outputs in body["input"]
and seed warned_paths from them, so a repeated read after a prior warning can be
classified as allowed_after_prior_signal across request boundaries.
```

---

## 5. Signal semantics

The signal is advisory and one-shot per path per request/run.

Decision table:

| Case | Behaviour |
|---|---|
| First observed read of path | Pass through normally; record path |
| Path already read, not written, not warned | Inject advisory result; mark warned |
| Path already read, not written, already warned | Pass through normally; emit telemetry `allowed_after_prior_signal` |
| Path was written after prior read | Pass through normally; do not warn |
| Cannot parse command safely | Pass through normally |
| Non-read command | Pass through normally |

Preferred signal text:

```text
Note: you already read README.md earlier in this conversation. Use the existing
context unless you believe the file changed.
```

For multiple paths:

```text
Note: you already read these files earlier in this conversation: README.md,
streamlink_3.sh. Use the existing context unless you believe they changed.
```

Do not say "error" in the human text. This is a feedback signal, not a failure.

Implementation may initially reuse `CompletedToolCallDecision(kind="error")`
because the stream/non-stream continuation path already handles injected
`function_call_output` items. If that route is used, document it as
**signal-over-error-path** and keep the payload clearly advisory. A cleaner
follow-up is a first-class `kind="signal"` decision.

---

## 6. Parser responsibilities

File:

```text
proxy/qz_file_signal.py
```

Functions:

```python
normalize_path(path: str) -> str
extract_command(call: dict) -> str
extract_read_paths(call: dict) -> frozenset[str]
extract_write_paths(call: dict) -> frozenset[str]
seed_repeated_read_state(input_items: list) -> RepeatedReadState
repeated_read_signal(call: dict, state: RepeatedReadState) -> RepeatedReadDecision
record_tool_call(call: dict, state: RepeatedReadState) -> None
```

Suggested decision type:

```python
@dataclass(frozen=True)
class RepeatedReadDecision:
    should_signal: bool
    message: str = ""
    paths: frozenset[str] = frozenset()
    action: str = ""  # "signalled", "allowed_after_prior_signal", "none"
    scope: str = ""   # "input_history", "current_run", "mixed"
```

Path normalisation:

```text
Use os.path.normpath().
Do not resolve symlinks.
Do not touch the filesystem.
Do not require paths to exist.
```

Command parsing:

```text
Prefer shlex.split() for simple command segments.
Use regex fallback only for common shell separators/pipes.
Do not implement a full shell parser in v1.
```

Parser precedence:

```text
1. Extract the raw command string from function_call arguments.
2. Split obvious top-level command separators/pipes with a conservative regex.
3. For each segment, try shlex.split(posix=True).
4. If shlex.split() fails or returns an unusable token list, use the regex
   fallback for known read commands.
5. If both paths are unclear, return no paths and pass the tool call through.
```

This means complex bash can produce false negatives. That is acceptable for v1.
A missed repeated read is no worse than not having the feature. A false positive
is more annoying, so prefer conservative extraction.

Read command classes:

```text
content_read:
  cat, head, tail, sed, nl, bat, xxd

search_read:
  grep, rg, awk

metadata_read:
  wc
```

Directory probing is deliberately excluded from file-read signalling:

```text
ls, find
```

Those belong to a later orientation/redundant-directory-probe signal.

Write detection v1:

```text
apply_patch create/update/delete/move paths
obvious shell redirection only if easy and low-risk
```

Do not infer local file reads or writes from `web_search` output. web_search is
not a local file tool.

---

## 7. Integration points

### 7.1 `proxy/qz_proxy_tools.py`

Change:

```text
ProxyLocalToolRegistry.completed_call_decision()
```

Add optional parameter:

```python
repeated_read_state: RepeatedReadState | None = None
```

Insert the signal check immediately before Codex-native passthrough:

```text
after protocol adapter handling
before name in CODEX_NATIVE_TOOL_NAMES
```

Reason:

```text
apply_patch/protocol adapters need their own coercion first.
Codex-native read tools are where repeated reads mostly appear.
Unknown tools should still produce normal unknown-tool errors.
```

If `repeated_read_signal()` returns `should_signal=True`, return an injected
function_call_output using the original call_id.

Implementation choice for first patch:

```text
Use existing kind="error" continuation path, but payload message must be
advisory and telemetry must identify it as repeated_read_signal.
```

Preferred later cleanup:

```text
Add CompletedToolCallDecision(kind="signal") so feedback signals are not
semantically mixed with true errors.
```

### 7.2 `proxy/qz_responses_stream.py`

Change:

```text
ResponsesStreamRuntime.run()
```

At start of run:

```python
repeated_read_state = seed_repeated_read_state(working_body.get("input") or [])
```

Pass `repeated_read_state` to `completed_call_decision()`.

After each non-signalled completed call, update state with `record_tool_call()`.

Emit telemetry through the existing request-scoped emitter when a signal fires
or a previously-warned repeat is allowed.

### 7.3 `proxy/qz_request_router.py`

Change:

```text
RequestRouter._run_responses_locally()
```

Same pattern as streaming:

```text
seed state from body["input"]
pass state to completed_call_decision()
record calls after handling
emit telemetry
```

### 7.4 `proxy/qz_telemetry.py`

Add retained lifecycle event type:

```text
repeated_read_signal
```

Payload shape:

```json
{
  "tool": "exec_command",
  "call_id": "...",
  "paths": ["README.md"],
  "action": "signalled",
  "scope": "input_history"
}
```

Allowed `action` values:

```text
signalled
allowed_after_prior_signal
```

Allowed `scope` values:

```text
input_history
current_run
mixed
unknown
```

---

## 8. Tests

### 8.1 New parser/state tests

File:

```text
tests/test_qz_file_signal.py
```

Tests:

| Test | Covers |
|---|---|
| `test_normalize_path_dot` | `./foo/bar` -> `foo/bar` |
| `test_normalize_path_dotdot` | `a/../b` -> `b` |
| `test_extract_cat_path` | `cat README.md` |
| `test_extract_cat_multiple_paths` | `cat a b` |
| `test_extract_head_with_option` | `head -n 40 README.md` |
| `test_extract_sed_read` | `sed -n '1,80p' README.md` |
| `test_extract_rg_search_path` | `rg pattern src` |
| `test_extract_grep_search_path` | `grep -R pattern src` |
| `test_skip_ls` | `ls -la` returns no file-read paths |
| `test_skip_find` | `find . -type f` returns no file-read paths |
| `test_invalid_json_args` | malformed arguments returns empty paths |
| `test_seed_from_prior_function_call` | input history seeds `read_paths` |
| `test_seed_from_prior_function_call_detects_cross_request_repeat` | prior `cat README.md` in input history makes current `cat README.md` signal |
| `test_seed_write_from_apply_patch` | apply_patch marks `written_paths` |
| `test_repeat_from_history_signals` | repeated read from seeded input signals |
| `test_repeat_after_write_does_not_signal` | written path suppresses signal |
| `test_warn_once_then_allow` | second repeat after warning allows in the same run |
| `test_prior_warning_not_seeded_v1_signals_again_across_request` | documents v1 policy: prior repeated_read_signal in input history does not seed `warned_paths` |
| `test_seed_skips_message_blobs` | input-history seeding ignores ordinary message text/content blobs |

Deferred v2 test:

```text
test_seed_warned_paths_from_prior_signal_output
  input history contains a prior repeated_read_signal for README.md
  current run attempts cat README.md
  decision is allowed_after_prior_signal
```

Do not implement this deferred test in v1 unless warning replay is implemented
now.

### 8.2 Decision tests

File:

```text
tests/test_qz_proxy_tools.py
```

Tests:

| Test | Covers |
|---|---|
| `test_repeated_read_signal_before_codex_native_passthrough` | repeat shell/exec command becomes injected signal |
| `test_first_read_codex_native_passthrough` | first read remains public |
| `test_repeat_after_warning_passthrough` | avoids infinite advisory loop |
| `test_apply_patch_unaffected` | protocol adapter path unchanged |
| `test_web_search_unaffected` | proxy-local path unchanged |
| `test_unknown_tool_unaffected` | unknown-tool error unchanged |

### 8.3 Stream/non-stream tests

Add focused tests rather than a full live smoke first:

```text
streaming path:
  seeded input history contains prior read
  model emits same read
  proxy appends advisory function_call_output upstream and continues

non-streaming path:
  same as above for _run_responses_locally()
```

Telemetry tests:

```text
repeated_read_signal retained in per-request lifecycle bucket
payload contains paths/action/scope/call_id
```

---

## 9. Edge cases

| Edge case | Handling |
|---|---|
| `./` and `../` variants | `os.path.normpath()` only |
| Symlinks | Deferred; no filesystem resolution |
| File changed after prior read | Suppress if path appears in `written_paths` |
| Model genuinely wants to re-read | First repeat gets signal; second repeat allowed in the same request/run |
| Previous request already warned about this path | v1 may signal again because `warned_paths` is not seeded from prior signal outputs |
| Multiple paths in one command | Signal if any path is repeated and not written |
| Complex bash/heredocs/process substitution | Best effort only; pass through if unclear |
| `ls`/`find` orientation waste | Deferred to separate orientation signal |
| `web_search` result mentions files | Ignore; not a local file operation |
| Compaction dropped context | Signal remains advisory; model can re-read after warning |
| No prior input history | State starts empty; current-run tracking still works |
| Missing stable session id | Not required for v1 because input replay is used |

---

## 10. Deferred work

Do not include in v1:

```text
persistent per-session read cache
full shell parser
symlink/canonical filesystem resolution
content-hash similarity detection
generic tool-call counter signal
orientation/redundant ls/find signal
web_search-derived file operation inference
proxy-side file reading
seeding warned_paths from previous repeated_read_signal outputs
first-class CompletedToolCallDecision(kind="signal") unless the error-path shim
proves too ugly
```

Future v2 ideas:

```text
session-keyed state once a reliable conversation/session identifier is found
orientation signal for repeated `pwd`, `ls /`, `ls -la`, and root-directory probes
content-hash/diminishing-return signal for similar outputs
warning replay by parsing prior repeated_read_signal outputs from body["input"]
true `kind="signal"` lifecycle path separate from `kind="error"`
```

---

## 11. Acceptance criteria

The feature is acceptable when:

```text
1. Parser/state tests pass without touching stream code.
2. Proxy decision tests prove first read passes and repeated read signals.
3. A repeated read seeded from incoming body["input"] triggers a signal.
4. A repeated read after prior warning is allowed through inside the same run.
5. A prior repeated_read_signal in body["input"] is not used to seed warned_paths
   in v1, and this behaviour is covered by an explicit policy test.
6. The seed pass scans only function_call/function_call_output-style items and
   skips ordinary message blobs.
7. apply_patch, web_search, unknown tools, and dropped-tool errors keep existing behaviour.
8. repeated_read_signal telemetry appears in /qz/telemetry/request.
9. Full unit suite remains green.
```

Live smoke is optional after unit coverage:

```text
Run a Codex task known to re-read README.md or another stable file.
Confirm the model receives the advisory signal and usually converges without a
third redundant read.
```

---

## 12. Implementation order

Recommended order:

```text
1. Add proxy/qz_file_signal.py with parser/state only.
2. Add tests/test_qz_file_signal.py.
3. Add explicit v1 warning-replay policy test.
4. Add repeated_read_signal telemetry type.
5. Thread RepeatedReadState into completed_call_decision() with no behaviour change.
6. Enable signal-over-error-path for repeated reads.
7. Add decision tests.
8. Add stream/non-stream seeded-history tests.
9. Run full suite.
10. Live smoke against a known redundant-read prompt.
```

This keeps the first patch boring and testable. Boring is good. Boring means the
ship does not turn into decorative plasma.
