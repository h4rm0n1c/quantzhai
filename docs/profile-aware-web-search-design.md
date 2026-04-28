# Profile-aware `web_search` for Qwen3.6Turbo Codex proxy

Date: 2026-04-28

## Summary

The local proxy already has the hard parts of a usable web runtime: a `web_search` tool shim, SearXNG querying, `open_page`, `find_in_page`, caching, source/citation attachment, and policy loading from JSON.

The missing piece is intent-aware search selection. At the moment the runtime is biased toward coding search first. That is right for Codex-as-programmer, but too narrow for Codex-as-general-local-agent.

The recommended change is to keep one tool named `web_search`, then add a `profile` argument:

```json
{
  "action": "search",
  "query": "latest llama.cpp release notes",
  "profile": "auto",
  "top_k": 8
}
```

The proxy maps `profile` to SearXNG categories and engines.

## Why this design

Do not create separate tool names like `coding_search`, `news_search`, and `research_search`.

A single profile-aware tool is easier for the model to learn and easier to police. The proxy remains the authority for engine choice, quarantine lists, disabled engines, targeted engines, and fallbacks.

## Profiles

### `auto`

Infer intent from query keywords.

Use conservative routing:

1. `ai_models`
2. `sysadmin`
3. `coding`
4. `research`
5. `news`
6. `reference`
7. `broad`

If uncertain, use `broad`.

### `broad`

General purpose search. Use for random questions, public web lookups, general discovery, and “what is this?” queries.

Recommended engines: duckduckgo, bing, mojeek, startpage, presearch, wiby, searchmysite, crowdview, yacy.

Avoid Brave for now because the robust run put it in quarantine.

### `coding`

Repos, package registries, programming Q&A, and technical docs.

Recommended engines: github, gitlab, gitea.com, stackoverflow, superuser, askubuntu, discuss.python, mdn, microsoft learn, docker hub, npm, crates.io, lib.rs, pkg.go.dev, pub.dev, rubygems, packagist, hex, hackernews, lobste.rs.

### `sysadmin`

Linux, services, homelab, networking, reverse proxy, manpages, distro docs.

Recommended engines: superuser, askubuntu, mankier, nixos wiki, gentoo, alpine linux packages, voidlinux, caddy.community, pi-hole.community, mdn, microsoft learn.

### `research`

Academic and scientific search.

Recommended engines: arxiv, crossref, google scholar, semantic scholar, pubmed, openairepublications, openairedatasets, pdbe.

### `news`

Current events and release/news style queries.

Recommended engines: reuters, ansa, bing news, duckduckgo news, mojeek news, presearch news, startpage news, wikinews, il post, naver news.

### `ai_models`

Model, dataset, GGUF, checkpoint, LoRA, Ollama and Hugging Face discovery.

Recommended engines: huggingface, huggingface datasets, ollama.

Do not use `huggingface spaces` by default for coding. Keep it explicit/targeted if demo/app discovery is needed later.

### `reference`

Definitions, word meanings, reference texts, dictionaries, lightweight lookup.

Recommended engines: wiktionary, wikibooks, wikisource, wikiquote, wolframalpha, jisho.

## Tool schema change

In `normalize_tools_for_llamacpp()`, add:

```python
"profile": {
    "type": "string",
    "enum": ["auto", "broad", "coding", "research", "news", "ai_models", "reference", "sysadmin"],
    "description": "Search profile used to select SearXNG categories and engines."
}
```

The existing `categories` and `engines` fields should remain as expert overrides.

## Runtime behaviour

For a search call:

1. Parse `profile`, defaulting to `auto`.
2. If `profile == "auto"`, infer from query keywords.
3. Resolve that profile to categories and engines from policy JSON.
4. Remove engines in `disabled_even_if_configured`, `quarantine_until_fixed`, `never_for_coding_agent` when profile is `coding`, and `non_text_engines_disabled_for_current_web_search_tool`.
5. Run primary search.
6. If result count is below `low_result_fallback_threshold`, run the configured fallback profile.
7. Deduplicate by URL.
8. Include debug metadata: selected profile, requested profile, categories, engines, fallback used, and unresponsive engines.

## Important implementation notes

The current proxy has `_coding_profile()` and `_search_web()` wired so search defaults through coding profile first. Replace that narrow flow with `_profile_config(profile, query)` and `_search_web(query, profile="auto", ...)`.

Keep `_coding_profile()` temporarily as a compatibility wrapper if useful.

Also update repeat-detection signature. Include `profile` in the signature so these two calls are not treated as duplicates:

```json
{"action": "search", "query": "qwen release", "profile": "news"}
{"action": "search", "query": "qwen release", "profile": "ai_models"}
```

## Policy file

Use:

```text
searxng-agent-policy-profiled.json
```

It keeps the legacy keys your current proxy expects, but adds `web_search_profiles`, `targeted_engines`, `routing`, `quality_rules`, `quarantine_until_fixed`, and `non_text_engines_disabled_for_current_web_search_tool`.
