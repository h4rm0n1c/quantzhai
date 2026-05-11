# Repeated-Read/Dedup Signal — Implementation Plan

Date: 2026-05-12

Status: approved. Implements Option 1 from `docs/llm-signal-system.md`.

---

## 1. Current tool lifecycle path

The proxy processes tool calls through two parallel paths:

**Streaming path** (`qz_responses_stream.py:559`, `ResponsesStreamRuntime.run()`):
```
SSE from upstream (llama.cpp)
 → tool_call_state.observe() detects completed function_call [line 770]
 → proxy_tool_registry.completed_call_decision() [qz_proxy_tools.py:179]
   → kind="public"  (Codex-native: exec_command, shell_command, etc.)
     → emit_public_tool_item() → forward to Codex via SSE → RETURN [lines 871-894]
   → kind="proxy_local"  (web_search, etc.)
     → execute(), append upstream_items to next_input → continue hop loop [800-869]
   → kind="error"  (coercion failure, dropped tool)
     → append error_result to next_input → continue hop loop [784-798]
```

**Non-streaming path** (`qz_request_router.py:765`, `_run_responses_locally()`):
```
Upstream JSON response
 → for each output item: completed_call_decision()
   → kind="public" → add to public_trace → RETURN [799-808]
   → kind="proxy_local" → execute, extend next_input → continue [819-837]
   → kind="error" → append error_result to next_input → continue [824-826]
```

**Key constraint:** For `kind="public"` (Codex-native tools — `exec_command`, `shell_command`), the proxy forwards the `function_call` to Codex and ends the stream. Codex executes locally and submits the `function_call_output` in a **new** request. The proxy never sees the tool output for Codex-native tools.

This means per-request dedup state cannot detect cross-request re-reads of Codex-native tools. Per-request state only covers proxy-local tool loops (web_search → read → search → re-read). Cross-request dedup is deferred (needs a session identifier).

---

## 2. Best insertion point

**Primary: `ProxyLocalToolRegistry.completed_call_decision()` at `qz_proxy_tools.py:179`.**

Insert a dedup check between step 3 (protocol adapter/public) and step 4 (known Codex-native), right before line 226. The existing `kind="error"` path at `qz_responses_stream.py:784-798` already handles synthetic error results: appends to `next_input`, breaks the inner while loop, and continues the hop loop. No new control flow needed.

The dedup check:
1. Call `is_read_operation(call)` — returns True for `shell_command`/`exec_command` names
2. Call `extract_read_paths(call)` — parses arguments JSON, extracts file paths from known read commands (cat, head, tail, read, less, more, bat, nl, tac)
3. Compare against `read_paths` set (passed as parameter), exclude paths in `written_paths`
4. If match found: return `CompletedToolCallDecision(kind="error", error_result=synthesize_tool_error_result(call, "Note: you already read ..."))`
5. If no match: fall through to existing logic

**State tracking** happens in the callers (`ResponsesStreamRuntime.run()` and `_run_responses_locally()`) — they update `read_paths` and `written_paths` sets after each non-dedup'd call.

---

## 3. State object needed

```python
# Two plain set[str] local variables in ResponsesStreamRuntime.run()
# near existing counters at lines 578-579:
_dedup_read_paths: set[str] = set()
_dedup_written_paths: set[str] = set()
```

Same pattern in `_run_responses_locally()` at `qz_request_router.py:779-780`.

No dataclass, no module-level state, no session cache. Reset on every `run()` / `_run_responses_locally()` call.

---

## 4. Edge cases

| Edge case | Handling |
|---|---|
| `./` and `../` variants | `os.path.normpath()` resolves `.`/`..` segments. No symlink resolution |
| Write-then-read | If path is in `written_paths`, dedup is suppressed. Track writes from `apply_patch` create_file/update_file ops and `exec_command` redirect-to-file patterns |
| Multiple files in one cmd (`cat a b`) | Extract all distinct paths; flag if ANY is a repeat |
| Pipes/redirects (`cat < a`) | Simple regex catches `cat path` but misses `< a` redirect — acceptable for v1 |
| Model ignores signal | Signal is advisory, not blocking. The error result does NOT prevent a re-request — the model sees the note and can decide. If it re-requests, the dedup fires again |
| Non-existent paths | No FS access. Just compare argument strings. Only flag for known read commands to reduce false positives |
| Non-read commands (`ls -la`) | Only flag when command starts with a known read command name |
| Context compaction-induced re-reads | Model may legitimately re-read if compaction dropped earlier content. Signal is advisory — the model can ignore. This is a known limitation per the design doc |
| Cross-request re-reads (benchmark case) | **NOT covered by v1.** Requires per-session state keyed by a session identifier (not currently available). The benchmark's `README.md × 3` spans multiple Codex requests |
| First read in a turn | No state yet → passes through normally |
| `call_id` for synthetic result | Use the original call's `call_id` so the model sees it as the result of its function_call |

---

## 5. Smallest first patch

### File 1 — NEW: `proxy/qz_file_dedup.py` (~80 lines)

```python
import json
import os
import re


READ_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more", "read", "bat", "nl", "tac", "xxd",
})


def normalize_path(p: str) -> str:
    return os.path.normpath(p)


def extract_read_paths(call: dict) -> frozenset[str]:
    name = call.get("name", "")
    if name not in {"shell_command", "exec_command"}:
        return frozenset()
    try:
        args = json.loads(call.get("arguments") or "{}")
    except (json.JSONDecodeError, TypeError):
        return frozenset()
    cmd = (args.get("cmd") or args.get("command") or "").strip()
    if not cmd:
        return frozenset()
    return _parse_read_paths(cmd)


def _parse_read_paths(cmd: str) -> frozenset[str]:
    paths = set()
    for read_cmd in READ_COMMANDS:
        pattern = re.compile(
            r'(?:^|\||;|&&)\s*' + re.escape(read_cmd) + r'\s+(\S+)',
            re.I,
        )
        for m in pattern.finditer(cmd):
            raw = m.group(1)
            raw = re.sub(r'[;|&>"\'<>]+$', '', raw)
            if not raw:
                continue
            paths.add(normalize_path(raw))
    return frozenset(paths)


def is_read_operation(call: dict) -> bool:
    return call.get("name") in {"shell_command", "exec_command"}


def dedup_decision(
    call: dict,
    read_paths: set[str],
    written_paths: set[str],
) -> tuple[bool, str, frozenset[str]]:
    if not is_read_operation(call):
        return False, "", frozenset()
    paths = extract_read_paths(call)
    if not paths:
        return False, "", frozenset()
    matched = (paths & read_paths) - written_paths
    if not matched:
        return False, "", frozenset()
    path_list = ", ".join(sorted(matched))
    return (
        True,
        f"Note: you already read {path_list} earlier in this turn.",
        matched,
    )
```

### File 2 — `proxy/qz_proxy_tools.py`: `completed_call_decision()` method

Changes:
- Add `read_paths: set[str] | None = None` and `written_paths: set[str] | None = None` parameters
- Import `dedup_decision` from `qz_file_dedup`
- Insert before line 226 (known Codex-native check):

```python
# 3b. Dedup signal for repeat reads
if (
    read_paths is not None
    and (dedup := dedup_decision(call, read_paths, written_paths or set()))
    and dedup[0]
):
    error = synthesize_tool_error_result(call, dedup[1])
    return CompletedToolCallDecision(kind="error", call=call, error_result=error)
```

### File 3 — `proxy/qz_responses_stream.py`: `ResponsesStreamRuntime.run()`

Changes:
- Import `extract_read_paths` from `qz_file_dedup`
- Add `_dedup_read_paths` and `_dedup_written_paths` local vars near line 578-579
- Pass them to `completed_call_decision()` at line 778
- After the public-tool path at line 871, update state:

```python
# Track paths for future dedup (public tool)
for p in extract_read_paths(completed_call):
    _dedup_read_paths.add(p)
```

- After proxy-local tool execution (line 849 area), track paths from web_search results that look like file operations
- After `apply_patch` protocol adapter handling, extract paths from operation arguments and add to `_dedup_written_paths`

### File 4 — `proxy/qz_request_router.py`: `_run_responses_locally()`

Same pattern as File 3 for the non-streaming path:
- Add `_dedup_read_paths`/`_dedup_written_paths` local vars near line 779-780
- Pass to `completed_call_decision()` at line 819
- Update state after processing

---

## 6. Tests to write

### NEW: `tests/test_qz_file_dedup.py`

| Test | What it covers |
|---|---|
| `test_normalize_path_dot` | `"./foo/bar"` → `"foo/bar"` |
| `test_normalize_path_dotdot` | `"a/../b"` → `"b"` |
| `test_normalize_path_absolute` | `"/a/b/../c"` → `"/a/c"` |
| `test_extract_paths_cat` | `cat README.md` → `{"README.md"}` |
| `test_extract_paths_multi_file` | `cat a b` → `{"a", "b"}` |
| `test_extract_paths_skips_non_read` | `ls -la` → `set()` |
| `test_extract_paths_piped_read` | `cat a \| head` → `{"a"}` |
| `test_extract_paths_from_function_call` | Full call dict → correct frozenset |
| `test_extract_paths_empty_args` | Missing/invalid args → `frozenset()` |
| `test_dedup_read_first_time` | Path not in read_paths → `(False, "", frozenset())` |
| `test_dedup_read_repeat` | Path in read_paths → `(True, "Note: ...", {path})` |
| `test_dedup_after_write` | Path in written_paths → `(False, "", frozenset())` |
| `test_dedup_other_write` | Different path written → dedup still fires |
| `test_dedup_skips_web_search` | web_search call → `is_read_operation=False` |

### Add to `tests/test_qz_proxy_tools.py`

| Test | What it covers |
|---|---|
| `test_completed_call_decision_dedup_fires_for_repeat_read` | shell_command with repeat path → `kind="error"`, output contains "Note: you already read" |
| `test_completed_call_decision_dedup_first_read_ok` | shell_command first read → `kind="public"` |
| `test_completed_call_decision_dedup_skips_proxy_local` | web_search → no dedup check, `kind="proxy_local"` |
| `test_completed_call_decision_dedup_skips_apply_patch` | apply_patch → no dedup check, `kind="public"` |

---

## 7. Risks and non-goals

### Risks

| Risk | Mitigation |
|---|---|
| **False positive on legitimate re-read** (post-compaction, model genuinely lost earlier content) | Signal is advisory, not blocking. The error result doesn't prevent re-read — it just appends a note. If the model insists, it can re-request (and the dedup fires again, but the model has the content) |
| **Brittle command parsing** (false negatives from complex bash like `cat <(cmd)` or `cat "$path"`) | Start with simple regex for known read commands. Missing a re-read = same as not having the feature. File an issue to expand patterns |
| **Performance overhead** | Lightweight regex + `json.loads`. Negligible next to model inference latency |
| **No symlink resolution** | Intentionally skipped. Requires FS access, adds security surface. `os.path.normpath()` only |
| **False positive: `grep cat file`** matcher fires | Unlikely to match an EXACT previously-read path. If it does, the signal is still advisory |
| **Cross-request re-read NOT detected** (benchmark's main failure mode) | Explicit deferred item. Per-request state cannot span Codex's tool-call→result→next-request cycle. Fix requires a session identifier contract between Codex and proxy |

### Non-goals for v1

- Cross-request/session path tracking (needs session ID contract — deferred)
- Full bash lexer/parser
- Symlink resolution
- Content-hash dedup (Option 3 from signal doc)
- Call blocking (advisory only — model can always re-request)
- Generic tool-call counter signal (Option 2 — separate feature)
- Proxy-side file reading
