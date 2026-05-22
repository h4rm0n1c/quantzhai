# QuantZhai End-to-End Smoke Plan

Date: 2026-05-22
Status: Slice G — final audit slice. Smoke plan for post-audit fix-pass validation.

Audit series A–F complete. This plan exercises the full Codex ⇄ QuantZhai ⇄ llama.cpp
⇄ tools ⇄ observability pipeline before and after each fix pass.

---

## 0. Backend Preflight Gate (MANDATORY — abort if fails)

Before GPU checks, confirm the proxy resolved a launch model and requested start:

```bash
curl -s http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/backend/status | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('phase:', d.get('phase'))
print('launch_model_key:', d.get('launch_model_key'))
print('launch_model_path_basename:', d.get('launch_model_path_basename'))
print('launch_model_error:', d.get('launch_model_error'))
"
```

Required:
- `phase` is not "idle", "disabled", or "stopped" (typically "starting", "running", or "healthy")
- `launch_model_key` is non-empty
- `launch_model_path_basename` is non-empty
- `launch_model_error` is null/empty

If any of these fail:
- STOP. Record S1.2 as FAIL.
- Investigate why `_preload_last_model` failed to resolve/set the model.
- Check: `.env` QZ_MODEL_KEY, `var/models/` contents, `var/model-state.json`.

---

## 1. GPU Preflight Gate (MANDATORY — abort if fails)

All smoke runs assume:

```text
Repo state:
  cd ~/turboquant/quantzhai
  git status --short   # should be clean; no uncommitted changes affecting proxy behaviour

Environment:
  source scripts/qz-env            # loads all QZ_* defaults

Backend:
  Direct -m mode ONLY — no --models-dir
  Known-good model: e.g. kuato (Q4_K_S or Q5_K_M) or similar small model
  QZ_MODEL_DIR mounted to Docker /models
  $QZ_DOCKER_CMD available (may be "sudo docker")

Proxy:
  QZ_PROXY_HOST=127.0.0.1
  QZ_PROXY_PORT=18180
  QZ_SERVER_HOST=127.0.0.1
  QZ_SERVER_PORT=18084

SearXNG (for web_search tests):
  SEARXNG_BASE_URL set to local SearXNG instance (e.g. http://127.0.0.1:8890)
  FSE engine available in local SearXNG (confirmed in §64.12 live smoke)
  SoFurry NOT expected (absent from all config)
  e926/furbooru: deployment-dependent; skip furry_images test if unavailable

Observer terminals (open before smoke, leave running throughout):
  Terminal A: scripts/qz-thoughts
  Terminal B: scripts/qz-top
```

---

## 2. Smoke Matrix

### Group 1 — Backend/Model Startup

| id | purpose | action | expected result | artifact | audit finding | failure means | fix pass |
|---|---|---|---|---|---|---|---|
| S1.1 | Clean stop | `scripts/qz-down --force` | Container removed; proxy pid killed | `var/log/qz-*.log` | — | — | — |
| S1.2 | Start proxy + backend | `scripts/qz-up` | Proxy starts; backend autostarted; no `--models-dir` in docker command | proxy log, backend log | Slice A pipeline | backend not starting | — |
| S1.3 | Verify -m flag | `$QZ_DOCKER_CMD logs <container> 2>&1 \| grep -E "\-m |\-\-model"` | `-m /models/<selected>.gguf` present; `--models-dir` absent | container logs | Slice A §no-router-mode | --models-dir present = config bug | H |
| S1.4 | Model ready | `curl -s http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/model/status \| python3 -m json.tool` | `selected_model_ready: true`, `request_admission_state: ready`, `backend_loaded_model` matches selected | JSON response | Slice A §stage2, Slice D P1 | ready=false → wait or restart | — |
| S1.5 | Control plane | `curl -s http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/control-plane \| python3 -m json.tool` | `backend.phase: healthy`, `backend.loaded_model` populated | JSON response | Slice D metadata | phase not healthy → inspect backend logs | — |
| S1.6 | qz-top model display | Observe qz-top (Terminal B) | PROFILE panel shows selected model; `ready=true`, `admission=ready`; VRAM stabilises | qz-top TUI | Slice E MS1 | proxy-offline confusion | K |
| S1.7 | VRAM stabilization | Watch qz-top GPU panel for 10s | VRAM process column shows stable non-zero allocation; not fluctuating | qz-top VRAM panel | Slice E §token | VRAM=0 after model load = backend failed | — |

### Group 2 — Basic Codex Flows

| id | purpose | action | expected result | artifact | audit finding | failure means | fix pass |
|---|---|---|---|---|---|---|---|
| S2.1 | No-tool prompt | `scripts/qz-codex exec -m <model> --json --ephemeral 'Say: hello world'` | Codex receives final answer; no raw function_call JSON in output text | stdout/stderr | Slice C L1 | raw JSON → output_text artifact detection needed | J |
| S2.2 | Reasoning prompt | `scripts/qz-codex exec -m <model> --json --ephemeral 'Reason step by step: what is 17*23?'` | Answer includes calculation; qz-thoughts THOUGHT panel shows reasoning tokens | qz-thoughts Terminal A | Slice C §4 | thought panel blank → Slice A correction confirmed |  — |
| S2.3 | Multi-turn exchange | `scripts/qz-codex exec -m <model>` (interactive, 3 exchanges) | Conversation continues; no duplicate response.completed; no orphan tool | Codex output | Slice D response.id | multiple completions → response.id mismatch | I |
| S2.4 | qz-thoughts streams | During S2.2, watch Terminal A | THOUGHT panel shows deltas; ANSWER panel shows final text | qz-thoughts | Slice C §1 (correction) | blank panels → sse_event not firing (Slice A error) | — |
| S2.5 | response.id in qz-thoughts | After S2.1, read `state.response_id` in qz-thoughts source | `state.response_id` set from response.created | internal state | Slice D P1 | blank → not populated | I |

### Group 3 — web_search Capability Flow

| id | purpose | action | expected result | artifact | audit finding | failure means | fix pass |
|---|---|---|---|---|---|---|---|
| S3.1 | Capabilities | `curl -s http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/web-search/capabilities \| python3 -m json.tool` | `furry_fse`, `furry_images`, all 14 profiles listed; budget modes present; warnings if base URL unset | JSON response | Slice F §5 | missing profiles → VALID_WEB_SEARCH_PROFILES stale | fix already in ebdf87b |
| S3.2 | Broad search | `scripts/qz-codex exec -m <model> --json --ephemeral 'Use web_search: what is the latest llama.cpp release?'` | Search result returned; `profile=broad` or inferred; no localhost URL in output | Codex output | Slice C L2, Slice D §3 | localhost in output → retrieval endpoint leak | — |
| S3.3 | Coding search | Same as S3.2 with coding query | `selected_profile=coding` or similar | Codex output | Slice F §7 | wrong profile → routing config | — |
| S3.4 | FSE-only search | Prompt Codex to `web_search` with `profile="furry_fse"`, coding or story query on FSE | `selected_profile=furry_fse`; FSE results; `retrieval_source=fse` on results; no e926/furbooru in result | Codex output + qz-thoughts web row | Slice F §10 | no results → FSE not in local SearXNG; not available | L |
| S3.5 | FSE retrieval | If S3.4 returns result with `retrieval_available=true`, call `retrieve` on that URL | Retrieved prose content; no localhost URL; `retrieval_source=fse` in output | Codex output | Slice D §6 | localhost in output → endpoint redaction bug | — |
| S3.6 | furry_images | `profile="furry_images"` search | Results from e926/furbooru if available; skip if zero results (engines not in local SearXNG) | Codex output | Slice F §2, §3 | always zero results → engines not in local SearXNG | L |
| S3.7 | Explicit FSE override | `profile="furry", engines=["fse"]` | Only FSE results; no e926/furbooru | Codex output | Slice F §7 | mixing engines → profile_config bug | — |
| S3.8 | qz-thoughts web_search | During any search, watch Terminal A | Activity row shows web query, profile, results count | qz-thoughts | Slice E §2 | no activity row → telemetry not emitting | — |

### Group 4 — Tool Schema / Coercion

| id | purpose | action | expected result | artifact | audit finding | failure means | fix pass |
|---|---|---|---|---|---|---|---|
| S4.1 | Function-typed web_search replacement | POST to /v1/responses with `tools: [{type:"function",name:"web_search",...stale schema...}]` | Proxy replaces with proxy schema; `action="capabilities"` in upstream tool description; `report.replaced` in captures | `latest-dropped-tools.txt` | Slice B §A (ebdf87b) | stale schema passes → dedup bug (fixed) | — |
| S4.2 | Duplicate web_search dedup | POST with both `{type:"web_search"}` and `{type:"function",name:"web_search"}` | Single `web_search` tool in upstream request; no duplicate | `latest-forwarded.json` | Slice B §A | duplicate upstream → dedup not working | H |
| S4.3 | Malformed web_search JSON args | Prompt model or direct POST: trigger model to call `web_search` with `arguments="{not json}"` | Proxy coerce() returns error; error function_call_output injected next hop; no raw args in Codex output | next-hop request input | Slice B §B malformed web_search | raw JSON in Codex output → leak | J |
| S4.4 | Unknown tool call | Via captures/manual harness: inject `function_call` for `totally_unknown_tool` | Error result injected; `tool_call_error` telemetry emitted; model gets "not recognised" message | telemetry | Slice B §C | no error → unknown tool passes through | H |
| S4.5 | Dropped write_stdin | POST with `write_stdin` but no live exec session | write_stdin dropped; if model calls it anyway, `dropped_tool_names` triggers error | `latest-dropped-tools.txt` | Slice B §C | no error → dropped tool passes through | H |
| S4.6 | apply_patch coercion | Trigger model to call apply_patch with sibling-patch format | Proxy coerces; corrected call forwarded to Codex | Codex apply_patch item | Slice B §B apply_patch | parse error → coercion broken | H |
| S4.7 | Repeated-read advisory | Read same file twice in Codex session | Second read gets advisory output; `repeated_read_signal` in telemetry | telemetry | Slice B §B repeated-read | no signal → repeated-read disabled | — |
| S4.8 | Coercion telemetry (post-B2) | After fix-pass H: trigger any coercion | `coercion_succeeded` or `coercion_failed` in telemetry | telemetry | Slice B B2 gap | no event → B2 not implemented | H |

### Group 5 — Leak Vectors

| id | purpose | action | expected result | artifact | audit finding | failure means | fix pass |
|---|---|---|---|---|---|---|---|
| S5.1 | Output text artifact detection | If model outputs `*** Begin Patch` in output_text | Currently NOT detected (Slice C L4) — document baseline | Codex output | Slice C L4 critical | text passes through → implement J fix | J |
| S5.2 | Reasoning artifact abort | Trigger reasoning-only stream with patch JSON in thinking | `reasoning_only_aborted` telemetry; fallback message to Codex; no patch JSON in final output | Codex output + telemetry | Slice C L5 | patch JSON in reasoning reaches Codex → bug | — |
| S5.3 | Function call arg suppression | Watch forwarded-sse.raw capture during any tool call | No `response.function_call_arguments.delta` in forwarded SSE; no raw arg JSON in Codex final text | `forwarded-sse.raw` | Slice C L1, L7 | function_call delta in forwarded SSE → mapper bug | — |
| S5.4 | Tool result not as final text | After web_search, check Codex output text | No raw `{ok:true,action:search,result:{...}}` blob in final answer | Codex output | Slice C L2 | raw blob → proxy emitting tool result as text | — |

### Group 6 — Metadata Propagation

| id | purpose | action | expected result | artifact | audit finding | failure means | fix pass |
|---|---|---|---|---|---|---|---|
| S6.1 | response.id through streaming | Run S2.1; capture forwarded-sse.raw | response.id in `response.created` matches response.id in `response.completed` (no-tool case) | `forwarded-sse.raw` | Slice D P1 | mismatch → multi-hop synthesis | I |
| S6.2 | response.id multi-hop | Run any web_search (S3.2); capture forwarded-sse.raw | After tool continuation, synthesised response.completed has `resp_local_*` — document baseline mismatch | `forwarded-sse.raw` | Slice D P1 | unexpected match → threading already working | I |
| S6.3 | call_id roundtrip | During any web_search, inspect next-hop request body | `function_call.call_id == function_call_output.call_id` in next_input | `latest-request.json` | Slice D §4 | mismatch → call_id routing bug | H |
| S6.4 | usage in response.completed | Check forwarded-sse.raw response.completed | `usage.input_tokens > 0, output_tokens > 0` | `forwarded-sse.raw` | Slice D P1 usage | zero usage → drain failed or upstream omitted | I |
| S6.5 | Zero usage fallback | Trigger reasoning-only abort (S5.2) | `usage` in synthesised terminal may be all-zeros — document baseline | Codex output | Slice D P1 usage | non-zero → drain succeeded | I |
| S6.6 | model field correct | Check response.model in any forwarded event | Matches selected model key, not raw backend alias | `forwarded-sse.raw` | Slice D §3 | wrong model name → rewrite_sse_payload bug | — |
| S6.7 | cached_tokens/reasoning_tokens | If upstream emits them, check forwarded usage | `input_tokens_details.cached_tokens` and `output_tokens_details.reasoning_tokens` present if upstream provides | `forwarded-sse.raw` | Slice D P2 | absent when upstream has them → normalisation bug | — |

### Group 7 — Failure / Reconnect

| id | purpose | action | expected result | artifact | audit finding | failure means | fix pass |
|---|---|---|---|---|---|---|---|
| S7.1 | Proxy restart while qz-thoughts open | Kill qz-proxy, restart via `scripts/qz-up` | qz-thoughts shows `("proxy", "unavailable")` then `("proxy", "reconnected")`; thought panel preserved | qz-thoughts Terminal A | Slice E §4 | no reconnect → qz-thoughts dead loop | — |
| S7.2 | Backend stopped while qz-top open | `$QZ_DOCKER_CMD stop <container>` | qz-top shows `loaded: none` or `○` backend health; `phase` changes | qz-top Terminal B | Slice E MS1 | qz-top identical to proxy-offline confusion | K |
| S7.3 | Backend kill during request | If safe: `$QZ_DOCKER_CMD kill <container>` during active Codex request | `backend_died_after_healthy` set; qz-top shows DEATH; `stream_failed` or `stream_terminal_classified` in qz-thoughts | qz-top + qz-thoughts | Slice E §7 | no DEATH display → control-plane not updated | K |
| S7.4 | Model not ready rejection | Stop backend; send request while `selected_model_ready=false` | 503 with `qz.responses.error.v1`; `request_admission_state != ready`; telemetry `responses_rejected_*` | curl response | Slice E §7, Slice D P1 | no rejection → gate not working | — |
| S7.5 | Too-large model rollback | `POST /qz/model/select-and-restart` with a model that is known-too-large | Load fails; `last_load_error_type=insufficient_vram`; rollback to `last_good_key`; qz-top shows failed_candidate | `/qz/model/status` + qz-top | Slice E §7 | no rollback → rollback bug | — |
| S7.6 | Idle reconnect qz-thoughts | Leave qz-thoughts running for >30s idle | `idle_reconnected` status shown; `last_seq` preserved; no state reset | qz-thoughts | Slice E §4 | state cleared → idle reconnect bug | — |

---

## 3. Command Blocks

### Prerequisites and environment

```bash
cd ~/turboquant/quantzhai
source scripts/qz-env
echo "Proxy:  $QZ_PROXY_HOST:$QZ_PROXY_PORT"
echo "Backend: $QZ_SERVER_HOST:$QZ_SERVER_PORT"
echo "Model dir: $QZ_MODEL_DIR"
echo "Docker: $QZ_DOCKER_CMD"
git status --short
```

### Group 1 — Backend startup

```bash
# S1.1 Clean stop
scripts/qz-down --force

# S1.2 Start proxy + backend (auto-starts backend)
scripts/qz-up

# S1.3 Verify direct -m mode (run after backend starts ~30s)
$QZ_DOCKER_CMD logs "$QZ_CONTAINER" 2>&1 | grep -E -- '-m |--model[^s]'
# Expected: -m /models/<model>.gguf
$QZ_DOCKER_CMD logs "$QZ_CONTAINER" 2>&1 | grep -- '--models-dir'
# Expected: (empty — no match)

# S1.4 Model readiness
curl -s "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/model/status" | python3 -m json.tool | grep -E "selected_model_ready|request_admission_state|backend_loaded_model"

# S1.5 Control plane
curl -s "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/control-plane" | python3 -m json.tool | python3 -c "
import json,sys
d=json.load(sys.stdin)
b=d.get('backend',{})
m=d.get('models',{})
print('phase:', b.get('phase'))
print('loaded:', b.get('loaded_model'))
print('ready:', m.get('selected_model_ready'))
print('admission:', m.get('request_admission_state'))
"
```

### Group 2 — Basic Codex flows

```bash
# S2.1 No-tool prompt
scripts/qz-codex exec -m kuato --json --ephemeral 'Say exactly: hello world'

# S2.2 Reasoning prompt — watch qz-thoughts in Terminal A
scripts/qz-codex exec -m kuato --json --ephemeral 'Reason step by step: what is 17 multiplied by 23? Show your working.'

# S2.3 Multi-turn (interactive)
scripts/qz-codex exec -m kuato
# Inside Codex: type 3-4 short messages, then /quit

# (While any Codex session runs, watch Terminal A for thought/answer panels)
```

### Group 3 — web_search

```bash
# S3.1 Capabilities endpoint (operator check)
curl -s "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/web-search/capabilities" | python3 -m json.tool | python3 -c "
import json,sys
caps=json.load(sys.stdin)
profiles=list(caps.get('profiles',{}).keys())
print('profiles:', sorted(profiles))
print('warnings:', caps.get('warnings',[]))
print('base_url_configured:', caps.get('agent_api',{}).get('base_url_configured'))
"

# S3.2 Broad search via Codex
scripts/qz-codex exec -m kuato --json --ephemeral \
  'Use web_search with profile="broad" and budget_mode="quick" to find: current llama.cpp release. Report one sentence summary.'

# S3.4 FSE-only search
scripts/qz-codex exec -m kuato --json --ephemeral \
  'Use web_search with profile="furry_fse" and budget_mode="deep" to find stories about a black wizard on FSE. Report title and URL of the first result.'

# S3.5 FSE retrieval (if S3.4 returns retrieval_available=true result)
# Use the URL from the FSE result:
scripts/qz-codex exec -m kuato --json --ephemeral \
  'Use web_search: first call action="capabilities", then call action="retrieve", url="<URL_FROM_FSE_RESULT>", retrieval_source="fse", budget_mode="deep". Report the first paragraph.'

# S3.6 furry_images (skip if SearXNG lacks e926/furbooru)
scripts/qz-codex exec -m kuato --json --ephemeral \
  'Use web_search with profile="furry_images", budget_mode="quick" to search for: wolf. Report result count.'
```

### Group 4 — Tool schema / coercion

```bash
# S4.1 Function-typed web_search replacement check
# Post a request with stale function-style web_search schema
curl -s -X POST "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/v1/responses" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer local" \
  -d '{
    "model": "kuato",
    "stream": false,
    "input": [{"role":"user","content":"Say: replacement test done"}],
    "tools": [{"type":"function","name":"web_search","description":"Stale Codex web search schema","parameters":{"type":"object","properties":{"query":{"type":"string"}}}}]
  }' | python3 -m json.tool

# Check forwarded request — schema should be proxy schema with action="capabilities"
QZ_CAPTURE_MODE=latest python3 -c "
import json, pathlib
fwd = pathlib.Path('var/captures/latest-forwarded.json')
if fwd.exists():
    d = json.loads(fwd.read_text())
    tools = d.get('tools', [])
    for t in tools:
        if t.get('name') == 'web_search':
            desc = t.get('description','')
            print('has capabilities:', 'capabilities' in desc)
            print('first 100 chars:', desc[:100])
"

# S4.2 Duplicate dedup check (send both types)
curl -s -X POST "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/v1/responses" \
  -H "Content-Type: application/json" -H "Authorization: Bearer local" \
  -d '{
    "model": "kuato", "stream": false,
    "input": [{"role":"user","content":"tool count test"}],
    "tools": [{"type":"web_search"},{"type":"function","name":"web_search","description":"duplicate"}]
  }' | python3 -c "
import json,sys
# Just check it didn't error
print(json.load(sys.stdin).get('status','?'))
"
# Then check captures
python3 -c "
import json, pathlib
fwd = pathlib.Path('var/captures/latest-forwarded.json')
if fwd.exists():
    d = json.loads(fwd.read_text())
    ws_count = sum(1 for t in d.get('tools',[]) if t.get('name')=='web_search')
    print('web_search count upstream:', ws_count)  # expected: 1
"
```

### Group 5 — Leak vectors

```bash
# S5.3 Function call suppression check
# Enable capture mode, run a web_search, check forwarded SSE
QZ_CAPTURE_MODE=latest scripts/qz-codex exec -m kuato --json --ephemeral \
  'Use web_search with action="capabilities". Report one sentence.'

# After run:
python3 -c "
import pathlib
sse = pathlib.Path('var/captures/latest-forwarded-sse.raw').read_bytes() if pathlib.Path('var/captures/latest-forwarded-sse.raw').exists() else b''
if b'function_call_arguments' in sse:
    print('FAIL: function_call_arguments found in forwarded SSE')
else:
    print('OK: no function_call_arguments in forwarded SSE')
if b'127.0.0.1' in sse or b'localhost' in sse:
    print('FAIL: localhost URL found in forwarded SSE')
else:
    print('OK: no localhost URL in forwarded SSE')
"

# S5.4 Tool result as text check
# After a web_search, check final Codex output for raw tool result JSON
# (Manual check: does the final answer contain {ok:true, action:search, result:...}?)
```

### Group 6 — Metadata

```bash
# S6.1 response.id through no-tool streaming
QZ_CAPTURE_MODE=latest scripts/qz-codex exec -m kuato --json --ephemeral 'Say: metadata test'

python3 -c "
import pathlib, re
sse_raw = pathlib.Path('var/captures/latest-forwarded-sse.raw')
if not sse_raw.exists():
    print('no capture')
else:
    content = sse_raw.read_text()
    created_ids = re.findall(r'response\.created.*?\"id\":\s*\"([^\"]+)\"', content, re.DOTALL)[:2]
    completed_ids = re.findall(r'response\.completed.*?\"id\":\s*\"([^\"]+)\"', content, re.DOTALL)[:2]
    print('created response.id:', created_ids)
    print('completed response.id:', completed_ids)
    if created_ids and completed_ids:
        match = any(c in completed_ids for c in created_ids)
        print('ID match:', match)
"

# S6.4 usage in response.completed
python3 -c "
import json, pathlib, re
sse = pathlib.Path('var/captures/latest-forwarded-sse.raw')
if sse.exists():
    for line in sse.read_text().splitlines():
        if line.startswith('data:') and 'response.completed' in line:
            try:
                d = json.loads(line[5:])
                usage = d.get('response', {}).get('usage', {})
                print('input_tokens:', usage.get('input_tokens'))
                print('output_tokens:', usage.get('output_tokens'))
                print('cached_tokens:', usage.get('input_tokens_details', {}).get('cached_tokens'))
                print('reasoning_tokens:', usage.get('output_tokens_details', {}).get('reasoning_tokens'))
            except:
                pass
"
```

### Group 7 — Failure / reconnect

```bash
# S7.1 Proxy restart while qz-thoughts running (Terminal A)
# 1. Have qz-thoughts open in Terminal A
# 2. In main terminal:
pkill -f qz-proxy || true
sleep 2
scripts/qz-up
# 3. Watch Terminal A: expect "proxy unavailable" then "proxy reconnected"

# S7.2 Backend stop while qz-top running (Terminal B)
$QZ_DOCKER_CMD stop "$QZ_CONTAINER" 2>/dev/null || true
# Watch Terminal B: backend health should go ○; loaded model shows none

# S7.3 Backend kill during request (ONLY on non-production setup)
# 1. Start a long Codex generation
# 2. While running:
$QZ_DOCKER_CMD kill "$QZ_CONTAINER" 2>/dev/null || true
# 3. Watch qz-top for DEATH label; watch qz-thoughts for stream_terminal event
# 4. After: check model/status for backend_died_after_healthy

curl -s "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/model/status" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print('died:', d.get('backend_died_after_healthy'))"

# S7.4 Model not ready rejection (backend stopped)
$QZ_DOCKER_CMD stop "$QZ_CONTAINER" 2>/dev/null || true
sleep 3
curl -s -X POST "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/v1/responses" \
  -H "Content-Type: application/json" -H "Authorization: Bearer local" \
  -d '{"model":"kuato","stream":false,"input":[{"role":"user","content":"test"}]}' | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print('status:', d.get('status'), 'error:', d.get('error'))"
# Expected: 503 with "model not ready" or similar

# Restart backend after
curl -s -X POST "http://$QZ_PROXY_HOST:$QZ_PROXY_PORT/qz/recovery/trigger" \
  -H "Content-Type: application/json" \
  -d '{"action":"reload_selected_model","reason":"smoke test restart","confirm":"<QZ_RECOVERY_CONFIRM_PHRASE>"}' \
  | python3 -m json.tool
```

---

## 4. Expected Outputs

### Group 1 expected

```text
S1.3: $QZ_DOCKER_CMD logs output contains:
  -m /models/kuato-Q4_K_S.gguf   (or equivalent gguf filename)
  NO --models-dir

S1.4: /qz/model/status:
  "selected_model_ready": true
  "request_admission_state": "ready"
  "backend_loaded_model": "<model key>"

S1.5: /qz/control-plane:
  "phase": "healthy"
  "loaded_model": "<model key>"
  "backend_health_ok": true

S1.6 qz-top:
  PROFILE panel: shows selected model name
  ready=true  admission=ready
  GPU panel: non-zero util
```

### Group 2 expected

```text
S2.1: Final Codex output text: "hello world" (or similar)
  No raw JSON in output text

S2.2: Reasoning visible in qz-thoughts THOUGHT panel (not blank)
  Answer in ANSWER panel
  response.completed visible in backend section

S2.4: qz-thoughts THOUGHT panel shows streaming chars during reasoning
  ANSWER panel shows final answer chars
```

### Group 3 expected

```text
S3.1: capabilities.profiles contains:
  furry_fse, furry_images, furry, broad, coding, research, ...
  base_url_configured: true (if SEARXNG_BASE_URL set)
  warnings: [] or ["No SearXNG base URL"] if not set

S3.4: FSE results with:
  profile: furry_fse (or selected_profile: furry_fse)
  retrieval_source: fse on results where retrieval_available=true
  No localhost/127.0.0.1 in any result URL or output

S3.5: Retrieved content:
  content: first paragraph of FSE story (prose text)
  No http://127.0.0.1:8890/retrieve URL visible
  retrieval_source: fse
```

### Group 4 expected

```text
S4.1: Upstream tool description contains "action=\"capabilities\""
  NOT "stale Codex web search schema" text

S4.2: Upstream request has exactly 1 web_search tool entry

S4.3: No raw {not json} appears in Codex final output text
  Error injected to model next hop

S4.5: latest-dropped-tools.txt contains "write_stdin"
```

### Group 5 expected

```text
S5.3: forwarded-sse.raw contains NO bytes matching "function_call_arguments"
  forwarded-sse.raw contains NO bytes matching "127.0.0.1" or "localhost"
  (after a web_search run)

S5.4: Final Codex output text does NOT contain:
  {"ok":true,"action":"search","result":{...}}
  or similar raw tool result blob
```

### Group 7 expected

```text
S7.1: qz-thoughts backend panel:
  ("proxy", "unavailable") appears when proxy killed
  ("proxy", "reconnected") appears after restart
  last_seq resets to 0; new events accepted

S7.3: qz-top:
  "DEATH" label appears in MODEL panel in red
  runtime=<error_type> row

S7.4: Response to POST /v1/responses while backend down:
  503 status or equivalent
  error contains "not ready" or "model not found"
```

---

## 5. Failure Classification Guide

| symptom | probable cause | audit doc | fix pass |
|---|---|---|---|
| Backend `--models-dir` in logs | Router mode reintroduced; code regressed | Slice A §no-router | — |
| `selected_model_ready: false` after 120s | Backend failed to load; OOM; wrong model path | Slice D §3, Slice E §7 | — |
| qz-top shows `loaded: none` when proxy running | Control-plane `backend.loaded_model` empty; backend not healthy | Slice E MS1, MS4 | K |
| qz-top proxy-offline = no-model confusion | ModelStatus() empty state; no proxy-offline label | Slice E MS1 | K |
| qz-thoughts THOUGHT panel blank during reasoning | sse_event telemetry not emitting (was Slice A false finding; confirmed working) | Slice C §1 correction | — |
| qz-thoughts cannot tell model-not-ready from upstream failure | Same request_failed telemetry event | Slice E P1 | K |
| function_call delta in forwarded-sse.raw | Streaming mapper suppression broken | Slice C L7 | — |
| Patch/tool JSON in Codex final output text | Model-output tool JSON in output_text (L4) | Slice C L4 | J |
| Localhost URL in Codex output | retrieval endpoint not redacted | Slice D §3, Slice F §6 | — |
| Duplicate web_search upstream | Dedup broken | Slice B §A | H |
| Coercion failure → raw args in output | coerce() not called or error_result not injected | Slice B §B | H |
| zero usage in response.completed | _drain_stream_for_usage failed or upstream no response.completed | Slice D P1 | I |
| response.id mismatch in multi-hop | Synthesized _emit_completed uses timestamp ID | Slice D P1 | I |
| FSE search returns zero results | FSE engine not in local SearXNG | Slice F §3 | L |
| furry_images zero results | e926/furbooru not in local SearXNG | Slice F §3 | L |
| furry_images claims retrieval but prose unavailable | retrieval_expected=True misleading | Slice F §2 | L |
| No `coercion_succeeded` / `tool_schema_replaced` events | B2 not implemented | Slice B B2, Slice E P2 | H |
| No usage in qz-thoughts | Usage field not rendered in qz-thoughts | Slice E P2 | K |
| cached/reasoning tokens absent from qz-top | Not read from usage dict | Slice E P2 | K |
| qz-thoughts reconnect broken | TelemetryFeed reconnect loop not running | Slice E §4 | — |
| DEATH label missing when backend killed | backend_died_after_healthy not populated | Slice E §7 | K |

---

## 6. Fix-Pass Ordering After Smoke

### H — B2 Tool/Coercion Fixes (highest priority)

**Goal**: close the observability and correctness gaps in tool routing.

Changes:
1. `proxy/qz_tools.py`: Add `__post_init__` assertion to `ToolCoercionResult` (neither-set guard).
2. `proxy/qz_request_router.py._run_responses_locally`: Apply `error_result` for dropped/unknown non-proxy-local items in the hop loop.
3. `proxy/qz_tool_request.py`: Emit `tool_schema_replaced` telemetry event after normalisation when `report.replaced` is non-empty.
4. `proxy/qz_proxy_tools.py.completed_call_decision`: Emit `coercion_succeeded` / `coercion_failed` telemetry.
5. Tests: adapter_for_name, streaming coerce error fixture, neither-set, write_stdin dropped→called, tool_schema_replaced event, coercion_failed event.

Validates: S4.1, S4.2, S4.4, S4.5, S4.8.

### I — Metadata Fixes

**Goal**: response.id stability through multi-hop streaming; usage not lost in fallbacks.

Changes:
1. `proxy/qz_responses_stream.py.StreamRunState`: Add `upstream_response_id: str` field; populate from first `response.created` event; use in `_emit_completed`.
2. Document zero-usage-in-fallbacks as known gap pending upstream fix; add test asserting the behaviour is explicit not silent.
3. Add regression test: single-hop streaming response.id in response.created == response.id in response.completed.

Validates: S6.1, S6.2, S6.4.

### J — Stream Leak Fixes

**Goal**: detect model-output tool artifacts in output_text channel.

Changes:
1. `proxy/qz_responses_stream.py`: Add `_looks_like_output_text_tool_artifact(text)` check in `response.output_text.delta` accumulation — weaker signal than reasoning heuristic (only strong patch-envelope markers); emit telemetry `output_text_tool_artifact_detected`.
2. Do NOT abort automatically on first output_text artifact — collect a sample then check at `response.completed` path. Reasoning abort fires mid-stream; output_text abort is higher risk of false-positive.
3. Add SSE fixture: `output_text_tool_json.raw` (patch envelope in output_text delta).
4. Add test: `test_output_text_patch_envelope_detected_in_telemetry`.

Validates: S5.1, S5.2.

### K — Observability Fixes

**Goal**: qz-top and qz-thoughts show useful state when proxy is offline, model rejected, or tokens missing.

Changes:
1. `scripts/qz-top`: Add "PROXY OFFLINE" message in PROFILE panel when `model_status_from_control_plane(None)` returns empty state (when cp is None).
2. `scripts/qz-top`: Read `cached_tokens` and `reasoning_tokens` from `request_completed.usage`; display in RATES panel as `cached=N` and `reason=N` when non-zero.
3. `proxy/qz_control_plane.py`: Add `prompt_files`, `reasoning_level`, `reasoning_policy`, `sampling`, `selected_context_length`, `backend_context_length` to `/qz/control-plane` response.
4. `scripts/qz-thoughts`: Add usage row (in/out tokens) from `request_completed` event payload.
5. Tests: proxy-offline display, cached/reasoning token display, control-plane prompt_files field.

Validates: S1.6, S7.2, S7.4.

### L — Search Profile Fixes

**Goal**: accurate retrieval claim for furry_images; capabilities warns about unavailable engines.

Changes:
1. `proxy/qz_tool_web.py._profile_retrieval_expected`: Add `"furbooru"` and `"e926"` to exclusion list — image metadata engines do not imply prose retrieval. OR: add `retrieval_expected: false` to `config/default/search.json furry_images` profile.
2. `proxy/qz_tool_web.py.build_web_search_capabilities`: When `_allowed_engines_cache` is non-empty and a profile's engines are not in it, add warning to `capabilities.warnings`: `"Profile {name} engines not confirmed by local probe: {engines}"`.
3. Tests: furry_images retrieval_expected=False; capabilities warning when engine not in probe.
4. Live: probe local SearXNG for SoFurry engine; if found, add `furry_sofurry` profile.

Validates: S3.6 (furry_images retrieval), S3.1 (capabilities warning).

### M — Final Live Smoke

**Goal**: rerun full smoke matrix after H–L fixes; confirm all P0/P1 gaps resolved.

Use result template from §7.

---

## 7. Manual Smoke Result Template

```
=== QuantZhai Smoke Run ===

Date/Time: ___________________
Commit SHA: ___________________  (git rev-parse HEAD)
Selected model: ___________________
Context: QZ_CONTEXT=___  QZ_BATCH=___  QZ_UBATCH=___
GPUs: ___  VRAM available: ___  VRAM used after load: ___
SearXNG URL: ___________________
SearXNG FSE available: yes / no / untested
SearXNG e926/furbooru available: yes / no / untested
SoFurry in local SearXNG: yes / no / untested

Fix pass being validated: H / I / J / K / L / M (circle one or list)

=== Smoke Results ===

Group 1 — Backend/Model
S1.1  clean stop:              PASS / FAIL / SKIP
S1.2  qz-up:                   PASS / FAIL / SKIP
S1.3  -m /models/ verified:    PASS / FAIL / SKIP
S1.4  selected_model_ready:    PASS / FAIL / SKIP
S1.5  control plane healthy:   PASS / FAIL / SKIP
S1.6  qz-top model display:    PASS / FAIL / SKIP
S1.7  VRAM stable:             PASS / FAIL / SKIP

Group 2 — Basic Codex
S2.1  no-tool prompt:          PASS / FAIL / SKIP
S2.2  reasoning prompt:        PASS / FAIL / SKIP
S2.3  multi-turn:              PASS / FAIL / SKIP
S2.4  qz-thoughts streams:     PASS / FAIL / SKIP
S2.5  response.id in state:    PASS / FAIL / SKIP

Group 3 — web_search
S3.1  capabilities endpoint:   PASS / FAIL / SKIP
S3.2  broad search:            PASS / FAIL / SKIP
S3.3  coding search:           PASS / FAIL / SKIP
S3.4  FSE-only search:         PASS / FAIL / SKIP / N/A(no FSE)
S3.5  FSE retrieval:           PASS / FAIL / SKIP / N/A
S3.6  furry_images:            PASS / FAIL / SKIP / N/A(no e926)
S3.7  explicit FSE override:   PASS / FAIL / SKIP
S3.8  qz-thoughts web row:     PASS / FAIL / SKIP

Group 4 — Tool Schema/Coercion
S4.1  function-typed replaced: PASS / FAIL / SKIP
S4.2  duplicate deduped:       PASS / FAIL / SKIP
S4.3  malformed args:          PASS / FAIL / SKIP
S4.4  unknown tool error:      PASS / FAIL / SKIP
S4.5  dropped write_stdin:     PASS / FAIL / SKIP
S4.6  apply_patch coercion:    PASS / FAIL / SKIP
S4.7  repeated-read advisory:  PASS / FAIL / SKIP
S4.8  coercion telemetry:      PASS / FAIL / SKIP / N/A(pre-H)

Group 5 — Leak Vectors
S5.1  output_text artifact:    BASELINE / DETECTED / SKIP
S5.2  reasoning artifact abort:PASS / FAIL / SKIP
S5.3  function_call suppressed:PASS / FAIL / SKIP
S5.4  tool result not in text: PASS / FAIL / SKIP

Group 6 — Metadata
S6.1  response.id no-tool:     PASS / FAIL / SKIP
S6.2  response.id multi-hop:   BASELINE / FIXED / SKIP
S6.3  call_id roundtrip:       PASS / FAIL / SKIP
S6.4  usage in completed:      PASS / FAIL / SKIP
S6.5  zero-usage fallback:     BASELINE / SKIP
S6.6  model field correct:     PASS / FAIL / SKIP
S6.7  cached/reasoning tokens: PASS / FAIL / SKIP / N/A

Group 7 — Failure/Reconnect
S7.1  proxy restart reconnect: PASS / FAIL / SKIP
S7.2  backend stop qz-top:     PASS / FAIL / SKIP
S7.3  backend kill DEATH:      PASS / FAIL / SKIP
S7.4  not-ready rejection:     PASS / FAIL / SKIP
S7.5  too-large rollback:      PASS / FAIL / SKIP
S7.6  idle reconnect:          PASS / FAIL / SKIP

=== Failures ===
(List FAIL items with error text and mapped fix pass)

1. S___: ___________________  → fix pass ___
2. S___: ___________________  → fix pass ___
...

=== Captures ===
var/captures/latest-forwarded-sse.raw: saved / not saved
var/captures/latest-forwarded.json: saved / not saved
var/captures/latest-dropped-tools.txt: saved / not saved

=== Final Recommendation ===
Proceed to fix pass: H / I / J / K / L / M (or "all passed, ship")
Blocking issue: ___________________
```

---

## 8. Audit Series Summary

The following audit series is now complete:

| Slice | Status | Output |
|---|---|---|
| A — Streaming/tool/reasoning contract | ✅ | `docs/runtime-streaming-tool-contract-audit.md` |
| B — Tool schema/coercion audit | ✅ | `docs/tool-schema-coercion-audit.md` |
| C — Streaming event mapper | ✅ | `docs/streaming-event-mapper-audit.md` |
| D — Metadata propagation | ✅ | `docs/metadata-propagation-audit.md` |
| E — qz-thoughts/qz-top observability | ✅ | `docs/observability-ui-audit.md` |
| F — Search profile granularity | ✅ | `docs/search-profile-granularity-audit.md` |
| G — End-to-end smoke plan | ✅ | `docs/end-to-end-smoke-plan.md` |

**Code freeze audit phase: COMPLETE.**

**Next: Fix pass H** — B2 tool/coercion fixes (ToolCoercionResult guard, non-streaming
dropped-tool gap, coercion/schema telemetry, missing tests).
