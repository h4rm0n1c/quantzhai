#!/usr/bin/env python3
"""
Web search Codex contract checker for QuantZhai.

Verifies that the proxy emits the correct Codex-visible SSE events for web_search
tool calls, as confirmed by the Codex source audit in issue #66.

Correct contract (output from the proxy):
  event: response.output_item.added   data.item.type == "web_search_call"
  event: response.output_item.done    data.item.type == "web_search_call"
  event: response.completed

Detection: item type is in the SSE *data payload*, not in the event name.
  web_search_call events are response.output_item.added/done events whose
  data.item.type field equals "web_search_call".  Scanning event names for
  the string "web_search_call" will miss them entirely.

Forbidden (fake lifecycle events removed in issue #66):
  response.web_search_call.in_progress
  response.web_search_call.searching
  response.web_search_call.completed

Usage:
  python3 scripts/qz_web_search_contract_check.py --mode=deterministic [--repo-root PATH]
  python3 scripts/qz_web_search_contract_check.py --mode=live \\
      --proxy-base http://127.0.0.1:18080 --model MODEL [--timeout 90]
  python3 scripts/qz_web_search_contract_check.py --mode=self-test

Output: JSON printed to stdout.
  {"status": "pass"|"fail"|"no_search"|"not_ready"|"error_503_despite_ready"|"error_timeout"|"error",
   "checks": {...}, "mode": "deterministic"|"live"}

Exit code: always 0 (status encoded in JSON so the shell script can inspect it).
"""

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# SSE parser — payload-aware
# ---------------------------------------------------------------------------

def parse_sse_events_with_payloads(stream_text: str) -> list:
    """Parse SSE stream text into list of (event_name, payload) tuples.

    Each SSE block may have an "event:" line giving the event name, and a
    "data:" line (or multiple) giving the JSON payload.

    Critically: web search items appear as:
        event: response.output_item.added
        data: {"item": {"type": "web_search_call", ...}}

    The event name does NOT contain "web_search_call".  To detect web search
    items you must inspect payload["item"]["type"].

    Returns a list of (event_name, payload) where:
    - event_name is the string from the "event:" line (or None if absent)
    - payload is a parsed dict, the string "[DONE]", or None on parse error
    """
    events = []
    event_type = None
    data_lines = []
    for line in stream_text.splitlines():
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
        elif line == "":
            if data_lines:
                data = "\n".join(data_lines)
                if data == "[DONE]":
                    payload = "[DONE]"
                else:
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        payload = None
                # Fall back to payload["type"] when no event: line was present
                effective_event = event_type or (
                    payload.get("type") if isinstance(payload, dict) else None
                )
                events.append((effective_event, payload))
            event_type = None
            data_lines = []
    return events


# ---------------------------------------------------------------------------
# Contract checker
# ---------------------------------------------------------------------------

def check_contract(events: list, mode: str = "deterministic") -> dict:
    """Check Codex web_search contract against a parsed SSE event list.

    Args:
        events:  list of (event_name, payload) from parse_sse_events_with_payloads
        mode:    "deterministic" — no_search (no web_search_call items) is a FAIL
                 "live"          — no_search is a SKIP (model may decline tool use)

    Returns a dict with keys:
        status:  "pass" | "fail" | "no_search" | "error"
        checks:  per-item bool results
        events:  list of event names seen
    """
    all_event_names = [et for et, _ in events]

    # Detect web_search_call items by inspecting data payload, not event name
    added_web_search = any(
        et == "response.output_item.added"
        and isinstance(p, dict)
        and isinstance(p.get("item"), dict)
        and p["item"].get("type") == "web_search_call"
        for et, p in events
    )
    done_web_search = any(
        et == "response.output_item.done"
        and isinstance(p, dict)
        and isinstance(p.get("item"), dict)
        and p["item"].get("type") == "web_search_call"
        for et, p in events
    )
    response_completed = "response.completed" in all_event_names

    # Fake lifecycle events must be absent (removed in issue #66)
    fake_ip        = "response.web_search_call.in_progress" in all_event_names
    fake_searching = "response.web_search_call.searching"   in all_event_names
    fake_completed = "response.web_search_call.completed"   in all_event_names

    checks = {
        "output_item_added_with_web_search_call_item":  added_web_search,
        "output_item_done_with_web_search_call_item":   done_web_search,
        "response_completed":                           response_completed,
        "fake_web_search_call_in_progress_absent":      not fake_ip,
        "fake_web_search_call_searching_absent":        not fake_searching,
        "fake_web_search_call_completed_absent":        not fake_completed,
    }

    # No web_search_call items at all
    if not added_web_search and not done_web_search:
        # Deterministic: the fake stream *must* produce a web_search_call item —
        # missing items mean the proxy failed to execute/convert web_search.
        # Live: the model may simply have chosen not to call web_search.
        status = "fail" if mode == "deterministic" else "no_search"
        return {"status": status, "checks": checks, "events": all_event_names}

    if (added_web_search and done_web_search and response_completed
            and not fake_ip and not fake_searching and not fake_completed):
        status = "pass"
    else:
        status = "fail"

    return {"status": status, "checks": checks, "events": all_event_names}


# ---------------------------------------------------------------------------
# Fake helpers for deterministic mode (no test-file dependency)
# ---------------------------------------------------------------------------

def _sse_bytes(event_type: str, payload: dict) -> bytes:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload)}\n"
        "\n"
    ).encode("utf-8")


class _FakeStream:
    """Minimal fake upstream HTTP stream for deterministic testing."""
    def __init__(self, chunks):
        self._lines = []
        for chunk in chunks:
            self._lines.extend(chunk.splitlines(keepends=True))
        self.closed = False

    def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def close(self):
        self.closed = True


class _FakeWebRuntime:
    """Fake web_search runtime — returns predetermined results, makes no HTTP calls."""
    def execute_web_search_call(self, call_item, counters, seen_signatures, request_id=""):
        call_id = call_item.get("call_id") or call_item.get("id") or "call_det"
        public_item = {
            "id": "wsc_det_1",
            "type": "web_search_call",
            "status": "completed",
            "call_id": call_id,
            "action": {
                "type": "search",
                "queries": ["dragon site:fse.anthro.fr"],
                "result_count": 1,
            },
        }
        output_item = {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps({
                "ok": True,
                "results": [{"title": "Dragon FSE", "url": "https://fse.anthro.fr/dragon"}],
            }),
        }
        sources = [{"url": "https://fse.anthro.fr/dragon", "title": "Dragon FSE"}]
        return public_item, output_item, sources


def _make_web_call_chunks(item_id="fc_web_det", call_id="call_web_det"):
    """Build a fake upstream SSE stream that contains a web_search function_call."""
    arguments = json.dumps({
        "action": "search",
        "query": "dragon site:fse.anthro.fr",
        "profile": "furry_fse",
    })
    return [
        _sse_bytes("response.created", {
            "type": "response.created",
            "response": {
                "id": "resp_det_web",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "test-model.gguf",
                "output": [],
            },
        }),
        _sse_bytes("response.output_item.added", {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "function_call",
                "status": "in_progress",
                "call_id": call_id,
                "name": "web_search",
                "arguments": "",
            },
        }),
        _sse_bytes("response.function_call_arguments.delta", {
            "type": "response.function_call_arguments.delta",
            "item_id": item_id,
            "output_index": 0,
            "delta": arguments,
        }),
        _sse_bytes("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "function_call",
                "status": "completed",
                "call_id": call_id,
                "name": "web_search",
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _make_final_message_chunks(text="contract verified"):
    """Build a fake upstream SSE stream for the second hop (final assistant message)."""
    return [
        _sse_bytes("response.created", {
            "type": "response.created",
            "response": {
                "id": "resp_det_final",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "test-model.gguf",
                "output": [],
            },
        }),
        _sse_bytes("response.output_item.added", {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "msg_det",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_bytes("response.output_text.delta", {
            "type": "response.output_text.delta",
            "item_id": "msg_det",
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        }),
        _sse_bytes("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg_det",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        }),
        _sse_bytes("response.completed", {
            "type": "response.completed",
            "response": {
                "id": "resp_det_final",
                "object": "response",
                "status": "completed",
                "model": "test-model.gguf",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_det",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        }),
        b"data: [DONE]\n\n",
    ]


# ---------------------------------------------------------------------------
# Deterministic mode
# ---------------------------------------------------------------------------

def run_deterministic(repo_root: str) -> dict:
    """Run deterministic web_search Codex contract check.

    Uses ResponsesStreamRuntime with a fake upstream stream and FakeWebRuntime.
    No live model or network calls are made.

    This check always produces pass or fail — never no_search — because the
    fake stream always contains a web_search function_call that the proxy must
    convert to a web_search_call public item.
    """
    sys.path.insert(0, repo_root)
    try:
        from proxy.qz_responses_stream import ResponsesStreamRuntime  # type: ignore
    except ImportError:
        try:
            sys.path.insert(0, os.path.join(repo_root, "proxy"))
            from qz_responses_stream import ResponsesStreamRuntime  # type: ignore
        except ImportError as exc:
            return {
                "status": "error",
                "error": f"Cannot import ResponsesStreamRuntime: {exc}",
                "mode": "deterministic",
            }

    web_call_chunks   = _make_web_call_chunks()
    final_chunks      = _make_final_message_chunks()
    call_id           = "call_web_det"

    def opener(body):
        has_output = any(
            isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
            for item in body.get("input") or []
        )
        if has_output:
            return _FakeStream(final_chunks)
        return _FakeStream(web_call_chunks)

    output_chunks = []
    try:
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",   # not used — stream_opener is provided
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=_FakeWebRuntime(),
            chunk_writer=output_chunks.append,
            stream_opener=opener,
            capture_enabled=False,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "search for dragons"}],
            }],
            "tools": [{"type": "web_search"}],
        }
        runtime.run(body, "test-model.gguf")
    except Exception as exc:
        return {
            "status": "error",
            "error": f"RuntimeError: {exc}",
            "mode": "deterministic",
        }

    stream_text = b"".join(output_chunks).decode("utf-8")
    events = parse_sse_events_with_payloads(stream_text)
    result = check_contract(events, mode="deterministic")
    result["mode"] = "deterministic"
    return result


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------

def run_live(proxy_base: str, model: str, timeout: int = 90) -> dict:
    """Run opportunistic live web_search contract check against the real proxy.

    This check may return "no_search" if the model does not call web_search.
    That is not a failure — live model tool-use is prompt-dependent.
    """
    import urllib.request
    import urllib.error

    search_prompt = (
        "Use web_search with action=search and profile=furry_fse "
        "to search for: dragon site:fse.anthro.fr\n"
        "Return only the first result URL. Do not explain."
    )
    body = {
        "model": model,
        "stream": True,
        "tools": [{"type": "web_search"}],
        "input": [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": search_prompt}],
        }],
    }
    req = urllib.request.Request(
        proxy_base.rstrip("/") + "/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local",
        },
    )

    stream_lines = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                stream_lines.append(raw_line.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            model_ready = None
            try:
                with urllib.request.urlopen(
                    proxy_base.rstrip("/") + "/qz/model/status", timeout=5
                ) as ms:
                    ms_data = json.loads(ms.read().decode("utf-8"))
                    model_ready = ms_data.get("selected_model_ready")
            except Exception:
                pass
            if model_ready is True:
                return {
                    "status": "error_503_despite_ready",
                    "error": str(exc),
                    "model_ready": True,
                    "mode": "live",
                }
            return {
                "status": "not_ready",
                "error": str(exc),
                "model_ready": model_ready,
                "mode": "live",
            }
        return {"status": "error", "error": str(exc), "mode": "live"}
    except Exception as exc:
        err_str = str(exc)
        if any(kw in err_str.lower()
               for kw in ("timed out", "timeout", "connection refused", "urlopen error")):
            return {"status": "error_timeout", "error": err_str, "mode": "live"}
        return {"status": "error", "error": err_str, "mode": "live"}

    stream_text = "".join(stream_lines)
    events = parse_sse_events_with_payloads(stream_text)
    result = check_contract(events, mode="live")
    result["mode"] = "live"
    return result


# ---------------------------------------------------------------------------
# Self-test mode (also exercised by tests/test_qz_web_search_contract_check.py)
# ---------------------------------------------------------------------------

def run_self_test() -> bool:
    """Run internal unit tests.  Returns True on success, False on failure."""
    failures = []

    def assert_eq(actual, expected, msg):
        if actual != expected:
            failures.append(f"{msg}: expected {expected!r}, got {actual!r}")

    def assert_true(value, msg):
        if not value:
            failures.append(f"expected True: {msg}")

    def assert_false(value, msg):
        if value:
            failures.append(f"expected False: {msg}")

    # ------------------------------------------------------------------
    # T1: parser produces (event_name, payload) tuples for output_item.* events
    # ------------------------------------------------------------------
    stream_t1 = (
        "event: response.output_item.added\n"
        'data: {"type":"response.output_item.added","output_index":0,'
        '"item":{"id":"wsc_1","type":"web_search_call","status":"in_progress"}}\n'
        "\n"
        "event: response.output_item.done\n"
        'data: {"type":"response.output_item.done","output_index":0,'
        '"item":{"id":"wsc_1","type":"web_search_call","status":"completed"}}\n'
        "\n"
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"status":"completed","model":"t","output":[]}}\n'
        "\n"
    )
    evts_t1 = parse_sse_events_with_payloads(stream_t1)
    names_t1 = [et for et, _ in evts_t1]
    assert_true("response.output_item.added" in names_t1,
                "T1: output_item.added present in parsed events")
    assert_true("response.output_item.done" in names_t1,
                "T1: output_item.done present in parsed events")
    assert_true("response.completed" in names_t1,
                "T1: response.completed present in parsed events")

    # ------------------------------------------------------------------
    # T2: check_contract detects web_search_call via item.type in payload
    # ------------------------------------------------------------------
    res_t2 = check_contract(evts_t1, mode="deterministic")
    assert_eq(res_t2["status"], "pass", "T2: correct contract → pass")
    assert_true(res_t2["checks"]["output_item_added_with_web_search_call_item"],
                "T2: added item detected via payload.item.type")
    assert_true(res_t2["checks"]["output_item_done_with_web_search_call_item"],
                "T2: done item detected via payload.item.type")

    # ------------------------------------------------------------------
    # T3: detection does NOT require event name to contain "web_search_call"
    # Event names are response.output_item.added/done — no "web_search_call" in them.
    # ------------------------------------------------------------------
    stream_t3 = (
        "event: response.output_item.added\n"
        'data: {"output_index":0,"item":{"type":"web_search_call","id":"wsc"}}\n'
        "\n"
        "event: response.output_item.done\n"
        'data: {"output_index":0,"item":{"type":"web_search_call","id":"wsc","status":"completed"}}\n'
        "\n"
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}\n'
        "\n"
    )
    evts_t3 = parse_sse_events_with_payloads(stream_t3)
    res_t3  = check_contract(evts_t3, mode="deterministic")
    assert_eq(res_t3["status"], "pass",
              "T3: passes without web_search_call in any event name")
    names_t3 = [et for et, _ in evts_t3]
    assert_false(any("web_search_call" in (n or "") for n in names_t3),
                 "T3: no event name should contain web_search_call")

    # ------------------------------------------------------------------
    # T4: fake lifecycle events → fail
    # ------------------------------------------------------------------
    stream_t4 = (
        "event: response.output_item.added\n"
        'data: {"output_index":0,"item":{"type":"web_search_call","id":"wsc"}}\n'
        "\n"
        # Fake events that must not appear (removed in issue #66)
        "event: response.web_search_call.in_progress\n"
        'data: {"type":"response.web_search_call.in_progress"}\n'
        "\n"
        "event: response.web_search_call.searching\n"
        'data: {"type":"response.web_search_call.searching"}\n'
        "\n"
        "event: response.output_item.done\n"
        'data: {"output_index":0,"item":{"type":"web_search_call","id":"wsc","status":"completed"}}\n'
        "\n"
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}\n'
        "\n"
    )
    evts_t4 = parse_sse_events_with_payloads(stream_t4)
    res_t4  = check_contract(evts_t4, mode="deterministic")
    assert_eq(res_t4["status"], "fail",
              "T4: fake lifecycle events in stream → fail")
    assert_false(res_t4["checks"]["fake_web_search_call_in_progress_absent"],
                 "T4: in_progress detected as present")
    assert_false(res_t4["checks"]["fake_web_search_call_searching_absent"],
                 "T4: searching detected as present")

    # ------------------------------------------------------------------
    # T5: no web_search_call items → no_search in live, fail in deterministic
    # ------------------------------------------------------------------
    stream_t5 = (
        "event: response.output_item.added\n"
        'data: {"output_index":0,"item":{"type":"message","id":"msg_1","role":"assistant"}}\n'
        "\n"
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}\n'
        "\n"
    )
    evts_t5     = parse_sse_events_with_payloads(stream_t5)
    res_t5_live = check_contract(evts_t5, mode="live")
    res_t5_det  = check_contract(evts_t5, mode="deterministic")
    assert_eq(res_t5_live["status"], "no_search",
              "T5: no web_search items in live mode → no_search (not fail)")
    assert_eq(res_t5_det["status"], "fail",
              "T5: no web_search items in deterministic mode → fail")

    # ------------------------------------------------------------------
    # T6: response.completed missing → fail
    # ------------------------------------------------------------------
    stream_t6 = (
        "event: response.output_item.added\n"
        'data: {"output_index":0,"item":{"type":"web_search_call","id":"wsc"}}\n'
        "\n"
        "event: response.output_item.done\n"
        'data: {"output_index":0,"item":{"type":"web_search_call","id":"wsc","status":"completed"}}\n'
        "\n"
        # No response.completed
    )
    evts_t6 = parse_sse_events_with_payloads(stream_t6)
    res_t6  = check_contract(evts_t6, mode="deterministic")
    assert_eq(res_t6["status"], "fail",
              "T6: missing response.completed → fail")
    assert_false(res_t6["checks"]["response_completed"],
                 "T6: response_completed check is False")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return False

    print(f"self-test: 6 test groups passed")
    return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Web search Codex contract checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["deterministic", "live", "self-test"],
        required=True,
        help=(
            "deterministic: run with fake stream + FakeWebRuntime (no live model). "
            "live: make real HTTP request to proxy. "
            "self-test: run internal unit tests."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Path to quantzhai repo root (deterministic mode; default: parent of scripts/)",
    )
    parser.add_argument(
        "--proxy-base",
        default="http://127.0.0.1:18080",
        help="Proxy base URL (live mode)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model identifier (live mode)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Request timeout in seconds (live mode)",
    )
    args = parser.parse_args()

    if args.mode == "self-test":
        ok = run_self_test()
        sys.exit(0 if ok else 1)

    if args.mode == "deterministic":
        repo_root = args.repo_root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        result = run_deterministic(repo_root)
    else:  # live
        result = run_live(args.proxy_base, args.model, args.timeout)

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
