#!/usr/bin/env python3
import argparse
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from html import unescape as html_unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        MODEL_BUDGETS,
        api_contract_payload,
    )
    from .qz_responses import (
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _build_local_compaction_response,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _decode_local_compaction_blob,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _normalize_ws,
        _now_ts,
        _parse_apply_patch_arguments,
        _truncate,
        clean_content,
        extract_response_output_text,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
        recursive_clean,
    )
    from .qz_sse import (
        _normalize_response_usage,
        make_response_stream_events,
        make_sse_block,
        transform_sse_event,
    )
    from .qz_telemetry import DEFAULT_TELEMETRY
    from .qz_runtime_io import append_capture, capture_path, runtime_log, write_capture
except ImportError:
    from qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        MODEL_BUDGETS,
        api_contract_payload,
    )
    from qz_responses import (
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _build_local_compaction_response,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _decode_local_compaction_blob,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _normalize_ws,
        _now_ts,
        _parse_apply_patch_arguments,
        _truncate,
        clean_content,
        extract_response_output_text,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
        recursive_clean,
    )
    from qz_sse import (
        _normalize_response_usage,
        make_response_stream_events,
        make_sse_block,
        transform_sse_event,
    )
    from qz_telemetry import DEFAULT_TELEMETRY
    from qz_runtime_io import append_capture, capture_path, runtime_log, write_capture

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




class ProxyHandler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:18084"
    reasoning_stream_format = "raw"
    searxng_base_url = None
    searxng_timeout = 15.0
    searxng_policy_path = None
    searxng_capabilities_path = None
    searxng_policy = {}
    searxng_capabilities = {}
    web_search_cache = {}
    opened_page_cache = {}
    active_deprecation = None
    telemetry = DEFAULT_TELEMETRY

    def log_message(self, fmt, *args):
        return

    def _send_json(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._send_codex_rate_limit_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_telemetry_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_codex_rate_limit_headers()
        self.end_headers()

        with self.telemetry.subscribe() as events:
            try:
                for event in self.telemetry.recent(50):
                    self.wfile.write(make_sse_block(event["type"], event))
                    self.wfile.flush()

                while True:
                    try:
                        event = events.get(timeout=30)
                    except Exception:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self.wfile.write(make_sse_block(event["type"], event))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def _telemetry_sse_payload(self, event_type, payload):
        if not isinstance(payload, dict):
            return None
        compact = dict(payload)
        response = compact.get("response")
        if isinstance(response, dict):
            compact["response"] = {
                key: response.get(key)
                for key in ("id", "model", "status", "created_at")
                if response.get(key) is not None
            }
        item = compact.get("item")
        if isinstance(item, dict):
            compact["item"] = {
                key: item.get(key)
                for key in ("id", "type", "status", "role", "call_id", "name")
                if item.get(key) is not None
            }
        return {
            "event_type": event_type,
            "payload": compact,
        }

    def _emit_sse_telemetry(self, chunk):
        if not chunk:
            return
        event_name = ""
        data_lines = []
        for raw_line in chunk.splitlines():
            try:
                line = raw_line.decode("utf-8", errors="replace")
            except AttributeError:
                line = str(raw_line)
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())

        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except Exception:
            return

        event_type = event_name or payload.get("type") or "event"
        if event_type not in {
            "response.created",
            "response.in_progress",
            "response.completed",
            "response.output_item.added",
            "response.output_item.done",
            "response.reasoning_text.delta",
            "response.reasoning_text.done",
            "response.reasoning_summary_text.delta",
            "response.reasoning_summary_text.done",
            "response.output_text.delta",
            "response.output_text.done",
            "response.function_call_arguments.delta",
        }:
            return

        compact = self._telemetry_sse_payload(event_type, payload)
        if compact is not None:
            self.telemetry.emit("sse_event", compact)


    def _send_codex_rate_limit_headers(self):
        primary = LOCAL_CODEX_RATE_LIMITS["primary"]
        secondary = LOCAL_CODEX_RATE_LIMITS["secondary"]
        credits = LOCAL_CODEX_RATE_LIMITS["credits"]

        self.send_header("x-codex-limit-id", LOCAL_CODEX_RATE_LIMITS["limit_id"])
        self.send_header("x-codex-limit-name", LOCAL_CODEX_RATE_LIMITS["limit_name"])
        self.send_header("x-codex-plan-type", LOCAL_CODEX_RATE_LIMITS["plan_type"])
        self.send_header("x-codex-primary-used-percent", str(primary["used_percent"]))
        self.send_header("x-codex-primary-window-minutes", str(primary["window_minutes"]))
        self.send_header("x-codex-primary-resets-in-seconds", str(primary["resets_in_seconds"]))
        self.send_header("x-codex-primary-resets-at", str(primary["resets_at"]))
        self.send_header("x-codex-secondary-used-percent", str(secondary["used_percent"]))
        self.send_header("x-codex-secondary-window-minutes", str(secondary["window_minutes"]))
        self.send_header("x-codex-secondary-resets-in-seconds", str(secondary["resets_in_seconds"]))
        self.send_header("x-codex-secondary-resets-at", str(secondary["resets_at"]))
        self.send_header("x-codex-credits-has-credits", "true" if credits["has_credits"] else "false")
        self.send_header("x-codex-credits-unlimited", "true" if credits["unlimited"] else "false")
        self._send_deprecation_headers()

    def _send_deprecation_headers(self):
        info = self.active_deprecation
        if not isinstance(info, dict):
            return
        reason = info.get("reason") or "Endpoint is deprecated."
        replacement = info.get("replacement") or "/v1/responses"
        self.send_header("Deprecation", "true")
        self.send_header("X-QuantZhai-Deprecated", "true")
        self.send_header("X-QuantZhai-Replacement", replacement)
        self.send_header("Warning", f'299 QuantZhai "{reason}"')

    def _mark_deprecated_endpoint(self, path: str):
        self.active_deprecation = LEGACY_API_ENDPOINTS.get(path)
        if not self.active_deprecation:
            return
        self.telemetry.emit("deprecated_endpoint", {
            "path": path,
            "replacement": self.active_deprecation.get("replacement"),
            "removal": self.active_deprecation.get("removal"),
        })
        try:
            import time
            append_capture(
                "latest-deprecated-api.log",
                f"{time.time():.3f} {path} replacement={self.active_deprecation.get('replacement')} removal={self.active_deprecation.get('removal')}\n",
            )
        except Exception:
            pass

    def _codex_rate_limits_payload(self):
        payload = {
            "type": "codex.rate_limits",
            "rate_limits": LOCAL_CODEX_RATE_LIMITS,
            "metered_limit_name": "local",
        }
        payload.update(LOCAL_CODEX_RATE_LIMITS)
        return payload

    def _write_codex_rate_limits_event(self):
        self.wfile.write(make_sse_block("codex.rate_limits", self._codex_rate_limits_payload()))
        self.wfile.flush()


    def _handle_responses_compact(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        try:
            write_capture("latest-compact-request.json", body)
        except Exception:
            pass

        out = _build_local_compaction_response(body)

        try:
            import pathlib
            cmp_item = next((item for item in out.get("output", []) if isinstance(item, dict) and item.get("type") == "compaction"), None)
            payload = _decode_local_compaction_blob(cmp_item.get("encrypted_content", "")) if cmp_item else None
            if payload:
                capture_path("latest-compact-summary.txt").write_text(
                    payload.get("summary_text", ""),
                    encoding="utf-8",
                )
        except Exception:
            pass

        self._send_json(200, out)


    def _write_transformed_sse_stream(self, resp, raw_log=None):
        summary_started = set()
        event_lines = []

        while True:
            chunk = resp.readline()
            if not chunk:
                if event_lines:
                    for out_chunk in transform_sse_event(event_lines, summary_started, self.reasoning_stream_format):
                        self._emit_sse_telemetry(out_chunk)
                        self.wfile.write(out_chunk)
                        self.wfile.flush()
                break

            if raw_log is not None:
                raw_log.write(chunk)
                raw_log.flush()

            event_lines.append(chunk)
            if chunk in (b"\n", b"\r\n"):
                for out_chunk in transform_sse_event(event_lines, summary_started, self.reasoning_stream_format):
                    self._emit_sse_telemetry(out_chunk)
                    self.wfile.write(out_chunk)
                    self.wfile.flush()
                event_lines = []


    def _ollama_models(self):
        now = "2026-04-27T00:00:00Z"
        models = []
        for name in MODEL_BUDGETS:
            models.append({
                "name": name,
                "model": name,
                "modified_at": now,
                "size": 1,
                "digest": "local-qwen36turbo",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "35B-A3B",
                    "quantization_level": "Q4_K_M+TurboQuant"
                }
            })
        return models

    def _handle_ollama_get(self):
        # Codex --oss may probe Ollama-compatible endpoints before using /v1.
        if self.path in ("/api/tags", "/v1/api/tags"):
            self._send_json(200, {"models": self._ollama_models()})
            return True

        if self.path in ("/api/version", "/v1/api/version"):
            self._send_json(200, {"version": "0.13.4"})
            return True

        if self.path in ("/api/ps", "/v1/api/ps"):
            self._send_json(200, {"models": self._ollama_models()})
            return True

        return False

    def _handle_ollama_post(self):
        if self.path not in ("/api/pull", "/v1/api/pull", "/api/show", "/v1/api/show"):
            return False

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

        model = body.get("model") or body.get("name") or "Qwen3.6Turbo-medium"

        if self.path in ("/api/pull", "/v1/api/pull"):
            # Pretend the model is already installed.
            # Ollama permits non-stream response {"status":"success"} when stream=false;
            # Codex only needs pull to not fail.
            self._send_json(200, {"status": "success"})
            return True

        if self.path in ("/api/show", "/v1/api/show"):
            self._send_json(200, {
                "modelfile": f"FROM {model}\n",
                "parameters": "",
                "template": "",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "35B-A3B",
                    "quantization_level": "Q4_K_M+TurboQuant"
                },
                "model_info": {
                    "general.architecture": "qwen",
                    "general.name": model,
                    "qwen36turbo.context_length": 131072
                },
                "capabilities": ["completion", "tools", "thinking"]
            })
            return True

        return False


    def _log_request_path(self, method):
        if self.path.startswith("/qz/telemetry"):
            return
        self.telemetry.emit("request_started", {
            "method": method,
            "path": self.path,
            "accept": self.headers.get("Accept", ""),
            "content_type": self.headers.get("Content-Type", ""),
        })
        try:
            import time
            append_capture("latest-paths.log", f"{time.time():.3f} {method} {self.path} accept={self.headers.get('Accept','')} content_type={self.headers.get('Content-Type','')}\n")
        except Exception:
            pass

    def do_GET(self):
        self.active_deprecation = None
        self._log_request_path("GET")
        if self._handle_ollama_get():
            return

        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "proxy": "Qwen3.6Turbo",
                "upstream": self.upstream,
                "models": MODEL_BUDGETS,
                "supports": list(CURRENT_API_ENDPOINTS),
                "api_contract": api_contract_payload(),
            })
            return

        if self.path == "/qz/telemetry/state":
            self._send_json(200, self.telemetry.state())
            return

        if self.path.startswith("/qz/telemetry/recent"):
            limit = 100
            try:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                limit = int((params.get("limit") or [limit])[0])
            except Exception:
                limit = 100
            self._send_json(200, {
                "events": self.telemetry.recent(limit),
                "state": self.telemetry.state(),
            })
            return

        if self.path == "/qz/telemetry/events":
            self._send_telemetry_sse()
            return

        if self.path == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [
                    {"id": name, "object": "model", "owned_by": "local"}
                    for name in MODEL_BUDGETS
                ],
            })
            return

        self._proxy_raw("GET")

    def do_POST(self):
        self.active_deprecation = None
        self._log_request_path("POST")

        if self._handle_ollama_post():
            return

        if self.path in LEGACY_API_ENDPOINTS:
            self._mark_deprecated_endpoint(self.path)
            self._proxy_json_api("/v1/chat/completions")
            return

        if self.path in ("/responses/compact", "/v1/responses/compact"):
            self._handle_responses_compact()
            return

        if self.path in ("/responses", "/v1/responses"):
            self._proxy_json_api("/v1/responses")
            return

        self._proxy_raw("POST")

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

    def _execute_web_search_call(self, call_item: dict, counters: dict, seen_signatures: set):
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

        return web_call_item, tool_output_item, _unique_sources(sources)

    def _annotate_output_with_url_citations(self, out: dict, sources):
        unique_sources = _unique_sources(sources)[:4]
        if not unique_sources:
            return out

        output_items = out.get("output") or []
        for item in reversed(output_items):
            if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
                continue
            content = item.get("content") or []
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text") or ""
                annotations = list(part.get("annotations") or [])
                for idx, source in enumerate(unique_sources, start=1):
                    marker = f" [{idx}]"
                    start_index = len(text)
                    text += marker
                    end_index = len(text) - 1
                    annotations.append({
                        "type": "url_citation",
                        "start_index": start_index,
                        "end_index": end_index,
                        "title": source.get("title") or source.get("url"),
                        "url": source.get("url"),
                    })
                part["text"] = text
                part["annotations"] = annotations
                return out
        return out

    def _call_upstream_json(self, url: str, body: dict):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.headers.get("Authorization", "Bearer local"),
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=900) as resp:
            resp_data = resp.read()
            status = resp.status
            content_type = resp.headers.get("Content-Type", "application/json")
        return status, content_type, resp_data

    def _run_responses_locally(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        url = self.upstream + "/v1/responses"
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = False

        public_trace = []
        gathered_sources = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()

        for _hop in range(WEB_SEARCH_MAX_HOPS):
            status, content_type, resp_data = self._call_upstream_json(url, working_body)
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = requested_model

            output_items = out.get("output") or []
            web_calls = [
                item for item in output_items
                if isinstance(item, dict) and item.get("type") == "function_call" and item.get("name") == "web_search"
            ]

            if not web_calls:
                final_out = dict(out)
                final_out["output"] = public_trace + normalize_apply_patch_output_for_codex(
                    output_items,
                    apply_patch_output_style,
                )
                final_out["usage"] = _normalize_response_usage(final_out.get("usage"))
                self._annotate_output_with_url_citations(final_out, gathered_sources)
                runtime_log("latest-web-runtime-final.json", final_out)
                return status, content_type, final_out

            next_input = list(working_body.get("input") or [])
            next_input.extend(output_items)

            for call in web_calls:
                public_item, tool_output_item, sources = self._execute_web_search_call(call, counters, seen_signatures)
                public_trace.append(public_item)
                gathered_sources.extend(sources)
                next_input.append(tool_output_item)

            working_body["input"] = next_input

        fallback_out = {
            "id": f"resp_local_{_now_ts()}",
            "object": "response",
            "created_at": _now_ts(),
            "model": requested_model,
            "output": public_trace + [{
                "id": f"msg_local_{_now_ts()}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "I stopped the web tool loop after hitting the safety limit for repeated search/open actions.",
                    "annotations": [],
                }],
            }],
            "usage": _normalize_response_usage({}),
        }
        self._annotate_output_with_url_citations(fallback_out, gathered_sources)
        runtime_log("latest-web-runtime-final.json", fallback_out)
        return 200, "application/json", fallback_out



    def _proxy_json_api(self, upstream_path):
        try:
            import time
            append_capture("latest-json-api.log", f"{time.time():.3f} ENTER path={self.path} upstream_path={upstream_path} accept={self.headers.get('Accept','')}\n")
        except Exception:
            pass

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        # Debug: dump latest request so we can see Codex tool schema.
        try:
            write_capture("latest-request.json", body)
        except Exception:
            pass

        client_wants_stream = (
            body.get("stream") is True
            or "text/event-stream" in self.headers.get("Accept", "")
        )

        requested_model = body.get("model") or "Qwen3.6Turbo-medium"
        budget = MODEL_BUDGETS.get(requested_model, MODEL_BUDGETS["Qwen3.6Turbo"])

        body["model"] = requested_model
        body["thinking_budget_tokens"] = budget
        body.setdefault("temperature", 0.1)

        if upstream_path == "/v1/responses":
            apply_patch_output_style = _apply_patch_output_style(body)
            input_items = body.get("input")
            if isinstance(input_items, list):
                body["input"] = _microcompact_old_tool_results(_expand_local_compaction_items(input_items))
            body = normalize_responses_input_for_qwen(body)
            body = normalize_tools_for_llamacpp(body)
            try:
                write_capture("latest-normalized-request.json", body)
            except Exception:
                pass

            try:
                status, content_type, out = self._run_responses_locally(
                    body,
                    requested_model,
                    apply_patch_output_style,
                )
            except urllib.error.HTTPError as e:
                resp_data = e.read()
                try:
                    self._send_json(e.code, json.loads(resp_data.decode("utf-8")))
                except Exception:
                    self.send_response(e.code)
                    self.send_header("Content-Type", "text/plain")
                    self._send_codex_rate_limit_headers()
                    self.end_headers()
                    self.wfile.write(resp_data)
                return
            except Exception as e:
                try:
                    import traceback
                    runtime_log("latest-web-runtime-error.txt", traceback.format_exc())
                except Exception:
                    pass
                self._send_json(502, {"error": f"local web runtime error: {e}"})
                return

            if client_wants_stream:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self._write_codex_rate_limits_event()
                sse_log = None
                try:
                    sse_log = capture_path("latest-synthetic-sse.raw").open("wb")
                    for chunk in make_response_stream_events(out):
                        sse_log.write(chunk)
                        sse_log.flush()
                        self._emit_sse_telemetry(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                finally:
                    if sse_log is not None:
                        sse_log.close()
                return

            self._send_json(status, out)
            return

        data = json.dumps(body).encode("utf-8")
        url = self.upstream + upstream_path

        try:
            import time
            append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM url={url} bytes={len(data)} stream={body.get('stream')}\n")
        except Exception:
            pass

        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.headers.get("Authorization", "Bearer local"),
                "Accept": self.headers.get("Accept", "application/json"),
            },
        )

        try:
            resp = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e:
            resp_data = e.read()
            try:
                self._send_json(e.code, json.loads(resp_data.decode("utf-8")))
            except Exception:
                self.send_response(e.code)
                self.send_header("Content-Type", "text/plain")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self.wfile.write(resp_data)
            return
        except Exception as e:
            try:
                import time, traceback
                append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM_EXCEPTION {type(e).__name__}: {e}\n")
                append_capture("latest-json-api.log", traceback.format_exc() + "\n")
            except Exception:
                pass
            self._send_json(502, {"error": f"upstream error: {e}"})
            return

        content_type = resp.headers.get("Content-Type", "application/json")
        status = resp.status

        if upstream_path == "/v1/responses" and client_wants_stream and "text/event-stream" in content_type:
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._send_codex_rate_limit_headers()
            self.end_headers()
            self._write_codex_rate_limits_event()

            try:
                raw_log_path = capture_path("latest-upstream-response.raw")
                status_path = capture_path("latest-upstream-status.txt")
                status_path.write_text(
                    f"status={status}\ncontent_type={content_type}\nstream=passthrough\nreasoning_stream_format={self.reasoning_stream_format}\nrate_limits=local\n",
                    encoding="utf-8"
                )
                raw_log = raw_log_path.open("wb")
            except Exception:
                raw_log = None

            try:
                self._write_transformed_sse_stream(resp, raw_log)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if raw_log is not None:
                    raw_log.close()
                resp.close()
            self.close_connection = True
            return

        resp_data = resp.read()
        resp.close()

        try:
            write_capture("latest-upstream-response.raw", resp_data, mode="bytes")
            capture_path("latest-upstream-status.txt").write_text(
                f"status={status}\ncontent_type={content_type}\n",
                encoding="utf-8"
            )
        except Exception:
            pass

        try:
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = requested_model
            if upstream_path == "/v1/responses":
                out["usage"] = _normalize_response_usage(out.get("usage"))
            # Do not recursively clean response text here.
            # Reasoning format conversion belongs in the SSE transform layer.
            # Final output_text must pass through unchanged.

            if upstream_path == "/v1/responses" and client_wants_stream:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self._write_codex_rate_limits_event()
                for chunk in make_response_stream_events(out):
                    self._emit_sse_telemetry(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return

            self._send_json(status, out)
        except Exception:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self._send_codex_rate_limit_headers()
            self.send_header("Content-Length", str(len(resp_data)))
            self.end_headers()
            self.wfile.write(resp_data)

    def _proxy_raw(self, method):
        length = int(self.headers.get("Content-Length", "0") or "0")
        data = self.rfile.read(length) if length else None
        url = self.upstream + self.path

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": self.headers.get("Content-Type", "application/json"),
                "Authorization": self.headers.get("Authorization", "Bearer local"),
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                resp_data = resp.read()
                status = resp.status
                content_type = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            resp_data = e.read()
            status = e.code
            content_type = e.headers.get("Content-Type", "application/json")
        except Exception as e:
            self._send_json(502, {"error": f"upstream error: {e}"})
            return

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._send_codex_rate_limit_headers()
        self.send_header("Content-Length", str(len(resp_data)))
        self.end_headers()
        self.wfile.write(resp_data)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18180)
    ap.add_argument("--upstream", default="http://127.0.0.1:18084")
    ap.add_argument("--reasoning-stream-format", choices=("raw", "summary", "hidden"), default="raw")
    ap.add_argument("--searxng-base-url", default=os.environ.get("SEARXNG_BASE_URL"))
    ap.add_argument("--searxng-timeout", type=float, default=float(os.environ.get("SEARXNG_TIMEOUT", "15")))
    ap.add_argument("--searxng-policy", default=os.environ.get("SEARXNG_POLICY"))
    ap.add_argument("--searxng-capabilities", default=os.environ.get("SEARXNG_CAPABILITIES"))
    args = ap.parse_args()

    ProxyHandler.upstream = args.upstream.rstrip("/")
    ProxyHandler.reasoning_stream_format = args.reasoning_stream_format

    script_dir = Path(__file__).resolve().parent
    policy_path = Path(args.searxng_policy) if args.searxng_policy else script_dir / "searxng-agent-policy.json"
    capabilities_path = Path(args.searxng_capabilities) if args.searxng_capabilities else script_dir / "searxng-capabilities.json"
    policy = _safe_json_file(policy_path)
    capabilities = _safe_json_file(capabilities_path)

    ProxyHandler.searxng_policy_path = str(policy_path)
    ProxyHandler.searxng_capabilities_path = str(capabilities_path)
    ProxyHandler.searxng_policy = policy
    ProxyHandler.searxng_capabilities = capabilities
    ProxyHandler.searxng_base_url = args.searxng_base_url or policy.get("searxng_base") or capabilities.get("base")
    ProxyHandler.searxng_timeout = args.searxng_timeout

    server = ThreadingHTTPServer((args.listen, args.port), ProxyHandler)
    print(
        f"Qwen3.6Turbo proxy listening on {args.listen}:{args.port} -> {ProxyHandler.upstream}, reasoning_stream_format={ProxyHandler.reasoning_stream_format}, searxng_base={ProxyHandler.searxng_base_url}",
        flush=True,
    )
    server.serve_forever()

if __name__ == "__main__":
    main()
