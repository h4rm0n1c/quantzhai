# Search Provider Boundary Audit

Date: 2026-05-22
Status: Post-decoupling audit. Provider guidance architecture active.

---

## 1. Ownership Model

```
QuantZhai owns:
  action schema (search, retrieve, open_page, find_in_page, capabilities)
  budget system (modes, absolute caps, per-call limits)
  source_strict enforcement mechanism (no fallback, discard wrong-source results)
  fallback policy enforcement
  retrieval orchestration (retrieve action, normalize_retrieve_response)
  trace logging (latest-web-search-*.json)
  provider_guidance section in capabilities (generic fetch + merge)

searchengines-private / Agent API /guidance owns:
  engine syntax and pagination quirks
  source-specific retrieval behaviour (FSE redirect handling, SoFurry restrictions)
  content-warning/public redirect handling
  provider-specific operator guidance (purpose, use_when, hard_rules)
  provider_preference for source-strict profiles
  whether fse_direct or other direct providers exist
  SoFurry availability and routing (if/when implemented)
```

---

## 2. Classification

### 2.1 KEEP_GENERIC — generic mechanism, stays in QuantZhai

| Item | Location | Notes |
|---|---|---|
| Action schema (search/retrieve/open_page/...) | `qz_tool_web.py` | Generic protocol |
| Budget modes and absolute caps | `qz_tool_web.py`, `search.json` | Operator-configurable |
| source_strict mechanism | `_profile_source_strict`, `_search_web` | Logic only; config drives which profiles |
| Wrong-source result filtering | `_result_matches_expected_source` | Generic |
| Fallback policy enforcement | `_search_web` | Generic |
| Retrieval normalization | `_normalize_retrieve_response` | Generic field shapes |
| Trace log files | `_search_web`, `_query_searxng` | Generic fields |
| `provider_id`, `count_mismatch`, `provider_reported_count` | `_query_searxng` | Generic diagnostics |
| `accepted_result_count` | `_search_web` | Generic |
| `provider_guidance` section in capabilities | `build_web_search_capabilities` | Generic fetch + merge |
| Domain annotations for widely-known neutral domains (github, arxiv, stackoverflow) | `_DOMAIN_SOURCE_KIND` | Public/stable |
| `_fetch_provider_guidance_cached` | `WebSearchRuntime` | Generic /guidance endpoint |
| `_get_guidance_source_strict` | `WebSearchRuntime` | Generic guidance→strict mapping |

### 2.2 KEEP_FALLBACK — acceptable shipped default, provider guidance overrides

These items are reasonable local defaults that QuantZhai ships for usability when
no provider guidance is present. They are NOT searchengines-private secrets.

| Item | Location | Notes |
|---|---|---|
| `furry`, `furry_fse`, `furry_images` profiles in `search.json` | `config/default/search.json` | Shipped as routing defaults; provider guidance overrides description |
| `fse.anthro.fr` in `expected_domains` for `furry_fse` | `config/default/search.json` | Routing constraint, not lore |
| `expected_retrieval_sources=["fse"]` for `furry_fse` | `config/default/search.json` | Routing constraint |
| `fse.anthro.fr` in `_DOMAIN_SOURCE_KIND` | `qz_tool_web.py` | Annotation; no routing effect |
| `e926.net`, `furbooru.org` in `_DOMAIN_SOURCE_KIND` | `qz_tool_web.py` | Annotation; no routing effect |
| `character-card`, `fse`, `furbooru` in `_RETRIEVAL_SOURCE_KIND` | `qz_tool_web.py` | Annotation; no routing effect |
| `_PROFILE_DESCRIPTION_FALLBACKS` furry/character entries | `qz_tool_web.py` | Fallback description only |
| `profile == "furry_fse"` in `_profile_source_strict` | `qz_tool_web.py` | Deprecated compat; remove when guidance always present |
| `all(e == "fse")` in `_profile_source_strict` | `qz_tool_web.py` | Deprecated compat |
| "Source-strict profiles enforce exact engine matching" usage note | `build_web_search_capabilities` | Generic note, acceptable |

### 2.3 MOVE_TO_PROVIDER_GUIDANCE — removed from static QuantZhai, returned by /guidance

| Item | Former location | Status |
|---|---|---|
| `provider_preference=["fse_direct", "searxng_fse"]` for furry_fse | `build_web_search_capabilities` | **REMOVED**. Now only from guidance. |
| "furry_fse: source-strict — only fse engine / fse.anthro.fr results..." usage note | `build_web_search_capabilities` | **REMOVED**. Provider guidance owns this. |
| "furry_fse: prose/story retrieval expected (FSE Agent API)." usage note | `build_web_search_capabilities` | **REMOVED**. |
| "furry_images: image metadata only (e926/furbooru tags/ratings)..." usage note | `build_web_search_capabilities` | **REMOVED**. |
| "furry: mixed convenience profile covering prose (FSE) and image metadata..." usage note | `build_web_search_capabilities` | **REMOVED**. |
| `fse_direct` static entry in `providers_info` | `build_web_search_capabilities` | **REMOVED**. Now only from guidance providers. |
| Per-profile `purpose`, `use_when`, `do_not_use_for`, `hard_rules` guidance | n/a | Now delivered via /guidance. |

### 2.4 REMOVE_LEAK — was a leaked assumption, removed

| Item | Former location | Why removed |
|---|---|---|
| "SoFurry is not configured. It is not discoverable unless added to config/user/search.json." usage note | `build_web_search_capabilities` | Leaked architectural assumption about searchengines-private. QuantZhai must not mention SoFurry unless guidance exposes it. |
| `sofurry_in_probe → warnings.append("SoFurry engine detected...")` block | `build_web_search_capabilities` | Leaked internal engine name as a user-visible route. SoFurry routing is searchengines-private territory. |
| `"fse_direct is not available and SearXNG fse engine is absent..."` warning | `build_web_search_capabilities` | Leaked searchengines-private provider name. Replaced by generic source-strict probe warning. |

---

## 3. Boundary Rules

### What QuantZhai MUST NOT do:

- Hard-code SoFurry as a named route or config entry
- Mention SoFurry, ackAdult, `_token`, content-warning forms, or cookies
- Expose `fse_direct` as a static provider unless guidance delivers it
- Hard-code `provider_preference` for any profile — provider guidance owns this
- Hard-code per-profile usage prose about searchengines-specific sites (FSE, furbooru, e926, SoFurry)

### What QuantZhai MUST do:

- Fetch `/guidance` from the Agent API (generic endpoint), handle failure gracefully
- Merge guidance into capabilities output under `provider_guidance`
- Attach per-profile `provider_guidance` fields when guidance has them
- Attach `provider_preference` from guidance only
- Enforce `source_strict` from: local config → guidance → deprecated compat fallbacks

### What searchengines-private SHOULD own (via /guidance):

```json
{
  "schema": "qz.provider_guidance.v1",
  "provider_id": "searchengines-local",
  "profiles": {
    "furry_fse": {
      "source_strict": true,
      "purpose": "Furry prose/story discovery via FSE only.",
      "use_when": "Looking for furry fiction stories indexed at fse.anthro.fr.",
      "do_not_use_for": "General furry image search; use furry_images instead.",
      "provider_preference": ["fse_direct", "searxng_fse"],
      "retrieval_guidance": "Use action=retrieve on fse.anthro.fr results for full story text.",
      "hard_rules": [
        "Never fall back to broad or general engines.",
        "Expected source: fse.anthro.fr only."
      ]
    }
  },
  "providers": {
    "fse_direct": {
      "available": false,
      "provider_id": "fse_direct",
      "note": "Direct FSE provider. Not yet available."
    }
  },
  "warnings": []
}
```

---

## 4. Items Still To Do

| Item | Classification | Action needed |
|---|---|---|
| `_PROFILE_DESCRIPTION_FALLBACKS` furry entries | KEEP_FALLBACK | Move descriptions to config only (already in search.json); remove from fallback dict in future |
| `profile == "furry_fse"` compat in `_profile_source_strict` | KEEP_FALLBACK | Remove when /guidance is always present and returns source_strict |
| `all(e == "fse")` compat in `_profile_source_strict` | KEEP_FALLBACK | Remove when guidance engine-pinning is implemented |
| `fse.anthro.fr` in `_DOMAIN_SOURCE_KIND` | KEEP_FALLBACK | Move to guidance or config annotation in future |
| Implement /guidance endpoint in searchengines-private | MOVE_TO_PROVIDER_GUIDANCE | searchengines-private work |
