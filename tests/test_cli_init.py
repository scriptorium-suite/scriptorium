from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scriptorium import cli
from scriptorium.config import resolve_config_path


class InitCliTests(unittest.TestCase):
    def invoke_json(self, arguments: list[str]) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(arguments)
        return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_missing_required_arguments_preserve_json_contract(self):
        exit_code, report, stderr = self.invoke_json(["init", "--run", "--json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertEqual(report["operation"], "init")
        self.assertEqual(report["mode"], "run")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["errors"], [{"code": "entry_error"}])

    def test_preview_then_run_create_a_real_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "research workspace"
            provenance_home = root / "provenance data"
            config_dir = root / "config family"
            arguments = [
                "init",
                "--workspace",
                str(workspace),
                "--provenance-home",
                str(provenance_home),
                "--project-id",
                "catalyst-screening",
                "--title",
                "Catalyst screening",
                "--host",
                "codex",
                "--idea",
                "Screen stable catalyst candidates.",
                "--config-dir",
                str(config_dir),
                "--json",
            ]

            preview_code, preview, preview_stderr = self.invoke_json(arguments)

            self.assertEqual(preview_code, 0)
            self.assertEqual(preview_stderr, "")
            self.assertEqual(preview["status"], "planned")
            self.assertFalse(workspace.exists())
            self.assertFalse(provenance_home.exists())
            self.assertFalse(resolve_config_path(config_dir).exists())

            run_code, applied, run_stderr = self.invoke_json(arguments + ["--run"])

            self.assertEqual(run_code, 0)
            self.assertEqual(run_stderr, "")
            self.assertEqual(applied["status"], "initialized")
            self.assertTrue(
                (workspace / "Projects" / "catalyst-screening.md").is_file()
            )
            self.assertTrue(provenance_home.is_dir())
            self.assertTrue(resolve_config_path(config_dir).is_file())

    def test_invalid_input_returns_content_free_json_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exit_code, report, stderr = self.invoke_json(
                [
                    "init",
                    "--workspace",
                    str(root / "workspace"),
                    "--provenance-home",
                    str(root / "home"),
                    "--project-id",
                    "Invalid_ID",
                    "--title",
                    "Invalid project",
                    "--host",
                    "codex",
                    "--config-dir",
                    str(root / "config"),
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["errors"], [{"code": "entry_error"}])
        self.assertNotIn(str(root), json.dumps(report))


if __name__ == "__main__":
    unittest.main()
