import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium.demo import (
    DemoError,
    MARKER_VALUE,
    _clear_managed_files,
    _present_artifacts,
    _safe_destination,
    _write_failed_report,
    find_script,
    isolated_environment,
    load_compatibility,
    prepare_output,
    project_version,
    run_demo,
)
from importlib import resources


class DemoUnitTests(unittest.TestCase):
    def test_compatibility_manifest_locks_alpha_baseline(self):
        self.assertEqual(
            load_compatibility(),
            {
                "scriptorium-spec": "2.2.0",
                "steward": "0.2.0",
                "provenance": "0.17.0",
            },
        )

    def test_demo_library_fixture_is_explicitly_synthetic_and_versioned(self):
        payload = resources.files("scriptorium.assets").joinpath("library-kb.v1.1.json").read_text(
            encoding="utf-8"
        )
        data = json.loads(payload)
        self.assertEqual(data["schema_version"], "library-kb/1.1")
        self.assertEqual(data["generated_by"], "scriptorium demo fixture")
        self.assertEqual([item["key"] for item in data["items"]], ["DEMO0001", "DEMO0002", "DEMO0003"])

    def test_prepare_output_reuses_only_owned_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "demo"
            prepared = prepare_output(root)
            evidence = prepared / "keep.txt"
            evidence.write_text("owned", encoding="utf-8")
            self.assertEqual(prepare_output(root), prepared)
            self.assertEqual(evidence.read_text(encoding="utf-8"), "owned")

    def test_prepare_output_rejects_nonempty_unowned_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "demo"
            root.mkdir()
            (root / "user-file.txt").write_text("do not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(DemoError, "not an owned demo directory"):
                prepare_output(root)

    def test_prepare_output_rejects_linklike_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "demo"
            root.mkdir()
            marker = root / ".scriptorium-demo"
            marker.write_text(MARKER_VALUE, encoding="utf-8")
            with mock.patch(
                "scriptorium.demo._is_linklike",
                side_effect=lambda path: path.resolve() == marker.resolve(),
            ):
                with self.assertRaisesRegex(DemoError, "output marker cannot be"):
                    prepare_output(root)

    def test_project_version_reads_public_package_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8"
            )
            self.assertEqual(project_version(root), "1.2.3")

    def test_find_script_prefers_source_virtual_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = root / ".venv" / "Scripts" / "demo.exe"
            command.parent.mkdir(parents=True)
            command.write_bytes(b"")
            self.assertEqual(find_script(root, "demo"), command)

    def test_find_script_checks_the_active_virtual_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "component"
            root.mkdir()
            active = Path(temporary) / "active" / "Scripts"
            active.mkdir(parents=True)
            python = active / "python.exe"
            command = active / "demo.exe"
            python.write_bytes(b"")
            command.write_bytes(b"")
            with mock.patch("scriptorium.demo.sys.executable", str(python)), mock.patch(
                "scriptorium.demo.shutil.which", return_value=None
            ):
                self.assertEqual(find_script(root, "demo"), command.resolve())

    def test_isolated_environment_removes_provider_credentials(self):
        names = [
            "APPDATA",
            "LOCALAPPDATA",
            "OPENAI_API_KEY",
            "ZOTERO_API_KEY",
            "LECTERN_PROVIDER_TOKEN",
            "PYTHONPATH",
        ]
        old = {name: os.environ.get(name) for name in names}
        try:
            for name in names:
                os.environ[name] = "secret-sentinel"
            config_dir = Path("config")
            env = isolated_environment(
                provenance_home=Path("provenance"),
                workspace=Path("workspace"),
                config_dir=config_dir,
            )
            for name in names[2:]:
                self.assertNotIn(name, env)
            self.assertEqual(
                env["APPDATA"], str(config_dir / "home" / "AppData" / "Roaming")
            )
            self.assertEqual(
                env["LOCALAPPDATA"],
                str(config_dir / "home" / "AppData" / "Local"),
            )
            self.assertNotEqual(env["APPDATA"], "secret-sentinel")
            self.assertNotEqual(env["LOCALAPPDATA"], "secret-sentinel")
            self.assertEqual(env["PYTHONUTF8"], "1")
            self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_clear_managed_files_prevents_stale_success_without_touching_other_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = prepare_output(Path(temporary) / "demo")
            managed = root / "workspace" / "report.txt"
            unrelated = root / "user-note.txt"
            managed.parent.mkdir(parents=True)
            managed.write_text("stale", encoding="utf-8")
            unrelated.write_text("keep", encoding="utf-8")
            _clear_managed_files(root, [managed])
            self.assertFalse(managed.exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_safe_destination_rejects_linklike_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            trapped = root / "workspace"
            target = trapped / "review.md"
            with mock.patch("scriptorium.demo._is_linklike", side_effect=lambda path: path == trapped):
                with self.assertRaisesRegex(DemoError, "symlink or junction"):
                    _safe_destination(root, target)

    def test_runtime_failure_writes_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            roots = {}
            for name, version in {
                "spec": "9.9.9",
                "steward": "0.2.0",
                "provenance": "0.17.0",
            }.items():
                root = base / name
                root.mkdir()
                (root / "pyproject.toml").write_text(
                    f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
                )
                roots[name] = root
            output = base / "output"
            with self.assertRaisesRegex(DemoError, "incompatible scriptorium-spec"):
                run_demo(
                    output,
                    spec_root=roots["spec"],
                    steward_root=roots["steward"],
                    provenance_root=roots["provenance"],
                )
            report = json.loads((output / "demo-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["demo_status"], "failed")
            self.assertIn("incompatible scriptorium-spec", report["error"])

    def test_failed_report_does_not_write_through_linklike_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report_path = root / "demo-report.json"
            report_path.write_text("outside sentinel", encoding="utf-8")
            with mock.patch(
                "scriptorium.demo._is_linklike",
                side_effect=lambda path: path == report_path,
            ):
                _write_failed_report(root, report_path, {"demo_status": "failed"})
            self.assertEqual(report_path.read_text(encoding="utf-8"), "outside sentinel")

    def test_failed_report_does_not_write_through_real_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "demo"
            root.mkdir()
            outside = base / "outside.json"
            outside.write_text("outside sentinel", encoding="utf-8")
            report_path = root / "demo-report.json"
            try:
                report_path.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")
            _write_failed_report(root, report_path, {"demo_status": "failed"})
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside sentinel")

    def test_present_artifacts_ignores_linklike_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            artifact = root / "workspace" / "report.txt"
            artifact.parent.mkdir()
            artifact.write_text("sentinel", encoding="utf-8")
            with mock.patch(
                "scriptorium.demo._is_linklike",
                side_effect=lambda path: path == artifact,
            ):
                self.assertEqual(_present_artifacts(root, ["workspace/report.txt"]), [])


if __name__ == "__main__":
    unittest.main()
