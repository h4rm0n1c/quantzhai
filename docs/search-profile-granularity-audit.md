# Search Profile Granularity Audit

Date: 2026-05-22
Status: Slice F discovery — authoritative profile/engine/capabilities map.

Related:
- `docs/search-config-contract.md` — search.json schema, precedence, §60/§64/§65 designs.
- `docs/web-search-provider-architecture-audit.md` — provider architecture, fse_direct finding, count semantics, trace logs.
- `docs/runtime-streaming-tool-contract-audit.md` — Slice A pipeline.

---

## 1. Profile Inventory

All profiles come from `config/default/search.json.profiles`. The legacy
`config/default/search-policy.json.web_search_profiles` provides no additional
profiles (it has no `web_search_profiles` key). Profiles are loaded via
`search_config_profiles` in `WebSearchRuntime`.

| profile | description | categories | engines | fallback_profiles | retrieval_expected? | sources_expected | in capabilities? | in VALID_WEB_SEARCH_PROFILES? | explicit selectable? | explicit engines override? |
|---|---|---|---|---|---|---|---|---|---|---|
| `auto` | Infer from keywords; falls back to broad | — | — | broad | no | unknown | yes | yes | yes | yes |
| `broad` | General web search across healthy text engines | general, web | duckduckgo, bing, mojeek, presearch, wiby, searchmysite, crowdview, yacy | reference, news, coding | no | unknown | yes | yes | yes | yes |
| `coding` | Repos, packages, Q&A, official tech docs | it, repos, q&a, packages, software wikis | github, gitlab, gitea.com, stackoverflow, superuser, askubuntu, discuss.python, mdn, microsoft learn, docker hub, npm, crates.io, lib.rs, pkg.go.dev, hackernews, lobste.rs | broad | no | official_docs, source_repo, package_registry, q_and_a | yes | yes | yes | yes |
| `sysadmin` | Linux, networking, manpages, distro docs | it, q&a, packages, software wikis | superuser, askubuntu, mankier, nixos wiki, gentoo, alpine linux packages, mdn, microsoft learn | coding, broad | no | official_docs, q_and_a | yes | yes | yes | yes |
| `research` | Papers, academic, DOI-style | science, scientific publications | arxiv, crossref, google scholar, semantic scholar, pubmed, openairepublications | broad | no | academic | yes | yes | yes | yes |
| `news` | Current events | news | reuters, bing news, duckduckgo news, mojeek news, presearch news, startpage news | broad | no | news | yes | yes | yes | yes |
| `ai_models` | Hugging Face, Ollama, dataset discovery | it, repos | huggingface, huggingface datasets, ollama | broad, coding | no | source_repo | yes | yes | yes | yes |
| `reference` | Dictionaries, encyclopedias | dictionaries, wikimedia, general | wiktionary, wikibooks, wolframalpha, jisho | broad | no | encyclopedia | yes | yes | yes | yes |
| `character_cards` | Character card metadata; Agent API retrieval | general | taverncard, aicharactercards | broad | **yes** | character_card | yes | yes | yes | yes |
| `furry` | FSE prose + e926/furbooru image metadata | general, images | fse, e926, furbooru | **furry_fse**, broad | **yes** (mixed: FSE prose + furbooru img) | prose_archive, furry_community | yes | yes | yes | yes |
| `furry_fse` | FSE-only prose/story discovery; Agent API retrieval | general | fse | broad | **yes** | prose_archive | yes | yes | yes | yes |
| `furry_images` | e926 + furbooru image metadata | images | e926, furbooru | broad | **yes** (via heuristic — see §2) | furry_community | yes | yes | yes | yes |
| `gaming_wikis` | Gaming mod wikis; Agent API retrieval | general, it | pcgamingwiki, alliedmodders wiki, official tf2 wiki, pantheon wiki | coding, broad | **yes** | gaming_wiki, official_docs | yes | yes | yes | yes |
| `archives` | Bitmagnet, nyaa, wiby; local index | files, general | bitmagnet, nyaa, wiby | broad | no (bitmagnet is local_index) | local_index | yes | yes | yes | yes |

---

## 2. Furry/Search Profile Audit

### `furry`

- **Engines**: `["fse", "e926", "furbooru"]`
- **FSE included**: yes
- **e926/furbooru included**: yes
- **SoFurry included**: **no** — absent from all repo config
- **Fallback**: `["furry_fse", "broad"]` — if `furry` returns < `low_result_fallback_threshold` (2) results, tries `furry_fse` then `broad`. **Correct.**
- **Categories**: `["general", "images"]` — mixed; `images` category is present but standard image search engines (bing images, etc.) are blocked by legacy policy's `non_text_engines_disabled_for_current_web_search_tool`. e926/furbooru are NOT in that block list.
- **retrieval_expected**: True — FSE part is prose-retrievable. e926/furbooru provide image metadata, not prose. **Mixed retrieval claim**: the profile bundles a prose-retrievable engine (fse) with image metadata engines (e926/furbooru). The model may try `retrieve` on an e926/furbooru result expecting prose but get image metadata.

**Recommendation (not in this slice)**: Split furry into furry_fse (prose-only) and furry_images (image-only) for separate selection — which `furry_fse` and `furry_images` already do. Keep `furry` as a convenience profile but document the mixed retrieval nature clearly.

### `furry_fse`

- **Engines**: `["fse"]` — FSE only. ✓
- **Categories**: `["general"]` ✓
- **Fallback**: `["broad"]` ✓
- **retrieval_expected**: True — "fse" is in `_profile_retrieval_expected` heuristic text check. ✓
- **Source kinds**: `prose_archive` ✓
- **In capabilities**: yes ✓
- **In VALID_WEB_SEARCH_PROFILES**: yes (added in ebdf87b) ✓
- **Engine suppression risk**: FSE is NOT in `non_text_engines_disabled_for_current_web_search_tool` or `disabled_even_if_configured`. NOT blocked by policy. ✓
- **Live availability**: FSE was tested in §64.12 live smoke against 127.0.0.1:8890. It is assumed available in the local SearXNG instance.

### `furry_images`

- **Engines**: `["e926", "furbooru"]` ✓
- **Categories**: `["images"]` — not `["general"]`. Note: the `images` category may cause SearXNG to return a different result format than `general`. Local SearXNG behavior depends on engine implementation.
- **Fallback**: `["broad"]` ✓
- **retrieval_expected**: True — computed via `_profile_retrieval_expected` because "furbooru" is in the heuristic text check. **Potentially misleading**: e926/furbooru return image metadata (tags, ratings, URLs), not prose. The `retrieve` action on a furbooru image result would attempt FSE-like prose retrieval which is not appropriate. The `retrieval_source` annotation on furbooru search results (`"furry_community"`) does NOT indicate prose retrieval availability.
- **Source kinds**: `furry_community` ✓
- **Engine suppression risk**: e926 and furbooru are NOT in `non_text_engines_disabled_for_current_web_search_tool`. They are custom SearXNG engines for a local instance, not the mainstream image engines that are blocked. **Not suppressed by policy.**
- **Live availability**: Unknown without a live probe. The local SearXNG instance at 127.0.0.1:8890 may or may not have e926/furbooru configured as engines. This is deployment-specific.

### SoFurry

- **In any repo config**: **NO.** Zero mentions of "sofurry" or "SoFurry" in all config files and proxy code.
- **In search-policy.json**: No.
- **In search.json**: No.
- **As a custom SearXNG engine file**: No custom engine files found in the repo.
- **Conclusion**: SoFurry is not discoverable from the repository. Whether the local SearXNG instance exposes a SoFurry engine requires a live query to `SEARXNG_BASE_URL/config` or inspection of the deployed SearXNG container. This audit cannot confirm SoFurry availability.
- **If SoFurry becomes available**: A future `furry_sofurry` profile should use `engines: ["sofurry"]`, `categories: ["general"]`, `fallback_profiles: ["furry_fse", "broad"]`, and document whether Agent API retrieval is supported.

---

## 3. Engine Availability Audit

**Important caveat**: Engine availability in the local SearXNG instance is a deployment-time fact, not a repo fact. The proxy's `_allowed_engines_cache` is built from `searxng_capabilities["engine_probe"]` — a live probe result. If no capabilities are loaded, all non-blocked engines pass through. The table below marks live availability as "unknown" where the repo cannot confirm.

| engine | in search.json | available in local probe? | blocked by policy? | non_text suppression? | profiles using it | retrieval support | current status | gap/bug |
|---|---|---|---|---|---|---|---|---|
| `fse` | yes (furry, furry_fse) | assumed yes (§64.12 live smoke tested FSE retrieval) | no | no | furry, furry_fse | **yes** (prose_archive, Agent API fse source) | active | none |
| `sofurry` | **no** | **unknown** | no | no | none | unknown | **absent from all config** | document if local SearXNG exposes it |
| `e926` | yes (furry, furry_images) | unknown | no | **no** (not in standard image engine block list) | furry, furry_images | no (image metadata only) | unknown live | need live probe to confirm |
| `furbooru` | yes (furry, furry_images) | unknown | no | **no** | furry, furry_images | partial (`furbooru` source kind, not prose) | unknown live | `retrieval_expected` may mislead |
| `taverncard` | yes (character_cards) | unknown | no | no | character_cards | yes (character-card source) | unknown live | none |
| `aicharactercards` | yes (character_cards) | unknown | no | no | character_cards | yes (character-card source) | unknown live | none |
| `pcgamingwiki` | yes (gaming_wikis) | unknown | no | no | gaming_wikis | yes (mediawiki source → gaming_wiki kind) | unknown live | none |
| `alliedmodders wiki` | yes (gaming_wikis) | unknown | no | no | gaming_wikis | yes (mediawiki) | unknown live | none |
| `official tf2 wiki` | yes (gaming_wikis) | unknown | no | no | gaming_wikis | yes (mediawiki) | unknown live | none |
| `pantheon wiki` | yes (gaming_wikis) | unknown | no | no | gaming_wikis | partial (wiki, not gaming_wiki override) | unknown live | none |
| `bitmagnet` | yes (archives) | unknown | no | no | archives | yes (local_index source) | unknown live | none |
| duckduckgo, bing, mojeek | yes (broad) | assumed yes | no | no (text engines) | broad, others | no | active text engines | none |
| bing images, brave.images, etc. | no (not in search.json) | n/a | no | **yes** (`non_text_engines_disabled_for_current_web_search_tool`) | none | n/a | blocked by legacy policy | correct — mainstream image search blocked |
| google, qwant, yahoo | no (not in search.json) | n/a | **yes** (disabled_even_if_configured) | — | none | n/a | globally disabled | correct |

**Key finding on non_text suppression**: The `non_text_engines_disabled_for_current_web_search_tool` list in `search-policy.json` blocks mainstream image search engines (`bing images`, `brave.images`, `duckduckgo images`, etc.) but does NOT include `e926` or `furbooru`. These are custom SearXNG engines specific to a local furry-content instance and are intentionally NOT in the general suppression list. They pass through the engine filter unless blocked by the probe allowlist.

---

## 4. Policy/Suppression Audit

### Suppression lists (from legacy `search-policy.json`)

| list | source | what it blocks | affects furry profiles? |
|---|---|---|---|
| `disabled_even_if_configured` | legacy policy | google, qwant, yahoo, wikipedia, arch wiki, etc. | no |
| `non_text_engines_disabled_for_current_web_search_tool` | legacy policy | bing images, brave.images, duckduckgo images/videos, google images/videos, mojeek images, presearch images/videos, qwant images/videos, startpage images | **no** — e926/furbooru/fse NOT in this list |
| `quarantine_until_fixed` | legacy policy | various broken engines | no |
| `never_for_coding_agent` | legacy policy | torrents, image/video engines | no (coding profile not used for furry) |

### Allowed engine cache (`_allowed_engines_cache`)

Built from `searxng_capabilities["engine_probe"]`. If no capabilities payload is loaded (`searxng_capabilities = {}`):
- `_allowed_engines_cache = frozenset()` (empty)
- `_filter_engines` check: `if ok_engines and engine not in ok_engines: continue` — since `ok_engines` is empty, the condition is False, no filtering happens
- **All non-blocked engines pass through when no probe data is loaded**

This means: if the proxy starts without a `SEARXNG_CAPABILITIES` file or live probe, furry_fse using `fse`, furry_images using `e926/furbooru`, and all other profiles pass all their engines to SearXNG. SearXNG itself will return nothing for engines it doesn't have configured.

**Can furry_fse be blocked accidentally?** Only if:
1. `fse` is in `disabled_even_if_configured` (it is not), OR
2. `fse` is in `non_text_engines_disabled_for_current_web_search_tool` (it is not), OR
3. A probe is loaded and `fse` is NOT in the probe allowlist.

**Can furry_images be silently emptied?** Yes, in two scenarios:
1. Probe is loaded and neither `e926` nor `furbooru` appears in the probe allowlist → `_filter_engines` removes both → empty engine list sent to SearXNG → no results.
2. Local SearXNG doesn't have e926/furbooru engines configured → SearXNG returns no results even if the engine names are sent.

**Does capabilities warn when profile engines are blocked?** **NO.** `build_web_search_capabilities` shows the CONFIGURED engines from search.json, not the EFFECTIVE filtered engines. If a profile's engines are all blocked by policy or not in the probe allowlist, capabilities still shows them as if they're available.

**Does runtime fall back to broad if all engines are filtered?**
In `_search_web`: if `explicit_engines` override is active, the explicit-override guard fires and returns results directly without fallback (even if empty). If profile-derived engines are all filtered → empty engine list sent to SearXNG → low-result path tries `fallback_profiles`. So yes, low-result fallback fires, but with an empty engine list the upstream query likely returns nothing → fallback eventually hits broad.

**Does the model see enough warning?** If capabilities shows `engines: ["e926", "furbooru"]` for furry_images but those engines produce no results (because not in local SearXNG), the model gets zero results with no explanation. No capability warning exists for "this engine may not be locally available."

**Are image engines intentionally excluded from normal web_search?** The mainstream big-name image engines (bing images, etc.) are excluded via `non_text_engines_disabled_for_current_web_search_tool`. The specialized local engines (e926, furbooru) are NOT excluded — they're opt-in specialized content.

**Are retrieval profiles mixed confusingly?** Yes for `furry`: it mixes prose-retrievable (fse) with image metadata (e926/furbooru) in one profile. `furry_fse` and `furry_images` address this split correctly, but `furry` itself remains mixed.

---

## 5. Capabilities Output Audit

`build_web_search_capabilities(runtime)` returns `qz.web_search.capabilities.v1`.

| capability field | expected | current | gap/bug | proposed test | fix pass |
|---|---|---|---|---|---|
| `schema` | `qz.web_search.capabilities.v1` | ✓ | none | existing test | none |
| `supported_actions` | includes `capabilities`, `search`, `retrieve`, `open_page`, `find_in_page` | ✓ | none | existing test | none |
| `profiles` | all configured profiles including furry_fse, furry_images | ✓ | none | `test_furry_fse_and_furry_images_in_capabilities` — existing | none |
| profile `description` | human-readable, no local URLs | ✓ (sanitised by `_safe_capability_text`) | none | — | none |
| profile `categories` | from config | ✓ | none | — | none |
| profile `engines` | **configured engines from search.json** | ✓ | **does NOT show effective/filtered engines** — gap: agent can't tell which engines are actually probe-available | add `blocked_engines` and `effective_engine_count` fields | P2 |
| profile `retrieval_expected` | accurate per-profile | **partial** — `furry_images` shows `True` via "furbooru" heuristic; furbooru is image metadata, not prose | misleading for furry_images | add test checking furry_images retrieval_expected is documented as image-only | P2 |
| profile `intended_use` | from config `intended_use` or description | ✓ | none | — | none |
| profile `source_kinds_expected` | inferred from name+engine text | ✓ | none | — | none |
| `budget_modes` | all 4 modes with limits | ✓ | none | existing test | none |
| `absolute_caps` | operator-configurable safety rails | ✓ | none | — | none |
| `retrieval.available` | `bool(searxng_base_url_configured)` | ✓ | none | — | none |
| `retrieval.supported_sources` | from `_collect_retrieval_sources` | ✓ includes fse, furbooru, character-card, mediawiki, bitmagnet | none | existing test | none |
| `retrieval.notes` | endpoint hidden, retrieve selectively | ✓ | none | — | none |
| `agent_api.endpoint_hidden_from_model` | True | ✓ | none | existing test | none |
| `config.warnings` | from `search_config_warnings` | ✓ | does NOT include "these engines may not be in local SearXNG" | add probe-based warning | P2 |
| `warnings` | config warnings | partial | no warning when profile engines are blocked/not-in-probe | P2 | P2 |
| **missing**: effective engine filter | not present | **absent** | agent can see `engines: ["e926"]` but not know if e926 is probe-available | add `effective_engine_status` or warning in capabilities | P2 |
| `furry_fse` in profiles | yes | ✓ | none | `test_capabilities_exposes_furry_fse_and_furry_images` | none |
| `furry_images` in profiles | yes | ✓ | none | same | none |

---

## 6. Retrieval/Source Audit

| source | retrieval_source key | Agent API support | retrieval_available set correctly? | gap/bug |
|---|---|---|---|---|
| FSE | `fse` | **yes** — prose stories, body_text field returned by Agent API | yes — `retrieval_available=True` on fse results | none |
| character_cards (taverncard, aicharactercards) | `character-card` | **yes** — character card metadata (name, description, personality) | yes | none |
| gaming wikis (pcgamingwiki, alliedmodders, tf2 wiki) | `mediawiki` | **yes** — wiki page content | yes | none |
| furbooru | `furbooru` | **partial** — furbooru Agent API returns image metadata, not prose. `retrieval_available=True` is set when the probe says `furbooru.available=True`. The content is image tags/ratings, not readable prose. | **potentially misleading** — model may expect prose retrieve, gets image metadata | furry_images profile: `retrieval_expected` says True but retrieve content is image metadata only |
| e926 | n/a | **no** — e926 is a content filter layer on e621; SearXNG e926 engine returns image posts. Retrieval via Agent API not confirmed. | n/a (no annotation) | unknown |
| bitmagnet | `bitmagnet` | **yes** — local torrent index | yes | none |
| mediawiki (general) | `mediawiki` | **yes** | yes | none |
| SoFurry | n/a | **unknown** — not in repo | n/a | absent |

**Are retrieve endpoints hidden?** Yes — `retrieval_endpoint` is never included in any model-visible payload. Confirmed by `_annotate_source` (intentionally excluded) and existing test `test_retrieval_endpoint_not_exposed`.

**Does capabilities explain retrieve action correctly?** Yes — `retrieval.notes` includes "Use retrieve only for selected results that advertise retrieval_available=true" and "The raw retrieval endpoint is hidden from model-visible output."

**Does FSE result include retrieval_source?** Yes — `_annotate_source` sets `retrieval_source="fse"` when `retrieval.source == "fse"` from the search result's `retrieval` metadata dict.

**Does furry_fse advertise retrieval accurately?** `retrieval_expected=True` — correct for FSE prose content. But this is a static config-derived value; actual retrieval_available on individual results depends on the Agent API probe response for that result's source.

**Does furry_images avoid false retrieval promises?** **No** — `retrieval_expected=True` (from "furbooru" heuristic). furbooru image metadata is not prose. This is misleading. An agent may try `retrieve` on a furbooru image result and get image tags instead of readable content.

---

## 7. Query Routing / Explicit Engines Audit

### profile="furry_fse" selection

- `_profile_config("furry_fse", query)` → `actual_profile = "furry_fse"` (valid in `_valid_profiles_cache`).
- `cfg = search_config_profiles.get("furry_fse")` → `{"categories": ["general"], "engines": ["fse"]}`.
- `engines = ["fse"]`; `_filter_engines(["fse"], "furry_fse")` checks if `fse` is blocked and if in probe allowlist.
- If no probe → `fse` passes. If probe → `fse` passes only if in probe.
- **Result**: FSE-only query sent to SearXNG. ✓

### profile="furry", engines=["fse"]

- Explicit engines override: `explicit_engines = ["fse"]`, filtered via `_filter_engines(["fse"], "furry")`.
- `query_categories = []` (profile="furry" is not in the `ai_models`/`broad` exception check, but `primary_engines` is non-empty so query_categories might be empty... let me check).
- Actually looking at line 1292: `query_categories = [] if route["profile"] in ("ai_models", "broad") and primary_engines else primary_categories`. "furry" is not in ("ai_models", "broad") so `query_categories = primary_categories = ["general", "images"]`.
- Explicit-engine guard fires: `if explicit_categories or explicit_engines or len(results) >= threshold: return result` → no fallback.
- **Result**: FSE-only query with furry categories. Model can override to FSE using explicit engines parameter. ✓

### profile="furry_images" selection

- `actual_profile = "furry_images"`. Engines: `["e926", "furbooru"]`.
- If both are probe-filtered or not in local SearXNG → empty engine list → SearXNG returns nothing.
- Categories: `["images"]` — may affect SearXNG result format.

### profile="auto" with furry-ish query

- `_infer_search_profile(query)` → uses legacy `routing.auto_keywords` if present, else keyword matching from legacy policy.
- Legacy policy has no `auto_keywords` for furry. `search.json` has no `routing.auto_keywords`.
- Result: falls through to `default_profile` from routing (`"broad"`) or the config's `defaults.profile` (`"auto"` → `_infer_search_profile` → `"broad"`).
- **No automatic furry/FSE routing for auto profile.** ✓ (Profile selection is explicit per §60.5a.)

### Fallback from furry to broad when few results

- `low_result_fallback_threshold = 2` (from `routing.low_result_fallback_threshold` in search.json).
- `furry` → `furry_fse` (if results < 2) → `broad` (if still < 2).
- `fallback_used` is set in result payload and `web_search_route` telemetry.
- **Selected_profile in result**: `result["profile"]` shows actual profile used (including fallback). `result["fallback_used"]` shows which fallback was triggered. ✓

### Invalid explicit engines

- `_filter_engines` silently drops engines not in probe allowlist or in blocked lists.
- No error or warning emitted. Model receives query with zero engines — SearXNG chooses from default enabled engines for the given categories.
- **Gap**: silent engine drop; no feedback to model.

### Explicit engines override allowed?

Yes — `engines` parameter in web_search schema. `_filter_engines` still applies policy blocking and probe allowlist. Explicit engines are allowed for expert use cases.

---

## 8. Gap Classification

### P0 — Protocol/safety

None identified. No localhost URL leaks. Engine filtering doesn't corrupt tool protocol.

### P1 — Agent can be misled into wrong source/profile

1. **`furry_images` `retrieval_expected=True` is misleading**: Capabilities tells the agent that retrieval is expected for furry_images. If the agent tries `retrieve` on an e926 or furbooru result expecting prose, it gets image metadata (tags, ratings). The retrieve action may succeed technically but the content is useless for prose tasks.

2. **Capabilities shows configured engines, not effective engines**: If `e926` and `furbooru` are not in the local SearXNG instance, the agent sees them in capabilities but gets zero results with no explanation. No probe-availability warning exists.

### P2 — Useful info missing

3. **No warning in capabilities about engine probe availability**: If an engine is configured but not probe-available (or not in local SearXNG), capabilities shows it without marking it as potentially unavailable.

4. **SoFurry completely absent**: Agent cannot search SoFurry via any profile. If SoFurry content is needed, the agent has no supported path.

5. **`furry` profile `retrieval_expected` mismatch**: furry mixes prose-retrievable FSE with non-prose e926/furbooru. The `retrieval_expected` is True from FSE but the agent may apply it to image results incorrectly.

6. **Silent engine drop on explicit engines override**: If the agent specifies `engines=["e926"]` but e926 is not in the probe allowlist, the engine is silently dropped with no feedback.

### P3 — Documentation/test gaps

7. **No test confirming furry_images `retrieval_expected` is documented as image-only**: The current `retrieval_expected=True` is misleading and unchallenged.

8. **No test for furry fallback: furry → furry_fse → broad sequence**.

9. **No test for blocked explicit engine producing empty result with no warning**.

10. **No test that capabilities shows furry_images description mentions image metadata rather than prose**.

---

## 9. Test Coverage Audit

### Existing tests (relevant)

| test | covers |
|---|---|
| `test_furry_fse_profile_exists` | furry_fse in search.json ✓ |
| `test_furry_fse_uses_only_fse` | engines: ["fse"] ✓ |
| `test_furry_images_profile_exists` | furry_images exists ✓ |
| `test_furry_images_profile_uses_e926_and_furbooru` | correct engines ✓ |
| `test_capabilities_exposes_furry_fse_and_furry_images` | both in capabilities ✓ |
| `test_furry_fse_is_valid_profile_in_runtime` | in _valid_profiles_cache ✓ |
| `test_furry_images_is_valid_profile_in_runtime` | in _valid_profiles_cache ✓ |
| `test_furry_fse_profile_in_VALID_WEB_SEARCH_PROFILES_static` | in static set ✓ |
| `test_fse_retrieval_gives_prose_archive` | fse source_kind annotation ✓ |
| `test_furbooru_retrieval_gives_furry_community` | furbooru annotation ✓ |
| `test_retrieval_endpoint_not_exposed` | localhost URL not in output ✓ |
| `test_custom_profile_appears_and_disabled_profile_is_hidden` | profile visibility ✓ |
| `test_v1_profiles_added_to_valid_profiles` | search_config_profiles augment ✓ |

### Missing tests (precise list)

1. **`test_furry_images_retrieval_expected_is_from_heuristic`** — assert that `furry_images` profile has `retrieval_expected=True` in capabilities, AND document that this is from the "furbooru" text heuristic, not from confirmed prose retrieval. Serves as a baseline before future fix.

2. **`test_furry_fallback_sequence_furry_to_furry_fse_to_broad`** — mock `_query_searxng` to return 0 results for furry engines and furry_fse, verify `fallback_used` progresses correctly and `selected_profile` shows the final active profile.

3. **`test_furry_fse_not_blocked_by_non_text_policy`** — construct a `WebSearchRuntime` with the actual legacy policy loaded and verify `fse` is NOT in `_blocked_engines_general`.

4. **`test_e926_not_blocked_by_non_text_policy`** — same, verify `e926` not in `_blocked_engines_general`.

5. **`test_furbooru_not_blocked_by_non_text_policy`** — same for furbooru.

6. **`test_capabilities_does_not_warn_about_probe_availability`** — document current gap: capabilities shows configured engines without probe-availability warning. Assert the current `warnings` list in capabilities for furry_images is empty even when no probe is loaded. (Baseline test before future fix.)

7. **`test_furry_fse_explicit_engine_override_selects_fse_only`** — `profile="furry", engines=["fse"]` → query only uses fse (no e926, furbooru).

8. **`test_furry_images_category_is_images_not_general`** — furry_images uses `categories: ["images"]` not `["general"]`.

9. **`test_sofurry_absent_from_all_profiles`** — confirm no profile uses `sofurry` as an engine. Documents the known absence.

10. **`test_capabilities_furry_profile_fallback_includes_furry_fse`** — capabilities profile `furry` has `furry_fse` in source_kinds or description mentions furry_fse as fallback. (Optional but useful for agent guidance.)

---

## 10. Findings Summary

### Can we individually search FSE?

**Yes.** `profile="furry_fse"` sends `engines=["fse"]` to SearXNG. FSE is not blocked by any policy. FSE was confirmed live in §64.12 smoke tests. Use `profile="furry_fse"` or `profile="furry", engines=["fse"]`.

### Can we individually search SoFurry?

**No.** SoFurry is completely absent from all config files and the proxy codebase. There are no custom SearXNG engine files for SoFurry in the repo. Whether the local SearXNG instance has a SoFurry engine configured is a deployment question that cannot be answered from the repository alone.

**To add SoFurry support (future)**:
1. Verify local SearXNG has a `sofurry` engine (check `GET /config` endpoint).
2. Add `furry_sofurry` profile to `config/default/search.json`.
3. Update `VALID_WEB_SEARCH_PROFILES` and `_PROFILE_DESCRIPTION_FALLBACKS`.
4. Document whether Agent API retrieval is supported.

### Does furry_images currently work or is it likely suppressed?

**Unknown — deployment-dependent.** `e926` and `furbooru` are NOT blocked by the proxy policy. Whether they produce results depends on whether the local SearXNG instance has these engines configured. If SearXNG doesn't have e926/furbooru, the profile returns zero results and falls back to `broad`. Needs a live probe to confirm.

### Does capabilities tell the agent enough?

**Mostly yes, with one misleading field.** Capabilities correctly exposes furry_fse, furry_images, all profiles, budget modes, retrieval support, and usage notes. The gap is:
- `furry_images.retrieval_expected=True` is misleading (image metadata, not prose).
- No warning when configured engines may not be available in local SearXNG.

### Critical gaps

1. **P1**: `furry_images.retrieval_expected=True` misleads agent into expecting prose retrieval from image metadata engines.
2. **P1**: Capabilities shows configured engines without probe-availability warning — agent can be confused by zero results with no explanation.
3. **P2**: SoFurry completely absent.
4. **P2**: Silent engine drop on explicit override with no model feedback.

### Uncertain areas needing live capture

- Whether `e926` and `furbooru` are configured in the local SearXNG instance at 127.0.0.1:8890. Check `GET 127.0.0.1:8890/config` for engine list.
- Whether the furbooru Agent API retrieval returns image metadata or something more useful.
- Whether SoFurry is in the local SearXNG instance.

### Proposed fix-pass order

1. **P1a — fix `furry_images.retrieval_expected`**: Change `_profile_retrieval_expected` heuristic to NOT trigger on "furbooru" alone (image metadata is not prose retrieval). Or add `retrieval_expected: false` override in the furry_images config entry.
2. **P1b — add probe-availability warning to capabilities**: When `_allowed_engines_cache` is non-empty and a profile's engines are not in it, add a warning to `capabilities.warnings`.
3. **P3 — add 5 missing engine suppression tests** (tests 3–5, 6, 9 from §9).
4. **SoFurry (future, not this slice)**: Live-probe local SearXNG. If engine found, add profile.

### Recommended next audit slice

**Slice G / Fix-pass B2**: This is the last audit slice. The audit series (A-F) is now complete. Begin fix pass B2: implement ToolCoercionResult guard, non-streaming dropped-tool fix, coercion/schema telemetry, and the test fixtures identified in Slices B–F.
