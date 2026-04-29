#!/usr/bin/env python3
import base64
import json
import re
import time
import urllib.parse
import urllib.request
from html import unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path

try:
    from .qz_runtime_io import runtime_log
    from .qz_tools import function_tool
except ImportError:
    from qz_runtime_io import runtime_log
    from qz_tools import function_tool


def _now_ts() -> int:
    return int(time.time())


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


class WebSearchToolAdapter:
    upstream_name = "web_search"

    def accepts_tool(self, tool: dict) -> bool:
        return isinstance(tool, dict) and tool.get("type") == "web_search"

    def to_upstream_tool(self, tool: dict) -> dict:
        return function_tool(
            "web_search",
            "Search the web, open a page, or find text in an opened page using the local web runtime.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "open_page", "find_in_page"],
                        "description": "The web action to perform.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for search, or needle text for find_in_page.",
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["auto", "broad", "coding", "research", "news", "ai_models", "reference", "sysadmin"],
                        "description": "Search profile used to select SearXNG categories and engines.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Page URL for open_page or find_in_page.",
                    },
                    "page_id": {
                        "type": "string",
                        "description": "Previously opened page identifier for find_in_page.",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional SearXNG categories to use for search.",
                    },
                    "engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional SearXNG engines to use for search.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "description": "Optional maximum number of search results to return.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        )

    def normalize_tool_choice(self, tool_choice: dict):
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "web_search":
            return {"type": "function", "name": "web_search"}
        return None

    def input_to_upstream(self, item: dict):
        return None

    def output_to_codex(self, item: dict, output_style: str = "native"):
        return None


WEB_SEARCH_TOOL_ADAPTER = WebSearchToolAdapter()

WEB_SEARCH_SEARCH_CACHE_TTL = 300
WEB_SEARCH_PAGE_CACHE_TTL = 900
WEB_SEARCH_MAX_RESULTS = 8
WEB_SEARCH_MAX_HOPS = 6
WEB_SEARCH_MAX_SEARCHES = 2
WEB_SEARCH_MAX_OPENS = 3
WEB_SEARCH_USER_AGENT = "qwen36turbo-web-runtime/1.0"
VALID_WEB_SEARCH_PROFILES = {
    "auto",
    "broad",
    "coding",
    "research",
    "news",
    "ai_models",
    "reference",
    "sysadmin",
}


def _string_list(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks = []
        self._skip_depth = 0
        self.in_title = False
        self.title_chunks = []

    def handle_starttag(self, tag, attrs):
        tag = (tag or "").lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
            return
        if tag in {"p", "div", "section", "article", "main", "header", "footer", "aside", "li", "ul", "ol", "br", "tr", "table", "pre", "code", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        tag = (tag or "").lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
            return
        if tag in {"p", "div", "section", "article", "main", "header", "footer", "aside", "li", "ul", "ol", "br", "tr", "table", "pre", "code", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth or not data:
            return
        if self.in_title:
            self.title_chunks.append(data)
        self._chunks.append(data)

    def get_text(self):
        return _normalize_ws(html_unescape(" ".join(self._chunks)).replace("\xa0", " "))

    def get_title(self):
        return _normalize_ws(html_unescape(" ".join(self.title_chunks)).replace("\xa0", " "))


def _safe_json_file(path: Path):
    try:
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _canonicalize_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except Exception:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    clean_path = parts.path or "/"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(), clean_path, parts.query, ""))


def _unique_sources(sources):
    out = []
    seen = set()
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        url = _canonicalize_url(source.get("url") or "")
        title = _normalize_ws(source.get("title") or "")
        if not url:
            continue
        key = (url, title)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "url": url,
            "title": title or url,
        })
    return out


def _now_float():
    import time
    return time.time()


def _http_fetch(url: str, timeout: float, accept: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": WEB_SEARCH_USER_AGENT,
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        final_url = resp.geturl()
        return raw, ctype, final_url


def _extract_page_text(raw: bytes, content_type: str):
    text = ""
    title = ""
    ctype = (content_type or "").lower()
    decoded = raw.decode("utf-8", errors="replace")
    if "html" in ctype or decoded.lstrip().startswith("<"):
        parser = _HTMLTextExtractor()
        try:
            parser.feed(decoded)
        except Exception:
            pass
        title = parser.get_title()
        text = parser.get_text()
    elif "json" in ctype or "xml" in ctype or ctype.startswith("text/"):
        text = _normalize_ws(decoded)
    else:
        text = _normalize_ws(decoded)
    return title, text

class WebSearchRuntime:
    def __init__(self, base_url=None, timeout=15.0, policy=None, capabilities=None, search_cache=None, opened_page_cache=None, telemetry=None):
        self.searxng_base_url = base_url
        self.searxng_timeout = timeout
        self.searxng_policy = policy or {}
        self.searxng_capabilities = capabilities or {}
        self.web_search_cache = search_cache if search_cache is not None else {}
        self.opened_page_cache = opened_page_cache if opened_page_cache is not None else {}
        self.telemetry = telemetry

    def _emit(self, event_type: str, payload: dict | None = None):
        if not self.telemetry:
            return
        try:
            self.telemetry.emit(event_type, payload if isinstance(payload, dict) else {})
        except Exception:
            pass

    def _cache_get(self, cache: dict, key: str, ttl: int):
        now = _now_float()
        item = cache.get(key)
        if not item:
            return None
        if now - item.get("ts", 0) > ttl:
            cache.pop(key, None)
            return None
        return item.get("value")

    def _cache_put(self, cache: dict, key: str, value):
        cache[key] = {"ts": _now_float(), "value": value}

    def _allowed_engine_names(self):
        caps = self.searxng_capabilities or {}
        ok = set()
        for name, meta in (caps.get("engine_probe") or {}).items():
            if isinstance(meta, dict) and meta.get("status") == "ok":
                ok.add(name)
        if ok:
            return ok
        for item in caps.get("recommended_for_coding_agent") or []:
            if isinstance(item, dict) and item.get("name"):
                ok.add(item["name"])
        return ok

    def _policy_get_path(self, dotted, default=None):
        obj = self.searxng_policy or {}
        for part in str(dotted or "").split("."):
            if not part:
                continue
            if not isinstance(obj, dict):
                return default
            obj = obj.get(part)
        return obj if obj is not None else default

    def _blocked_engines(self, profile: str):
        policy = self.searxng_policy or {}
        blocked = set(_string_list(policy.get("disabled_even_if_configured")))
        blocked.update(_string_list(policy.get("non_text_engines_disabled_for_current_web_search_tool")))
        blocked.update(_string_list(policy.get("quarantine_until_fixed")))
        if profile == "coding":
            blocked.update(_string_list(policy.get("never_for_coding_agent")))
        return blocked

    def _filter_engines(self, engines, profile: str):
        blocked = self._blocked_engines(profile)
        ok_engines = self._allowed_engine_names()
        filtered = []
        seen = set()
        for engine in _string_list(engines):
            if engine in seen or engine in blocked:
                continue
            if ok_engines and engine not in ok_engines:
                continue
            seen.add(engine)
            filtered.append(engine)
        return filtered

    def _infer_search_profile(self, query: str):
        routing = (self.searxng_policy or {}).get("routing") or {}
        keywords = routing.get("auto_keywords") or {}
        precedence = _string_list(routing.get("auto_precedence")) or [
            "ai_models",
            "sysadmin",
            "coding",
            "research",
            "news",
            "reference",
            "broad",
        ]
        text = _normalize_ws(query or "").lower()
        for profile in precedence:
            if profile not in VALID_WEB_SEARCH_PROFILES or profile == "auto":
                continue
            for keyword in _string_list(keywords.get(profile)):
                if keyword.lower() in text:
                    return profile
        default_profile = str(routing.get("default_profile") or "broad").strip()
        return default_profile if default_profile in VALID_WEB_SEARCH_PROFILES and default_profile != "auto" else "broad"

    def _profile_config(self, profile: str, query: str):
        requested_profile = str(profile or "auto").strip()
        if requested_profile not in VALID_WEB_SEARCH_PROFILES:
            requested_profile = "auto"
        actual_profile = self._infer_search_profile(query) if requested_profile == "auto" else requested_profile
        if actual_profile not in VALID_WEB_SEARCH_PROFILES or actual_profile == "auto":
            actual_profile = "broad"

        profiles = (self.searxng_policy or {}).get("web_search_profiles") or {}
        cfg = profiles.get(actual_profile) if isinstance(profiles, dict) else None
        cfg = cfg if isinstance(cfg, dict) else {}

        categories = _string_list(cfg.get("categories"))
        categories_from = cfg.get("categories_from")
        if not categories and isinstance(categories_from, str):
            categories = _string_list(self._policy_get_path(categories_from))

        engines = _string_list(cfg.get("engines"))
        engines_from = cfg.get("engines_from")
        if not engines and isinstance(engines_from, str):
            engines = _string_list(self._policy_get_path(engines_from))

        fallback_profiles = [
            item for item in _string_list(cfg.get("fallback_profiles"))
            if item in VALID_WEB_SEARCH_PROFILES and item != "auto" and item != actual_profile
        ]

        if not categories and actual_profile == "coding":
            legacy = self._coding_profile()
            categories = legacy["categories"]
            engines = engines or legacy["engines"]
            fallback_profiles = fallback_profiles or ["broad"]
        elif not categories:
            categories = ["general", "web"] if actual_profile == "broad" else ["general"]

        if not engines and actual_profile == "broad":
            engines = _string_list((self.searxng_policy or {}).get("agent_default", {}).get("engines"))

        if actual_profile == "coding":
            text = _normalize_ws(query or "").lower()
            coding_error_terms = (
                " error",
                "error:",
                "traceback",
                "exception",
                "decode",
                "stdin",
                "failed",
                "cannot",
                "can't",
                "stack trace",
            )
            if any(term in f" {text}" for term in coding_error_terms):
                categories = ["q&a"]
                engines = ["stackoverflow", "superuser", "askubuntu", "discuss.python"]

        return {
            "requested_profile": requested_profile,
            "profile": actual_profile,
            "categories": categories,
            "engines": self._filter_engines(engines, actual_profile),
            "fallback_profiles": fallback_profiles,
        }

    def _coding_profile(self):
        policy = self.searxng_policy or {}
        caps = self.searxng_capabilities or {}
        safe_categories = set(caps.get("safe_categories") or [])
        disallowed = set(policy.get("disabled_even_if_configured") or [])
        disallowed |= set(policy.get("never_for_coding_agent") or [])
        ok_engines = self._allowed_engine_names()

        categories = list((policy.get("agent_coding") or {}).get("categories") or ["it", "repos", "q&a", "packages", "software wikis"])
        if safe_categories:
            categories = [c for c in categories if c in safe_categories]
        if not categories:
            categories = ["it", "repos", "q&a", "packages", "software wikis"]

        engines = list((policy.get("agent_coding") or {}).get("engines") or [])
        engines = [e for e in engines if e not in disallowed and (not ok_engines or e in ok_engines)]

        fallback_engines = list((policy.get("agent_default") or {}).get("engines") or [])
        fallback_engines = [e for e in fallback_engines if e not in disallowed and (not ok_engines or e in ok_engines)]

        if not engines:
            engines = fallback_engines[:8]

        fallback_categories = list((policy.get("agent_default") or {}).get("categories") or ["web", "general"])
        if safe_categories:
            fallback_categories = [c for c in fallback_categories if c in safe_categories]
        if not fallback_categories:
            fallback_categories = ["web", "general"]

        return {
            "categories": categories,
            "engines": engines,
            "fallback_categories": fallback_categories,
            "fallback_engines": fallback_engines,
        }

    def _query_searxng(self, query: str, categories=None, engines=None, top_k: int = WEB_SEARCH_MAX_RESULTS):
        if not self.searxng_base_url:
            return {"error": "SearXNG is not configured.", "results": []}

        categories = [c for c in (categories or []) if isinstance(c, str) and c.strip()]
        engines = [e for e in (engines or []) if isinstance(e, str) and e.strip()]
        key = json.dumps({
            "q": query,
            "categories": categories,
            "engines": engines,
            "top_k": top_k,
        }, sort_keys=True)
        cached = self._cache_get(self.web_search_cache, key, WEB_SEARCH_SEARCH_CACHE_TTL)
        if cached is not None:
            return cached

        params = {
            "q": query,
            "format": "json",
            "pageno": "1",
        }
        if categories:
            params["categories"] = ",".join(categories)
        if engines:
            params["engines"] = ",".join(engines)

        url = self.searxng_base_url.rstrip("/") + "/search?" + urllib.parse.urlencode(params)
        try:
            raw, _content_type, _final_url = _http_fetch(url, self.searxng_timeout, "application/json")
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            result = {"error": str(e), "results": []}
            self._cache_put(self.web_search_cache, key, result)
            return result

        results = []
        seen = set()
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            item_url = _canonicalize_url(item.get("url") or "")
            if not item_url or item_url in seen:
                continue
            seen.add(item_url)
            results.append({
                "title": _normalize_ws(item.get("title") or "") or item_url,
                "url": item_url,
                "snippet": _truncate(_normalize_ws(item.get("content") or ""), 400),
                "engine": item.get("engine"),
                "engines": item.get("engines") or [],
                "published_date": item.get("publishedDate") or item.get("pubdate"),
            })
            if len(results) >= max(1, min(int(top_k or WEB_SEARCH_MAX_RESULTS), WEB_SEARCH_MAX_RESULTS)):
                break

        result = {
            "query": query,
            "results": results,
            "categories": categories,
            "engines": engines,
            "unresponsive_engines": payload.get("unresponsive_engines") or [],
            "answers": payload.get("answers") or [],
        }
        self._cache_put(self.web_search_cache, key, result)
        return result

    def _search_web(self, query: str, profile="auto", categories=None, engines=None, top_k: int = WEB_SEARCH_MAX_RESULTS):
        route = self._profile_config(profile, query)
        explicit_categories = _string_list(categories)
        explicit_engines = _string_list(engines)
        primary_categories = explicit_categories or route["categories"]
        primary_engines = self._filter_engines(explicit_engines, route["profile"]) if explicit_engines else route["engines"]
        query_categories = [] if route["profile"] in ("ai_models", "broad") and primary_engines else primary_categories

        threshold = 1
        try:
            threshold = int(((self.searxng_policy or {}).get("routing") or {}).get("low_result_fallback_threshold") or 1)
        except Exception:
            threshold = 1
        threshold = max(1, min(threshold, WEB_SEARCH_MAX_RESULTS))

        self._emit("web_search_route", {
            "query": query,
            "requested_profile": route["requested_profile"],
            "selected_profile": route["profile"],
            "categories": primary_categories,
            "query_categories": query_categories,
            "engines": primary_engines,
            "fallback_profiles": route["fallback_profiles"],
            "explicit_categories": bool(explicit_categories),
            "explicit_engines": bool(explicit_engines),
        })

        result = self._query_searxng(query, query_categories, primary_engines, top_k=top_k)
        result.update({
            "requested_profile": route["requested_profile"],
            "profile": route["profile"],
            "fallback_used": None,
            "fallback_profiles": route["fallback_profiles"],
            "categories": primary_categories,
            "engines": primary_engines,
            "query_categories": query_categories,
        })

        route_log = {
            "query": query,
            "requested_profile": route["requested_profile"],
            "selected_profile": route["profile"],
            "categories": primary_categories,
            "query_categories": query_categories,
            "engines": primary_engines,
            "fallback_profiles": route["fallback_profiles"],
            "fallback_used": None,
            "result_count": len(result.get("results") or []),
            "threshold": threshold,
            "explicit_categories": bool(explicit_categories),
            "explicit_engines": bool(explicit_engines),
        }

        # Explicit engine/category calls are expert overrides. Do not silently route elsewhere.
        if explicit_categories or explicit_engines or len(result.get("results") or []) >= threshold:
            runtime_log("latest-web-search-route.json", route_log)
            return result

        best = result
        primary_count = len(result.get("results") or [])
        for fallback_profile in route["fallback_profiles"]:
            fallback_route = self._profile_config(fallback_profile, query)
            fallback_query_categories = [] if fallback_route["profile"] in ("ai_models", "broad") and fallback_route["engines"] else fallback_route["categories"]
            fallback = self._query_searxng(
                query,
                fallback_query_categories,
                fallback_route["engines"],
                top_k=top_k,
            )
            fallback_count = len(fallback.get("results") or [])
            route_log.setdefault("fallback_attempts", []).append({
                "profile": fallback_route["profile"],
                "categories": fallback_route["categories"],
                "query_categories": fallback_query_categories,
                "engines": fallback_route["engines"],
                "result_count": fallback_count,
            })
            if fallback_count > len(best.get("results") or []):
                fallback.update({
                    "requested_profile": route["requested_profile"],
                    "profile": fallback_route["profile"],
                    "fallback_used": fallback_route["profile"],
                    "fallback_profiles": route["fallback_profiles"],
                    "primary_profile": route["profile"],
                    "primary_result_count": primary_count,
                    "categories": fallback_route["categories"],
                    "query_categories": fallback_query_categories,
                    "engines": fallback_route["engines"],
                })
                best = fallback
            if len(best.get("results") or []) >= threshold:
                break

        route_log["fallback_used"] = best.get("fallback_used")
        route_log["result_count"] = len(best.get("results") or [])
        self._emit("web_search_route", route_log)
        runtime_log("latest-web-search-route.json", route_log)
        return best

    def _open_page(self, url: str):
        canonical_url = _canonicalize_url(url)
        if not canonical_url:
            return {"error": f"Unsupported URL: {url}"}

        cached = self._cache_get(self.opened_page_cache, canonical_url, WEB_SEARCH_PAGE_CACHE_TTL)
        if cached is not None:
            return cached

        try:
            raw, content_type, final_url = _http_fetch(
                canonical_url,
                self.searxng_timeout,
                "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.1",
            )
        except Exception as e:
            result = {
                "url": canonical_url,
                "page_id": "page_" + base64.urlsafe_b64encode(canonical_url.encode("utf-8")).decode("ascii").rstrip("="),
                "title": canonical_url,
                "content": "",
                "content_type": "fetch_error",
                "status": "error",
                "error": str(e),
            }
            self._cache_put(self.opened_page_cache, canonical_url, result)
            return result

        title, text = _extract_page_text(raw, content_type)
        final_url = _canonicalize_url(final_url) or canonical_url
        result = {
            "url": final_url,
            "page_id": "page_" + base64.urlsafe_b64encode(final_url.encode("utf-8")).decode("ascii").rstrip("="),
            "title": title or final_url,
            "content": _truncate(text, 12000),
            "content_type": content_type,
            "status": "ok",
        }
        self._cache_put(self.opened_page_cache, canonical_url, result)
        if final_url != canonical_url:
            self._cache_put(self.opened_page_cache, final_url, result)
        return result

    def _find_in_page(self, query: str, url: str = None, page_id: str = None):
        page = None
        if page_id:
            for item in self.opened_page_cache.values():
                value = item.get("value") if isinstance(item, dict) else None
                if isinstance(value, dict) and value.get("page_id") == page_id:
                    page = value
                    break
        if page is None and url:
            page = self._open_page(url)

        if not isinstance(page, dict) or not page.get("content"):
            return {
                "page_id": page.get("page_id") if isinstance(page, dict) else page_id,
                "url": page.get("url") if isinstance(page, dict) else url,
                "title": page.get("title") if isinstance(page, dict) else (url or ""),
                "query": query,
                "matches": [],
                "status": "empty",
            }

        haystack = page.get("content", "")
        needle = (query or "").strip()
        if not needle:
            return {
                "page_id": page.get("page_id"),
                "url": page.get("url"),
                "title": page.get("title"),
                "query": query,
                "matches": [],
                "status": "empty",
            }

        lower_haystack = haystack.lower()
        lower_needle = needle.lower()
        matches = []
        start = 0
        while len(matches) < 5:
            idx = lower_haystack.find(lower_needle, start)
            if idx < 0:
                break
            snippet_start = max(0, idx - 140)
            snippet_end = min(len(haystack), idx + len(needle) + 220)
            snippet = _normalize_ws(haystack[snippet_start:snippet_end])
            matches.append({
                "start_index": idx,
                "end_index": idx + len(needle) - 1,
                "snippet": snippet,
            })
            start = idx + len(needle)

        return {
            "page_id": page.get("page_id"),
            "url": page.get("url"),
            "title": page.get("title"),
            "query": query,
            "matches": matches,
            "status": "ok" if matches else "empty",
        }

    def _parse_web_search_arguments(self, arguments: str):
        try:
            data = json.loads(arguments or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        action = str(data.get("action") or "").strip() or "search"
        query = data.get("query")
        profile = str(data.get("profile") or "auto").strip()
        if profile not in VALID_WEB_SEARCH_PROFILES:
            profile = "auto"
        url = data.get("url")
        page_id = data.get("page_id")
        categories = data.get("categories") if isinstance(data.get("categories"), list) else None
        engines = data.get("engines") if isinstance(data.get("engines"), list) else None
        top_k = data.get("top_k")
        try:
            top_k = int(top_k) if top_k is not None else WEB_SEARCH_MAX_RESULTS
        except Exception:
            top_k = WEB_SEARCH_MAX_RESULTS
        top_k = max(1, min(top_k, WEB_SEARCH_MAX_RESULTS))
        return {
            "action": action,
            "query": query,
            "profile": profile,
            "url": url,
            "page_id": page_id,
            "categories": categories,
            "engines": engines,
            "top_k": top_k,
        }

    def execute_web_search_call(self, call_item: dict, counters: dict, seen_signatures: set):
        args = self._parse_web_search_arguments(call_item.get("arguments") or "{}")
        action = args["action"]
        query = args.get("query")
        profile = args.get("profile") or "auto"
        url = args.get("url")
        page_id = args.get("page_id")
        signature = (
            action,
            profile if action == "search" else "",
            _normalize_ws(query or "").lower(),
            _canonicalize_url(url or ""),
            page_id or "",
        )

        if signature in seen_signatures:
            repeated = True
        else:
            repeated = False
            seen_signatures.add(signature)

        started_at = _now_float()
        self._emit("tool_call_started", {
            "tool": "web_search",
            "action": action,
            "call_id": call_item.get("call_id") or call_item.get("id"),
            "query": query if isinstance(query, str) else None,
            "profile": profile if action == "search" else None,
            "url": url if isinstance(url, str) else None,
            "page_id": page_id if isinstance(page_id, str) else None,
        })

        error = None
        payload = {}
        sources = []

        if action == "search":
            if counters["search"] >= WEB_SEARCH_MAX_SEARCHES:
                error = f"Refusing search: reached per-turn limit of {WEB_SEARCH_MAX_SEARCHES} search calls."
            elif repeated:
                error = "Refusing repeated search request; use the cached result or open a page instead."
            elif not isinstance(query, str) or not query.strip():
                error = "Missing query for search."
            else:
                counters["search"] += 1
                payload = self._search_web(
                    query=query.strip(),
                    profile=profile,
                    categories=args.get("categories"),
                    engines=args.get("engines"),
                    top_k=args.get("top_k") or WEB_SEARCH_MAX_RESULTS,
                )
                sources = [{"url": r.get("url"), "title": r.get("title")} for r in payload.get("results") or []]

        elif action == "open_page":
            if counters["open_page"] >= WEB_SEARCH_MAX_OPENS:
                error = f"Refusing open_page: reached per-turn limit of {WEB_SEARCH_MAX_OPENS} page opens."
            elif repeated:
                error = "Refusing repeated open_page request for the same page."
            elif not isinstance(url, str) or not url.strip():
                error = "Missing url for open_page."
            else:
                counters["open_page"] += 1
                payload = self._open_page(url.strip())
                sources = [{"url": payload.get("url"), "title": payload.get("title")}]

        elif action == "find_in_page":
            if repeated:
                error = "Refusing repeated find_in_page request with the same arguments."
            elif not isinstance(query, str) or not query.strip():
                error = "Missing query for find_in_page."
            elif not page_id and not url:
                error = "find_in_page requires page_id or url."
            else:
                payload = self._find_in_page(query=query.strip(), url=url, page_id=page_id)
                sources = [{"url": payload.get("url"), "title": payload.get("title")}]

        else:
            error = f"Unsupported web_search action: {action}"

        result_payload = {
            "ok": error is None,
            "action": action,
            "result": payload if error is None else {},
            "error": error,
        }

        web_call_item = {
            "id": call_item.get("id") or call_item.get("call_id") or f"wsc_local_{_now_ts()}",
            "type": "web_search_call",
            "status": "completed",
            "call_id": call_item.get("call_id"),
            "action": {
                "type": action,
            },
        }

        if action == "search" and isinstance(query, str):
            web_call_item["action"]["queries"] = [query]
            web_call_item["action"]["profile"] = profile
            if isinstance(payload, dict) and payload.get("profile"):
                web_call_item["action"]["selected_profile"] = payload.get("profile")
            if isinstance(payload, dict) and payload.get("fallback_used"):
                web_call_item["action"]["fallback_used"] = payload.get("fallback_used")
            web_call_item["action"]["result_count"] = len((payload or {}).get("results") or [])
        elif action == "open_page" and isinstance(url, str):
            web_call_item["action"]["url"] = payload.get("url") if isinstance(payload, dict) else url
            if isinstance(payload, dict) and payload.get("page_id"):
                web_call_item["action"]["page_id"] = payload.get("page_id")
        elif action == "find_in_page":
            web_call_item["action"]["query"] = query
            if isinstance(payload, dict):
                web_call_item["action"]["url"] = payload.get("url")
                web_call_item["action"]["page_id"] = payload.get("page_id")
                web_call_item["action"]["match_count"] = len(payload.get("matches") or [])

        if error:
            web_call_item["status"] = "failed"
            web_call_item["error"] = error

        tool_output_item = {
            "type": "function_call_output",
            "call_id": call_item.get("call_id") or call_item.get("id") or f"fc_local_{_now_ts()}",
            "output": json.dumps(result_payload, ensure_ascii=False),
        }

        self._emit("tool_call_completed", {
            "tool": "web_search",
            "action": action,
            "call_id": call_item.get("call_id") or call_item.get("id"),
            "status": "failed" if error else "completed",
            "error": error,
            "result_count": len(sources) if action == "search" else len(sources),
            "duration_ms": round((_now_float() - started_at) * 1000.0, 2),
        })

        return web_call_item, tool_output_item, _unique_sources(sources)
