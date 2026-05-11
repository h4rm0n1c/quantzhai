# Conversation History Integrity Audit Plan

Date: 2026-05-11

## Purpose

Two related open questions about what the model actually sees in conversation
history versus what it should see. Both require capture evidence before any
code changes.

---

## Item 1: Tool history filter gaps

`ToolHistoryReplayFilter` in `proxy/qz_tool_lifecycle.py` drops malformed
`function_call` + `parse_error_output` pairs before upstream replay. This is
correct for the known cases it was built to handle. However:

- Are there other malformed history patterns that get dropped silently without
  the model knowing?
- Is there a case where the filter drops a call/result pair that the model
  expects to be present, causing it to re-attempt a tool call it already
  ran successfully?
- Does the filter handle all the new item types added by the coercion system
  (`function_call_output` error injections, etc.)?

**Required:** Run a session with the filter's drop logic instrumented. Check
what gets dropped and whether the model's subsequent behaviour indicates
missing history. Inspect captures of sessions with multiple tool calls to
verify the input items upstream match the model's apparent belief about its
prior actions.

**Risk level:** Low — the filter was written for a specific known bad pattern.
Unexpected gaps would show as the model retrying actions it already completed.

---

## Item 2: Reasoning channel visibility in subsequent turns

The proxy transforms `response.reasoning_text.delta` into
`response.reasoning_summary_text.delta` in summary mode. The summary text
is visible to Codex (and the user via `qz-thoughts`) but:

- What ends up in the actual input items on subsequent hops? Does the model
  receive its own reasoning back in history, or just the answer?
- If a bad decision was made in the reasoning channel that caused a tool
  failure, does the model have access to that chain of thought on the next
  turn when it needs to diagnose and fix the failure?
- In summary mode, is the reasoning summary included in the conversation
  history sent upstream, or is it stripped (as a `reasoning` item, which
  is in the drop list)?

**Relevant code:**
- `proxy/qz_responses_stream.py` — reasoning transform and summary emission
- `proxy/qz_request_normalization.py` line 310 — `reasoning` and
  `web_search_call` are dropped from input replay
- `proxy/qz_sse.py` — reasoning summary transform

The drop at line 310 is intentional for preventing reasoning items from
being replayed to upstream. But it means the model's reasoning is:
- Visible to the human via Codex/qz-thoughts during the turn
- Summarised and sent to Codex as `reasoning_summary_text`
- **NOT included in the next turn's input to the model itself**

This means the model cannot directly reference its own prior reasoning when
diagnosing a failure. It only has the visible output items (tool results,
assistant messages) to work from.

**Whether this matters:** For most tasks, the model re-reasons from the
result. For complex debugging chains where the failure cause is in the
reasoning (e.g., the model thought X was true and acted on it, but X was
wrong), the model cannot access the reasoning that led to the wrong decision
without reasoning again from scratch.

**Required:** Capture a real session where reasoning preceded a tool failure.
Inspect what the model receives as input on the retry turn. Does it know why
it made the decision it did, or does it have to re-derive that from the
visible evidence?

---

## Capture method

For both items, use the existing fuzz infrastructure:

```bash
QZ_CAPTURE_MODE=full ./scripts/qz-codex exec \
  -m Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL \
  --skip-git-repo-check --full-auto \
  "<a task that triggers tool use, reasoning, and a failure/retry>"
```

Then inspect:
- `var/captures/requests/*/incoming-request.json` — what Codex sent back
- `var/captures/requests/*/forwarded-request.json` — what the proxy sent upstream
- Compare: what was in Codex's input vs what reached the model

The difference between these two files is the proxy's transformation. Any
item present in the incoming request but absent or altered in the forwarded
request is something the model cannot see.

---

## Decision criteria

**If tool history filter gaps exist:** Add specific drop-logging and a
telemetry event so mismatches are visible. Fix the filter for any new patterns.

**If reasoning is not in model history and that causes problems:** Consider
whether a short reasoning-summary block should be prepended to subsequent
turns as part of the prompt/instruction stack, similar to how QZSTATE works
for runtime state injection. Do NOT replay raw reasoning to upstream — that
is correctly dropped.

**If reasoning absence is not causing problems:** Document and close.
