#!/usr/bin/env python3
import unittest

from proxy.qz_model_router import ModelRouter
from proxy.qz_request_router import (
    normalize_reasoning_stream_format,
    profile_reasoning_stream_format,
)


class ProfilePolicyTests(unittest.TestCase):
    def test_reasoning_stream_format_uses_profile_override(self):
        selected = {"overrides": {"reasoning_stream_format": "hidden"}}

        self.assertEqual(profile_reasoning_stream_format(selected, "summary"), "hidden")

    def test_reasoning_stream_format_supports_legacy_hide_flag(self):
        selected = {"overrides": {"hide_reasoning_stream": True}}

        self.assertEqual(profile_reasoning_stream_format(selected, "summary"), "hidden")

    def test_invalid_reasoning_stream_format_falls_back_to_proxy_default(self):
        selected = {"overrides": {"reasoning_stream_format": "sideways"}}

        self.assertEqual(profile_reasoning_stream_format(selected, "summary"), "summary")
        self.assertEqual(normalize_reasoning_stream_format("nope", "hidden"), "hidden")

    def test_profile_can_lock_default_reasoning_level_against_client_request(self):
        router = ModelRouter(None)
        selected = {
            "default_reasoning_level": "low",
            "overrides": {
                "default_reasoning_level": "low",
                "allow_client_reasoning_override": False,
            },
        }

        policy = router.selected_reasoning_policy(selected, {"reasoning": {"effort": "high"}})

        self.assertEqual(policy["effort"], "low")
        self.assertFalse(policy["client_override_allowed"])
        self.assertEqual(policy["default_effort"], "low")

    def test_profile_allows_client_reasoning_override_by_default(self):
        router = ModelRouter(None)
        selected = {
            "default_reasoning_level": "low",
            "overrides": {"default_reasoning_level": "low"},
        }

        policy = router.selected_reasoning_policy(selected, {"reasoning": {"effort": "high"}})

        self.assertEqual(policy["effort"], "high")
        self.assertTrue(policy["client_override_allowed"])
        self.assertEqual(policy["default_effort"], "low")


if __name__ == "__main__":
    unittest.main()
