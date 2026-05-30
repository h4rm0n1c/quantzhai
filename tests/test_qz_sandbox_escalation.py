"""Tests for qz_sandbox_escalation — transparent exec sandbox escalation."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proxy"))

from qz_sandbox_escalation import (
    SANDBOX_DENIAL_SIGNALS,
    CorrectionTracker,
    SandboxEscalationManager,
    _build_correction_note,
    _extract_output_text,
    _is_sandbox_denial,
    build_escalation_sse,
    make_escalated_call,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exec_call(call_id: str, cmd: str, sandbox_perms: str = "use_default") -> dict:
    return {
        "type": "function_call",
        "id": call_id,
        "call_id": call_id,
        "name": "exec_command",
        "arguments": json.dumps({"command": cmd, "sandbox_permissions": sandbox_perms}),
    }


def _exec_output(call_id: str, text: str) -> dict:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": text,
    }


def _exec_output_list(call_id: str, text: str) -> dict:
    """Output encoded as a list of content items (alternate wire format)."""
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": [{"type": "text", "text": text}],
    }


# ---------------------------------------------------------------------------
# _extract_output_text
# ---------------------------------------------------------------------------

def test_extract_output_text_string():
    item = _exec_output("c1", "Exit code: 1\nRead-only file system")
    assert "Read-only file system" in _extract_output_text(item)


def test_extract_output_text_list():
    item = _exec_output_list("c1", "bash: test.txt: Read-only file system")
    assert "Read-only file system" in _extract_output_text(item)


def test_extract_output_text_empty():
    assert _extract_output_text({"type": "function_call_output", "call_id": "x"}) == ""


# ---------------------------------------------------------------------------
# _is_sandbox_denial
# ---------------------------------------------------------------------------

def test_is_sandbox_denial_ro_filesystem():
    assert _is_sandbox_denial("bash: test.txt: Read-only file system")


def test_is_sandbox_denial_case_insensitive():
    assert _is_sandbox_denial("READ-ONLY FILE SYSTEM")


def test_is_sandbox_denial_landlock():
    assert _is_sandbox_denial("landlock: blocked by policy")


def test_is_sandbox_denial_seccomp():
    assert _is_sandbox_denial("seccomp violation")


def test_is_sandbox_denial_network_allowlist():
    assert _is_sandbox_denial("Domain not in allowlist.")


def test_is_sandbox_denial_network_local_policy():
    assert _is_sandbox_denial("Sandbox policy blocks local/private network addresses.")


def test_is_sandbox_denial_network_mitm():
    assert _is_sandbox_denial("MITM required for limited HTTPS.")


def test_is_sandbox_denial_network_proxy_header_allowlist():
    # x-proxy-error header value appears in curl -v output
    assert _is_sandbox_denial("< x-proxy-error: blocked-by-allowlist")


def test_is_sandbox_denial_network_proxy_header_local():
    assert _is_sandbox_denial("blocked-by-local-network-policy")


def test_is_sandbox_denial_apply_patch_message():
    assert _is_sandbox_denial(
        "patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings"
    )


def test_is_sandbox_denial_normal_success():
    assert not _is_sandbox_denial("Exit code: 0\nOutput:\nhello world")


def test_is_sandbox_denial_normal_failure():
    # A plain non-zero exit with no sandbox keywords is not a denial
    assert not _is_sandbox_denial("Exit code: 1\ncommand not found: typo")


def test_is_sandbox_denial_permission_denied_not_triggered():
    # Broad "permission denied" is intentionally excluded to avoid false positives
    # on normal filesystem permission errors.
    assert not _is_sandbox_denial("Permission denied: /etc/shadow")


def test_is_sandbox_denial_operation_not_permitted_not_triggered():
    assert not _is_sandbox_denial("Operation not permitted")


# ---------------------------------------------------------------------------
# make_escalated_call
# ---------------------------------------------------------------------------

def test_make_escalated_call_adds_require_escalated():
    original = _exec_call("abc123", "/bin/bash -c 'echo hi > /tmp/out.txt'")
    esc = make_escalated_call(original)
    args = json.loads(esc["arguments"])
    assert args["sandbox_permissions"] == "require_escalated"


def test_make_escalated_call_preserves_command():
    cmd = "/bin/bash -c 'tee /tmp/result.txt'"
    original = _exec_call("x", cmd)
    esc = make_escalated_call(original)
    args = json.loads(esc["arguments"])
    assert args["command"] == cmd


def test_make_escalated_call_id_suffix():
    original = _exec_call("call_007", "echo hi")
    esc = make_escalated_call(original)
    assert esc["call_id"].startswith("call_007")
    assert esc["call_id"] != "call_007"
    assert "_qzesc" in esc["call_id"]


def test_make_escalated_call_type():
    original = _exec_call("c1", "ls")
    esc = make_escalated_call(original)
    assert esc["type"] == "function_call"
    assert esc["name"] == "exec_command"


# ---------------------------------------------------------------------------
# build_escalation_sse
# ---------------------------------------------------------------------------

def _parse_sse_events(raw: bytes) -> list[tuple[str, dict]]:
    """Parse SSE bytes into list of (event_type, payload) pairs."""
    events = []
    current_event = None
    current_data = None
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            data = line[6:]
            if data != "[DONE]":
                current_data = json.loads(data)
        elif line == "":
            if current_event and current_data is not None:
                events.append((current_event, current_data))
            current_event = None
            current_data = None
    return events


def test_build_escalation_sse_event_sequence():
    call = make_escalated_call(_exec_call("c1", "echo hi"))
    raw = build_escalation_sse(call)
    events = _parse_sse_events(raw)
    types = [e for e, _ in events]
    assert types[0] == "response.created"
    assert types[1] == "response.in_progress"
    assert "response.output_item.added" in types
    assert "response.function_call_arguments.delta" in types
    assert "response.function_call_arguments.done" in types
    assert "response.output_item.done" in types
    assert types[-1] == "response.completed"


def test_build_escalation_sse_contains_require_escalated():
    call = make_escalated_call(_exec_call("c1", "cp a b"))
    raw = build_escalation_sse(call)
    assert b"require_escalated" in raw


def test_build_escalation_sse_call_id_present():
    original = _exec_call("orig_call", "echo x")
    esc = make_escalated_call(original)
    raw = build_escalation_sse(esc)
    assert esc["call_id"].encode() in raw


def test_build_escalation_sse_done_marker():
    call = make_escalated_call(_exec_call("c1", "ls"))
    raw = build_escalation_sse(call)
    assert b"data: [DONE]" in raw


# ---------------------------------------------------------------------------
# SandboxEscalationManager — check_for_denial
# ---------------------------------------------------------------------------

def test_check_for_denial_detects_ro_failure():
    mgr = SandboxEscalationManager()
    items = [
        _exec_call("c1", "/bin/bash -c 'echo hi > /tmp/out'"),
        _exec_output("c1", "Exit code: 1\nstderr: /tmp/out: Read-only file system"),
    ]
    esc_call, orig_cid = mgr.check_for_denial(items)
    assert esc_call is not None
    assert orig_cid == "c1"
    assert json.loads(esc_call["arguments"])["sandbox_permissions"] == "require_escalated"


def test_check_for_denial_no_exec_call_skips():
    mgr = SandboxEscalationManager()
    # Output present but no matching function_call in input
    items = [
        _exec_output("c1", "Read-only file system"),
    ]
    esc_call, orig_cid = mgr.check_for_denial(items)
    assert esc_call is None
    assert orig_cid is None


def test_check_for_denial_clean_output_returns_none():
    mgr = SandboxEscalationManager()
    items = [
        _exec_call("c1", "echo hello"),
        _exec_output("c1", "Exit code: 0\nhello"),
    ]
    esc_call, orig_cid = mgr.check_for_denial(items)
    assert esc_call is None
    assert orig_cid is None


def test_check_for_denial_registers_pending():
    mgr = SandboxEscalationManager()
    items = [
        _exec_call("c2", "touch /tmp/x"),
        _exec_output("c2", "Read-only file system"),
    ]
    esc_call, _ = mgr.check_for_denial(items)
    assert esc_call is not None
    with mgr._lock:
        assert esc_call["call_id"] in mgr._pending


def test_check_for_denial_apply_patch_output():
    mgr = SandboxEscalationManager()
    items = [
        _exec_call("c3", "some-cmd"),
        {
            "type": "function_call_output",
            "call_id": "c3",
            "output": "patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings",
        },
    ]
    esc_call, orig_cid = mgr.check_for_denial(items)
    assert esc_call is not None
    assert orig_cid == "c3"


def test_check_for_denial_skips_already_escalated_calls():
    """An exec call that already has the _qzesc suffix must not be re-escalated."""
    mgr = SandboxEscalationManager()
    esc_cid = "call_007_qzesc"
    items = [
        _exec_call(esc_cid, "cp a b"),
        _exec_output(esc_cid, "Exit code: 1\nbash: b: Read-only file system"),
    ]
    esc_call, orig_cid = mgr.check_for_denial(items)
    assert esc_call is None
    assert orig_cid is None


# ---------------------------------------------------------------------------
# SandboxEscalationManager — check_pending_resolution
# ---------------------------------------------------------------------------

def _make_pending_scenario() -> tuple[SandboxEscalationManager, str, str, str]:
    """
    Set up a manager with one pending escalation.
    Returns (mgr, original_call_id, esc_call_id, esc_output_text).
    """
    mgr = SandboxEscalationManager()
    orig_cid = "orig_001"
    items = [
        _exec_call(orig_cid, "cp a b"),
        _exec_output(orig_cid, "Read-only file system"),
    ]
    esc_call, _ = mgr.check_for_denial(items)
    return mgr, orig_cid, esc_call["call_id"], "Exit code: 0\nOutput:\nok"


def test_check_pending_resolution_replaces_failure_with_success():
    mgr, orig_cid, esc_cid, esc_text = _make_pending_scenario()

    # Build input as Codex would send after running the escalated call
    input_items = [
        {"type": "message", "role": "user", "content": "do the thing"},
        _exec_call(orig_cid, "cp a b"),
        _exec_output(orig_cid, "Read-only file system"),
        _exec_call(esc_cid, "cp a b"),        # synthetic escalated call
        _exec_output(esc_cid, esc_text),       # escalated success
    ]
    rewritten = mgr.check_pending_resolution(input_items)
    assert rewritten is not None

    ids_and_types = [(i.get("type"), i.get("call_id")) for i in rewritten]

    # Synthetic escalated call must be gone
    assert ("function_call", esc_cid) not in ids_and_types
    # Escalated output with esc_cid must be gone
    assert ("function_call_output", esc_cid) not in ids_and_types

    # Original failed output replaced with success, original call_id retained
    outputs = [i for i in rewritten if i.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == orig_cid
    assert esc_text in _extract_output_text(outputs[0])


def test_check_pending_resolution_preserves_other_items():
    mgr, orig_cid, esc_cid, esc_text = _make_pending_scenario()
    user_msg = {"type": "message", "role": "user", "content": "fix it"}
    input_items = [
        user_msg,
        _exec_call(orig_cid, "cp a b"),
        _exec_output(orig_cid, "Read-only file system"),
        _exec_call(esc_cid, "cp a b"),
        _exec_output(esc_cid, esc_text),
    ]
    rewritten = mgr.check_pending_resolution(input_items)
    assert user_msg in rewritten


def test_check_pending_resolution_original_exec_call_retained():
    mgr, orig_cid, esc_cid, esc_text = _make_pending_scenario()
    input_items = [
        _exec_call(orig_cid, "cp a b"),
        _exec_output(orig_cid, "Read-only file system"),
        _exec_call(esc_cid, "cp a b"),
        _exec_output(esc_cid, esc_text),
    ]
    rewritten = mgr.check_pending_resolution(input_items)
    calls = [i for i in rewritten if i.get("type") == "function_call"]
    assert any(c.get("call_id") == orig_cid for c in calls)


def test_check_pending_resolution_appends_escalation_note():
    """The rewritten output must contain a plain-English note telling the model what happened."""
    mgr, orig_cid, esc_cid, esc_text = _make_pending_scenario()
    input_items = [
        _exec_call(orig_cid, "cp a b"),
        _exec_output(orig_cid, "Read-only file system"),
        _exec_call(esc_cid, "cp a b"),
        _exec_output(esc_cid, "Exit code: 0\n"),
    ]
    rewritten = mgr.check_pending_resolution(input_items)
    assert rewritten is not None
    outputs = [i for i in rewritten if i.get("type") == "function_call_output"]
    assert len(outputs) == 1
    note_text = outputs[0]["output"]
    # Note must explain in plain English — no proxy parameter names
    assert "sandbox" in note_text.lower()
    assert "proxy" in note_text.lower()
    assert "succeeded" in note_text.lower()
    assert "sandbox" in note_text.lower()
    assert "Exit code: 0" in note_text  # original success output preserved
    # Must NOT expose internal parameter names that confuse the model
    assert "sandbox_permissions" not in note_text


def test_check_pending_resolution_clears_pending():
    mgr, orig_cid, esc_cid, esc_text = _make_pending_scenario()
    input_items = [
        _exec_call(orig_cid, "cp a b"),
        _exec_output(orig_cid, "Read-only file system"),
        _exec_call(esc_cid, "cp a b"),
        _exec_output(esc_cid, esc_text),
    ]
    mgr.check_pending_resolution(input_items)
    with mgr._lock:
        assert esc_cid not in mgr._pending


def test_check_pending_resolution_deduplicates_advisory_outputs():
    """
    If the advisory injector also added a function_call_output with the same
    original call_id, the rewrite should produce exactly one output item (the
    escalated success) and not duplicate it.
    """
    mgr, orig_cid, esc_cid, esc_text = _make_pending_scenario()
    advisory_output = {
        "type": "function_call_output",
        "call_id": orig_cid,
        "output": "[sandbox advisory] use require_escalated",
    }
    input_items = [
        _exec_call(orig_cid, "cp a b"),
        _exec_output(orig_cid, "Read-only file system"),
        advisory_output,                           # injected by advisory system
        _exec_call(esc_cid, "cp a b"),
        _exec_output(esc_cid, esc_text),
    ]
    rewritten = mgr.check_pending_resolution(input_items)
    assert rewritten is not None
    outputs = [i for i in rewritten if i.get("type") == "function_call_output"]
    assert len(outputs) == 1, f"expected 1 output item, got {len(outputs)}: {outputs}"
    assert outputs[0]["call_id"] == orig_cid
    assert esc_text in _extract_output_text(outputs[0])


def test_check_pending_resolution_no_match_returns_none():
    mgr = SandboxEscalationManager()
    items = [
        _exec_call("c1", "echo hi"),
        _exec_output("c1", "Exit code: 0\nhi"),
    ]
    assert mgr.check_pending_resolution(items) is None


# ---------------------------------------------------------------------------
# Full round-trip: denial → escalation SSE → resolution
# ---------------------------------------------------------------------------

def test_round_trip_full_escalation():
    """
    Simulate the two-request cycle:
      1. Codex sends back a sandbox denial.
      2. Proxy detects it, registers escalation, builds SSE.
      3. Codex runs escalated call, sends back success.
      4. Proxy rewrites input and forwards clean history to LLM.
    """
    mgr = SandboxEscalationManager()

    # --- Turn 1: model generated exec call, Codex ran it, got sandbox error ---
    turn1_input = [
        {"type": "message", "role": "user", "content": "write hello to /tmp/hi.txt"},
        _exec_call("c_write", "/bin/bash -c 'echo hello > /tmp/hi.txt'"),
        _exec_output("c_write", "Exit code: 1\nstderr: /tmp/hi.txt: Read-only file system\n"),
    ]
    esc_call, orig_cid = mgr.check_for_denial(turn1_input)
    assert esc_call is not None
    assert orig_cid == "c_write"

    sse_bytes = build_escalation_sse(esc_call)
    assert b"require_escalated" in sse_bytes

    # --- Turn 2: Codex ran escalated call, sends back success ---
    turn2_input = [
        {"type": "message", "role": "user", "content": "write hello to /tmp/hi.txt"},
        _exec_call("c_write", "/bin/bash -c 'echo hello > /tmp/hi.txt'"),
        _exec_output("c_write", "Exit code: 1\nstderr: /tmp/hi.txt: Read-only file system\n"),
        # Synthetic escalated call the proxy emitted
        _exec_call(esc_call["call_id"], "/bin/bash -c 'echo hello > /tmp/hi.txt'"),
        # Escalated success
        _exec_output(esc_call["call_id"], "Exit code: 0\nOutput:\n"),
    ]
    rewritten = mgr.check_pending_resolution(turn2_input)
    assert rewritten is not None

    # No escalation artefacts in forwarded history
    call_ids = [i.get("call_id") for i in rewritten if isinstance(i, dict)]
    assert esc_call["call_id"] not in call_ids

    # Original call retained, output rewritten to success
    outputs = [i for i in rewritten if i.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "c_write"
    out_text = _extract_output_text(outputs[0])
    assert "Exit code: 0" in out_text
    assert "proxy" in out_text.lower()
    assert "succeeded" in out_text.lower()
    assert "sandbox_permissions" not in out_text  # no internal param names


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _build_correction_note
# ---------------------------------------------------------------------------

def test_build_correction_note_markdown_fences():
    note = _build_correction_note("```json\n{\"diff\":\"..\"}\n```", "{\"diff\":\"..\"}")
    assert "markdown code fences" in note


def test_build_correction_note_diff_headers():
    note = _build_correction_note(
        '{"diff": "--- a/foo.py\\n+++ b/foo.py\\n@@ @@\\n-old\\n+new"}',
        '{"diff": "@@ @@\\n-old\\n+new"}',
    )
    assert "unified diff headers" in note


def test_build_correction_note_fallback():
    note = _build_correction_note('{"x":1}', '{"y":2}')
    assert note  # has some text


# ---------------------------------------------------------------------------
# CorrectionTracker
# ---------------------------------------------------------------------------

def _apply_patch_output(call_id: str, text: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def test_correction_tracker_injects_note():
    ct = CorrectionTracker()
    ct.register("c1", "```json\n{\"diff\":\"..\"}\n```", "{\"diff\":\"..\"}")
    items = [_apply_patch_output("c1", "Patch applied successfully.")]
    result = ct.inject_notes(items)
    assert result is not items
    assert "auto-corrected" in result[0]["output"]
    assert "markdown code fences" in result[0]["output"]


def test_correction_tracker_preserves_other_outputs():
    ct = CorrectionTracker()
    ct.register("c1", "```json\n{}\n```", "{}")
    items = [
        _apply_patch_output("c_other", "Other output"),
        _apply_patch_output("c1", "Patch applied."),
    ]
    result = ct.inject_notes(items)
    assert result[0]["output"] == "Other output"
    assert "auto-corrected" in result[1]["output"]


def test_correction_tracker_clears_after_inject():
    ct = CorrectionTracker()
    ct.register("c1", "```json\n{}\n```", "{}")
    items = [_apply_patch_output("c1", "ok")]
    ct.inject_notes(items)
    # Second call should be a no-op — tracker is cleared
    result2 = ct.inject_notes(items)
    assert result2 is items  # unchanged, no correction left


def test_correction_tracker_no_match_returns_same_list():
    ct = CorrectionTracker()
    items = [_apply_patch_output("c1", "ok")]
    result = ct.inject_notes(items)
    assert result is items


def test_correction_tracker_non_output_items_unchanged():
    ct = CorrectionTracker()
    ct.register("c1", "```json\n{}\n```", "{}")
    items = [
        {"type": "message", "role": "user", "content": "fix it"},
        _apply_patch_output("c1", "ok"),
    ]
    result = ct.inject_notes(items)
    assert result[0] == {"type": "message", "role": "user", "content": "fix it"}


def test_correction_tracker_thread_safe():
    import threading
    ct = CorrectionTracker()
    for i in range(20):
        ct.register(f"call_{i}", f"```json\n{{\"i\":{i}}}\n```", f"{{\"i\":{i}}}")

    outputs = [_apply_patch_output(f"call_{i}", f"ok_{i}") for i in range(20)]
    results = {}

    def inject(i):
        r = ct.inject_notes([outputs[i]])
        results[i] = r[0]["output"]

    threads = [threading.Thread(target=inject, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    for i in range(20):
        assert "auto-corrected" in results[i], f"call_{i} missing correction note"


# ---------------------------------------------------------------------------
# _parse_apply_patch_arguments outer fence stripping
# ---------------------------------------------------------------------------

def test_concurrent_escalations_do_not_cross():
    import threading
    mgr = SandboxEscalationManager()
    results = {}

    def escalate(thread_id: int):
        cid = f"call_{thread_id}"
        items = [
            _exec_call(cid, "touch /x"),
            _exec_output(cid, "Read-only file system"),
        ]
        esc, orig = mgr.check_for_denial(items)
        results[thread_id] = (esc, orig)

    threads = [threading.Thread(target=escalate, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i, (esc, orig) in results.items():
        assert esc is not None
        assert orig == f"call_{i}"
        args = json.loads(esc["arguments"])
        assert args["sandbox_permissions"] == "require_escalated"


# ---------------------------------------------------------------------------
# _parse_apply_patch_arguments outer fence stripping (integration)
# ---------------------------------------------------------------------------

def test_parse_apply_patch_outer_json_fence_succeeds():
    """Model wraps entire JSON arguments in ```json...``` — should parse OK."""
    import json as _json
    from qz_tool_apply_patch import _parse_apply_patch_arguments
    inner = _json.dumps({"operation": {"type": "update_file", "path": "foo.py", "diff": "@@ -1 +1 @@\n-old\n+new\n"}})
    fenced = f"```json\n{inner}\n```"
    result = _parse_apply_patch_arguments(fenced)
    assert result is not None
    assert result.get("type") == "update_file"
    assert result.get("path") == "foo.py"


def test_parse_apply_patch_outer_plain_fence_succeeds():
    """Model wraps JSON in plain ``` without language tag."""
    import json as _json
    from qz_tool_apply_patch import _parse_apply_patch_arguments
    inner = _json.dumps({"operation": {"type": "create_file", "path": "bar.rs", "diff": "+fn main() {}"}})
    fenced = f"```\n{inner}\n```"
    result = _parse_apply_patch_arguments(fenced)
    assert result is not None
    assert result.get("type") == "create_file"


def test_parse_apply_patch_clean_args_unchanged():
    """Clean JSON without fences should parse as before."""
    import json as _json
    from qz_tool_apply_patch import _parse_apply_patch_arguments
    clean = _json.dumps({"operation": {"type": "update_file", "path": "x.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}})
    result = _parse_apply_patch_arguments(clean)
    assert result is not None
    assert result.get("path") == "x.py"


def test_parse_apply_patch_still_fails_on_garbage():
    """Genuinely unparseable arguments should still return None."""
    from qz_tool_apply_patch import _parse_apply_patch_arguments
    assert _parse_apply_patch_arguments("this is not json at all") is None
    assert _parse_apply_patch_arguments('{"no_operation_key": true}') is None
