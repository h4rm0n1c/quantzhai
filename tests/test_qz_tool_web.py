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
