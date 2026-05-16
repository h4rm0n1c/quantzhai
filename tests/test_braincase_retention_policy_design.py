"""Structural design tests for #54 Slice A: BrainCase retention policy matrix.

These tests validate the retention policy fixture files and design doc invariants.
No runtime pruning is implemented. No DB writes occur.

Invariants this file protects:
- Policy fixture parses as valid JSON.
- Schema field is braincase/retention-policy@1.
- Rules list includes required named rules.
- No rule has action=delete or action=promote.
- No rule defines a memory_domain enum/registry.
- Actions are from the allowed set: keep, stale, retire.
- Duration fields are strings (e.g. '24h', '7d') or null.
- Sample-decisions fixture parses and covers keep/stale/retire.
- Design doc mentions no automatic ingestion, no raw DELETE, operator CLI.
- Design doc mentions retention is not HSM lifecycle.
- BrainCaseDB does not own policy/domain authority.
"""
import json
import pathlib
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_FIXTURE_DIR = _REPO / "docs" / "fixtures" / "braincase" / "retention"
_DOC_PATH = _REPO / "docs" / "braincase-retention-policy.md"

_ALLOWED_ACTIONS = frozenset({"keep", "stale", "retire"})
_FORBIDDEN_ACTIONS = frozenset({"delete", "promote", "activate", "render"})


def _load_policy() -> dict:
    return json.loads((_FIXTURE_DIR / "default-policy.json").read_text())


def _load_decisions() -> list:
    return json.loads((_FIXTURE_DIR / "sample-decisions.json").read_text())


# ---------------------------------------------------------------------------
# Policy fixture parsing and structure
# ---------------------------------------------------------------------------

class PolicyFixtureParseTests(unittest.TestCase):

    def test_default_policy_parses(self):
        data = _load_policy()
        self.assertIsInstance(data, dict)

    def test_schema_field_is_correct(self):
        data = _load_policy()
        self.assertEqual(data.get("schema"), "braincase/retention-policy@1")

    def test_has_rules_list(self):
        data = _load_policy()
        self.assertIn("rules", data)
        self.assertIsInstance(data["rules"], list)
        self.assertGreater(len(data["rules"]), 0)

    def test_has_default_action(self):
        data = _load_policy()
        self.assertIn("default_action", data)
        self.assertIn(data["default_action"], _ALLOWED_ACTIONS)

    def test_rules_include_candidate_ephemeral_expiry(self):
        rules = {r["name"] for r in _load_policy()["rules"]}
        self.assertIn("candidate_ephemeral_expiry", rules)

    def test_rules_include_candidate_session_expiry(self):
        rules = {r["name"] for r in _load_policy()["rules"]}
        self.assertIn("candidate_session_expiry", rules)

    def test_rules_include_active_durable_keep(self):
        rules = {r["name"] for r in _load_policy()["rules"]}
        self.assertIn("active_durable_keep", rules)

    def test_rules_include_active_ephemeral_expiry(self):
        rules = {r["name"] for r in _load_policy()["rules"]}
        self.assertIn("active_ephemeral_expiry", rules)

    def test_rules_include_active_project_stale(self):
        rules = {r["name"] for r in _load_policy()["rules"]}
        self.assertIn("active_project_stale", rules)

    def test_rules_include_candidate_durable_review_overdue(self):
        rules = {r["name"] for r in _load_policy()["rules"]}
        self.assertIn("candidate_durable_review_overdue", rules)


# ---------------------------------------------------------------------------
# Forbidden actions
# ---------------------------------------------------------------------------

class PolicyForbiddenActionTests(unittest.TestCase):

    def test_no_rule_has_delete_action(self):
        for rule in _load_policy()["rules"]:
            self.assertNotEqual(rule.get("action"), "delete",
                                f"Rule {rule['name']!r} must not have action=delete")
            self.assertNotIn("delete", str(rule.get("action", "")))

    def test_no_rule_has_promote_action(self):
        for rule in _load_policy()["rules"]:
            self.assertNotEqual(rule.get("action"), "promote",
                                f"Rule {rule['name']!r} must not have action=promote")

    def test_no_rule_has_activate_action(self):
        for rule in _load_policy()["rules"]:
            self.assertNotEqual(rule.get("action"), "activate",
                                f"Rule {rule['name']!r} must not have action=activate")

    def test_all_rule_actions_in_allowed_set(self):
        for rule in _load_policy()["rules"]:
            action = rule.get("action")
            self.assertIn(action, _ALLOWED_ACTIONS,
                          f"Rule {rule['name']!r} action {action!r} not in {_ALLOWED_ACTIONS}")

    def test_no_forbidden_action_in_entire_fixture(self):
        fixture_str = (_FIXTURE_DIR / "default-policy.json").read_text()
        for forbidden in _FORBIDDEN_ACTIONS:
            # Only check as a standalone action value, not substrings
            import re
            pattern = rf'"action"\s*:\s*"{forbidden}"'
            self.assertIsNone(
                re.search(pattern, fixture_str),
                f"Forbidden action {forbidden!r} must not appear in policy fixture"
            )


# ---------------------------------------------------------------------------
# memory_domain: no registry/enum
# ---------------------------------------------------------------------------

class PolicyDomainTests(unittest.TestCase):

    def test_no_memory_domain_enum_in_rules(self):
        """memory_domain in rules is a match value, not a registry enum."""
        for rule in _load_policy()["rules"]:
            match = rule.get("match", {})
            # memory_domain may appear as a match string — that is fine
            # What must not exist is a list/enum of domains inside rule structure
            if "memory_domain" in match:
                self.assertIsInstance(match["memory_domain"], str,
                                      f"Rule {rule['name']!r}: memory_domain match must be a string, not a list or enum")

    def test_no_top_level_domain_registry(self):
        """Policy fixture must not have a top-level domain registry."""
        data = _load_policy()
        self.assertNotIn("memory_domain_registry", data)
        self.assertNotIn("allowed_memory_domains", data)
        self.assertNotIn("domain_enum", data)


# ---------------------------------------------------------------------------
# Duration fields
# ---------------------------------------------------------------------------

class PolicyDurationTests(unittest.TestCase):

    def _check_duration(self, value, field_name: str):
        if value is None:
            return  # null is valid (no expiry)
        self.assertIsInstance(value, str,
                              f"{field_name} must be a string like '24h' or '7d', not {type(value)}")
        import re
        self.assertTrue(
            re.match(r'^\d+[hd]$', value),
            f"{field_name}={value!r} must match pattern NNh or NNd"
        )

    def test_stale_after_is_string_or_null(self):
        for rule in _load_policy()["rules"]:
            self._check_duration(rule.get("stale_after"), f"{rule['name']}.stale_after")

    def test_expire_after_is_string_or_null(self):
        for rule in _load_policy()["rules"]:
            self._check_duration(rule.get("expire_after"), f"{rule['name']}.expire_after")

    def test_active_durable_has_null_thresholds(self):
        """Durable active records must have null expiry — never auto-retire by age."""
        rules_by_name = {r["name"]: r for r in _load_policy()["rules"]}
        durable = rules_by_name.get("active_durable_keep", {})
        self.assertIsNone(durable.get("stale_after"),
                          "active_durable_keep must have stale_after=null")
        self.assertIsNone(durable.get("expire_after"),
                          "active_durable_keep must have expire_after=null")


# ---------------------------------------------------------------------------
# Sample decisions fixture
# ---------------------------------------------------------------------------

class SampleDecisionsTests(unittest.TestCase):

    def test_sample_decisions_parses(self):
        data = _load_decisions()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_decisions_include_keep(self):
        actions = {d["action"] for d in _load_decisions()}
        self.assertIn("keep", actions)

    def test_decisions_include_stale(self):
        actions = {d["action"] for d in _load_decisions()}
        self.assertIn("stale", actions)

    def test_decisions_include_retire(self):
        actions = {d["action"] for d in _load_decisions()}
        self.assertIn("retire", actions)

    def test_no_decision_action_deletes(self):
        for d in _load_decisions():
            self.assertNotEqual(d.get("action"), "delete")

    def test_no_decision_action_promotes(self):
        for d in _load_decisions():
            self.assertNotEqual(d.get("action"), "promote")

    def test_decisions_have_required_fields(self):
        required = {"record_id", "action", "reason", "age_ms", "matched_rule", "dry_run"}
        for d in _load_decisions():
            for field in required:
                self.assertIn(field, d, f"Decision missing field {field!r}: {d}")

    def test_dry_run_is_true_in_samples(self):
        """Sample decisions are all dry-run (no writes)."""
        for d in _load_decisions():
            self.assertTrue(d.get("dry_run"),
                            "Sample decisions must all be dry_run=true (no DB writes)")


# ---------------------------------------------------------------------------
# Design doc content invariants
# ---------------------------------------------------------------------------

class DesignDocTests(unittest.TestCase):

    def setUp(self):
        self.doc = _DOC_PATH.read_text()

    def test_doc_mentions_no_automatic_ingestion(self):
        self.assertIn("automatic ingestion", self.doc.lower())
        # Should appear in a prohibition context
        lower = self.doc.lower()
        self.assertTrue(
            "no automatic ingestion" in lower or
            "not automatic ingestion" in lower,
            "Doc must say no automatic ingestion"
        )

    def test_doc_mentions_no_raw_delete(self):
        lower = self.doc.lower()
        self.assertTrue(
            "no raw delete" in lower or
            "not raw delete" in lower or
            "no raw delete" in lower,
            "Doc must prohibit raw DELETE"
        )

    def test_doc_mentions_operator_cli(self):
        self.assertIn("operator", self.doc.lower())
        self.assertTrue(
            "operator cli" in self.doc.lower() or
            "prune" in self.doc.lower(),
            "Doc must mention operator CLI / prune"
        )

    def test_doc_says_pruning_is_explicit(self):
        lower = self.doc.lower()
        self.assertTrue(
            "explicit" in lower,
            "Doc must say pruning is explicit (not automatic)"
        )

    def test_doc_says_retention_is_not_hsm(self):
        lower = self.doc.lower()
        self.assertTrue(
            "not hsm" in lower or
            "is not hsm" in lower,
            "Doc must say this is not HSM lifecycle"
        )

    def test_doc_mentions_braincase_does_not_own_policy(self):
        lower = self.doc.lower()
        self.assertTrue(
            "policy is operator" in lower or
            "operator/config" in lower or
            "not braincase" in lower or
            "braincase does not" in lower.replace("\n", " ") or
            "not db-owned" in lower,
            "Doc must say BrainCaseDB does not own lifecycle policy"
        )

    def test_doc_mentions_no_promotion(self):
        lower = self.doc.lower()
        self.assertTrue(
            "no automatic promotion" in lower or
            "not promote" in lower or
            "must not become active" in lower,
            "Doc must say candidates are not auto-promoted by retention policy"
        )

    def test_doc_mentions_revisions_preserved(self):
        lower = self.doc.lower()
        self.assertTrue(
            "revision" in lower or "retire_state_record" in lower,
            "Doc must mention revision history is preserved"
        )

    def test_doc_mentions_limbicore_not_hsm(self):
        self.assertIn("limbicore", self.doc.lower())
        lower = self.doc.lower()
        self.assertTrue(
            "not hsm" in lower or "is not hsm" in lower,
            "Doc must distinguish LimbiCore retention from HSM lifecycle"
        )

    def test_doc_mentions_memory_domain_not_registry(self):
        lower = self.doc.lower()
        self.assertTrue(
            "registry" in lower,
            "Doc must mention memory_domain is not a registry (prohibition)"
        )


# ---------------------------------------------------------------------------
# No new model-facing tools
# ---------------------------------------------------------------------------

class NoNewToolsTests(unittest.TestCase):

    def test_no_braincase_prune_in_tool_definitions(self):
        from proxy.qz_braincase_tools import (
            get_braincase_tool_definitions,
            QZ_BRAINCASE_TOOLS_ENABLED_ENV,
            QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV,
        )
        env = {
            QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true",
            QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV: "true",
        }
        names = [d["name"] for d in get_braincase_tool_definitions(env=env)]
        for forbidden in ("braincase.prune", "braincase.retain",
                          "braincase.expire", "braincase.promote_candidate",
                          "braincase.write", "braincase.update",
                          "braincase.search", "braincase.inspect"):
            self.assertNotIn(forbidden, names,
                             f"{forbidden!r} must not be a model-facing tool")

    def test_policy_fixture_has_no_model_facing_tool_definitions(self):
        """The policy fixture is operator config, not a model tool definition."""
        data = _load_policy()
        self.assertNotIn("type", data,
                         "Policy fixture must not look like a function tool def")
        self.assertNotIn("parameters", data,
                         "Policy fixture must not have tool schema parameters")


if __name__ == "__main__":
    unittest.main()
