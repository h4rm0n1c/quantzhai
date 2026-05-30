"""
Integration tests for network sandbox escalation.
Sends crafted HTTP requests to the running proxy and verifies the
synthetic escalation SSE is returned for each network block signal.
"""
import json, urllib.request

PROXY = "http://127.0.0.1:18180"
MODEL = "Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS"
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []

def check(label, cond, detail=""):
    status = PASS if cond else FAIL
    print(f"  {status}  {label}" + (f"\n        {detail}" if detail and not cond else ""))
    results.append((label, cond))

def sse_request(exec_output: str, call_id: str = "net_test_001") -> bytes:
    body = {
        "model": MODEL,
        "stream": True,
        "input": [
            {"type": "message", "role": "user", "content": "fetch https://example.com"},
            {
                "type": "function_call",
                "id": call_id, "call_id": call_id,
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "curl -v https://example.com"}),
                "status": "completed",
            },
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": exec_output,
            },
        ],
        "tools": [],
    }
    req = urllib.request.Request(
        f"{PROXY}/v1/responses",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local",
            "Accept": "text/event-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()

def check_escalation(signal_desc: str, output_text: str):
    """Verify a specific network block signal triggers transparent escalation."""
    print(f"\n=== {signal_desc} ===")
    cid = f"net_{signal_desc.replace(' ','_')[:20]}"
    try:
        raw = sse_request(output_text, call_id=cid)
        esc_cid = cid + "_qzesc"

        # Proxy must return synthetic SSE (not forward to LLM)
        has_require_esc = b"require_escalated" in raw
        has_completed = b"response.completed" in raw
        has_done = b"[DONE]" in raw
        # No reasoning events = LLM was NOT called
        no_llm = b"response.reasoning" not in raw

        check(f"[{signal_desc}] Returns escalation SSE", has_require_esc,
              f"raw snippet: {raw[max(0,raw.find(b'require')-30):raw.find(b'require')+50]}")
        check(f"[{signal_desc}] Short-circuits before LLM", no_llm,
              "reasoning events found — LLM was called instead of short-circuiting")
        check(f"[{signal_desc}] Stream completes cleanly", has_completed and has_done)
    except Exception as exc:
        check(f"[{signal_desc}] Request succeeded", False, str(exc))

# -----------------------------------------------------------------------
# Test each network block signal
# -----------------------------------------------------------------------

# 1. Codex network proxy allowlist block (most common)
check_escalation(
    "domain not in allowlist",
    "curl: (56) Recv failure: Connection reset by peer\nDomain not in allowlist."
)

# 2. Local/private network block
check_escalation(
    "local network policy",
    "curl: (7) Failed to connect\nSandbox policy blocks local/private network addresses."
)

# 3. MITM required
check_escalation(
    "mitm required",
    "curl: (35) SSL handshake failed\nMITM required for limited HTTPS."
)

# 4. x-proxy-error header in curl -v verbose output
check_escalation(
    "x-proxy-error header (allowlist)",
    "* Connected to 127.0.0.1\n< HTTP/1.1 403 Forbidden\n< x-proxy-error: blocked-by-allowlist\nDomain not in allowlist."
)

# 5. x-proxy-error local network header
check_escalation(
    "x-proxy-error header (local)",
    "< x-proxy-error: blocked-by-local-network-policy\nSandbox policy blocks local/private network addresses."
)

# -----------------------------------------------------------------------
# Negative: real connection refused must NOT escalate
# -----------------------------------------------------------------------
print("\n=== Real connection refused — must NOT escalate ===")
try:
    raw = sse_request(
        "curl: (7) Failed to connect to example.com port 443: Connection refused",
        call_id="no_esc_test"
    )
    # This is ambiguous (medium confidence in existing classifier), but with our
    # signals it should NOT trigger escalation since "Connection refused" alone
    # is not in SANDBOX_DENIAL_SIGNALS
    has_require_esc = b"require_escalated" in raw
    check("Real connection refused does NOT trigger escalation", not has_require_esc,
          "escalation fired on a non-sandbox signal")
except Exception as exc:
    check("Request completed", False, str(exc))

# -----------------------------------------------------------------------
print(f"\n{'='*52}")
passed = sum(1 for _, ok in results if ok)
print(f"  {passed}/{len(results)} checks passed")
if passed < len(results):
    print("  Failed:")
    for label, ok in results:
        if not ok:
            print(f"    - {label}")
