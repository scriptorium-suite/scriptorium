from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium import cli
from scriptorium.config import SuiteConfig, render_config
from scriptorium.resume import ResumeError, _parse_capsule, format_resume_report, run_resume


def capsule() -> dict[str, object]:
    return {
        "capsule_version": "context-capsule/0.1",
        "project": {
            "project_id": "synthetic-catalyst",
            "title": "Synthetic Catalyst Study",
            "status": "active",
            "stage": "evidence-review",
            "priority": "high",
            "updated": "2026-07-22",
            "goal": "Test one falsifiable catalyst hypothesis.",
            "conclusion": "",
        },
        "next_actions": ["Compare the two synthetic papers."],
        "blocked_by": "",
        "recent_progress": [
            {"date": "2026-07-22", "items": ["Approved the research question."]}
        ],
        "literature": [
            {
                "id": "paper-1",
                "citekey": "synthetic2026",
                "title": "A Synthetic Catalyst Paper",
                "year": 2026,
                "read_status": "deep_read",
                "tldr": "A synthetic result for contract testing.",
            }
        ],
        "research_artifacts": [
            {
                "kind": "reading-note",
                "schema_version": "reading-note/1.0",
                "id": "reading-note-1",
                "title": "Synthetic reading note",
                "status": "reviewed",
                "source_ids": ["paper-1"],
                "summary": "Reference-only synthetic evidence.",
                "trust": "reference_only",
            }
        ],
        "reference_leads": {
            "gaps": ["No synthetic replication yet."],
            "priority_reads": ["paper-1"],
        },
        "trust": {
            "project_state": "human_or_approved",
            "recent_progress": "auto_applied_low_risk_not_approved_claims",
            "research_artifacts": "reference_only_not_approved_claims",
        },
        "limits": {"max_markdown_chars": 8000, "truncated": False},
    }


def completed(value: dict[str, object], *, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["prov-context"],
        returncode=0,
        stdout=json.dumps(value),
        stderr=stderr,
    )


class ResumeBoundaryTests(unittest.TestCase):
    def test_run_resume_uses_public_command_and_suppresses_local_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "private data"
            source = root / "Provenance"
            home.mkdir()
            source.mkdir()
            with (
                mock.patch("scriptorium.resume._compatibility_version", return_value="0.17.0"),
                mock.patch(
                    "scriptorium.resume._resolve_provenance",
                    return_value=(source, Path("prov-context")),
                ),
                mock.patch(
                    "scriptorium.resume.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(
                            args=["prov-context", "--version"],
                            returncode=0,
                            stdout="0.17.0\n",
                            stderr="",
                        ),
                        completed(capsule(), stderr="private diagnostic"),
                    ],
                ) as invoke,
            ):
                report = run_resume(
                    provenance_home=home,
                    project="synthetic-catalyst",
                )

        command = invoke.call_args_list[1].args[0]
        self.assertEqual(
            command, ["prov-context", "--project", "synthetic-catalyst", "--json"]
        )
        self.assertEqual(
            invoke.call_args_list[1].kwargs["env"]["PROVENANCE_HOME"],
            str(home.resolve()),
        )
        self.assertEqual(report["operation"], "resume")
        self.assertEqual(report["capsule"]["project"]["project_id"], "synthetic-catalyst")
        self.assertEqual(report["entry"]["stderr"], "suppressed")
        rendered = json.dumps(report)
        self.assertNotIn(str(home), rendered)
        self.assertNotIn("private diagnostic", rendered)

    def test_unknown_component_fields_fail_closed(self):
        value = capsule()
        value["private_path"] = "hidden"
        with self.assertRaisesRegex(ResumeError, "root.*incompatible"):
            _parse_capsule(json.dumps(value))

    def test_local_paths_fail_closed(self):
        value = capsule()
        value["project"]["goal"] = r"Read C:\Users\Researcher\private.md"
        with self.assertRaisesRegex(ResumeError, "suppressed local path"):
            _parse_capsule(json.dumps(value))

    def test_reference_artifacts_cannot_claim_authoritative_trust(self):
        value = capsule()
        value["research_artifacts"][0]["trust"] = "authoritative"
        with self.assertRaisesRegex(ResumeError, "artifact trust"):
            _parse_capsule(json.dumps(value))

    def test_component_failure_does_not_forward_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            failed = subprocess.CompletedProcess(
                args=["prov-context"],
                returncode=2,
                stdout="",
                stderr=r"unknown project under C:\Users\Researcher\private",
            )
            with (
                mock.patch("scriptorium.resume._compatibility_version", return_value="0.17.0"),
                mock.patch(
                    "scriptorium.resume._resolve_provenance",
                    return_value=(None, Path("prov-context")),
                ),
                mock.patch(
                    "scriptorium.resume.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(
                            args=["prov-context", "--version"],
                            returncode=0,
                            stdout="0.17.0\n",
                            stderr="",
                        ),
                        failed,
                    ],
                ),
            ):
                with self.assertRaisesRegex(ResumeError, "did not return a capsule") as raised:
                    run_resume(provenance_home=home, project="missing")
        self.assertNotIn("Researcher", str(raised.exception))

    def test_runtime_version_mismatch_fails_before_capsule_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with (
                mock.patch(
                    "scriptorium.resume._compatibility_version",
                    return_value="0.17.0",
                ),
                mock.patch(
                    "scriptorium.resume._resolve_provenance",
                    return_value=(None, Path("prov-context")),
                ),
                mock.patch(
                    "scriptorium.resume.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=["prov-context", "--version"],
                        returncode=0,
                        stdout="0.16.0\n",
                        stderr=r"private C:\Users\Researcher",
                    ),
                ) as invoke,
            ):
                with self.assertRaisesRegex(
                    ResumeError, "incompatible runtime version"
                ) as raised:
                    run_resume(provenance_home=home, project="synthetic-catalyst")

        self.assertEqual(invoke.call_count, 1)
        self.assertNotIn("Researcher", str(raised.exception))

    def test_human_report_marks_research_artifacts_reference_only(self):
        report = {
            "generated_by": {"version": "0.1.0"},
            "capsule": capsule(),
            "path_selection": {},
            "warnings": [],
        }
        rendered = format_resume_report(report)
        self.assertIn("Synthetic Catalyst Study", rendered)
        self.assertIn("Reference context", rendered)
        self.assertIn("reference-only", rendered)


class ResumeCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.home = self.root / "provenance"
        self.workspace.mkdir()
        self.home.mkdir()
        self.config_root = self.root / "config"
        config_path = self.config_root / "scriptorium" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes(
            render_config(
                SuiteConfig(
                    workspace=self.workspace.resolve(),
                    provenance_home=self.home.resolve(),
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

    def test_resume_uses_configured_home_and_project(self):
        component_report = {"operation": "resume", "exit_code": 0}
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("scriptorium.cli.run_resume", return_value=component_report) as run,
        ):
            exit_code, report, stderr = self.invoke_json(
                ["resume", "--config-dir", str(self.config_root), "--json"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(run.call_args.kwargs["provenance_home"], self.home.resolve())
        self.assertEqual(run.call_args.kwargs["project"], "configured-project")
        self.assertEqual(report["path_selection"]["data_root"]["source"], "suite-config")

    def test_resume_json_usage_error_never_echoes_private_arguments(self):
        secret = r"C:\Users\Researcher\private-project"
        exit_code, report, stderr = self.invoke_json(
            ["resume", "--project", secret, "--json", "--unknown", secret]
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr, "")
        self.assertEqual(report["operation"], "resume")
        self.assertNotIn(secret, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
