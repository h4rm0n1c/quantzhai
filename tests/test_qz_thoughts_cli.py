import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class QzThoughtsCliTests(unittest.TestCase):
    def test_once_file_coalesces_delta_activity(self):
        raw = textwrap.dedent(
            """
            event: response.created
            data: {"type":"response.created","response":{"id":"resp_test","status":"in_progress","model":"model-test"}}

            event: response.output_item.added
            data: {"type":"response.output_item.added","item":{"id":"rs_test","type":"reasoning","status":"in_progress"}}

            event: response.reasoning_summary_text.delta
            data: {"type":"response.reasoning_summary_text.delta","delta":"I"}

            event: response.reasoning_summary_text.delta
            data: {"type":"response.reasoning_summary_text.delta","delta":"'ll"}

            event: response.reasoning_summary_text.done
            data: {"type":"response.reasoning_summary_text.done","text":"I'll"}

            event: response.output_text.delta
            data: {"type":"response.output_text.delta","delta":"hello"}

            event: response.output_text.delta
            data: {"type":"response.output_text.delta","delta":" world"}

            event: response.output_text.done
            data: {"type":"response.output_text.done","text":"hello world"}

            event: response.output_item.done
            data: {"type":"response.output_item.done","item":{"id":"msg_test","type":"message","status":"completed"}}

            event: response.completed
            data: {"type":"response.completed","response":{"id":"resp_test","status":"completed","model":"model-test"}}

            data: [DONE]

            """
        ).lstrip()
        with tempfile.NamedTemporaryFile("w", suffix=".raw", delete=False) as handle:
            handle.write(raw)
            capture = handle.name

        env = os.environ.copy()
        env["QZ_PROXY_PORT"] = "9"
        env["QZ_PROXY_HOST"] = "127.0.0.1"
        try:
            result = subprocess.run(
                [str(ROOT / "scripts/qz-thoughts"), "--once", "--file", capture],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=True,
            )
        finally:
            os.unlink(capture)

        self.assertIn("THOUGHT\nI'll", result.stdout)
        self.assertIn("ANSWER\nhello world", result.stdout)
        self.assertEqual(result.stdout.count("  thought   done 4 chars"), 1)
        self.assertEqual(result.stdout.count("  answer    done 11 chars"), 1)
        self.assertNotIn("  thought   I", result.stdout)
        self.assertNotIn("  thought   'll", result.stdout)


if __name__ == "__main__":
    unittest.main()
