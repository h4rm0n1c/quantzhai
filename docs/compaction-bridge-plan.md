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

---

## What we know about the current state

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

### What OpenAI has — old format

The Responses API long context management involves:
- `response.completed` with `output` items carrying the full conversation
- The `/v1/responses/compact` endpoint which clients can call to summarize
  long conversation history into a compaction blob
- Codex uses this to manage multi-turn context beyond the model's window

### What OpenAI just added — new format

OpenAI recently added a new compaction/summarization approach (exact details
to be researched). Candidates:
- `previous_response_id` chaining — reference a prior response by id instead
  of replaying the full input history
- Conversation-level summarization with a new blob schema
- A new `/v1/responses/summarize` or similar endpoint
- Changes to how `response.completed` carries compacted history

**Before doing anything to the compaction system, research exactly what the
new OpenAI format is and what Codex actually sends.**

---

## What Qwen3.6 on llama.cpp can handle

Unknown. Required research:

- Does llama.cpp's Responses-compatible endpoint understand any compaction
  blob format at all, or does it just process raw input items?
- Does Qwen3.6 respond well to summarized conversation history in text form,
  or does it perform better with the full raw history truncated at its window?
- What is the effective context window for multi-turn coding tasks — does the
  256k context actually help or does quality degrade well before the limit?
- How does Qwen3.6 handle the transition from "real conversation" to "compacted
  summary" mid-history? Does it lose thread continuity?

Without evidence on any of these questions, any compaction design is guesswork.

---

## The bridge problem

The fundamental issue: Codex's compaction format is designed to work with
OpenAI's hosted models. QuantZhai intercepts these compaction requests and must:

1. Decode whatever Codex sends (old or new format)
2. Convert it into a form Qwen/llama.cpp can process usefully
3. Ensure the round-trip produces coherent conversation history for the model

Currently the proxy does this approximately. The risk is:
- Codex sends a new compaction format the proxy doesn't recognise → proxy
  passes garbage to llama.cpp or drops important history
- The proxy's conversion loses information that was critical for task continuity
- The model receives compacted history it can't reason about and produces
  incoherent responses

---

## Required audit before any design

### Phase A: Capture real compaction traffic

Enable captures and run a Codex session long enough to trigger compaction.
Inspect:
- What does Codex actually send when it compacts? What item types appear?
- What is the shape of the compaction blob on the wire?
- Does the new OpenAI compaction format appear in current Codex CLI versions?

```bash
QZ_CAPTURE_MODE=full ./scripts/qz-codex exec -m <profile> \
  "Long multi-turn task that generates many tool calls..."
```

Then inspect `var/captures/requests/*/incoming-request.json` for compaction
item types.

### Phase B: Research OpenAI compaction formats

- Read OpenAI Responses API changelog for compaction format changes
- Check Codex CLI source for how it builds compaction requests
- Document the exact wire format(s) Codex can send

### Phase C: Test Qwen3.6 comprehension

With capture data in hand, run controlled experiments:
- Full history vs summarized text — does task completion rate differ?
- Does the `localcmp:v1:` blob decode into something the model uses?
- What context summarization format does the model respond to best?

### Phase D: Design the bridge

Based on evidence from A/B/C:
- Define the proxy's compaction input interface (what Codex sends, what formats
  to support)
- Define the proxy's compaction output to llama.cpp (how to transform it)
- Define the fallback when Codex sends a format the proxy doesn't recognise

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
