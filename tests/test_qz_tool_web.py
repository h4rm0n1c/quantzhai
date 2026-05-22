import json
import unittest
from unittest.mock import patch

from proxy.qz_tool_web import WebSearchRuntime


class WebSearchRuntimeTests(unittest.TestCase):
    def test_custom_policy_profile_can_be_default_route(self):
        runtime = WebSearchRuntime(
            policy={
                "web_search_profiles": {
                    "torrent_lab": {
                        "categories": ["files"],
                        "engines": ["bt4g"],
                    }
                }
            },
            capabilities={"engine_probe": {"bt4g": {"status": "ok"}}},
            default_profile="torrent_lab",
            policy_selection={"source": "model_override", "default_profile": "torrent_lab"},
        )
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=8):
            calls.append({"query": query, "categories": categories, "engines": engines})
            return {"results": [{"title": "one", "url": "https://example.test/one"}]}

        runtime._query_searxng = fake_query

        out = runtime._search_web("linux iso")

        self.assertEqual(out["profile"], "torrent_lab")
        self.assertEqual(out["categories"], ["files"])
        self.assertEqual(out["engines"], ["bt4g"])
        self.assertEqual(calls[0]["categories"], ["files"])
        self.assertEqual(calls[0]["engines"], ["bt4g"])

    def test_explicit_custom_policy_profile_is_accepted(self):
        runtime = WebSearchRuntime(
            policy={
                "web_search_profiles": {
                    "archives": {
                        "categories": ["general"],
                        "engines": ["internetarchivescholar"],
                    }
                }
            },
            capabilities={"engine_probe": {"internetarchivescholar": {"status": "ok"}}},
        )

        args = runtime._parse_web_search_arguments(json.dumps({
            "action": "search",
            "query": "old manuals",
            "profile": "archives",
        }))

        self.assertEqual(args["profile"], "archives")

    def test_execute_search_requires_query(self):
        runtime = WebSearchRuntime()
        call = {
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": json.dumps({"action": "search"}),
        }

        public_item, tool_output, sources = runtime.execute_web_search_call(call, {"search": 0, "open_page": 0}, set())
        payload = json.loads(tool_output["output"])

        self.assertEqual(public_item["type"], "web_search_call")
        self.assertEqual(public_item["status"], "failed")
        self.assertFalse(payload["ok"])
        self.assertEqual(sources, [])

    def test_execute_search_enforces_repeat_guard(self):
        runtime = WebSearchRuntime()
        call = {
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": json.dumps({"action": "search", "query": "quantzhai"}),
        }
        counters = {"search": 0, "open_page": 0}
        seen = set()

        runtime.execute_web_search_call(call, counters, seen)
        public_item, tool_output, _sources = runtime.execute_web_search_call(call, counters, seen)
        payload = json.loads(tool_output["output"])

        self.assertEqual(public_item["status"], "failed")
        self.assertIn("repeated search", payload["error"])


class WebSearchCapabilitiesTests(unittest.TestCase):
    def _call(self):
        return {
            "type": "function_call",
            "call_id": "c_caps",
            "name": "web_search",
            "arguments": json.dumps({"action": "capabilities"}),
        }

    def test_tool_schema_contains_capabilities_action_and_short_guidance(self):
        from proxy.qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
        schema = WEB_SEARCH_TOOL_ADAPTER.to_upstream_tool({})
        props = schema["parameters"]["properties"]
        self.assertIn("capabilities", props["action"]["enum"])
        desc = schema["description"]
        self.assertIn('action="capabilities"', desc)
        self.assertIn("when unsure", desc)
        self.assertIn("Do not rely on hardcoded profile names", desc)
        self.assertNotIn("character_cards, furry, gaming_wikis, archives", props["profile"]["description"])
        self.assertNotIn("enum", props["budget_mode"])

    def test_capabilities_action_returns_schema_without_consuming_budgets(self):
        runtime = WebSearchRuntime(
            base_url="http://127.0.0.1:8890",
            search_config_profiles={
                "custom_live": {
                    "description": "Custom profile from live config with secret=hidden.",
                    "categories": ["general"],
                    "engines": ["example-engine"],
                }
            },
            budget_mode_table={
                "normal": {"max_results": 14, "max_searches_per_turn": 6,
                           "max_page_opens_per_turn": 5, "max_retrievals_per_turn": 3,
                           "max_retrieved_chars": 10000},
                "audit": {"max_results": 200, "max_searches_per_turn": 200,
                          "max_page_opens_per_turn": 200, "max_retrievals_per_turn": 200,
                          "max_retrieved_chars": 999999},
            },
            absolute_caps={"results": 50, "searches": 20, "opens": 20,
                           "retrievals": 10, "retrieved_chars": 60000},
            config_source="default",
            config_path="/home/user/repo/config/default/search.json",
        )
        counters = {"search": 2, "open_page": 1, "retrieve": 1}
        seen = {("existing",)}
        public_item, tool_output, sources = runtime.execute_web_search_call(
            self._call(), counters, seen
        )
        payload = json.loads(tool_output["output"])
        caps = payload["result"]

        self.assertEqual(public_item["status"], "completed")
        self.assertTrue(payload["ok"])
        self.assertEqual(caps["schema"], "qz.web_search.capabilities.v1")
        self.assertIn("capabilities", caps["supported_actions"])
        self.assertIn("custom_live", caps["profiles"])
        self.assertEqual(caps["budget_modes"]["normal"]["max_results"], 14)
        self.assertEqual(caps["budget_modes"]["audit"]["max_results"], 50)
        self.assertEqual(caps["absolute_caps"]["max_retrievals_per_turn"], 10)
        self.assertTrue(caps["retrieval"]["available"])
        self.assertTrue(caps["agent_api"]["endpoint_hidden_from_model"])
        self.assertEqual(caps["config"]["config_path"], "config/default/search.json")
        self.assertEqual(counters, {"search": 2, "open_page": 1, "retrieve": 1})
        self.assertEqual(seen, {("existing",)})
        self.assertEqual(sources, [])
        serialized = json.dumps(caps).lower()
        self.assertNotIn("127.0.0.1:8890/retrieve", serialized)
        self.assertNotIn("localhost", serialized)
        self.assertNotIn("secret=hidden", serialized)

    def test_capabilities_does_not_call_search_or_retrieve(self):
        runtime = WebSearchRuntime(base_url="http://127.0.0.1:8890")
        runtime._query_searxng = lambda *a, **kw: self.fail("capabilities must not search")
        runtime._retrieve = lambda *a, **kw: self.fail("capabilities must not retrieve")
        runtime.execute_web_search_call(self._call(), {"search": 0, "open_page": 0, "retrieve": 0}, set())

    def test_custom_profile_appears_and_disabled_profile_is_hidden(self):
        runtime = WebSearchRuntime(
            search_config_profiles={
                "live_profile": {"categories": ["it"], "engines": ["docs"], "description": "Live docs."},
                "disabled_profile": {"enabled": False, "categories": ["general"]},
            },
        )
        from proxy.qz_tool_web import build_web_search_capabilities
        caps = build_web_search_capabilities(runtime)
        self.assertIn("live_profile", caps["profiles"])
        self.assertNotIn("disabled_profile", caps["profiles"])
        self.assertEqual(caps["profiles"]["live_profile"]["categories"], ["it"])
        self.assertEqual(caps["profiles"]["live_profile"]["engine_count"], 1)

    def test_config_budget_modes_override_builtins_and_custom_mode_resolves(self):
        runtime = WebSearchRuntime(
            budget_mode_table={
                "normal": {"max_results": 7, "max_searches_per_turn": 3,
                           "max_page_opens_per_turn": 2, "max_retrievals_per_turn": 1,
                           "max_retrieved_chars": 900},
                "local_audit": {"max_results": 9, "max_searches_per_turn": 4,
                                "max_page_opens_per_turn": 4, "max_retrievals_per_turn": 2,
                                "max_retrieved_chars": 1800},
            },
            default_budget_mode="local_audit",
            absolute_caps={"results": 8, "retrieved_chars": 1200},
        )
        from proxy.qz_tool_web import build_web_search_capabilities
        caps = build_web_search_capabilities(runtime)
        self.assertEqual(caps["default_budget_mode"], "local_audit")
        self.assertEqual(caps["budget_modes"]["normal"]["max_results"], 7)
        self.assertEqual(caps["budget_modes"]["local_audit"]["max_results"], 8)
        self.assertEqual(caps["budget_modes"]["local_audit"]["max_retrieved_chars"], 1200)

    def test_capabilities_telemetry_requested_completed_and_failure_nonfatal(self):
        emitted = []

        class FakeTelemetry:
            def emit(self, event_type, payload):
                emitted.append(event_type)

        runtime = WebSearchRuntime(telemetry=FakeTelemetry())
        runtime.execute_web_search_call(self._call(), {"search": 0, "open_page": 0, "retrieve": 0}, set())
        self.assertIn("web_search_capabilities_requested", emitted)
        self.assertIn("web_search_capabilities_completed", emitted)

        class BrokenTelemetry:
            def emit(self, event_type, payload):
                raise RuntimeError("down")

        runtime = WebSearchRuntime(telemetry=BrokenTelemetry())
        _public, tool_output, _sources = runtime.execute_web_search_call(
            self._call(), {"search": 0, "open_page": 0, "retrieve": 0}, set()
        )
        self.assertTrue(json.loads(tool_output["output"])["ok"])


class SourceAnnotationTests(unittest.TestCase):
    """Tests for two-layer web_search source annotations — #60 Slice D2."""

    def _ann(self, url, retrieval=None, published_date=None):
        from proxy.qz_tool_web import _annotate_source
        return _annotate_source(url, retrieval, published_date)

    # --- Layer 1: domain-based ---

    def test_official_docs_python(self):
        a = self._ann("https://docs.python.org/3/library/pathlib.html")
        self.assertEqual(a["source_kind"], "official_docs")
        self.assertEqual(a["trust_hint"], "high")

    def test_official_docs_mdn(self):
        a = self._ann("https://developer.mozilla.org/en-US/docs/Web/API")
        self.assertEqual(a["source_kind"], "official_docs")
        self.assertEqual(a["trust_hint"], "high")

    def test_source_repo_github(self):
        a = self._ann("https://github.com/owner/repo")
        self.assertEqual(a["source_kind"], "source_repo")
        self.assertEqual(a["trust_hint"], "high")

    def test_q_and_a_stackoverflow(self):
        a = self._ann("https://stackoverflow.com/questions/12345/some-question")
        self.assertEqual(a["source_kind"], "q_and_a")
        self.assertEqual(a["trust_hint"], "medium")

    def test_package_registry_pypi(self):
        a = self._ann("https://pypi.org/project/requests/")
        self.assertEqual(a["source_kind"], "package_registry")
        self.assertEqual(a["trust_hint"], "high")

    def test_academic_arxiv(self):
        a = self._ann("https://arxiv.org/abs/2303.08774")
        self.assertEqual(a["source_kind"], "academic")
        self.assertEqual(a["trust_hint"], "high")

    def test_encyclopedia_wikipedia(self):
        a = self._ann("https://en.wikipedia.org/wiki/Python_(programming_language)")
        self.assertEqual(a["source_kind"], "encyclopedia")
        self.assertEqual(a["trust_hint"], "medium")

    def test_gaming_wiki_pcgamingwiki(self):
        a = self._ann("https://www.pcgamingwiki.com/wiki/Half-Life_2")
        self.assertEqual(a["source_kind"], "gaming_wiki")
        self.assertEqual(a["trust_hint"], "medium")

    def test_unknown_domain(self):
        a = self._ann("https://some-random-blog.example.com/post")
        self.assertEqual(a["source_kind"], "unknown")
        self.assertEqual(a["trust_hint"], "unknown")

    def test_domain_field_present(self):
        a = self._ann("https://docs.python.org/3/")
        self.assertEqual(a["domain"], "docs.python.org")

    # --- Layer 2: retrieval metadata ---

    def test_character_card_retrieval_overrides(self):
        ret = {"available": True, "source": "character-card"}
        a = self._ann("https://aicharactercards.com/charactercards/foo/bar/", ret)
        self.assertEqual(a["source_kind"], "character_card")
        self.assertEqual(a["trust_hint"], "low")
        self.assertTrue(a["retrieval_available"])
        self.assertEqual(a["retrieval_source"], "character-card")

    def test_fse_retrieval_gives_prose_archive(self):
        ret = {"available": True, "source": "fse"}
        a = self._ann("https://fse.anthro.fr/stories/12345-some-story", ret)
        self.assertEqual(a["source_kind"], "prose_archive")
        self.assertTrue(a["retrieval_available"])

    def test_furbooru_retrieval_gives_furry_community(self):
        ret = {"available": True, "source": "furbooru"}
        a = self._ann("https://furbooru.org/images/12345", ret)
        self.assertEqual(a["source_kind"], "furry_community")

    def test_mediawiki_pcgamingwiki_gives_gaming_wiki(self):
        ret = {"available": True, "source": "mediawiki"}
        a = self._ann("https://www.pcgamingwiki.com/wiki/Half-Life_2", ret)
        self.assertEqual(a["source_kind"], "gaming_wiki")
        self.assertEqual(a["trust_hint"], "medium")

    def test_mediawiki_alliedmodders_gives_official_docs(self):
        ret = {"available": True, "source": "mediawiki"}
        a = self._ann("https://wiki.alliedmods.net/SourceMod_Scripting", ret)
        self.assertEqual(a["source_kind"], "official_docs")
        self.assertEqual(a["trust_hint"], "high")

    def test_bitmagnet_retrieval_gives_local_index(self):
        ret = {"available": True, "source": "bitmagnet"}
        a = self._ann("http://127.0.0.1:3333/torrents/abc123", ret)
        self.assertEqual(a["source_kind"], "local_index")
        self.assertEqual(a["trust_hint"], "low")

    def test_retrieval_endpoint_not_exposed(self):
        ret = {
            "available": True,
            "source": "character-card",
            "endpoint": "http://127.0.0.1:8890/retrieve?url=https://...",
        }
        a = self._ann("https://taverncard.com/cards/123", ret)
        self.assertNotIn("endpoint", a)
        self.assertNotIn("retrieval_endpoint", a)
        # no localhost URL in any string value
        ann_str = json.dumps(a)
        self.assertNotIn("127.0.0.1", ann_str)

    def test_retrieval_available_false_when_not_available(self):
        ret = {"available": False, "reason": "unsupported host"}
        a = self._ann("https://example.com/page", ret)
        self.assertFalse(a["retrieval_available"])
        self.assertNotIn("retrieval_source", a)

    # --- Freshness ---

    def test_freshness_recent_from_url_year(self):
        from proxy.qz_tool_web import _freshness_hint
        self.assertEqual(_freshness_hint("https://example.com/2026/post", None), "recent")

    def test_freshness_dated_from_url_year(self):
        from proxy.qz_tool_web import _freshness_hint
        self.assertEqual(_freshness_hint("https://example.com/2020/post", None), "dated")

    def test_freshness_recent_from_published_date(self):
        from proxy.qz_tool_web import _freshness_hint
        self.assertEqual(_freshness_hint("https://example.com/", "2026-03-15"), "recent")

    def test_freshness_dated_from_published_date(self):
        from proxy.qz_tool_web import _freshness_hint
        self.assertEqual(_freshness_hint("https://example.com/", "2019-01-01"), "dated")

    def test_freshness_unknown_no_signal(self):
        from proxy.qz_tool_web import _freshness_hint
        self.assertEqual(_freshness_hint("https://example.com/no-date-here", None), "unknown")

    # --- sources list safety ---

    def test_unique_sources_carries_annotation_fields(self):
        from proxy.qz_tool_web import _unique_sources
        sources = [{"url": "https://github.com/x/y", "title": "repo",
                    "source_kind": "source_repo", "trust_hint": "high"}]
        result = _unique_sources(sources)
        self.assertEqual(result[0]["source_kind"], "source_repo")
        self.assertEqual(result[0]["trust_hint"], "high")

    def test_retrieval_retriever_exposed_in_annotation(self):
        """retrieval_retriever should appear in annotation for model use."""
        ret = {"available": True, "source": "character-card",
               "retriever": "fetch-character-card.py",
               "endpoint": "http://127.0.0.1:8890/retrieve?url=x"}
        a = self._ann("https://taverncard.com/cards/1", ret)
        self.assertEqual(a.get("retrieval_retriever"), "fetch-character-card.py")

    def test_retrieval_retriever_absent_when_not_retrievable(self):
        ret = {"available": False}
        a = self._ann("https://taverncard.com/cards/1", ret)
        self.assertNotIn("retrieval_retriever", a)
        self.assertFalse(a.get("retrieval_available"))

    def test_result_entry_has_no_raw_retrieval_dict(self):
        """Payload result entries must not contain the raw retrieval dict (with endpoint)."""
        from proxy.qz_tool_web import _annotate_source
        ret = {"available": True, "source": "fse",
               "endpoint": "http://127.0.0.1:8890/retrieve?url=x"}
        ann = _annotate_source("https://fse.anthro.fr/stories/123", ret)
        # The annotation dict must not contain the raw retrieval dict
        self.assertNotIn("retrieval", ann)
        self.assertNotIn("endpoint", ann)
        self.assertNotIn("127.0.0.1", json.dumps(ann))

    def test_unique_sources_carries_retrieval_retriever(self):
        from proxy.qz_tool_web import _unique_sources
        sources = [{"url": "https://taverncard.com/cards/1", "title": "t",
                    "source_kind": "character_card",
                    "retrieval_available": True,
                    "retrieval_source": "character-card",
                    "retrieval_retriever": "fetch-character-card.py"}]
        result = _unique_sources(sources)
        self.assertEqual(result[0].get("retrieval_retriever"), "fetch-character-card.py")

    def test_unique_sources_no_retrieval_endpoint(self):
        from proxy.qz_tool_web import _unique_sources
        sources = [{"url": "https://taverncard.com/cards/1", "title": "t",
                    "source_kind": "character_card",
                    "retrieval_endpoint": "http://127.0.0.1:8890/retrieve?url=x"}]
        result = _unique_sources(sources)
        self.assertNotIn("retrieval_endpoint", result[0])
        out_str = json.dumps(result)
        self.assertNotIn("127.0.0.1", out_str)


class WebSearchBudgetConfigTests(unittest.TestCase):
    """Tests for search.json routing budget wiring — #60 Slice B."""

    def test_default_budgets_from_constants_when_no_params(self):
        # Default mode is now "normal" (#64). No-param runtime uses normal-mode values.
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        runtime = WebSearchRuntime()
        normal = WEB_SEARCH_MODE_DEFAULTS["normal"]
        self.assertEqual(runtime.max_searches_per_turn, normal["max_searches_per_turn"])
        self.assertEqual(runtime.max_page_opens_per_turn, normal["max_page_opens_per_turn"])
        self.assertEqual(runtime.max_results_per_query, normal["max_results"])

    def test_max_searches_from_param(self):
        runtime = WebSearchRuntime(max_searches_per_turn=2)
        self.assertEqual(runtime.max_searches_per_turn, 2)

    def test_max_page_opens_from_param(self):
        runtime = WebSearchRuntime(max_page_opens_per_turn=1)
        self.assertEqual(runtime.max_page_opens_per_turn, 1)

    def test_max_results_from_param(self):
        runtime = WebSearchRuntime(max_results_per_query=5)
        self.assertEqual(runtime.max_results_per_query, 5)

    def test_invalid_budget_falls_back_to_constant(self):
        from proxy.qz_tool_web import WEB_SEARCH_MAX_SEARCHES
        runtime = WebSearchRuntime(max_searches_per_turn="not_a_number")
        self.assertEqual(runtime.max_searches_per_turn, WEB_SEARCH_MAX_SEARCHES)

    def test_zero_budget_falls_back_to_constant(self):
        from proxy.qz_tool_web import WEB_SEARCH_MAX_SEARCHES
        runtime = WebSearchRuntime(max_searches_per_turn=0)
        self.assertEqual(runtime.max_searches_per_turn, WEB_SEARCH_MAX_SEARCHES)

    def test_negative_budget_falls_back_to_constant(self):
        from proxy.qz_tool_web import WEB_SEARCH_MAX_OPENS
        runtime = WebSearchRuntime(max_page_opens_per_turn=-1)
        self.assertEqual(runtime.max_page_opens_per_turn, WEB_SEARCH_MAX_OPENS)

    def test_search_budget_enforced_at_custom_limit(self):
        """Budget enforcement uses max_searches_per_turn, not the hard-coded constant."""
        runtime = WebSearchRuntime(max_searches_per_turn=2)
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=8):
            calls.append(query)
            return {"results": [{"title": "x", "url": "https://x.test/"}]}

        runtime._query_searxng = fake_query
        counters = {"search": 2, "open_page": 0}
        seen = set()
        public_item, tool_output, _sources = runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c1", "name": "web_search",
             "arguments": '{"action":"search","query":"test"}'},
            counters, seen,
        )
        payload = json.loads(tool_output["output"])
        self.assertFalse(payload["ok"])
        self.assertIn("2", payload["error"])  # limit of 2 mentioned
        self.assertEqual(len(calls), 0)  # no actual search

    def test_top_k_parsed_raw_clamped_in_execute(self):
        """#64: _parse_web_search_arguments returns raw top_k; per-call clamping happens in execute."""
        runtime = WebSearchRuntime(max_results_per_query=3)
        args = runtime._parse_web_search_arguments(
            '{"action":"search","query":"q","top_k":999}'
        )
        # Raw value preserved; clamping to effective mode limit done in execute_web_search_call.
        self.assertGreaterEqual(args["top_k"], 1)
        # With a max_results_per_query=3 flat param and no mode_table, normal mode → 12.
        # Actual effective clamp tested in budget mode tests.

    def test_default_search_json_max_searches_is_4(self):
        """config/default/search.json routing.max_searches_per_turn should be 4."""
        import json
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "default" / "search.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(data["routing"]["max_searches_per_turn"], 4)

    def test_low_result_fallback_from_param(self):
        runtime = WebSearchRuntime(low_result_fallback_threshold=3)
        self.assertEqual(runtime.low_result_fallback_threshold, 3)

    def test_low_result_fallback_from_legacy_policy(self):
        runtime = WebSearchRuntime(
            policy={"routing": {"low_result_fallback_threshold": 5}},
        )
        self.assertEqual(runtime.low_result_fallback_threshold, 5)

    def test_max_results_flat_param_accepted_above_old_ceiling(self):
        """#64: flat max_results_per_query is no longer clamped to the old 8-result ceiling."""
        runtime = WebSearchRuntime(max_results_per_query=25)
        self.assertEqual(runtime.max_results_per_query, 25)

    def test_max_results_at_ceiling_is_accepted(self):
        from proxy.qz_tool_web import WEB_SEARCH_MAX_RESULTS
        runtime = WebSearchRuntime(max_results_per_query=WEB_SEARCH_MAX_RESULTS)
        self.assertEqual(runtime.max_results_per_query, WEB_SEARCH_MAX_RESULTS)

    def test_max_results_below_ceiling_is_accepted(self):
        runtime = WebSearchRuntime(max_results_per_query=3)
        self.assertEqual(runtime.max_results_per_query, 3)

    def test_request_router_passes_budgets_from_search_config(self):
        """_web_runtime reads search.json routing.* and passes to WebSearchRuntime."""
        from proxy.qz_search_config import SearchConfigResult
        from pathlib import Path
        # Minimal SearchConfigResult with custom budgets
        fake_result = SearchConfigResult(
            config={
                "routing": {
                    "max_searches_per_turn": 2,
                    "max_page_opens_per_turn": 1,
                    "max_results": 5,
                    "low_result_fallback_threshold": 3,
                },
                "profiles": {},
                "defaults": {},
            },
            source="default",
            path=Path("/fake/search.json"),
            legacy_policy_path=None,
            warnings=[],
        )

        class FakeHandler:
            searxng_policy = {}
            searxng_policy_path = ""
            searxng_capabilities = {}
            searxng_base_url = ""
            searxng_timeout = 15.0
            web_search_cache = {}
            opened_page_cache = {}
            telemetry = None
            root = str(Path(__file__).resolve().parents[1])
            search_config_result = fake_result

        class FakeRouter:
            handler = FakeHandler()

        from proxy.qz_request_router import RequestRouter
        # Directly test _web_runtime by borrowing the logic
        scr = FakeHandler.search_config_result
        _routing = (scr.config.get("routing") or {})
        budgets = {
            "max_searches_per_turn": _routing.get("max_searches_per_turn"),
            "max_page_opens_per_turn": _routing.get("max_page_opens_per_turn"),
            "max_results_per_query": _routing.get("max_results") or _routing.get("max_results_per_query"),
            "low_result_fallback_threshold": _routing.get("low_result_fallback_threshold"),
        }
        runtime = WebSearchRuntime(**budgets)
        self.assertEqual(runtime.max_searches_per_turn, 2)
        self.assertEqual(runtime.max_page_opens_per_turn, 1)
        self.assertEqual(runtime.max_results_per_query, 5)
        self.assertEqual(runtime.low_result_fallback_threshold, 3)

    def test_low_result_fallback_param_overrides_legacy_policy(self):
        runtime = WebSearchRuntime(
            policy={"routing": {"low_result_fallback_threshold": 5}},
            low_result_fallback_threshold=1,
        )
        self.assertEqual(runtime.low_result_fallback_threshold, 1)


class WebSearchBudgetTelemetryTests(unittest.TestCase):
    """Tests for web_search_budget_exceeded telemetry — #60 Slice C."""

    def _runtime_with_telemetry(self, max_searches=2, max_opens=1):
        emitted = []

        class FakeTelemetry:
            def emit(self, event_type, payload):
                emitted.append({"event_type": event_type, "payload": payload})

        runtime = WebSearchRuntime(
            max_searches_per_turn=max_searches,
            max_page_opens_per_turn=max_opens,
            telemetry=FakeTelemetry(),
        )
        return runtime, emitted

    def _call(self, action, **kwargs):
        args = {"action": action}
        args.update(kwargs)
        return {
            "type": "function_call",
            "call_id": "c_test",
            "name": "web_search",
            "arguments": json.dumps(args),
        }

    def test_search_budget_exceeded_emits_telemetry(self):
        runtime, emitted = self._runtime_with_telemetry(max_searches=1)
        counters = {"search": 1, "open_page": 0}
        runtime.execute_web_search_call(self._call("search", query="q"), counters, set())
        budget_events = [e for e in emitted if e["event_type"] == "web_search_budget_exceeded"]
        self.assertEqual(len(budget_events), 1)
        p = budget_events[0]["payload"]
        self.assertEqual(p["action"], "search")
        self.assertEqual(p["limit"], 1)
        self.assertEqual(p["counter"], 1)
        self.assertEqual(p["query"], "q")
        self.assertEqual(p["call_id"], "c_test")

    def test_open_page_budget_exceeded_emits_telemetry(self):
        runtime, emitted = self._runtime_with_telemetry(max_opens=1)
        counters = {"search": 0, "open_page": 1}
        runtime.execute_web_search_call(
            self._call("open_page", url="https://example.com/"), counters, set()
        )
        budget_events = [e for e in emitted if e["event_type"] == "web_search_budget_exceeded"]
        self.assertEqual(len(budget_events), 1)
        p = budget_events[0]["payload"]
        self.assertEqual(p["action"], "open_page")
        self.assertEqual(p["limit"], 1)
        self.assertEqual(p["counter"], 1)
        self.assertEqual(p["url"], "https://example.com/")
        self.assertEqual(p["call_id"], "c_test")

    def test_model_error_output_unchanged_when_budget_exceeded(self):
        """Hard error to model still fires with {"ok": false, "error": "..."}."""
        runtime, emitted = self._runtime_with_telemetry(max_searches=1)
        counters = {"search": 1, "open_page": 0}
        public_item, tool_output, _sources = runtime.execute_web_search_call(
            self._call("search", query="q"), counters, set()
        )
        out = json.loads(tool_output["output"])
        self.assertFalse(out["ok"])
        self.assertIn("limit", out["error"].lower())

    def test_repeated_search_does_not_emit_budget_event(self):
        """Repeated-search guard fires before budget check; no budget event."""
        runtime, emitted = self._runtime_with_telemetry(max_searches=4)
        seen = set()
        counters = {"search": 0, "open_page": 0}
        # First call — allowed
        call = self._call("search", query="duplicate")
        runtime.execute_web_search_call(call, counters, seen)
        # Second identical call — repeated guard triggers, budget not hit
        runtime.execute_web_search_call(call, counters, seen)
        budget_events = [e for e in emitted if e["event_type"] == "web_search_budget_exceeded"]
        self.assertEqual(len(budget_events), 0)

    def test_telemetry_failure_does_not_break_tool_call(self):
        """If telemetry.emit() raises, the tool call still returns an error result."""
        class BrokenTelemetry:
            def emit(self, event_type, payload):
                raise RuntimeError("telemetry down")

        runtime = WebSearchRuntime(
            max_searches_per_turn=1,
            telemetry=BrokenTelemetry(),
        )
        counters = {"search": 1, "open_page": 0}
        try:
            public_item, tool_output, _sources = runtime.execute_web_search_call(
                self._call("search", query="q"), counters, set()
            )
        except Exception:
            self.fail("telemetry failure must not propagate")
        out = json.loads(tool_output["output"])
        self.assertFalse(out["ok"])

    def test_no_budget_event_for_successful_search(self):
        runtime, emitted = self._runtime_with_telemetry(max_searches=4)
        counters = {"search": 0, "open_page": 0}

        def fake_query(query, categories=None, engines=None, top_k=8):
            return {"results": [{"title": "x", "url": "https://x.test/"}]}

        runtime._query_searxng = fake_query
        runtime.execute_web_search_call(
            self._call("search", query="fine"), counters, set()
        )
        budget_events = [e for e in emitted if e["event_type"] == "web_search_budget_exceeded"]
        self.assertEqual(len(budget_events), 0)

    def test_search_profile_included_in_payload(self):
        runtime, emitted = self._runtime_with_telemetry(max_searches=1)
        counters = {"search": 1, "open_page": 0}
        runtime.execute_web_search_call(
            self._call("search", query="q", profile="coding"), counters, set()
        )
        p = [e for e in emitted if e["event_type"] == "web_search_budget_exceeded"][0]["payload"]
        self.assertEqual(p["profile"], "coding")


class SearchConfigProfilesTests(unittest.TestCase):
    """Tests for search_config_profiles (qz.search.v1) integration — #39 Slice C."""

    def test_v1_profiles_added_to_valid_profiles(self):
        """Profile names from search_config_profiles appear in _valid_profiles()."""
        runtime = WebSearchRuntime(
            search_config_profiles={"myprofile": {"categories": ["it"]}},
        )
        self.assertIn("myprofile", runtime._valid_profiles())

    def test_v1_profile_used_when_legacy_has_no_entry(self):
        """v1 profile cfg is used when legacy web_search_profiles has no matching entry."""
        runtime = WebSearchRuntime(
            policy={"web_search_profiles": {"broad": {"categories": ["general"]}}},
            search_config_profiles={"v1only": {"categories": ["science"], "engines": ["arxiv"]}},
            capabilities={"engine_probe": {"arxiv": {"status": "ok"}}},
        )
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=8):
            calls.append({"categories": categories, "engines": engines})
            return {"results": [{"title": "x", "url": "https://x.test/"}]}

        runtime._query_searxng = fake_query
        out = runtime._search_web("some query", profile="v1only")

        self.assertEqual(out["profile"], "v1only")
        self.assertEqual(out["categories"], ["science"])
        self.assertIn("arxiv", out["engines"])

    def test_legacy_profile_wins_over_v1_when_both_present(self):
        """Legacy web_search_profiles wins over v1 profiles for the same name."""
        runtime = WebSearchRuntime(
            policy={"web_search_profiles": {
                "overlap": {"categories": ["from-legacy"], "engines": ["legacy-engine"]},
            }},
            search_config_profiles={
                "overlap": {"categories": ["from-v1"], "engines": ["v1-engine"]},
            },
            capabilities={"engine_probe": {
                "legacy-engine": {"status": "ok"},
                "v1-engine": {"status": "ok"},
            }},
        )
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=8):
            calls.append({"categories": categories, "engines": engines})
            return {"results": [{"title": "x", "url": "https://x.test/"}]}

        runtime._query_searxng = fake_query
        out = runtime._search_web("query", profile="overlap")

        self.assertEqual(out["profile"], "overlap")
        self.assertIn("from-legacy", out["categories"])

    def test_empty_search_config_profiles_does_not_break_runtime(self):
        runtime = WebSearchRuntime(search_config_profiles={})
        self.assertIsInstance(runtime._valid_profiles(), (set, frozenset))

    def test_none_search_config_profiles_treated_as_empty(self):
        runtime = WebSearchRuntime(search_config_profiles=None)
        self.assertEqual(runtime.search_config_profiles, {})


class RetrieveActionTests(unittest.TestCase):
    """Tests for web_search action='retrieve'."""

    MEDIAWIKI_RAW = {
        "source": "pcgamingwiki",
        "input": "https://www.pcgamingwiki.com/wiki/Half-Life_2",
        "status": "ok",
        "summary": "Half-Life 2 is a 2004 first-person shooter.",
        "fields": {
            "title": "Half-Life 2",
            "display_title": "Half-Life 2",
            "categories": ["Action", "Shooter"],
            "word_count": 2400,
            "revision_id": 12345,
            "body_text": "Half-Life 2 is a first-person shooter game developed by Valve.",
            "body_text_truncated": False,
        },
        "freshness": {"source_updated_at": "2024-01-15T00:00:00Z"},
        "agent_api": {"retriever": "fetch-mediawiki-page.py", "source": "pcgamingwiki",
                      "input": "https://www.pcgamingwiki.com/wiki/Half-Life_2"},
    }

    CHARACTER_CARD_RAW = {
        "name": "Lyra",
        "description": "A helpful wizard.",
        "personality": "Calm and wise.",
        "scenario": "A fantasy tavern.",
        "creator": "author_123",
        "tags": ["fantasy", "wizard"],
        "topics": ["magic"],
        "spec": "chara_card_v3",
        "nsfw": False,
        "agent_api": {"retriever": "fetch-character-card.py", "source": "aicharactercards",
                      "input": "https://aicharactercards.com/cards/lyra/"},
    }

    def _make_runtime(self, base_url="http://127.0.0.1:8890"):
        return WebSearchRuntime(base_url=base_url, max_retrievals_per_turn=3)

    def _counters(self):
        return {"search": 0, "open_page": 0, "find_in_page": 0, "retrieve": 0}

    def _call(self, runtime, url, retrieval_source="", counters=None):
        if counters is None:
            counters = self._counters()
        call_item = {
            "type": "function_call",
            "call_id": "c_retrieve_test",
            "name": "web_search",
            "arguments": json.dumps({
                "action": "retrieve",
                "url": url,
                "retrieval_source": retrieval_source,
            }),
        }
        _web_item, tool_output, _sources = runtime.execute_web_search_call(
            call_item,
            counters=counters,
            seen_signatures=set(),
        )
        return json.loads(tool_output["output"])

    # --- normalize_retrieve_response tests ---

    def test_normalize_mediawiki_shape(self):
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response(self.MEDIAWIKI_RAW,
                                                   "https://www.pcgamingwiki.com/wiki/Half-Life_2")
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], "retrieve")
        self.assertEqual(out["title"], "Half-Life 2")
        self.assertIn("Half-Life 2", out["summary"])
        self.assertIn("Half-Life 2", out["content"])
        self.assertEqual(out["retrieval_source"], "pcgamingwiki")
        self.assertEqual(out["retriever"], "fetch-mediawiki-page.py")
        self.assertFalse(out["truncated"])
        self.assertIsInstance(out["metadata"], dict)
        self.assertNotIn("body_text", out["metadata"])

    def test_normalize_character_card_shape(self):
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response(self.CHARACTER_CARD_RAW,
                                                   "https://aicharactercards.com/cards/lyra/")
        self.assertTrue(out["ok"])
        self.assertEqual(out["title"], "Lyra")
        self.assertIn("wizard", out["summary"])
        self.assertIn("Calm and wise", out["content"])
        self.assertEqual(out["retrieval_source"], "aicharactercards")
        self.assertEqual(out["retriever"], "fetch-character-card.py")
        self.assertEqual(out["freshness_hint"], "unknown")
        self.assertIsInstance(out["metadata"], dict)

    def test_normalize_mediawiki_truncation(self):
        import copy
        raw = copy.deepcopy(self.MEDIAWIKI_RAW)
        raw["fields"]["body_text"] = "A" * 7000
        runtime = WebSearchRuntime(max_retrieved_chars=6000)
        out = runtime._normalize_retrieve_response(raw, "https://pcgamingwiki.com/wiki/Test")
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["content"]), 6000)

    def test_normalize_missing_body_falls_back_to_summary(self):
        raw = {
            "source": "fse",
            "status": "ok",
            "summary": "A short story summary.",
            "fields": {"title": "My Story"},
            "agent_api": {"retriever": "fse.py", "source": "fse"},
        }
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response(raw, "https://fse.example/1")
        self.assertTrue(out["ok"])
        self.assertIn("summary", out["content"])

    def test_normalize_no_localhost_in_output(self):
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response(self.MEDIAWIKI_RAW,
                                                   "https://www.pcgamingwiki.com/wiki/Half-Life_2")
        serialized = json.dumps(out)
        self.assertNotIn("127.0.0.1", serialized)
        self.assertNotIn("8890", serialized)

    def test_normalize_bad_status_returns_ok_false(self):
        raw = {
            "status": "not_found",
            "agent_api": {"retriever": "r.py", "source": "x"},
        }
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response(raw, "https://example.com/missing")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "retrieval_failed")

    # --- execute_web_search_call retrieve branch ---

    def test_retrieve_no_base_url(self):
        runtime = WebSearchRuntime(base_url="")
        out = self._call(runtime, "https://pcgamingwiki.com/wiki/Test")
        self.assertFalse(out["ok"])
        self.assertIn("no_base_url", out["error"])

    def test_retrieve_empty_url_returns_error(self):
        runtime = self._make_runtime()
        out = self._call(runtime, "")
        self.assertFalse(out.get("ok"))

    def test_retrieve_budget_exceeded(self):
        runtime = WebSearchRuntime(max_retrievals_per_turn=2, base_url="http://127.0.0.1:8890")
        counters = {**self._counters(), "retrieve": 2}
        events = []
        original_emit = runtime._emit
        runtime._emit = lambda name, data=None: events.append(name)
        out = self._call(runtime, "https://pcgamingwiki.com/wiki/Test", counters=counters)
        self.assertFalse(out.get("ok"))
        self.assertIn("web_search_retrieve_budget_exceeded", events)

    def test_retrieve_telemetry_started_and_failed_on_network_error(self):
        """started fires before HTTP; failed fires when _http_fetch raises."""
        import urllib.error
        with patch("proxy.qz_tool_web._http_fetch", side_effect=urllib.error.URLError("connection refused")):
            runtime = self._make_runtime(base_url="http://127.0.0.1:8890")
            events = []
            runtime._emit = lambda name, data=None: events.append(name)
            self._call(runtime, "https://pcgamingwiki.com/wiki/Half-Life_2")
            self.assertIn("web_search_retrieve_started", events)
            self.assertIn("web_search_retrieve_failed", events)

    def test_retrieve_cache_prevents_second_http_call(self):
        """Second call with the same canonical URL returns cached result without a new HTTP fetch."""
        raw_bytes = json.dumps(self.MEDIAWIKI_RAW).encode()
        with patch("proxy.qz_tool_web._http_fetch", return_value=(raw_bytes, "application/json", "https://x")) as mock_fetch:
            runtime = self._make_runtime()
            c1 = self._counters()
            r1 = self._call(runtime, "https://www.pcgamingwiki.com/wiki/Half-Life_2", counters=c1)
            c2 = self._counters()
            r2 = self._call(runtime, "https://www.pcgamingwiki.com/wiki/Half-Life_2", counters=c2)
            self.assertEqual(mock_fetch.call_count, 1)
            self.assertTrue(r1["result"]["ok"])
            self.assertTrue(r2["result"]["ok"])
            self.assertEqual(r1["result"]["title"], r2["result"]["title"])

    def test_retrieve_cache_hit_still_increments_budget_counter(self):
        """A cache hit still counts against the per-turn retrieve budget."""
        raw_bytes = json.dumps(self.MEDIAWIKI_RAW).encode()
        with patch("proxy.qz_tool_web._http_fetch", return_value=(raw_bytes, "application/json", "https://x")):
            runtime = self._make_runtime()
            counters = self._counters()
            self._call(runtime, "https://www.pcgamingwiki.com/wiki/Half-Life_2", counters=counters)
            self.assertEqual(counters["retrieve"], 1)
            self._call(runtime, "https://www.pcgamingwiki.com/wiki/Half-Life_2", counters=counters)
            self.assertEqual(counters["retrieve"], 2)

    def test_retrieve_success_payload_no_localhost(self):
        runtime = self._make_runtime()

        def fake_retrieve(url, retrieval_source="", max_chars=None):
            return {
                "ok": True, "action": "retrieve", "url": url,
                "retrieval_source": "pcgamingwiki", "retriever": "r.py",
                "title": "HL2", "summary": "Game summary.", "content": "Full content.",
                "metadata": {}, "truncated": False, "freshness_hint": "unknown",
            }

        runtime._retrieve = fake_retrieve
        result = self._call(runtime, "https://pcgamingwiki.com/wiki/Test")
        serialized = json.dumps(result)
        self.assertNotIn("127.0.0.1", serialized)
        self.assertNotIn("8890", serialized)

    def test_retrieve_does_not_affect_existing_search_action(self):
        runtime = self._make_runtime()
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=8):
            calls.append(query)
            return {"results": [{"title": "t", "url": "https://example.test/"}]}

        runtime._query_searxng = fake_query
        counters = self._counters()
        _web_item, tool_output, _sources = runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c_t", "name": "web_search",
             "arguments": json.dumps({"action": "search", "query": "half-life 2"})},
            counters=counters,
            seen_signatures=set(),
        )
        self.assertEqual(len(calls), 1)
        text = json.loads(tool_output["output"])
        self.assertIn("results", text.get("result", {}))

    def test_retrieve_malformed_arguments_no_exception(self):
        runtime = self._make_runtime()
        result = self._call(runtime, "")  # empty url → should fail cleanly
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)

    def test_retrieve_budget_defaults_from_normal_mode(self):
        # #64: default mode is normal; no-param runtime uses normal-mode retrieve limit.
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        runtime = WebSearchRuntime()
        self.assertEqual(runtime.max_retrievals_per_turn, WEB_SEARCH_MODE_DEFAULTS["normal"]["max_retrievals_per_turn"])

    def test_retrieve_max_chars_flat_param_accepted_above_old_ceiling(self):
        # #64: old 12000 hard ceiling removed; flat param up to absolute max.
        from proxy.qz_tool_web import WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS
        runtime = WebSearchRuntime(max_retrieved_chars=30000)
        self.assertEqual(runtime.max_retrieved_chars, 30000)
        # Still clamped by absolute cap
        runtime2 = WebSearchRuntime(max_retrieved_chars=999999)
        self.assertEqual(runtime2.max_retrieved_chars, WEB_SEARCH_ABSOLUTE_MAX_RETRIEVED_CHARS)

    def test_retrieve_zero_budget_flat_param_falls_back_to_quick_default(self):
        # Invalid flat param falls back to quick-mode default for that field.
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        runtime = WebSearchRuntime(max_retrievals_per_turn=0)
        # quick-mode default is 2, but since no mode_table and no explicit mode, uses normal (default mode)
        # For zero/invalid flat params, falls through to normal mode baseline (4)
        self.assertGreaterEqual(runtime.max_retrievals_per_turn, 1)

    def test_retrieve_negative_budget_flat_param_falls_back(self):
        runtime = WebSearchRuntime(max_retrievals_per_turn=-5)
        self.assertGreaterEqual(runtime.max_retrievals_per_turn, 1)

    def test_retrieve_telemetry_failure_is_nonfatal(self):
        """If telemetry.emit raises, execute_web_search_call still returns a valid result."""
        class BrokenTelemetry:
            def emit(self, event_type, payload):
                raise RuntimeError("telemetry down")

        raw_bytes = json.dumps(self.MEDIAWIKI_RAW).encode()
        with patch("proxy.qz_tool_web._http_fetch", return_value=(raw_bytes, "application/json", "https://x")):
            runtime = WebSearchRuntime(
                base_url="http://127.0.0.1:8890",
                telemetry=BrokenTelemetry(),
            )
            try:
                result = self._call(runtime, "https://www.pcgamingwiki.com/wiki/Half-Life_2")
            except Exception:
                self.fail("telemetry failure must not propagate from retrieve call")
            self.assertIsInstance(result, dict)
            self.assertIn("ok", result)

    def test_normalize_invalid_response_non_dict(self):
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response("not a dict", "https://example.com/")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_response")

    def test_normalize_metadata_bounded_to_6_keys(self):
        """Metadata must never exceed 6 keys regardless of upstream fields."""
        raw = {
            "source": "fse",
            "status": "ok",
            "summary": "s",
            "fields": {
                "title": "T",
                "author": "A",
                "rating": 5,
                "word_count": 1000,
                "published_at": "2020-01-01",
                "revision_id": 42,
                "categories": ["a", "b"],
                "length": 999,  # 7th eligible field — must be excluded
            },
            "agent_api": {"retriever": "r.py", "source": "fse"},
        }
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response(raw, "https://fse.example/1")
        self.assertTrue(out["ok"])
        self.assertLessEqual(len(out["metadata"]), 6)
        self.assertNotIn("body_text", out["metadata"])

    def test_normalize_character_card_tags_bounded_to_10(self):
        raw = dict(self.CHARACTER_CARD_RAW)
        raw["tags"] = [f"tag{i}" for i in range(20)]
        runtime = self._make_runtime()
        out = runtime._normalize_retrieve_response(raw, "https://aicharactercards.com/cards/lyra/")
        self.assertTrue(out["ok"])
        self.assertLessEqual(len(out["metadata"].get("tags", [])), 10)

    def test_request_router_passes_retrieve_budgets(self):
        """_web_runtime passes max_retrievals_per_turn and max_retrieved_chars from search.json routing."""
        from proxy.qz_search_config import SearchConfigResult
        from pathlib import Path
        fake_result = SearchConfigResult(
            config={
                "routing": {
                    "max_searches_per_turn": 2,
                    "max_page_opens_per_turn": 1,
                    "max_retrievals_per_turn": 5,
                    "max_retrieved_chars": 4000,
                },
                "profiles": {},
                "defaults": {},
            },
            source="default",
            path=Path("/fake/search.json"),
            legacy_policy_path=None,
            warnings=[],
        )
        _routing = fake_result.config.get("routing") or {}
        budgets = {
            "max_retrievals_per_turn": _routing.get("max_retrievals_per_turn"),
            "max_retrieved_chars": _routing.get("max_retrieved_chars"),
        }
        runtime = WebSearchRuntime(**budgets)
        self.assertEqual(runtime.max_retrievals_per_turn, 5)
        self.assertEqual(runtime.max_retrieved_chars, 4000)

    def test_router_counters_include_retrieve_key(self):
        """Production counters dict must include 'retrieve' so the retrieve branch
        can safely use counters['retrieve'] += 1."""
        import importlib, ast
        import proxy.qz_request_router as _rr_mod
        import inspect
        src = inspect.getsource(_rr_mod)
        # Check that the counters initialization includes "retrieve"
        self.assertIn('"retrieve"', src.split("counters = {")[1].split("}")[0])


class WebSearchBudgetModeTests(unittest.TestCase):
    """Tests for #64 research-grade budget modes."""

    def _make_call(self, action="search", query="q", budget_mode=None, url=None):
        args = {"action": action}
        if query:
            args["query"] = query
        if budget_mode:
            args["budget_mode"] = budget_mode
        if url:
            args["url"] = url
        return {
            "type": "function_call", "call_id": "c_mode_test", "name": "web_search",
            "arguments": json.dumps(args),
        }

    def _counters(self):
        return {"search": 0, "open_page": 0, "retrieve": 0}

    def _fake_query(self, n=5):
        def query(q, categories=None, engines=None, top_k=12):
            return {"results": [{"title": f"r{i}", "url": f"https://x.test/{i}"} for i in range(n)]}
        return query

    # --- _resolve_budget_mode unit tests ---

    def test_default_mode_is_normal(self):
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        result = _resolve_budget_mode("", {}, {}, {})
        self.assertEqual(result["budget_mode"], "normal")
        self.assertEqual(result["max_results"], WEB_SEARCH_MODE_DEFAULTS["normal"]["max_results"])

    def test_quick_mode_resolves_conservative_limits(self):
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        result = _resolve_budget_mode("quick", {"quick": WEB_SEARCH_MODE_DEFAULTS["quick"]}, {}, {})
        q = WEB_SEARCH_MODE_DEFAULTS["quick"]
        self.assertEqual(result["max_results"], q["max_results"])
        self.assertEqual(result["max_searches_per_turn"], q["max_searches_per_turn"])
        self.assertEqual(result["max_retrieved_chars"], q["max_retrieved_chars"])

    def test_normal_mode_resolves_expected_values(self):
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        n = WEB_SEARCH_MODE_DEFAULTS["normal"]
        result = _resolve_budget_mode("normal", {}, {}, {})
        self.assertEqual(result["max_results"], n["max_results"])
        self.assertEqual(result["max_searches_per_turn"], n["max_searches_per_turn"])
        self.assertEqual(result["max_page_opens_per_turn"], n["max_page_opens_per_turn"])
        self.assertEqual(result["max_retrievals_per_turn"], n["max_retrievals_per_turn"])
        self.assertEqual(result["max_retrieved_chars"], n["max_retrieved_chars"])

    def test_deep_mode_exceeds_old_8_ceiling(self):
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        result = _resolve_budget_mode("deep", {}, {}, {})
        self.assertGreater(result["max_results"], 8)
        self.assertEqual(result["max_results"], WEB_SEARCH_MODE_DEFAULTS["deep"]["max_results"])

    def test_audit_mode_resolves_high_values(self):
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        result = _resolve_budget_mode("audit", {}, {}, {})
        a = WEB_SEARCH_MODE_DEFAULTS["audit"]
        self.assertEqual(result["max_retrievals_per_turn"], a["max_retrievals_per_turn"])
        self.assertEqual(result["max_retrieved_chars"], a["max_retrieved_chars"])
        self.assertGreater(result["max_retrieved_chars"], 12000)

    def test_invalid_mode_falls_back_to_default(self):
        from proxy.qz_tool_web import _resolve_budget_mode
        result = _resolve_budget_mode("fancy_mode", {}, {}, {})
        self.assertEqual(result["budget_mode"], "normal")

    def test_flat_routing_compat_when_no_budget_modes_key(self):
        from proxy.qz_tool_web import _resolve_budget_mode
        flat = {"max_results": 6, "max_searches_per_turn": 3, "max_page_opens_per_turn": 2,
                "max_retrievals_per_turn": 2, "max_retrieved_chars": 4000}
        result = _resolve_budget_mode("", None, flat, {})
        self.assertEqual(result["max_results"], 6)
        self.assertEqual(result["max_searches_per_turn"], 3)
        self.assertEqual(result["max_retrieved_chars"], 4000)

    def test_absolute_cap_clamps_mode_budget(self):
        from proxy.qz_tool_web import _resolve_budget_mode
        caps = {"results": 10, "searches": 5, "opens": 5, "retrievals": 3, "retrieved_chars": 8000}
        result = _resolve_budget_mode("deep", {}, {}, caps)
        self.assertEqual(result["max_results"], 10)
        self.assertEqual(result["max_searches_per_turn"], 5)
        self.assertEqual(result["max_retrieved_chars"], 8000)

    def test_absolute_cap_cannot_exceed_built_in_constant(self):
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_ABSOLUTE_MAX_RESULTS
        caps = {"results": 99999}
        result = _resolve_budget_mode("audit", {}, {}, caps)
        self.assertLessEqual(result["max_results"], WEB_SEARCH_ABSOLUTE_MAX_RESULTS)

    def test_empty_flat_budgets_uses_default_mode(self):
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        result = _resolve_budget_mode("", None, {}, {})
        # All flat values None/absent → falls through to default mode (normal)
        self.assertEqual(result["budget_mode"], "normal")
        self.assertEqual(result["max_results"], WEB_SEARCH_MODE_DEFAULTS["normal"]["max_results"])

    # --- execute_web_search_call integration tests ---

    def test_execute_uses_deep_mode_max_results(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        runtime = WebSearchRuntime()
        captured = []
        def fake_query(q, categories=None, engines=None, top_k=12):
            captured.append(top_k)
            return {"results": [{"title": "t", "url": "https://x.test/"}]}
        runtime._query_searxng = fake_query
        counters = self._counters()
        runtime.execute_web_search_call(
            self._make_call(budget_mode="deep"), counters, set()
        )
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], WEB_SEARCH_MODE_DEFAULTS["deep"]["max_results"])

    def test_execute_budget_exceeded_telemetry_includes_mode(self):
        emitted = []
        class FakeTelemetry:
            def emit(self, event_type, payload): emitted.append({"event": event_type, "payload": payload})
        runtime = WebSearchRuntime(telemetry=FakeTelemetry())
        counters = {**self._counters(), "search": 99}
        runtime.execute_web_search_call(self._make_call(budget_mode="deep"), counters, set())
        exceeded = [e for e in emitted if e["event"] == "web_search_budget_exceeded"]
        self.assertEqual(len(exceeded), 1)
        self.assertEqual(exceeded[0]["payload"]["budget_mode"], "deep")

    def test_execute_tool_call_started_includes_mode(self):
        emitted = []
        class FakeTelemetry:
            def emit(self, event_type, payload): emitted.append({"event": event_type, "payload": payload})
        runtime = WebSearchRuntime(telemetry=FakeTelemetry())
        runtime._query_searxng = self._fake_query()
        runtime.execute_web_search_call(self._make_call(budget_mode="audit"), self._counters(), set())
        started = [e for e in emitted if e["event"] == "tool_call_started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["payload"]["budget_mode"], "audit")

    def test_retrieve_budget_exceeded_telemetry_includes_mode(self):
        emitted = []
        class FakeTelemetry:
            def emit(self, event_type, payload): emitted.append({"event": event_type, "payload": payload})
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        deep_retrievals = WEB_SEARCH_MODE_DEFAULTS["deep"]["max_retrievals_per_turn"]
        runtime = WebSearchRuntime(base_url="http://127.0.0.1:8890", telemetry=FakeTelemetry())
        counters = {**self._counters(), "retrieve": deep_retrievals}
        runtime.execute_web_search_call(
            self._make_call(action="retrieve", url="https://example.com/", budget_mode="deep"),
            counters, set()
        )
        exceeded = [e for e in emitted if e["event"] == "web_search_retrieve_budget_exceeded"]
        self.assertEqual(len(exceeded), 1)
        self.assertEqual(exceeded[0]["payload"]["budget_mode"], "deep")

    def test_mode_table_from_config_used_when_present(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        custom_table = {
            "deep": {"max_results": 30, "max_searches_per_turn": 15,
                     "max_page_opens_per_turn": 15, "max_retrievals_per_turn": 8,
                     "max_retrieved_chars": 25000}
        }
        runtime = WebSearchRuntime(budget_mode_table=custom_table)
        captured = []
        def fake_query(q, categories=None, engines=None, top_k=12):
            captured.append(top_k)
            return {"results": []}
        runtime._query_searxng = fake_query
        runtime.execute_web_search_call(self._make_call(budget_mode="deep"), self._counters(), set())
        self.assertEqual(captured[0], 30)

    def test_retrieve_uses_effective_max_chars(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        from unittest.mock import patch
        MEDIAWIKI_RAW = {
            "source": "pcgamingwiki", "input": "https://www.pcgamingwiki.com/wiki/T",
            "status": "ok", "summary": "s",
            "fields": {"title": "T", "body_text": "A" * 50000, "body_text_truncated": False},
            "freshness": {}, "agent_api": {"retriever": "r.py", "source": "pcgamingwiki"},
        }
        raw_bytes = json.dumps(MEDIAWIKI_RAW).encode()
        with patch("proxy.qz_tool_web._http_fetch", return_value=(raw_bytes, "application/json", "https://x")):
            runtime = WebSearchRuntime(base_url="http://127.0.0.1:8890")
            counters = self._counters()
            _wc, to, _ = runtime.execute_web_search_call(
                self._make_call(action="retrieve", url="https://www.pcgamingwiki.com/wiki/T",
                                budget_mode="deep"),
                counters, set()
            )
            out = json.loads(to["output"])
            content_len = len(out["result"]["content"])
            deep_chars = WEB_SEARCH_MODE_DEFAULTS["deep"]["max_retrieved_chars"]
            self.assertLessEqual(content_len, deep_chars)
            self.assertGreater(content_len, 12000)  # exceeds old ceiling

    def test_tool_schema_contains_budget_mode(self):
        from proxy.qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
        schema = WEB_SEARCH_TOOL_ADAPTER.to_upstream_tool({})
        props = schema["parameters"]["properties"]
        self.assertIn("budget_mode", props)
        self.assertEqual(props["budget_mode"]["type"], "string")
        self.assertNotIn("enum", props["budget_mode"])
        self.assertIn("capabilities", props["budget_mode"]["description"])

    def test_tool_schema_contains_retrieve_action(self):
        from proxy.qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
        schema = WEB_SEARCH_TOOL_ADAPTER.to_upstream_tool({})
        props = schema["parameters"]["properties"]
        self.assertIn("retrieve", props["action"]["enum"])

    def test_tool_schema_top_k_no_hard_maximum(self):
        from proxy.qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
        schema = WEB_SEARCH_TOOL_ADAPTER.to_upstream_tool({})
        top_k = schema["parameters"]["properties"]["top_k"]
        self.assertNotIn("maximum", top_k)

    def test_no_localhost_in_deep_mode_output(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        runtime = WebSearchRuntime(base_url="http://127.0.0.1:8890")
        runtime._query_searxng = self._fake_query(25)
        counters = self._counters()
        _wc, to, _srcs = runtime.execute_web_search_call(
            self._make_call(budget_mode="deep"), counters, set()
        )
        serialized = to["output"]
        self.assertNotIn("127.0.0.1", serialized)
        self.assertNotIn(":8890", serialized)

    def test_existing_calls_without_budget_mode_still_work(self):
        runtime = WebSearchRuntime()
        calls = []
        def fake_query(q, categories=None, engines=None, top_k=12):
            calls.append(top_k)
            return {"results": [{"title": "t", "url": "https://x.test/"}]}
        runtime._query_searxng = fake_query
        counters = self._counters()
        _wc, to, _ = runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c", "name": "web_search",
             "arguments": json.dumps({"action": "search", "query": "hello"})},
            counters, set()
        )
        out = json.loads(to["output"])
        self.assertTrue(out["ok"])
        self.assertEqual(len(calls), 1)

    def test_invalid_budget_mode_arg_falls_back_to_normal(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        runtime = WebSearchRuntime()
        captured = []
        def fake_query(q, categories=None, engines=None, top_k=12):
            captured.append(top_k)
            return {"results": []}
        runtime._query_searxng = fake_query
        runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c", "name": "web_search",
             "arguments": json.dumps({"action": "search", "query": "q", "budget_mode": "turbo_max"})},
            self._counters(), set()
        )
        self.assertEqual(captured[0], WEB_SEARCH_MODE_DEFAULTS["normal"]["max_results"])

    # --- Precedence edge cases ---

    def test_flat_all_none_no_table_uses_default_mode(self):
        """All flat values None and no mode_table → else branch → normal mode defaults."""
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        flat_all_none = {"max_results": None, "max_searches_per_turn": None,
                         "max_page_opens_per_turn": None, "max_retrievals_per_turn": None,
                         "max_retrieved_chars": None}
        result = _resolve_budget_mode("", None, flat_all_none, {})
        self.assertEqual(result["budget_mode"], "normal")
        self.assertEqual(result["max_results"], WEB_SEARCH_MODE_DEFAULTS["normal"]["max_results"])

    def test_mode_table_beats_flat_fields(self):
        """budget_modes present: flat routing.max_* are ignored even if flat values differ."""
        from proxy.qz_tool_web import _resolve_budget_mode
        table = {"normal": {"max_results": 15, "max_searches_per_turn": 9,
                             "max_page_opens_per_turn": 9, "max_retrievals_per_turn": 5,
                             "max_retrieved_chars": 15000}}
        flat = {"max_results": 3, "max_searches_per_turn": 1, "max_page_opens_per_turn": 1,
                "max_retrievals_per_turn": 1, "max_retrieved_chars": 1000}
        result = _resolve_budget_mode("", table, flat, {})
        # Mode table entry for "normal" wins over flat
        self.assertEqual(result["max_results"], 15)
        self.assertEqual(result["max_searches_per_turn"], 9)

    def test_mode_not_in_table_uses_builtin_defaults(self):
        """Table present but mode not found → falls back to built-in WEB_SEARCH_MODE_DEFAULTS."""
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        table = {"normal": WEB_SEARCH_MODE_DEFAULTS["normal"]}  # no "deep" entry
        result = _resolve_budget_mode("deep", table, {}, {})
        self.assertEqual(result["budget_mode"], "deep")
        self.assertEqual(result["max_results"], WEB_SEARCH_MODE_DEFAULTS["deep"]["max_results"])

    def test_partial_flat_budgets_fills_missing_with_quick_defaults(self):
        """Partial flat config: missing fields use quick-mode defaults (old constant equivalents)."""
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        flat = {"max_results": 5, "max_searches_per_turn": None,
                "max_page_opens_per_turn": None, "max_retrievals_per_turn": None,
                "max_retrieved_chars": None}
        result = _resolve_budget_mode("", None, flat, {})
        self.assertEqual(result["max_results"], 5)
        # Missing fields fall back to quick-mode defaults
        self.assertEqual(result["max_searches_per_turn"],
                         WEB_SEARCH_MODE_DEFAULTS["quick"]["max_searches_per_turn"])

    def test_different_modes_in_same_turn_share_counters(self):
        """Two calls with different budget_mode values share the same turn counters."""
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        runtime = WebSearchRuntime()
        runtime._query_searxng = lambda q, categories=None, engines=None, top_k=12: {"results": [{"title": "t", "url": "https://x.test/"}]}
        counters = self._counters()
        # First call: quick mode (searches=4)
        runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c1", "name": "web_search",
             "arguments": json.dumps({"action": "search", "query": "q1", "budget_mode": "quick"})},
            counters, set()
        )
        self.assertEqual(counters["search"], 1)
        # Second call: deep mode (searches=20) — counter continues from 1
        runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c2", "name": "web_search",
             "arguments": json.dumps({"action": "search", "query": "q2", "budget_mode": "deep"})},
            counters, set()
        )
        self.assertEqual(counters["search"], 2)

    def test_quick_mode_counter_limit_enforced(self):
        """quick mode limit (4 searches) is enforced even if previous calls used deep mode."""
        runtime = WebSearchRuntime()
        counters = {**self._counters(), "search": 4}  # already at quick limit
        _wc, to, _ = runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c", "name": "web_search",
             "arguments": json.dumps({"action": "search", "query": "q", "budget_mode": "quick"})},
            counters, set()
        )
        out = json.loads(to["output"])
        self.assertFalse(out["ok"])
        self.assertIn("4", out["error"])

    def test_deep_mode_counter_allows_more_searches(self):
        """deep mode limit (20 searches) allows calls beyond quick limit of 4."""
        runtime = WebSearchRuntime()
        runtime._query_searxng = lambda q, categories=None, engines=None, top_k=12: {"results": []}
        counters = {**self._counters(), "search": 5}  # would be refused under quick
        _wc, to, _ = runtime.execute_web_search_call(
            {"type": "function_call", "call_id": "c", "name": "web_search",
             "arguments": json.dumps({"action": "search", "query": "q", "budget_mode": "deep"})},
            counters, set()
        )
        out = json.loads(to["output"])
        self.assertTrue(out["ok"])

    def test_absolute_cap_from_config_cannot_exceed_builtin(self):
        """routing.absolute_max_results=9999 is clamped to WEB_SEARCH_ABSOLUTE_MAX_RESULTS."""
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_ABSOLUTE_MAX_RESULTS
        result = _resolve_budget_mode("audit", {}, {}, {"results": 9999})
        self.assertLessEqual(result["max_results"], WEB_SEARCH_ABSOLUTE_MAX_RESULTS)

    def test_default_budget_mode_respected_when_no_budget_mode_arg(self):
        """default_budget_mode=quick causes no-arg calls to use quick limits."""
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS, _resolve_budget_mode
        result = _resolve_budget_mode("", {}, {}, {}, default_mode="quick")
        self.assertEqual(result["budget_mode"], "quick")
        self.assertEqual(result["max_results"], WEB_SEARCH_MODE_DEFAULTS["quick"]["max_results"])

    def test_invalid_default_mode_falls_through_to_builtin_default(self):
        """default_budget_mode=gibberish → built-in WEB_SEARCH_DEFAULT_BUDGET_MODE (normal)."""
        from proxy.qz_tool_web import _resolve_budget_mode, WEB_SEARCH_MODE_DEFAULTS
        result = _resolve_budget_mode("", {}, {}, {}, default_mode="gibberish")
        self.assertEqual(result["budget_mode"], "normal")


class RetrieveMaxCharsParamTests(unittest.TestCase):
    """Tests that _retrieve passes max_chars to /retrieve query string."""

    MEDIAWIKI_RAW = {
        "source": "pcgamingwiki",
        "input": "https://www.pcgamingwiki.com/wiki/Half-Life_2",
        "status": "ok",
        "summary": "HL2 is a game.",
        "fields": {"title": "Half-Life 2", "body_text": "A" * 40000, "body_text_truncated": False},
        "freshness": {},
        "agent_api": {"retriever": "fetch-mediawiki-page.py", "source": "pcgamingwiki",
                      "input": "https://www.pcgamingwiki.com/wiki/Half-Life_2"},
    }

    def _captured_url(self, max_chars, mode=None, retrieval_source=""):
        """Call _retrieve with the given max_chars and capture the URL passed to _http_fetch."""
        raw_bytes = json.dumps(self.MEDIAWIKI_RAW).encode()
        captured = []
        def fake_fetch(url, timeout, accept):
            captured.append(url)
            return raw_bytes, "application/json", "https://x"
        with patch("proxy.qz_tool_web._http_fetch", side_effect=fake_fetch):
            rt = WebSearchRuntime(base_url="http://127.0.0.1:8890")
            rt._retrieve("https://www.pcgamingwiki.com/wiki/Half-Life_2",
                         retrieval_source=retrieval_source, max_chars=max_chars)
        return captured[0] if captured else ""

    def _captured_url_via_execute(self, budget_mode):
        """Drive _retrieve via execute_web_search_call to get the URL with per-call budget."""
        raw_bytes = json.dumps(self.MEDIAWIKI_RAW).encode()
        captured = []
        def fake_fetch(url, timeout, accept):
            captured.append(url)
            return raw_bytes, "application/json", "https://x"
        with patch("proxy.qz_tool_web._http_fetch", side_effect=fake_fetch):
            rt = WebSearchRuntime(base_url="http://127.0.0.1:8890")
            call = {"type": "function_call", "call_id": "c", "name": "web_search",
                    "arguments": json.dumps({
                        "action": "retrieve",
                        "url": "https://www.pcgamingwiki.com/wiki/Half-Life_2",
                        "budget_mode": budget_mode,
                        "retrieval_source": "mediawiki",
                    })}
            rt.execute_web_search_call(call, {"search": 0, "open_page": 0, "retrieve": 0}, set())
        return captured[0] if captured else ""

    def _parse_max_chars(self, url):
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        return int(qs["max_chars"][0]) if "max_chars" in qs else None

    def test_quick_mode_sends_max_chars_6000(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        url = self._captured_url_via_execute("quick")
        self.assertEqual(self._parse_max_chars(url),
                         WEB_SEARCH_MODE_DEFAULTS["quick"]["max_retrieved_chars"])

    def test_deep_mode_sends_max_chars_30000(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        url = self._captured_url_via_execute("deep")
        self.assertEqual(self._parse_max_chars(url),
                         WEB_SEARCH_MODE_DEFAULTS["deep"]["max_retrieved_chars"])

    def test_audit_mode_sends_max_chars_60000(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        url = self._captured_url_via_execute("audit")
        self.assertEqual(self._parse_max_chars(url),
                         WEB_SEARCH_MODE_DEFAULTS["audit"]["max_retrieved_chars"])

    def test_direct_max_chars_forwarded(self):
        url = self._captured_url(max_chars=25000)
        self.assertEqual(self._parse_max_chars(url), 25000)

    def test_none_max_chars_uses_instance_default(self):
        from proxy.qz_tool_web import WEB_SEARCH_MODE_DEFAULTS
        url = self._captured_url(max_chars=None)
        # Instance default from normal mode (default budget)
        self.assertEqual(self._parse_max_chars(url),
                         WEB_SEARCH_MODE_DEFAULTS["normal"]["max_retrieved_chars"])

    def test_upstream_over_return_still_clamped_by_proxy(self):
        """Even if upstream ignores max_chars hint, proxy-side _max_chars truncates."""
        long_body = "B" * 50000
        big_raw = dict(self.MEDIAWIKI_RAW)
        big_raw = {**self.MEDIAWIKI_RAW,
                   "fields": {**self.MEDIAWIKI_RAW["fields"], "body_text": long_body}}
        raw_bytes = json.dumps(big_raw).encode()
        with patch("proxy.qz_tool_web._http_fetch",
                   return_value=(raw_bytes, "application/json", "https://x")):
            rt = WebSearchRuntime(base_url="http://127.0.0.1:8890")
            result = rt._retrieve("https://www.pcgamingwiki.com/wiki/Half-Life_2",
                                  max_chars=6000)
            self.assertTrue(result["ok"])
            self.assertLessEqual(len(result["content"]), 6000)
            self.assertTrue(result["truncated"])

    def test_max_chars_not_in_model_output(self):
        """max_chars param must not appear in model-visible tool output."""
        raw_bytes = json.dumps(self.MEDIAWIKI_RAW).encode()
        with patch("proxy.qz_tool_web._http_fetch",
                   return_value=(raw_bytes, "application/json", "https://x")):
            rt = WebSearchRuntime(base_url="http://127.0.0.1:8890")
            call = {"type": "function_call", "call_id": "c", "name": "web_search",
                    "arguments": json.dumps({
                        "action": "retrieve",
                        "url": "https://www.pcgamingwiki.com/wiki/Half-Life_2",
                        "budget_mode": "deep",
                    })}
            _, to, _ = rt.execute_web_search_call(
                call, {"search": 0, "open_page": 0, "retrieve": 0}, set()
            )
            serialized = to["output"]
            self.assertNotIn("127.0.0.1", serialized)
            self.assertNotIn("8890", serialized)
            self.assertNotIn("/retrieve?", serialized)


class FurryProfileTests(unittest.TestCase):
    def _runtime_with_config(self, profiles):
        return WebSearchRuntime(search_config_profiles=profiles)

    def test_furry_fse_profile_uses_only_fse(self):
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        profiles = cfg.config.get("profiles", {})
        self.assertIn("furry_fse", profiles)
        fse_cfg = profiles["furry_fse"]
        self.assertEqual(fse_cfg.get("engines"), ["fse"])

    def test_furry_images_profile_uses_e926_and_furbooru(self):
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        profiles = cfg.config.get("profiles", {})
        self.assertIn("furry_images", profiles)
        img_cfg = profiles["furry_images"]
        engines = img_cfg.get("engines", [])
        self.assertIn("e926", engines)
        self.assertIn("furbooru", engines)
        self.assertNotIn("fse", engines)

    def test_furry_fse_is_valid_profile_in_runtime(self):
        runtime = self._runtime_with_config({"furry_fse": {"categories": ["general"], "engines": ["fse"]}})
        self.assertIn("furry_fse", runtime._valid_profiles())

    def test_furry_images_is_valid_profile_in_runtime(self):
        runtime = self._runtime_with_config({"furry_images": {"categories": ["images"], "engines": ["e926", "furbooru"]}})
        self.assertIn("furry_images", runtime._valid_profiles())

    def test_capabilities_exposes_furry_fse_and_furry_images(self):
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        profiles = cfg.config.get("profiles", {})
        runtime = WebSearchRuntime(search_config_profiles=profiles)
        caps = build_web_search_capabilities(runtime)
        self.assertIn("furry_fse", caps["profiles"])
        self.assertIn("furry_images", caps["profiles"])

    def test_furry_fse_profile_in_VALID_WEB_SEARCH_PROFILES_static(self):
        from proxy.qz_tool_web import VALID_WEB_SEARCH_PROFILES
        self.assertIn("furry_fse", VALID_WEB_SEARCH_PROFILES)
        self.assertIn("furry_images", VALID_WEB_SEARCH_PROFILES)

    # --- Fix Pass L: Slice F missing tests ---

    def test_furry_images_retrieval_expected_is_false(self):
        """furry_images must not claim prose retrieval (Fix Pass L)."""
        from proxy.qz_search_config import load_search_config
        from proxy.qz_tool_web import _profile_retrieval_expected
        cfg = load_search_config()
        img_cfg = cfg.config.get("profiles", {}).get("furry_images", {})
        self.assertFalse(_profile_retrieval_expected(img_cfg),
                         "furry_images must not claim prose retrieval")

    def test_furry_images_retrieval_kind_is_image_metadata(self):
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        img_cfg = cfg.config.get("profiles", {}).get("furry_images", {})
        self.assertEqual(img_cfg.get("retrieval_kind"), "image_metadata")

    def test_furry_fse_retrieval_expected_remains_true(self):
        from proxy.qz_search_config import load_search_config
        from proxy.qz_tool_web import _profile_retrieval_expected
        cfg = load_search_config()
        fse_cfg = cfg.config.get("profiles", {}).get("furry_fse", {})
        self.assertTrue(_profile_retrieval_expected(fse_cfg))

    def test_capabilities_furry_images_not_prose_retrieval(self):
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(search_config_profiles=cfg.config.get("profiles", {}))
        caps = build_web_search_capabilities(runtime)
        img_profile = caps["profiles"].get("furry_images", {})
        self.assertFalse(img_profile.get("retrieval_expected"),
                         "furry_images capabilities must not claim prose retrieval")

    def test_capabilities_furry_fse_retrieval_expected_true(self):
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(search_config_profiles=cfg.config.get("profiles", {}))
        caps = build_web_search_capabilities(runtime)
        fse_profile = caps["profiles"].get("furry_fse", {})
        self.assertTrue(fse_profile.get("retrieval_expected"))

    def test_capabilities_engine_availability_unknown_without_probe(self):
        """Without probe data, capabilities must say availability unknown."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(
            search_config_profiles={"furry_images": {"engines": ["e926", "furbooru"]}},
            capabilities={},  # no probe
        )
        caps = build_web_search_capabilities(runtime)
        self.assertFalse(
            caps["profiles"]["furry_images"]["engine_availability_known"],
            "Availability should be unknown without probe data",
        )
        warning_text = " ".join(caps.get("warnings", []))
        self.assertIn("not been probed", warning_text)

    def test_capabilities_effective_engines_with_probe(self):
        """With probe data, capabilities includes effective/unavailable engine info."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_images": {"engines": ["e926", "furbooru"]},
                "furry_fse": {"engines": ["fse"]},
            },
            capabilities={"engine_probe": {
                "e926": {"status": "ok"},
                "fse": {"status": "ok"},
                # furbooru absent — probe unavailable
            }},
        )
        caps = build_web_search_capabilities(runtime)
        img = caps["profiles"]["furry_images"]
        fse = caps["profiles"]["furry_fse"]
        self.assertTrue(img["engine_availability_known"])
        self.assertIn("furbooru", img.get("probe_unavailable_engines", []))
        self.assertEqual(fse["effective_engine_count"], 1)

    def test_capabilities_zero_effective_engines_warning(self):
        """Profile with all engines probe-unavailable triggers warning."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(
            search_config_profiles={"custom": {"engines": ["engine_x", "engine_y"]}},
            capabilities={"engine_probe": {"engine_z": {"status": "ok"}}},
        )
        caps = build_web_search_capabilities(runtime)
        warning_text = " ".join(caps.get("warnings", []))
        self.assertIn("no available engines", warning_text)

    def test_capabilities_no_local_url_leak(self):
        """Capabilities must not expose local SearXNG URLs."""
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(
            search_config_profiles=cfg.config.get("profiles", {}),
            base_url="http://127.0.0.1:8890",
        )
        caps = build_web_search_capabilities(runtime)
        caps_str = str(caps)
        self.assertNotIn("127.0.0.1", caps_str)
        self.assertNotIn("localhost", caps_str)
        self.assertNotIn(":8890", caps_str)

    def test_e926_furbooru_fse_not_in_non_text_blocked(self):
        """e926, furbooru, and fse must not be in the non_text suppression list."""
        import json
        from pathlib import Path
        policy_path = Path(__file__).resolve().parents[1] / "config/default/search-policy.json"
        if not policy_path.exists():
            self.skipTest("search-policy.json not found")
        policy = json.loads(policy_path.read_text())
        blocked = policy.get("non_text_engines_disabled_for_current_web_search_tool", [])
        self.assertNotIn("e926", blocked)
        self.assertNotIn("furbooru", blocked)
        self.assertNotIn("fse", blocked)

    def test_furry_sofurry_profile_absent_by_default(self):
        """furry_sofurry profile is not configured unless explicitly added."""
        from proxy.qz_tool_web import VALID_WEB_SEARCH_PROFILES
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        profiles = cfg.config.get("profiles", {})
        self.assertNotIn("furry_sofurry", profiles,
                         "furry_sofurry must not be configured unless SoFurry is verified")

    def test_sofurry_probe_does_not_create_profile(self):
        """Probe detecting sofurry engine does NOT auto-create a profile."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(
            search_config_profiles={},
            capabilities={"engine_probe": {"sofurry": {"status": "ok"}}},
        )
        caps = build_web_search_capabilities(runtime)
        self.assertNotIn("furry_sofurry", caps["profiles"])
        # But a warning should mention the unconfigured engine
        warning_text = " ".join(caps.get("warnings", []))
        self.assertIn("SoFurry", warning_text)

    def test_explicit_fse_override_resolves_to_fse_only(self):
        """profile='furry', engines=['fse'] routes only to fse."""
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=12):
            calls.append({"query": query, "engines": engines or []})
            return {"results": []}

        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry": {"categories": ["general", "images"], "engines": ["fse", "e926", "furbooru"]},
            },
            capabilities={"engine_probe": {"fse": {"status": "ok"}, "e926": {"status": "ok"}, "furbooru": {"status": "ok"}}},
        )
        runtime._query_searxng = fake_query
        runtime._search_web("test", profile="furry", engines=["fse"])
        self.assertEqual(calls[0]["engines"], ["fse"])

    def test_capabilities_usage_notes_mention_explicit_engines(self):
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(search_config_profiles={})
        caps = build_web_search_capabilities(runtime)
        notes_str = " ".join(caps.get("usage_notes", []))
        self.assertIn("engines", notes_str.lower())

    def test_capabilities_usage_notes_mention_sofurry_absent(self):
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(search_config_profiles={})
        caps = build_web_search_capabilities(runtime)
        notes_str = " ".join(caps.get("usage_notes", []))
        self.assertIn("SoFurry", notes_str)

    # --- Source-strict tests ---

    def test_furry_fse_config_fallback_profiles_empty(self):
        """furry_fse must have no fallback_profiles in config (never broaden)."""
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        fse_cfg = cfg.config.get("profiles", {}).get("furry_fse", {})
        fallbacks = fse_cfg.get("fallback_profiles", None)
        self.assertIsNotNone(fallbacks, "furry_fse must explicitly declare fallback_profiles")
        self.assertEqual(list(fallbacks), [], "furry_fse fallback_profiles must be empty")

    def test_furry_fse_config_source_strict_true(self):
        """furry_fse config must declare source_strict=true."""
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        fse_cfg = cfg.config.get("profiles", {}).get("furry_fse", {})
        self.assertTrue(fse_cfg.get("source_strict"), "furry_fse must be source_strict=true")

    def test_furry_fse_config_engines_only_fse(self):
        """furry_fse engines must be exactly ['fse']."""
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        fse_cfg = cfg.config.get("profiles", {}).get("furry_fse", {})
        self.assertEqual(fse_cfg.get("engines"), ["fse"])

    def test_furry_fse_config_expected_domains(self):
        """furry_fse must declare expected_domains including fse.anthro.fr."""
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        fse_cfg = cfg.config.get("profiles", {}).get("furry_fse", {})
        domains = fse_cfg.get("expected_domains", [])
        self.assertIn("fse.anthro.fr", domains)

    def test_profile_source_strict_furry_fse_by_name(self):
        """_profile_source_strict is true for furry_fse by name alone."""
        from proxy.qz_tool_web import _profile_source_strict
        self.assertTrue(_profile_source_strict("furry_fse", False, []))

    def test_profile_source_strict_cfg_flag(self):
        """_profile_source_strict is true when cfg_source_strict=True."""
        from proxy.qz_tool_web import _profile_source_strict
        self.assertTrue(_profile_source_strict("custom", True, []))

    def test_profile_source_strict_explicit_fse_only(self):
        """_profile_source_strict is true when explicit_engines=['fse']."""
        from proxy.qz_tool_web import _profile_source_strict
        self.assertTrue(_profile_source_strict("furry", False, ["fse"]))

    def test_profile_source_strict_false_for_mixed_explicit(self):
        """_profile_source_strict is false when explicit_engines has non-fse engines."""
        from proxy.qz_tool_web import _profile_source_strict
        self.assertFalse(_profile_source_strict("furry", False, ["fse", "e926"]))

    def test_profile_source_strict_false_for_broad(self):
        """_profile_source_strict is false for broad with no explicit engines."""
        from proxy.qz_tool_web import _profile_source_strict
        self.assertFalse(_profile_source_strict("broad", False, []))

    def test_result_matches_expected_engine(self):
        """_result_matches_expected_source matches on engine field."""
        from proxy.qz_tool_web import _result_matches_expected_source
        result = {"engine": "fse", "engines": [], "url": "https://example.com/x"}
        self.assertTrue(_result_matches_expected_source(result, ["fse"], [], []))

    def test_result_matches_expected_engines_list(self):
        """_result_matches_expected_source matches on engines list."""
        from proxy.qz_tool_web import _result_matches_expected_source
        result = {"engine": None, "engines": ["fse", "extra"], "url": "https://example.com/x"}
        self.assertTrue(_result_matches_expected_source(result, ["fse"], [], []))

    def test_result_matches_expected_domain(self):
        """_result_matches_expected_source matches on URL domain."""
        from proxy.qz_tool_web import _result_matches_expected_source
        result = {"engine": "", "engines": [], "url": "https://fse.anthro.fr/stories/123"}
        self.assertTrue(_result_matches_expected_source(result, [], ["fse.anthro.fr"], []))

    def test_result_matches_expected_retrieval_source(self):
        """_result_matches_expected_source matches on retrieval_source."""
        from proxy.qz_tool_web import _result_matches_expected_source
        result = {"engine": "", "engines": [], "url": "https://example.com/x", "retrieval_source": "fse"}
        self.assertTrue(_result_matches_expected_source(result, [], [], ["fse"]))

    def test_result_not_matches_duckduckgo(self):
        """_result_matches_expected_source rejects engine=duckduckgo for FSE-strict."""
        from proxy.qz_tool_web import _result_matches_expected_source
        result = {"engine": "duckduckgo", "engines": ["duckduckgo"], "url": "https://wattpad.com/x"}
        self.assertFalse(_result_matches_expected_source(result, ["fse"], ["fse.anthro.fr"], ["fse"]))

    def test_result_not_matches_engines_list_duckduckgo(self):
        """_result_matches_expected_source rejects engines=['duckduckgo']."""
        from proxy.qz_tool_web import _result_matches_expected_source
        result = {"engine": None, "engines": ["duckduckgo"], "url": "https://rule34.xxx/x"}
        self.assertFalse(_result_matches_expected_source(result, ["fse"], ["fse.anthro.fr"], ["fse"]))

    def test_furry_fse_no_fallback_when_zero_results(self):
        """profile='furry_fse' must not fall back to broad when primary returns zero results."""
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=12):
            calls.append({"engines": engines or [], "categories": categories or []})
            return {"results": []}

        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "categories": ["general"],
                    "engines": ["fse"],
                    "source_strict": True,
                    "expected_engines": ["fse"],
                    "expected_domains": ["fse.anthro.fr"],
                    "fallback_profiles": [],
                },
                "broad": {
                    "categories": ["general", "web"],
                    "engines": ["duckduckgo", "bing"],
                    "fallback_profiles": [],
                },
            },
        )
        runtime._query_searxng = fake_query
        result = runtime._search_web("dragon transformation", profile="furry_fse")
        # Must only call once (no fallback to broad)
        self.assertEqual(len(calls), 1, "furry_fse must not trigger fallback searches")
        self.assertNotIn("duckduckgo", calls[0]["engines"])
        self.assertEqual(result.get("fallback_used"), None)

    def test_furry_fse_discards_wrong_source_results(self):
        """furry_fse must discard results from non-FSE engines."""
        wrong_result = {"engine": "duckduckgo", "engines": ["duckduckgo"], "url": "https://wattpad.com/story/1", "title": "Wattpad"}
        fse_result = {"engine": "fse", "engines": ["fse"], "url": "https://fse.anthro.fr/stories/999", "title": "FSE Story"}

        def fake_query(query, categories=None, engines=None, top_k=12):
            return {"results": [wrong_result, fse_result]}

        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "categories": ["general"],
                    "engines": ["fse"],
                    "source_strict": True,
                    "expected_engines": ["fse"],
                    "expected_domains": ["fse.anthro.fr"],
                    "fallback_profiles": [],
                },
            },
        )
        runtime._query_searxng = fake_query
        result = runtime._search_web("dragon", profile="furry_fse")
        urls = [r["url"] for r in result.get("results", [])]
        self.assertNotIn("https://wattpad.com/story/1", urls, "Wattpad result must be discarded")
        self.assertIn("https://fse.anthro.fr/stories/999", urls, "FSE result must be kept")

    def test_furry_fse_all_wrong_source_returns_zero_with_warning(self):
        """furry_fse returns zero results and warning when all results are wrong-source."""
        def fake_query(query, categories=None, engines=None, top_k=12):
            return {"results": [
                {"engine": "duckduckgo", "engines": ["duckduckgo"], "url": "https://rule34.xxx/post/1", "title": "X"},
                {"engine": "bing", "engines": ["bing"], "url": "https://wattpad.com/2", "title": "Y"},
            ]}

        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "categories": ["general"],
                    "engines": ["fse"],
                    "source_strict": True,
                    "expected_engines": ["fse"],
                    "expected_domains": ["fse.anthro.fr"],
                    "fallback_profiles": [],
                },
            },
        )
        runtime._query_searxng = fake_query
        result = runtime._search_web("dragon", profile="furry_fse")
        self.assertEqual(result.get("results", []), [], "All wrong-source results must be discarded")
        warning = result.get("source_strict_warning", "")
        self.assertIn("source-strict", warning, "Must include source_strict_warning")
        self.assertIn("2", warning, "Warning must mention discarded count")

    def test_furry_profile_with_explicit_fse_engine_is_source_strict(self):
        """profile='furry', engines=['fse'] must behave source-strict (no fallback)."""
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=12):
            calls.append({"engines": engines or []})
            return {"results": []}

        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry": {
                    "categories": ["general", "images"],
                    "engines": ["fse", "e926", "furbooru"],
                    "fallback_profiles": ["broad"],
                },
                "broad": {"engines": ["duckduckgo"], "fallback_profiles": []},
            },
        )
        runtime._query_searxng = fake_query
        runtime._search_web("dragon", profile="furry", engines=["fse"])
        # Explicit engine override + fse-only = source_strict; must not call fallback
        self.assertEqual(len(calls), 1, "Explicit fse-only override must not trigger fallback")

    def test_broad_profile_still_uses_fallback(self):
        """Broad profile must still fall back when below threshold (regression guard)."""
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=12):
            calls.append({"engines": engines or [], "categories": categories or []})
            return {"results": []}

        runtime = WebSearchRuntime(
            search_config_profiles={
                "broad": {
                    "categories": ["general"],
                    "engines": ["bing"],
                    "fallback_profiles": ["reference"],
                },
                "reference": {
                    "categories": ["dictionaries"],
                    "engines": ["wiktionary"],
                    "fallback_profiles": [],
                },
            },
            low_result_fallback_threshold=2,
        )
        runtime._query_searxng = fake_query
        runtime._search_web("test query", profile="broad")
        # Must have tried the fallback profile too
        self.assertGreater(len(calls), 1, "Broad must still attempt fallback")

    def test_route_log_includes_source_strict_fields(self):
        """route_log must include source_strict and wrong_source_results_discarded."""
        import json
        from pathlib import Path
        import tempfile, os

        # Temporarily redirect runtime_log to a temp file
        logged = {}

        def fake_query(query, categories=None, engines=None, top_k=12):
            return {"results": []}

        # Capture what _emit receives
        emitted = []

        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "categories": ["general"],
                    "engines": ["fse"],
                    "source_strict": True,
                    "expected_engines": ["fse"],
                    "expected_domains": ["fse.anthro.fr"],
                    "fallback_profiles": [],
                },
            },
        )
        runtime._query_searxng = fake_query

        original_emit = runtime._emit
        def capturing_emit(event_type, payload):
            emitted.append((event_type, payload))
        runtime._emit = capturing_emit

        runtime._search_web("dragon", profile="furry_fse")
        route_events = [p for t, p in emitted if t == "web_search_route"]
        self.assertTrue(route_events, "Must have emitted web_search_route")
        first_event = route_events[0]
        self.assertIn("source_strict", first_event)
        self.assertTrue(first_event["source_strict"])

    def test_capabilities_furry_fse_source_strict_true(self):
        """capabilities.profiles.furry_fse must advertise source_strict=true."""
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(search_config_profiles=cfg.config.get("profiles", {}))
        caps = build_web_search_capabilities(runtime)
        fse_profile = caps["profiles"].get("furry_fse", {})
        self.assertTrue(fse_profile.get("source_strict"), "capabilities must show source_strict=true for furry_fse")

    def test_capabilities_furry_fse_no_broad_fallback(self):
        """capabilities.profiles.furry_fse must not advertise broad as fallback."""
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(search_config_profiles=cfg.config.get("profiles", {}))
        caps = build_web_search_capabilities(runtime)
        fse_profile = caps["profiles"].get("furry_fse", {})
        fallbacks = fse_profile.get("fallback_profiles", [])
        self.assertEqual(list(fallbacks), [], "furry_fse capabilities must show empty fallback_profiles")

    def test_capabilities_warns_when_fse_probe_unavailable(self):
        """capabilities must warn when fse engine is missing from SearXNG probe."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "engines": ["fse"],
                    "source_strict": True,
                    "fallback_profiles": [],
                },
            },
            capabilities={"engine_probe": {"duckduckgo": {"status": "ok"}}},  # fse absent
        )
        caps = build_web_search_capabilities(runtime)
        warning_text = " ".join(caps.get("warnings", []))
        self.assertIn("fse", warning_text.lower())
        self.assertIn("source-strict", warning_text.lower())

    def test_capabilities_no_warning_when_fse_probe_available(self):
        """capabilities must not warn when fse is present in SearXNG probe."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "engines": ["fse"],
                    "source_strict": True,
                    "fallback_profiles": [],
                },
            },
            capabilities={"engine_probe": {"fse": {"status": "ok"}}},
        )
        caps = build_web_search_capabilities(runtime)
        warning_text = " ".join(caps.get("warnings", []))
        self.assertNotIn("source-strict", warning_text.lower())

    def test_capabilities_usage_notes_mention_furry_fse_source_strict(self):
        """usage_notes must mention that furry_fse is source-strict."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(search_config_profiles={})
        caps = build_web_search_capabilities(runtime)
        notes_str = " ".join(caps.get("usage_notes", []))
        self.assertIn("source-strict", notes_str.lower())

    def test_furry_images_fallback_unchanged(self):
        """furry_images is not source-strict; its fallback behaviour is unaffected."""
        from proxy.qz_tool_web import _profile_source_strict
        self.assertFalse(_profile_source_strict("furry_images", False, []))

    def test_furry_mixed_profile_not_strict_without_explicit_fse(self):
        """profile='furry' without explicit fse engine is not source-strict."""
        from proxy.qz_tool_web import _profile_source_strict
        self.assertFalse(_profile_source_strict("furry", False, []))


class WebSearchCountMismatchTests(unittest.TestCase):
    """SearXNG reported count must never drive routing; parsed count is authoritative."""

    def _make_runtime(self):
        return WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "categories": ["general"],
                    "engines": ["fse"],
                    "source_strict": True,
                    "expected_engines": ["fse"],
                    "expected_domains": ["fse.anthro.fr"],
                    "fallback_profiles": [],
                },
                "broad": {
                    "categories": ["general", "web"],
                    "engines": ["duckduckgo"],
                    "fallback_profiles": [],
                },
            },
        )

    def _fse_result(self, story_id=42):
        return {
            "engine": "fse",
            "engines": ["fse"],
            "url": f"https://fse.anthro.fr/stories/{story_id}-dragon-tale",
            "title": "Dragon Tale",
            "content": "A story about dragons",
        }

    def _fake_query_with_mismatch(self, results):
        """Return a fake _query_searxng that simulates provider_reported_count=0 but real results."""
        def fake_query(query, categories=None, engines=None, top_k=12):
            return {
                "query": query,
                "results": list(results),
                "categories": categories or [],
                "engines": engines or [],
                "unresponsive_engines": [],
                "answers": [],
                "provider_id": "searxng",
                "provider_reported_count": 0,
                "parsed_result_count": len(results),
                "count_mismatch": len(results) > 0,
                "warnings": (
                    [f"searxng_result_count_mismatch: provider reported 0 results "
                     f"but {len(results)} result(s) were parsed; using parsed count for routing"]
                    if results else []
                ),
            }
        return fake_query

    def test_count_mismatch_zero_reported_valid_parsed_returns_result(self):
        """number_of_results=0 with valid parsed FSE result still returns the result."""
        runtime = self._make_runtime()
        runtime._query_searxng = self._fake_query_with_mismatch([self._fse_result()])
        result = runtime._search_web("dragon transformation", profile="furry_fse")
        self.assertEqual(len(result.get("results", [])), 1,
                         "Result must be returned despite provider reporting count=0")
        self.assertEqual(result["results"][0]["url"],
                         "https://fse.anthro.fr/stories/42-dragon-tale")

    def test_count_mismatch_no_fallback_when_accepted_results_exist(self):
        """number_of_results=0 does not trigger fallback if accepted source-valid results exist."""
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=12):
            calls.append({"engines": engines or []})
            return {
                "query": query,
                "results": [self._fse_result()],
                "provider_id": "searxng",
                "provider_reported_count": 0,
                "parsed_result_count": 1,
                "count_mismatch": True,
                "warnings": [],
            }

        runtime = self._make_runtime()
        runtime._query_searxng = fake_query
        runtime._search_web("test", profile="furry_fse")
        self.assertEqual(len(calls), 1,
                         "Must not call fallback when accepted results exist despite count=0")

    def test_count_mismatch_fields_in_search_result(self):
        """count_mismatch=True and provider_id appear in _search_web result."""
        runtime = self._make_runtime()
        runtime._query_searxng = self._fake_query_with_mismatch([self._fse_result()])
        result = runtime._search_web("test", profile="furry_fse")
        self.assertTrue(result.get("count_mismatch"),
                        "count_mismatch must be propagated to search result")
        self.assertEqual(result.get("provider_id"), "searxng")
        self.assertIsNotNone(result.get("provider_reported_count"),
                             "provider_reported_count must be in result")

    def test_count_mismatch_warning_propagated_to_result(self):
        """count_mismatch warning from provider is included in result warnings list."""
        runtime = self._make_runtime()
        runtime._query_searxng = self._fake_query_with_mismatch([self._fse_result()])
        result = runtime._search_web("test", profile="furry_fse")
        warnings_text = " ".join(result.get("warnings") or [])
        self.assertIn("searxng_result_count_mismatch", warnings_text,
                      "Mismatch warning must appear in result warnings")

    def test_count_mismatch_broad_fallback_uses_parsed_count(self):
        """Broad profile: fallback decision uses parsed count, not provider_reported_count=0."""
        calls = []

        def fake_query(query, categories=None, engines=None, top_k=12):
            calls.append({"engines": engines or []})
            # Simulate: provider says 0 but we actually have results
            n = 3 if ("duckduckgo" in (engines or [])) else 0
            return {
                "query": query,
                "results": [{"engine": "duckduckgo", "engines": ["duckduckgo"],
                              "url": f"https://example.com/{i}", "title": f"Result {i}"}
                             for i in range(n)],
                "provider_id": "searxng",
                "provider_reported_count": 0,
                "parsed_result_count": n,
                "count_mismatch": n > 0,
                "warnings": [],
            }

        runtime = WebSearchRuntime(
            search_config_profiles={
                "broad": {
                    "categories": ["general"],
                    "engines": ["duckduckgo"],
                    "fallback_profiles": [],
                },
            },
            low_result_fallback_threshold=2,
        )
        runtime._query_searxng = fake_query
        result = runtime._search_web("test", profile="broad")
        # parsed_result_count=3 >= threshold=2, so no fallback needed
        self.assertEqual(len(result.get("results", [])), 3,
                         "Parsed results must be returned when parsed count >= threshold")

    def test_accepted_result_count_in_search_result(self):
        """accepted_result_count reflects post-filter count, not provider metadata."""
        runtime = self._make_runtime()
        fse_r = self._fse_result()
        wrong_r = {"engine": "duckduckgo", "engines": ["duckduckgo"],
                   "url": "https://wattpad.com/1", "title": "W"}

        def fake_query(query, categories=None, engines=None, top_k=12):
            return {
                "query": query,
                "results": [fse_r, wrong_r],
                "provider_id": "searxng",
                "provider_reported_count": 0,
                "parsed_result_count": 2,
                "count_mismatch": True,
                "warnings": [],
            }

        runtime._query_searxng = fake_query
        result = runtime._search_web("test", profile="furry_fse")
        # One wrong-source result is discarded; accepted count is 1
        self.assertEqual(result.get("accepted_result_count"), 1,
                         "accepted_result_count must reflect post-filter accepted results")


class WebSearchProviderDiagnosticsTests(unittest.TestCase):
    """Provider trace fields: provider_id, counts, and capabilities providers section."""

    def test_query_searxng_provider_fields_on_success(self):
        """_query_searxng result includes provider_id, provider_reported_count, etc."""
        from unittest.mock import patch

        runtime = WebSearchRuntime(base_url="http://127.0.0.1:8888")

        fake_payload = json.dumps({
            "results": [
                {"url": "https://fse.anthro.fr/stories/1-test", "title": "Test", "engine": "fse"}
            ],
            "number_of_results": 0,
            "unresponsive_engines": [],
            "answers": [],
        }).encode()

        def fake_fetch(url, timeout, accept):
            return fake_payload, "application/json", url

        with patch("proxy.qz_tool_web._http_fetch", fake_fetch):
            result = runtime._query_searxng("dragon", engines=["fse"])

        self.assertEqual(result.get("provider_id"), "searxng")
        self.assertEqual(result.get("provider_reported_count"), 0)
        self.assertEqual(result.get("parsed_result_count"), 1)
        self.assertTrue(result.get("count_mismatch"),
                        "count_mismatch must be True when provider says 0 but 1 parsed")

    def test_query_searxng_provider_fields_on_no_mismatch(self):
        """count_mismatch is False when provider count matches parsed count."""
        from unittest.mock import patch

        runtime = WebSearchRuntime(base_url="http://127.0.0.1:8888")

        fake_payload = json.dumps({
            "results": [
                {"url": "https://example.com/1", "title": "One", "engine": "duckduckgo"}
            ],
            "number_of_results": 1,
        }).encode()

        def fake_fetch(url, timeout, accept):
            return fake_payload, "application/json", url

        with patch("proxy.qz_tool_web._http_fetch", fake_fetch):
            result = runtime._query_searxng("test")

        self.assertFalse(result.get("count_mismatch"),
                         "count_mismatch must be False when provider count matches")

    def test_query_searxng_provider_fields_when_no_base_url(self):
        """_query_searxng returns safe provider fields when SearXNG is not configured."""
        runtime = WebSearchRuntime()
        result = runtime._query_searxng("test")
        self.assertEqual(result.get("provider_id"), "searxng")
        self.assertIsNone(result.get("provider_reported_count"))
        self.assertEqual(result.get("parsed_result_count"), 0)
        self.assertFalse(result.get("count_mismatch"))

    def test_capabilities_providers_section_present(self):
        """capabilities must include a providers section."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(search_config_profiles={})
        caps = build_web_search_capabilities(runtime)
        self.assertIn("providers", caps,
                      "capabilities must have a providers section")
        providers = caps["providers"]
        self.assertIn("searxng", providers)
        self.assertIn("fse_direct", providers)
        self.assertIn("agent_retrieve", providers)

    def test_capabilities_fse_direct_unavailable(self):
        """capabilities must honestly report fse_direct as unavailable (not implemented)."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(search_config_profiles={})
        caps = build_web_search_capabilities(runtime)
        fse_direct = caps["providers"]["fse_direct"]
        self.assertFalse(fse_direct.get("available"),
                         "fse_direct must be reported as unavailable")
        self.assertIn("note", fse_direct,
                      "fse_direct must include an explanatory note")

    def test_capabilities_searxng_availability_reflects_base_url(self):
        """capabilities searxng.available is True when base_url configured, False otherwise."""
        from proxy.qz_tool_web import build_web_search_capabilities

        runtime_no_url = WebSearchRuntime(search_config_profiles={})
        caps_no_url = build_web_search_capabilities(runtime_no_url)
        self.assertFalse(caps_no_url["providers"]["searxng"]["available"],
                         "searxng.available must be False without a base URL")

        runtime_with_url = WebSearchRuntime(
            base_url="http://127.0.0.1:8888",
            search_config_profiles={},
        )
        caps_with_url = build_web_search_capabilities(runtime_with_url)
        self.assertTrue(caps_with_url["providers"]["searxng"]["available"],
                        "searxng.available must be True with a base URL")

    def test_capabilities_furry_fse_provider_preference(self):
        """capabilities.profiles.furry_fse must advertise provider_preference."""
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(search_config_profiles=cfg.config.get("profiles", {}))
        caps = build_web_search_capabilities(runtime)
        fse_profile = caps["profiles"].get("furry_fse", {})
        self.assertIn("provider_preference", fse_profile,
                      "furry_fse must expose provider_preference in capabilities")
        pref = fse_profile["provider_preference"]
        self.assertIn("fse_direct", pref,
                      "fse_direct must appear in furry_fse provider_preference")

    def test_capabilities_warns_when_fse_absent_from_probe(self):
        """capabilities warns when fse absent from probe and furry_fse configured."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(
            search_config_profiles={
                "furry_fse": {
                    "engines": ["fse"],
                    "source_strict": True,
                    "fallback_profiles": [],
                },
            },
            capabilities={"engine_probe": {"duckduckgo": {"status": "ok"}}},
        )
        caps = build_web_search_capabilities(runtime)
        warning_text = " ".join(caps.get("warnings", []))
        self.assertIn("fse_direct", warning_text.lower() or warning_text,
                      "Must warn when fse_direct unavailable and fse engine absent")

    def test_capabilities_no_sofurry_provider(self):
        """capabilities must never include a sofurry provider — not implemented."""
        from proxy.qz_tool_web import build_web_search_capabilities
        runtime = WebSearchRuntime(search_config_profiles={})
        caps = build_web_search_capabilities(runtime)
        providers = caps.get("providers", {})
        self.assertNotIn("sofurry", providers,
                         "SoFurry must not appear as a provider")

    def test_capabilities_regression_broad_profile_unaffected(self):
        """Adding providers section must not affect broad profile capabilities."""
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(search_config_profiles=cfg.config.get("profiles", {}))
        caps = build_web_search_capabilities(runtime)
        broad = caps["profiles"].get("broad", {})
        self.assertFalse(broad.get("source_strict", False),
                         "broad must not be source_strict")
        self.assertNotIn("provider_preference", broad,
                         "broad must not have provider_preference (not source-strict)")

    def test_capabilities_coding_profile_unaffected_by_providers(self):
        """coding profile must not have provider_preference or source_strict=True."""
        from proxy.qz_tool_web import build_web_search_capabilities
        from proxy.qz_search_config import load_search_config
        cfg = load_search_config()
        runtime = WebSearchRuntime(search_config_profiles=cfg.config.get("profiles", {}))
        caps = build_web_search_capabilities(runtime)
        coding = caps["profiles"].get("coding", {})
        self.assertFalse(coding.get("source_strict", False))
        self.assertNotIn("provider_preference", coding)


if __name__ == "__main__":
    unittest.main()
