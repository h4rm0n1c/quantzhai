# Compaction Bridge Plan

Date: 2026-05-11
Status: **Delivered** — 2026-05-11 (session 4).

## The problem in one sentence

Codex sends compaction-format conversation history. Qwen on llama.cpp expects
something it can reason over. QuantZhai is the bridge.

---

## What was delivered

### v2 blob format (`localcmp:v2:`)

`proxy/qz_responses.py` now encodes compaction blobs with `localcmp:v2:` and
includes structured history markers around the summary text:

```
<|history_summary|>
Prior turn summary:
- ...
<|end_history_summary|>
```

The decoder accepts both `localcmp:v1:` and `localcmp:v2:` so old blobs from
live sessions are not broken.

### Auto-compaction trigger via `compact_threshold`

`proxy/qz_request_router.py` inspects `context_management.compact_threshold`
on every `/v1/responses` request. When the estimated input token count
(`_estimate_items_tokens`) exceeds the threshold the proxy:

1. Calls `_build_local_compaction_response()` — no upstream hop needed.
2. Returns `{"object": "response.compaction", "output": [...], "usage": {...}}`
   directly to Codex.
3. Emits a `request_completed` telemetry event with
   `suppressed: "auto_compaction_triggered"`.

Codex replays the compaction blob in the next request's input. The proxy
expands it back to a plain summary text message before forwarding to llama.cpp.

### Native compaction passthrough

Items with `type: compaction` but a non-local `encrypted_content` (i.e. not
starting with `localcmp:`) are passed through to llama.cpp unchanged. This
means future Codex versions using a different compaction scheme do not silently
corrupt the history.

### Improved compaction limits

```python
COMPACTION_CONFIG = {
    "keep_recent_items": 8,
    "min_preserve_items": 4,
    "max_summary_chars": 16000,      # was 12000
    "max_tool_output_chars": 800,    # was 600
    "max_item_summary_chars": 600,   # was 500
    "max_compaction_depth": 8,       # was 6
    "target_output_tokens": 12000,   # was 10000
}
```

### Tests

- `tests/test_qz_compaction.py` — 29 unit tests covering encode/decode
  roundtrip, v1 compat, token estimation, expand, build response (depth
  tracking, capping, usage, edge cases), and summary markers.
- `tests/smoke_compaction_live.py` — live integration smoke against the real
  proxy + llama.cpp. Builds a realistic multi-turn history from
  `/tmp/linuxstreamtools-source`, fires at `compact_threshold=500`, verifies
  the proxy returns a valid `response.compaction`, then sends the blob back
  and confirms the model answers coherently from the summary.

Live smoke result (2026-05-11, caveman/HauhauCS-Aggressive):
```
10/10 checks passed
model answer: 'Based on the context read:\n\n- streamlinkbgm/streamlink_3.sh\n- obs_stuff/...'
```
The model correctly recalled file names from the compacted history without
seeing the raw files in the second request.

---

## Original research notes retained for context

### What we knew about the current state (pre-delivery)

### What QuantZhai has today

A vestigial local compaction system built around the `/v1/responses/compact`
endpoint and a `localcmp:v1:` blob format:

- `proxy/qz_responses.py` implements `_summarize_items_for_compaction()`,
  `_microcompact_old_tool_results()`, `_expand_local_compaction_items()`, and
  the compact endpoint handler.
- The `localcmp:v1:` prefix wraps base64-encoded JSON containing a `summary_text`
  field. On replay, the summary is re-injected as a user-visible context message.
- `_microcompact_old_tool_results()` replaces old tool outputs with informative
  placeholders (just improved to preserve error signals rather than generic drops).
- The 600-char cap on `max_tool_output_chars` in the compaction summary blob.
- No systematic testing of whether the blob format is ever decoded correctly
  in real sessions.
- No research on what Qwen3.6 actually comprehends about summarised history.

It is unclear whether this system helps, hurts, or is simply ignored in
production. It was built empirically against observed Codex traffic and not
validated against the model's actual capabilities.

### What OpenAI had — old format (at time of research)

The Responses API long context management involves:
- `response.completed` with `output` items carrying the full conversation
- The `/v1/responses/compact` endpoint which clients can call to summarize
  long conversation history into a compaction blob
- Codex uses this to manage multi-turn context beyond the model's window

### What OpenAI added — new format (candidates at time of research)
- `previous_response_id` chaining — reference a prior response by id instead
  of replaying the full input history
- Conversation-level summarization with a new blob schema
- A new `/v1/responses/summarize` or similar endpoint
- Changes to how `response.completed` carries compacted history

### What Qwen3.6 on llama.cpp could handle (pre-delivery assessment)

Unknown at research time. The live smoke answered the key question: the model
correctly recalled compacted file names from a `<|history_summary|>` summary
without seeing the raw files in the follow-up turn. Native blob passthrough
covers unknown formats from future Codex versions.

### The bridge problem (pre-delivery framing)

Codex's compaction format is designed to work with OpenAI's hosted models.
QuantZhai intercepts these and must decode them, convert to a form Qwen can
use, and ensure the round-trip produces coherent history. The risk of an
unrecognised format corrupting history is now mitigated by native passthrough.

---

## Remaining risks and open work

- Summary quality is heuristic (bullet-point extraction). A Qwen-generated
  summary would be richer, but requires a local inference hop which adds
  latency. Deferred until profile eval can measure the difference.
- `_estimate_items_tokens` uses `len(text) // 4` — a rough char-based
  approximation. This is sufficient for threshold-gating but will drift from
  actual tokeniser counts on non-ASCII content.
- The live smoke confirmed the model can recall facts from the compacted
  summary, but has not been tested under deep compaction (depth > 2) or with
  very long tool outputs that get truncated by `max_tool_output_chars`.
- Codex currently sends `context_management.compact_threshold` only when the
  session model catalog has a configured `truncation_policy.limit`. Sessions
  using our default 249K-token catalog will not trigger auto-compaction through
  this path; they rely on explicit `/v1/responses/compact` calls or manual
  threshold selection.

---

## Relationship to other docs

- `docs/edge-case-config-contract-plan.md` — the broader config cleanup; compaction
  output belongs under `var/generated/` or `var/state/` in the target layout.
- `docs/config-data-path-audit.md` — F14/F15 (undocumented var/ layout, no schema
  version on state files) are related.
- `proxy/qz_responses.py` — all local compaction code lives here.
