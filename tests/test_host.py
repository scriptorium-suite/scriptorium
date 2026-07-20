import contextlib
import hashlib
import io
import json
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scriptorium import cli
from scriptorium import host as host_module
from scriptorium.host import (
    ASSET_ID,
    HOST_TARGETS,
    MANIFEST_PATH,
    HostInstallError,
    format_host_install_report,
    inspect_host_installation,
    run_host_install,
)


def target(root: Path, host: str) -> Path:
    return root.joinpath(*HOST_TARGETS[host].split("/"))


def manifest_path(root: Path) -> Path:
    return root.joinpath(*MANIFEST_PATH.split("/"))


class HostInstallTests(unittest.TestCase):
    def test_python_311_reparse_attribute_detects_a_junction(self):
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

        class LegacyJunction:
            def lstat(self):
                return SimpleNamespace(st_file_attributes=reparse_flag)

            def is_symlink(self):
                return False

            def __str__(self):
                return "legacy-junction"

        self.assertTrue(host_module._is_link_or_junction(LegacyJunction()))

    def test_dry_run_for_each_host_is_write_free(self):
        for selected in ("codex", "claude-code"):
            with self.subTest(host=selected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                report = run_host_install(
                    workspace=root, host=selected, dry_run=True
                )
                self.assertEqual(report["status"], "planned")
                self.assertEqual(report["exit_code"], 0)
                self.assertEqual(report["files"][0]["action"], "create")
                self.assertFalse(target(root, selected).exists())
                self.assertFalse(manifest_path(root).exists())

    def test_both_hosts_receive_the_same_canonical_skill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = run_host_install(workspace=root, host="codex")
            claude = run_host_install(workspace=root, host="claude-code")
            self.assertEqual(codex["status"], "installed")
            self.assertEqual(claude["status"], "installed")
            codex_payload = target(root, "codex").read_bytes()
            claude_payload = target(root, "claude-code").read_bytes()
            self.assertEqual(codex_payload, claude_payload)
            self.assertNotIn(b"\r\n", codex_payload)
            manifest = json.loads(manifest_path(root).read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["files"]), set(HOST_TARGETS.values()))
            self.assertTrue(
                all(record["asset_id"] == ASSET_ID for record in manifest["files"].values())
            )

    def test_canonical_skill_wires_the_pending_fill_boundary(self):
        payload = host_module._load_skill().decode("utf-8")

        for required in (
            "project-resolution",
            "agent-fill",
            "prov-sync-unresolved",
            "prov-sync-pending",
            "prov-sync-fill",
            "fill.json",
            "Never tick",
            "`Approvals.md`",
        ):
            self.assertIn(required, payload)
        self.assertIn("Before any fill write", payload)
        self.assertIn("Never write or replace", payload)
        self.assertIn("separate authorization", payload)
        self.assertIn("do not create or fill a session summary", payload)

    def test_repeat_install_is_unchanged_and_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_host_install(workspace=root, host="codex")
            skill_mtime = target(root, "codex").stat().st_mtime_ns
            manifest_mtime = manifest_path(root).stat().st_mtime_ns
            with mock.patch("scriptorium.host._write_payload") as writer:
                report = run_host_install(workspace=root, host="codex")
            self.assertEqual(report["status"], "unchanged")
            self.assertEqual(report["files"][0]["action"], "unchanged")
            writer.assert_not_called()
            self.assertEqual(target(root, "codex").stat().st_mtime_ns, skill_mtime)
            self.assertEqual(manifest_path(root).stat().st_mtime_ns, manifest_mtime)

    def test_identical_unregistered_skill_is_adopted_without_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = target(root, "codex")
            destination.parent.mkdir(parents=True)
            destination.write_bytes(host_module._load_skill())
            before = destination.stat().st_mtime_ns
            report = run_host_install(workspace=root, host="codex")
            self.assertEqual(report["files"][0]["action"], "unchanged")
            self.assertEqual(report["status"], "installed")
            self.assertEqual(destination.stat().st_mtime_ns, before)
            self.assertTrue(manifest_path(root).is_file())

    def test_unmanaged_different_skill_is_a_zero_write_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = target(root, "codex")
            destination.parent.mkdir(parents=True)
            original = b"user-owned skill\n"
            destination.write_bytes(original)
            report = run_host_install(workspace=root, host="codex")
            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["exit_code"], 1)
            self.assertEqual(destination.read_bytes(), original)
            self.assertFalse(manifest_path(root).exists())

    def test_modified_managed_skill_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_host_install(workspace=root, host="codex")
            destination = target(root, "codex")
            destination.write_text("user customization\n", encoding="utf-8")
            before_manifest = manifest_path(root).read_bytes()
            report = run_host_install(workspace=root, host="codex")
            self.assertEqual(report["status"], "conflict")
            self.assertEqual(destination.read_text(encoding="utf-8"), "user customization\n")
            self.assertEqual(manifest_path(root).read_bytes(), before_manifest)

    def test_unmodified_owned_old_skill_is_safely_updated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_host_install(workspace=root, host="codex")
            old_payload = (
                b"---\nname: scriptorium-research\n"
                b"description: Previous official research workflow.\n---\n\n# Previous\n"
            )
            old_digest = hashlib.sha256(old_payload).hexdigest()
            target(root, "codex").write_bytes(old_payload)
            manifest = json.loads(manifest_path(root).read_text(encoding="utf-8"))
            manifest["files"][HOST_TARGETS["codex"]]["sha256"] = old_digest
            manifest_path(root).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = run_host_install(workspace=root, host="codex")
            self.assertEqual(report["files"][0]["action"], "update")
            self.assertEqual(target(root, "codex").read_bytes(), host_module._load_skill())

    def test_invalid_manifest_refuses_all_target_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = manifest_path(root)
            path.parent.mkdir(parents=True)
            path.write_text("not json", encoding="utf-8")
            report = run_host_install(workspace=root, host="claude-code")
            inspection = inspect_host_installation(workspace=root)
            self.assertEqual(report["status"], "conflict")
            self.assertEqual(inspection["manifest"]["state"], "invalid")
            self.assertFalse(target(root, "claude-code").exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "not json")

    def test_missing_file_and_file_workspace_are_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing"
            self.assertEqual(
                run_host_install(workspace=missing, host="codex")["exit_code"], 1
            )
            file_workspace = root / "workspace.txt"
            file_workspace.write_text("x", encoding="utf-8")
            self.assertEqual(
                run_host_install(workspace=file_workspace, host="codex")["exit_code"], 1
            )

    def test_linklike_managed_ancestor_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real_probe = host_module._is_link_or_junction

            def linklike(path: Path) -> bool:
                return path == root / ".agents" or real_probe(path)

            with mock.patch("scriptorium.host._is_link_or_junction", side_effect=linklike):
                report = run_host_install(workspace=root, host="codex")
            self.assertEqual(report["status"], "conflict")
            self.assertFalse((root / ".agents").exists())
            self.assertFalse(manifest_path(root).exists())

    def test_create_race_never_deletes_the_other_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = target(root, "codex")

            def racing_create(*_args, **_kwargs):
                destination.write_bytes(b"other writer\n")
                raise FileExistsError("racing writer won")

            with mock.patch("scriptorium.host.os.open", side_effect=racing_create):
                with self.assertRaisesRegex(HostInstallError, "cannot create managed file"):
                    run_host_install(workspace=root, host="codex")
            self.assertEqual(destination.read_bytes(), b"other writer\n")
            self.assertFalse(manifest_path(root).exists())

    def test_workspace_lock_prevents_concurrent_manifest_loss(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entered = threading.Event()
            release = threading.Event()
            original_load = host_module._load_manifest
            first_report = []

            def slow_first_load(workspace: Path):
                if threading.current_thread().name == "first-installer":
                    entered.set()
                    self.assertTrue(release.wait(timeout=5))
                return original_load(workspace)

            def install_first():
                first_report.append(run_host_install(workspace=root, host="codex"))

            with mock.patch(
                "scriptorium.host._load_manifest", side_effect=slow_first_load
            ):
                thread = threading.Thread(target=install_first, name="first-installer")
                thread.start()
                self.assertTrue(entered.wait(timeout=5))
                second = run_host_install(workspace=root, host="claude-code")
                release.set()
                thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(first_report[0]["status"], "installed")
            self.assertEqual(second["status"], "conflict")
            self.assertFalse(target(root, "claude-code").exists())
            manifest = json.loads(manifest_path(root).read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["files"]), {HOST_TARGETS["codex"]})

            retried = run_host_install(workspace=root, host="claude-code")
            self.assertEqual(retried["status"], "installed")
            manifest = json.loads(manifest_path(root).read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["files"]), set(HOST_TARGETS.values()))

    def test_manifest_compare_and_swap_preserves_an_external_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_host_install(workspace=root, host="codex")
            original_write = host_module._write_payload

            def interleaved_write(**kwargs):
                original_write(**kwargs)
                if kwargs["relative"] == HOST_TARGETS["claude-code"]:
                    manifest = json.loads(
                        manifest_path(root).read_text(encoding="utf-8")
                    )
                    manifest["external_note"] = "preserve me"
                    manifest_path(root).write_text(
                        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

            with mock.patch(
                "scriptorium.host._write_payload", side_effect=interleaved_write
            ):
                report = run_host_install(workspace=root, host="claude-code")

            self.assertEqual(report["status"], "conflict")
            manifest = json.loads(manifest_path(root).read_text(encoding="utf-8"))
            self.assertEqual(manifest["external_note"], "preserve me")
            self.assertNotIn(HOST_TARGETS["claude-code"], manifest["files"])

            retry = run_host_install(workspace=root, host="claude-code")
            self.assertEqual(retry["status"], "installed")
            manifest = json.loads(manifest_path(root).read_text(encoding="utf-8"))
            self.assertEqual(manifest["external_note"], "preserve me")
            self.assertIn(HOST_TARGETS["claude-code"], manifest["files"])

    def test_unicode_workspace_installs_and_inspects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "科研 workspace 🧪"
            root.mkdir()
            report = run_host_install(workspace=root, host="claude-code")
            inspection = inspect_host_installation(workspace=root)
            selected = next(item for item in inspection["hosts"] if item["name"] == "claude-code")
            self.assertEqual(report["exit_code"], 0)
            self.assertEqual(selected["state"], "canonical")
            self.assertEqual(inspection["manifest"]["state"], "valid")

    def test_inspection_distinguishes_missing_unregistered_and_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = target(root, "codex")
            destination.parent.mkdir(parents=True)
            destination.write_bytes(host_module._load_skill())
            inspection = inspect_host_installation(workspace=root)
            states = {item["name"]: item["state"] for item in inspection["hosts"]}
            self.assertEqual(states, {"codex": "unregistered", "claude-code": "missing"})
            run_host_install(workspace=root, host="codex")
            destination.write_text("modified\n", encoding="utf-8")
            inspection = inspect_host_installation(workspace=root)
            codex = next(item for item in inspection["hosts"] if item["name"] == "codex")
            self.assertEqual(codex["state"], "modified")

    def test_human_report_discloses_safety_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = run_host_install(
                workspace=Path(temporary), host="codex", dry_run=True
            )
            rendered = format_host_install_report(report)
            self.assertIn("Network: no action requested", rendered)
            self.assertIn("No global config, hook, credential", rendered)
            self.assertNotIn("\x1b", rendered)


class HostCliTests(unittest.TestCase):
    def test_json_install_outputs_one_report_and_returns_its_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = cli.main(
                    [
                        "host",
                        "install",
                        "codex",
                        "--workspace",
                        temporary,
                        "--json",
                    ]
                )
            report = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["status"], "installed")
            self.assertEqual(report["operation"], "host.install")

    def test_json_conflict_outputs_one_report_and_returns_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = target(root, "codex")
            destination.parent.mkdir(parents=True)
            destination.write_text("user skill\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = cli.main(
                    [
                        "host",
                        "install",
                        "codex",
                        "--workspace",
                        temporary,
                        "--json",
                    ]
                )
            report = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(report["status"], "conflict")
            self.assertEqual(destination.read_text(encoding="utf-8"), "user skill\n")

    def test_json_internal_error_returns_two_without_traceback(self):
        stdout = io.StringIO()
        with mock.patch(
            "scriptorium.cli.run_host_install",
            side_effect=HostInstallError("packaged skill invalid"),
        ), contextlib.redirect_stdout(stdout):
            exit_code = cli.main(
                [
                    "host",
                    "install",
                    "codex",
                    "--workspace",
                    ".",
                    "--json",
                ]
            )
        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["error"], "packaged skill invalid")


if __name__ == "__main__":
    unittest.main()
