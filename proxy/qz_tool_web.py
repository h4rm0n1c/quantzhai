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
            (
                "Search the web, open a page, find text in an opened page, retrieve structured content for a "
                "result URL, or inspect the live web_search runtime capabilities.\n\n"
                "Use action=\"capabilities\" when unsure what profiles, budgets, retrieval sources, or search "
                "modes are available. Do not rely on hardcoded profile names if capabilities are available.\n"
                "Use action=\"search\" after selecting a profile and budget_mode from capabilities. Use "
                "action=\"retrieve\" when a search result says retrieval_available=true.\n"
                "Profile and budget_mode are explicit choices; there is no automatic keyword routing. Retrieved "
                "content should inform the answer, not be dumped wholesale into it."
            ),
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "open_page", "find_in_page", "retrieve", "capabilities"],
                        "description": (
                            "capabilities: return live runtime profiles, budgets, retrieval support, and usage notes. "
                            "search: query the web and get annotated results. open_page: fetch a page by URL. "
                            "find_in_page: search for text within an opened page. retrieve: fetch structured content "
                            "for a result URL when search results report retrieval_available=true."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for search, or needle text for find_in_page.",
                    },
                    "profile": {
                        "type": "string",
                        "description": (
                            "Search profile controlling engines and categories. Use action=\"capabilities\" to inspect "
                            "the live profile list before unfamiliar research tasks."
                        ),
                    },
                    "budget_mode": {
                        "type": "string",
                        "description": (
                            "Research depth. Controls per-turn limits on searches, page opens, retrievals, and "
                            "retrieved content size. Use action=\"capabilities\" for the live supported modes."
                        ),
                    },
                    "url": {
                        "type": "string",
                        "description": "Page URL for open_page, find_in_page, or retrieve.",
                    },
                    "retrieval_source": {
                        "type": "string",
                        "description": (
                            "Optional source hint for retrieve action. "
                            "Use the retrieval_source annotation from search results "
                            "(e.g. mediawiki, fse, character-card). Helps the Agent API dispatch correctly."
                        ),
                    },
                    "page_id": {
                        "type": "string",
                        "description": "Previously opened page identifier returned by open_page, for use with find_in_page.",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional SearXNG categories to use for search. Overrides profile default.",
                    },
                    "engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional SearXNG engines to use for search. Overrides profile default.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Optional maximum number of search results to return. "
                            "Clamped to the effective budget_mode limit. "
                            "Omit to use the mode default."
                        ),
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
WEB_SEARCH_MAX_HOPS = WEB_SEARCH_TOOL_ADAPTER.lifecycle.continuation_hops
WEB_SEARCH_RETRIEVE_CACHE_TTL = 900
WEB_SEARCH_USER_AGENT = "qwen36turbo-web-runtime/1.0"

# Quick-mode built-in defaults — no longer hard ceilings; use as fallback constants only.
WEB_SEARCH_MAX_RESULTS = 8
WEB_SEARCH_MAX_SEARCHES = 4
WEB_SEARCH_MAX_OPENS = 3
WEB_SEARCH_MAX_RETRIEVALS = 3
WEB_SEARCH_RETRIEVE_MAX_CHARS = 6000

# Built-in absolute caps — operator may lower via config; cannot raise above these.
WEB_SEARCH_ABSOLUTE_MAX_RESULTS          = 100
WEB_SEARCH_ABSOLUTE_MAX_SEARCHES         = 100
WEB_SEARCH_ABSOLUTE_MAX_OPENS            = 100
WEB_SEARCH_ABSOLUTE_MAX_RETRIEVALS       = 50
WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS  = 120_000

# Named budget modes: quick / normal / deep / audit
WEB_SEARCH_DEFAULT_BUDGET_MODE = "normal"
WEB_SEARCH_VALID_BUDGET_MODES = {"quick", "normal", "deep", "audit"}
WEB_SEARCH_MODE_DEFAULTS: dict = {
    "quick":  {"max_results": 8,  "max_searches_per_turn": 4,  "max_page_opens_per_turn": 3,  "max_retrievals_per_turn": 2,  "max_retrieved_chars": 6_000},
    "normal": {"max_results": 12, "max_searches_per_turn": 8,  "max_page_opens_per_turn": 8,  "max_retrievals_per_turn": 4,  "max_retrieved_chars": 12_000},
    "deep":   {"max_results": 25, "max_searches_per_turn": 20, "max_page_opens_per_turn": 20, "max_retrievals_per_turn": 10, "max_retrieved_chars": 30_000},
    "audit":  {"max_results": 50, "max_searches_per_turn": 40, "max_page_opens_per_turn": 40, "max_retrievals_per_turn": 20, "max_retrieved_chars": 60_000},
}
VALID_WEB_SEARCH_PROFILES = {
    "auto",
    "broad",
    "coding",
    "research",
    "news",
    "ai_models",
    "reference",
    "sysadmin",
    # Shipped in config/default/search.json
    "character_cards",
    "furry",
    "furry_fse",
    "furry_images",
    "gaming_wikis",
    "archives",
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


def _safe_capability_text(value, limit: int = 220) -> str:
    text = _normalize_ws(str(value or ""))
    text = re.sub(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?\S*", "[local-endpoint-hidden]", text)
    text = re.sub(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S+", r"\1=[hidden]", text)
    return _truncate(text, limit)


def _safe_capability_list(value, limit: int = 24, item_limit: int = 80) -> list:
    out = []
    seen = set()
    for item in _string_list(value):
        safe = _safe_capability_text(item, item_limit)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        out.append(safe)
        if len(out) >= limit:
            break
    return out


def _budget_mode_names(mode_table: dict | None) -> list:
    names = set(WEB_SEARCH_VALID_BUDGET_MODES)
    if isinstance(mode_table, dict):
        names.update(
            str(name).strip()
            for name in mode_table.keys()
            if isinstance(name, str) and name.strip()
        )
    return sorted(names)


def _effective_absolute_caps(absolute_caps: dict | None) -> dict:
    ac = absolute_caps or {}
    return {
        "max_results": min(
            _resolve_budget_int(ac.get("results"), WEB_SEARCH_ABSOLUTE_MAX_RESULTS, 1),
            WEB_SEARCH_ABSOLUTE_MAX_RESULTS,
        ),
        "max_searches_per_turn": min(
            _resolve_budget_int(ac.get("searches"), WEB_SEARCH_ABSOLUTE_MAX_SEARCHES, 1),
            WEB_SEARCH_ABSOLUTE_MAX_SEARCHES,
        ),
        "max_page_opens_per_turn": min(
            _resolve_budget_int(ac.get("opens"), WEB_SEARCH_ABSOLUTE_MAX_OPENS, 1),
            WEB_SEARCH_ABSOLUTE_MAX_OPENS,
        ),
        "max_retrievals_per_turn": min(
            _resolve_budget_int(ac.get("retrievals"), WEB_SEARCH_ABSOLUTE_MAX_RETRIEVALS, 1),
            WEB_SEARCH_ABSOLUTE_MAX_RETRIEVALS,
        ),
        "max_retrieved_chars": min(
            _resolve_budget_int(ac.get("retrieved_chars"), WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS, 1),
            WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS,
        ),
    }


def _safe_config_path(path: str) -> str | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    normalized = raw.replace("\\", "/")
    for marker in ("config/default/search.json", "config/user/search.json"):
        if normalized.endswith(marker):
            return marker
    name = Path(normalized).name
    return name if name == "search.json" else None


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


def _resolve_budget_mode(
    requested_mode: str,
    mode_table: dict | None,
    flat_budgets: dict | None,
    absolute_caps: dict | None,
    default_mode: str = WEB_SEARCH_DEFAULT_BUDGET_MODE,
) -> dict:
    """Resolve effective per-call budget from mode, config table, flat overrides, and absolute caps.

    Precedence (highest → lowest):
      1. Built-in ABSOLUTE_* constants (never exceeded)
      2. absolute_caps from routing.absolute_max_* (operator may lower only)
      3. Mode budget from mode_table[effective_mode]
      4. flat_budgets (#60 compat — used when mode_table is falsy and mode is absent)
      5. Built-in WEB_SEARCH_MODE_DEFAULTS[effective_mode]

    Returns dict with: budget_mode, max_results, max_searches_per_turn,
    max_page_opens_per_turn, max_retrievals_per_turn, max_retrieved_chars.
    """
    fb = flat_budgets or {}
    mt = mode_table or {}
    ac = absolute_caps or {}

    # Resolve absolute caps (operator may only lower the built-in constant)
    abs_results  = min(_resolve_budget_int(ac.get("results"),          WEB_SEARCH_ABSOLUTE_MAX_RESULTS,          1), WEB_SEARCH_ABSOLUTE_MAX_RESULTS)
    abs_searches = min(_resolve_budget_int(ac.get("searches"),         WEB_SEARCH_ABSOLUTE_MAX_SEARCHES,         1), WEB_SEARCH_ABSOLUTE_MAX_SEARCHES)
    abs_opens    = min(_resolve_budget_int(ac.get("opens"),            WEB_SEARCH_ABSOLUTE_MAX_OPENS,            1), WEB_SEARCH_ABSOLUTE_MAX_OPENS)
    abs_retrievals = min(_resolve_budget_int(ac.get("retrievals"),     WEB_SEARCH_ABSOLUTE_MAX_RETRIEVALS,       1), WEB_SEARCH_ABSOLUTE_MAX_RETRIEVALS)
    abs_chars    = min(_resolve_budget_int(ac.get("retrieved_chars"),  WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS,  1), WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS)

    # Determine effective mode name. Built-in modes remain valid fallbacks, and
    # search.json may add local modes without requiring a code/schema change.
    valid_modes = set(WEB_SEARCH_VALID_BUDGET_MODES)
    if isinstance(mt, dict):
        valid_modes.update(
            str(name).strip()
            for name in mt.keys()
            if isinstance(name, str) and name.strip()
        )
    mode = requested_mode if requested_mode in valid_modes else ""
    fallback_mode = default_mode if default_mode in valid_modes else WEB_SEARCH_DEFAULT_BUDGET_MODE
    effective_mode = mode or fallback_mode

    # Determine base budget values
    if mt:
        # Named mode table present: use it (with built-in default as fallback per field)
        mode_base = WEB_SEARCH_MODE_DEFAULTS.get(effective_mode) or WEB_SEARCH_MODE_DEFAULTS[WEB_SEARCH_DEFAULT_BUDGET_MODE]
        mt_entry = mt.get(effective_mode) or {}
        results    = _resolve_budget_int(mt_entry.get("max_results"),           mode_base["max_results"],           1)
        searches   = _resolve_budget_int(mt_entry.get("max_searches_per_turn"), mode_base["max_searches_per_turn"], 1)
        opens      = _resolve_budget_int(mt_entry.get("max_page_opens_per_turn"), mode_base["max_page_opens_per_turn"], 1)
        retrievals = _resolve_budget_int(mt_entry.get("max_retrievals_per_turn"), mode_base["max_retrievals_per_turn"], 1)
        chars      = _resolve_budget_int(mt_entry.get("max_retrieved_chars"),   mode_base["max_retrieved_chars"],   1)
    elif not mode and any(v is not None for v in fb.values()):
        # No mode_table and no explicit budget_mode: use flat #60 compat fields.
        # The outer any() guarantees at least one flat value is not None, so the
        # "all None" sub-case is impossible here and handled by the else branch below.
        quick_base = WEB_SEARCH_MODE_DEFAULTS["quick"]
        results    = _resolve_budget_int(fb.get("max_results"),             quick_base["max_results"],             1)
        searches   = _resolve_budget_int(fb.get("max_searches_per_turn"),   quick_base["max_searches_per_turn"],   1)
        opens      = _resolve_budget_int(fb.get("max_page_opens_per_turn"), quick_base["max_page_opens_per_turn"], 1)
        retrievals = _resolve_budget_int(fb.get("max_retrievals_per_turn"), quick_base["max_retrievals_per_turn"], 1)
        chars      = _resolve_budget_int(fb.get("max_retrieved_chars"),     quick_base["max_retrieved_chars"],     1)
    else:
        # No mode_table, explicit mode requested: use built-in mode defaults
        mode_base = WEB_SEARCH_MODE_DEFAULTS.get(effective_mode) or WEB_SEARCH_MODE_DEFAULTS[WEB_SEARCH_DEFAULT_BUDGET_MODE]
        results    = mode_base["max_results"]
        searches   = mode_base["max_searches_per_turn"]
        opens      = mode_base["max_page_opens_per_turn"]
        retrievals = mode_base["max_retrievals_per_turn"]
        chars      = mode_base["max_retrieved_chars"]

    return {
        "budget_mode":              effective_mode,
        "max_results":              min(results,    abs_results),
        "max_searches_per_turn":    min(searches,   abs_searches),
        "max_page_opens_per_turn":  min(opens,      abs_opens),
        "max_retrievals_per_turn":  min(retrievals, abs_retrievals),
        "max_retrieved_chars":      min(chars,      abs_chars),
    }


_PROFILE_DESCRIPTION_FALLBACKS = {
    "auto": "Runtime-selected profile; inspect selected_profile in search output.",
    "broad": "General web search.",
    "coding": "Code, documentation, package, repository, and technical Q&A search.",
    "sysadmin": "Linux, networking, services, manpages, and distro docs search.",
    "research": "Higher quality general and academic research search.",
    "news": "Current news and recent event search.",
    "ai_models": "Model, dataset, Hugging Face and Ollama discovery.",
    "reference": "Definitions, dictionaries, and reference texts.",
    "character_cards": "Character card metadata. Agent API retrieval available.",
    "furry": "Furry community: prose/story discovery (FSE) and image metadata (e926, furbooru). SFW-biased.",
    "furry_fse": "Furry prose/story discovery via FSE only. Agent API retrieval available.",
    "furry_images": "Furry image metadata (e926, furbooru). SFW-biased.",
    "gaming_wikis": "Gaming and mod wiki pages. Agent API retrieval available.",
    "archives": "File/torrent archives and retro/old-web sources.",
}


def _profile_source_kinds(name: str, cfg: dict) -> list:
    text = " ".join([
        name or "",
        " ".join(_string_list(cfg.get("engines"))),
        " ".join(_string_list(cfg.get("categories"))),
        str(cfg.get("description") or ""),
    ]).lower()
    kinds = []
    checks = [
        ("coding", ["official_docs", "source_repo", "package_registry", "q_and_a"]),
        ("sysadmin", ["official_docs", "q_and_a", "package_registry"]),
        ("research", ["academic"]),
        ("news", ["news"]),
        ("reference", ["encyclopedia"]),
        ("character", ["character_card"]),
        ("furry", ["prose_archive", "furry_community"]),
        ("fse", ["prose_archive"]),
        ("furbooru", ["furry_community"]),
        ("e926", ["furry_community"]),
        ("gaming", ["gaming_wiki"]),
        ("wiki", ["wiki"]),
        ("archive", ["local_index", "unknown"]),
        ("torrent", ["local_index"]),
        ("bitmagnet", ["local_index"]),
    ]
    for needle, values in checks:
        if needle in text:
            kinds.extend(values)
    if not kinds:
        kinds.append("unknown")
    out = []
    for item in kinds:
        if item not in out:
            out.append(item)
    return out[:8]


def _profile_retrieval_expected(cfg: dict) -> bool:
    if not isinstance(cfg, dict):
        return False
    for key in ("retrieval_expected", "retrieval_available", "retrieve", "agent_api_retrieval"):
        if key in cfg:
            return bool(cfg.get(key))
    text = " ".join([
        str(cfg.get("description") or ""),
        " ".join(_string_list(cfg.get("engines"))),
    ]).lower()
    return any(term in text for term in (
        "retrieval available", "agent api", "taverncard", "aicharactercards",
        "fse", "furbooru", "pcgamingwiki", "alliedmodders", "bitmagnet",
    ))


def _profile_source_strict(
    profile: str,
    cfg_source_strict: bool,
    explicit_engines: list,
    guidance_source_strict: bool = False,
) -> bool:
    """True when search must stay within expected sources — no fallback, no broadening.

    Priority order:
    1. Local config source_strict=true
    2. Provider guidance source_strict=true
    3. profile=="furry_fse" — deprecated compat fallback, kept until guidance always present
    4. All explicit_engines are "fse" — deprecated compat fallback
    """
    if cfg_source_strict:
        return True
    if guidance_source_strict:
        return True
    # Deprecated compatibility fallbacks. Remove when provider guidance is always present.
    if profile == "furry_fse":
        return True
    if explicit_engines and all(e.strip().lower() == "fse" for e in explicit_engines):
        return True
    return False


def _result_matches_expected_source(
    result: dict,
    expected_engines: list,
    expected_domains: list,
    expected_retrieval_sources: list,
) -> bool:
    """True when result came from an expected source in source-strict mode.

    Accepts when any of:
    - result.engine or result.engines includes an expected engine name
    - result.url contains an expected domain
    - result.retrieval_source matches an expected retrieval source
    """
    if not isinstance(result, dict):
        return False

    # Engine fields
    engine = str(result.get("engine") or "").strip().lower()
    engines_raw = result.get("engines") or []
    all_engines: set = {engine} | {str(e).lower() for e in engines_raw if e} - {""}

    if expected_engines:
        exp_lower = {e.strip().lower() for e in expected_engines if e}
        if all_engines & exp_lower:
            return True

    # URL domain
    url = str(result.get("url") or "").lower()
    for domain in (expected_domains or []):
        if domain.strip().lower() in url:
            return True

    # retrieval_source annotation (added by _annotate_source)
    rs = str(result.get("retrieval_source") or "").strip().lower()
    if rs and expected_retrieval_sources:
        if rs in {s.strip().lower() for s in expected_retrieval_sources if s}:
            return True

    return False


def _collect_retrieval_sources(runtime) -> list:
    sources = set(_RETRIEVAL_SOURCE_KIND.keys())
    sources.add("mediawiki")
    caches = [
        getattr(runtime, "web_search_cache", None),
        getattr(runtime, "opened_page_cache", None),
    ]
    for cache in caches:
        if not isinstance(cache, dict):
            continue
        for item in cache.values():
            value = item.get("value") if isinstance(item, dict) else item
            results = value.get("results") if isinstance(value, dict) else None
            for result in results or []:
                if isinstance(result, dict) and result.get("retrieval_source"):
                    sources.add(str(result.get("retrieval_source")))
    for cfg in (getattr(runtime, "search_config_profiles", None) or {}).values():
        if not isinstance(cfg, dict):
            continue
        for key in ("retrieval_source", "retrieval_sources", "supported_retrieval_sources"):
            value = cfg.get(key)
            if isinstance(value, str) and value.strip():
                sources.add(value.strip())
            elif isinstance(value, list):
                sources.update(_string_list(value))
    return sorted(s for s in sources if s)


def build_web_search_capabilities(runtime) -> dict:
    """Return a bounded model-readable summary of the live web_search runtime."""
    warnings = []
    profiles = {}

    # Fetch provider guidance (cached, short timeout, failure = empty dict).
    guidance: dict = {}
    if hasattr(runtime, "_fetch_provider_guidance_cached"):
        try:
            guidance = runtime._fetch_provider_guidance_cached() or {}
        except Exception:
            pass
    guidance_warnings = list(getattr(runtime, "_provider_guidance_warnings", []))
    guidance_profiles: dict = (guidance.get("profiles") or {}) if guidance else {}
    guidance_available = bool(guidance)

    config_profiles = getattr(runtime, "search_config_profiles", None) or {}
    if not isinstance(config_profiles, dict):
        config_profiles = {}
    legacy_profiles = ((getattr(runtime, "searxng_policy", None) or {}).get("web_search_profiles") or {})
    if not isinstance(legacy_profiles, dict):
        legacy_profiles = {}

    profile_names = []
    for source in (config_profiles, legacy_profiles):
        for name, cfg in source.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(cfg, dict) and cfg.get("enabled") is False:
                continue
            if name not in profile_names:
                profile_names.append(name)

    # Probe/allowlist data for per-profile engine availability.
    _probe_engines: frozenset = getattr(runtime, "_allowed_engines_cache", frozenset())
    _blocked_general: frozenset = getattr(runtime, "_blocked_engines_general", frozenset())
    _engine_availability_known: bool = bool(_probe_engines)

    for name in sorted(profile_names):
        cfg = config_profiles.get(name)
        if not isinstance(cfg, dict):
            cfg = legacy_profiles.get(name) if isinstance(legacy_profiles.get(name), dict) else {}
        categories = _safe_capability_list(cfg.get("categories"), limit=16)
        engines = _safe_capability_list(cfg.get("engines"), limit=24)
        raw_engines = _string_list(cfg.get("engines"))
        description = _safe_capability_text(
            cfg.get("description") or cfg.get("purpose") or _PROFILE_DESCRIPTION_FALLBACKS.get(name, ""),
            220,
        )
        # Compute effective/availability fields for this profile.
        blocked_here = [e for e in raw_engines if e in _blocked_general]
        if _engine_availability_known:
            unavailable_here = [e for e in raw_engines
                                 if e not in _probe_engines and e not in blocked_here]
            effective_here = [e for e in raw_engines
                              if e not in _blocked_general and e in _probe_engines]
        else:
            unavailable_here = []
            effective_here = raw_engines  # unknown; assume all configured
        effective_count = len(effective_here)
        # retrieval_kind: explicit config field for image-metadata-only profiles.
        retrieval_kind = str(cfg.get("retrieval_kind") or "")
        # source_strict: from local config, provider guidance, or deprecated compat fallback.
        _guidance_strict_flag = bool((guidance_profiles.get(name) or {}).get("source_strict"))
        source_strict_flag = bool(cfg.get("source_strict")) or name == "furry_fse" or _guidance_strict_flag
        fallback_profiles_cap = (
            []
            if source_strict_flag
            else _safe_capability_list(cfg.get("fallback_profiles"), limit=8)
        )
        profile_entry: dict = {
            "description": description,
            "categories": categories,
            "engines": engines,
            "engine_count": len(raw_engines),
            "retrieval_expected": _profile_retrieval_expected(cfg),
            "intended_use": _safe_capability_text(cfg.get("intended_use") or description, 180),
            "source_kinds_expected": _profile_source_kinds(name, cfg),
            "engine_availability_known": _engine_availability_known,
            "effective_engine_count": effective_count,
            "source_strict": source_strict_flag,
            "fallback_profiles": fallback_profiles_cap,
        }
        if retrieval_kind:
            profile_entry["retrieval_kind"] = retrieval_kind
        if blocked_here:
            profile_entry["blocked_engines"] = _safe_capability_list(blocked_here, limit=16)
        if _engine_availability_known and unavailable_here:
            profile_entry["probe_unavailable_engines"] = _safe_capability_list(
                unavailable_here, limit=16
            )
        if _engine_availability_known and effective_count == 0 and raw_engines:
            warnings.append(
                f"Profile '{name}' has no available engines after policy/probe filtering."
            )
        # Source-strict profiles get a targeted warning when required engines are missing.
        if source_strict_flag and _engine_availability_known and raw_engines:
            strict_missing = [e for e in raw_engines if e not in _probe_engines and e not in blocked_here]
            if strict_missing:
                warnings.append(
                    f"Profile '{name}' is source-strict but required engine(s) "
                    f"{strict_missing!r} are not available in local SearXNG probe. "
                    "Searches will return zero results."
                )
        # Attach compact provider guidance for this profile, if available.
        _pg = guidance_profiles.get(name) or {}
        if _pg and isinstance(_pg, dict):
            _guidance_compact: dict = {}
            for _gf in ("purpose", "use_when", "do_not_use_for", "source_strict",
                        "supports_pagination", "max_pages_cap", "retrieval_guidance"):
                _gv = _pg.get(_gf)
                if _gv is None:
                    continue
                if isinstance(_gv, str):
                    _guidance_compact[_gf] = _safe_capability_text(_gv, 220)
                elif isinstance(_gv, bool):
                    _guidance_compact[_gf] = _gv
            _hard_rules = _pg.get("hard_rules")
            if isinstance(_hard_rules, list):
                _guidance_compact["hard_rules"] = _safe_capability_list(_hard_rules, limit=8, item_limit=160)
            if _guidance_compact:
                profile_entry["provider_guidance"] = _guidance_compact
            # Provider preference from guidance only — not hard-coded.
            _pref = _pg.get("provider_preference")
            if isinstance(_pref, list) and _pref:
                profile_entry["provider_preference"] = _safe_capability_list(_pref, limit=4, item_limit=40)
        profiles[name] = profile_entry

    # Global probe-availability warning.
    if not _engine_availability_known:
        warnings.append(
            "Engine availability has not been probed; "
            "configured engines may not exist in local SearXNG."
        )

    budget_modes = {}
    for mode in _budget_mode_names(getattr(runtime, "_budget_mode_table", None)):
        budget = _resolve_budget_mode(
            mode,
            getattr(runtime, "_budget_mode_table", {}),
            getattr(runtime, "_flat_budgets", {}),
            getattr(runtime, "_absolute_caps", {}),
            getattr(runtime, "_default_budget_mode", WEB_SEARCH_DEFAULT_BUDGET_MODE),
        )
        budget_modes[mode] = {
            "max_results": budget["max_results"],
            "max_searches_per_turn": budget["max_searches_per_turn"],
            "max_page_opens_per_turn": budget["max_page_opens_per_turn"],
            "max_retrievals_per_turn": budget["max_retrievals_per_turn"],
            "max_retrieved_chars": budget["max_retrieved_chars"],
        }

    absolute_caps = _effective_absolute_caps(getattr(runtime, "_absolute_caps", {}))
    supported_sources = _collect_retrieval_sources(runtime)
    config_warnings = [
        _safe_capability_text(w, 220)
        for w in getattr(runtime, "search_config_warnings", [])
        if _safe_capability_text(w, 220)
    ]
    warnings.extend(config_warnings[:8])

    default_budget = _resolve_budget_mode(
        "",
        getattr(runtime, "_budget_mode_table", {}),
        getattr(runtime, "_flat_budgets", {}),
        getattr(runtime, "_absolute_caps", {}),
        getattr(runtime, "_default_budget_mode", WEB_SEARCH_DEFAULT_BUDGET_MODE),
    )
    base_url_configured = bool(str(getattr(runtime, "searxng_base_url", "") or "").strip())
    config_source = _safe_capability_text(getattr(runtime, "search_config_source", "") or "runtime", 80)
    config_path = _safe_config_path(getattr(runtime, "search_config_path", ""))

    providers_info: dict = {
        "searchengines_agent": {
            "available": base_url_configured,
            "provider_id": (guidance.get("provider_id") or "searchengines-private") if guidance_available else "searchengines-private",
            "guidance_available": guidance_available,
            "probe_status": "probed" if _engine_availability_known else "not_probed",
            "note": "Agent API facade for search, guidance, and retrieval (searchengines-private).",
        },
        "agent_retrieve": {
            "available": base_url_configured,
            "provider_id": "agent_retrieve",
            "note": "Agent API retrieval for selected search results (action=retrieve).",
        },
    }
    # Merge provider-specific entries from guidance (provider owns these names).
    if guidance:
        for _pid, _pdata in (guidance.get("providers") or {}).items():
            if isinstance(_pdata, dict) and isinstance(_pid, str) and _pid.strip() and _pid not in providers_info:
                providers_info[_pid] = {
                    k: (_safe_capability_text(v, 120) if isinstance(v, str) else v)
                    for k, v in _pdata.items()
                    if k in ("available", "provider_id", "note", "reason")
                }

    if not base_url_configured:
        warnings.append("SearXNG provider is not available: no base URL configured.")

    return {
        "ok": True,
        "schema": "qz.web_search.capabilities.v1",
        "generated_at": _now_ts(),
        "default_profile": getattr(runtime, "default_search_profile", "") or "auto",
        "default_budget_mode": default_budget["budget_mode"],
        "supported_actions": ["search", "open_page", "find_in_page", "retrieve", "capabilities"],
        "profiles": profiles,
        "budget_modes": budget_modes,
        "absolute_caps": absolute_caps,
        "retrieval": {
            "available": base_url_configured,
            "action": "retrieve",
            "max_retrievals_by_budget_mode": {
                mode: data["max_retrievals_per_turn"] for mode, data in budget_modes.items()
            },
            "max_chars_by_budget_mode": {
                mode: data["max_retrieved_chars"] for mode, data in budget_modes.items()
            },
            "supported_sources": supported_sources,
            "notes": [
                "Use retrieve only for selected results that advertise retrieval_available=true.",
                "The raw retrieval endpoint is hidden from model-visible output.",
            ],
        },
        "annotations": {
            "source_kind_values": sorted(
                {kind for kind, _trust in _DOMAIN_SOURCE_KIND.values()}
                | {kind for kind, _trust in _RETRIEVAL_SOURCE_KIND.values()}
                | {"wiki", "news", "unknown"}
            ),
            "trust_hint_values": ["high", "medium", "low", "unknown"],
            "freshness_hint_values": ["recent", "dated", "unknown"],
            "fields_added_to_search_results": [
                "source_kind",
                "trust_hint",
                "freshness_hint",
                "retrieval_available",
                "retrieval_source",
                "retrieval_retriever",
            ],
        },
        "providers": providers_info,
        "provider_guidance": {
            "available": guidance_available,
            "provider_id": _safe_capability_text((guidance.get("provider_id") or ""), 60) if guidance_available else None,
            "schema": _safe_capability_text((guidance.get("schema") or ""), 60) if guidance_available else None,
            "profiles_present": sorted((guidance.get("profiles") or {}).keys()) if guidance_available else [],
            "warnings": [_safe_capability_text(w, 180) for w in (guidance.get("warnings") or [])[:4]],
            "fetch_warnings": [_safe_capability_text(w, 180) for w in guidance_warnings[:4]],
        },
        "agent_api": {
            "base_url_configured": base_url_configured,
            "status": "configured_not_probed" if base_url_configured else "not_configured",
            "retrieval_metadata_supported": True,
            "endpoint_hidden_from_model": True,
        },
        "config": {
            "config_source": config_source,
            "config_path": config_path,
            "loaded_profiles_count": len(profiles),
            "loaded_budget_modes_count": len(budget_modes),
            "default_budget_mode": default_budget["budget_mode"],
            "stale_or_missing": {
                "missing_config": config_source in ("", "empty"),
                "warnings_present": bool(config_warnings),
            },
            "warnings": config_warnings[:8],
        },
        "usage_notes": [
            "Choose profile explicitly.",
            "Choose budget_mode explicitly.",
            "Use capabilities before unfamiliar research tasks.",
            "There is no automatic keyword routing.",
            "retrieval_endpoint is never exposed.",
            "Retrieve content selectively; do not dump large retrieved text.",
            "Use engines=[...] to narrow a profile to specific configured engines.",
            "Invalid or probe-unavailable explicit engines may produce no results.",
            "Source-strict profiles enforce exact engine matching; they never fall back to other engines.",
            "Use action=capabilities to inspect provider_guidance for profile-specific usage instructions.",
        ],
        "warnings": warnings[:8],
    }


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
        # Flat per-session budget params — #60 compat; used when budget_modes absent.
        max_searches_per_turn=None,
        max_page_opens_per_turn=None,
        max_results_per_query=None,
        low_result_fallback_threshold=None,
        max_retrievals_per_turn=None,
        max_retrieved_chars=None,
        # Budget mode table and absolute caps from search.json routing.
        budget_mode_table=None,
        absolute_caps=None,
        default_budget_mode=None,
        config_source: str = "",
        config_path: str = "",
        config_warnings=None,
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

        # Store data for per-call _resolve_budget_mode.
        self._budget_mode_table = budget_mode_table if isinstance(budget_mode_table, dict) else {}
        self._absolute_caps = absolute_caps if isinstance(absolute_caps, dict) else {}
        self._default_budget_mode = str(default_budget_mode or WEB_SEARCH_DEFAULT_BUDGET_MODE).strip()
        self.search_config_source = str(config_source or "").strip()
        self.search_config_path = str(config_path or "").strip()
        self.search_config_warnings = list(config_warnings or []) if isinstance(config_warnings, list) else []
        # Flat #60 compat fields stored for fallback use in _resolve_budget_mode.
        self._flat_budgets = {
            "max_results":             max_results_per_query,
            "max_searches_per_turn":   max_searches_per_turn,
            "max_page_opens_per_turn": max_page_opens_per_turn,
            "max_retrievals_per_turn": max_retrievals_per_turn,
            "max_retrieved_chars":     max_retrieved_chars,
        }

        # Resolve the default budget (no mode argument) for backward-compat attributes.
        _default_budget = _resolve_budget_mode(
            "", self._budget_mode_table, self._flat_budgets,
            self._absolute_caps, self._default_budget_mode,
        )
        self.max_searches_per_turn   = _default_budget["max_searches_per_turn"]
        self.max_page_opens_per_turn = _default_budget["max_page_opens_per_turn"]
        self.max_results_per_query   = _default_budget["max_results"]
        self.max_retrievals_per_turn = _default_budget["max_retrievals_per_turn"]
        self.max_retrieved_chars     = _default_budget["max_retrieved_chars"]

        self.retrieval_cache: dict = {}

        # Provider guidance cache — fetched lazily from Agent API /guidance endpoint.
        self._provider_guidance: dict = {}
        self._provider_guidance_fetched_at: float = 0.0
        self._provider_guidance_warnings: list = []
        self._provider_guidance_cache_ttl: float = 120.0

        # Pre-build immutable per-instance caches — inputs are set once at __init__.
        _policy_profiles = (self.searxng_policy or {}).get("web_search_profiles") or {}
        _all_profiles = set(VALID_WEB_SEARCH_PROFILES)
        if isinstance(_policy_profiles, dict):
            _all_profiles.update(n for n in _policy_profiles if isinstance(n, str) and n.strip())
        if isinstance(self.search_config_profiles, dict):
            _all_profiles.update(n for n in self.search_config_profiles if isinstance(n, str) and n.strip())
        self._valid_profiles_cache: frozenset = frozenset(_all_profiles)

        _caps = self.searxng_capabilities or {}
        _ok_engines: set = set()
        for _n, _m in (_caps.get("engine_probe") or {}).items():
            if isinstance(_m, dict) and _m.get("status") == "ok":
                _ok_engines.add(_n)
        if not _ok_engines:
            for _item in (_caps.get("recommended_for_coding_agent") or []):
                if isinstance(_item, dict) and _item.get("name"):
                    _ok_engines.add(_item["name"])
        self._allowed_engines_cache: frozenset = frozenset(_ok_engines)

        _blocked_general: set = set(_string_list(self.searxng_policy.get("disabled_even_if_configured")))
        _blocked_general.update(_string_list(self.searxng_policy.get("non_text_engines_disabled_for_current_web_search_tool")))
        _blocked_general.update(_string_list(self.searxng_policy.get("quarantine_until_fixed")))
        self._blocked_engines_general: frozenset = frozenset(_blocked_general)
        self._blocked_engines_coding: frozenset = frozenset(
            _blocked_general | set(_string_list(self.searxng_policy.get("never_for_coding_agent")))
        )

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

    def _fetch_provider_guidance_cached(self) -> dict:
        """Fetch /guidance from the Agent API. Returns {} on failure; failures are warnings.

        Caches for _provider_guidance_cache_ttl seconds. The /guidance endpoint is
        optional — its absence is normal and not an error. Provider guidance is generic:
        any Agent API may expose it; searchengines-private is one example.
        """
        now = _now_float()
        if self._provider_guidance_fetched_at > 0 and (now - self._provider_guidance_fetched_at) < self._provider_guidance_cache_ttl:
            return self._provider_guidance
        if not self.searxng_base_url:
            self._provider_guidance_fetched_at = now
            return self._provider_guidance
        url = self.searxng_base_url.rstrip("/") + "/guidance"
        try:
            raw, _ctype, _final = _http_fetch(url, min(float(self.searxng_timeout), 5.0), "application/json")
            guidance = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(guidance, dict):
                guidance = {}
            self._provider_guidance = guidance
            self._provider_guidance_warnings = []
        except Exception as exc:
            self._provider_guidance = {}
            self._provider_guidance_warnings = [f"Provider guidance unavailable: {type(exc).__name__}"]
        self._provider_guidance_fetched_at = now
        return self._provider_guidance

    def _get_guidance_source_strict(self, profile: str) -> bool:
        """Return source_strict from cached provider guidance (no network fetch)."""
        guidance = self._provider_guidance
        if not isinstance(guidance, dict) or not guidance:
            return False
        pg = (guidance.get("profiles") or {}).get(profile) or {}
        return bool(pg.get("source_strict"))

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
        return self._allowed_engines_cache

    def _valid_profiles(self):
        return self._valid_profiles_cache

    def _is_valid_profile(self, profile: str):
        return profile in self._valid_profiles_cache

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
        if profile == "coding":
            return self._blocked_engines_coding
        return self._blocked_engines_general

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

        # Source-strict fields — consumed by _search_web to enforce no-fallback + filtering.
        source_strict_cfg = bool(cfg.get("source_strict"))
        expected_engines_cfg = _string_list(cfg.get("expected_engines"))
        expected_domains_cfg = _string_list(cfg.get("expected_domains"))
        expected_retrieval_sources_cfg = _string_list(cfg.get("expected_retrieval_sources"))

        return {
            "requested_profile": requested_profile,
            "profile": actual_profile,
            "categories": categories,
            "engines": self._filter_engines(engines, actual_profile),
            "fallback_profiles": fallback_profiles,
            "source_strict": source_strict_cfg,
            "expected_engines": expected_engines_cfg,
            "expected_domains": expected_domains_cfg,
            "expected_retrieval_sources": expected_retrieval_sources_cfg,
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
            return {
                "error": "SearXNG is not configured.", "results": [],
                "provider_id": "searxng", "provider_reported_count": None,
                "parsed_result_count": 0, "count_mismatch": False, "warnings": [],
            }

        categories = [c for c in (categories or []) if isinstance(c, str) and c.strip()]
        engines = [e for e in (engines or []) if isinstance(e, str) and e.strip()]
        cache_key = json.dumps({
            "q": query,
            "categories": categories,
            "engines": engines,
            "top_k": top_k,
        }, sort_keys=True)
        cached = self._cache_get(self.web_search_cache, cache_key, WEB_SEARCH_SEARCH_CACHE_TTL)
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
            result = {
                "error": str(e), "results": [],
                "provider_id": "searxng", "provider_reported_count": None,
                "parsed_result_count": 0, "count_mismatch": False, "warnings": [],
            }
            self._cache_put(self.web_search_cache, cache_key, result)
            return result

        # Extract provider-reported result count (SearXNG metadata — diagnostic only).
        # Never use this for routing/fallback decisions; always use len(parsed results).
        provider_reported_count = None
        for _count_field in ("number_of_results", "result_count", "numberOfResults"):
            _raw_count = payload.get(_count_field)
            if _raw_count is not None:
                try:
                    provider_reported_count = int(_raw_count)
                    break
                except (TypeError, ValueError):
                    pass

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
            if len(results) >= max(1, int(top_k or self.max_results_per_query)):
                break

        parsed_result_count = len(results)
        # Mismatch: SearXNG says 0 but we parsed real results — SearXNG metadata bug.
        count_mismatch = (provider_reported_count == 0 and parsed_result_count > 0)
        provider_warnings: list = []
        if count_mismatch:
            provider_warnings.append(
                f"searxng_result_count_mismatch: provider reported 0 results "
                f"but {parsed_result_count} result(s) were parsed; "
                "using parsed count for routing (SearXNG metadata bug)"
            )

        result = {
            "query": query,
            "results": results,
            "categories": categories,
            "engines": engines,
            "unresponsive_engines": payload.get("unresponsive_engines") or [],
            "answers": payload.get("answers") or [],
            "provider_id": "searxng",
            "provider_reported_count": provider_reported_count,
            "parsed_result_count": parsed_result_count,
            "count_mismatch": count_mismatch,
            "warnings": provider_warnings,
        }

        runtime_log("latest-web-search-provider-raw-summary.json", {
            "provider_id": "searxng",
            "query": query,
            "requested_engines": engines,
            "requested_categories": categories,
            "provider_reported_count": provider_reported_count,
            "parsed_result_count": parsed_result_count,
            "count_mismatch": count_mismatch,
            "unresponsive_engines": payload.get("unresponsive_engines") or [],
            "warnings": provider_warnings,
        })

        self._cache_put(self.web_search_cache, cache_key, result)
        return result

    def _search_web(self, query: str, profile="auto", categories=None, engines=None, top_k: int = WEB_SEARCH_MAX_RESULTS):
        route = self._profile_config(profile, query)
        explicit_categories = _string_list(categories)
        explicit_engines = _string_list(engines)
        primary_categories = explicit_categories or route["categories"]
        primary_engines = self._filter_engines(explicit_engines, route["profile"]) if explicit_engines else route["engines"]
        query_categories = [] if route["profile"] in ("ai_models", "broad") and primary_engines else primary_categories

        # Source-strict enforcement: config flag, provider guidance, or deprecated fallbacks.
        source_strict = _profile_source_strict(
            route["profile"],
            bool(route.get("source_strict")),
            explicit_engines,
            guidance_source_strict=self._get_guidance_source_strict(route["profile"]),
        )
        if source_strict:
            # Never fall back — silently broadening would return unrelated results.
            route["fallback_profiles"] = []
        expected_engines_strict = route.get("expected_engines") or []
        expected_domains_strict = route.get("expected_domains") or []
        expected_retrieval_sources_strict = route.get("expected_retrieval_sources") or []

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
            "source_strict": source_strict,
        })

        runtime_log("latest-web-search-request.json", {
            "action": "search",
            "query": query,
            "requested_profile": route["requested_profile"],
            "selected_profile": route["profile"],
            "provider_id": "searxng",
            "requested_engines_before_filter": _string_list(explicit_engines) if explicit_engines else _string_list(route.get("engines") or []),
            "requested_engines_after_filter": primary_engines,
            "categories_before": primary_categories,
            "categories_after": query_categories,
            "source_strict": source_strict,
            "fallback_profiles": route["fallback_profiles"],
        })

        result = self._query_searxng(query, query_categories, primary_engines, top_k=top_k)

        # Source-strict result validation: discard results not from expected sources.
        # If SearXNG falls back to other engines (e.g. DuckDuckGo instead of fse),
        # we catch and discard those wrong-source results here.
        wrong_source_discarded = 0
        if source_strict and (expected_engines_strict or expected_domains_strict or expected_retrieval_sources_strict):
            orig_results = list(result.get("results") or [])
            filtered = [
                r for r in orig_results
                if _result_matches_expected_source(
                    r, expected_engines_strict, expected_domains_strict, expected_retrieval_sources_strict
                )
            ]
            wrong_source_discarded = len(orig_results) - len(filtered)
            result["results"] = filtered
            if wrong_source_discarded > 0 and not filtered:
                source_strict_warning_text = (
                    f"profile {route['profile']!r} is source-strict; "
                    f"SearXNG returned {wrong_source_discarded} non-source result(s), "
                    "likely engine fallback or misconfiguration. Returning zero results."
                )
                result["source_strict_warning"] = source_strict_warning_text
                result.setdefault("warnings", []).append(source_strict_warning_text)

        accepted_result_count = len(result.get("results") or [])
        provider_warnings = list(result.get("warnings") or [])

        runtime_log("latest-web-search-normalized.json", {
            "query": query,
            "selected_profile": route["profile"],
            "provider_id": result.get("provider_id", "searxng"),
            "provider_reported_count": result.get("provider_reported_count"),
            "parsed_result_count": result.get("parsed_result_count"),
            "accepted_result_count": accepted_result_count,
            "wrong_source_results_discarded": wrong_source_discarded,
            "source_strict": source_strict,
            "count_mismatch": result.get("count_mismatch", False),
            "result_urls": [r.get("url", "") for r in (result.get("results") or [])[:10]],
            "warnings": provider_warnings,
        })

        result.update({
            "requested_profile": route["requested_profile"],
            "profile": route["profile"],
            "search_policy": self.search_policy_selection,
            "fallback_used": None,
            "fallback_profiles": route["fallback_profiles"],
            "categories": primary_categories,
            "engines": primary_engines,
            "query_categories": query_categories,
            "accepted_result_count": accepted_result_count,
        })
        result["warnings"] = provider_warnings

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
            "provider_id": result.get("provider_id", "searxng"),
            "provider_reported_count": result.get("provider_reported_count"),
            "parsed_result_count": result.get("parsed_result_count"),
            "accepted_result_count": accepted_result_count,
            "count_mismatch": result.get("count_mismatch", False),
            "result_count": accepted_result_count,
            "threshold": threshold,
            "explicit_categories": bool(explicit_categories),
            "explicit_engines": bool(explicit_engines),
            "source_strict": source_strict,
            "expected_engines": expected_engines_strict,
            "expected_domains": expected_domains_strict,
            "expected_retrieval_sources": expected_retrieval_sources_strict,
            "wrong_source_results_discarded": wrong_source_discarded,
            "fallback_suppressed_reason": "source_strict" if source_strict else None,
            "warnings": provider_warnings,
        }

        # Explicit engine/category calls and source-strict profiles skip fallback.
        if explicit_categories or explicit_engines or source_strict or accepted_result_count >= threshold:
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
        # budget_mode: preserve supported built-in or config-defined names; resolution
        # happens in execute_web_search_call.
        raw_mode = str(data.get("budget_mode") or "").strip()
        budget_mode = raw_mode if raw_mode in set(_budget_mode_names(self._budget_mode_table)) else ""
        # top_k: parse raw int if provided; keep None if absent so execute_web_search_call
        # can substitute the per-call effective max_results.
        _raw_top_k = data.get("top_k")
        try:
            top_k = max(1, int(_raw_top_k)) if _raw_top_k is not None else None
        except Exception:
            top_k = None
        retrieval_source = str(data.get("retrieval_source") or "").strip()
        return {
            "action": action,
            "query": query,
            "profile": profile,
            "budget_mode": budget_mode,
            "url": url,
            "page_id": page_id,
            "categories": categories,
            "engines": engines,
            "top_k": top_k,
            "retrieval_source": retrieval_source,
        }

    def _normalize_retrieve_response(self, raw: dict, url: str, max_chars: int | None = None) -> dict:
        """Normalize Agent API /retrieve response into unified output.

        Handles two upstream shapes:
          - Normalized format (mediawiki, FSE): has status/summary/fields/agent_api
          - Character-card format: flat top-level with name/description/creator/tags/agent_api
        Never includes retrieval.endpoint or localhost URLs.
        """
        if not isinstance(raw, dict):
            return {"ok": False, "error": "invalid_response"}

        _max_chars = max_chars if (isinstance(max_chars, int) and max_chars > 0) else self.max_retrieved_chars

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
            truncated = bool(fields.get("body_text_truncated")) or (len(body) > _max_chars)
            content = body[:_max_chars] if truncated else body
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
            truncated = len(assembled) > _max_chars
            content = assembled[:_max_chars] if truncated else assembled
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

    def _retrieve(self, url: str, retrieval_source: str = "", max_chars: int | None = None) -> dict:
        """Call Agent API /retrieve and return normalized result.

        max_chars overrides self.max_retrieved_chars for this call (used for per-call
        budget mode resolution). Never exposes the raw /retrieve endpoint URL.
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

        effective_max_chars = max_chars if (isinstance(max_chars, int) and max_chars > 0) else self.max_retrieved_chars
        params: dict = {"url": canonical, "max_chars": effective_max_chars}
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

        result = self._normalize_retrieve_response(raw, canonical, max_chars=max_chars)
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

        if action == "capabilities":
            self._emit("web_search_capabilities_requested", {
                "request_id": request_id or call_item.get("request_id") or "",
                "call_id": call_item.get("call_id") or call_item.get("id"),
            })
            try:
                payload = build_web_search_capabilities(self)
                self._emit("web_search_capabilities_completed", {
                    "request_id": request_id or call_item.get("request_id") or "",
                    "call_id": call_item.get("call_id") or call_item.get("id"),
                    "profiles": len(payload.get("profiles") or {}),
                    "budget_modes": len(payload.get("budget_modes") or {}),
                })
                error = None
            except Exception as exc:
                payload = {}
                error = f"Capabilities failed: {type(exc).__name__}: {exc}"
                self._emit("web_search_capabilities_failed", {
                    "request_id": request_id or call_item.get("request_id") or "",
                    "call_id": call_item.get("call_id") or call_item.get("id"),
                    "error": error,
                })

            result_payload = {
                "ok": error is None,
                "action": action,
                "result": payload if error is None else {},
                "error": error,
            }
            web_call_item = {
                "id": call_item.get("id") or call_item.get("call_id") or f"wsc_local_{_now_ts()}",
                "type": "web_search_call",
                "status": "completed" if error is None else "failed",
                "call_id": call_item.get("call_id"),
                "action": {"type": action},
            }
            if error:
                web_call_item["error"] = error
            tool_output_item = {
                "type": "function_call_output",
                "call_id": call_item.get("call_id") or call_item.get("id") or f"fc_local_{_now_ts()}",
                "output": json.dumps(result_payload, ensure_ascii=False),
            }
            return web_call_item, tool_output_item, []

        # Resolve per-call effective budget from budget_mode argument.
        budget = _resolve_budget_mode(
            args.get("budget_mode") or "",
            self._budget_mode_table,
            self._flat_budgets,
            self._absolute_caps,
            self._default_budget_mode,
        )
        effective_mode           = budget["budget_mode"]
        eff_max_searches         = budget["max_searches_per_turn"]
        eff_max_opens            = budget["max_page_opens_per_turn"]
        eff_max_results          = budget["max_results"]
        eff_max_retrievals       = budget["max_retrievals_per_turn"]
        eff_max_retrieved_chars  = budget["max_retrieved_chars"]

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
            "budget_mode": effective_mode,
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
            if counters["search"] >= eff_max_searches:
                error = f"Refusing search: reached per-turn limit of {eff_max_searches} search calls."
                self._emit("web_search_budget_exceeded", {
                    "action": "search",
                    "budget_mode": effective_mode,
                    "limit": eff_max_searches,
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
                effective_top_k = min(max(1, args.get("top_k") or eff_max_results), eff_max_results)
                payload = self._search_web(
                    query=query.strip(),
                    profile=profile,
                    categories=args.get("categories"),
                    engines=args.get("engines"),
                    top_k=effective_top_k,
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
            if counters["open_page"] >= eff_max_opens:
                error = f"Refusing open_page: reached per-turn limit of {eff_max_opens} page opens."
                self._emit("web_search_budget_exceeded", {
                    "action": "open_page",
                    "budget_mode": effective_mode,
                    "limit": eff_max_opens,
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
            elif counters.get("retrieve", 0) >= eff_max_retrievals:
                error = f"Refusing retrieve: reached per-turn limit of {eff_max_retrievals} retrieve calls."
                self._emit("web_search_retrieve_budget_exceeded", {
                    "url": str(url),
                    "budget_mode": effective_mode,
                    "limit": eff_max_retrievals,
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
                payload = self._retrieve(str(url).strip(), retrieval_source, eff_max_retrieved_chars)
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
            "budget_mode": effective_mode,
            "call_id": call_item.get("call_id") or call_item.get("id"),
            "status": "failed" if error else "completed",
            "error": error,
            "result_count": len(sources) if action == "search" else len(sources),
            "duration_ms": round((_now_float() - started_at) * 1000.0, 2),
        })

        return web_call_item, tool_output_item, _unique_sources(sources)
