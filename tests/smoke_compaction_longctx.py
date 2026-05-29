#!/usr/bin/env python3
"""
smoke_compaction_longctx.py — Long-context compaction smoke test.

Uses real sessions from the session corpus (var/session-corpus/) to verify
that the Zenkai dual-signal architecture works at scale:

  Saiyan test: key discoveries (causal chains, constraints, outcomes) survive
               compaction and appear in the summary.

  Frieza test: planning scaffolding is eliminated, not preserved. The summary
               should not contain the literal openers of Frieza items.

Usage:
    python3 tests/smoke_compaction_longctx.py [--proxy-url http://host:port]
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "var" / "session-corpus"

sys.path.insert(0, str(ROOT))
from proxy.qz_responses import _decode_local_compaction_blob
from proxy.qz_survival_weight import score_items, FRIEZA_PATTERNS


_results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok))
    status = "ok " if ok else "FAIL"
    detail_str = f"  ({detail})" if detail and not ok else ""
    print(f"  [{status}] {name}{detail_str}")


def _post_compact(url: str, payload: dict, timeout: int = 180):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _find_session(min_tokens: int = 50_000, prefer_domain: str = "") -> dict | None:
    """Find a qualifying session from the corpus."""
    candidates = []
    for meta_path in CORPUS.glob("*/meta.json"):
        try:
            with open(meta_path) as f:
                m = json.load(f)
            est = m.get("estimated_tokens", 0)
            if est < min_tokens:
                continue
            input_path = meta_path.parent / "input.json"
            if not input_path.exists():
                continue
            candidates.append((est, m, input_path))
        except Exception:
            pass

    if not candidates:
        return None

    # Prefer the requested domain, then largest
    def key(c):
        est, m, _ = c
        domain_match = prefer_domain.lower() in m.get("cwd", "").lower()
        return (not domain_match, -est)

    candidates.sort(key=key)
    est, meta, input_path = candidates[0]

    with open(input_path) as f:
        items = json.load(f)

    return {"meta": meta, "items": items, "est_tokens": est}


def _extract_frieza_openers(items: list[dict], max_samples: int = 5) -> list[str]:
    """Return first few planning-narration openers found in items."""
    openers = []
    for item in items:
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        text = item.get("content", "")
        if not isinstance(text, str):
            continue
        for pat in FRIEZA_PATTERNS.values():
            m = pat.search(text)
            if m:
                opener = m.group(0).strip()[:60]
                if opener not in openers:
                    openers.append(opener)
                if len(openers) >= max_samples:
                    return openers
    return openers


def _extract_discovery_phrases(items: list[dict], max_samples: int = 5) -> list[str]:
    """Extract short phrases from Level 2 (concept) spans in the session."""
    from proxy.qz_survival_weight import score_text
    phrases = []
    for item in items:
        if item.get("type") != "message":
            continue
        text = item.get("content", "")
        if not isinstance(text, str) or len(text) < 50:
            continue
        spans = score_text(text)
        for s in spans:
            if s.level == 2 and s.text not in phrases and len(s.text) > 4:
                # Find the sentence containing this span
                for sent in text.split("."):
                    if s.text.lower() in sent.lower() and len(sent.strip()) > 20:
                        phrase = sent.strip()[:100]
                        if phrase not in phrases:
                            phrases.append(phrase)
                        break
            if len(phrases) >= max_samples:
                return phrases
    return phrases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-url", default="http://127.0.0.1:18180")
    parser.add_argument("--min-tokens", type=int, default=30_000)
    parser.add_argument("--domain", default="",
                        help="Prefer sessions from this domain (e.g. 'game', 'putty')")
    args = parser.parse_args()

    compact_url = f"{args.proxy_url}/v1/responses/compact"
    print(f"Long-context compaction smoke test")
    print(f"  proxy: {args.proxy_url}")
    print(f"  corpus: {CORPUS}")
    print()

    # --- find session ---
    session = _find_session(min_tokens=args.min_tokens, prefer_domain=args.domain)
    if not session:
        print(f"SKIP: no qualifying session in corpus (min_tokens={args.min_tokens})")
        print("  Run scripts/qz-extract-session-corpus first.")
        sys.exit(0)

    meta = session["meta"]
    items = session["items"]
    est = session["est_tokens"]
    domain = meta.get("cwd", "?").replace("/home/harri/", "~/")
    print(f"Session: {meta['session_id'][:24]}")
    print(f"  domain: {domain}")
    print(f"  source: {meta.get('source', '?')}")
    print(f"  items:  {len(items)}")
    print(f"  ~{est:,} estimated tokens")
    print()

    # Collect expected signals before compaction
    frieza_openers = _extract_frieza_openers(items)
    discovery_phrases = _extract_discovery_phrases(items)

    print(f"Frieza openers found in session ({len(frieza_openers)}):")
    for o in frieza_openers:
        print(f"    {o!r}")
    print(f"Discovery phrases found ({len(discovery_phrases)}):")
    for p in discovery_phrases[:3]:
        print(f"    {p!r}")
    print()

    # Use a sample of items if the session is very long (avoid timeout)
    input_items = items
    if len(items) > 400:
        # Take first 200 + last 100 items — captures context + recent state
        input_items = items[:200] + items[-100:]
        print(f"  (trimmed to {len(input_items)} items for smoke test speed)")

    # --- POST to /v1/responses/compact ---
    print("[1] POST /v1/responses/compact ...")
    try:
        resp = _post_compact(compact_url, {
            "model": "kuato",
            "input": input_items,
            "instructions": "You are a coding assistant.",
        })
    except Exception as exc:
        print(f"FAIL: request error: {exc}")
        sys.exit(1)

    check("HTTP response is response.compaction",
          resp.get("object") == "response.compaction",
          f"object={resp.get('object')}")

    output = resp.get("output") or []
    cmp_item = next((i for i in output if isinstance(i, dict) and i.get("type") == "compaction"), None)
    check("output contains compaction item", cmp_item is not None)
    if not cmp_item:
        sys.exit(1)

    blob = cmp_item.get("encrypted_content", "")
    check("blob is v3 (LLM anchored)", blob.startswith("localcmp:v3:"),
          f"prefix={blob[:20]}")

    decoded = _decode_local_compaction_blob(blob)
    check("blob decodes", decoded is not None)
    if not decoded:
        sys.exit(1)

    summary = decoded.get("summary_text", "") or ""
    check("summary is non-empty", len(summary) > 100, f"len={len(summary)}")

    meta_blob = decoded.get("metadata") or {}
    check("v3 succeeded (no fallback)",
          not meta_blob.get("v3_fallback_reason"),
          f"fallback={meta_blob.get('v3_fallback_reason')}")

    print()
    print(f"  summary length: {len(summary)} chars")
    print(f"  summary preview: {summary[:300]!r}")
    print()

    # --- Saiyan test: discovery phrases survived ---
    print("[2] Saiyan test — discovery phrases must survive ...")
    if discovery_phrases:
        survived = 0
        for phrase in discovery_phrases:
            # Check if key words from the phrase appear in the summary
            key_words = [w for w in phrase.lower().split()
                         if len(w) > 5 and w.isalpha()][:3]
            if key_words and all(w in summary.lower() for w in key_words):
                survived += 1
                check(f"discovery survived: {phrase[:60]!r}", True)
            else:
                check(f"discovery survived: {phrase[:60]!r}", False,
                      f"key_words={key_words} not all in summary")
        if discovery_phrases:
            rate = survived / len(discovery_phrases)
            check(f"discovery survival rate ≥50% ({survived}/{len(discovery_phrases)})",
                  rate >= 0.5, f"rate={rate:.0%}")
    else:
        print("  (no discovery phrases found in session sample — skip)")

    print()

    # --- Frieza test: planning scaffolding not preserved verbatim ---
    print("[3] Frieza test — planning openers must not appear verbatim in summary ...")
    if frieza_openers:
        for opener in frieza_openers[:3]:
            in_summary = opener.lower() in summary.lower()
            check(f"Frieza eliminated: {opener!r}", not in_summary,
                  "opener appears verbatim in summary")
    else:
        print("  (no Frieza openers found in session sample — skip)")

    print()
    passed = sum(1 for _, ok in _results if ok)
    total = len(_results)
    print("=" * 50)
    if passed == total:
        print(f"{passed}/{total} checks passed")
        print("ok compaction longctx smoke")
    else:
        failed = [n for n, ok in _results if not ok]
        print(f"{passed}/{total} passed — FAILED: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
