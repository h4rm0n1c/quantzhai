"""Regression tests for sandbox / tool-failure guidance in the main agent harness.

These tests verify that key guidance phrases are present in the prompt file used
by all default profiles (prompts/codex-core-qwenified.md). They are content
guards — if the guidance is accidentally removed or diluted, the tests fail.

Tests also verify that no auto-escalation or overclaiming language was
accidentally introduced.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_HARNESS = ROOT / "prompts" / "codex-core-qwenified.md"


class SandboxGuidanceContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MAIN_HARNESS.read_text(encoding="utf-8")

    def test_harness_file_exists(self):
        self.assertTrue(MAIN_HARNESS.is_file(), f"Main harness not found: {MAIN_HARNESS}")

    def test_sandbox_section_present(self):
        self.assertIn("Sandbox and Tool Failures", self.text)

    def test_readonly_fs_string_present(self):
        """Guidance mentions the specific filesystem sandbox denial string."""
        self.assertIn("Read-only file system", self.text)

    def test_require_escalated_guidance_present(self):
        """Guidance tells model to use require_escalated when appropriate."""
        self.assertIn("require_escalated", self.text)

    def test_escalation_requires_justification(self):
        """Guidance requires a user-visible justification for escalation."""
        self.assertIn("justification", self.text)

    def test_stop_and_report_if_escalation_unavailable(self):
        """Guidance says stop and report when escalation is rejected/unavailable."""
        text_lower = self.text.lower()
        self.assertTrue(
            "stop and report" in text_lower or "rejected or unavailable" in text_lower,
            "Guidance should tell model to stop and report when escalation fails",
        )

    def test_connection_refused_guidance_present(self):
        """Guidance addresses connection-refused failures to avoid proxy misdiagnosis."""
        self.assertIn("connection refused", self.text.lower())

    def test_plain_permission_denied_not_overclaimed(self):
        """Guidance warns that plain permission denied is not necessarily a sandbox boundary."""
        self.assertIn("permission denied", self.text.lower())
        # Must include a qualification that plain permission denied is not sandbox denial
        text_lower = self.text.lower()
        self.assertTrue(
            "normal file" in text_lower or "file-permission" in text_lower or
            "file permission" in text_lower,
            "Guidance should clarify that permission denied is a normal file-permission error",
        )

    def test_no_auto_escalation_language(self):
        """Guidance must not instruct the model to escalate automatically or silently."""
        text_lower = self.text.lower()
        # The word 'silently' should appear in a prohibition context
        if "silently" in text_lower:
            # Fine if it's in a 'never ... silently' context
            idx = text_lower.index("silently")
            context = text_lower[max(0, idx - 20):idx + 30]
            self.assertTrue(
                "never" in context or "not" in context or "do not" in context,
                "If 'silently' appears, it must be in a prohibition context",
            )
        # Must not say "automatically escalate" as a positive instruction
        self.assertNotIn("automatically escalate", text_lower)
        self.assertNotIn("auto-escalate", text_lower)

    def test_guidance_is_concise(self):
        """The sandbox section should stay short — not a mega-prompt."""
        start = self.text.find("Sandbox and Tool Failures")
        self.assertNotEqual(start, -1)
        # Find the next section header after the sandbox section
        remaining = self.text[start:]
        next_section = remaining.find("\n# ", 5)
        if next_section == -1:
            section_text = remaining
        else:
            section_text = remaining[:next_section]
        word_count = len(section_text.split())
        self.assertLessEqual(
            word_count, 150,
            f"Sandbox guidance section has {word_count} words — keep it under 150",
        )

    def test_default_profiles_reference_main_harness(self):
        """config/default/profiles.json must still reference the main harness as default."""
        import json
        profiles_json = ROOT / "config" / "default" / "profiles.json"
        self.assertTrue(profiles_json.is_file(), "config/default/profiles.json missing")
        data = json.loads(profiles_json.read_text(encoding="utf-8"))
        default_prompt = (data.get("defaults") or {}).get("system_prompt_file", "")
        self.assertIn("codex-core", default_prompt,
                      "Default profiles should reference codex-core as the system prompt")


if __name__ == "__main__":
    unittest.main()
