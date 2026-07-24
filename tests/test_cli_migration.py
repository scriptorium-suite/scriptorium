from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scriptorium import cli
from scriptorium.migration import MigrationError


def aggregate_report(operation: str, status: str) -> dict[str, object]:
    return {
        "schema_version": "migration-report/1.0",
        "operation": operation,
        "status": status,
        "summary": {
            "sources_requested": 1,
            "files": 2,
            "markdown": 1,
            "pdf": 1,
            "bytes": 24,
            "changed": 0,
            "unchanged": 0,
        },
        "limitations": [],
    }


class MigrationCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "research workspace"
        self.sources = self.root / "selected sources"
        self.platform_state = self.root / "platform state"
        self.workspace.mkdir()
        self.sources.mkdir()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.platform_state),
                "XDG_STATE_HOME": str(self.platform_state),
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def invoke(
        self, arguments: list[str]
    ) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(arguments)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def invoke_json(
        self, arguments: list[str]
    ) -> tuple[int, dict[str, object], str]:
        exit_code, stdout, stderr = self.invoke(arguments)
        return exit_code, json.loads(stdout), stderr

    def test_plan_forwards_repeated_explicit_sources(self):
        first = self.sources / "first.md"
        second = self.sources / "second.pdf"
        expected = SimpleNamespace(report=aggregate_report("plan", "planned"))
        with mock.patch(
            "scriptorium.cli.plan_migration", return_value=expected
        ) as plan:
            exit_code, report, stderr = self.invoke_json(
                [
                    "migrate",
                    "plan",
                    "--source",
                    str(first),
                    "--source",
                    str(second),
                    "--workspace",
                    str(self.workspace),
                    "--batch-id",
                    "batch-001",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(report, expected.report)
        plan.assert_called_once_with(
            [first, second],
            workspace=self.workspace,
            batch_id="batch-001",
        )

    def test_apply_uses_sources_for_first_run_and_identity_for_reapply(self):
        source = self.sources / "idea.md"
        planned = object()
        applied = SimpleNamespace(report=aggregate_report("apply", "applied"))
        with (
            mock.patch(
                "scriptorium.cli.plan_migration", return_value=planned
            ) as plan,
            mock.patch(
                "scriptorium.cli.apply_migration", return_value=applied
            ) as apply,
            mock.patch("scriptorium.cli.reapply_migration") as reapply,
        ):
            exit_code, report, _stderr = self.invoke_json(
                [
                    "migrate",
                    "apply",
                    "--source",
                    str(source),
                    "--workspace",
                    str(self.workspace),
                    "--batch-id",
                    "batch-001",
                    "--json",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "applied")
        plan.assert_called_once_with(
            [source], workspace=self.workspace, batch_id="batch-001"
        )
        apply.assert_called_once_with(planned)
        reapply.assert_not_called()

        unchanged = SimpleNamespace(
            report=aggregate_report("apply", "unchanged")
        )
        with (
            mock.patch("scriptorium.cli.plan_migration") as plan,
            mock.patch("scriptorium.cli.apply_migration") as apply,
            mock.patch(
                "scriptorium.cli.reapply_migration",
                return_value=unchanged,
            ) as reapply,
        ):
            exit_code, report, _stderr = self.invoke_json(
                [
                    "migrate",
                    "apply",
                    "--workspace",
                    str(self.workspace),
                    "--batch-id",
                    "batch-001",
                    "--json",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "unchanged")
        reapply.assert_called_once_with(
            workspace=self.workspace, batch_id="batch-001"
        )
        plan.assert_not_called()
        apply.assert_not_called()

    def test_verify_and_rollback_need_only_workspace_and_batch(self):
        verified = SimpleNamespace(
            report=aggregate_report("verify", "applied")
        )
        with mock.patch(
            "scriptorium.cli.verify_migration", return_value=verified
        ) as verify:
            exit_code, report, _stderr = self.invoke_json(
                [
                    "migrate",
                    "verify",
                    "--workspace",
                    str(self.workspace),
                    "--batch-id",
                    "batch-001",
                    "--json",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["operation"], "verify")
        verify.assert_called_once_with(
            workspace=self.workspace, batch_id="batch-001"
        )

        loaded = object()
        rolled_back = SimpleNamespace(
            report=aggregate_report("rollback", "rolled-back")
        )
        with (
            mock.patch(
                "scriptorium.cli.load_migration", return_value=loaded
            ) as load,
            mock.patch(
                "scriptorium.cli.rollback_migration",
                return_value=rolled_back,
            ) as rollback,
        ):
            exit_code, report, _stderr = self.invoke_json(
                [
                    "migrate",
                    "rollback",
                    "--workspace",
                    str(self.workspace),
                    "--batch-id",
                    "batch-001",
                    "--json",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "rolled-back")
        load.assert_called_once_with(
            workspace=self.workspace, batch_id="batch-001"
        )
        rollback.assert_called_once_with(loaded)

    def test_real_cli_lifecycle_is_path_free_and_idempotent(self):
        markdown = self.sources / "synthetic idea.md"
        pdf = self.sources / "synthetic paper.pdf"
        markdown.write_text("# Synthetic\n", encoding="utf-8")
        pdf.write_bytes(b"%PDF-1.4\nsynthetic\n")
        common = [
            "--workspace",
            str(self.workspace),
            "--batch-id",
            "synthetic-batch",
            "--json",
        ]

        human_code, human_output, human_stderr = self.invoke(
            [
                "migrate",
                "plan",
                "--source",
                str(self.sources),
                "--workspace",
                str(self.workspace),
                "--batch-id",
                "synthetic-batch",
            ]
        )
        plan_code, planned, plan_stderr = self.invoke_json(
            [
                "migrate",
                "plan",
                "--source",
                str(self.sources),
                *common,
            ]
        )
        apply_code, applied, apply_stderr = self.invoke_json(
            [
                "migrate",
                "apply",
                "--source",
                str(self.sources),
                *common,
            ]
        )
        verify_code, verified, verify_stderr = self.invoke_json(
            ["migrate", "verify", *common]
        )
        repeat_code, repeated, repeat_stderr = self.invoke_json(
            ["migrate", "apply", *common]
        )
        rollback_code, rolled_back, rollback_stderr = self.invoke_json(
            ["migrate", "rollback", *common]
        )

        self.assertEqual(
            (plan_code, apply_code, verify_code, repeat_code, rollback_code),
            (0, 0, 0, 0, 0),
        )
        self.assertEqual(human_code, 0)
        self.assertEqual(human_stderr, "")
        self.assertIn("Migration plan: planned", human_output)
        self.assertNotIn(str(self.root), human_output)
        self.assertNotIn(markdown.name, human_output)
        self.assertEqual(
            (
                planned["status"],
                applied["status"],
                verified["status"],
                repeated["status"],
                rolled_back["status"],
            ),
            ("planned", "applied", "applied", "unchanged", "rolled-back"),
        )
        self.assertEqual(
            plan_stderr
            + apply_stderr
            + verify_stderr
            + repeat_stderr
            + rollback_stderr,
            "",
        )
        serialized = json.dumps(
            [planned, applied, verified, repeated, rolled_back],
            ensure_ascii=False,
        )
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(markdown.name, serialized)
        self.assertEqual(markdown.read_text(encoding="utf-8"), "# Synthetic\n")
        self.assertEqual(pdf.read_bytes(), b"%PDF-1.4\nsynthetic\n")

    def test_usage_and_runtime_errors_never_echo_private_arguments(self):
        sentinel = str(self.root / "private research")
        arguments = [
            "migrate",
            "plan",
            "--source",
            sentinel,
            "--workspace",
            sentinel,
            "--batch-id",
            "batch-001",
            "--unknown",
            sentinel,
        ]
        exit_code, report, stderr = self.invoke_json(arguments + ["--json"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertEqual(report["operation"], "plan")
        self.assertEqual(report["errors"], [{"code": "entry_error"}])
        self.assertNotIn(sentinel, json.dumps(report))

        exit_code, stdout, stderr = self.invoke(arguments)
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("invalid migrate invocation", stderr)
        self.assertNotIn(sentinel, stderr)

        failures = (
            (MigrationError("target_exists"), "target_exists"),
            (MigrationError(sentinel), "entry_error"),
            (OSError(sentinel), "entry_error"),
        )
        for failure, code in failures:
            with (
                self.subTest(code=code),
                mock.patch(
                    "scriptorium.cli.plan_migration",
                    side_effect=failure,
                ),
            ):
                exit_code, report, stderr = self.invoke_json(
                    [
                        "migrate",
                        "plan",
                        "--source",
                        sentinel,
                        "--workspace",
                        str(self.workspace),
                        "--batch-id",
                        "batch-001",
                        "--json",
                    ]
                )
            self.assertEqual(exit_code, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(report["errors"], [{"code": code}])
            self.assertNotIn(sentinel, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
