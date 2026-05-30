"""
Live integration test for proxy sandbox escalation and apply_patch correction.
Sends crafted HTTP requests directly to the running proxy.
"""
import json
import os
import sys
import time
import urllib.request

PROXY = "http://127.0.0.1:18180"
MODEL = "Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[33m    \033[0m"

results = []

def check(label, cond, detail=""):
    status = PASS if cond else FAIL
    print(f"  {status}  {label}" + (f"\n  {INFO}  {detail}" if detail else ""))
    results.append((label, cond))

def sse_request(body: dict) -> bytes:
    """POST to /v1/responses with stream=True, return raw response bytes."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{PROXY}/v1/responses",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local",
            "Accept": "text/event-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()

def parse_events(raw: bytes) -> list[tuple[str, dict]]:
    events = []
    cur_ev = cur_data = None
    for line in raw.decode(errors="replace").splitlines():
        if line.startswith("event: "):
            cur_ev = line[7:]
        elif line.startswith("data: "):
            d = line[6:]
            if d != "[DONE]":
                try: cur_data = json.loads(d)
                except Exception: cur_data = {}
        elif line == "" and cur_ev:
            events.append((cur_ev, cur_data or {}))
            cur_ev = cur_data = None
    return events

# -----------------------------------------------------------------------
# 1. Sandbox escalation — send a conversation where the last exec output
#    contains "Read-only file system".  The proxy should intercept and
#    return a synthetic SSE stream containing require_escalated.
# -----------------------------------------------------------------------
print("\n=== Test 1: Sandbox escalation intercept ===")

EXEC_CALL_ID = "call_esc_test_001"
body_esc = {
    "model": MODEL,
    "stream": True,
    "input": [
        {"type": "message", "role": "user", "content": "write hello to /tmp/esc_test.txt"},
        # The model's exec call
        {
            "type": "function_call",
            "id": EXEC_CALL_ID,
            "call_id": EXEC_CALL_ID,
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "/bin/bash -lc 'echo hello > /tmp/esc_test.txt'", "sandbox_permissions": "use_default"}),
            "status": "completed",
        },
        # Codex sends back sandbox denial
        {
            "type": "function_call_output",
            "call_id": EXEC_CALL_ID,
            "output": "Exit code: 1\nstderr: /tmp/esc_test.txt: Read-only file system\n",
        },
    ],
    "tools": [],
}

try:
    raw = sse_request(body_esc)
    events = parse_events(raw)
    ev_types = [e for e, _ in events]

    esc_id = EXEC_CALL_ID + "_qzesc"
    found_fn = any(
        e == "response.output_item.done" and
        isinstance(p.get("item"), dict) and
        p["item"].get("name") == "exec_command" and
        esc_id in (p["item"].get("call_id","") + p["item"].get("id",""))
        for e, p in events
    )
    contains_req_esc = b"require_escalated" in raw
    has_done = b"[DONE]" in raw

    check("Proxy returns SSE stream", bool(ev_types))
    check("Stream contains require_escalated", contains_req_esc)
    check("output_item.done has exec_command", found_fn,
          f"events seen: {ev_types[:8]}")
    check("Stream ends with [DONE]", has_done)
    check("Response is complete (no LLM call made)",
          "response.completed" in ev_types,
          "Escalation should short-circuit before LLM")
except Exception as exc:
    check("Test 1 request succeeded", False, str(exc))

# -----------------------------------------------------------------------
# 2. Escalation not triggered for clean exec output
# -----------------------------------------------------------------------
print("\n=== Test 2: Clean exec output — no escalation ===")

body_clean = {
    "model": MODEL,
    "stream": True,
    "input": [
        {"type": "message", "role": "user", "content": "what is 2+2"},
    ],
    "max_output_tokens": 30,
    "tools": [],
}

try:
    # Just check we get a normal SSE stream that reaches the LLM
    raw2 = sse_request(body_clean)
    has_reasoning_or_output = b"response.output" in raw2 or b"response.reasoning" in raw2 or b"response.completed" in raw2
    check("Clean request still flows to LLM", has_reasoning_or_output)
    check("No require_escalated in clean response", b"require_escalated" not in raw2)
except Exception as exc:
    check("Test 2 request succeeded", False, str(exc))

# -----------------------------------------------------------------------
# 3. Apply_patch correction tracker — synthetic correction note injection.
#    Register a correction manually then send a request whose input has
#    the matching function_call_output.  Verify note appears in forwarded
#    input (we check via /qz/telemetry or just verify the tracker state).
# -----------------------------------------------------------------------
print("\n=== Test 3: CorrectionTracker injection ===")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "home", "harri", "turboquant", "quantzhai", "proxy"))
sys.path.insert(0, "/home/harri/turboquant/quantzhai/proxy")

try:
    from qz_sandbox_escalation import get_correction_tracker, CorrectionTracker

    ct = CorrectionTracker()
    ct.register("ap_call_test", "```json\n{\"diff\":\"..\"}\n```", "{\"diff\":\"..\"}")

    items = [
        {"type": "message", "role": "user", "content": "fix it"},
        {"type": "function_call", "call_id": "ap_call_test", "name": "apply_patch", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "ap_call_test", "output": "Patch applied successfully."},
    ]
    result = ct.inject_notes(items)

    note_item = next((i for i in result if i.get("call_id") == "ap_call_test" and i.get("type") == "function_call_output"), None)
    check("Correction note injected into output", note_item is not None and "auto-corrected" in note_item.get("output",""),
          note_item.get("output","")[:120] if note_item else "no item found")
    check("Note mentions markdown fences", note_item is not None and "markdown" in note_item.get("output","").lower())
    check("Non-output items unchanged", result[0] == items[0])
    check("Tracker clears after injection", ct.inject_notes(items) is items, "second call should be identity")
except Exception as exc:
    check("Test 3 ran", False, str(exc))

# -----------------------------------------------------------------------
# 4. Re-escalation guard — _qzesc suffix call is NOT re-escalated
# -----------------------------------------------------------------------
print("\n=== Test 4: Re-escalation guard ===")

try:
    from qz_sandbox_escalation import SandboxEscalationManager
    mgr = SandboxEscalationManager()
    items_esc = [
        {
            "type": "function_call",
            "id": "orig_qzesc",
            "call_id": "orig_qzesc",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "touch /x", "sandbox_permissions": "require_escalated"}),
        },
        {"type": "function_call_output", "call_id": "orig_qzesc",
         "output": "Read-only file system"},
    ]
    esc, orig = mgr.check_for_denial(items_esc)
    check("Already-escalated call is not re-escalated", esc is None,
          f"esc={esc}, orig={orig}")
except Exception as exc:
    check("Test 4 ran", False, str(exc))

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
print()
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{'='*48}")
print(f"  {passed}/{total} checks passed")
if passed < total:
    print("  Failed:")
    for label, ok in results:
        if not ok:
            print(f"    - {label}")


# -----------------------------------------------------------------------
# Test E-1: exec_command 'command' → 'cmd' field rename
# -----------------------------------------------------------------------
print("\n=== Test E-1: exec_command field rename 'command' → 'cmd' ===")

# We can't easily trigger the rename through the proxy HTTP API since the
# correction happens in the outgoing SSE stream (model → Codex direction),
# not on the incoming Codex → proxy → LLM direction.
# Test it via the unit path instead.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'proxy'))

try:
    from qz_sandbox_escalation import _build_correction_note, CorrectionTracker
    import json as _json

    orig = _json.dumps({"command": "ls -la", "workdir": "/tmp"})
    corr = _json.dumps({"cmd": "ls -la", "workdir": "/tmp"})
    note = _build_correction_note(orig, corr)
    check("E-1 correction note mentions field rename", "command" in note and "cmd" in note, note)

    # Tracker injects the note into exec result
    ct = CorrectionTracker()
    ct.register("exec_e1_test", orig, corr)
    items = [{"type": "function_call_output", "call_id": "exec_e1_test",
              "output": "Exit code: 0\nls output here"}]
    result = ct.inject_notes(items)
    check("E-1 tracker injects note into exec result",
          "command" in result[0]["output"] and "renamed" in result[0]["output"],
          result[0]["output"][:120])
    check("E-1 original output preserved", "Exit code: 0" in result[0]["output"])
    check("E-1 tracker clears after inject", ct.inject_notes(items) is items)
except Exception as exc:
    check("E-1 unit checks ran", False, str(exc))
