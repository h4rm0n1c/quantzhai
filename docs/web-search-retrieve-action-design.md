# web_search retrieve action design

Date: 2026-05-20
Status: #63 CLOSED — all slices delivered, live smoke passed.
#64 note: `max_retrieved_chars` is now a mode-controlled budget field.
  See `docs/search-config-contract.md §64` for the research-grade budget
  modes design. In `deep` mode the default is 30 000 chars; in `audit`
  mode 60 000 chars. The old 12 000-char ceiling is removed in #64 Slice B.

---

## 1. Purpose

Add an explicit `action="retrieve"` to the `web_search` tool that:
- Calls the local Agent API `/retrieve` server-side
- Never exposes the raw localhost retrieve endpoint to the model
- Returns structured, truncated content to the model
- Fails cleanly when no base URL is configured or retrieval is unavailable

---

## 2. Action shape

### Request

```json
{
  "action": "retrieve",
  "url": "https://www.pcgamingwiki.com/wiki/Half-Life_2",
  "retrieval_source": "mediawiki"
}
```

| Field | Required | Notes |
|---|---|---|
| `action` | yes | `"retrieve"` |
| `url` | yes | Result URL to retrieve |
| `retrieval_source` | no | Optional hint from `retrieval_source` annotation. Helps Agent API dispatch. |

**No `source` alias** — `retrieval_source` only, for consistency with annotation field names.

### Response (success)

```json
{
  "ok": true,
  "action": "retrieve",
  "url": "https://...",
  "retrieval_source": "mediawiki",
  "retriever": "fetch-mediawiki-page.py",
  "title": "Half-Life 2",
  "summary": "Half-Life 2 is a first-person shooter game...",
  "content": "...",
  "metadata": {
    "categories": ["Action", "Shooter"],
    "word_count": 2400
  },
  "truncated": false,
  "freshness_hint": "unknown"
}
```

### Response (failure)

```json
{
  "ok": false,
  "action": "retrieve",
  "url": "https://...",
  "error": "retrieval_unavailable",
  "error_detail": "No base URL configured"
}
```

---

## 3. Live Agent API `/retrieve` response shapes

Probed 2026-05-20 against `http://127.0.0.1:8890`.

### 3.1 Normalized format (mediawiki, FSE)

Common structure:

| Field | Value |
|---|---|
| `source` | Source key (`pcgamingwiki`, `fse`, etc.) |
| `input` | Original URL |
| `status` | `"ok"` or error string |
| `summary` | Short text summary |
| `fields` | Dict of structured metadata |
| `freshness` | Dict with basis/timestamps |
| `warnings` | List of warning strings |
| `agent_api` | `{retriever, source, input}` |

**mediawiki `fields` keys:** `pageid, title, display_title, url, canonical_url, revision_id, touched, length, categories, sections, body_text, body_text_chars, body_text_truncated`

**FSE `fields` keys:** `story_id, url, final_url, title, author, rating, word_count, summary, tags, published_at, updated_at, body_text, body_visible`

### 3.2 Character card format (taverncard/aicharactercards)

**Different top-level structure** — returns parsed card data directly:

| Field | Value |
|---|---|
| `name` | Card name |
| `description` | Character description |
| `creator` | Creator username |
| `tags` | List of tag strings |
| `topics` | List of topic strings |
| `spec` | Card spec (`chara_card_v3`, etc.) |
| `nsfw` | bool or null |
| `first_mes` | Opening message |
| `personality` | Character personality |
| `scenario` | Scenario description |
| `source_site` | `aicharactercards.com` |
| `source_url` | Canonical card URL |
| `agent_api` | `{retriever, source, input}` |

No `status`, `summary`, or `fields` keys at top level.

---

## 4. Normalization layer

The proxy must normalize both response shapes into a unified output.

### 4.1 Normalized format → output

| Output field | Source |
|---|---|
| `title` | `fields.title` or `fields.display_title` |
| `summary` | `summary` (truncated to 500 chars) |
| `content` | `fields.body_text` (truncated) |
| `metadata` | Bounded selection from `fields` (see §4.3) |
| `retrieval_source` | `source` |
| `retriever` | `agent_api.retriever` |
| `truncated` | `fields.body_text_truncated` or content was cut |
| `freshness_hint` | Derived from `freshness.source_updated_at` or `freshness.last_seen` |

### 4.2 Character card format → output

| Output field | Source |
|---|---|
| `title` | `name` |
| `summary` | `description` truncated to 500 chars |
| `content` | Full character content assembled from `description + personality + scenario` |
| `metadata` | `{creator, tags: [...], topics: [...], spec, nsfw}` |
| `retrieval_source` | `agent_api.source` or `"character-card"` |
| `retriever` | `agent_api.retriever` |
| `truncated` | false unless assembled content was trimmed |
| `freshness_hint` | `"unknown"` (character cards have no date signal) |

### 4.3 Metadata bounded selection

Maximum 6 keys in `metadata`. Prefer:
- For mediawiki: `categories`, `word_count` or `length`, `revision_id` for staleness
- For FSE: `author`, `rating`, `word_count`, `published_at`, `tags[:10]`
- For character cards: `creator`, `tags[:10]`, `topics[:10]`, `spec`, `nsfw`

Never include raw `body_text` in `metadata` (it's already in `content`).

### 4.4 Missing content

- If `body_text` is absent and `body_visible` is present (FSE), use `body_visible`
- If neither, use `summary` as content fallback
- If no content at all: `content = ""`, `truncated = false`

---

## 5. Truncation

| Constant | Default | search.json key |
|---|---|---|
| `WEB_SEARCH_RETRIEVE_MAX_CHARS` | 6000 | `routing.max_retrieved_chars` |

Apply to: `content` field.
`truncated: true` when content was cut.

6000 chars matches the upstream FSE truncation default observed in testing.

If `search.json routing.max_retrieved_chars` is set and valid, use it (clamped to a hard ceiling of 12000).

---

## 6. Budget

| Constant | Default | Counter key |
|---|---|---|
| `WEB_SEARCH_MAX_RETRIEVALS` | 3 | `counters["retrieve"]` |
| `search.json routing.max_retrievals_per_turn` | optional override | — |

Refusal: `{"ok": false, "error": "budget_exceeded"}` + `web_search_retrieve_budget_exceeded` telemetry.

---

## 7. Telemetry

All events are OPERATOR-visible only (FeedbackVisibility.OPERATOR). No localhost endpoint in any payload.

| Event | Trigger | Safe payload fields |
|---|---|---|
| `web_search_retrieve_started` | Before request | `url, retrieval_source, call_id` |
| `web_search_retrieve_completed` | After success | `url, retrieval_source, retriever, duration_ms, truncated, call_id` |
| `web_search_retrieve_failed` | After error | `url, retrieval_source, error_class, call_id` |
| `web_search_retrieve_budget_exceeded` | Budget hit | `url, limit, counter, call_id` |

---

## 8. Cache

Use a separate small in-memory `retrieval_cache` dict on `WebSearchRuntime`, keyed by URL.

| Constant | Value |
|---|---|
| `WEB_SEARCH_RETRIEVE_CACHE_TTL` | 900 (15 min, same as `WEB_SEARCH_PAGE_CACHE_TTL`) |

Cache the normalized output dict. No persistent storage.

---

## 9. Safety and compatibility

```text
- No raw retrieval.endpoint in any model-visible field.
- No automatic retrieval loops.
- No BrainCase writes.
- Existing search/open_page/find_in_page unchanged.
- SearXNG base URL unset → ok=false, error="no_base_url".
- If upstream /retrieve returns error status → ok=false, error="retrieval_failed".
- If URL is invalid/empty → ok=false, error="invalid_url".
- No extra network calls beyond the one /retrieve request.
```

---

## 10. Implementation plan (Slice B)

Add `_retrieve()` method to `WebSearchRuntime`:

```python
def _retrieve(self, url: str, retrieval_source: str = "") -> dict:
    """Call Agent API /retrieve and return normalized result."""
    ...
```

Add `_normalize_retrieve_response(raw: dict, url: str) -> dict` as module-level helper.

In `execute_web_search_call()`, add `action == "retrieve"` branch after `find_in_page`.

In `_parse_web_search_arguments()`, return `retrieval_source` field when action is `retrieve`.

In `WebSearchRuntime.__init__`: add `retrieval_cache`, `max_retrievals_per_turn`.

In `qz_request_router._web_runtime()`: pass `max_retrievals_per_turn` from `search_config_budgets`.

In `config/default/search.json routing`: document `max_retrievals_per_turn` and `max_retrieved_chars`.

---

## 11. Test plan for Slice B

```text
test_retrieve_success_mediawiki_response
  mock /retrieve → normalized format → correct title/summary/content/metadata

test_retrieve_success_character_card_response
  mock /retrieve → character-card format → correct title/summary/content/metadata

test_retrieve_budget_exceeded
  counters["retrieve"] >= limit → ok=false + budget_exceeded telemetry

test_retrieve_no_base_url
  base_url="" → ok=false, error="no_base_url"

test_retrieve_invalid_url
  url="" → ok=false, error="invalid_url"

test_retrieve_upstream_error
  mock /retrieve → error status → ok=false

test_retrieve_telemetry_started_completed
  success path emits started + completed

test_retrieve_telemetry_failed
  failure path emits failed

test_retrieve_no_localhost_in_output
  no "127.0.0.1" or "8890" in normalized output

test_retrieve_truncation
  body_text > WEB_SEARCH_RETRIEVE_MAX_CHARS → truncated=true, content clamped

test_retrieve_cache_hit
  second call with same URL returns cached result, no HTTP call

test_retrieve_existing_actions_unaffected
  search/open_page/find_in_page still work normally

test_retrieve_schema_coerce
  malformed action=retrieve arguments → ok=false, not exception
```

---

## 12. Non-goals

```text
- No async retrieval
- No batch retrieval
- No BrainCase storage of retrieved content
- No persistent cache
- No auto-retrieval on every search result
- No content re-ranking
- No download of binary/media files
- No authentication flows
```

---

## Related

- `docs/web-search-local-searxng-inventory.md` — Agent API surface
- `docs/search-config-contract.md §60.D2.1` — retrieval annotation design
- `proxy/qz_tool_web.py` — WebSearchRuntime
- Issue #63 — this issue
- Issue #60 — closed; search budgets/annotations/profiles
