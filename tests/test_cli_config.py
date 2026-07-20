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


class ConfigFallbackCliTests(unittest.TestCase):
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

    def invoke_json(self, arguments: list[str]) -> tuple[int, dict[str, object]]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(arguments)
        self.assertEqual(stderr.getvalue(), "")
        return exit_code, json.loads(stdout.getvalue())

    def test_pull_uses_configured_roots_and_default_project(self):
        component_report = {"operation": "pull", "exit_code": 0}
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("scriptorium.cli.run_pull", return_value=component_report) as run,
        ):
            exit_code, report = self.invoke_json(
                ["pull", "--config-dir", str(self.config_root), "--json"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(report, component_report)
        self.assertEqual(run.call_args.kwargs["workspace"], self.workspace.resolve())
        self.assertEqual(
            run.call_args.kwargs["provenance_home"], self.provenance_home.resolve()
        )
        self.assertEqual(run.call_args.kwargs["project"], "configured-project")

    def test_environment_paths_precede_configured_paths(self):
        env_workspace = self.root / "environment workspace"
        env_home = self.root / "environment provenance"
        env_workspace.mkdir()
        env_home.mkdir()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "SCRIPTORIUM_CONFIG_DIR": str(self.config_root),
                    "SCRIPTORIUM_WORKSPACE": str(env_workspace),
                    "PROVENANCE_HOME": str(env_home),
                },
                clear=True,
            ),
            mock.patch(
                "scriptorium.cli.run_pull",
                return_value={"operation": "pull", "exit_code": 0},
            ) as run,
        ):
            exit_code, report = self.invoke_json(["pull", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_args.kwargs["workspace"], env_workspace)
        self.assertEqual(run.call_args.kwargs["provenance_home"], env_home)
        self.assertIsNone(run.call_args.kwargs["project"])
        self.assertEqual(
            report["path_selection"]["workspace"]["source"], "environment"
        )
        self.assertEqual(
            report["path_selection"]["data_root"]["environment"],
            "PROVENANCE_HOME",
        )
        self.assertEqual(
            {warning["path"] for warning in report["warnings"]},
            {"workspace", "data_root"},
        )
        self.assertNotIn(str(env_workspace), json.dumps(report))
        self.assertNotIn(str(env_home), json.dumps(report))

    def test_pull_run_fails_closed_on_environment_config_conflict(self):
        env_workspace = self.root / "environment workspace"
        env_home = self.root / "environment provenance"
        env_workspace.mkdir()
        env_home.mkdir()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "SCRIPTORIUM_CONFIG_DIR": str(self.config_root),
                    "SCRIPTORIUM_WORKSPACE": str(env_workspace),
                    "PROVENANCE_HOME": str(env_home),
                },
                clear=True,
            ),
            mock.patch("scriptorium.cli.run_pull") as run,
        ):
            exit_code, report = self.invoke_json(["pull", "--run", "--json"])

        self.assertEqual(exit_code, 2)
        run.assert_not_called()
        self.assertEqual(report["mode"], "run")
        self.assertEqual(
            {warning["path"] for warning in report["warnings"]},
            {"workspace", "data_root"},
        )

    def test_pull_run_accepts_explicit_roots_despite_conflicting_environment(self):
        explicit_workspace = self.root / "explicit workspace"
        explicit_home = self.root / "explicit provenance"
        explicit_workspace.mkdir()
        explicit_home.mkdir()
        component_report = {"operation": "pull", "exit_code": 0}
        with (
            mock.patch.dict(
                os.environ,
                {
                    "SCRIPTORIUM_WORKSPACE": str(self.root / "environment workspace"),
                    "PROVENANCE_HOME": str(self.root / "environment provenance"),
                },
                clear=True,
            ),
            mock.patch(
                "scriptorium.cli.run_pull", return_value=component_report
            ) as run,
        ):
            exit_code, report = self.invoke_json(
                [
                    "pull",
                    "--config-dir",
                    str(self.config_root),
                    "--workspace",
                    str(explicit_workspace),
                    "--provenance-home",
                    str(explicit_home),
                    "--run",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(run.call_args.kwargs["run"])
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["path_selection"]["workspace"]["source"], "cli")

    def test_blank_environment_paths_do_not_block_config_fallback(self):
        component_report = {"operation": "pull", "exit_code": 0}
        with (
            mock.patch.dict(
                os.environ,
                {
                    "SCRIPTORIUM_CONFIG_DIR": str(self.config_root),
                    "SCRIPTORIUM_WORKSPACE": "   ",
                    "PROVENANCE_VAULT": "\t",
                    "PROVENANCE_HOME": " ",
                },
                clear=True,
            ),
            mock.patch("scriptorium.cli.run_pull", return_value=component_report) as run,
        ):
            exit_code, report = self.invoke_json(["pull", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_args.kwargs["workspace"], self.workspace.resolve())
        self.assertEqual(
            run.call_args.kwargs["provenance_home"], self.provenance_home.resolve()
        )
        self.assertEqual(run.call_args.kwargs["project"], "configured-project")
        self.assertEqual(
            report["path_selection"]["workspace"]["source"], "suite-config"
        )
        self.assertEqual(report["warnings"], [])

    def test_doctor_warns_when_environment_and_suite_config_disagree(self):
        env_workspace = self.root / "doctor environment workspace"
        env_home = self.root / "doctor environment provenance"
        env_workspace.mkdir()
        env_home.mkdir()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "SCRIPTORIUM_CONFIG_DIR": str(self.config_root),
                    "SCRIPTORIUM_WORKSPACE": str(env_workspace),
                    "PROVENANCE_HOME": str(env_home),
                },
                clear=True,
            ),
            mock.patch(
                "scriptorium.cli.run_doctor",
                return_value={"operation": "doctor", "exit_code": 1},
            ),
        ):
            exit_code, report = self.invoke_json(["doctor", "--json"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            {warning["path"] for warning in report["warnings"]},
            {"workspace", "data_root"},
        )
        self.assertEqual(
            report["path_selection"]["provenance_root"]["source"],
            "auto-discovery",
        )

    def test_single_workspace_override_does_not_reuse_configured_project(self):
        other_workspace = self.root / "other workspace"
        other_workspace.mkdir()
        with mock.patch(
            "scriptorium.cli.run_pull",
            return_value={"operation": "pull", "exit_code": 0},
        ) as run:
            self.invoke_json(
                [
                    "pull",
                    "--config-dir",
                    str(self.config_root),
                    "--workspace",
                    str(other_workspace),
                    "--json",
                ]
            )

        self.assertEqual(run.call_args.kwargs["workspace"], other_workspace)
        self.assertIsNone(run.call_args.kwargs["project"])

    def test_doctor_uses_config_when_paths_are_omitted(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch(
                "scriptorium.cli.run_doctor",
                return_value={"operation": "doctor", "exit_code": 1},
            ) as run,
        ):
            exit_code, report = self.invoke_json(
                ["doctor", "--config-dir", str(self.config_root), "--json"]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["operation"], "doctor")
        self.assertEqual(run.call_args.kwargs["workspace"], self.workspace.resolve())
        self.assertEqual(
            run.call_args.kwargs["provenance_home"], self.provenance_home.resolve()
        )

    def test_doctor_config_error_preserves_json_contract(self):
        config_path = self.config_root / "scriptorium" / "config.toml"
        config_path.write_bytes(b"invalid = [\n")

        exit_code, report = self.invoke_json(
            ["doctor", "--config-dir", str(self.config_root), "--json"]
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(report["target"], "public-alpha")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["errors"], [{"code": "entry_error"}])
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_host_install_uses_configured_workspace(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch(
                "scriptorium.cli.run_host_install",
                return_value={"operation": "host.install", "exit_code": 0},
            ) as run,
        ):
            exit_code, report = self.invoke_json(
                [
                    "host",
                    "install",
                    "codex",
                    "--config-dir",
                    str(self.config_root),
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["operation"], "host.install")
        self.assertEqual(run.call_args.kwargs["workspace"], self.workspace.resolve())


if __name__ == "__main__":
    unittest.main()
