# Native Tool Advisory Policy

**Issue:** h4rm0n1c/quantzhai#61 — Add advisory policy for native exec/tool use patterns
**Slice:** A (design/audit only — no implementation)
**Status:** Design complete. Implementation begins in Slice B.

**QuantZhai commit at design start:** 3670821
**Codex repo path:** `/tmp/qz-audit/codex`
**Codex audit SHA:** `46f30d02828bd4c52827e5f0482a6f2a982cce5b`

---

## 1. Codex Source Audit

### Repo and SHA

```text
path: /tmp/qz-audit/codex
SHA:  46f30d02828bd4c52827e5f0482a6f2a982cce5b
date: 2026-05-26 (same SHA as #66–#70 prior audits; no advance)
```

### Files checked for this design

```text
codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs
codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs
codex-rs/core/src/tools/handlers/shell/shell_command.rs
codex-rs/core/src/tools/handlers/shell/shell_handler.rs
codex-rs/core/src/tools/handlers/shell/container_exec.rs
codex-rs/core/src/tools/handlers/shell_spec.rs
codex-rs/core/src/tools/handlers/request_permissions.rs
codex-rs/core/src/tools/handlers/plan.rs
codex-rs/core/src/tools/handlers/goal/
codex-rs/core/src/tools/handlers/view_image.rs
codex-rs/protocol/src/models.rs
codex-rs/codex-api/src/sse/responses.rs
```

### Tool classification table

Scope column decides whether #61 advisory design should cover the tool.

| Codex tool name | Codex source file | Item kind | Payload kind | Item/event shape | QuantZhai handling | Advisory in-scope? | Reason |
|---|---|---|---|---|---|---|---|
| `exec_command` | `handlers/unified_exec/exec_command.rs` | `function_call` | `ToolPayload::Function { arguments }` | `output_item.added → function_call_arguments.delta × N → function_call_arguments.done → output_item.done` | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` | **Yes** | Primary execution tool; generates most command-loop patterns. `cmd` field observable. |
| `write_stdin` | `handlers/unified_exec/write_stdin.rs` | `function_call` | `ToolPayload::Function { arguments }` | Same as exec_command | Native pass-through | **Yes** | `session_id` field enables loop detection per-session. A stuck interactive process produces repeated write_stdin to same session. |
| `shell_command` | `handlers/shell/shell_command.rs` | `function_call` | `ToolPayload::Function { arguments }` (`command: String`) | Same as exec_command | Native pass-through | **Yes** | Shell-style exec. Single `command` string field. Same failure-loop pattern as exec_command. |
| `shell` | `handlers/shell/shell_handler.rs` | `function_call` | `ToolPayload::Function { arguments }` (`command: Vec<String>`) | Same as exec_command | Native pass-through (issue #68) | **Cautious** | Not advertised in QuantZhai's `shell_command` config. Unlikely to be called. In-scope in principle but lower priority. |
| `container.exec` | `handlers/shell/container_exec.rs` | `function_call` | `ToolPayload::Function { arguments }` (`command: Vec<String>`) | Same as exec_command | Native pass-through (issue #68) | **Cautious** | Never advertised, fallback only. Advisory applicable if ever called. Same pattern as `shell`. |
| `request_permissions` | `handlers/request_permissions.rs` | `function_call` | `ToolPayload::Function { arguments }` | Same as exec_command | Native pass-through (issue #67) | **Cautious — escalation only** | Escalation already observed via `tool_escalation_requested` (on outgoing exec with `sandbox_permissions=require_escalated`). `request_permissions` itself is separate. Needs live evidence before advisory. |
| `update_plan` | `handlers/plan.rs` | `function_call` | `ToolPayload::Function { arguments }` | Same as exec_command | Native pass-through (issue #67) | **Out of scope** | No looping risk; used once per plan revision. Not an execution pattern. |
| `request_user_input` | `handlers/request_user_input.rs` | `function_call` | `ToolPayload::Function { arguments }` | Same as exec_command | Native pass-through (issue #67) | **Out of scope** | Pauses for user input. Repetition is user-driven, not a proxy concern. |
| `view_image` | `handlers/view_image.rs` | `function_call` | `ToolPayload::Function { arguments }` (`path`, `detail`) | Same as exec_command | Native pass-through (issue #67) | **Out of scope** | Not execution. Repeated image reads are benign. |
| `get_goal` | `handlers/goal/get_goal.rs` | `function_call` | `ToolPayload::Function {}` (no args) | Same as exec_command | Native pass-through (issue #67) | **Out of scope** | Goal management. No write/exec risk. |
| `create_goal` | `handlers/goal/create_goal.rs` | `function_call` | `ToolPayload::Function { arguments }` | Same as exec_command | Native pass-through (issue #67) | **Out of scope** | Goal management. Advisory adds no value. |
| `update_goal` | `handlers/goal/update_goal.rs` | `function_call` | `ToolPayload::Function { arguments }` | Same as exec_command | Native pass-through (issue #67) | **Out of scope** | Goal management. Advisory adds no value. |
| `apply_patch` | `handlers/apply_patch.rs` | `custom_tool_call` | Freeform patch body (string) | `output_item.added → custom_tool_call_input.delta × N → output_item.done` | Protocol adapter (issue #66) | **Cautious — write-count only** | Not exec; Codex executes file write. High volume of apply_patch in one turn is a signal. However, coercion telemetry already covers error paths. Write-count advisory is cautious. |
| `local_shell` | `handlers/shell/local_shell.rs` | **`local_shell_call`** | `ToolPayload::LocalShell` | `LocalShellCall` ResponseItem (not function_call) | **Not in CODEX_NATIVE_TOOL_NAMES** (issue #69) | **Out of scope** | Dedicated item type, no proxy adapter. If model calls `local_shell` as function_call, proxy returns unsupported-tool error. Advisory not warranted without an adapter. |
| `web_search` | `protocol/src/models.rs` | `web_search_call` | `WebSearchAction` | `output_item.added / output_item.done` (item.type=web_search_call) | Proxy-local execution | **Out of scope** | Already has budget enforcement and `web_search_budget_exceeded` telemetry. Advisory for native tools does not extend to web_search. |

**Advisory tool target set for #61 Slice B:**

```text
Primary (sufficient source evidence + QuantZhai handling + observable pattern):
  exec_command      — cmd field; failure exit codes observable
  write_stdin       — session_id field; per-session repetition observable
  shell_command     — command field; same failure-loop pattern as exec_command

Secondary (in scope if evidence presents; lower priority):
  shell             — same as exec_command/shell_command; not advertised in QuantZhai config
  container.exec    — same as shell; never advertised

Cautious/out-of-scope for Slice B:
  apply_patch       — write-count advisory only, not failure loop
  request_permissions — no proxy-side observation of response (Codex-side gate)
  All goal tools, update_plan, request_user_input, view_image — no loop/exec risk
  local_shell       — no proxy adapter
  web_search        — separate budget enforcement system
```

---

## 2. Current Advisory Paths

### 2.1 `render_advisory_output(call, message)` — `proxy/qz_feedback.py:78`

Builds a `function_call_output` item with plain-text `output` field. Not a JSON error — advisory text the model can use or ignore.

```python
{
    "type": "function_call_output",
    "call_id": call["call_id"],   # matched to original call
    "output": message,            # plain text advisory
}
```

- **Model-visible:** Yes — function_call_output in model's context
- **Blocking:** No — call is NOT executed; the advisory result is injected instead of the normal output
- **Scope:** Per-call; fires once per repeated-read path per turn (warned_paths guards)

**Clarification on "blocking":** The current repeated-read signal does NOT allow the original tool call to execute. The advisory output replaces it. This is the correct pattern for #61 advisories too — inject the advisory as the call result; Codex sees the advisory and decides whether to retry, skip, or change approach. This is advisory-only in the sense that no hard blocking occurs at the system level, but the individual call's output is replaced.

### 2.2 `repeated_read_signal(call, state)` — `proxy/qz_file_signal.py:224`

- **Input:** Outgoing `function_call` item + `RepeatedReadState`
- **Detection:** Checks if command parses to a read command (cat/head/tail/grep/rg/sed/etc.) targeting a path already in `state.read_paths`
- **State:** `RepeatedReadState` — `read_paths`, `written_paths`, `warned_paths`, `history_read_paths`
- **Returns:** `RepeatedReadDecision(should_signal, message, paths, action, scope)`

### 2.3 `CompletedToolCallDecision(kind="signal")` — `proxy/qz_tool_lifecycle.py:11`

When `completed_call_decision()` returns `kind="signal"`:
- `signal_result`: the `function_call_output` advisory dict (from `render_advisory_output`)
- `signal_metadata`: telemetry payload dict

### 2.4 `qz_responses_stream.py` signal handling — line 1928

```python
if decision.kind == "signal":
    hs.next_input.append(decision.signal_result)  # advisory into model context
    hs.signal_injected = True
    self._emit("repeated_read_signal", decision.signal_metadata or {})
    # break — no public Codex lifecycle event
```

- **Original tool call:** NOT passed through (no `output_item.added/done` emitted)
- **Delay:** None — advisory fires synchronously in the hop loop
- **Model-visible:** Yes — via `next_input` in the next hop
- **Operator-visible:** Yes — via `repeated_read_signal` telemetry event

### 2.5 Router signal handling — `proxy/qz_request_router.py:2624`

```python
next_input.append(rr_decision.signal_result)
self._emit("repeated_read_signal", {**(rr_decision.signal_metadata or {}), "request_id": request_id})
```

Same pattern in non-streaming path.

### 2.6 Existing telemetry for native tool outputs

Separate from the advisory system: `proxy/qz_native_tool_output.py`

- `tool_sandbox_denied` — fires when `function_call_output` contains "Read-only file system"
- `tool_connection_failed` — fires when output contains "Connection refused"
- Both are **operator-only** (telemetry); NOT injected into model context
- Exit codes parsed via `_parse_exit_code()` from the `"Process exited with code N"` envelope

**Key observation:** Native tool exit codes ARE observable at the proxy, but only on the **incoming side** (next request's `input` array containing `function_call_output` items). The proxy processes them in `classify_native_tool_outputs()`. This is the right place to detect repeated failures.

### 2.7 Escalation telemetry — `proxy/qz_responses_stream.py:795`

`tool_escalation_requested` fires on **outgoing** exec calls where `sandbox_permissions == "require_escalated"`. This already exists and fires before the call passes through. Payload: `tool`, `call_id`, `sandbox_permissions`, `justification` (200-char preview), `cmd_preview` (80-char preview).

### 2.8 Current tests

| Test class | File | Coverage |
|---|---|---|
| `RepeatedReadSignalTests` | `tests/test_qz_file_signal.py` | `repeated_read_signal()` end-to-end |
| `CompletedCallDecisionTests` | `tests/test_qz_proxy_tools.py` | `kind="signal"` path |
| `RepeatedReadStreamingTests` | `tests/test_qz_responses_stream.py` | Stream emission, telemetry |
| `FeedbackRenderTests` | `tests/test_qz_feedback.py` | `render_advisory_output()` |

---

## 3. Native Tool Surface

### 3.1 `CODEX_NATIVE_TOOL_NAMES` — source: `proxy/qz_tools.py:47`

```python
CODEX_NATIVE_TOOL_NAMES = frozenset({
    "exec_command",      # cmd: str; workdir, shell, tty, yield_time_ms, max_output_tokens
                         # sandbox_permissions: use_default | with_additional_permissions | require_escalated
    "write_stdin",       # session_id: int (required); chars: str; yield_time_ms, max_output_tokens
    "shell_command",     # command: str; sandbox_permissions; justification; prefix_rule
    "update_plan",       # UpdatePlanArgs
    "request_user_input",# RequestUserInputArgs
    "request_permissions",# RequestPermissionsArgs
    "view_image",        # path, detail
    "get_goal",          # no args
    "create_goal",       # objective, token_budget
    "update_goal",       # status
    "shell",             # command: Vec<String>; sandbox_permissions; justification; prefix_rule
    "container.exec",    # command: Vec<String>; same as shell
})
```

Confirmed at audit SHA `46f30d02828bd4c52827e5f0482a6f2a982cce5b`. See `docs/codex-source-tool-inventory.md`.

**CODEX_NATIVE_TOOL_NAMES is a routing inventory, not an authority and not an immutability
boundary.** Tools in this set may acquire in-transit handling (observation, telemetry,
advisory signals) when justified by Codex source, runtime evidence, and tests.
The native advisory signals added in #61 are exactly this type of justified in-transit
handling. See `docs/codex-tool-parity-and-proxy-policy.md` for the full policy.

### 3.2 Argument fields observable by the proxy

The proxy reads arguments from outgoing `function_call` items. Relevant fields:

| Tool | Advisory-relevant field | Type | Advisory use |
|---|---|---|---|
| `exec_command` | `cmd` | str | Command hash, cmd_preview for advisory wording |
| `exec_command` | `sandbox_permissions` | str | Escalation counting |
| `shell_command` | `command` | str | Command hash |
| `shell_command` | `sandbox_permissions` | str | Escalation counting |
| `write_stdin` | `session_id` | int | Per-session loop counting |
| `write_stdin` | `chars` | str | (hash only; do not store raw) |
| `shell` | `command` | Vec\<String\> (JSON array) | Command hash |
| `container.exec` | `command` | Vec\<String\> (JSON array) | Command hash |

### 3.3 Exit code observable path

Exit codes are visible on the **incoming side** (prior hops' results as `function_call_output` items in the next request's `input` array). `_parse_exit_code()` in `qz_native_tool_output.py` already handles this. The advisory logic must read these to detect repeated failures.

### 3.4 Excluded tools and why

| Tool | Excluded reason |
|---|---|
| `apply_patch` | Protocol adapter, not native pass-through; handled by coercion telemetry; #62 covers advisory for borderline coercion |
| `web_search` | Proxy-local; separate budget enforcement |
| `local_shell` | Not in `CODEX_NATIVE_TOOL_NAMES`; no proxy adapter yet |
| `update_plan` | No exec/write risk |
| `request_user_input` | Pauses for user; no looping concern |
| `view_image` | No exec/write risk |
| Goal tools | No exec/write risk |
| MCP tools | Out of scope for QuantZhai |
| Multi-agent tools | Out of scope |

---

## 4. Candidate Advisory-Only Patterns

All patterns are advisory-only. No blocking. No automatic retry. Original call is replaced by advisory result (same as repeated-read signal); model decides next action.

### Pattern A: Repeated failing commands

**Trigger:** The same or similar command fails multiple times without a successful intervening change to address the failure. Specifically: same command signature produces non-zero exit code N≥2 consecutive times (threshold configurable).

**Observable:** Exit codes from `function_call_output` items in incoming request input. Command args from outgoing `function_call` items. Signature = hash of normalised command string.

**Advisory wording:**
> "The command `<tool>` has failed multiple times with the same error pattern. Consider inspecting the error output, checking whether the prerequisite conditions exist, or trying a different approach."

**What "advisory only" means here:** The current call is allowed to pass through on the first N-1 failures. On the Nth failure (or when the condition is met on an incoming call), the advisory fires for the NEXT attempt with that signature.

**Design note:** This pattern fires at request entry time (when incoming results are scanned), not at outgoing call time. The state from prior hops within the same turn carries the failure history.

---

### Pattern B: Tool loop / excessive native calls

**Trigger:** Total native tool call count in one turn exceeds `max_native_tool_calls_per_turn`. OR: the same tool is called with near-identical argument signature more than `repeated_same_tool_args_threshold` times.

**Observable:** Outgoing `function_call` items counted per turn. Argument signatures hashed.

**Advisory wording (total count):**
> "You have made many tool calls in this turn. Consider summarising progress so far or changing strategy before continuing."

**Advisory wording (repeated same args):**
> "You have called `<tool>` multiple times with similar arguments. If earlier calls did not work, consider checking the error output and trying a different approach."

**Warn once per signature per turn.** A second advisory for the same tool/signature is suppressed.

---

### Pattern C: Excessive write operations

**Trigger:** Total `apply_patch` calls in one turn exceeds `write_operation_threshold`.

**Observable:** Outgoing calls with `name == "apply_patch"` counted per turn (already in stream loop; use hop accumulator).

**Advisory wording:**
> "You have written many files in this turn. Before continuing, confirm the overall intent and whether each change is necessary."

**Note:** This is a very gentle threshold (default 10). The model may legitimately write many files in one turn. The advisory is informational only.

---

### Pattern D: write_stdin loop (stuck interactive process)

**Trigger:** `write_stdin` called to the same `session_id` more than `write_stdin_same_call_threshold` times within one turn, suggesting the model may be stuck trying to interact with a process that is not responding.

**Observable:** Outgoing `function_call` items with `name == "write_stdin"` parsed for `session_id`. Count per session_id per turn.

**Advisory wording:**
> "You have sent multiple inputs to the same session. If the process is not responding as expected, consider checking whether it is still running or trying a different approach."

---

### Pattern E: Sandbox/escalation retries

**Trigger:** `sandbox_permissions == "require_escalated"` appears on more than `escalation_retry_threshold` distinct outgoing exec calls within one turn.

**Observable:** `_check_sandbox_escalation()` already parses this in `qz_responses_stream.py` and emits `tool_escalation_requested` telemetry. The advisory state can count these.

**Condition for advisory:** Only if `escalation_retry_threshold` is exceeded. Single escalation requests are normal. Repeated escalation requests within one turn suggest the model is not understanding that permission is not being granted.

**Advisory wording:**
> "Multiple escalation requests have been made in this turn. If elevated permissions are needed for the task, explain the specific requirement to the user before proceeding."

**Important caveat:** The proxy can observe outgoing escalation requests but NOT whether they were denied. Codex handles the sandbox grant/deny. This advisory fires on repeated escalation attempts regardless of grant status. Threshold should be conservative (≥2 or ≥3) to avoid false positives on legitimate multi-step escalated workflows.

---

## 5. Threshold / Config Proposal

### Decision: hard-coded defaults with environment variable overrides

**Rationale:** The repo uses env-var overrides for similar per-request operational parameters (e.g. `QZ_OUTPUT_TEXT_ARTIFACT_SCAN_LIMIT`, `QZ_REQUIRE_GPU`). This pattern is already established and keeps the config surface small. No new config file is needed for initial implementation.

Building a dedicated `config/default/tool_policy.json` would require a parser, a config-load path, and hot-reload semantics. The advisory system does not need that complexity in Slice B. Env overrides are sufficient for the operator to tune thresholds without code changes.

### Proposed initial thresholds

| Constant | Default | Env override | Rationale |
|---|---|---|---|
| `QZ_NATIVE_FAIL_REPEAT_THRESHOLD` | 3 | `QZ_NATIVE_FAIL_REPEAT_THRESHOLD` | After 3 failures with the same command signature in one turn, the model likely needs a warning to change approach. |
| `QZ_NATIVE_MAX_CALLS_PER_TURN` | 24 | `QZ_NATIVE_MAX_CALLS_PER_TURN` | High-volume native tool use (24+) often indicates an infinite loop or agent confusion. |
| `QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD` | 4 | `QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD` | Calling the same tool with identical arguments 4 times in a turn is a clear repetition signal. |
| `NATIVE_ADVISORY_WRITE_THRESHOLD` | 10 | `QZ_NATIVE_WRITE_THRESHOLD` | 10 `apply_patch` calls is already a lot for normal tasks. Default is permissive to avoid noise. |
| `NATIVE_ADVISORY_WRITE_STDIN_SESSION_THRESHOLD` | 3 | `QZ_NATIVE_WRITE_STDIN_SESSION_THRESHOLD` | 3 write_stdin calls to the same session without a new exec_command between them suggests a stuck interaction. |
| `NATIVE_ADVISORY_ESCALATION_THRESHOLD` | 2 | `QZ_NATIVE_ESCALATION_THRESHOLD` | 2 escalation requests in one turn is unusual. Conservative to avoid false positives. |

All thresholds are loaded once at proxy startup. If an env var is unset, the constant default applies.

---

### Troubleshooting

- If advisories trigger during legitimate large refactors, raise `QZ_NATIVE_MAX_CALLS_PER_TURN`.
- If repeated useful probes trigger warnings, raise `QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD`.
- If the agent keeps retrying identical failing commands before being warned, lower `QZ_NATIVE_FAIL_REPEAT_THRESHOLD`.

---

## 6. Signal State Model

### Design principle

Follow the pattern of `RepeatedReadState` in `qz_file_signal.py`: a plain Python dataclass, per-turn (not persisted), seeded from input history, mutable during the hop loop.

The new state should live in a new module (e.g. `proxy/qz_native_signal.py`), parallel to `qz_file_signal.py`. The `completed_call_decision()` method in `ProxyLocalToolRegistry` (or a new parallel function) would accept it alongside `repeated_read_state`.

### Proposed shape

```python
@dataclass
class NativeToolAdvisoryState:
    # Per-turn call counts
    native_call_count: int = 0          # total native tool calls this turn

    # Failure tracking
    # Key: command_signature (hash), Value: consecutive failure count
    command_failure_counts: dict[str, int] = field(default_factory=dict)
    # Key: command_signature, Value: last exit code observed
    command_last_exit_code: dict[str, int] = field(default_factory=dict)

    # Repeated-args tracking
    # Key: (tool_name, arg_signature), Value: call count
    tool_arg_call_counts: dict[tuple, int] = field(default_factory=dict)

    # Write-count tracking
    apply_patch_count: int = 0          # apply_patch calls this turn

    # write_stdin session tracking
    # Key: session_id (int), Value: consecutive write_stdin count without new exec
    write_stdin_session_counts: dict[int, int] = field(default_factory=dict)

    # Escalation tracking
    escalation_count: int = 0           # require_escalated requests this turn

    # Dedup guard — warn once per signature per turn
    # Set of (tool_name, advisory_kind, signature) that have already been warned
    warned_signatures: set[tuple] = field(default_factory=set)
```

### State lifecycle

```text
Request arrives
    │
    ▼
NativeToolAdvisoryState created (empty for this turn)
    │
    ▼ (optional: seed from prior hops' function_call_output items)
advisory_state.ingest_prior_results(input_items)
    │
    ▼
Per outgoing call in stream loop:
    advisory_state.observe_outgoing_call(call)
    → increments native_call_count
    → hashes args, updates tool_arg_call_counts
    → updates apply_patch_count, write_stdin_session_counts
    → checks escalation
    advisory = advisory_state.check_advisories(call)
    if advisory is not None:
        emit kind="signal" with advisory
    else:
        pass through normally
    │
    ▼
Per incoming result batch (next hop):
    advisory_state.ingest_results(function_call_output_items)
    → parses exit codes, updates command_failure_counts
```

### Cross-hop state

The state is per-turn (one `NativeToolAdvisoryState` per request). It persists across hops within the turn (like `repeated_read_state` which is also per-turn). There is no cross-turn persistence in this issue.

---

## 7. Signature Safety

### Principle

Do not store or emit raw command bodies, raw file paths, or raw argument content in operator telemetry. The advisory wording shown to the model may name the tool but should not dump raw telemetry data.

### Safe signature functions

```python
def command_signature(call: dict) -> str:
    """Safe signature for exec_command/shell_command.

    Does NOT store the raw command. Returns a stable hash of the normalised
    command string. Two similar commands produce different signatures;
    the same command repeated produces the same signature.
    """
    # Extract cmd or command field
    args = _parse_args(call)
    cmd_str = args.get("cmd") or args.get("command") or ""
    if isinstance(cmd_str, list):
        cmd_str = " ".join(str(t) for t in cmd_str)
    # Normalise: strip leading/trailing whitespace, collapse internal spaces
    normalised = " ".join(cmd_str.split())
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def arg_signature(call: dict) -> tuple[str, str]:
    """Safe (tool_name, arg_hash) signature.

    Used for detecting repeated calls with same args regardless of tool.
    """
    tool_name = call.get("name") or "unknown"
    args = call.get("arguments") or ""
    if isinstance(args, dict):
        args = json.dumps(args, sort_keys=True)
    arg_hash = hashlib.sha256(args.encode()).hexdigest()[:12]
    return (tool_name, arg_hash)
```

### Telemetry payload safety rules

For any new advisory telemetry event (e.g. `native_tool_advisory`):

```python
{
    "tool": tool_name,               # safe: fixed vocabulary
    "call_id": call_id_str,          # safe: opaque identifier
    "advisory_kind": "...",          # safe: enum value
    "command_signature": "abc123",   # safe: hash, not raw command
    "exit_code_class": "nonzero",    # safe: class, not raw code
    "call_count": N,                 # safe: integer
    "warn_count": N,                 # safe: integer
    # NOT INCLUDED:
    #   raw cmd string
    #   raw arguments JSON
    #   raw file paths
    #   raw process output
}
```

Model-visible advisory wording:
- Names the tool (allowed — model already knows which tool it called)
- Describes the pattern (e.g. "failed multiple times")
- Does NOT echo raw command text back to the model
- Does NOT include operator telemetry fields

### Exit code handling

Exit codes are integers. Storing the raw exit code in advisory state is safe (it is a number, not a path or command). Advisory wording can say "non-zero exit" without revealing the specific exit code if desired.

---

## 8. Model-Visible Advisory Wording

Rules:
- Short (1–2 sentences)
- Actionable — tells model what to do next
- Not scolding — describes the pattern, suggests action
- Does not reveal hidden telemetry data
- Fires once per signature/pattern per turn (warned_signatures guard)

### Pattern A: Repeated failing commands

```text
"The command '{tool}' has failed {n} consecutive times with the same pattern.
Consider checking the error output from the last attempt, or try a different approach."
```

(where `{tool}` is the tool name, `{n}` is the failure count)

### Pattern B: Excessive call count

```text
"You have made {n} tool calls in this turn. Consider summarising your progress and
the current state before making further calls."
```

### Pattern B2: Repeated same-args

```text
"'{tool}' has been called with the same arguments {n} times. If the previous
attempts did not succeed, inspect the output from the last attempt before retrying."
```

### Pattern C: Excessive writes

```text
"You have applied {n} patches in this turn. Before continuing, verify that each
change was needed and the current state is what you expect."
```

### Pattern D: write_stdin loop

```text
"'{tool}' has sent input to session {session_id} {n} times. If the process is
not responding, check whether it is still active before sending more input."
```

### Pattern E: Escalation retries

```text
"Multiple sandbox permission requests have been made in this turn. If elevated
permissions are required, explain the specific requirement to the user before retrying."
```

---

## 9. Non-Goals

This issue and all derived slices explicitly exclude:

- **No blocking of tool calls.** The advisory replaces the individual call's output, but does not prevent Codex from executing the next call in the same pattern.
- **No automatic retries.** The proxy never retries a call on the model's behalf.
- **No BrainCase.** Advisory state is per-turn in-memory. No SQLite writes.
- **No cross-session identity.** State resets on each request.
- **No apply_patch changes.** #62 handles apply_patch coercion advisory specifically.
- **No web_search changes.** web_search has its own budget enforcement.
- **No Codex protocol changes.** Advisories use the existing `function_call_output` + `kind="signal"` path.
- **No raw command/path leakage in operator telemetry.** Signatures are hashed.
- **No changes to `CODEX_NATIVE_TOOL_NAMES`.** The set is correct and frozen at 12 tools.
- **No changes to existing advisory paths.** The repeated-read signal is unchanged.

---

## 10. Implementation Slices

### Slice B (next: implement detection and advisories)

Implement `NativeToolAdvisoryState` and the primary advisory detection functions.

**Scope:**
- New module `proxy/qz_native_signal.py`:
  - `NativeToolAdvisoryState` dataclass
  - `command_signature(call)`, `arg_signature(call)` safe hash helpers
  - `observe_outgoing_call(state, call)` — counts calls, hashes args
  - `ingest_prior_results(state, input_items)` — parses exit codes from prior function_call_output items
  - `check_native_advisories(call, state, thresholds)` — returns `NativeAdvisoryDecision(should_signal, message, metadata)` or None
- Hook into `completed_call_decision()` in `proxy/qz_proxy_tools.py`:
  - Accept `NativeToolAdvisoryState | None` alongside `repeated_read_state`
  - Check native advisories when `name in CODEX_NATIVE_TOOL_NAMES`
  - Return `kind="signal"` with advisory output and metadata when triggered
- Hook into `qz_responses_stream.py`:
  - Pass `NativeToolAdvisoryState` through the hop loop (like `repeated_read_state`)
  - Call `ingest_prior_results()` at hop start from input items
- New telemetry event: `native_tool_advisory` (add to `REQUEST_LIFECYCLE_EVENT_TYPES` in `qz_telemetry.py`)

**Patterns in Slice B:**
- Pattern A: repeated failing commands (primary)
- Pattern B: excessive call count per turn
- Pattern B2: repeated same-tool same-args

**Tests in Slice B:**
- Unit tests for `command_signature()` and `arg_signature()` safety (no raw command in output)
- Unit tests for `observe_outgoing_call()` state transitions
- Unit tests for `ingest_prior_results()` exit code parsing
- Unit tests for `check_native_advisories()` for each Pattern A/B/B2 threshold
- Integration tests via `test_qz_proxy_tools.py`: `kind="signal"` returns for each pattern
- Integration tests via `test_qz_responses_stream.py`: advisory injected, telemetry emitted
- Dedup guard tests: advisory fires once per signature per turn

**Non-regressions:** All 3641 existing tests must continue to pass.

---

### Slice C (write-count, write_stdin, escalation)

Implement write-count, write_stdin session loop, and escalation advisory if source evidence supports them.

**Prerequisites:** Slice B merged and green. At least one live `apply_patch`-heavy session or write_stdin capture to validate thresholds. Escalation advisory requires a live escalation-retry capture.

**Scope:**
- Extend `NativeToolAdvisoryState` with `apply_patch_count`, `write_stdin_session_counts`, `escalation_count`
- Extend `observe_outgoing_call()` to track these
- Extend `check_native_advisories()` for Patterns C/D/E
- Tests for each pattern

---

### Slice D (docs, stocktake, live smoke)

**Scope:**
- Update `docs/agent-infrastructure-implementation-stocktake.md`
- Update `docs/tool-policy-audit.md` §3.4 (native tool advisory now has implementation)
- Optionally update `docs/signal-feedback-subsystem-plan.md`
- Live smoke if useful (qz-smoke-native-advisory script or manual capture)

---

## 11. Relationship to Existing Advisory Infrastructure

### How this fits into `completed_call_decision()`

The current decision chain in `proxy/qz_proxy_tools.py` step 4 (native tool path):

```python
# 4. Known Codex-native tool
if name in CODEX_NATIVE_TOOL_NAMES:
    if repeated_read_state is not None:
        rr_decision = repeated_read_signal(call, repeated_read_state)
        if rr_decision.should_signal:
            ...
            return CompletedToolCallDecision(kind="signal", ...)
    return CompletedToolCallDecision(kind="public", ...)
```

After Slice B, this becomes:

```python
# 4. Known Codex-native tool
if name in CODEX_NATIVE_TOOL_NAMES:
    if repeated_read_state is not None:
        rr_decision = repeated_read_signal(call, repeated_read_state)
        if rr_decision.should_signal:
            ...
            return CompletedToolCallDecision(kind="signal", ...)
    if native_advisory_state is not None:
        nat_decision = check_native_advisories(call, native_advisory_state, thresholds)
        if nat_decision is not None and nat_decision.should_signal:
            advisory = render_advisory_output(call, nat_decision.message)
            return CompletedToolCallDecision(
                kind="signal", call=call,
                signal_result=advisory,
                signal_metadata=nat_decision.metadata,
            )
    return CompletedToolCallDecision(kind="public", ...)
```

The signal result shape is identical to the repeated-read path. The stream and router handling (`kind="signal"`) requires no changes.

### Telemetry

Add `native_tool_advisory` to `REQUEST_LIFECYCLE_EVENT_TYPES` in `qz_telemetry.py`. The event is emitted alongside the `kind="signal"` return, parallel to how `repeated_read_signal` is emitted today.

---

## 12. Slice C.0 — Evidence and Design Refresh (2026-05-26)

**Codex audit SHA:** `46f30d02828bd4c52827e5f0482a6f2a982cce5b` (unchanged; no advance)
**QuantZhai commits at this pass:** ef5b5ff (Slice B.1 hardening, immediately prior)

**Outcome:** A — design/evidence pass only. No runtime changes.

---

### 12.1 Live Evidence Review

Nine request captures from `var/captures/requests/` were inspected.

| Pattern | Evidence found | Notes |
|---|---|---|
| C: excessive apply_patch | **None** | All 9 captures have 0–2 function_call items. No high-volume apply_patch session observed. |
| D: write_stdin loop | **None** | `write_stdin` appears in tool declarations but no `function_call` items of type `write_stdin` appear in any capture. The existing write_stdin drop mechanism (§12.4) already handles no-session abuse. |
| E: escalation retry | **Indirect** | `qz_req_1779799312343_72b0` shows a single `exec_command` with `sandbox_permissions: "require_escalated"` — a single escalation, not a loop. Followed by `qz_req_1779799316831_7040` where the permissions context says "Approval policy is currently never" yet the escalated call appears in history. Confirms the pattern is real; confirms a single escalation is legitimate (threshold ≥ 2 is correct). |

No live evidence found for Patterns C or D. Pattern E has indirect evidence (the escalation path is real and the detection infrastructure is already proven).

---

### 12.2 Critical Design Constraint: apply_patch is NOT in CODEX_NATIVE_TOOL_NAMES

`apply_patch` is a protocol adapter, not a Codex-native pass-through tool. It goes through path #3
("Protocol adapter") in `completed_call_decision()`, not path #4 ("Known Codex-native tool") where
`check_native_advisories()` is invoked.

This means **Pattern C counting cannot hook into `check_native_advisories`** without additional plumbing.

Options for Pattern C:

**Option C-a:** Increment `apply_patch_count` in the protocol adapter path (#3 in
`completed_call_decision`), then check the count in `check_native_advisories` if `apply_patch` calls it.
Problem: `check_native_advisories` only runs on path #4, so this doesn't work directly.

**Option C-b:** Add a separate `check_write_advisory(call, state)` function that is called from path #3
in `completed_call_decision` when `name == "apply_patch"`.

**Option C-c:** Increment `apply_patch_count` in path #3 and check the threshold there,
returning `kind="signal"` from `completed_call_decision` directly. This is the simplest and most
self-contained change.

**Recommended:** Option C-c — keep the advisory decision inside `completed_call_decision`
just like the native advisory path. The signal return value and metadata format are identical.

```python
# In completed_call_decision path #3 (after coercion succeeds):
if native_advisory_state is not None and name == "apply_patch":
    native_advisory_state.apply_patch_count += 1
    if native_advisory_state.apply_patch_count > QZ_NATIVE_WRITE_THRESHOLD:
        kind = "excessive_write_count"
        if ("apply_patch", kind, "total") not in native_advisory_state.warned_signatures:
            native_advisory_state.warned_signatures.add(("apply_patch", kind, "total"))
            advisory = render_advisory_output(call, "...")
            return CompletedToolCallDecision(kind="signal", ...)
```

Note: this must NOT change apply_patch protocol behavior (coercion, output shaping). It inserts
purely before the `return CompletedToolCallDecision(kind="public", ...)` at the end of path #3.

**Non-goal constraint preserved:** "No apply_patch changes" in CLAUDE.md refers to
protocol/coercion behavior. The write-count advisory is orthogonal — it fires at the advisory threshold,
not on every call, and does not alter how apply_patch arguments are parsed or how output is shaped.

---

### 12.3 Pattern E: Escalation Retry — Design Ready for Slice C.1

**Observable:** `sandbox_permissions == "require_escalated"` in outgoing `exec_command` /
`shell_command` arguments. The proxy already detects this in `_check_sandbox_escalation()` in
`qz_responses_stream.py` and emits `tool_escalation_requested` telemetry.

**State field:** Add `escalation_count: int = 0` to `NativeToolAdvisoryState`.

**Detection path:** Two places need to track escalation count:
1. `seed_native_advisory_state(input_items)` — scan prior `function_call` items for
   `sandbox_permissions == "require_escalated"` and increment `state.escalation_count`.
2. `record_native_tool_call(call, state)` — when a call passes through on the native path,
   detect `require_escalated` and increment.

**Advisory check in `check_native_advisories()`:**
```python
# After existing Pattern 1 (excessive call count) check:
if state.escalation_count >= QZ_NATIVE_ESCALATION_THRESHOLD:
    kind = "repeated_escalation"
    if ("__escalation__", kind, "total") not in state.warned_signatures:
        state.warned_signatures.add(("__escalation__", kind, "total"))
        return NativeAdvisoryDecision(
            should_signal=True,
            message="Multiple sandbox permission requests have been made in this turn. "
                    "If elevated permissions are required, explain the specific requirement "
                    "to the user before retrying.",
            metadata={
                "tool_name": name,
                "advisory_reason": kind,
                "count": state.escalation_count,
                "threshold": QZ_NATIVE_ESCALATION_THRESHOLD,
            },
        )
```

**Telemetry safety:** Metadata contains only count, threshold, advisory_reason, tool_name.
No raw command, no justification text, no `cmd_preview`.

**False positive risk:** A task that legitimately needs escalation for two different commands
in the same turn (e.g. `git push` then `sudo systemctl`) would trigger the advisory at threshold 2.
This is by design — the advisory is informational only. If the threshold is too aggressive for a
workflow, operator can raise `QZ_NATIVE_ESCALATION_THRESHOLD`.

**Integration point:** The `check_native_advisories` call is at path #4 in `completed_call_decision`.
Exec commands (`exec_command`, `shell_command`, `shell`, `container.exec`) are in `CODEX_NATIVE_TOOL_NAMES`
and go through path #4. The escalation check fires on these tools when the threshold is reached.

**Dedup key:** `("__escalation__", "repeated_escalation", "total")` — once per turn, like excessive
call count.

---

### 12.4 Pattern D: write_stdin Loop — No Evidence; Hold

**Existing mitigation:** `normalize_tools_for_llamacpp` in `proxy/qz_tool_request.py` already
drops `write_stdin` from the tool declaration when there is no live exec session in request history.
This prevents the primary abuse case (model inventing session IDs or calling `write_stdin` with no
active interactive process).

**What a loop would require:**
- A live exec session must be present (otherwise write_stdin is not offered)
- The model must call `write_stdin` to the same session multiple times
- The process must not be responding

**Observable key:** `session_id` (int) from `write_stdin` arguments. Must hash the value (not
store raw). **The `chars` field (stdin content) must never appear in state or telemetry.**

**Design note — reset semantics:** Should the write_stdin count reset on a new `exec_command`?
Proposed: reset the count for a session when ANY exec_command or shell_command that is NOT
write_stdin completes successfully (exit code 0). This is conservative and avoids resetting on
failed exec attempts that themselves indicate a stuck state.

**Proposed threshold:** `QZ_NATIVE_WRITE_STDIN_SESSION_THRESHOLD` = 3 (default).
Three write_stdin calls to the same session without a successful exec between them.

**False positive risk:** Legitimate REPL interaction (Python REPL, pager, sudo password prompt)
may involve 2–3 writes to the same session. Threshold 3 is permissive but not zero-risk. If the
operator's workflow involves more REPL interaction, they should raise the threshold.

**Recommendation:** Hold for Slice C.3 until at least one live write_stdin loop capture is available.
The existing drop mechanism is sufficient protection for now.

---

### 12.5 Pattern C: Excessive Writes — No Evidence; Hold for Live Data

**Default threshold:** 10 apply_patch calls per turn (permissive; legitimate refactors may need 5–8).

**False positive risk:** Large automated refactors (rename a method across many files). Operator should
raise `QZ_NATIVE_WRITE_THRESHOLD` if needed.

**Implementation readiness:** Design is clear (Option C-c above). Code change is small (increment +
threshold check in `completed_call_decision` path #3, new field in `NativeToolAdvisoryState`).

**Recommendation:** Implement in Slice C.2, after at least one live apply_patch-heavy session confirms
the threshold is not noise-prone. Do not implement now without live evidence.

---

### 12.6 Proposed Implementation Ordering

| Slice | Pattern | Readiness | Rationale |
|---|---|---|---|
| C.1 | E: Escalation retry | Ready | Detection path proven; single new field; low false-positive risk |
| C.2 | C: Excessive writes | Needs live evidence | Design clear (Option C-c); code small; threshold unvalidated |
| C.3 | D: write_stdin loop | Needs live evidence | Drop mechanism already covers main case; reset semantics unvalidated |

Each slice is independent. C.1 does not depend on C.2 or C.3.

---

### 12.7 NativeToolAdvisoryState Fields Needed for Slice C

```python
@dataclass
class NativeToolAdvisoryState:
    # Existing fields (Slice B)
    native_call_count: int = 0
    command_failure_counts: dict[str, int] = field(default_factory=dict)
    tool_arg_call_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    warned_signatures: set[tuple[str, str, str]] = field(default_factory=set)

    # Slice C.1 addition
    escalation_count: int = 0           # require_escalated requests this turn

    # Slice C.2 addition (when live evidence supports it)
    apply_patch_count: int = 0          # apply_patch calls this turn

    # Slice C.3 addition (when live evidence supports it)
    # Key: session_id hash (NOT raw int), Value: write_stdin count without intervening exec
    write_stdin_session_counts: dict[str, int] = field(default_factory=dict)
```

**Note on session_id safety:** The session_id in `write_stdin` is an integer (process ID or
similar). While integers are lower risk than strings, they should still be hashed before use as
dict keys to avoid surfacing process-identifying information in telemetry. Use
`hashlib.sha256(str(session_id).encode()).hexdigest()[:12]` as the key.

---

### 12.8 Threshold Constants for Slice C

Add to `proxy/qz_native_signal.py`:

```python
# Slice C thresholds (added in respective slices)
QZ_NATIVE_ESCALATION_THRESHOLD = _parse_env_int("QZ_NATIVE_ESCALATION_THRESHOLD", 2)
QZ_NATIVE_WRITE_THRESHOLD = _parse_env_int("QZ_NATIVE_WRITE_THRESHOLD", 10)
QZ_NATIVE_WRITE_STDIN_SESSION_THRESHOLD = _parse_env_int("QZ_NATIVE_WRITE_STDIN_SESSION_THRESHOLD", 3)
```

All use the existing `_parse_env_int` safe helper added in Slice B.1.

---

*Created: 2026-05-26. Issue: h4rm0n1c/quantzhai#61 Slice A design.*
*Governs: #61 Slice B (implementation), Slice C (write-count/escalation), Slice D (docs/smoke).*
*Depends on: docs/codex-source-tool-contract.md, docs/codex-source-tool-inventory.md, docs/tool-policy-audit.md.*
*Slice C.0 evidence/design added: 2026-05-26. Outcome A. Codex SHA unchanged: 46f30d02828bd4c52827e5f0482a6f2a982cce5b.*
