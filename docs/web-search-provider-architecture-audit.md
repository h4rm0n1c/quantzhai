# Web Search Provider Architecture Audit

Date: 2026-05-22
Status: Post-decoupling — provider guidance active. See also: docs/search-provider-boundary-audit.md

---

## 1. Problem Statement

`web_search` with `profile=furry_fse` has returned unrelated broad/general results
despite the source-strict guarantee. Direct FSE queries through `~/searchengines/`
work correctly. QuantZhai's web_search obscured whether failure happened in profile
routing, engine filtering, SearXNG request construction, response parsing, fallback
routing, source-strict filtering, or retrieval annotation.

---

## 2. Current Architecture Map

### 2.1 Data-flow

```
model tool call (action=search, profile=furry_fse, query=...)
  └─ WebSearchToolAdapter.coerce()        validate JSON shape
  └─ execute_web_search_call()            budget/counter enforcement
  └─ _search_web(query, profile, ...)     profile routing + orchestration
        ├─ _profile_config(profile, q)    resolve profile → engines, categories,
        │                                 source_strict, expected_*, fallback_profiles
        ├─ _filter_engines(engines)       remove blocked/probe-unavailable engines
        ├─ _profile_source_strict(...)    compute source_strict flag
        ├─ runtime_log(latest-web-search-request.json)
        ├─ _query_searxng(q, cats, engs)  ← HTTP call to SearXNG /search?format=json
        │     ├─ extract provider_reported_count (DIAGNOSTIC ONLY)
        │     ├─ parse results → len = parsed_result_count
        │     ├─ compute count_mismatch
        │     └─ runtime_log(latest-web-search-provider-raw-summary.json)
        ├─ source-strict filter           discard results not from expected sources
        ├─ compute accepted_result_count
        ├─ runtime_log(latest-web-search-normalized.json)
        ├─ runtime_log(latest-web-search-route.json)
        └─ fallback loop (suppressed when source_strict=true)
```

### 2.2 Components

| Component | File | Role |
|---|---|---|
| `WebSearchToolAdapter` | `proxy/qz_tool_web.py` | Coercion, tool schema, lifecycle spec |
| `WebSearchRuntime` | `proxy/qz_tool_web.py` | All search/retrieval execution |
| `_search_web` | `proxy/qz_tool_web.py` | Profile routing, orchestration, fallback |
| `_query_searxng` | `proxy/qz_tool_web.py` | SearXNG HTTP call, result parsing |
| `_profile_config` | `proxy/qz_tool_web.py` | Profile → engines/categories/strict policy |
| `_filter_engines` | `proxy/qz_tool_web.py` | Removes blocked/probe-unavailable engines |
| `_profile_source_strict` | `proxy/qz_tool_web.py` | Strict mode determination |
| `_result_matches_expected_source` | `proxy/qz_tool_web.py` | Post-parse source filtering |
| `build_web_search_capabilities` | `proxy/qz_tool_web.py` | capabilities action response |
| `load_search_config` | `proxy/qz_search_config.py` | Config file loading/merging |
| `config/default/search.json` | config | Profile definitions, engine routing, budgets |
| `config/user/search.json` | config | Local overrides (never committed) |
| `~/searchengines/scripts/searxng-agent-api.py` | external | Agent API proxy (SearXNG + retrieval) |
| `~/searchengines/scripts/fetch-fse-story.py` | external | Single-story retrieval (not search) |
| `~/searchengines/scripts/searxng-query.sh` | external | Direct SearXNG query wrapper for debugging |

### 2.3 Provider IDs

| provider_id | Description | Status |
|---|---|---|
| `searxng` | SearXNG local instance `/search?format=json` | Implemented |
| `fse_direct` | Direct FSE search bypassing SearXNG | Not implemented (see §4) |
| `agent_retrieve` | Agent API `/retrieve` for structured retrieval | Implemented |
| `open_page` | Direct HTTP fetch of a URL | Implemented |

---

## 3. Known Failure Modes Before This Audit

### 3.1 Blind debugging

Before this audit, these were invisible to operators:

| Layer | What was missing |
|---|---|
| SearXNG request | Constructed query params not logged |
| Raw response | provider_reported_count vs parsed count not captured |
| Post-parse | Parsed vs accepted result counts not logged |
| Source filter | Discard reason too coarse |
| Fallback | Why fallback was/wasn't used unclear |
| Count trust | SearXNG `number_of_results=0` could mask real results |

### 3.2 SearXNG reported count bug

SearXNG's `number_of_results` field in JSON responses is unreliable. The fse engine
has been observed returning `number_of_results=0` while returning valid parsed results.

**Before this fix:** `number_of_results` was not used for routing (already safe), but
there was no diagnostic to capture the discrepancy.

**After this fix:** `count_mismatch`, `provider_reported_count`, and
`parsed_result_count` are captured in every search result and in all trace logs.
The `warnings` list includes `searxng_result_count_mismatch` when mismatch occurs.

**Rule: never use provider-reported count for routing or fallback. Always use
`len(parsed results)` (for routing) and `len(accepted results)` (for source-strict profiles).**

### 3.3 Source-strict profile may get zero engines after filtering

If the SearXNG probe hasn't been run or the `fse` engine isn't in the probe cache,
`_filter_engines` silently drops `fse` from the engine list. The search is sent
to SearXNG with no `engines=` parameter. SearXNG may then route to general engines
and return non-FSE results. The source-strict filter catches and discards them, but
the operator sees zero results with no clear explanation.

**Mitigation now in place:** `build_web_search_capabilities` warns when `furry_fse`
is configured but `fse` is absent from the SearXNG probe.

---

## 4. Direct FSE Provider — Honest Findings

### 4.1 What ~/searchengines/ provides

```
~/searchengines/scripts/
  fetch-fse-story.py         ← retrieves ONE story by URL or ID (not search)
  fetch-fse-story.sh         ← shell wrapper for the above
  searxng-query.sh           ← direct SearXNG query wrapper (still uses SearXNG)
  searxng-agent-api.py       ← proxy server: /search proxies SearXNG + /retrieve dispatches fetchers
  searxng-test-fse-module.sh ← tests fse.py engine module inside searxng-core container
```

### 4.2 There is no direct FSE search

`~/searchengines/` has no script that searches `fse.anthro.fr` directly (i.e. scrapes
the story list/search endpoint) independently of SearXNG. FSE searching runs through
SearXNG's `fse` engine module inside the `searxng-core` Docker container.

`fetch-fse-story.py` is a retrieval helper only: it fetches a single known story URL
and returns structured JSON. It is not a search tool.

### 4.3 Known-good direct FSE invocation

The working direct FSE invocation (via SearXNG) is:

```bash
cd ~/searchengines
SEARX=http://127.0.0.1:8888 scripts/searxng-query.sh --engine fse "dragon transformation" | jq '.results | length, .results[0:3]'
```

This queries SearXNG directly with `engine=fse`, bypassing QuantZhai's proxy layer.

Or equivalently with curl:

```bash
curl -sS "$SEARXNG_BASE/search?q=dragon+transformation&format=json&engines=fse" | jq '.results | length, .results[0:3]'
```

### 4.4 fse_direct provider: not implemented

`fse_direct` is defined as a provider concept in `build_web_search_capabilities` and
in per-profile `provider_preference`, but it is NOT implemented. Implementing it would
require a new scraper for `fse.anthro.fr`'s search endpoint.

**Current routing for `profile=furry_fse`:**
1. Engines list: `["fse"]`
2. After probe filter: `["fse"]` if fse in probe, else `[]`
3. SearXNG `/search?engines=fse&q=...`
4. Source-strict filter: discard non-`fse.anthro.fr` results

**Future `fse_direct` routing (not yet implemented):**
1. Call `fse_direct` provider directly with query → list of fse.anthro.fr results
2. Normalize to QuantZhai result shape
3. On failure: return `provider_unavailable`, never broad fallback

**capabilities shows:**
```json
"fse_direct": { "available": false, "provider_id": "fse_direct",
  "note": "Direct FSE provider not implemented. FSE search runs through SearXNG fse engine." }
```

---

## 5. Provider Trace Logs

Every search now writes four files under `var/`:

| File | Content | When written |
|---|---|---|
| `latest-web-search-request.json` | Query, profile routing decision, engines before/after filter | Before SearXNG call |
| `latest-web-search-provider-raw-summary.json` | Raw provider counts, mismatch flag, unresponsive engines | After SearXNG response |
| `latest-web-search-normalized.json` | Accepted count, discarded count, result URLs | After source filtering |
| `latest-web-search-route.json` | Full routing log: profile, engines, fallback, all counts | At route conclusion |

Fields in every log:

```
provider_id
query
requested_profile / selected_profile
requested_engines_before_filter / after_filter
categories_before / after
source_strict
provider_reported_count    ← DIAGNOSTIC ONLY
parsed_result_count
accepted_result_count
wrong_source_results_discarded
count_mismatch
fallback_used
fallback_suppressed_reason
warnings
```

Local endpoints are never written to model-visible output. Log files may contain
sanitized local endpoint details if useful for debugging.

---

## 6. Source-Strict Semantics

### 6.1 What triggers source_strict

```python
_profile_source_strict(profile, cfg_source_strict, explicit_engines):
  return (
    cfg_source_strict          # profile config: source_strict=true
    or profile == "furry_fse"  # always pinned by name
    or all(e == "fse" for e in explicit_engines)  # explicit fse-only override
  )
```

### 6.2 What source_strict enforces

1. `fallback_profiles` cleared before and after provider call — no broadening
2. After parsing: discard all results not matching `expected_engines`, `expected_domains`,
   or `expected_retrieval_sources` from the profile config
3. If all results discarded: return zero results + `source_strict_warning`
4. Never route to `fse_direct` and also ask SearXNG broad engines — if `fse_direct` is
   unavailable and SearXNG `fse` is unconfigured, return `provider_unavailable`, not broad

### 6.3 furry_fse config (config/default/search.json)

```json
"furry_fse": {
  "engines": ["fse"],
  "source_strict": true,
  "expected_engines": ["fse"],
  "expected_domains": ["fse.anthro.fr"],
  "expected_retrieval_sources": ["fse"],
  "fallback_profiles": []
}
```

---

## 7. Operator Debugging Commands

### 7.1 Direct SearXNG FSE query

```bash
SEARXNG="http://127.0.0.1:8888"
curl -sS "$SEARXNG/search?q=dragon+transformation&format=json&engines=fse" | \
  jq '{count: .results | length, results: .results[0:3] | map({title, url, engine})}'
```

### 7.2 Check probe cache (SearXNG engine availability)

```bash
curl -sS "http://127.0.0.1:18180/qz/status" | jq '.searxng.engine_probe | keys'
```

### 7.3 Inspect latest QuantZhai provider trace logs

```bash
jq . var/latest-web-search-request.json
jq . var/latest-web-search-provider-raw-summary.json
jq . var/latest-web-search-normalized.json
jq . var/latest-web-search-route.json
```

### 7.4 Same-query comparison: SearXNG direct vs QuantZhai

Run both with the same query and compare:

```bash
# Direct SearXNG
SEARXNG="http://127.0.0.1:8888"
echo "--- Direct SearXNG ---"
curl -sS "$SEARXNG/search?q=dragon+transformation&format=json&engines=fse" | \
  jq '{direct_count: .results | length, results: .results[0:3] | map({title, url})}'

# After a QuantZhai search, inspect the trace
echo "--- QuantZhai last search ---"
jq '{selected_profile, provider_id, parsed_result_count, accepted_result_count,
     wrong_source_results_discarded, count_mismatch, fallback_used, warnings}' \
  var/latest-web-search-normalized.json
```

### 7.5 Direct FSE story retrieval (not search)

```bash
# Fetch a single known story (retrieval, not search)
python3 ~/searchengines/scripts/fetch-fse-story.py "https://fse.anthro.fr/stories/42-example" --max-chars 500
```

---

## 8. Count Semantics

| Field | Source | Use |
|---|---|---|
| `provider_reported_count` | SearXNG `number_of_results` | **Diagnostic only. Never routing.** |
| `parsed_result_count` | `len(raw_payload["results"])` after dedup | Routing for non-strict profiles |
| `accepted_result_count` | `len(results after source filter)` | Routing for source-strict profiles |
| `wrong_source_results_discarded` | `parsed - accepted` when strict | Diagnostic, appears in warnings |
| `count_mismatch` | `provider_reported == 0 and parsed > 0` | Diagnostic warning flag |

---

## 9. Provider Contract (fse_direct — Future)

When/if `fse_direct` is implemented, it must:

**Input:** `query`, `top_k`, optional safe-search settings

**Output (per result):**
```json
{
  "title": "...",
  "url": "https://fse.anthro.fr/stories/...",
  "snippet": "...",
  "provider_id": "fse_direct",
  "engine": "fse",
  "source_kind": "prose_archive",
  "retrieval_available": true,
  "retrieval_source": "fse"
}
```

**Provider result envelope:**
```json
{
  "provider_id": "fse_direct",
  "provider_reported_count": null,
  "parsed_result_count": <int>,
  "count_mismatch": false,
  "warnings": [...],
  "results": [...]
}
```

**Failure:** return `{"error": "provider_unavailable", "results": []}` — never fall back to broad.

---

## 10. Capabilities Schema

`action=capabilities` now returns:

```json
{
  "providers": {
    "searxng":       { "available": true/false, "provider_id": "searxng", "probe_status": "...", "note": "..." },
    "fse_direct":    { "available": false, "provider_id": "fse_direct", "note": "..." },
    "agent_retrieve":{ "available": true/false, "provider_id": "agent_retrieve", "note": "..." }
  },
  "profiles": {
    "furry_fse": {
      "source_strict": true,
      "fallback_profiles": [],
      "provider_preference": ["fse_direct", "searxng_fse"],
      "provider_preference_note": "fse_direct preferred (not yet implemented); currently routes via SearXNG fse engine"
    }
  }
}
```

---

## 11. Regression Guards

The following existing behaviors must be preserved:

- `broad` profile still falls back to fallback_profiles when below threshold
- `coding` profile is not source_strict and has no provider_preference
- `furry_images` is not source_strict
- `retrieval_endpoint` is never exposed in model-visible output
- No SoFurry profile is created automatically
- `furry_fse` fallback_profiles is `[]` in config

Tests covering these: `tests/test_qz_tool_web.py` regression section.
