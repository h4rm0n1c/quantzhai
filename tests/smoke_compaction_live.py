#!/usr/bin/env python3
"""
Live compaction smoke test — requires qz-up running (proxy + llama.cpp).

Does NOT use codex exec. Drives the proxy directly over HTTP so we control
exactly when compact_threshold fires. Uses /tmp/linuxstreamtools-source as
the scratch repo to give the model real files to summarise.

Test flow:
  1. Build a multi-turn input from real linuxstreamtools file content —
     enough chars to exceed a low compact_threshold.
  2. POST to /v1/responses with compact_threshold set low.
     Proxy should auto-compact and return response.compaction (no upstream hit).
  3. Decode the blob — verify it's v2 with summary_text.
  4. POST a follow-up: compaction blob + new user message asking a question
     about the content. Real model generates the answer.
  5. Assert the model's answer is non-empty and not an error.

Usage:
    python3 tests/smoke_compaction_live.py [--proxy-url http://host:port]
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from proxy.qz_responses import _decode_local_compaction_blob  # noqa: E402

SCRATCH_SRC = Path("/tmp/linuxstreamtools-source")
# Low enough that a few real file reads push past it, high enough that the
# single initial user message doesn't immediately compact.
COMPACT_THRESHOLD = 500


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post_json(url, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _get_json(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _sse_collect(url, payload, timeout=120):
    """POST and collect a full non-streaming response via the SSE stream."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": "Bearer local",
        },
    )
    events = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = b""
        while True:
            chunk = r.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                block, buf = buf.split(b"\n\n", 1)
                ev_type = None
                ev_data = None
                for line in block.splitlines():
                    line = line.decode("utf-8", errors="replace")
                    if line.startswith("event:"):
                        ev_type = line[6:].strip()
                    elif line.startswith("data:"):
                        ev_data = line[5:].strip()
                if ev_data and ev_data != "[DONE]":
                    try:
                        events.append((ev_type, json.loads(ev_data)))
                    except json.JSONDecodeError:
                        pass
                if ev_data == "[DONE]":
                    break
    return events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role, text):
    part_type = "output_text" if role == "assistant" else "input_text"
    part = {"type": part_type, "text": text}
    if role == "assistant":
        part["annotations"] = []
    return {"type": "message", "role": role, "content": [part]}


def _build_history_from_scratch():
    """Read real files from the scratch repo to build a realistic multi-turn history."""
    if not SCRATCH_SRC.exists():
        return None, "scratch not found at " + str(SCRATCH_SRC)

    turns = []
    files_read = []
    for path in sorted(SCRATCH_SRC.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        if path.suffix in (".pyc", ".png", ".jpg", ".gif", ".mp3", ".ico"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = path.relative_to(SCRATCH_SRC)
        turns.append(_msg("user", f"Read this file — {rel}:\n\n{text[:1200]}"))
        turns.append(_msg("assistant", f"I've read {rel}. It contains: {text[:200]!r}..."))
        files_read.append(str(rel))
        if len(turns) >= 10:
            break

    if not turns:
        return None, "no readable files in scratch repo"

    return turns, files_read


def _proxy_model(proxy_url):
    status = _get_json(f"{proxy_url}/qz/status")
    if not status:
        return None
    backend = status.get("backend", {})
    return (
        backend.get("selected_key")
        or backend.get("selected_backend_id")
        or backend.get("loaded_model")
    )


def _extract_text(output_items):
    texts = []
    for item in output_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
        elif item.get("type") == "reasoning":
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "reasoning_text":
                    texts.append(part.get("text", ""))
    return " ".join(texts).strip()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

_results = []

def check(name, cond, detail=""):
    ok = bool(cond)
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"\n         {detail}" if not ok else ""))
    _results.append((name, ok))
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-url", default="http://127.0.0.1:18180")
    args = parser.parse_args()
    proxy_url = args.proxy_url.rstrip("/")
    responses_url = f"{proxy_url}/v1/responses"

    # 1. Proxy health
    model = _proxy_model(proxy_url)
    if not model:
        print(f"ERROR: proxy not reachable at {proxy_url} — run ./scripts/qz-up first")
        sys.exit(1)
    print(f"proxy ok — model: {model}")

    # 2. Build realistic multi-turn history from linuxstreamtools files
    history, meta = _build_history_from_scratch()
    if history is None:
        print(f"ERROR: {meta}")
        sys.exit(1)
    files_read = meta
    print(f"history built — {len(history)} turns from {len(files_read)} file(s): {files_read[:4]}")

    # Append a user turn that asks for a summary
    history.append(_msg("user", "Based on what you've read, what is the main purpose of this project? Give a one-sentence answer."))

    from proxy.qz_responses import _estimate_items_tokens
    tok_estimate = _estimate_items_tokens(history)
    print(f"estimated input tokens: {tok_estimate}  compact_threshold: {COMPACT_THRESHOLD}")

    if tok_estimate <= COMPACT_THRESHOLD:
        print("WARNING: input is below threshold — compaction may not trigger. "
              "Consider adding more files to SCRATCH_SRC.")

    # 3. POST with compact_threshold — proxy should auto-compact and return
    print("\n[1] POST with compact_threshold → expect response.compaction ...")
    status, body = _post_json(responses_url, {
        "model": model,
        "stream": False,
        "input": history,
        "context_management": {"compact_threshold": COMPACT_THRESHOLD},
    }, timeout=15)

    check("HTTP 200", status == 200, f"got {status}: {str(body)[:200]}")
    check("object is response.compaction", body.get("object") == "response.compaction",
          f"object={body.get('object')} keys={list(body.keys())}")
    check("has output list", isinstance(body.get("output"), list))

    cmp_item = next(
        (i for i in (body.get("output") or []) if isinstance(i, dict) and i.get("type") == "compaction"),
        None,
    )
    check("first output item is compaction", cmp_item is not None, str(body.get("output", [])[:1]))

    blob = (cmp_item or {}).get("encrypted_content", "")
    check("blob is v2 format", blob.startswith("localcmp:v2:"), blob[:40])

    payload = _decode_local_compaction_blob(blob)
    check("blob decodes to dict", payload is not None)
    check("summary_text present", bool((payload or {}).get("summary_text")),
          str(payload)[:200] if payload else "")
    check("depth >= 1", (payload or {}).get("depth", 0) >= 1)

    if payload:
        print(f"\n  summary preview: {payload['summary_text'][:280]!r}")

    # 4. Follow-up: replay compaction blob + ask a new question → real model answers
    print("\n[2] Follow-up with compaction blob → real model should answer ...")

    follow_input = list(body.get("output") or [])
    follow_input.append(_msg("user",
        "What shell scripts or executables does this project provide? "
        "Name them if you can recall from the earlier context."))

    try:
        events = _sse_collect(responses_url, {
            "model": model,
            "stream": True,
            "input": follow_input,
        }, timeout=120)
    except Exception as exc:
        check("follow-up request succeeded", False, str(exc))
        events = []

    if events:
        final = next(
            (ev for _, ev in reversed(events)
             if isinstance(ev, dict) and ev.get("type") == "response.completed"),
            None,
        )
        answer = ""
        if final:
            answer = _extract_text(final.get("response", {}).get("output", []))

        check("follow-up got response.completed", final is not None,
              f"event types seen: {[t for t, _ in events[-5:]]}")
        check("model produced non-empty answer", len(answer) > 20,
              f"answer: {answer!r}")
        if answer:
            print(f"\n  model answer: {answer[:400]!r}")

    # 5. Summary
    passed = sum(1 for _, ok in _results if ok)
    total = len(_results)
    print(f"\n{'='*50}")
    if passed == total:
        print(f"{passed}/{total} checks passed")
        print("ok compaction live smoke")
    else:
        failed = [n for n, ok in _results if not ok]
        print(f"{passed}/{total} passed — FAILED: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
