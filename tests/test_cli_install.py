from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium import cli
from scriptorium.installer import InstallError


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

    def test_capture_run_marks_target_failed_when_download_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "capture"

            def failing_download(_component, _destination):
                raise InstallError("release asset download failed", code="asset_download")

            with mock.patch(
                "scriptorium.installer._download_release_asset",
                side_effect=failing_download,
            ):
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
            self.assertEqual(report["errors"], [{"code": "asset_download"}])
            marker = json.loads(
                (target / ".scriptorium-install.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["state"], "failed")

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
