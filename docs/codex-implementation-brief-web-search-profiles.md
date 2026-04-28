# Codex implementation brief: profile-aware web_search

## Goal

Patch `qwen36turbo_proxy.py` so the local Codex proxy can use one `web_search` tool across multiple work types:

- broad web search
- coding search
- sysadmin search
- research search
- news search
- AI/model search
- reference/dictionary search

Do not create multiple tool names. Add a `profile` argument to the existing `web_search` function schema.

## Inputs

Place this policy beside the proxy:

```text
~/turboquant/proxy/searxng-agent-policy-profiled.json
```

Then either:

```bash
cp searxng-agent-policy-profiled.json ~/turboquant/proxy/searxng-agent-policy.json
```

or launch the proxy with the explicit policy path if supported:

```bash
--searxng-policy ~/turboquant/proxy/searxng-agent-policy-profiled.json
```

## Patch map

### 1. Function: `normalize_tools_for_llamacpp`

Find the translated `web_search` tool schema.

Add a new property:

```python
"profile": {
    "type": "string",
    "enum": ["auto", "broad", "coding", "research", "news", "ai_models", "reference", "sysadmin"],
    "description": "Search profile used to select SearXNG categories and engines.",
}
```

Keep `categories` and `engines` as optional expert overrides.

### 2. Function: `_parse_web_search_arguments`

Parse:

```python
profile = str(data.get("profile") or "auto").strip().lower()
```

Validate against:

```python
VALID_WEB_SEARCH_PROFILES = {
    "auto", "broad", "coding", "research", "news",
    "ai_models", "reference", "sysadmin",
}
```

If invalid, use `"auto"`.

Return `profile` in the parsed args dict.

### 3. Add constants near web-search config

```python
VALID_WEB_SEARCH_PROFILES = {
    "auto", "broad", "coding", "research", "news",
    "ai_models", "reference", "sysadmin",
}
```

Optional: keep keyword routing in policy JSON, not hardcoded. Hardcoded fallback is acceptable if policy keys are missing.

### 4. Add helper: `_policy_get_path`

Resolve dotted policy paths like `agent_coding.engines`.

```python
def _policy_get_path(self, dotted, default=None):
    obj = self.searxng_policy or {}
    for part in dotted.split("."):
        if not isinstance(obj, dict):
            return default
        obj = obj.get(part)
    return obj if obj is not None else default
```

### 5. Add helper: `_blocked_engines(profile)`

Combine:

- `disabled_even_if_configured`
- `quarantine_until_fixed`
- `non_text_engines_disabled_for_current_web_search_tool`
- if `profile == "coding"`, also `never_for_coding_agent`

Return a set.

### 6. Add helper: `_infer_search_profile(query)`

Use `policy["routing"]["auto_keywords"]`.

Lowercase the query and scan profiles in `policy["routing"]["auto_precedence"]`.

Return first profile whose keyword appears.

Default to `routing.default_profile` or `"broad"`.

### 7. Add helper: `_profile_config(profile, query)`

Behaviour:

1. If `profile == "auto"`, infer actual profile.
2. Load `policy["web_search_profiles"][actual_profile]`.
3. Resolve `engines`: direct `engines` list, or `engines_from` dotted path.
4. Resolve `categories`.
5. Remove blocked engines.
6. Return:

```python
{
    "requested_profile": profile,
    "profile": actual_profile,
    "categories": categories,
    "engines": engines,
    "fallback_profiles": fallback_profiles,
}
```

### 8. Replace `_search_web`

Current `_search_web()` uses `_coding_profile()`.

Change signature to:

```python
def _search_web(self, query: str, profile="auto", categories=None, engines=None, top_k=WEB_SEARCH_MAX_RESULTS):
```

Behaviour:

1. If explicit `categories` or `engines` are provided, still honour them, after filtering blocked engines.
2. Otherwise resolve via `_profile_config(profile, query)`.
3. Query SearXNG.
4. If results are below low-result threshold, try fallback profiles in order.
5. Return payload with metadata:

```python
{
    "query": query,
    "profile": actual_profile,
    "requested_profile": requested_profile,
    "fallback_used": "broad" or None,
    "categories": categories,
    "engines": engines,
    "results": results,
    "unresponsive_engines": ...
}
```

### 9. Function: `_execute_web_search_call`

Read:

```python
profile = args.get("profile") or "auto"
```

Include profile in the repeat signature:

```python
signature = (
    action,
    profile,
    _normalize_ws(query or "").lower(),
    _canonicalize_url(url or ""),
    page_id or "",
)
```

Pass profile into `_search_web()`.

### 10. Optional logging

Write last route decision to:

```text
~/turboquant/proxy/latest-web-search-route.json
```

Include query, requested profile, selected profile, categories, engines, fallback used, result count, and unresponsive engines.

## Test calls

```json
{"action":"search","query":"python json decode error stdin","profile":"coding","top_k":5}
```
Expected profile: `coding`.

```json
{"action":"search","query":"pipewire virtual microphone devuan","profile":"auto","top_k":5}
```
Expected profile: `sysadmin`.

```json
{"action":"search","query":"qwen gguf turboquant","profile":"auto","top_k":5}
```
Expected profile: `ai_models`.

```json
{"action":"search","query":"latest linux kernel release","profile":"auto","top_k":5}
```
Expected profile: `news`.

```json
{"action":"search","query":"what is the mycroft project","profile":"broad","top_k":5}
```
Expected profile: `broad`.

## Acceptance criteria

- Existing coding searches still work.
- `profile` is accepted in `web_search` tool calls.
- `profile=auto` routes differently for coding/sysadmin/news/research/model/reference queries.
- Quarantined engines are not used.
- Image/video engines are not used by the text web_search tool.
- Manual `categories`/`engines` overrides still work, but are filtered through blocklists.
- Fallback search runs only when primary result count is too low.
