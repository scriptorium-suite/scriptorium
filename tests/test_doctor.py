import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium import cli
from scriptorium.doctor import (
    DoctorError,
    TARGETS,
    _agent_host_check,
    _browser_extension_check,
    _build_report,
    _check,
    _find_application,
    _host_adapter_check,
    _agent_capture_check,
    _entry_pull_check,
    _project_metadata,
    _provenance_home_check,
    _provenance_runtime_version,
    _resolve_lectern_root,
    _run_probe,
    _workspace_check,
    format_doctor_report,
    run_doctor,
)
from scriptorium.host import run_host_install


def required_pass(check_id: str, *, target: str) -> dict:
    return _check(
        check_id,
        target=target,
        required_for=TARGETS,
        passed=True,
        summary=f"{check_id} passed",
    )


def pull_capability_payload() -> dict:
    return {
        "format_version": 1,
        "operation": "pull.capabilities",
        "status": "ok",
        "exit_code": 0,
        "generated_by": {"name": "provenance", "version": "0.17.0"},
        "capabilities": {
            "dry_run_default": True,
            "explicit_run": True,
            "structured_report": True,
            "pull_lock": True,
            "codex_scan": True,
            "applies_checked_approvals": True,
            "model_calls": False,
            "installs_hooks": False,
            "network_requests": False,
        },
    }


def capability_checks(*, target: str) -> list[dict]:
    return [
        _check(
            "component.steward",
            target=target,
            required_for=TARGETS,
            passed=True,
            summary="Steward passed",
        ),
        _check(
            "integration.zotero",
            target=target,
            passed=False,
            summary="Zotero unavailable",
        ),
        _check(
            "integration.lectern",
            target=target,
            passed=False,
            summary="Lectern unavailable",
        ),
        _check(
            "integration.powerpoint",
            target=target,
            passed=False,
            summary="PowerPoint unavailable",
        ),
        _check(
            "capture.browser-extension",
            target=target,
            passed=True,
            manual=True,
            summary="Manual browser check",
        ),
    ]


class DoctorReportTests(unittest.TestCase):
    def test_check_is_fail_only_when_required_for_selected_target(self):
        demo = _check(
            "host.adapter",
            target="demo",
            required_for=("public-alpha",),
            passed=False,
            summary="missing",
            remediation="install",
        )
        public = _check(
            "host.adapter",
            target="public-alpha",
            required_for=("public-alpha",),
            passed=False,
            summary="missing",
            remediation="install",
        )
        passed = _check(
            "runtime.git",
            target="demo",
            required_for=TARGETS,
            passed=True,
            summary="available",
            remediation="install",
        )
        self.assertEqual(demo["status"], "info")
        self.assertEqual(public["status"], "fail")
        self.assertIsNone(passed["remediation"])

    def test_demo_can_be_ready_while_public_alpha_is_incomplete(self):
        target = "demo"
        checks = [
            required_pass("runtime.python", target=target),
            required_pass("component.spec", target=target),
            required_pass("component.provenance", target=target),
            _check(
                "host.adapter",
                target=target,
                required_for=("public-alpha",),
                passed=False,
                summary="adapter missing",
            ),
            *capability_checks(target=target),
        ]
        report = _build_report(target, checks, [])
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["readiness"]["demo"], "ready")
        self.assertEqual(report["readiness"]["public_alpha"], "incomplete")
        self.assertEqual(report["readiness"]["literature"], "file-only")

    def test_public_alpha_cannot_be_ready_when_demo_requirement_fails(self):
        target = "public-alpha"
        checks = [
            required_pass("runtime.python", target=target),
            required_pass("component.spec", target=target),
            required_pass("component.provenance", target=target),
            _check(
                "component.steward",
                target=target,
                required_for=TARGETS,
                passed=False,
                summary="Steward missing",
            ),
            *capability_checks(target=target)[1:],
        ]
        report = _build_report(target, checks, [])
        self.assertEqual(report["readiness"]["demo"], "incomplete")
        self.assertEqual(report["readiness"]["public_alpha"], "incomplete")
        self.assertEqual(report["exit_code"], 1)

    def test_public_alpha_required_failure_returns_one(self):
        target = "public-alpha"
        checks = [
            required_pass("runtime.python", target=target),
            required_pass("component.spec", target=target),
            required_pass("component.provenance", target=target),
            _check(
                "host.adapter",
                target=target,
                required_for=("public-alpha",),
                passed=False,
                summary="adapter missing",
                remediation="install adapter",
            ),
            *capability_checks(target=target),
        ]
        report = _build_report(target, checks, [])
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["summary"]["fail"], 1)

    def test_human_output_is_stable_plain_text_with_fix(self):
        target = "public-alpha"
        checks = [
            required_pass("runtime.python", target=target),
            _check(
                "host.adapter",
                target=target,
                required_for=("public-alpha",),
                passed=False,
                summary="adapter missing",
                remediation="install adapter",
            ),
            _check(
                "integration.obsidian",
                target=target,
                passed=False,
                summary="optional editor missing",
                remediation="Install only if desired.",
            ),
            *capability_checks(target=target),
        ]
        report = _build_report(target, checks, [])
        report["path_selection"] = {
            "data_root": {
                "source": "environment",
                "environment": "PROVENANCE_HOME",
                "suite_config_conflict": True,
            }
        }
        report["warnings"] = [
            {
                "code": "environment_suite_config_conflict",
                "path": "data_root",
            }
        ]
        rendered = format_doctor_report(report)
        self.assertIn("FAIL  host.adapter", rendered)
        self.assertIn("Fix: install adapter", rendered)
        self.assertIn("Next: Install only if desired.", rendered)
        self.assertIn("Public Alpha readiness: INCOMPLETE", rendered)
        self.assertIn("Not tested:", rendered)
        self.assertIn("local filesystem paths", rendered)
        self.assertIn("data_root: environment (PROVENANCE_HOME)", rendered)
        self.assertIn("WARNING:", rendered)
        self.assertIn("1 path-selection warnings", rendered)
        self.assertNotIn("\x1b", rendered)


class DoctorProbeTests(unittest.TestCase):
    def test_invalid_lectern_environment_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-lectern"
            with mock.patch.dict(
                os.environ,
                {"SCRIPTORIUM_LECTERN_ROOT": str(missing)},
                clear=False,
            ):
                self.assertIsNone(_resolve_lectern_root(None))

    def test_probe_environment_does_not_inherit_provider_secret(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-sentinel"}):
            output = _run_probe(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import os; print(os.environ.get('OPENAI_API_KEY', 'absent'))",
                ]
            )
        self.assertEqual(output, "absent")

    def test_probe_environment_preserves_local_home_and_temp_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile"
            runtime_temp = root / "temp"
            home.mkdir()
            runtime_temp.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "USERPROFILE": str(home),
                    "TEMP": str(runtime_temp),
                    "TMP": str(runtime_temp),
                },
                clear=False,
            ):
                output = _run_probe(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        (
                            "import json, os, pathlib; "
                            "print(json.dumps({"
                            "'home': str(pathlib.Path.home()), "
                            "'temp': os.environ.get('TEMP')"
                            "}))"
                        ),
                    ]
                )
        observed = json.loads(output)
        self.assertEqual(Path(observed["home"]), home)
        self.assertEqual(Path(observed["temp"]), runtime_temp)

    def test_project_metadata_requires_public_name_and_version_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.2.3"\n',
                encoding="utf-8",
            )
            self.assertEqual(_project_metadata(root), {"name": "demo", "version": "1.2.3"})

    def test_provenance_version_comes_from_mcp_initialize(self):
        response = {
            "jsonrpc": "2.0",
            "id": 0,
            "result": {"serverInfo": {"name": "provenance", "version": "0.17.0"}},
        }
        with mock.patch("scriptorium.doctor._run_probe", return_value=json.dumps(response)) as run:
            version = _provenance_runtime_version(Path("prov-mcp"), Path("root"))
        self.assertEqual(version, "0.17.0")
        self.assertIn('"method":"initialize"', run.call_args.kwargs["input_text"])
        self.assertEqual(run.call_args.kwargs["extra_env"]["PROVENANCE_HOME"], os.devnull)

    def test_agent_host_must_execute_a_version_probe(self):
        def which(name: str) -> str | None:
            return "codex.exe" if name == "codex" else None

        with mock.patch("scriptorium.doctor.shutil.which", side_effect=which), mock.patch(
            "scriptorium.doctor._run_probe", return_value="codex-cli 1.0.0"
        ) as run:
            check, hosts = _agent_host_check("public-alpha")
        self.assertEqual(check["status"], "pass")
        self.assertEqual(hosts[0]["name"], "codex")
        run.assert_called_once_with(["codex.exe", "--version"])

    def test_empty_agent_version_is_a_diagnostic_failure(self):
        with mock.patch("scriptorium.doctor.shutil.which", return_value="codex.exe"), mock.patch(
            "scriptorium.doctor._run_probe", return_value=""
        ):
            check, hosts = _agent_host_check("public-alpha")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(hosts, [])

    def test_agent_host_reports_a_failed_secondary_probe(self):
        def which(name: str) -> str:
            return f"{name}.exe"

        def probe(command: list[str]) -> str:
            if command[0] == "codex.exe":
                return "codex-cli 1.0.0"
            raise DoctorError("probe failed")

        with mock.patch("scriptorium.doctor.shutil.which", side_effect=which), mock.patch(
            "scriptorium.doctor._run_probe", side_effect=probe
        ):
            check, hosts = _agent_host_check("public-alpha")
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["details"]["failed_probes"], ["claude-code"])
        self.assertIn("version probe failed: claude-code", check["summary"])

    def test_application_detection_uses_registry_after_path(self):
        registered = Path("C:/Program Files/Demo/demo.exe")
        with mock.patch("scriptorium.doctor.shutil.which", return_value=None), mock.patch(
            "scriptorium.doctor._registry_application", return_value=registered
        ):
            self.assertEqual(_find_application("demo.exe", ()), registered)

    def test_browser_bundle_never_claims_browser_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "01-capture" / "manifest.json"
            manifest.parent.mkdir()
            (manifest.parent / "content.js").write_text("", encoding="utf-8")
            (manifest.parent / "popup.html").write_text("", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": 3,
                        "name": "Provenance",
                        "version": "0.1.0",
                        "content_scripts": [
                            {
                                "matches": ["https://chatgpt.com/*"],
                                "js": ["content.js"],
                            }
                        ],
                        "action": {"default_popup": "popup.html"},
                    }
                ),
                encoding="utf-8",
            )
            check = _browser_extension_check("demo", root)
        self.assertEqual(check["status"], "manual")
        self.assertEqual(check["details"]["bundle_validation"], "passed")
        self.assertEqual(check["details"]["browser_installation"], "not tested")

    def test_invalid_browser_bundle_is_not_reported_as_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "01-capture" / "manifest.json"
            manifest.parent.mkdir()
            manifest.write_text("{}", encoding="utf-8")
            check = _browser_extension_check("demo", root)
        self.assertEqual(check["status"], "info")
        self.assertNotEqual(check["details"]["bundle_validation"], "passed")
        self.assertIn("failed static validation", check["summary"])
        self.assertIn(
            "version must contain one to four numeric components",
            check["details"]["bundle_validation"],
        )

    def test_non_injecting_browser_bundle_fails_static_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "01-capture" / "manifest.json"
            manifest.parent.mkdir()
            (manifest.parent / "popup.html").write_text("", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": 3,
                        "name": "Provenance",
                        "version": "0.1.0",
                        "content_scripts": [{"matches": [], "js": []}],
                        "action": {"default_popup": "popup.html"},
                    }
                ),
                encoding="utf-8",
            )
            check = _browser_extension_check("demo", root)
        errors = check["details"]["bundle_validation"]
        self.assertEqual(check["status"], "info")
        self.assertIn("content_scripts[0].matches is missing", errors)
        self.assertIn("content_scripts[0].js is missing", errors)

    def test_malformed_provenance_response_is_a_diagnostic_error(self):
        malformed = json.dumps(
            {"jsonrpc": "2.0", "id": 0, "result": {"serverInfo": "not-an-object"}}
        )
        with mock.patch("scriptorium.doctor._run_probe", return_value=malformed):
            with self.assertRaisesRegex(DoctorError, "no valid runtime version"):
                _provenance_runtime_version(Path("prov-mcp"), Path("root"))

    def test_empty_workspace_is_not_public_alpha_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            check = _workspace_check("public-alpha", Path(temporary))
        self.assertEqual(check["status"], "fail")
        self.assertFalse(check["details"]["markdown_detected"])

    def test_repository_readme_is_not_a_compatible_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("# Project", encoding="utf-8")
            check = _workspace_check("public-alpha", root)
        self.assertEqual(check["status"], "fail")
        self.assertIsNone(check["details"]["project_note"])

    def test_project_contract_note_is_a_compatible_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "Projects"
            projects.mkdir()
            project = projects / "catalyst.md"
            project.write_text(
                "---\n"
                "schema_version: project/1.0\n"
                "project_id: catalyst\n"
                "title: Catalyst discovery\n"
                "status: active\n"
                "---\n\n# Catalyst\n",
                encoding="utf-8",
            )
            check = _workspace_check("public-alpha", root)
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["details"]["project_note"], str(project.resolve()))

    def test_non_string_required_project_field_is_not_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "Projects"
            projects.mkdir()
            (projects / "invalid.md").write_text(
                "---\n"
                "schema_version: project/1.0\n"
                "project_id: catalyst\n"
                "title: 42\n"
                "status: active\n"
                "---\n",
                encoding="utf-8",
            )

            check = _workspace_check("public-alpha", root)

        self.assertEqual(check["status"], "fail")
        self.assertFalse(check["details"]["markdown_detected"])

    def test_host_adapter_requires_a_canonical_skill_for_the_detected_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_host_install(workspace=root, host="codex")
            codex = [{"name": "codex", "path": "codex", "version": "codex 1.0"}]
            matching = _host_adapter_check("public-alpha", codex, root)
            mismatched = _host_adapter_check(
                "public-alpha",
                [{"name": "claude-code", "path": "claude", "version": "1.0"}],
                root,
            )
            self.assertEqual(matching["status"], "pass")
            self.assertEqual(matching["details"]["ready_hosts"], ["codex"])
            self.assertEqual(mismatched["status"], "fail")

    def test_provenance_home_must_be_explicit_and_existing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            missing = _provenance_home_check("public-alpha", None)
        self.assertEqual(missing["status"], "fail")
        self.assertEqual(missing["details"]["write_probe"], "not-run")

        with tempfile.TemporaryDirectory() as temporary:
            ready = _provenance_home_check("public-alpha", Path(temporary))
        self.assertEqual(ready["status"], "pass")
        self.assertEqual(ready["details"]["source"], "argument")

    def test_provenance_home_and_workspace_must_not_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            nested = workspace / ".provenance"
            sibling = root / "provenance-home"
            outer_home = root / "outer-provenance"
            nested_workspace = outer_home / "workspace"
            for directory in (workspace, nested, sibling, nested_workspace):
                directory.mkdir(parents=True, exist_ok=True)

            same = _provenance_home_check(
                "public-alpha", workspace, workspace
            )
            inside = _provenance_home_check(
                "public-alpha", nested, workspace
            )
            contains_workspace = _provenance_home_check(
                "public-alpha", outer_home, nested_workspace
            )
            separate = _provenance_home_check(
                "public-alpha", sibling, workspace
            )

        self.assertEqual(same["status"], "fail")
        self.assertEqual(inside["status"], "fail")
        self.assertEqual(contains_workspace["status"], "fail")
        self.assertEqual(same["details"]["workspace_separation"], "conflict")
        self.assertIn("non-nested", inside["details"]["error"])
        self.assertEqual(separate["status"], "pass")
        self.assertEqual(separate["details"]["workspace_separation"], "separate")

    def test_codex_capture_is_executable_but_claude_hook_stays_manual(self):
        codex = _check(
            "host.adapter",
            target="public-alpha",
            required_for=("public-alpha",),
            passed=True,
            summary="ready",
            details={"ready_hosts": ["codex"]},
        )
        claude = _check(
            "host.adapter",
            target="public-alpha",
            required_for=("public-alpha",),
            passed=True,
            summary="ready",
            details={"ready_hosts": ["claude-code"]},
        )
        self.assertEqual(_agent_capture_check("public-alpha", codex)["status"], "pass")
        self.assertEqual(_agent_capture_check("public-alpha", claude)["status"], "manual")

    def test_codex_scan_location_prefers_codex_home_then_profile_default(self):
        codex = _check(
            "host.adapter",
            target="public-alpha",
            required_for=("public-alpha",),
            passed=True,
            summary="ready",
            details={"ready_hosts": ["codex"]},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "custom-codex-home"
            profile = root / "profile"
            (configured / "sessions").mkdir(parents=True)
            profile.mkdir()

            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(configured), "USERPROFILE": str(profile)},
                clear=True,
            ):
                configured_check = _agent_capture_check("public-alpha", codex)
            with mock.patch.dict(
                os.environ,
                {"USERPROFILE": str(profile)},
                clear=True,
            ):
                default_check = _agent_capture_check("public-alpha", codex)

        configured_location = configured_check["details"]["codex_scan_location"]
        default_location = default_check["details"]["codex_scan_location"]
        self.assertEqual(configured_location["source"], "CODEX_HOME")
        self.assertEqual(Path(configured_location["root"]), configured.resolve())
        self.assertTrue(configured_location["sessions_exist"])
        self.assertEqual(default_location["source"], "profile-default")
        self.assertEqual(Path(default_location["root"]), (profile / ".codex").resolve())
        self.assertEqual(default_location["state"], "missing")
        self.assertEqual(
            default_check["details"]["codex_scan"], "home-unavailable"
        )
        self.assertIsNotNone(default_check["details"]["action_required"])

    def test_missing_codex_home_is_an_actionable_zero_session_state(self):
        codex = _check(
            "host.adapter",
            target="public-alpha",
            required_for=("public-alpha",),
            passed=True,
            summary="ready",
            details={"ready_hosts": ["codex"]},
        )
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-codex-home"
            with mock.patch.dict(
                os.environ, {"CODEX_HOME": str(missing)}, clear=True
            ):
                capture = _agent_capture_check("public-alpha", codex)

            self.assertEqual(capture["status"], "pass")
            self.assertEqual(
                capture["details"]["codex_scan"], "configured-home-missing"
            )
            self.assertIn("zero sessions", capture["summary"])
            self.assertIsNotNone(capture["details"]["action_required"])
            self.assertFalse(missing.exists())

    def test_pull_requires_a_compatible_public_capability_probe(self):
        public = _entry_pull_check("public-alpha", None)
        demo = _entry_pull_check("demo", None)
        self.assertEqual(public["status"], "fail")
        self.assertEqual(demo["status"], "info")
        self.assertFalse(public["details"]["implemented"])

        payload = pull_capability_payload()
        with mock.patch(
            "scriptorium.doctor.find_script", return_value=Path("prov-sync-pull")
        ), mock.patch("scriptorium.doctor._run_probe", return_value=json.dumps(payload)):
            ready = _entry_pull_check("public-alpha", Path("Provenance"), "0.17.0")
        self.assertEqual(ready["status"], "pass")
        self.assertTrue(ready["details"]["implemented"])

    def test_pull_rejects_an_incomplete_capability_probe(self):
        payload = pull_capability_payload()
        payload["capabilities"] = {"dry_run_default": True}
        with mock.patch(
            "scriptorium.doctor.find_script", return_value=Path("prov-sync-pull")
        ), mock.patch("scriptorium.doctor._run_probe", return_value=json.dumps(payload)):
            check = _entry_pull_check("public-alpha", Path("Provenance"), "0.17.0")
        self.assertEqual(check["status"], "fail")
        self.assertIn("missing guarantees", check["details"]["error"])

    def test_pull_rejects_inconsistent_probe_status_or_exit_code(self):
        for status, exit_code in (("error", 0), ("ok", 2)):
            with self.subTest(status=status, exit_code=exit_code):
                payload = pull_capability_payload()
                payload["status"] = status
                payload["exit_code"] = exit_code
                with mock.patch(
                    "scriptorium.doctor.find_script",
                    return_value=Path("prov-sync-pull"),
                ), mock.patch(
                    "scriptorium.doctor._run_probe",
                    return_value=json.dumps(payload),
                ):
                    check = _entry_pull_check(
                        "public-alpha", Path("Provenance"), "0.17.0"
                    )
                self.assertEqual(check["status"], "fail")
                self.assertIn("incompatible shape", check["details"]["error"])

    def test_pull_command_must_share_the_provenance_command_environment(self):
        payload = pull_capability_payload()

        def locate(_root: Path, name: str) -> Path:
            directory = (
                "pull-environment"
                if name == "prov-sync-pull"
                else "base-environment"
            )
            return Path(directory) / name

        with mock.patch(
            "scriptorium.doctor.find_script", side_effect=locate
        ), mock.patch(
            "scriptorium.doctor._run_probe", return_value=json.dumps(payload)
        ) as probe:
            check = _entry_pull_check(
                "public-alpha", Path("Provenance"), "0.17.0"
            )

        self.assertEqual(check["status"], "fail")
        self.assertIn("different Provenance environment", check["details"]["error"])
        probe.assert_not_called()

    def test_invalid_compatibility_shape_is_a_doctor_error(self):
        with mock.patch(
            "scriptorium.doctor.load_compatibility", side_effect=AttributeError("items")
        ):
            with self.assertRaisesRegex(DoctorError, "compatibility manifest is invalid"):
                run_doctor(target="demo")


class DoctorCliTests(unittest.TestCase):
    def test_json_output_reconfigures_a_narrow_console_encoding(self):
        report = {"exit_code": 0, "workspace": "D:/科研/🧪"}
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw, encoding="gbk", errors="strict")
        with mock.patch.object(sys, "stdout", stdout), mock.patch(
            "scriptorium.cli.run_doctor", return_value=report
        ):
            exit_code = cli.main(["doctor", "--json"])
            stdout.flush()
            payload = raw.getvalue()
            stdout.detach()
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(payload.decode("utf-8")), report)

    def test_json_mode_prints_one_object_and_returns_report_code(self):
        report = {
            "format_version": 1,
            "generated_by": {"name": "scriptorium", "version": "0.1.0"},
            "target": "public-alpha",
            "status": "incomplete",
            "exit_code": 1,
            "readiness": {},
            "checks": [],
            "egress": [],
            "summary": {},
            "limitations": [],
        }
        stdout = io.StringIO()
        with mock.patch("scriptorium.cli.run_doctor", return_value=report), contextlib.redirect_stdout(
            stdout
        ):
            exit_code = cli.main(["doctor", "--json"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue()), report)

    def test_doctor_internal_error_uses_exit_two_and_stderr(self):
        stderr = io.StringIO()
        with mock.patch(
            "scriptorium.cli.run_doctor", side_effect=DoctorError("manifest invalid")
        ), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(["doctor"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr.getvalue().strip(), "ERROR: manifest invalid")


if __name__ == "__main__":
    unittest.main()
