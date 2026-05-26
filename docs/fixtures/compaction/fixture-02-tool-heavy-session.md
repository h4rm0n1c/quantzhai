# Fixture 02: Tool-Heavy Session

**What this fixture tests:**
- Tool output signal retention (success/fail + first meaningful line)
- Error string and exit code preservation
- Evidence-to-decision chain across multiple tool calls
- Tests/results section population

---

## Input Sketch

```text
[User] Run the compaction smoke to confirm localcmp:v2: roundtrip is healthy.

[Agent] Running:
  python3 tests/smoke_compaction_live.py --target /tmp/linuxstreamtools

[Tool: local_shell] exit_code=0
  stdout:
    Building history from /tmp/linuxstreamtools ... 12 items
    Firing compact_threshold=500 ...
    Response: {"object": "response.compaction", "output": [...]}
    Blob prefix: localcmp:v2:
    Expanding blob in second request ...
    Model answer: 'Based on the context read: streamlinkbgm/streamlink_3.sh, obs_stuff/...'
    10/10 checks passed

[Agent] Smoke passed. localcmp:v2: roundtrip confirmed healthy.

[User] Now check what happens when the upstream model returns an empty response
       during compaction — does the proxy handle it gracefully?

[Agent] Checking proxy/qz_responses.py — _build_local_compaction_response:
  rg "_build_local_compaction_response" proxy/qz_responses.py

[Tool: local_shell] exit_code=0
  stdout:
    proxy/qz_responses.py:243: def _build_local_compaction_response(body):

[Agent] Reading _build_local_compaction_response — it calls
        _summarize_items_for_compaction which returns an empty string on empty
        input. The blob is still built; summary_text becomes the empty marker
        block. No crash path.

[Agent] Added a test for empty input:
  class BuildCompactionResponseTests — added test_empty_input_returns_valid_blob.
  Ran: python3 -m pytest tests/test_qz_compaction.py -x -k "empty_input"
  exit_code=0 — 1 test PASS.

[User] Good. Now also check the depth cap — what happens at depth 8?

[Agent] rg "max_compaction_depth" proxy/qz_responses.py
[Tool: local_shell] exit_code=0
  stdout: proxy/qz_responses.py:187:   "max_compaction_depth": 8,

[Agent] Checked _build_local_compaction_response — depth is incremented then
        capped at max_compaction_depth. Cap confirmed at 8. Existing test:
        test_depth_cap in BuildCompactionResponseTests covers this.

[Agent] Ran full suite: python3 -m pytest tests/test_qz_compaction.py
  exit_code=0 — 30 tests PASS (was 29, new test added).
```

---

## Expected Anchored Summary Output

```markdown
## Goal
Verify localcmp:v2: roundtrip health and confirm edge-case handling in
_build_local_compaction_response (empty input, depth cap).

## Active Constraints & Guardrails
- Do not change proxy compaction code without running python3 -m py_compile.
- Smoke target for safe live tests: /tmp/linuxstreamtools

## Current Status
### Done
- Smoke: localcmp:v2: roundtrip confirmed healthy — 10/10 checks passed
  (tests/smoke_compaction_live.py, /tmp/linuxstreamtools, 2026-05-27)
- Empty input edge case: test_empty_input_returns_valid_blob added and passing
- Depth cap: confirmed at 8 (max_compaction_depth), covered by test_depth_cap

### In Progress
- (none)

### Blocked / Deferred
- (none)

## Key Decisions
- _build_local_compaction_response does not crash on empty item list — it
  produces a valid blob with empty summary_text marker block.
  Evidence: source inspection proxy/qz_responses.py:243 + new test.
- Depth cap is 8 (max_compaction_depth). Cap logic confirmed in source.
  Evidence: rg found proxy/qz_responses.py:187 and test_depth_cap exists.

## Evidence Boundaries
- Confirmed (tool output): smoke exit_code=0, 10/10 checks passed,
  blob prefix localcmp:v2:, model recalled streamlinkbgm/streamlink_3.sh.
- Confirmed (source): _build_local_compaction_response at proxy/qz_responses.py:243.
- Confirmed (source): max_compaction_depth=8 at proxy/qz_responses.py:187.
- Confirmed (test): test_depth_cap exists in BuildCompactionResponseTests.

## Technical State
### Files / Paths
proxy/qz_responses.py
tests/test_qz_compaction.py
tests/smoke_compaction_live.py
/tmp/linuxstreamtools

### Commands / Flags / Env Vars
python3 tests/smoke_compaction_live.py --target /tmp/linuxstreamtools
python3 -m pytest tests/test_qz_compaction.py -x -k "empty_input"
python3 -m pytest tests/test_qz_compaction.py
rg "_build_local_compaction_response" proxy/qz_responses.py
rg "max_compaction_depth" proxy/qz_responses.py

### SHAs / Versions / Model Names
localcmp:v2: — active blob prefix
compact_threshold=500 — smoke trigger value

### Tests / Results
tests/smoke_compaction_live.py — 10/10 PASS (/tmp/linuxstreamtools, 2026-05-27)
tests/test_qz_compaction.py — 30 tests PASS (was 29; test_empty_input_returns_valid_blob added)
test_depth_cap (BuildCompactionResponseTests) — PASS

### Tool / Capture Outputs
smoke: exit_code=0; blob prefix localcmp:v2:; model recalled streamlinkbgm/streamlink_3.sh
rg _build_local_compaction_response: proxy/qz_responses.py:243
rg max_compaction_depth: proxy/qz_responses.py:187

## Rejected / Abandoned Approaches
- (none in this session)

## Open Questions / Uncertainties
- (none)

## Next Actions
1. (session complete — awaiting next task)

## Provenance / Source Pointers
- proxy/qz_responses.py:243 — _build_local_compaction_response
- proxy/qz_responses.py:187 — max_compaction_depth: 8
- tests/test_qz_compaction.py — BuildCompactionResponseTests
- tests/smoke_compaction_live.py — live roundtrip smoke
```

---

## What a Compliant Compaction Must Preserve

| Atom | Expected in output |
|---|---|
| `10/10 checks passed` | verbatim result string |
| `localcmp:v2:` | verbatim blob prefix |
| `exit_code=0` | verbatim for each tool call |
| `streamlinkbgm/streamlink_3.sh` | verbatim model recall output |
| `/tmp/linuxstreamtools` | verbatim safe smoke target path |
| `compact_threshold=500` | verbatim trigger value |
| `proxy/qz_responses.py:243` | verbatim source reference |
| `proxy/qz_responses.py:187` | verbatim source reference |
| `max_compaction_depth: 8` | verbatim config value |
| `test_empty_input_returns_valid_blob` | verbatim test name |
| `test_depth_cap` | verbatim test name |
| `BuildCompactionResponseTests` | verbatim class name |
| `30 tests PASS` | verbatim result (increment tracked) |

## What a Compliant Compaction Must NOT Do

- Summarize `exit_code=0` as "succeeded" without the exact code.
- Drop the source line references (`:243`, `:187`).
- Drop `streamlinkbgm/streamlink_3.sh` from the smoke evidence.
- Merge the two tool output inspections without their rg commands.
- Omit `/tmp/linuxstreamtools` as the designated safe smoke target.
