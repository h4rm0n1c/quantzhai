# Search Config Contract

Date: 2026-05-20
Status: #39 CLOSED. #60 CLOSED. #63 CLOSED. #64 OPEN — §64 design below.

---

## 1. Purpose

Define a stable, dedicated search configuration surface that is separate from
`qz.profiles.v1`. Search routing policy is a cross-cutting infrastructure
concern and must not be embedded in individual profile bundles.

This document establishes the v1 contract for:
- `config/default/search.json` — tracked default, committed to git
- `config/example/search.json` — reference template, committed to git
- `config/user/search.json` — local override, **never committed**
- Compatibility with existing `SEARXNG_POLICY` / `search-policy.json`

---

## 2. Non-goals

```text
- Do NOT move search config into qz.profiles.v1.
- Do NOT commit config/user/search.json or any private SearXNG endpoints.
- Do NOT remove config/default/search-policy.json yet (compatibility phase).
- Do NOT change the web_search tool schema in this design slice.
- Do NOT introduce new search backends or engines.
- Do NOT make SearXNG mandatory.
- Do NOT break existing SEARXNG_POLICY / SEARXNG_BASE_URL users.
```

---

## 3. Config file locations

| File | Purpose | Committed? |
|---|---|---|
| `config/default/search.json` | Tracked default: profiles, engine routing, limits | Yes |
| `config/example/search.json` | User-facing reference template | Yes |
| `config/user/search.json` | Local override: base URL, credentials, per-site blocks | **No** (gitignored) |
| `config/default/search-policy.json` | Legacy policy; still read; deprecation tracked | Yes (existing) |

### 3.1 Why not replace search-policy.json immediately?

`config/default/search-policy.json` is a live file with a stable format
(`profiled-web-search-v1`). The proxy already loads it via `SEARXNG_POLICY`.
Replacing it in one step would require simultaneous changes to the proxy loader,
qz-env defaults, and all documentation. Instead:

- Phase 1 (this design): define `search.json` contract and precedence rules
- Phase 2 (Slice B): create `config/default/search.json`, redirect proxy loader
- Phase 3 (Slice C): deprecate `search-policy.json` with compatibility shim

---

## 4. search.json schema (v1 draft)

```json
{
  "schema": "qz.search.v1",

  "searxng": {
    "base_url": "",
    "timeout_s": 15,
    "enabled": true
  },

  "defaults": {
    "profile": "auto",
    "language": "en",
    "safe_search": 0
  },

  "profiles": {
    "auto":      { "categories": ["general"] },
    "coding":    { "categories": ["it"], "engines": ["stackoverflow", "superuser"] },
    "research":  { "categories": ["science", "general"] },
    "news":      { "categories": ["news"] },
    "ai_models": { "categories": ["it", "general"] },
    "broad":     { "categories": ["general", "it", "science"] },
    "reference": { "categories": ["general", "science"] }
  },

  "routing": {
    "low_result_fallback": "broad",
    "max_results_per_query": 10,
    "dedup_by_canonical_url": true
  },

  "disabled_engines": [],
  "quarantined_engines": [],
  "non_text_engines_disabled": true,

  "compatibility": {
    "legacy_policy_path": ""
  }
}
```

**`config/default/search.json`** — committed, no `searxng.base_url` (empty/placeholder).
**`config/user/search.json`** — local-only, sets `searxng.base_url` and any overrides.
**`config/example/search.json`** — reference with inline comments showing all keys.

### 4.1 Key design decisions

**`searxng.base_url` stays out of default/example** — it is private (points to local
SearXNG instance IP). It lives in `config/user/search.json` only.

**Profiles are named, not embedded in qz.profiles.v1** — a profile bundle can reference
a search profile name (`default_search_profile: "research"`) but the routing rules
stay in `search.json`, not the profile bundle.

**`compatibility.legacy_policy_path`** — when non-empty, the proxy reads the old
`search-policy.json` format for `web_search_profiles` routing. This bridges the gap
until the new format is fully wired.

---

## 5. Precedence rules

```
Highest priority
  1. QZ_SEARCH_CONFIG_PATH env var (absolute path override)
  2. config/user/search.json
  3. config/default/search.json
  4. Legacy fallback: SEARXNG_POLICY env var → search-policy.json
Lowest priority
```

For individual keys within the merged config:
- `searxng.base_url`: env `SEARXNG_BASE_URL` overrides all file values
- `searxng.timeout_s`: env `SEARXNG_TIMEOUT` overrides all file values
- Capabilities: env `SEARXNG_CAPABILITIES` still overrides

### 5.1 Environment variable compatibility

| Env var | Role after v1 | Notes |
|---|---|---|
| `SEARXNG_BASE_URL` | Overrides `searxng.base_url` in merged config | Preserved |
| `SEARXNG_TIMEOUT` | Overrides `searxng.timeout_s` | Preserved |
| `SEARXNG_POLICY` | Fallback to legacy policy file when no `search.json` | Preserved |
| `SEARXNG_CAPABILITIES` | Still read for capability overrides | Preserved |
| `QZ_SEARCH_CONFIG_PATH` | **New** — explicit path override for all search config | New in Slice B |

No existing env vars are removed in Phase 1. Users who set `SEARXNG_POLICY` to a
custom path continue to work. Users who set `SEARXNG_BASE_URL` continue to work.

---

## 6. /qz/config/effective exposure

`/qz/config/effective` currently exposes:
- `paths.searxng_policy` — path to active policy file
- `paths.searxng_capabilities` — path to capabilities file

After the search.json surface is live, `/qz/config/effective` should expose an
`active_search_config` section:

```json
{
  "active_search_config": {
    "schema": "qz.search.effective.v1",
    "source": "user",                          // "user", "default", "legacy", "env"
    "path": "config/user/search.json",         // which file is active
    "searxng_base_url_set": true,              // bool — NOT the URL value
    "default_profile": "auto",
    "profile_names": ["auto", "coding", "research", "news"],
    "legacy_policy_path": "",                  // non-empty if compat mode active
    "warnings": []
  }
}
```

**`searxng_base_url_set` is a bool, not the URL** — never expose the base URL in
config/effective since it may be a private IP or internal hostname.

### 6.1 Warning cases to surface

```text
- "No SearXNG base URL configured" — search will fail at runtime
- "Active policy is a docs/ path" — stale from old repo (already exists)
- "Legacy search-policy.json is active" — migration recommended
```

---

## 7. Integration with qz.profiles.v1

A profile bundle (`config/default/profiles.json`) MAY reference a preferred
search profile name:

```json
{
  "schema": "qz.profiles.v1",
  "profiles": {
    "researcher": {
      "metadata": { "label": "Researcher" },
      "search": { "default_profile": "research" }
    }
  }
}
```

The `search.default_profile` field inside a profile bundle is read-only metadata
that the proxy can use to select a starting search profile. It does NOT embed
routing rules. The routing rules stay in `search.json`.

This keeps the separation clean:
- `qz.profiles.v1` → who you are (prompt, backend, memory)
- `search.json` → how search works (routing, engines, limits)

---

## 8. qz_tool_web.py integration plan

Currently `qz_tool_web.py` receives `searxng_policy` as a pre-loaded dict
from the proxy handler at construction time. No change is needed to this
interface in Slice A.

In Slice B:
1. Add a `load_search_config(env=None)` function to a new `proxy/qz_search_config.py`
   module that applies the precedence rules above.
2. The proxy handler calls `load_search_config()` at startup and passes the merged
   config to `WebSearchTool.__init__` via a new `search_config` parameter alongside
   the existing `searxng_policy`.
3. `qz_tool_web.py` reads `search_config.get("profiles")` for profile routing,
   falling back to the legacy `searxng_policy["web_search_profiles"]` format.
4. No change to the `web_search` tool schema exposed to Codex.

---

## 9. Implementation slice roadmap

| Slice | Content | Notes |
|---|---|---|
| ~~**A-design**~~ | ~~Contract, schema, precedence, compat, roadmap~~ | ~~—~~ |
| ~~**B-impl**~~ | ~~Create `config/default/search.json` + `proxy/qz_search_config.py` loader + tests~~ | ~~No routing change yet~~ |
| ~~**B.1**~~ | ~~Audit/polish loader~~ | ~~—~~ |
| ~~**C-impl**~~ | ~~Wire loader into proxy handler; expose in `/qz/config/effective`~~ | ~~done~~ |
| ~~**C.1**~~ | ~~Audit effective config exposure~~ | ~~done~~ |
| ~~**D-impl**~~ | ~~Connect profile bundle `search.default_profile` field~~ | ~~done~~ |
| **Close-out** | Deprecate `search-policy.json` compat path once migration confirmed | Closes #39 |

---

## 10. Test plan

```text
test_load_search_config_defaults_when_no_files
  No files, no env: returns schema-valid default config with empty base_url

test_load_search_config_user_overrides_default
  config/user/search.json present: user values take precedence

test_load_search_config_env_base_url_overrides_file
  SEARXNG_BASE_URL set: overrides searxng.base_url in merged config

test_load_search_config_env_path_overrides_all
  QZ_SEARCH_CONFIG_PATH set: uses that file, ignores default/user

test_load_search_config_legacy_fallback
  No search.json, SEARXNG_POLICY set: compat mode activates

test_search_config_base_url_not_leaked_in_effective
  effective config shows searxng_base_url_set:bool, never the URL string

test_search_config_user_file_is_gitignored
  config/user/search.json is in .gitignore (static check)

test_profile_resolution_uses_search_json_profiles
  Profile routing reads from search_config.profiles not hardcoded

test_load_search_config_path_respects_qz_var_dir
  (future, if state file moves under var/)
```

---

## 11. .gitignore entry required

`config/user/search.json` must be in `.gitignore`. If it is not, the close-out
slice must add it. Verify during Slice B.

---

## 12. Compatibility summary

| What | Before | After (Slice B+) | Notes |
|---|---|---|---|
| `SEARXNG_BASE_URL` env | Directly used | Still used (highest priority) | No change |
| `SEARXNG_POLICY` env | Points to policy file | Still checked as legacy fallback | Compat preserved |
| `SEARXNG_TIMEOUT` env | Directly used | Still used | No change |
| `SEARXNG_CAPABILITIES` env | Directly used | Still used | No change |
| `config/default/search-policy.json` | Active policy | Fallback via compat shim | Deprecated but not removed |
| `config/default/search.json` | (does not exist) | New primary config | Created in Slice B |
| `config/user/search.json` | (does not exist) | New local override | Gitignored |

---

## Related documents

- `docs/search-roadmap.md` — Phase 1/2/3 search quality roadmap
- `docs/profile-aware-web-search-design.md` — profile routing design
- `config/default/search-policy.json` — existing legacy policy (active)
- `proxy/qz_tool_web.py` — web_search tool implementation
- `proxy/qz_config_report.py` — current effective config exposure
- `docs/tool-policy-audit.md` — #59 audit of all tool coercion/advice paths
- Issue #39 — search config split
- Issue #60 — web_search budget/source quality improvements

---

## §60. web_search policy v2 design (#60 Slice A)

Date: 2026-05-20. Design only. No runtime changes in this slice.

---

### 60.1 Current state (from #59 audit)

The proxy enforces web_search budgets via hard-coded Python constants:

```python
WEB_SEARCH_MAX_SEARCHES = 4    # per-turn search call limit
WEB_SEARCH_MAX_OPENS    = 3    # per-turn open_page limit
WEB_SEARCH_MAX_RESULTS  = 8    # results per query
```

`config/default/search.json` already has `routing` fields that partially
overlap but are NOT yet read by the proxy:

```json
"routing": {
  "max_searches_per_turn": 3,
  "max_page_opens_per_turn": 3,
  "max_results": 8,
  "low_result_fallback_threshold": 2
}
```

**Inconsistency:** `search.json` says `max_searches_per_turn: 3` but the
constant is `4`. The config value must win once wired; update the default JSON
to `4` when wiring or document the change explicitly.

Budget-exceeded refusals are hard errors visible to the model but produce no
operator telemetry event (invisible in qz-thoughts / `/qz/telemetry/recent`).

Source annotations are minimal: `{"url": "...", "title": "..."}` only.
No domain type, freshness, or trust signal.

---

### 60.2 Budget config contract

**Location:** `search.json routing.*` fields, read at `WebSearchRuntime`
construction time. Hard-coded constants remain as fallbacks.

**Fields to wire** (Slice B):

| Field | search.json key | Current constant | Default |
|---|---|---|---|
| Max search calls per turn | `routing.max_searches_per_turn` | `WEB_SEARCH_MAX_SEARCHES = 4` | 4 |
| Max page opens per turn | `routing.max_page_opens_per_turn` | `WEB_SEARCH_MAX_OPENS = 3` | 3 |
| Max results per query | `routing.max_results` | `WEB_SEARCH_MAX_RESULTS = 8` | 8 |
| Low-result fallback threshold | `routing.low_result_fallback_threshold` | read from legacy policy `routing.low_result_fallback_threshold` | 2 |
| Dedup by canonical URL | `routing.dedup_by_canonical_url` | always enabled | true |

**Fields NOT wired in Slice B** (deferred):

- `max_continuation_hops` — controlled by `ToolLifecycleSpec.continuation_hops`; touching it requires proxy startup changes. Defer.

**Compatibility rule:** If `search.json` routing field is absent or invalid, fall back to the existing constant. Never break web_search when search.json is misconfigured.

**Search.json update:** Change `routing.max_searches_per_turn` from `3` to `4` to match the current constant. Document in a comment.

---

### 60.3 Telemetry additions (Slice C)

Add operator-visible telemetry events when budget limits are hit.
These are `FeedbackVisibility.OPERATOR` / `FeedbackChannel.TELEMETRY` — never
injected into the model context.

**New event: `web_search_budget_exceeded`**

```json
{
  "action": "search" | "open_page" | "find_in_page",
  "limit": 4,
  "counter": 4,
  "query": "...",
  "profile": "...",
  "url": "...",
  "call_id": "..."
}
```

`query` and `profile` included only for `action=search`.
`url` included only for `action=open_page`.
Call ID included always for correlation.

The existing hard error to the model is preserved unchanged. The new event is
additive.

**Emit site:** In `execute_web_search_call()` in `qz_tool_web.py`, after the
limit check, before returning the error.

---

### 60.4 Source quality annotations (Slice D)

Extend the `sources` list items with optional quality hints. These are
annotations derived from the result URL + metadata without external lookups.

**Proposed extended source item:**

```json
{
  "url": "https://docs.python.org/3/library/pathlib.html",
  "title": "pathlib — Object-oriented filesystem paths",
  "domain": "docs.python.org",
  "source_kind": "official_docs",
  "freshness_hint": "unknown",
  "trust_hint": "high"
}
```

**`source_kind` taxonomy:**

| Value | Examples |
|---|---|
| `official_docs` | docs.python.org, developer.mozilla.org, learn.microsoft.com |
| `source_repo` | github.com, gitlab.com, crates.io |
| `q_and_a` | stackoverflow.com, superuser.com, askubuntu.com |
| `forum` | reddit.com, hackernews.ycombinator.com, lobste.rs |
| `blog` | medium.com, substack.com, personal sites |
| `news` | reuters.com, bbc.co.uk, techcrunch.com |
| `encyclopedia` | wikipedia.org, wikibooks.org |
| `package_registry` | npmjs.com, pypi.org, hub.docker.com |
| `academic` | arxiv.org, pubmed.ncbi.nlm.nih.gov, semantic scholar |
| `unknown` | everything else |

**`trust_hint` taxonomy:**

| Value | Meaning |
|---|---|
| `high` | Official source, package registry, known-good domain |
| `medium` | Community Q&A, curated forum, encyclopedias |
| `low` | Aggregator, unknown blog, low-signal domain |
| `unknown` | Not classified |

**`freshness_hint` taxonomy:**

| Value | Meaning |
|---|---|
| `recent` | URL path contains current year, news categories |
| `dated` | URL path contains old year (> 2 years ago) |
| `unknown` | No signal available |

**Implementation approach:**
- Derive `source_kind` from domain pattern matching (no external lookup)
- Derive `trust_hint` from `source_kind` (official_docs/source_repo/academic → high; q_and_a/encyclopedia → medium; forum/blog → low; unknown → unknown)
- Derive `freshness_hint` from URL path year extraction
- Annotations added in `_unique_sources()` or a new `_annotate_sources()` helper
- Backwards compatible: existing consumers that only read `url`/`title` are unaffected

**What this does NOT include:**
- No network requests to validate sources
- No PageRank or engagement signals
- No BrainCase integration

---

### 60.5 Slice roadmap

| Slice | Content |
|---|---|
| ~~**A-design**~~ | ~~Budget contract, telemetry spec, source annotation spec~~ |
| ~~**B-impl**~~ | ~~Wire `search.json routing.*` fields into `WebSearchRuntime`~~ |
| ~~**B.1**~~ | ~~Audit/polish budget wiring; max_results ceiling~~ |
| ~~**C-impl**~~ | ~~Add `web_search_budget_exceeded` telemetry event~~ |
| ~~**C.1**~~ | ~~Audit telemetry coverage (done; no gaps)~~ |
| ~~**D0-discovery**~~ | ~~Inventory local SearXNG Agent API (8890); engine taxonomy; retrieval surface~~ |
| ~~**D1-rescope**~~ | ~~Add character_cards/furry/gaming_wikis/archives profiles; fix broad~~ |
| ~~**D1.1**~~ | ~~Remove auto_keywords/auto_precedence; cancel keyword-routing plan~~ |
| ~~**D2-impl**~~ | ~~Two-layer source annotations in `_query_searxng()` + `_unique_sources()`~~ |
| ~~**D2.1**~~ | ~~Audit: add retrieval_retriever; confirm endpoint redaction; define retrieve action~~ |
| ~~**E-audit**~~ | ~~Live smoke with `http://127.0.0.1:8890`; all profiles/annotations verified~~ |
| ~~**Close-out**~~ | ~~All criteria PASS; .env.example/config updated; #60 closed~~ |

---

---

### 60.D1 Source annotation final spec (after D0 discovery)

**Two-layer annotation** — confirmed after D0 inventory.

#### Layer 1: URL/domain-derived (always available)

Computed from the result URL without extra network calls.

| Field | Type | Derivation |
|---|---|---|
| `domain` | str | Extracted TLD+1 from URL |
| `source_kind` | str | Domain pattern match (see taxonomy) |
| `trust_hint` | str | Derived from `source_kind` |
| `freshness_hint` | str | URL path year or `publishedDate` field if present |

#### Layer 2: Agent API retrieval metadata (when `retrieval.available = true`)

Only present when QuantZhai is using the 8890 Agent API endpoint.

| Field | Type | Notes |
|---|---|---|
| `retrieval_available` | bool | Whether content can be retrieved |
| `retrieval_source` | str or null | Source key (see mapping below) |

`retrieval_endpoint` is **NOT** exposed in model-visible output — it's a `http://127.0.0.1:8890/...` URL that should not appear in agent context.

#### `retrieval.source` → `source_kind` mapping

| retrieval.source | source_kind |
|---|---|
| `character-card` | `character_card` |
| `fse` | `prose_archive` |
| `furbooru` | `furry_community` |
| `mediawiki` | domain-specific: `gaming_wiki` (pcgamingwiki), `official_docs` (alliedmodders, tf2w), `wiki` (pantheon) |
| `valve-developer-community` | `official_docs` |
| `bitmagnet` | `local_index` |

#### `trust_hint` rules

| source_kind | trust_hint |
|---|---|
| official_docs, package_registry, source_repo, academic | high |
| q_and_a, wiki, gaming_wiki, encyclopedia | medium |
| character_card, prose_archive, furry_community, forum, blog, news, local_index | low |
| unknown | unknown |

#### `freshness_hint` rules

1. If `publishedDate` is present in the result, parse year from it.
2. Otherwise, extract a 4-digit year from the URL path.
3. Current year → `recent`; year ≥ 2 years ago → `dated`; no signal → `unknown`.

---

---

### 60.D2.1 Retrieval handoff design

**Audit findings (D2.1):**

1. `payload["results"]` entries do NOT contain raw `retrieval.endpoint` — confirmed. The entry dict is built from explicit fields; `_annotate_source()` adds only safe annotation fields.
2. `sources` list does NOT contain `retrieval.endpoint` — confirmed.
3. `retrieval_retriever` was missing from D2 — added in D2.1. It identifies the retrieval script (`fetch-character-card.py` etc.) without exposing a network URL.
4. No localhost URL leaks in any model-facing output — confirmed.
5. Annotations appear in both `payload["results"]` and `sources` list — confirmed.
6. No extra network calls — confirmed.
7. No auto keyword routing — confirmed.

**Safe retrieval annotation set (final):**

```json
{
  "retrieval_available": true,
  "retrieval_source": "character-card",
  "retrieval_retriever": "fetch-character-card.py"
}
```

`retrieval.endpoint` is intentionally NOT included. The model never sees a raw `http://127.0.0.1:8890/retrieve?url=...` URL.

**Retrieve action design (for future E slice):**

The model should be able to retrieve content for a single selected result using a new `web_search` action `"retrieve"`:

```json
{
  "action": "retrieve",
  "url": "https://taverncard.com/cards/123",
  "retrieval_source": "character-card"
}
```

The proxy calls `http://127.0.0.1:8890/retrieve?url=<url>&source=<source>` server-side.
The model never constructs or sees the localhost endpoint URL.

Budget: a separate `max_retrievals_per_turn` limit (default 2), configurable in `search.json routing`.

Operator telemetry: `web_search_retrieve_started` / `web_search_retrieve_completed` (same pattern as budget-exceeded events).

This is NOT in #60. It should be opened as a new issue when ready.

---

### 60.5a Profile selection philosophy

**The model chooses profiles, not a keyword classifier.**

The `web_search` tool accepts an explicit `profile` argument. The system/tool
prompt (set by QuantZhai search config and profile bundles) guides the model
to choose the right profile for the query. Hard-coded keyword routing in the
proxy is brittle, hard to reason about, and produces worse outcomes than
well-structured system prompt guidance.

`auto_keywords` and `auto_precedence` are **not wired** in the proxy. They were
added to `search.json` in D1 and removed in D1.1. The auto-inference path in
`_infer_search_profile()` continues to work with existing keyword tables in the
legacy `search-policy.json` for backward compatibility, but no new keyword
routing will be added via `search.json` in #60.

Future improvements to profile selection belong in:
- System/tool prompt guidance (`AGENTS.md`, profile bundles)
- Explicit `profile` argument in web_search calls
- Not in proxy-side keyword routing

### 60.6 Non-goals

```text
- No change to web_search Codex-facing tool schema
- No new search backends
- No SearXNG requirement changes
- No BrainCase integration
- No session/workspace identity
- No profile-bundle routing rule changes
- No removal of legacy search-policy.json
```

---

### 60.7 Tests for Slice B

```text
test_web_search_runtime_reads_max_searches_from_search_config
  search.json routing.max_searches_per_turn = 2 → runtime rejects on 3rd search

test_web_search_runtime_reads_max_page_opens_from_search_config
  search.json routing.max_page_opens_per_turn = 1 → runtime rejects on 2nd open

test_web_search_runtime_falls_back_to_constant_when_config_absent
  no routing fields in search.json → uses WEB_SEARCH_MAX_SEARCHES = 4

test_web_search_runtime_falls_back_to_constant_on_invalid_value
  routing.max_searches_per_turn = "not_a_number" → uses constant

test_web_search_budget_exceeded_telemetry_emitted (Slice C)
  budget hit → web_search_budget_exceeded event in telemetry

test_source_annotation_source_kind_official_docs (Slice D)
  docs.python.org URL → source_kind = "official_docs"

test_source_annotation_trust_hint_high_for_official (Slice D)
  official_docs source_kind → trust_hint = "high"

test_source_annotation_freshness_hint_recent (Slice D)
  URL contains current year → freshness_hint = "recent"

test_source_annotations_backwards_compatible (Slice D)
  existing consumers reading only url/title unaffected
```

---

## §64. Research-grade web_search budget modes (#64 Slice A-design)

Date: 2026-05-21
Status: ALL SLICES COMPLETE. #64 closed.

### 64.0 Problem statement

`WEB_SEARCH_MAX_RESULTS = 8` and `WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING = 12000` are
enforced as hard global caps in `WebSearchRuntime.__init__` (lines 508–515):

```python
self.max_results_per_query = min(
    _resolve_budget_int(max_results_per_query, WEB_SEARCH_MAX_RESULTS),
    WEB_SEARCH_MAX_RESULTS,   # ← hard ceiling regardless of config
)
_raw_chars = _resolve_budget_int(max_retrieved_chars, WEB_SEARCH_RETRIEVE_MAX_CHARS)
self.max_retrieved_chars = min(_raw_chars, WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING)  # ← hard ceiling
```

And in `_query_searxng` line 798:

```python
if len(results) >= max(1, min(int(top_k or WEB_SEARCH_MAX_RESULTS), WEB_SEARCH_MAX_RESULTS)):
    break  # ← inner WEB_SEARCH_MAX_RESULTS prevents top_k > 8 even if caller passes more
```

These caps make it impossible for a deep-research or citation-audit task to retrieve
more than 8 results or 12 000 chars regardless of operator config. That is a
correctness problem.

### 64.1 Design decision: default mode is `normal`

When `budget_mode` is absent and no mode-specific override exists, the effective
mode is **`normal`**.

**Rationale:** QuantZhai is a locally operated research appliance, not a
consumer API. The operator has explicitly provisioned the proxy and Agent API.
`quick` is still available for cheap single-fact checks. `normal` provides a
meaningful research baseline without requiring the model to explicitly request it.

Calls using only flat `routing.max_*` overrides from #60 remain backward-compatible
and are not affected by this change (see §64.3 precedence).

### 64.2 Named budget modes

Built-in defaults for each named mode (all five budget fields):

| Mode | `max_results` | `max_searches` | `max_opens` | `max_retrievals` | `max_retrieved_chars` |
|---|---|---|---|---|---|
| `quick` | 8 | 4 | 3 | 2 | 6 000 |
| `normal` | 12 | 8 | 8 | 4 | 12 000 |
| `deep` | 25 | 20 | 20 | 10 | 30 000 |
| `audit` | 50 | 40 | 40 | 20 | 60 000 |

Operator guidance:
- `quick` — single fact check, verify a known page, narrow retrieval
- `normal` — routine multi-step research, standard agent work
- `deep` — serious multi-source research, source comparison, multiple retrievals
- `audit` — citation-heavy evidence scan, exhaustive source verification

Large retrieved chunks should be summarized or extracted in the answer, not
dumped raw into the final response.

### 64.3 Config shape

```jsonc
// config/default/search.json  routing section
"routing": {
  "default_budget_mode": "normal",       // explicit default; controls absent budget_mode

  // named mode table — all five fields required for each mode
  "budget_modes": {
    "quick":  { "max_results": 8,  "max_searches_per_turn": 4,  "max_page_opens_per_turn": 3,  "max_retrievals_per_turn": 2,  "max_retrieved_chars": 6000  },
    "normal": { "max_results": 12, "max_searches_per_turn": 8,  "max_page_opens_per_turn": 8,  "max_retrievals_per_turn": 4,  "max_retrieved_chars": 12000 },
    "deep":   { "max_results": 25, "max_searches_per_turn": 20, "max_page_opens_per_turn": 20, "max_retrievals_per_turn": 10, "max_retrieved_chars": 30000 },
    "audit":  { "max_results": 50, "max_searches_per_turn": 40, "max_page_opens_per_turn": 40, "max_retrievals_per_turn": 20, "max_retrieved_chars": 60000 }
  },

  // operator safety rails — clamp everything; operator may lower, not raise
  "absolute_max_results":               100,
  "absolute_max_searches_per_turn":     100,
  "absolute_max_page_opens_per_turn":   100,
  "absolute_max_retrievals_per_turn":   50,
  "absolute_max_retrieved_chars":       120000,

  // flat per-session overrides — #60 compatibility layer; used when no budget_modes key
  "max_results":               8,
  "max_searches_per_turn":     4,
  "max_page_opens_per_turn":   3,
  "max_retrievals_per_turn":   3,
  "max_retrieved_chars":       6000,
  "low_result_fallback_threshold": 2,
  "dedup_by_canonical_url": true
}
```

### 64.4 Precedence rules (highest first)

```
1. Built-in absolute constant caps (ABSOLUTE_* in code)
   ↓ applied last as a hard clamp; operator cannot raise above these
2. routing.absolute_max_* in search.json
   ↓ operator lowers the absolute cap; ignored if > built-in constant
3. Resolved mode budget (from routing.budget_modes.<mode>)
   ↓ selected by web_search budget_mode argument or routing.default_budget_mode
4. Flat routing.max_* fields (#60 compat)
   ↓ used only when routing.budget_modes is absent entirely
5. Built-in module constants (WEB_SEARCH_MAX_RESULTS = 8, etc.)
   ↓ final fallback when nothing else is configured
```

**Compatibility rule:** If `routing.budget_modes` is absent, the runtime falls
back to flat `routing.max_*` fields exactly as in #60. Existing search.json
files with only flat fields continue to work without change.

**Absolute rail semantics:**
- The built-in absolute constants (`ABSOLUTE_MAX_RESULTS = 100`, etc.) are the
  highest values the system will ever use, regardless of config.
- Operator may set `routing.absolute_max_*` to lower the cap for their instance.
- Operator may NOT raise above the built-in constant (the min of the two applies).
- Mode budget values that exceed the resolved absolute cap are silently clamped.

### 64.5 New `web_search` argument: `budget_mode`

```json
{
  "action": "search",
  "query": "...",
  "profile": "deep",
  "budget_mode": "deep"
}
```

- Valid values: `"quick"`, `"normal"`, `"deep"`, `"audit"`
- Unknown or invalid value: falls back to `routing.default_budget_mode` (i.e.,
  treated as absent). No error emitted to the model.
- The argument applies per call. Multiple calls in the same turn may use
  different modes (each call resolves its own mode budget independently).
- `budget_mode` is not auto-selected from query content. The model chooses.

**Tool schema change (Slice B):** add `budget_mode` to the `web_search` function
schema with `enum: ["quick", "normal", "deep", "audit"]` and a short description.

**`additionalProperties: false` note:** the current tool schema has
`"additionalProperties": false`. Adding `budget_mode` to properties lifts this
restriction for the new field. All existing arguments are unchanged.

### 64.6 Runtime implementation targets (Slice B)

#### New constants (replace old ceilings)

```python
# Built-in absolute caps — operator safety rails, not research limits
WEB_SEARCH_ABSOLUTE_MAX_RESULTS          = 100
WEB_SEARCH_ABSOLUTE_MAX_SEARCHES         = 100
WEB_SEARCH_ABSOLUTE_MAX_OPENS            = 100
WEB_SEARCH_ABSOLUTE_MAX_RETRIEVALS       = 50
WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS  = 120_000

# Built-in mode defaults (used when budget_modes absent from config)
WEB_SEARCH_MODE_DEFAULTS = {
    "quick":  {"max_results": 8,  "max_searches_per_turn": 4,  "max_page_opens_per_turn": 3,  "max_retrievals_per_turn": 2,  "max_retrieved_chars": 6_000},
    "normal": {"max_results": 12, "max_searches_per_turn": 8,  "max_page_opens_per_turn": 8,  "max_retrievals_per_turn": 4,  "max_retrieved_chars": 12_000},
    "deep":   {"max_results": 25, "max_searches_per_turn": 20, "max_page_opens_per_turn": 20, "max_retrievals_per_turn": 10, "max_retrieved_chars": 30_000},
    "audit":  {"max_results": 50, "max_searches_per_turn": 40, "max_page_opens_per_turn": 40, "max_retrievals_per_turn": 20, "max_retrieved_chars": 60_000},
}
WEB_SEARCH_DEFAULT_MODE = "normal"
```

Remove: `WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING = 12000` (replaced by absolute cap).
Rename: `WEB_SEARCH_MAX_RESULTS`, `WEB_SEARCH_MAX_SEARCHES`, etc. become the
`quick`-mode built-in values; they remain as named constants but are no longer used
as hard ceilings.

#### New helper: `_resolve_budget_mode`

```python
def _resolve_budget_mode(
    requested_mode: str,          # from web_search budget_mode arg (may be "")
    mode_table: dict,             # routing.budget_modes (may be empty/None)
    flat_budgets: dict,           # routing.max_* (#60 compat; may be all None)
    absolute_caps: dict,          # routing.absolute_max_* (may be all None)
    default_mode: str = "normal", # routing.default_budget_mode
) -> dict:
    """Return resolved {max_results, max_searches_per_turn, max_page_opens_per_turn,
    max_retrievals_per_turn, max_retrieved_chars} clamped to absolute caps."""
```

Resolution logic (five steps):
1. Pick mode name: `requested_mode` if valid, else `default_mode`.
2. Look up mode budget: `mode_table.get(mode_name)` if table present, else
   `WEB_SEARCH_MODE_DEFAULTS[mode_name]`.
3. If `mode_table` is absent entirely and no `requested_mode`, use flat
   `flat_budgets` (each field: value if valid int, else mode default).
4. Resolve absolute caps: `min(routing.absolute_max_X, ABSOLUTE_MAX_X)` for
   each field.
5. Clamp resolved budget values to absolute caps.

#### `WebSearchRuntime.__init__` changes

Add parameters:
```python
budget_mode: str = "",                  # requested mode for this runtime instance
budget_mode_table: dict | None = None,  # routing.budget_modes from search.json
absolute_caps: dict | None = None,      # routing.absolute_max_* from search.json
default_budget_mode: str = "",          # routing.default_budget_mode
```

On init, call `_resolve_budget_mode` and store the resolved values in the same
`self.max_*` fields. Existing flat params (`max_searches_per_turn`, etc.) remain
as a secondary input to `_resolve_budget_mode` for #60 compat — they are not
removed.

Store `self.budget_mode: str` (resolved mode name, e.g. `"normal"`) for telemetry.

**Ceiling removal:**
- Remove `min(..., WEB_SEARCH_MAX_RESULTS)` from `max_results_per_query` init.
- Remove `min(_raw_chars, WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING)`.
- The only active ceiling after Slice B is the resolved absolute cap.

**`_query_searxng` line 798:**
Change:
```python
if len(results) >= max(1, min(int(top_k or WEB_SEARCH_MAX_RESULTS), WEB_SEARCH_MAX_RESULTS)):
```
To:
```python
if len(results) >= max(1, int(top_k or self.max_results_per_query)):
```

This allows `top_k` values from deep/audit modes to actually fetch more than 8 results.

#### `_parse_web_search_arguments` changes

Parse `budget_mode` from the call arguments. Validate against known modes;
unknown value → `""` (treated as absent).

`top_k` clamping in `_parse_web_search_arguments` currently does:
```python
top_k = max(1, min(top_k, self.max_results_per_query))
```
This is correct — the runtime limit applies. No change needed here.

#### `qz_request_router._web_runtime` changes

Pass from `search.json routing`:
- `budget_mode_table`: `_routing.get("budget_modes") or {}`
- `absolute_caps`: extract `absolute_max_*` fields from `_routing`
- `default_budget_mode`: `_routing.get("default_budget_mode") or "normal"`

The `budget_mode` argument itself is NOT known at runtime-init time (it comes
per-call from the model). Mode resolution therefore happens in
`execute_web_search_call`, not in `__init__`. **Revised design (see §64.6a).**

#### §64.6a Per-call mode resolution (revised)

Because `budget_mode` is a per-call argument, the runtime cannot resolve it in
`__init__`. The implementation must:

1. Store `mode_table`, `absolute_caps`, `default_budget_mode`, and the flat
   `max_*` values on the runtime instance (set in `__init__`).
2. In `execute_web_search_call`, call `_resolve_budget_mode(budget_mode, ...)`
   at the top, using the per-call argument and the stored instance data.
3. Use the resolved budget dict for counter checks in that call: `resolved["max_searches_per_turn"]`,
   etc. instead of `self.max_searches_per_turn`.
4. Store `self.budget_mode` for use when `budget_mode_arg` is absent (the last
   resolved mode, or the default). This avoids re-resolving on every counter check.

**Trade-off:** This means budget limits can differ between calls in the same turn
if the model passes different `budget_mode` values. That is intentional — the model
controls the research depth per action.

**Counters remain turn-scoped** — they are not reset per call. A `deep` call after
two `quick` calls sees the same turn-level counters.

### 64.7 Telemetry changes

#### `web_search_budget_exceeded`

Add `budget_mode` field:
```json
{
  "action": "search",
  "budget_mode": "deep",
  "limit": 20,
  "counter": 20,
  "query": "...",
  "call_id": "..."
}
```

Same for `web_search_retrieve_budget_exceeded`:
```json
{
  "url": "...",
  "budget_mode": "deep",
  "limit": 10,
  "counter": 10,
  "call_id": "..."
}
```

#### `tool_call_started` / `tool_call_completed`

Add `budget_mode` field (string, the resolved mode name). This is cheap —
already a dict that touches every call. Provides observability without model noise.

### 64.8 Tool schema and guidance (Slice B + C-doc)

**Schema — delivered in Slice B, confirmed in C-doc:**

- `action` enum includes `search`, `open_page`, `find_in_page`, `retrieve`, and `capabilities`.
- `budget_mode` is a string; use `action="capabilities"` for the live effective modes.
- `retrieve` in `action` enum; `retrieval_source` documented as property.
- `maximum: 8` removed from `top_k`; description says "clamped to effective budget_mode limit".
- Tool-level description updated to prefer live capabilities over static profile or budget guidance.

**Tool-level description (Slice C-doc):**

```
Search the web, open a page, find text in an opened page, or retrieve full
structured content for a result URL using the local web runtime.

Use action="capabilities" when unsure which profiles, budget modes, retrieval
sources, or search modes are available. Do not rely on hardcoded profile names if
capabilities are available.

Profile and budget_mode are explicit choices — there is no automatic keyword routing.
When retrieving content: extract or summarize relevant sections rather than dumping raw
content into the final answer. Large retrieved chunks should inform the answer, not fill it.
Typical research pattern: capabilities → search → retrieve or open_page → answer.
```

**Context discipline (C-doc):**

```
search    → get annotated result list; note retrieval_available annotations
retrieve  → fetch structured content for a specific URL (mediawiki/FSE/character-card)
open_page → fetch and read a full page when retrieve is not available for the source
find_in_page → locate a needle in a previously opened page

After retrieving: extract or summarize what is relevant.
Do not copy large content blocks verbatim into final answers.
Retrieval fills context for reasoning; it does not replace reasoning.
```

**Budget mode quick reference:**

| Mode | Use when | Results | Searches | Opens | Retrievals | Max chars |
|---|---|---|---|---|---|---|
| `quick` | Verify a single fact | 8 | 4 | 3 | 2 | 6 000 |
| `normal` | Routine agent work (default) | 12 | 8 | 8 | 4 | 12 000 |
| `deep` | Multi-source research, comparison | 25 | 20 | 20 | 10 | 30 000 |
| `audit` | Evidence/citation scan, exhaustive | 50 | 40 | 40 | 20 | 60 000 |

Operator may override all values via `search.json routing.budget_modes.*`.
Absolute safety rails in `routing.absolute_max_*` clamp everything; cannot exceed
built-in constants (`WEB_SEARCH_ABSOLUTE_MAX_*`).

Remove `"maximum": 8` from `top_k` schema — the model can now suggest a higher
`top_k` for deep/audit mode (still clamped to the resolved `max_results_per_query`
by the runtime).

### 64.9 Non-goals

- No BrainCase integration
- No persistent crawling
- No auto keyword routing (profile and budget_mode are explicit model arguments)
- No bulk scrape loops
- No localhost endpoint leakage
- No changes to search profiles or SearXNG engine config
- No streaming budget exhaustion signals (model sees error text on budget hit)

### 64.10 Acceptance criteria for Slice B

```text
test_budget_mode_quick_uses_quick_defaults
  no budget_mode → effective mode = normal (not quick)
  budget_mode="quick" → effective limits are quick-mode values

test_budget_mode_normal_defaults
  absent budget_mode → uses routing.default_budget_mode or "normal"

test_budget_mode_deep_exceeds_old_8_ceiling
  budget_mode="deep" → max_results_per_query = 25 (not clamped to 8)

test_budget_mode_audit_retrievals
  budget_mode="audit" → max_retrievals_per_turn = 20, max_retrieved_chars = 60000

test_absolute_cap_clamps_mode
  routing.absolute_max_results = 10 → deep mode max_results clamped to 10

test_absolute_cap_cannot_exceed_built_in_constant
  routing.absolute_max_results = 9999 → clamped to WEB_SEARCH_ABSOLUTE_MAX_RESULTS = 100

test_flat_routing_compat_without_budget_modes
  search.json with only flat max_* fields (no budget_modes key) → works as before #64

test_flat_routing_compat_with_budget_modes_absent
  no search.json routing at all → uses normal mode defaults

test_unknown_budget_mode_falls_back_to_default
  budget_mode = "fancy_mode" → treated as absent → uses default mode

test_query_searxng_top_k_not_capped_at_8
  top_k = 25 → _query_searxng collects up to 25 results (not 8)

test_budget_exceeded_telemetry_includes_budget_mode
  deep-mode search exhausts counter → budget_exceeded event has budget_mode="deep"

test_retrieve_budget_exceeded_telemetry_includes_budget_mode
  deep-mode retrieve exhausts counter → event has budget_mode="deep"

test_router_passes_budget_mode_table_to_runtime
  search.json routing.budget_modes → passed to WebSearchRuntime

test_tool_schema_includes_budget_mode
  web_search function schema contains budget_mode and points agents at
  action="capabilities" for the live supported modes
```

### 64.11 Slice roadmap

| Slice | Status | Content |
|---|---|---|
| **A-design** | ✅ complete | This section |
| **B-impl** | ✅ complete | Budget mode wired; hard ceilings removed; 22 new tests; 2804 pass |
| **B.1-audit** | ✅ complete | 10 new precedence tests; dead code removed; 2814 pass |
| **C-doc** | ✅ complete | Tool description updated; §64.8 expanded; budget mode table added |
| **D-live-smoke** | ✅ complete | See §64.12 for results |

### 64.12 Live smoke results (Slice D — 2026-05-21)

Live tests run against `http://127.0.0.1:8890` using `WebSearchRuntime.execute_web_search_call`.

#### Search result counts

| Mode | top_k requested | Results returned | Old ceiling (8) respected |
|---|---|---|---|
| `deep` | 25 | **25** | No — ceiling removed ✓ |
| `audit` | 50 | **50** | No — ceiling removed ✓ |
| `quick` | 25 | **8** | Yes — mode limit applied ✓ |
| `normal` (default) | — | **12** | Mode default applied ✓ |

`deep` and `audit` return results beyond the old hard cap of 8. `quick` clamps to its mode limit.

#### Retrieved content lengths (per-mode truncation)

Tested against FSE story `the-black-wizard` (upstream body_text = 6016 chars):

| Mode | content_len | mode limit | truncated |
|---|---|---|---|
| `quick` | **6 000** | 6 000 | True ✓ |
| `deep` | **6 016** | 30 000 | False ✓ |

Tested with synthetic 20 000-char upstream body:

| Mode | content_len | mode limit | truncated |
|---|---|---|---|
| `quick` | **6 000** | 6 000 | True ✓ |
| `normal` | **12 000** | 12 000 | True ✓ |
| `deep` | **20 000** | 30 000 | False ✓ |
| `audit` | **20 000** | 60 000 | False ✓ |

The old 12 000-char hard ceiling is gone. `deep`/`audit` pass content up to their mode limit. Live upstream sources (FSE, PCGamingWiki mediawiki) do not produce content longer than ~6 000 chars in the Agent API — this is an upstream per-source limit, not a proxy constraint.

#### Telemetry

- `tool_call_started`: `budget_mode='deep'` ✓
- `tool_call_completed`: `budget_mode='deep'` ✓
- `web_search_budget_exceeded`: `budget_mode='deep'`, `limit=20` ✓
- `web_search_retrieve_budget_exceeded`: `budget_mode` present ✓

#### Safety

- No `127.0.0.1` or `:8890` in any model-visible output ✓
- No localhost in any telemetry payload ✓

## §65. web_search capabilities introspection

`web_search` now has a first-class introspection action:

```json
{"action": "capabilities"}
```

The response schema is `qz.web_search.capabilities.v1`. It is a bounded,
model-readable view of the live runtime, not a raw config dump.

It includes:

- supported actions: `search`, `open_page`, `find_in_page`, `retrieve`, `capabilities`
- live profiles loaded from `search.json`/runtime policy
- effective budget modes after config, compatibility fallback, and absolute caps
- retrieval availability and supported `retrieval_source` values
- source annotation field names and allowed hint values
- Agent API feature/status summary without exposing endpoint URLs
- config source/count/warning metadata
- usage notes for agent planning

The static tool schema is intentionally short. The capabilities response is the
source of truth for profile names, budget modes, retrieval support, and local
runtime search modes. Agents should not assume hardcoded profile names.

Recommended agent pattern:

```text
capabilities → search → retrieve/open_page → answer
```

Use `capabilities` before unfamiliar research tasks, then choose `profile` and
`budget_mode` explicitly. There is still no automatic keyword routing. The
`retrieval_endpoint` remains hidden from all model-visible outputs; agents only
receive safe annotations such as `retrieval_available`, `retrieval_source`, and
`retrieval_retriever`.

Examples:

```json
{"action":"capabilities"}
{"action":"search","query":"current llama.cpp flash attention Vulkan status","profile":"broad","budget_mode":"normal"}
{"action":"search","query":"python pathlib official docs glob case_sensitive","profile":"coding","budget_mode":"normal"}
{"action":"search","query":"Half-Life 2 PCGamingWiki controller support","profile":"gaming_wikis","budget_mode":"deep"}
{"action":"search","query":"Lyra tavern character card","profile":"character_cards","budget_mode":"quick"}
{"action":"search","query":"site:fse.anthro.fr black wizard","profile":"furry","budget_mode":"deep"}
{"action":"search","query":"old shareware archive iso","profile":"archives","budget_mode":"audit"}
{"action":"retrieve","url":"https://www.pcgamingwiki.com/wiki/Half-Life_2","retrieval_source":"mediawiki","budget_mode":"deep"}
```

Capabilities telemetry:

```text
web_search_capabilities_requested
web_search_capabilities_completed
web_search_capabilities_failed
```

Capabilities calls do not consume search/open/retrieve budgets, do not call the
SearXNG search endpoint, do not call the Agent API retrieve endpoint, and do not
mutate per-turn repeated-call state.

Optional operator endpoint:

```text
GET /qz/web-search/capabilities
```

The endpoint returns the same schema for debugging by operators or future
monitoring surfaces. It is read-only and has no script wrapper.
