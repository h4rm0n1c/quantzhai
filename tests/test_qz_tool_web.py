import json
import unittest

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
        from proxy.qz_tool_web import WEB_SEARCH_MAX_SEARCHES, WEB_SEARCH_MAX_OPENS, WEB_SEARCH_MAX_RESULTS
        runtime = WebSearchRuntime()
        self.assertEqual(runtime.max_searches_per_turn, WEB_SEARCH_MAX_SEARCHES)
        self.assertEqual(runtime.max_page_opens_per_turn, WEB_SEARCH_MAX_OPENS)
        self.assertEqual(runtime.max_results_per_query, WEB_SEARCH_MAX_RESULTS)

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

    def test_top_k_clamped_to_max_results_per_query(self):
        """top_k from args cannot exceed max_results_per_query."""
        runtime = WebSearchRuntime(max_results_per_query=3)
        args = runtime._parse_web_search_arguments(
            '{"action":"search","query":"q","top_k":999}'
        )
        self.assertEqual(args["top_k"], 3)

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

    def test_max_results_clamped_to_safe_ceiling(self):
        """max_results_per_query cannot exceed WEB_SEARCH_MAX_RESULTS even when configured higher."""
        from proxy.qz_tool_web import WEB_SEARCH_MAX_RESULTS
        runtime = WebSearchRuntime(max_results_per_query=9999)
        self.assertEqual(runtime.max_results_per_query, WEB_SEARCH_MAX_RESULTS)

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
        self.assertIsInstance(runtime._valid_profiles(), set)

    def test_none_search_config_profiles_treated_as_empty(self):
        runtime = WebSearchRuntime(search_config_profiles=None)
        self.assertEqual(runtime.search_config_profiles, {})


if __name__ == "__main__":
    unittest.main()
