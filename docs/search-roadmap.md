# Search Roadmap

Date: 2026-04-28

## Goal

Make QuantZhai's local `web_search` useful outside narrow coding search while keeping one stable tool interface for Codex.

The near-term target is a profile-aware SearXNG-backed search runtime:

```json
{
  "action": "search",
  "query": "latest qwen gguf release",
  "profile": "auto"
}
```

## Current Assets

- `profile-aware-web-search-design.md`: profile design and runtime behavior.
- `searxng-agent-policy-profiled.json`: draft policy with profile-aware engine routing.
- `codex-implementation-brief-web-search-profiles.md`: implementation brief for patching the proxy.
- `profiled-web-search-pickup-README.md`: pickup pack instructions.

## Phase 1: Land Profile Routing

- Add `profile` to the `web_search` tool schema.
- Support `auto`, `broad`, `coding`, `sysadmin`, `research`, `news`, `ai_models`, and `reference`.
- Keep `categories` and `engines` as expert overrides.
- Replace coding-first routing with profile resolution.
- Include `profile` in repeat-detection signatures.

## Phase 2: Policy-Driven Engine Selection

- Load `searxng-agent-policy-profiled.json` from a configurable path.
- Resolve profile to categories and engines through policy JSON.
- Apply disabled, quarantined, and non-text engine filters.
- Add low-result fallback routing.
- Return debug metadata showing requested profile, selected profile, engines, categories, and fallback use.

## Phase 3: Result Quality

- Deduplicate by canonical URL.
- Prefer primary sources for coding and technical docs.
- Prefer current sources for news and release queries.
- Penalize low-signal mirrors, spam pages, and tool-hostile sources.
- Keep page-open and find-in-page behavior separate from search result ranking.

## Phase 4: Observability

- Write search request/response captures under `var/captures/`.
- Add compact logs for selected profile, engine failures, timeout counts, fallback hits, and result counts.
- Keep captures ignored by git.
- Add a small manual smoke-test checklist for each profile.

## Phase 5: Agent UX

- Document profile examples in README or a short docs page.
- Add guidance for when Codex should use each profile.
- Decide whether `auto` is enough for normal users or if `qz-codex` should expose profile defaults.
- Keep the public tool name as `web_search` unless a hard compatibility issue appears.

## Open Questions

- Should SearXNG live outside QuantZhai, or should this repo eventually include a minimal compose file?
- Should search policy be user-editable in `.env`, runtime `var/`, or tracked `config/`?
- Should QuantZhai support multiple search backends later, or keep SearXNG as the single local adapter?
- How much of search quality should be policy JSON versus Python scoring code?
