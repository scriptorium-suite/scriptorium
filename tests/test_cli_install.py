from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scriptorium import cli


class InstallCliTests(unittest.TestCase):
    def invoke(self, arguments: list[str]) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(arguments)
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_core_preview_is_zero_write_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "components"
            code, report, stderr = self.invoke(
                ["install", "core", "--target", str(target), "--json"]
            )
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(report["status"], "planned")
            self.assertEqual(report["writes"], "none")
            self.assertFalse(target.exists())

    def test_capture_run_fails_without_creating_target_until_asset_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "capture"
            code, report, stderr = self.invoke(
                [
                    "install",
                    "capture",
                    "--target",
                    str(target),
                    "--run",
                    "--json",
                ]
            )
            self.assertEqual(code, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(report["errors"], [{"code": "artifact_unpublished"}])
            self.assertFalse(target.exists())

    def test_unknown_profile_is_a_stable_json_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "components"
            code, report, stderr = self.invoke(
                ["install", "unknown", "--target", str(target), "--json"]
            )
            self.assertEqual(code, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(report["errors"], [{"code": "unknown_profile"}])
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
