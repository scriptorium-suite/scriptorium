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
from scriptorium.demo import DemoError
from scriptorium.host import run_host_install
from scriptorium.pull import (
    ENTRY_LIMITATIONS,
    PullError,
    SUMMARY_FIELDS,
    _parse_component_report,
    _resolve_provenance,
    format_pull_report,
    run_pull,
)


def component_report(
    *, mode: str = "preview", status: str = "planned", exit_code: int = 0
) -> dict:
    summary = {key: 0 for key in SUMMARY_FIELDS}
    summary["codex_found"] = 1
    return {
        "format_version": 1,
        "generated_by": {"name": "provenance", "version": "0.17.0"},
        "operation": "pull",
        "mode": mode,
        "status": status,
        "exit_code": exit_code,
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "not-invoked",
            "optional_connectors": "not-invoked",
        },
        "stages": [
            {
                "id": "sync_state",
                "status": "ok",
                "counts": {"files": 0, "records": 0, "invalid": 0},
            }
        ],
        "summary": summary,
        "action_required": [],
        "errors": [],
        "limitations": [],
    }


def completed(report: dict, *, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["prov-sync-pull"],
        returncode=report["exit_code"],
        stdout=json.dumps(report),
        stderr=stderr,
    )


class PullTests(unittest.TestCase):
    def test_canonical_codex_adapter_adds_scan_flag_and_run_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "provenance-home"
            provenance_root = root / "Provenance"
            workspace.mkdir()
            home.mkdir()
            provenance_root.mkdir()
            run_host_install(workspace=workspace, host="codex")
            result = component_report(mode="run", status="action-required", exit_code=0)
            with (
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(provenance_root, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run", return_value=completed(result)
                ) as invoke,
            ):
                report = run_pull(
                    workspace=workspace,
                    provenance_home=home,
                    project="catalyst",
                    run=True,
                )

            command = invoke.call_args.args[0]
            self.assertIn("--scan-codex", command)
            self.assertIn("--run", command)
            self.assertIsNone(invoke.call_args.kwargs["timeout"])
            self.assertEqual(command[command.index("--project") + 1], "catalyst")
            self.assertEqual(report["generated_by"]["name"], "scriptorium")
            self.assertEqual(
                report["entry"]["component_generated_by"]["name"], "provenance"
            )
            self.assertTrue(report["entry"]["scan_codex"])

    def test_claude_only_manifest_does_not_add_codex_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            run_host_install(workspace=workspace, host="claude-code")
            with (
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    return_value=completed(component_report()),
                ) as invoke,
            ):
                report = run_pull(workspace=workspace, provenance_home=home)
            self.assertNotIn("--scan-codex", invoke.call_args.args[0])
            self.assertNotIn("--run", invoke.call_args.args[0])
            self.assertEqual(invoke.call_args.kwargs["timeout"], 60)
            self.assertEqual(report["mode"], "preview")
            self.assertFalse(report["entry"]["codex_selected"])
            self.assertEqual(report["status"], "planned")
            self.assertEqual(report["action_required"], [])

    def test_codex_home_is_forwarded_as_the_scan_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "provenance-home"
            codex_home = root / "custom-codex-home"
            workspace.mkdir()
            home.mkdir()
            codex_home.mkdir()
            run_host_install(workspace=workspace, host="codex")
            with (
                mock.patch.dict(
                    os.environ, {"CODEX_HOME": str(codex_home)}, clear=False
                ),
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    return_value=completed(component_report()),
                ) as invoke,
            ):
                report = run_pull(workspace=workspace, provenance_home=home)

            command = invoke.call_args.args[0]
            self.assertEqual(
                command[command.index("--codex-home") + 1], str(codex_home.resolve())
            )
            self.assertEqual(report["entry"]["codex_home_source"], "CODEX_HOME")
            self.assertEqual(report["entry"]["codex_home_state"], "ready")

    def test_missing_codex_home_reports_zero_session_setup_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "provenance-home"
            missing_codex_home = root / "missing-codex-home"
            workspace.mkdir()
            home.mkdir()
            run_host_install(workspace=workspace, host="codex")
            result = component_report(status="noop")
            result["summary"]["codex_found"] = 0
            with (
                mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": str(missing_codex_home)},
                    clear=False,
                ),
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    return_value=completed(result),
                ) as invoke,
            ):
                report = run_pull(workspace=workspace, provenance_home=home)

            command = invoke.call_args.args[0]
            self.assertNotIn("--scan-codex", command)
            self.assertNotIn("--codex-home", command)
            self.assertFalse(missing_codex_home.exists())
            self.assertEqual(report["status"], "action-required")
            self.assertEqual(report["summary"]["codex_found"], 0)
            self.assertTrue(report["entry"]["codex_selected"])
            self.assertFalse(report["entry"]["scan_codex"])
            self.assertEqual(report["entry"]["codex_home_state"], "missing")
            self.assertIn(
                {"type": "codex-home-setup", "count": 1},
                report["action_required"],
            )

    def test_codex_home_expands_environment_variables_and_whitespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "provenance-home"
            codex_home = root / "custom-codex-home"
            workspace.mkdir()
            home.mkdir()
            codex_home.mkdir()
            run_host_install(workspace=workspace, host="codex")
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "SCRIPTORIUM_TEST_PROFILE": str(root),
                        "CODEX_HOME": " $SCRIPTORIUM_TEST_PROFILE/custom-codex-home ",
                    },
                    clear=False,
                ),
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    return_value=completed(component_report()),
                ) as invoke,
            ):
                run_pull(workspace=workspace, provenance_home=home)

            command = invoke.call_args.args[0]
            self.assertEqual(
                command[command.index("--codex-home") + 1], str(codex_home.resolve())
            )

    def test_blank_codex_home_uses_the_component_profile_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "provenance-home"
            profile = root / "profile"
            codex_home = profile / ".codex"
            workspace.mkdir()
            home.mkdir()
            codex_home.mkdir(parents=True)
            run_host_install(workspace=workspace, host="codex")
            with (
                mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": "   ", "USERPROFILE": str(profile)},
                    clear=True,
                ),
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    return_value=completed(component_report()),
                ) as invoke,
            ):
                report = run_pull(workspace=workspace, provenance_home=home)

            command = invoke.call_args.args[0]
            self.assertEqual(
                command[command.index("--codex-home") + 1],
                str(codex_home.resolve()),
            )
            self.assertEqual(report["entry"]["codex_home_source"], "profile-default")

    def test_missing_profile_default_skips_scan_and_requests_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "provenance-home"
            profile = root / "profile"
            workspace.mkdir()
            home.mkdir()
            profile.mkdir()
            run_host_install(workspace=workspace, host="codex")
            result = component_report(status="noop")
            result["summary"]["codex_found"] = 0
            with (
                mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": "   ", "USERPROFILE": str(profile)},
                    clear=True,
                ),
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    return_value=completed(result),
                ) as invoke,
            ):
                report = run_pull(workspace=workspace, provenance_home=home)

            command = invoke.call_args.args[0]
            self.assertNotIn("--scan-codex", command)
            self.assertNotIn("--codex-home", command)
            self.assertEqual(report["entry"]["codex_home_state"], "missing")
            self.assertEqual(report["status"], "action-required")
            self.assertIn(
                {"type": "codex-home-setup", "count": 1},
                report["action_required"],
            )

    def test_invalid_provenance_environment_root_does_not_fall_back_to_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-provenance"
            with (
                mock.patch.dict(
                    os.environ,
                    {"SCRIPTORIUM_PROVENANCE_ROOT": str(missing)},
                    clear=False,
                ),
                mock.patch(
                    "scriptorium.pull.resolve_component_root",
                    side_effect=DemoError("not found"),
                ),
                mock.patch("scriptorium.pull.shutil.which") as installed,
            ):
                with self.assertRaisesRegex(
                    PullError, "configured Provenance root is unavailable"
                ):
                    _resolve_provenance(None, expected_version="0.17.0")
            installed.assert_not_called()

    def test_environment_is_narrow_and_raw_stderr_is_suppressed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "secret-sentinel",
                        "CODEX_HOME": "codex-home",
                        "PROV_SYNC_NO_ENQUEUE": "1",
                    },
                ),
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    return_value=completed(
                        component_report(), stderr="private diagnostic"
                    ),
                ) as invoke,
            ):
                report = run_pull(workspace=workspace, provenance_home=home)
            env = invoke.call_args.kwargs["env"]
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("PROV_SYNC_NO_ENQUEUE", env)
            self.assertEqual(env["CODEX_HOME"], "codex-home")
            self.assertEqual(env["PROVENANCE_HOME"], str(home.resolve()))
            self.assertEqual(report["entry"]["stderr"], "suppressed")
            self.assertNotIn("private diagnostic", json.dumps(report))

    def test_missing_explicit_path_is_an_error_before_launch(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("scriptorium.pull.subprocess.run") as invoke,
        ):
            missing = Path(temporary) / "missing-private-name"
            with self.assertRaisesRegex(PullError, "workspace does not exist") as raised:
                run_pull(
                    workspace=missing,
                    provenance_home=Path(temporary),
                )
            self.assertNotIn(str(missing), str(raised.exception))
            invoke.assert_not_called()

    def test_provenance_home_and_workspace_must_not_overlap(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("scriptorium.pull.subprocess.run") as invoke,
        ):
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            nested = workspace / ".provenance"
            nested.mkdir()
            outer_home = root / "provenance"
            nested_workspace = outer_home / "workspace"
            nested_workspace.mkdir(parents=True)
            for selected_workspace, home in (
                (workspace, workspace),
                (workspace, nested),
                (nested_workspace, outer_home),
            ):
                with self.subTest(workspace=selected_workspace, home=home):
                    with self.assertRaisesRegex(
                        PullError, "separate, non-nested"
                    ):
                        run_pull(
                            workspace=selected_workspace,
                            provenance_home=home,
                        )
            invoke.assert_not_called()

    def test_missing_public_command_is_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            with (
                mock.patch(
                    "scriptorium.pull.resolve_component_root",
                    side_effect=RuntimeError("not found"),
                ),
                mock.patch("scriptorium.pull.shutil.which", return_value=None),
            ):
                with self.assertRaisesRegex(PullError, "prov-sync-pull.*unavailable"):
                    run_pull(workspace=workspace, provenance_home=home)

    def test_explicit_source_version_is_fixed_by_compatibility_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "provenance"\nversion = "9.9.9"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PullError, "incompatible Provenance"):
                _resolve_provenance(root, expected_version="0.17.0")

    def test_non_json_extra_stdout_and_timeout_fail_closed(self):
        with self.assertRaisesRegex(PullError, "non-JSON or extra stdout"):
            _parse_component_report(
                json.dumps(component_report()) + "\nlog line",
                expected_mode="preview",
                expected_version="0.17.0",
                process_exit_code=0,
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            with (
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("prov-sync-pull", 60),
                ),
            ):
                with self.assertRaisesRegex(PullError, "timed out"):
                    run_pull(workspace=workspace, provenance_home=home)

    def test_process_start_error_is_suppressed_and_wrapped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            with (
                mock.patch(
                    "scriptorium.pull._resolve_provenance",
                    return_value=(None, Path("prov-sync-pull")),
                ),
                mock.patch(
                    "scriptorium.pull.subprocess.run",
                    side_effect=OSError("private process detail"),
                ),
            ):
                with self.assertRaisesRegex(PullError, "could not run") as raised:
                    run_pull(workspace=workspace, provenance_home=home)
            self.assertNotIn("private process detail", str(raised.exception))

    def test_report_exit_one_is_trusted_when_process_and_status_match(self):
        report, producer = _parse_component_report(
            json.dumps(component_report(status="blocked", exit_code=1)),
            expected_mode="preview",
            expected_version="0.17.0",
            process_exit_code=1,
        )
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(producer["version"], "0.17.0")

    def test_report_mismatch_or_wrong_producer_is_rejected(self):
        with self.assertRaisesRegex(PullError, "exit code disagrees"):
            _parse_component_report(
                json.dumps(component_report()),
                expected_mode="preview",
                expected_version="0.17.0",
                process_exit_code=1,
            )
        wrong = component_report()
        wrong["generated_by"]["version"] = "0.16.0"
        with self.assertRaisesRegex(PullError, "producer is incompatible"):
            _parse_component_report(
                json.dumps(wrong),
                expected_mode="preview",
                expected_version="0.17.0",
                process_exit_code=0,
            )

    def test_report_types_are_strict_and_unknown_fields_are_dropped(self):
        boolean_version = component_report()
        boolean_version["format_version"] = True
        with self.assertRaisesRegex(PullError, "incompatible report envelope"):
            _parse_component_report(
                json.dumps(boolean_version),
                expected_mode="preview",
                expected_version="0.17.0",
                process_exit_code=0,
            )

        non_string_status = component_report()
        non_string_status["status"] = ["planned"]
        with self.assertRaisesRegex(PullError, "incompatible status"):
            _parse_component_report(
                json.dumps(non_string_status),
                expected_mode="preview",
                expected_version="0.17.0",
                process_exit_code=0,
            )

        non_integer_exit = component_report()
        non_integer_exit["exit_code"] = [0]
        with self.assertRaisesRegex(PullError, "unsupported exit code"):
            _parse_component_report(
                json.dumps(non_integer_exit),
                expected_mode="preview",
                expected_version="0.17.0",
                process_exit_code=0,
            )

        additive = component_report()
        additive["private_future_field"] = "must not cross the entry boundary"
        additive["generated_by"]["private_future_field"] = "must not cross"
        parsed, producer = _parse_component_report(
            json.dumps(additive),
            expected_mode="preview",
            expected_version="0.17.0",
            process_exit_code=0,
        )
        self.assertNotIn("private_future_field", parsed)
        self.assertEqual(producer, {"name": "provenance", "version": "0.17.0"})

    def test_nested_component_data_is_rebuilt_as_aggregate_only(self):
        report = component_report(status="blocked", exit_code=1)
        report["private_future_field"] = "private-top-level"
        report["egress"]["private_route"] = "D:/private/connector"
        report["stages"][0]["artifact"] = "D:/private/sync-state.jsonl"
        report["stages"][0]["counts"]["private_sessions"] = "private-session"
        report["stages"].append(
            {
                "id": "future-private-stage",
                "status": "private-status",
                "detail": "private-stage-detail",
            }
        )
        report["summary"]["private_text"] = "private-research-claim"
        report["action_required"] = [
            {
                "type": "agent-fill",
                "count": 1,
                "artifact": "D:/private/scaffold.json",
            },
            {
                "type": "project-resolution",
                "count": 2,
                "cwd": "D:/private/unmapped",
            },
        ]
        report["errors"] = [
            {
                "code": "worker_busy",
                "message": "private worker message",
                "detail": "D:/private/worker.lock",
            },
            {
                "code": "worker_state_failed",
                "message": "private queue state message",
            },
            {
                "code": "lock_release_failed",
                "message": "private lock release message",
            },
        ]
        report["limitations"] = ["private component limitation"]

        parsed, _ = _parse_component_report(
            json.dumps(report),
            expected_mode="preview",
            expected_version="0.17.0",
            process_exit_code=1,
        )

        self.assertEqual(
            parsed["egress"],
            {
                "suite_managed": "not-requested",
                "host_managed": "not-invoked",
                "optional_connectors": "not-invoked",
            },
        )
        self.assertEqual(
            parsed["stages"],
            [
                {
                    "id": "sync_state",
                    "status": "ok",
                    "counts": {"files": 0, "records": 0, "invalid": 0},
                }
            ],
        )
        self.assertEqual(set(parsed["summary"]), set(SUMMARY_FIELDS))
        self.assertTrue(all(type(value) is int for value in parsed["summary"].values()))
        self.assertEqual(
            parsed["action_required"],
            [
                {"type": "agent-fill", "count": 1},
                {"type": "project-resolution", "count": 2},
            ],
        )
        self.assertEqual(
            parsed["errors"],
            [
                {"code": "worker_busy"},
                {"code": "worker_state_failed"},
                {"code": "lock_release_failed"},
            ],
        )
        self.assertEqual(parsed["limitations"], list(ENTRY_LIMITATIONS))
        self.assertNotIn("private", json.dumps(parsed).casefold())

    def test_required_nested_semantics_fail_closed(self):
        missing_summary = component_report()
        del missing_summary["summary"]["projects"]

        invalid_summary_count = component_report()
        invalid_summary_count["summary"]["codex_found"] = True

        invalid_stage_status = component_report()
        invalid_stage_status["stages"][0]["status"] = "completed"

        invalid_stage_count = component_report()
        invalid_stage_count["stages"][0]["counts"]["records"] = "1"

        unknown_action = component_report()
        unknown_action["action_required"] = [{"type": "private-action", "count": 1}]

        wrapper_only_action = component_report()
        wrapper_only_action["action_required"] = [
            {"type": "codex-home-setup", "count": 1}
        ]

        missing_action_count = component_report()
        missing_action_count["action_required"] = [{"type": "agent-fill"}]

        unknown_error = component_report(status="blocked", exit_code=1)
        unknown_error["errors"] = [{"code": "private-error"}]

        incompatible_egress = component_report()
        incompatible_egress["egress"]["suite_managed"] = "requested"

        cases = (
            missing_summary,
            invalid_summary_count,
            invalid_stage_status,
            invalid_stage_count,
            unknown_action,
            wrapper_only_action,
            missing_action_count,
            unknown_error,
            incompatible_egress,
        )
        for report in cases:
            with self.subTest(report=report):
                with self.assertRaises(PullError):
                    _parse_component_report(
                        json.dumps(report),
                        expected_mode="preview",
                        expected_version="0.17.0",
                        process_exit_code=report["exit_code"],
                    )

    def test_text_report_only_contains_aggregate_safe_information(self):
        report = component_report(mode="run", status="action-required")
        report["generated_by"] = {"name": "scriptorium", "version": "0.1.0"}
        report["entry"] = {"scan_codex": True}
        report["summary"]["private_text"] = "secret claim"
        report["action_required"] = [
            {"type": "agent-fill", "count": 1, "artifact": "D:/private/scaffold.json"},
            {"type": "private-action", "count": 99},
        ]
        report["errors"] = [
            {"code": "worker_busy", "detail": "D:/private/worker.lock"},
            {"code": "private_error", "detail": "secret"},
        ]
        report["limitations"] = ["private limitation"]
        report["path_selection"] = {
            "workspace": {
                "source": "environment",
                "environment": "SCRIPTORIUM_WORKSPACE",
                "suite_config_conflict": True,
            }
        }
        report["warnings"] = [
            {
                "code": "environment_suite_config_conflict",
                "path": "workspace",
            }
        ]
        rendered = format_pull_report(report)
        self.assertIn("codex_found=1", rendered)
        self.assertIn("Actions: agent-fill=1", rendered)
        self.assertIn("Errors: worker_busy", rendered)
        self.assertIn("workspace: environment (SCRIPTORIUM_WORKSPACE)", rendered)
        self.assertIn("pull --run is blocked", rendered)
        self.assertNotIn("secret claim", rendered)
        self.assertNotIn("D:/private", rendered)
        self.assertNotIn("private-action", rendered)
        self.assertNotIn("private_error", rendered)
        self.assertNotIn("private limitation", rendered)


class PullCliTests(unittest.TestCase):
    def test_json_report_is_single_object_and_component_exit_one_is_returned(self):
        report = component_report(status="blocked", exit_code=1)
        report["generated_by"] = {"name": "scriptorium", "version": "0.1.0"}
        report["entry"] = {"scan_codex": False}
        stdout = io.StringIO()
        with (
            mock.patch("scriptorium.cli.run_pull", return_value=report),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.main(
                [
                    "pull",
                    "--workspace",
                    "workspace",
                    "--provenance-home",
                    "provenance-home",
                    "--json",
                ]
            )
        parsed = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(parsed["operation"], "pull")
        self.assertEqual(parsed["status"], "blocked")

    def test_pull_error_uses_json_error_envelope_and_exit_two(self):
        stdout = io.StringIO()
        with (
            mock.patch(
                "scriptorium.cli.run_pull",
                side_effect=PullError("untrusted component output"),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.main(
                [
                    "pull",
                    "--workspace",
                    "workspace",
                    "--provenance-home",
                    "provenance-home",
                    "--run",
                    "--json",
                ]
            )
        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["mode"], "run")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["errors"], [{"code": "entry_error"}])
        self.assertEqual(report["action_required"], [])
        self.assertEqual(report["stages"], [])
        self.assertEqual(report["summary"], {})
        self.assertIn("egress", report)
        self.assertIn("entry", report)
        self.assertNotIn("error", report)
        self.assertNotIn("untrusted component output", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
