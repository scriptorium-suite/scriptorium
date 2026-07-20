from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium import cli
from scriptorium.inventory import InventoryError


ERROR_TOP_LEVEL_FIELDS = {
    "format_version",
    "generated_by",
    "operation",
    "mode",
    "status",
    "exit_code",
    "summary",
    "routing_preview",
    "action_required",
    "egress",
    "safety",
    "errors",
    "limitations",
}


class InventoryCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "research sources"
        self.conversations = self.root / "conversation exports"
        self.zotero = self.root / "zotero exports"
        self.source.mkdir()
        self.conversations.mkdir()
        self.zotero.mkdir()

    def invoke_json(self, arguments: list[str]) -> tuple[int, dict, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(arguments)
        return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

    def fake_report(self, *, exit_code: int = 0) -> dict:
        return {
            "format_version": 1,
            "generated_by": {"name": "scriptorium", "version": "test"},
            "operation": "inventory",
            "mode": "preview",
            "status": "planned" if exit_code == 0 else "partial",
            "exit_code": exit_code,
            "summary": {},
            "routing_preview": [],
            "action_required": [],
            "egress": {},
            "safety": {},
            "errors": [],
            "limitations": [],
        }

    def test_repeated_explicit_sources_are_forwarded_without_loading_config(self):
        second_source = self.root / "second source"
        second_source.mkdir()
        expected = self.fake_report()
        with (
            mock.patch("scriptorium.cli.load_config") as load_config,
            mock.patch(
                "scriptorium.cli.run_inventory", return_value=expected
            ) as run_inventory,
        ):
            exit_code, report, stderr = self.invoke_json(
                [
                    "inventory",
                    "--source",
                    str(self.source),
                    "--source",
                    str(second_source),
                    "--conversation-export",
                    str(self.conversations),
                    "--zotero-export",
                    str(self.zotero),
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(report, expected)
        self.assertEqual(stderr, "")
        load_config.assert_not_called()
        run_inventory.assert_called_once_with(
            sources=[self.source, second_source],
            conversation_exports=[self.conversations],
            zotero_exports=[self.zotero],
        )

    def test_json_usage_errors_are_fixed_and_content_free(self):
        sentinel = str(self.root / "private-research-path")
        cases = (
            [
                "inventory",
                "--source",
                sentinel,
                "--unknown-option",
                "private-value",
                "--json",
            ],
            ["inventory", "--source", "--json"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                exit_code, report, stderr = self.invoke_json(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(stderr, "")
                self.assertEqual(set(report), ERROR_TOP_LEVEL_FIELDS)
                self.assertEqual(report["operation"], "inventory")
                self.assertEqual(report["mode"], "preview")
                self.assertEqual(report["status"], "error")
                self.assertEqual(report["exit_code"], 2)
                self.assertEqual(report["errors"], [{"code": "entry_error"}])
                serialized = json.dumps(report, ensure_ascii=False)
                self.assertNotIn(sentinel, serialized)
                self.assertNotIn("private-value", serialized)

    def test_missing_all_sources_uses_the_same_json_error_boundary(self):
        exit_code, report, stderr = self.invoke_json(["inventory", "--json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertEqual(report["operation"], "inventory")
        self.assertEqual(report["errors"], [{"code": "entry_error"}])

    def test_internal_and_unexpected_errors_never_echo_paths(self):
        sentinel = str(self.root / "confidential project")
        errors = (
            InventoryError(f"unsafe source: {sentinel}"),
            PermissionError(f"cannot read {sentinel}"),
        )
        for error in errors:
            with (
                self.subTest(error=type(error).__name__),
                mock.patch("scriptorium.cli.run_inventory", side_effect=error),
            ):
                exit_code, report, stderr = self.invoke_json(
                    ["inventory", "--source", sentinel, "--json"]
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(report["errors"], [{"code": "entry_error"}])
            self.assertNotIn(sentinel, json.dumps(report, ensure_ascii=False))

    def test_human_error_is_fixed_and_does_not_echo_private_details(self):
        sentinel = str(self.root / "confidential project")
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch(
                "scriptorium.cli.run_inventory",
                side_effect=InventoryError(f"unsafe source: {sentinel}"),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = cli.main(["inventory", "--source", sentinel])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("inventory preview unavailable", stderr.getvalue())
        self.assertNotIn(sentinel, stderr.getvalue())

    def test_human_usage_error_is_fixed_and_does_not_echo_arguments(self):
        sentinel = str(self.root / "confidential project")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(
                ["inventory", "--source", sentinel, "--unknown", sentinel]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("invalid inventory invocation", stderr.getvalue())
        self.assertNotIn(sentinel, stderr.getvalue())

    def test_report_exit_code_is_propagated_without_reinterpretation(self):
        expected = self.fake_report(exit_code=1)
        with mock.patch(
            "scriptorium.cli.run_inventory", return_value=expected
        ):
            exit_code, report, stderr = self.invoke_json(
                ["inventory", "--source", str(self.source), "--json"]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(report, expected)
        self.assertEqual(stderr, "")

    def test_json_error_envelope_contains_no_dynamic_filesystem_metadata(self):
        exit_code, report, stderr = self.invoke_json(
            ["inventory", "--source", str(self.root / "missing"), "--json"]
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, "")
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["exit_code"], 1)
        serialized = json.dumps(report, ensure_ascii=False).casefold()
        self.assertNotIn(str(self.root).casefold(), serialized)
        self.assertFalse(
            {"path", "absolute_path", "relative_path", "filename", "size", "hash", "mtime"}
            & set(report)
        )


if __name__ == "__main__":
    unittest.main()
