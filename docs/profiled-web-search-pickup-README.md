# Pickup pack: profile-aware SearXNG web_search

Files:

- `profile-aware-web-search-design.md` — concept and architecture note.
- `../config/default/search-policy.json` — active policy file with profile-aware additions.
- `codex-implementation-brief-web-search-profiles.md` — patch guide for Codex/local agent.

Recommended pickup order:

```bash
mkdir -p ~/turboquant/profiled-web-search-pickup
cp profile-aware-web-search-design.md ~/turboquant/profiled-web-search-pickup/
cp ../config/default/search-policy.json ~/turboquant/profiled-web-search-pickup/
cp codex-implementation-brief-web-search-profiles.md ~/turboquant/profiled-web-search-pickup/
```

Then in Codex:

```text
Read codex-implementation-brief-web-search-profiles.md and patch qwen36turbo_proxy.py accordingly. Use config/default/search-policy.json as the policy source. Keep existing behaviour working.
```
