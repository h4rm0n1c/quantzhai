"""Tests for proxy/qz_braincase_retention.py — #54 Slice B.

Covers:
  parse_duration_ms (1-10)
  load_default_retention_policy (11-14)
  retention_decision_for_record core decisions (15-25)
  safety overrides (26-34)
  edge cases (35-42)
  match field coverage (43-51)
  fixture relationship (52-54)
"""
import copy
import json
import pathlib
import time
import unittest

from proxy.qz_braincase_retention import (
    _ALLOWED_ACTIONS,
    _FORBIDDEN_ACTIONS,
    load_default_retention_policy,
    parse_duration_ms,
    retention_decision_for_record,
)

_REPO = pathlib.Path(__file__).resolve().parents[1]
_FIXTURE_DIR = _REPO / "docs" / "fixtures" / "braincase" / "retention"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MS_PER_HOUR = 3_600_000
_MS_PER_DAY = 86_400_000


def _now():
    return int(time.time() * 1000)


def _record(
    record_id="rec_test",
    status="candidate",
    retention="ephemeral",
    tier="working_state",
    record_type="recent_topic",
    visibility="internal",
    memory_domain="coding",
    importance=0.5,
    source_refs=None,
    age_days=0,
    now_ms=None,
):
    if now_ms is None:
        now_ms = _now()
    ts = now_ms - age_days * _MS_PER_DAY
    return {
        "record_id": record_id,
        "status": status,
        "retention": retention,
        "tier": tier,
        "record_type": record_type,
        "visibility": visibility,
        "memory_domain": memory_domain,
        "importance": importance,
        "source_refs": source_refs if source_refs is not None else [],
        "created_at_ms": ts,
        "updated_at_ms": ts,
    }


def _policy():
    return load_default_retention_policy()


# ---------------------------------------------------------------------------
# 1-10: parse_duration_ms
# ---------------------------------------------------------------------------

class ParseDurationMsTests(unittest.TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(parse_duration_ms(None))

    def test_seconds_parses(self):
        self.assertEqual(parse_duration_ms("60s"), 60_000)

    def test_minutes_parses(self):
        self.assertEqual(parse_duration_ms("15m"), 15 * 60_000)

    def test_hours_parses(self):
        self.assertEqual(parse_duration_ms("24h"), 24 * 3_600_000)

    def test_days_parses(self):
        self.assertEqual(parse_duration_ms("7d"), 7 * 86_400_000)

    def test_whitespace_stripped(self):
        self.assertEqual(parse_duration_ms("  7d  "), 7 * 86_400_000)

    def test_invalid_string_returns_none(self):
        self.assertIsNone(parse_duration_ms("garbage"))

    def test_unsupported_unit_returns_none(self):
        self.assertIsNone(parse_duration_ms("7w"))
        self.assertIsNone(parse_duration_ms("30y"))

    def test_negative_duration_returns_none(self):
        self.assertIsNone(parse_duration_ms("-7d"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_duration_ms(""))
        self.assertIsNone(parse_duration_ms("   "))

    def test_non_numeric_magnitude_returns_none(self):
        self.assertIsNone(parse_duration_ms("xd"))
        self.assertIsNone(parse_duration_ms("1.5d"))

    def test_large_value_parses(self):
        self.assertEqual(parse_duration_ms("365d"), 365 * 86_400_000)

    def test_zero_duration(self):
        self.assertEqual(parse_duration_ms("0d"), 0)


# ---------------------------------------------------------------------------
# 11-14: load_default_retention_policy
# ---------------------------------------------------------------------------

class LoadDefaultPolicyTests(unittest.TestCase):

    def test_loads_successfully(self):
        policy = load_default_retention_policy()
        self.assertIsInstance(policy, dict)

    def test_schema_field_correct(self):
        policy = load_default_retention_policy()
        self.assertEqual(policy.get("schema"), "braincase/retention-policy@1")

    def test_has_rules(self):
        policy = load_default_retention_policy()
        self.assertIsInstance(policy.get("rules"), list)
        self.assertGreater(len(policy["rules"]), 0)

    def test_fresh_copy_each_call(self):
        p1 = load_default_retention_policy()
        p1["rules"].clear()
        p2 = load_default_retention_policy()
        self.assertGreater(len(p2["rules"]), 0,
                           "Mutating first return must not affect second load")


# ---------------------------------------------------------------------------
# 15-25: core evaluator decisions
# ---------------------------------------------------------------------------

class EvaluatorCoreTests(unittest.TestCase):

    def _decide(self, age_days, status, retention, **kwargs):
        now_ms = _now()
        rec = _record(
            status=status, retention=retention,
            age_days=age_days, now_ms=now_ms, **kwargs
        )
        return retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())

    def test_candidate_ephemeral_under_24h_keep(self):
        now_ms = _now()
        rec = _record(status="candidate", retention="ephemeral",
                      age_days=0, now_ms=now_ms)
        # Age just 1 hour — under 24h
        rec["created_at_ms"] = now_ms - _MS_PER_HOUR
        rec["updated_at_ms"] = now_ms - _MS_PER_HOUR
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        self.assertEqual(result["action"], "keep")

    def test_candidate_ephemeral_2d_stale(self):
        result = self._decide(2, "candidate", "ephemeral")
        self.assertEqual(result["action"], "stale")

    def test_candidate_ephemeral_8d_retire(self):
        result = self._decide(8, "candidate", "ephemeral")
        self.assertEqual(result["action"], "retire")

    def test_candidate_session_15d_retire(self):
        result = self._decide(15, "candidate", "session")
        self.assertEqual(result["action"], "retire")

    def test_candidate_project_20d_stale(self):
        result = self._decide(20, "candidate", "project")
        self.assertEqual(result["action"], "stale")

    def test_candidate_project_70d_retire(self):
        result = self._decide(70, "candidate", "project")
        self.assertEqual(result["action"], "retire")

    def test_candidate_durable_40d_stale_not_retire(self):
        result = self._decide(40, "candidate", "durable")
        self.assertEqual(result["action"], "stale",
                         "Durable candidate must not be retired by age; stale only")

    def test_active_ephemeral_8d_retire(self):
        result = self._decide(8, "active", "ephemeral")
        self.assertEqual(result["action"], "retire")

    def test_active_session_10d_stale(self):
        result = self._decide(10, "active", "session")
        self.assertEqual(result["action"], "stale")

    def test_active_project_100d_stale(self):
        result = self._decide(100, "active", "project")
        self.assertEqual(result["action"], "stale")

    def test_active_durable_365d_keep(self):
        result = self._decide(365, "active", "durable")
        self.assertEqual(result["action"], "keep",
                         "Active durable must never be retired by age")

    def test_result_has_required_fields(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        for field in ("record_id", "action", "reason", "age_ms",
                      "age_since_update_ms", "matched_rule", "dry_run", "warnings"):
            self.assertIn(field, result)

    def test_dry_run_always_true(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        self.assertTrue(result["dry_run"])


# ---------------------------------------------------------------------------
# 26-34: safety overrides
# ---------------------------------------------------------------------------

class SafetyOverrideTests(unittest.TestCase):

    def _malicious_policy(self, action: str) -> dict:
        return {
            "schema": "braincase/retention-policy@1",
            "default_action": "keep",
            "rules": [
                {
                    "name": "malicious_rule",
                    "match": {"status": "candidate", "retention": "ephemeral"},
                    "stale_after": None,
                    "expire_after": "1s",
                    "action": action,
                    "reason": f"malicious_{action}",
                }
            ],
        }

    def _old_candidate(self):
        now_ms = _now()
        rec = _record(status="candidate", retention="ephemeral",
                      age_days=30, now_ms=now_ms)
        return rec, now_ms

    def test_delete_action_becomes_keep(self):
        rec, now_ms = self._old_candidate()
        result = retention_decision_for_record(rec, now_ms=now_ms,
                                               policy=self._malicious_policy("delete"))
        self.assertEqual(result["action"], "keep")
        self.assertTrue(any("forbidden_action" in w or "delete" in w
                            for w in result["warnings"]))

    def test_promote_action_becomes_keep(self):
        rec, now_ms = self._old_candidate()
        result = retention_decision_for_record(rec, now_ms=now_ms,
                                               policy=self._malicious_policy("promote"))
        self.assertEqual(result["action"], "keep")

    def test_activate_action_becomes_keep(self):
        rec, now_ms = self._old_candidate()
        result = retention_decision_for_record(rec, now_ms=now_ms,
                                               policy=self._malicious_policy("activate"))
        self.assertEqual(result["action"], "keep")

    def test_render_action_becomes_keep(self):
        rec, now_ms = self._old_candidate()
        result = retention_decision_for_record(rec, now_ms=now_ms,
                                               policy=self._malicious_policy("render"))
        self.assertEqual(result["action"], "keep")

    def test_candidate_cannot_be_promoted_by_malicious_policy(self):
        rec, now_ms = self._old_candidate()
        policy = self._malicious_policy("promote")
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertNotEqual(result["action"], "promote")
        self.assertNotEqual(result["action"], "activate")

    def test_active_durable_cannot_be_retired_by_age_rule(self):
        """Malicious policy with retire action cannot retire active durable records."""
        now_ms = _now()
        rec = _record(status="active", retention="durable", age_days=365,
                      now_ms=now_ms)
        malicious = {
            "schema": "braincase/retention-policy@1",
            "default_action": "keep",
            "rules": [
                {
                    "name": "malicious_durable_retire",
                    "match": {"status": "active", "retention": "durable"},
                    "stale_after": None,
                    "expire_after": "1s",
                    "action": "retire",
                    "reason": "malicious_retire_durable",
                }
            ],
        }
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=malicious)
        self.assertEqual(result["action"], "keep",
                         "Active durable must always be kept; hard override")

    def test_action_always_in_allowed_set(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        for action in ("delete", "promote", "activate", "render", "garbage",
                       "keep", "stale", "retire"):
            policy = {
                "schema": "braincase/retention-policy@1",
                "default_action": "keep",
                "rules": [
                    {"name": "x", "match": {}, "stale_after": None,
                     "expire_after": "1s", "action": action, "reason": "x"}
                ],
            }
            result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
            self.assertIn(result["action"], _ALLOWED_ACTIONS,
                          f"action={action!r} must produce an allowed result")

    def test_evaluator_does_not_mutate_record(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        original = copy.deepcopy(rec)
        retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        self.assertEqual(rec, original, "record must not be mutated")

    def test_evaluator_does_not_mutate_policy(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        policy = _policy()
        original_rules_len = len(policy["rules"])
        retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(len(policy["rules"]), original_rules_len,
                         "policy must not be mutated")


# ---------------------------------------------------------------------------
# 35-42: edge cases
# ---------------------------------------------------------------------------

class EdgeCaseTests(unittest.TestCase):

    def test_no_matching_rule_returns_default_keep(self):
        now_ms = _now()
        rec = _record(status="candidate", retention="ephemeral", age_days=5,
                      now_ms=now_ms)
        empty_policy = {"schema": "braincase/retention-policy@1",
                        "default_action": "keep", "rules": []}
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=empty_policy)
        self.assertEqual(result["action"], "keep")
        self.assertEqual(result["reason"], "no_matching_rule")
        self.assertIsNone(result["matched_rule"])

    def test_invalid_default_action_becomes_keep_with_warning(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        policy = {"schema": "braincase/retention-policy@1",
                  "default_action": "delete", "rules": []}
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["action"], "keep")
        self.assertTrue(any("delete" in w or "invalid_default_action" in w
                            for w in result["warnings"]))

    def test_missing_created_at_ms_returns_keep_with_warning(self):
        now_ms = _now()
        rec = {
            "record_id": "rec_no_ts",
            "status": "candidate",
            "retention": "ephemeral",
            "updated_at_ms": now_ms - 5 * _MS_PER_DAY,
        }
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        # No created_at_ms but has updated_at_ms — can still evaluate thresholds
        # created_at warning should be present
        self.assertTrue(any("created_at_ms" in w or "missing" in w
                            for w in result["warnings"]))

    def test_missing_updated_at_ms_returns_keep_with_warning(self):
        now_ms = _now()
        rec = {
            "record_id": "rec_no_update_ts",
            "status": "candidate",
            "retention": "ephemeral",
            "created_at_ms": now_ms - 5 * _MS_PER_DAY,
        }
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        self.assertEqual(result["action"], "keep")
        self.assertEqual(result["reason"], "missing_or_invalid_timestamp")

    def test_future_updated_at_ms_returns_keep_with_warning(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        rec["updated_at_ms"] = now_ms + _MS_PER_DAY  # future timestamp
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        self.assertEqual(result["action"], "keep")
        self.assertTrue(any("future" in w or "invalid" in w
                            for w in result["warnings"]))

    def test_invalid_policy_returns_keep_with_warning(self):
        now_ms = _now()
        rec = _record(age_days=5, now_ms=now_ms)
        result = retention_decision_for_record(rec, now_ms=now_ms, policy="not_a_dict")
        self.assertEqual(result["action"], "keep")
        self.assertIn("invalid_policy", result["warnings"])

    def test_invalid_record_returns_keep_with_warning(self):
        now_ms = _now()
        result = retention_decision_for_record("not_a_dict", now_ms=now_ms,
                                               policy=_policy())
        self.assertEqual(result["action"], "keep")
        self.assertIn("invalid_record", result["warnings"])

    def test_invalid_duration_in_rule_handled_safely(self):
        now_ms = _now()
        rec = _record(status="candidate", retention="ephemeral",
                      age_days=30, now_ms=now_ms)
        policy_bad_dur = {
            "schema": "braincase/retention-policy@1",
            "default_action": "keep",
            "rules": [
                {
                    "name": "bad_duration_rule",
                    "match": {"status": "candidate", "retention": "ephemeral"},
                    "stale_after": "INVALID",
                    "expire_after": "ALSO_BAD",
                    "action": "retire",
                    "reason": "test",
                }
            ],
        }
        result = retention_decision_for_record(rec, now_ms=now_ms,
                                               policy=policy_bad_dur)
        # Invalid durations: both parse to None → keep (within window)
        self.assertEqual(result["action"], "keep")
        self.assertTrue(any("invalid_duration" in w for w in result["warnings"]))

    def test_retired_record_returns_already_inactive(self):
        now_ms = _now()
        rec = _record(status="retired", retention="ephemeral",
                      age_days=0, now_ms=now_ms)
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        self.assertEqual(result["action"], "keep")
        self.assertEqual(result["reason"], "already_inactive")

    def test_superseded_record_returns_already_inactive(self):
        now_ms = _now()
        rec = _record(status="superseded", retention="project",
                      age_days=0, now_ms=now_ms)
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=_policy())
        self.assertEqual(result["action"], "keep")
        self.assertEqual(result["reason"], "already_inactive")


# ---------------------------------------------------------------------------
# 43-51: match field coverage
# ---------------------------------------------------------------------------

class MatchFieldTests(unittest.TestCase):

    def _policy_with_rule(self, match: dict, stale_after=None,
                          expire_after="1s", action="retire") -> dict:
        return {
            "schema": "braincase/retention-policy@1",
            "default_action": "keep",
            "rules": [
                {
                    "name": "field_match_test",
                    "match": match,
                    "stale_after": stale_after,
                    "expire_after": expire_after,
                    "action": action,
                    "reason": "field_match_test",
                }
            ],
        }

    def test_memory_domain_exact_match(self):
        now_ms = _now()
        rec = _record(memory_domain="coding", age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"memory_domain": "coding"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "field_match_test")

    def test_memory_domain_no_match(self):
        now_ms = _now()
        rec = _record(memory_domain="hsm", age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"memory_domain": "coding"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertNotEqual(result["matched_rule"], "field_match_test")

    def test_tier_match(self):
        now_ms = _now()
        rec = _record(tier="project_state", age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"tier": "project_state"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "field_match_test")

    def test_record_type_match(self):
        now_ms = _now()
        rec = _record(record_type="constraint", age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"record_type": "constraint"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "field_match_test")

    def test_visibility_match(self):
        now_ms = _now()
        rec = _record(visibility="internal", age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"visibility": "internal"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "field_match_test")

    def test_min_importance_match(self):
        now_ms = _now()
        rec = _record(importance=0.8, age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"min_importance": 0.7})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "field_match_test")

    def test_min_importance_no_match(self):
        now_ms = _now()
        rec = _record(importance=0.3, age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"min_importance": 0.7})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertNotEqual(result["matched_rule"], "field_match_test")

    def test_max_importance_match(self):
        now_ms = _now()
        rec = _record(importance=0.2, age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"max_importance": 0.3})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "field_match_test")

    def test_source_refs_required_matches_nonempty(self):
        now_ms = _now()
        rec = _record(source_refs=["sref_001"], age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"source_refs_required": True})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "field_match_test")

    def test_source_refs_required_does_not_match_empty(self):
        now_ms = _now()
        rec = _record(source_refs=[], age_days=5, now_ms=now_ms)
        policy = self._policy_with_rule({"source_refs_required": True})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertNotEqual(result["matched_rule"], "field_match_test")


# ---------------------------------------------------------------------------
# 52-54: fixture relationship
# ---------------------------------------------------------------------------

class FixtureRelationshipTests(unittest.TestCase):

    def test_sample_decisions_parses(self):
        data = json.loads((_FIXTURE_DIR / "sample-decisions.json").read_text())
        self.assertIsInstance(data, list)

    def test_sample_decisions_actions_are_allowed(self):
        data = json.loads((_FIXTURE_DIR / "sample-decisions.json").read_text())
        for d in data:
            self.assertIn(d.get("action"), _ALLOWED_ACTIONS,
                          f"Sample decision action must be in {_ALLOWED_ACTIONS}")

    def test_sample_decisions_are_illustrative_keep_stale_retire(self):
        data = json.loads((_FIXTURE_DIR / "sample-decisions.json").read_text())
        actions = {d.get("action") for d in data}
        self.assertIn("keep", actions)
        self.assertIn("stale", actions)
        self.assertIn("retire", actions)



# ---------------------------------------------------------------------------
# Slice B.1: unknown match field hardening
# ---------------------------------------------------------------------------

class UnknownMatchFieldTests(unittest.TestCase):
    """Unknown match fields must not cause a rule to match (fail closed)."""

    def _policy_with_match(self, match: dict) -> dict:
        return {
            "schema": "braincase/retention-policy@1",
            "default_action": "keep",
            "rules": [
                {
                    "name": "typo_rule",
                    "match": match,
                    "stale_after": None,
                    "expire_after": "1s",
                    "action": "retire",
                    "reason": "typo_rule_fired",
                }
            ],
        }

    def test_typo_memory_domian_does_not_match(self):
        now_ms = _now()
        rec = _record(memory_domain="coding", age_days=5, now_ms=now_ms)
        policy = self._policy_with_match({"memory_domian": "coding"})  # typo
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertNotEqual(result["matched_rule"], "typo_rule",
                            "Typo 'memory_domian' must not cause rule to match")
        self.assertEqual(result["action"], "keep")

    def test_typo_retentoin_does_not_match(self):
        now_ms = _now()
        rec = _record(retention="ephemeral", age_days=5, now_ms=now_ms)
        policy = self._policy_with_match({"retentoin": "ephemeral"})  # typo
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertNotEqual(result["matched_rule"], "typo_rule")
        self.assertEqual(result["action"], "keep")

    def test_malformed_field_does_not_cause_broad_retirement(self):
        """A rule with an unknown field must not retire records it shouldn't touch."""
        now_ms = _now()
        rec = _record(status="active", retention="durable", age_days=365,
                      now_ms=now_ms)
        # Try to retire durable active via a typo rule that would otherwise match
        policy = self._policy_with_match({"statuz": "active", "retentoin": "durable"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["action"], "keep")
        self.assertNotEqual(result["reason"], "typo_rule_fired")

    def test_only_typo_rule_returns_default_keep(self):
        """When the only rule has a typo and does not match, default_action=keep applies."""
        now_ms = _now()
        rec = _record(status="candidate", retention="ephemeral",
                      age_days=30, now_ms=now_ms)
        policy = self._policy_with_match({"memory_domian": "coding"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["action"], "keep")
        self.assertEqual(result["reason"], "no_matching_rule")

    def test_valid_rule_still_matches(self):
        """A correctly spelt rule still matches after the hardening."""
        now_ms = _now()
        rec = _record(memory_domain="coding", age_days=5, now_ms=now_ms)
        policy = self._policy_with_match({"memory_domain": "coding"})
        result = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        self.assertEqual(result["matched_rule"], "typo_rule",
                         "Correctly spelt rule must still match")


if __name__ == "__main__":
    unittest.main()
