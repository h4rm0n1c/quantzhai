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

Status: landed in the proxy.

- `profile` is accepted by the `web_search` tool schema.
- `auto`, `broad`, `coding`, `sysadmin`, `research`, `news`, `ai_models`, and `reference` are supported.
- `categories` and `engines` remain expert overrides.
- Coding-first routing has been replaced with profile resolution.
- `profile` is included in repeat-detection signatures.

## Phase 2: Policy-Driven Engine Selection

Status: landed and smoke tested against a local SearXNG instance.

- `searxng-agent-policy-profiled.json` loads from configurable `SEARXNG_POLICY`.
- Profiles resolve to categories and engines through policy JSON.
- Disabled, quarantined, and non-text engine filters are applied.
- Low-result fallback routing is implemented.
- Debug metadata is returned and the latest route is written to `var/captures/latest-web-search-route.json`.
- Smoke tuning handled two early quality issues: AI/model searches now fall back to `broad` before `coding`, and coding error searches narrow to Q&A engines.

## Phase 3: Result Quality

Status: started.

- Deduplicate by canonical URL.
- Prefer primary sources for coding and technical docs.
- Prefer current sources for news and release queries.
- Penalize low-signal mirrors, spam pages, and tool-hostile sources.
- Keep page-open and find-in-page behavior separate from search result ranking.

Current first-pass quality behavior:

- Qwen/GGUF/model queries route to `ai_models`, then fall back to broad text search when focused model engines are sparse.
- Coding error queries route to `coding` and narrow to StackOverflow, SuperUser, AskUbuntu, and discuss.python.
- Reference queries route to `reference`.

## Phase 4: Observability

Status: started.

- Write search request/response captures under `var/captures/`.
- Add compact logs for selected profile, engine failures, timeout counts, fallback hits, and result counts.
- Keep captures ignored by git.
- Add a small manual smoke-test checklist for each profile.

Current capture:

```text
var/captures/latest-web-search-route.json
```

## Phase 5: Agent UX

- Document profile examples in README or a short docs page.
- Add guidance for when Codex should use each profile.
- Decide whether `auto` is enough for normal users or if `qz-codex` should expose profile defaults.
- Keep the public tool name as `web_search` unless a hard compatibility issue appears.

## Phase 6: Budgeted Search Packets

Status: planned after streaming/tool continuation work is easier to test.

The next improvement should not be raising the model-visible per-turn search
limit. The better shape is a budgeted packet mode: one `web_search` call can do
controlled internal fanout and return compressed evidence.

Target behavior:

- Accept `mode` values such as `quick`, `normal`, and `deep`.
- Accept a hard `max_context_tokens` budget.
- Generate a small number of query variants internally.
- Search across selected profiles and engines in parallel where practical.
- Deduplicate URLs and rank sources.
- Fetch a bounded number of top pages.
- Extract relevant spans instead of returning whole pages.
- Return one compact evidence packet to the model.
- Store full details under run-scoped captures.

The proxy must enforce the budget. Prompt guidance alone is not enough.

## Open Questions

- Should SearXNG live outside QuantZhai, or should this repo eventually include a minimal compose file?
- Should search policy be user-editable in `.env`, runtime `var/`, or tracked `config/`?
- Should QuantZhai support multiple search backends later, or keep SearXNG as the single local adapter?
- How much of search quality should be policy JSON versus Python scoring code?
- Should `mode=deep` be available by default, or require an explicit profile or
  benchmark flag to avoid accidental long searches?
