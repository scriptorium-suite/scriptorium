from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium import cli
from scriptorium.config import SuiteConfig, render_config
from scriptorium.status import StatusError


class StatusCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "research workspace"
        self.provenance_home = self.root / "provenance data"
        self.workspace.mkdir()
        self.provenance_home.mkdir()
        self.config_root = self.root / "config family"
        config_path = self.config_root / "scriptorium" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes(
            render_config(
                SuiteConfig(
                    workspace=self.workspace.resolve(),
                    provenance_home=self.provenance_home.resolve(),
                    hosts=("codex",),
                    default_project="configured-project",
                )
            )
        )

    def invoke_json(self, arguments: list[str]) -> tuple[int, dict[str, object], str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(arguments)
        return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_status_uses_configured_roots_and_default_project(self):
        status_report = {"operation": "status", "exit_code": 0}
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("scriptorium.cli.run_status", return_value=status_report) as run,
        ):
            exit_code, report, stderr = self.invoke_json(
                ["status", "--config-dir", str(self.config_root), "--json"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(report, status_report)
        self.assertEqual(stderr, "")
        self.assertEqual(run.call_args.kwargs["workspace"], self.workspace.resolve())
        self.assertEqual(
            run.call_args.kwargs["provenance_home"], self.provenance_home.resolve()
        )
        self.assertEqual(run.call_args.kwargs["project"], "configured-project")

    def test_nonblank_environment_precedes_config_and_blank_environment_does_not(self):
        environment_workspace = self.root / "environment workspace"
        environment_home = self.root / "environment home"
        environment_workspace.mkdir()
        environment_home.mkdir()
        status_report = {"operation": "status", "exit_code": 0}
        with (
            mock.patch.dict(
                os.environ,
                {
                    "SCRIPTORIUM_WORKSPACE": str(environment_workspace),
                    "PROVENANCE_HOME": str(environment_home),
                },
                clear=True,
            ),
            mock.patch("scriptorium.cli.run_status", return_value=status_report) as run,
        ):
            self.invoke_json(["status", "--json"])
        self.assertEqual(run.call_args.kwargs["workspace"], environment_workspace)
        self.assertEqual(run.call_args.kwargs["provenance_home"], environment_home)
        self.assertIsNone(run.call_args.kwargs["project"])

        with (
            mock.patch.dict(
                os.environ,
                {
                    "SCRIPTORIUM_CONFIG_DIR": str(self.config_root),
                    "SCRIPTORIUM_WORKSPACE": " ",
                    "PROVENANCE_VAULT": "\t",
                    "PROVENANCE_HOME": "   ",
                },
                clear=True,
            ),
            mock.patch("scriptorium.cli.run_status", return_value=status_report) as run,
        ):
            self.invoke_json(["status", "--json"])
        self.assertEqual(run.call_args.kwargs["workspace"], self.workspace.resolve())
        self.assertEqual(
            run.call_args.kwargs["provenance_home"], self.provenance_home.resolve()
        )
        self.assertEqual(run.call_args.kwargs["project"], "configured-project")

    def test_explicit_project_overrides_config_default(self):
        with mock.patch(
            "scriptorium.cli.run_status",
            return_value={"operation": "status", "exit_code": 0},
        ) as run:
            self.invoke_json(
                [
                    "status",
                    "--config-dir",
                    str(self.config_root),
                    "--project",
                    "explicit-project",
                    "--json",
                ]
            )
        self.assertEqual(run.call_args.kwargs["project"], "explicit-project")

    def test_workspace_override_does_not_reuse_configured_default_project(self):
        other_workspace = self.root / "other workspace"
        other_workspace.mkdir()
        with mock.patch(
            "scriptorium.cli.run_status",
            return_value={"operation": "status", "exit_code": 0},
        ) as run:
            self.invoke_json(
                [
                    "status",
                    "--config-dir",
                    str(self.config_root),
                    "--workspace",
                    str(other_workspace),
                    "--json",
                ]
            )

        self.assertEqual(run.call_args.kwargs["workspace"], other_workspace)
        self.assertIsNone(run.call_args.kwargs["project"])

    def test_json_usage_error_is_content_free(self):
        exit_code, report, stderr = self.invoke_json(
            ["status", "--json", "--unknown-option"]
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertEqual(report["operation"], "status")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["errors"], [{"code": "entry_error"}])

    def test_internal_error_does_not_echo_exception_or_paths(self):
        sentinel = str(self.root / "private project")
        with mock.patch(
            "scriptorium.cli.run_status",
            side_effect=StatusError(f"unsafe path: {sentinel}"),
        ):
            exit_code, report, stderr = self.invoke_json(
                ["status", "--config-dir", str(self.config_root), "--json"]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertNotIn(sentinel, json.dumps(report))
        self.assertEqual(report["errors"], [{"code": "entry_error"}])
        self.assertEqual(report["egress"]["host_managed"], "unknown")

    def test_unexpected_runtime_error_uses_the_same_content_free_boundary(self):
        sentinel = str(self.root / "unexpected private path")
        with mock.patch(
            "scriptorium.cli.run_status",
            side_effect=OSError(sentinel),
        ):
            exit_code, report, stderr = self.invoke_json(
                ["status", "--config-dir", str(self.config_root), "--json"]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertNotIn(sentinel, json.dumps(report))
        self.assertEqual(report["errors"], [{"code": "entry_error"}])


if __name__ == "__main__":
    unittest.main()
