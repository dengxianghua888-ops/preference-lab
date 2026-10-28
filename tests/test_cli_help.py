"""Help must expose the complete workflow while retaining the demo shorthand."""

import subprocess
import sys
import unittest


class CLIHelpTests(unittest.TestCase):
    def help_output(self, *arguments):
        result = subprocess.run(
            [sys.executable, "-m", "ecoalign_forge", *arguments],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_long_and_short_top_level_help_expose_workflow_commands(self):
        output = self.help_output("--help")
        self.assertEqual(output, self.help_output("-h"))
        for command in ("inspect", "review", "dataset", "verify", "report", "resume"):
            self.assertIn(command, output)

    def test_explicit_run_help_and_legacy_demo_shorthand_stay_available(self):
        output = self.help_output("run", "--help")
        self.assertEqual(output, self.help_output("--demo", "--help"))
        self.assertIn("--num-samples", output)
        self.assertIn("--persona", output)


if __name__ == "__main__":
    unittest.main()
