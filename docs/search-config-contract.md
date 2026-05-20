# Search Config Contract

Date: 2026-05-20
Status: Slices A–D complete. Profile bundle search.default_profile wired. Close-out next.

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
- Issue #39 — search config split
