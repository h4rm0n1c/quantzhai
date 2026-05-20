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
    from .qz_tools import ToolCoercionResult, ToolLifecycleSpec, function_tool
except ImportError:
    from qz_runtime_io import runtime_log
    from qz_tools import ToolCoercionResult, ToolLifecycleSpec, function_tool


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
    lifecycle = ToolLifecycleSpec(
        name="web_search",
        execution="proxy_local",
        public_item_type="web_search_call",
        telemetry_name="web_search",
        continuation_hops=6,
        lifecycle_event_prefix="response.web_search_call",
        lifecycle_start_stages=("in_progress", "searching"),
        lifecycle_done_stages=("completed",),
    )

    def coerce(self, call: dict) -> ToolCoercionResult:
        """Coerce a malformed web_search function_call.

        web_search execution already handles missing/bad fields with in-band
        errors, so this only needs to catch truly unparseable structures.
        """
        import json as _json
        arguments = call.get("arguments") or "{}" if isinstance(call, dict) else "{}"
        try:
            data = _json.loads(arguments)
        except Exception:
            return ToolCoercionResult(
                error_message=(
                    "web_search: arguments are not valid JSON. "
                    "Provide {\"action\": \"search\", \"query\": \"your query\"} as JSON."
                )
            )
        if not isinstance(data, dict):
            return ToolCoercionResult(
                error_message=(
                    "web_search: arguments must be a JSON object. "
                    "Provide {\"action\": \"search\", \"query\": \"your query\"}."
                )
            )
        # Arguments are structurally valid; runtime validation handles the rest.
        return ToolCoercionResult(corrected_arguments=arguments)

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
                        "description": "Search profile used to select SearXNG categories and engines. Common profiles: auto, broad, coding, research, news, ai_models, reference, sysadmin. Local policy may define more.",
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
WEB_SEARCH_MAX_HOPS = WEB_SEARCH_TOOL_ADAPTER.lifecycle.continuation_hops
WEB_SEARCH_MAX_SEARCHES = 4
WEB_SEARCH_MAX_OPENS = 3
WEB_SEARCH_MAX_RETRIEVALS = 3
WEB_SEARCH_RETRIEVE_MAX_CHARS = 6000
WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING = 12000
WEB_SEARCH_RETRIEVE_CACHE_TTL = 900
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
        entry: dict = {"url": url, "title": title or url}
        # Carry through safe annotation fields if already present.
        for _af in ("domain", "source_kind", "trust_hint", "freshness_hint",
                    "retrieval_available", "retrieval_source", "retrieval_retriever"):
            if _af in source:
                entry[_af] = source[_af]
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Source annotation helpers
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    """Return the netloc hostname (lower-case, no port) from a URL."""
    try:
        host = urllib.parse.urlsplit(url.strip()).netloc.lower()
        return host.split(":")[0] if ":" in host else host
    except Exception:
        return ""


# Layer-1 domain → (source_kind, trust_hint)
_DOMAIN_SOURCE_KIND: dict = {
    # Official docs
    "docs.python.org": ("official_docs", "high"),
    "developer.mozilla.org": ("official_docs", "high"),
    "learn.microsoft.com": ("official_docs", "high"),
    "docs.microsoft.com": ("official_docs", "high"),
    "developer.apple.com": ("official_docs", "high"),
    "docs.oracle.com": ("official_docs", "high"),
    "wiki.alliedmods.net": ("official_docs", "high"),
    "wiki.teamfortress.com": ("official_docs", "high"),
    "developer.valvesoftware.com": ("official_docs", "high"),
    # Source repos
    "github.com": ("source_repo", "high"),
    "gitlab.com": ("source_repo", "high"),
    "codeberg.org": ("source_repo", "high"),
    "sourcehut.org": ("source_repo", "high"),
    "gitea.com": ("source_repo", "high"),
    "bitbucket.org": ("source_repo", "high"),
    # Q&A
    "stackoverflow.com": ("q_and_a", "medium"),
    "superuser.com": ("q_and_a", "medium"),
    "askubuntu.com": ("q_and_a", "medium"),
    "discuss.python.org": ("q_and_a", "medium"),
    "serverfault.com": ("q_and_a", "medium"),
    # Package registries
    "pypi.org": ("package_registry", "high"),
    "npmjs.com": ("package_registry", "high"),
    "crates.io": ("package_registry", "high"),
    "lib.rs": ("package_registry", "high"),
    "hub.docker.com": ("package_registry", "high"),
    "pkg.go.dev": ("package_registry", "high"),
    "rubygems.org": ("package_registry", "high"),
    # Academic
    "arxiv.org": ("academic", "high"),
    "pubmed.ncbi.nlm.nih.gov": ("academic", "high"),
    "semanticscholar.org": ("academic", "high"),
    # Encyclopedia
    "wikipedia.org": ("encyclopedia", "medium"),
    "en.wikipedia.org": ("encyclopedia", "medium"),
    "wikibooks.org": ("encyclopedia", "medium"),
    # Gaming wikis
    "www.pcgamingwiki.com": ("gaming_wiki", "medium"),
    "pcgamingwiki.com": ("gaming_wiki", "medium"),
    # Character cards (domain-based)
    "www.taverncard.com": ("character_card", "low"),
    "taverncard.com": ("character_card", "low"),
    "aicharactercards.com": ("character_card", "low"),
    "www.aicharactercards.com": ("character_card", "low"),
    # Furry community (domain-based)
    "fse.anthro.fr": ("furry_community", "low"),
    "furbooru.org": ("furry_community", "low"),
    "www.furbooru.org": ("furry_community", "low"),
    "e926.net": ("furry_community", "low"),
    "www.e926.net": ("furry_community", "low"),
    # Hackernews/community
    "news.ycombinator.com": ("forum", "low"),
    "lobste.rs": ("forum", "low"),
    "reddit.com": ("forum", "low"),
    "www.reddit.com": ("forum", "low"),
}

# Layer-2 retrieval.source → (source_kind, trust_hint)
_RETRIEVAL_SOURCE_KIND: dict = {
    "character-card": ("character_card", "low"),
    "fse": ("prose_archive", "low"),
    "furbooru": ("furry_community", "low"),
    "valve-developer-community": ("official_docs", "high"),
    "bitmagnet": ("local_index", "low"),
}

# mediawiki retrieval: domain-specific override
_MEDIAWIKI_DOMAIN_KIND: dict = {
    "pcgamingwiki.com": ("gaming_wiki", "medium"),
    "www.pcgamingwiki.com": ("gaming_wiki", "medium"),
    "wiki.alliedmods.net": ("official_docs", "high"),
    "wiki.teamfortress.com": ("official_docs", "high"),
    "developer.valvesoftware.com": ("official_docs", "high"),
}


def _source_kind_and_trust(domain: str, retrieval: dict | None) -> tuple[str, str]:
    """Return (source_kind, trust_hint), preferring Layer 2 retrieval metadata."""
    if retrieval and isinstance(retrieval, dict) and retrieval.get("available"):
        rsource = str(retrieval.get("source") or "")
        if rsource == "mediawiki":
            # mediawiki: resolve by domain
            if domain in _MEDIAWIKI_DOMAIN_KIND:
                return _MEDIAWIKI_DOMAIN_KIND[domain]
            return "wiki", "medium"
        if rsource in _RETRIEVAL_SOURCE_KIND:
            return _RETRIEVAL_SOURCE_KIND[rsource]
    # Layer 1: domain lookup
    if domain in _DOMAIN_SOURCE_KIND:
        return _DOMAIN_SOURCE_KIND[domain]
    return "unknown", "unknown"


def _freshness_hint(url: str, published_date: str | None) -> str:
    """Derive freshness from publishedDate then URL year. Returns recent/dated/unknown."""
    import datetime as _dt, re as _re
    current_year = _dt.date.today().year
    # Try publishedDate
    for src in (published_date,):
        if not src:
            continue
        m = _re.search(r"\b(20\d{2})\b", str(src))
        if m:
            y = int(m.group(1))
            return "recent" if y >= current_year - 1 else "dated"
    # Try URL path year
    m = _re.search(r"(?<![0-9])(20\d{2})(?![0-9])", url or "")
    if m:
        y = int(m.group(1))
        return "recent" if y >= current_year - 1 else "dated"
    return "unknown"


def _annotate_source(url: str, retrieval: dict | None = None,
                     published_date: str | None = None) -> dict:
    """Return source annotation fields for one result.

    Never exposes retrieval.endpoint (localhost URL).
    """
    domain = _extract_domain(url)
    source_kind, trust_hint = _source_kind_and_trust(domain, retrieval)
    ann: dict = {
        "domain": domain,
        "source_kind": source_kind,
        "trust_hint": trust_hint,
        "freshness_hint": _freshness_hint(url, published_date),
    }
    if retrieval and isinstance(retrieval, dict):
        ann["retrieval_available"] = bool(retrieval.get("available"))
        rsource = retrieval.get("source")
        if rsource:
            ann["retrieval_source"] = str(rsource)
        rretriever = retrieval.get("retriever")
        if rretriever:
            ann["retrieval_retriever"] = str(rretriever)
        # retrieval.endpoint is deliberately NOT included (localhost URL)
    return ann


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

def _resolve_budget_int(value, default: int, min_val: int = 1) -> int:
    """Return a valid positive int budget, falling back to *default* on any error."""
    try:
        ival = int(value)
        return ival if ival >= min_val else default
    except (TypeError, ValueError):
        return default


class WebSearchRuntime:
    def __init__(
        self,
        base_url=None,
        timeout=15.0,
        policy=None,
        capabilities=None,
        search_cache=None,
        opened_page_cache=None,
        telemetry=None,
        policy_path: str = "",
        default_profile: str = "",
        policy_selection=None,
        search_config_profiles=None,
        max_searches_per_turn=None,
        max_page_opens_per_turn=None,
        max_results_per_query=None,
        low_result_fallback_threshold=None,
        max_retrievals_per_turn=None,
        max_retrieved_chars=None,
    ):
        self.searxng_base_url = base_url
        self.searxng_timeout = timeout
        self.searxng_policy = policy or {}
        self.searxng_capabilities = capabilities or {}
        self.web_search_cache = search_cache if search_cache is not None else {}
        self.opened_page_cache = opened_page_cache if opened_page_cache is not None else {}
        self.telemetry = telemetry
        self.searxng_policy_path = policy_path or ""
        self.default_search_profile = str(default_profile or "").strip()
        self.search_policy_selection = policy_selection if isinstance(policy_selection, dict) else {}
        self.search_config_profiles = search_config_profiles if isinstance(search_config_profiles, dict) else {}
        # Budget limits: prefer explicit params; fall back to module constants.
        self.max_searches_per_turn = _resolve_budget_int(max_searches_per_turn, WEB_SEARCH_MAX_SEARCHES)
        self.max_page_opens_per_turn = _resolve_budget_int(max_page_opens_per_turn, WEB_SEARCH_MAX_OPENS)
        # Clamp to the safe ceiling: no config value can exceed WEB_SEARCH_MAX_RESULTS.
        self.max_results_per_query = min(
            _resolve_budget_int(max_results_per_query, WEB_SEARCH_MAX_RESULTS),
            WEB_SEARCH_MAX_RESULTS,
        )
        # low_result_fallback_threshold: try param, then legacy policy key, then default 2.
        self.max_retrievals_per_turn = _resolve_budget_int(max_retrievals_per_turn, WEB_SEARCH_MAX_RETRIEVALS)
        _raw_chars = _resolve_budget_int(max_retrieved_chars, WEB_SEARCH_RETRIEVE_MAX_CHARS)
        self.max_retrieved_chars = min(_raw_chars, WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING)
        self.retrieval_cache: dict = {}
        if low_result_fallback_threshold is not None:
            self.low_result_fallback_threshold = _resolve_budget_int(low_result_fallback_threshold, 2)
        else:
            try:
                legacy = int(
                    (self.searxng_policy.get("routing") or {}).get("low_result_fallback_threshold") or 2
                )
                self.low_result_fallback_threshold = max(1, legacy)
            except Exception:
                self.low_result_fallback_threshold = 2

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

    def _valid_profiles(self):
        profiles = set(VALID_WEB_SEARCH_PROFILES)
        policy_profiles = (self.searxng_policy or {}).get("web_search_profiles") or {}
        if isinstance(policy_profiles, dict):
            profiles.update(
                name for name in policy_profiles.keys()
                if isinstance(name, str) and name.strip()
            )
        if isinstance(self.search_config_profiles, dict):
            profiles.update(
                name for name in self.search_config_profiles.keys()
                if isinstance(name, str) and name.strip()
            )
        return profiles

    def _is_valid_profile(self, profile: str):
        return profile in self._valid_profiles()

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
        if self.default_search_profile and self.default_search_profile != "auto" and self._is_valid_profile(self.default_search_profile):
            return self.default_search_profile
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
            if not self._is_valid_profile(profile) or profile == "auto":
                continue
            for keyword in _string_list(keywords.get(profile)):
                if keyword.lower() in text:
                    return profile
        default_profile = str(routing.get("default_profile") or "broad").strip()
        return default_profile if self._is_valid_profile(default_profile) and default_profile != "auto" else "broad"

    def _profile_config(self, profile: str, query: str):
        requested_profile = str(profile or "auto").strip()
        if not self._is_valid_profile(requested_profile):
            requested_profile = "auto"
        actual_profile = self._infer_search_profile(query) if requested_profile == "auto" else requested_profile
        if not self._is_valid_profile(actual_profile) or actual_profile == "auto":
            actual_profile = "broad"

        profiles = (self.searxng_policy or {}).get("web_search_profiles") or {}
        cfg = profiles.get(actual_profile) if isinstance(profiles, dict) else None
        cfg = cfg if isinstance(cfg, dict) else {}
        # Fall back to qz.search.v1 profiles when legacy policy has no entry.
        if not cfg and isinstance(self.search_config_profiles, dict):
            v1_cfg = self.search_config_profiles.get(actual_profile)
            if isinstance(v1_cfg, dict):
                cfg = v1_cfg

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
            if self._is_valid_profile(item) and item != "auto" and item != actual_profile
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
            pub_date = item.get("publishedDate") or item.get("pubdate")
            retrieval_meta = item.get("retrieval") if isinstance(item.get("retrieval"), dict) else None
            entry: dict = {
                "title": _normalize_ws(item.get("title") or "") or item_url,
                "url": item_url,
                "snippet": _truncate(_normalize_ws(item.get("content") or ""), 400),
                "engine": item.get("engine"),
                "engines": item.get("engines") or [],
                "published_date": pub_date,
            }
            entry.update(_annotate_source(item_url, retrieval_meta, pub_date))
            results.append(entry)
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

        threshold = max(1, min(self.low_result_fallback_threshold, self.max_results_per_query))

        self._emit("web_search_route", {
            "query": query,
            "requested_profile": route["requested_profile"],
            "selected_profile": route["profile"],
            "search_policy": self.search_policy_selection,
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
            "search_policy": self.search_policy_selection,
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
            "search_policy": self.search_policy_selection,
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
                    "search_policy": self.search_policy_selection,
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
        if not self._is_valid_profile(profile):
            profile = "auto"
        url = data.get("url")
        page_id = data.get("page_id")
        categories = data.get("categories") if isinstance(data.get("categories"), list) else None
        engines = data.get("engines") if isinstance(data.get("engines"), list) else None
        top_k = data.get("top_k")
        try:
            top_k = int(top_k) if top_k is not None else self.max_results_per_query
        except Exception:
            top_k = self.max_results_per_query
        top_k = max(1, min(top_k, self.max_results_per_query))
        retrieval_source = str(data.get("retrieval_source") or "").strip()
        return {
            "action": action,
            "query": query,
            "profile": profile,
            "url": url,
            "page_id": page_id,
            "categories": categories,
            "engines": engines,
            "top_k": top_k,
            "retrieval_source": retrieval_source,
        }

    def _normalize_retrieve_response(self, raw: dict, url: str) -> dict:
        """Normalize Agent API /retrieve response into unified output.

        Handles two upstream shapes:
          - Normalized format (mediawiki, FSE): has status/summary/fields/agent_api
          - Character-card format: flat top-level with name/description/creator/tags/agent_api
        Never includes retrieval.endpoint or localhost URLs.
        """
        if not isinstance(raw, dict):
            return {"ok": False, "error": "invalid_response"}

        agent_api = raw.get("agent_api") or {}
        retriever = str(agent_api.get("retriever") or "")
        retrieval_source = str(agent_api.get("source") or raw.get("source") or "")
        canonical_url = str(raw.get("input") or url or "")

        # Detect normalized format vs character-card format
        is_normalized = "status" in raw or "fields" in raw or "summary" in raw

        if is_normalized:
            status = str(raw.get("status") or "")
            if status and status != "ok":
                return {
                    "ok": False, "action": "retrieve",
                    "url": canonical_url, "retrieval_source": retrieval_source,
                    "error": "retrieval_failed", "error_detail": status,
                }
            fields = raw.get("fields") or {}
            title = str(fields.get("title") or fields.get("display_title") or canonical_url)
            summary = _truncate(_normalize_ws(str(raw.get("summary") or "")), 500)
            body = str(fields.get("body_text") or fields.get("body_visible") or summary)
            truncated = bool(fields.get("body_text_truncated")) or (len(body) > self.max_retrieved_chars)
            content = body[:self.max_retrieved_chars] if truncated else body
            # Bounded metadata — never include body_text
            meta = {}
            for k in ("categories", "word_count", "length", "author", "rating",
                      "published_at", "revision_id"):
                v = fields.get(k)
                if v is not None and len(meta) < 6:
                    meta[k] = v[:10] if isinstance(v, list) else v
            # Freshness: prefer freshness dict, fall back to fields timestamps (FSE)
            freshness = raw.get("freshness") or {}
            src_updated = str(
                freshness.get("source_updated_at")
                or freshness.get("last_seen")
                or fields.get("updated_at")
                or fields.get("published_at")
                or ""
            )
            freshness_hint = _freshness_hint(canonical_url, src_updated or None)
        else:
            # Character-card format
            title = str(raw.get("name") or canonical_url)
            desc = str(raw.get("description") or "")
            personality = str(raw.get("personality") or "")
            scenario = str(raw.get("scenario") or "")
            assembled = "\n\n".join(p for p in (desc, personality, scenario) if p.strip())
            truncated = len(assembled) > self.max_retrieved_chars
            content = assembled[:self.max_retrieved_chars] if truncated else assembled
            summary = _truncate(_normalize_ws(desc), 500)
            tags = (raw.get("tags") or [])[:10] if isinstance(raw.get("tags"), list) else []
            topics = (raw.get("topics") or [])[:10] if isinstance(raw.get("topics"), list) else []
            meta = {}
            for k, v in [("creator", raw.get("creator")), ("tags", tags),
                          ("topics", topics), ("spec", raw.get("spec")),
                          ("nsfw", raw.get("nsfw"))]:
                if v is not None and len(meta) < 6:
                    meta[k] = v
            freshness_hint = "unknown"

        return {
            "ok": True,
            "action": "retrieve",
            "url": canonical_url,
            "retrieval_source": retrieval_source,
            "retriever": retriever,
            "title": title,
            "summary": summary,
            "content": content,
            "metadata": meta,
            "truncated": truncated,
            "freshness_hint": freshness_hint,
        }

    def _retrieve(self, url: str, retrieval_source: str = "") -> dict:
        """Call Agent API /retrieve and return normalized result.

        Never exposes the raw /retrieve endpoint URL in the return value.
        """
        if not self.searxng_base_url:
            return {"ok": False, "action": "retrieve", "url": url,
                    "error": "no_base_url",
                    "error_detail": "No SearXNG base URL configured"}
        canonical = _canonicalize_url(url)
        if not canonical:
            return {"ok": False, "action": "retrieve", "url": url,
                    "error": "invalid_url",
                    "error_detail": "URL must be http or https"}

        cached = self._cache_get(self.retrieval_cache, canonical, WEB_SEARCH_RETRIEVE_CACHE_TTL)
        if cached is not None:
            return cached

        params: dict = {"url": canonical}
        if retrieval_source:
            params["source"] = retrieval_source
        retrieve_url = self.searxng_base_url.rstrip("/") + "/retrieve?" + urllib.parse.urlencode(params)
        try:
            raw_bytes, _ctype, _final = _http_fetch(retrieve_url, self.searxng_timeout, "application/json")
            raw = json.loads(raw_bytes.decode("utf-8", errors="replace"))
        except Exception as exc:
            return {"ok": False, "action": "retrieve", "url": canonical,
                    "error": "fetch_failed",
                    "error_detail": f"{type(exc).__name__}: {exc}"}

        result = self._normalize_retrieve_response(raw, canonical)
        if result.get("ok"):
            self._cache_put(self.retrieval_cache, canonical, result)
        return result

    def execute_web_search_call(self, call_item: dict, counters: dict, seen_signatures: set, request_id: str = ""):
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
            "request_id": request_id or call_item.get("request_id") or "",
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
            if counters["search"] >= self.max_searches_per_turn:
                error = f"Refusing search: reached per-turn limit of {self.max_searches_per_turn} search calls."
                self._emit("web_search_budget_exceeded", {
                    "action": "search",
                    "limit": self.max_searches_per_turn,
                    "counter": counters["search"],
                    "query": query if isinstance(query, str) else None,
                    "profile": profile,
                    "call_id": call_item.get("call_id") or call_item.get("id"),
                })
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
                    top_k=args.get("top_k") or self.max_results_per_query,
                )
                # Build sources with safe annotation fields; never expose retrieval.endpoint.
                sources = []
                for r in payload.get("results") or []:
                    if not isinstance(r, dict):
                        continue
                    src: dict = {"url": r.get("url"), "title": r.get("title")}
                    for _af in ("domain", "source_kind", "trust_hint", "freshness_hint",
                                "retrieval_available", "retrieval_source", "retrieval_retriever"):
                        if _af in r:
                            src[_af] = r[_af]
                    sources.append(src)

        elif action == "open_page":
            if counters["open_page"] >= self.max_page_opens_per_turn:
                error = f"Refusing open_page: reached per-turn limit of {self.max_page_opens_per_turn} page opens."
                self._emit("web_search_budget_exceeded", {
                    "action": "open_page",
                    "limit": self.max_page_opens_per_turn,
                    "counter": counters["open_page"],
                    "url": url if isinstance(url, str) else None,
                    "call_id": call_item.get("call_id") or call_item.get("id"),
                })
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

        elif action == "retrieve":
            retrieval_source = args.get("retrieval_source") or ""
            if not url or not str(url).strip():
                error = "retrieve action requires a non-empty url."
                self._emit("web_search_retrieve_failed", {
                    "url": "",
                    "retrieval_source": retrieval_source,
                    "error_class": "invalid_url",
                    "call_id": call_item.get("call_id") or call_item.get("id"),
                })
            elif counters.get("retrieve", 0) >= self.max_retrievals_per_turn:
                error = f"Refusing retrieve: reached per-turn limit of {self.max_retrievals_per_turn} retrieve calls."
                self._emit("web_search_retrieve_budget_exceeded", {
                    "url": str(url),
                    "limit": self.max_retrievals_per_turn,
                    "counter": counters.get("retrieve", 0),
                    "call_id": call_item.get("call_id") or call_item.get("id"),
                })
            else:
                self._emit("web_search_retrieve_started", {
                    "url": str(url),
                    "retrieval_source": retrieval_source,
                    "call_id": call_item.get("call_id") or call_item.get("id"),
                })
                _t0 = _now_float()
                counters["retrieve"] = counters.get("retrieve", 0) + 1
                payload = self._retrieve(str(url).strip(), retrieval_source)
                _dur = int((_now_float() - _t0) * 1000)
                if payload.get("ok"):
                    self._emit("web_search_retrieve_completed", {
                        "url": payload.get("url"),
                        "retrieval_source": payload.get("retrieval_source"),
                        "retriever": payload.get("retriever"),
                        "duration_ms": _dur,
                        "truncated": payload.get("truncated"),
                        "call_id": call_item.get("call_id") or call_item.get("id"),
                    })
                else:
                    self._emit("web_search_retrieve_failed", {
                        "url": payload.get("url"),
                        "retrieval_source": retrieval_source,
                        "error_class": payload.get("error"),
                        "call_id": call_item.get("call_id") or call_item.get("id"),
                    })
                    error = f"Retrieval failed: {payload.get('error', 'unknown')}: {payload.get('error_detail', '')}"

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
            "request_id": request_id or call_item.get("request_id") or "",
            "tool": "web_search",
            "action": action,
            "call_id": call_item.get("call_id") or call_item.get("id"),
            "status": "failed" if error else "completed",
            "error": error,
            "result_count": len(sources) if action == "search" else len(sources),
            "duration_ms": round((_now_float() - started_at) * 1000.0, 2),
        })

        return web_call_item, tool_output_item, _unique_sources(sources)
