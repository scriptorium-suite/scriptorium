from __future__ import annotations

import contextlib
import io
import json
import unittest

from scriptorium import cli


class PullCliUsageTests(unittest.TestCase):
    def test_missing_required_arguments_preserve_json_contract(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(["pull", "--run", "--json"])

        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["operation"], "pull")
        self.assertEqual(report["mode"], "run")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["errors"], [{"code": "entry_error"}])

    def test_unknown_argument_preserves_json_contract(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(
                [
                    "pull",
                    "--workspace",
                    "workspace",
                    "--provenance-home",
                    "provenance-home",
                    "--json",
                    "--unknown-option",
                ]
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["errors"], [{"code": "entry_error"}])


if __name__ == "__main__":
    unittest.main()
