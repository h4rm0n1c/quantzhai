# Local SearXNG Capability Inventory

Date: 2026-05-20
Status: #60 Slice D0-discovery — no implementation changes.

Source: ~/searchengines/ (private workspace; agents read AGENTS.md there first).

---

## 1. Setup

This machine runs two local search services:

| Service | Endpoint | Purpose |
|---|---|---|
| SearXNG (standard) | `http://127.0.0.1:8888` | Standard SearXNG JSON search |
| SearXNG Agent API | `http://127.0.0.1:8890` | SearXNG proxy + retrieval annotation layer |

The **Agent API** (`8890`) wraps SearXNG, annotates results with `retrieval`
metadata, and exposes `/retrieve` for one-result content extraction. QuantZhai
uses the Agent API, not the raw SearXNG port, for rich retrieval support.

Key response fields from the Agent API (not standard SearXNG):

```json
{
  "results": [
    {
      "url": "...",
      "title": "...",
      "content": "...",
      "engine": "...",
      "engines": ["..."],
      "category": "...",
      "template": "default.html",
      "retrieval": {
        "available": true | false,
        "source": "character-card | fse | furbooru | mediawiki | ...",
        "retriever": "fetch-character-card.py",
        "endpoint": "http://127.0.0.1:8890/retrieve?url=..."
      }
    }
  ],
  "agent_api": {
    "kind": "searxng-compatible-search-plus-retrieval",
    "retrievable_results": 30,
    "retrieve_endpoint": "http://127.0.0.1:8890/retrieve",
    "supported_sources_url": "http://127.0.0.1:8890/sources"
  }
}
```

---

## 2. Engine inventory

### 2.1 Standard web engines

| Engine | Category | Notes |
|---|---|---|
| duckduckgo | general | Active |
| google | general | Active |
| bing | general | Region/rate varies |
| startpage | general | Suspended: CAPTCHA |
| brave | general | Too many requests (rate-limited) |
| karmasearch | general | Suspended: access denied |
| wiby | general | Retro/old-web index |
| presearch | general | Active |
| mojeek | general | Active |

### 2.2 Coding/IT/Docs/Dev engines

| Engine | Category | Shortcut | Notes |
|---|---|---|---|
| stackoverflow | it | — | Q&A |
| superuser | it | — | Q&A |
| askubuntu | it | — | Q&A |
| mdn | it | — | Official web docs |
| github | repos/it | — | Source search |
| gitlab | repos | — | Source search |
| gitea.com | repos | — | Source search |
| codeberg | repos | — | Source search |
| sourcehut | repos | — | Source search |
| docker hub | it | — | Container images |
| npm | packages | — | Node packages |
| crates.io | packages | — | Rust packages |
| lib.rs | packages | — | Rust packages |
| hoogle | packages | `hgl` | Haskell type search |
| alliedmodders wiki | it | `amw` | SourceMod/Source SDK docs |
| official tf2 wiki | it | `tf2w` | TF2 mapping/VScript |
| pcgamingwiki | general | `pcgw` | PC game compatibility/config |

### 2.3 AI/Model/Dataset engines

| Engine | Category | Shortcut | Notes |
|---|---|---|---|
| huggingface | repos/it | `hf` | Model/dataset search |
| huggingface datasets | repos | `hfd` | Dataset search |
| huggingface spaces | repos | `hfs` | Spaces/apps |
| ollama | repos/it | — | Ollama model registry |
| civitai | it/images | `civ` | AI model metadata via private SFW relay (region-blocked direct) |

### 2.4 Character card / roleplay engines

| Engine | Category | Shortcut | Source | Retrieval |
|---|---|---|---|---|
| taverncard | general | `tc` | taverncard.com + aicharactercards.com | ✅ `character-card` via fetch-character-card.py |
| aicharactercards | general | `acc` | aicharactercards.com | ✅ `character-card` |

Both search public card metadata only. Default SFW. No full card PNG/image
downloads in retrieval. tag:/topic: syntax via taverncard JSON API.

### 2.5 Furry/community engines

| Engine | Category | Shortcut | Retrieval |
|---|---|---|---|
| fse | general | `fse` | ✅ `fse` via fetch-fse-story.py |
| e926 | images | `e926` | ⚠️ search-only; single-post retrieval deferred |
| furbooru | images | `fb` | ✅ `furbooru` via fetch-booru-post.py |

e926 and furbooru: image/tag metadata only, not prose. SFW-biased (rating:s
for e926). e621 deferred.

FSE (Furry Search Engine): prose/story discovery from fse.anthro.fr. Uses
external:false bias to prefer FSE-readable results. Can be slow; has longer
per-engine timeout.

### 2.6 Archive/forum/community engines

| Engine | Category | Notes |
|---|---|---|
| lemmy posts | social media | Fediverse posts |
| lemmy communities | social media | Fediverse communities |
| lemmy comments | social media | Fediverse comments |
| lemmy users | social media | Fediverse users |
| mastodon hashtags | social media | Mastodon tag search |
| mastodon users | social media | Mastodon account search |
| tootfinder | social media | Mastodon full-text search |
| hackernews | it/general | News/discussion |
| lobste.rs | it/general | Tech discussion |
| reddit | general | Via Google/DDG; no direct API |

### 2.7 Science/academic engines

| Engine | Category | Notes |
|---|---|---|
| arxiv | science | Preprints |
| google scholar | science | Academic search |
| semantic scholar | science | AI-indexed papers |
| pubmed | science | Biomedical |
| openairepublications | science | Open access |
| openairedatasets | science | Open access datasets |
| pdbe | science | Protein structure DB |

### 2.8 News engines

| Engine | Category | Notes |
|---|---|---|
| reuters | news | Wire service |
| bing news | news | Active |
| duckduckgo news | news | Active |
| wikinews | news | Wikimedia news |
| qwant news | news | Active |

### 2.9 Torrent/file engines

| Engine | Category | Shortcut | Notes |
|---|---|---|---|
| bitmagnet | files | `bm` | Local DHT corpus; Torznab via XML shim |
| nyaa | files | `nyaa` | Live Nyaa.si torrent listing metadata; magnet discovery |
| piratebay | files | — | Listed; fragility/rate unknown |
| bt4g | files | — | Listed |
| wikicommons.files | files | — | Wikimedia Commons files |

### 2.10 Image engines

| Engine | Category | Notes |
|---|---|---|
| e926 | images | SFW furry |
| furbooru | images | Furry images |
| civitai | images | AI model thumbnails (SFW relay) |
| flickr | images | Active |
| openverse | images | Open license |
| unsplash | images | Stock photography |
| bing images | images | Active |
| brave.images | images | Active |
| qwant images | images | Active |
| wikicommons.images | images | Wikimedia |

### 2.11 Video engines

| Engine | Category | Notes |
|---|---|---|
| youtube | videos | Active |
| dailymotion | videos | Active |
| bing videos | videos | Active |
| brave.videos | videos | Active |
| qwant videos | videos | Active |
| sepiasearch | videos | Peertube/fediverse video |
| wikicommons.videos | videos | Wikimedia |

### 2.12 Icon/design engines

| Engine | Category | Notes |
|---|---|---|
| devicons | it | Dev tool/language icons |
| lucide | it | Icon set |

### 2.13 Reference/wiki engines

| Engine | Category | Notes |
|---|---|---|
| wikipedia | general | Active |
| pantheon wiki | general | Pantheon TV-series lore |
| artic | general | Art Institute of Chicago |

### 2.14 Retrieval-supported sources (Agent API)

From `/sources`:

| Source key | Retriever | Hosts covered |
|---|---|---|
| taverncard/aicharactercards | fetch-character-card.py | taverncard.com, aicharactercards.com |
| fse | fetch-fse-story.py | fse.anthro.fr |
| furbooru | fetch-booru-post.py | furbooru.org |
| mediawiki | fetch-mediawiki-page.py | pantheon-amc.fandom.com, pcgamingwiki.com, wiki.alliedmods.net, wiki.teamfortress.com |
| valve-developer-community | fetch-vdc-wayback-page.py | developer.valvesoftware.com (Wayback) |
| bitmagnet | fetch-bitmagnet-health.py | local bitmagnet WebUI URLs |

Deferred (search-only): e926, civitai, nyaa.

---

## 3. Result shape (Agent API)

All results include: `url`, `title`, `content` (snippet), `engine`, `engines`,
`category`, `template`, `retrieval`.

Retrieval annotation: `{"available": bool, "source": "...", "retriever": "...", "endpoint": "..."}`.

When `retrieval.available = true`, the endpoint can be called with the result
URL to get enriched content (character card data, story text, wiki page, etc.).

---

## 4. Profile coverage audit (config/default/search.json)

| Profile | Current engines | Gaps | Issues |
|---|---|---|---|
| auto | keyword → other profiles | No character-card/furry keywords | Add keywords in v2 |
| broad | duckduckgo, bing, mojeek, startpage (suspended), presearch, wiby, crowdview, yacy | startpage suspended; mojeek/presearch low-traffic | Prune suspended engines |
| coding | github, stackoverflow, superuser, mdn, docker hub, npm, etc. | civitai, hoogle not present | Low priority |
| sysadmin | superuser, askubuntu, mankier, nixos wiki, mdn, etc. | alliedmodders wiki, official tf2 wiki, pcgamingwiki not present | Consider adding |
| research | arxiv, crossref, google scholar, etc. | Good coverage | — |
| news | reuters, bing news, duckduckgo news, etc. | Good coverage | — |
| ai_models | huggingface, huggingface datasets, ollama | civitai not present | Add if useful |
| reference | wiktionary, wikibooks, wolframalpha, jisho | Good | — |

---

## 5. New profile recommendations

The following profiles are clearly justified by the engine set:

| Proposed profile | Key engines | Notes |
|---|---|---|
| `character_cards` | taverncard, aicharactercards | Retrieval available; SFW default |
| `furry` | e926, furbooru, fse | Mixed text/image; SFW-biased |
| `archives` | bitmagnet, nyaa, piratebay, wiby | Mixed; bitmagnet is local-only |
| `gaming_wikis` | pcgamingwiki, pantheon wiki, alliedmodders wiki, tf2 wiki | MediaWiki retrieval available |

Deferred (needs more design):
- `content_retrieval` — a mode that prefers results with `retrieval.available = true`
- `erotica` / NSFW — not implemented; e621 deferred; outside current scope

---

## 6. Decisions for #60 Slice D source annotations

**Two-layer annotation** is the right approach:

**Layer 1 — URL/domain-derived (always available):**
`source_kind`, `trust_hint`, `freshness_hint` from domain pattern matching.
Matches the original Slice D design.

**Layer 2 — Engine/result-metadata-derived (Agent API only):**
When `retrieval.available = true`, annotate with:
- `source`: the source key from retrieval metadata (e.g. `character-card`, `fse`, `furbooru`)
- `retrieval_available`: bool
- `retrieve_endpoint`: the retrieve URL (if safe to expose)

This gives richer context without extra network calls.

---

## 7. Decisions for future slices

1. **Profile additions** (Slice D or separate issue): `character_cards`, `furry`, `gaming_wikis`, `archives`.
2. **Profile routing in search.json**: Add keywords to `auto` profile for character-card and furry queries.
3. **Retrieval-aware behaviour** (separate issue): When `retrieval.available = true`, a future slice can offer a retrieve action alongside open_page. This is bigger than Slice D — recommend a new issue.
4. **broad/auto engine pruning**: Remove suspended engines (startpage CAPTCHA, karmasearch access denied) from default broad engine list.
5. **SEARXNG_BASE_URL defaults**: 
   - `config/default/search.json` stays URL-free (privacy/locality policy).
   - `config/example/search.json` may document `http://127.0.0.1:8890` with a note that this is the Agent API.
   - `scripts/qz-env` default is currently `""` (disabled); leave as-is unless operator explicitly sets it.

---

## 8. Non-goals confirmed

- No change to web_search schema.
- No NSFW/erotica profile in QuantZhai defaults.
- No bulk content-retrieval loops.
- No authentication, cookie, or private-endpoint access.
- SearXNG remains optional in QuantZhai.

---

## Related files

- `~/searchengines/AGENTS.md` — workspace agent rules (read before working there)
- `~/searchengines/notes/search/engine-usage/README.md` — per-engine routing guide
- `~/searchengines/notes/search/searxng-agent-api.md` — Agent API design
- `~/searchengines/notes/search/searxng-engines.md` — engine-level ops notes
- `docs/search-config-contract.md §60` — #60 slice design
- `docs/tool-policy-audit.md` — tool coercion/advice audit
- `config/default/search.json` — current QuantZhai search config
