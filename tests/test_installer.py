from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from scriptorium.components import ComponentCatalog, load_component_catalog
from scriptorium.installer import (
    MAX_ASSET_BYTES,
    InstallError,
    execute_install,
    plan_install,
)


class InstallerTests(unittest.TestCase):
    def capture_catalog(self, asset: Path) -> ComponentCatalog:
        catalog = load_component_catalog()
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        components = dict(catalog.components)
        components["capture"] = replace(
            components["capture"], artifact_sha256=digest
        )
        return replace(catalog, components=components)

    def test_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "suite components"
            report = plan_install(profile="core", target=target)
            self.assertEqual(report["status"], "planned")
            self.assertEqual(report["writes"], "none")
            self.assertEqual(
                [action["component"] for action in report["actions"]],
                ["scriptorium-spec", "provenance"],
            )
            self.assertFalse(target.exists())

    def test_capture_preview_is_honest_about_unpublished_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "capture"
            report = plan_install(profile="capture", target=target)
            self.assertEqual(report["status"], "action-required")
            self.assertEqual(report["summary"]["unavailable"], 1)
            self.assertFalse(report["actions"][0]["available"])
            with self.assertRaisesRegex(InstallError, "unpublished"):
                execute_install(profile="capture", target=target)
            self.assertFalse(target.exists())

    def test_downloaded_capture_asset_installs_without_cloning_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "scriptorium-local-conversation-export-1.0.0.zip"
            with zipfile.ZipFile(asset, "w") as archive:
                archive.writestr("manifest.json", '{"manifest_version": 3}')
                archive.writestr("src/popup.html", "synthetic")
            catalog = self.capture_catalog(asset)
            target = root / "capture-only"

            preview = plan_install(
                profile="capture", target=target, asset=asset, catalog=catalog
            )
            self.assertEqual(preview["status"], "planned")
            self.assertEqual(preview["network"], "not-required")
            self.assertFalse(target.exists())

            report = execute_install(
                profile="capture", target=target, asset=asset, catalog=catalog
            )
            self.assertEqual(report["status"], "installed")
            self.assertTrue((target / "capture" / asset.name).is_file())
            self.assertEqual(
                (target / "capture" / "unpacked" / "src" / "popup.html").read_text(
                    encoding="utf-8"
                ),
                "synthetic",
            )
            self.assertFalse((target / "Provenance").exists())

    def test_capture_asset_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "scriptorium-local-conversation-export-1.0.0.zip"
            with zipfile.ZipFile(asset, "w") as archive:
                archive.writestr("../outside.txt", "do not write")
            catalog = self.capture_catalog(asset)
            target = root / "capture-only"
            with self.assertRaisesRegex(InstallError, "unsafe"):
                execute_install(
                    profile="capture", target=target, asset=asset, catalog=catalog
                )
            self.assertFalse((root / "outside.txt").exists())
            marker = json.loads(
                (target / ".scriptorium-install.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["state"], "failed")

    def test_capture_asset_rejects_case_colliding_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "scriptorium-local-conversation-export-1.0.0.zip"
            with zipfile.ZipFile(asset, "w") as archive:
                archive.writestr("src/popup.html", "first")
                archive.writestr("SRC/popup.html", "second")
            catalog = self.capture_catalog(asset)
            with self.assertRaisesRegex(InstallError, "entry set"):
                execute_install(
                    profile="capture",
                    target=root / "capture-only",
                    asset=asset,
                    catalog=catalog,
                )

    def test_capture_asset_is_rechecked_after_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "scriptorium-local-conversation-export-1.0.0.zip"
            with zipfile.ZipFile(asset, "w") as archive:
                archive.writestr("manifest.json", '{}')
            catalog = self.capture_catalog(asset)

            def corrupt_copy(_source, destination):
                Path(destination).write_bytes(b"changed during copy")

            with mock.patch(
                "scriptorium.installer.shutil.copyfile", side_effect=corrupt_copy
            ):
                with self.assertRaisesRegex(InstallError, "changed while copying"):
                    execute_install(
                        profile="capture",
                        target=root / "capture-only",
                        asset=asset,
                        catalog=catalog,
                    )

    def test_capture_asset_rejects_oversized_archive_before_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "scriptorium-local-conversation-export-1.0.0.zip"
            with asset.open("wb") as stream:
                stream.seek(MAX_ASSET_BYTES)
                stream.write(b"0")
            target = root / "capture-only"
            with self.assertRaisesRegex(InstallError, "too large"):
                plan_install(
                    profile="capture",
                    target=target,
                    asset=asset,
                    catalog=load_component_catalog(),
                )
            self.assertFalse(target.exists())

    def test_nonempty_unowned_target_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "existing"
            target.mkdir()
            sentinel = target / "user.txt"
            sentinel.write_text("owned by user", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "not owned"):
                plan_install(profile="core", target=target)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "owned by user")

    def test_malformed_ownership_marker_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "existing"
            target.mkdir()
            (target / ".scriptorium-install.json").write_text(
                '{"format_version": 1}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(InstallError, "unsupported"):
                plan_install(profile="core", target=target)

    def test_run_clones_exact_components_and_writes_marker_last(self):
        catalog = load_component_catalog()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "managed"

            def fake_install(component, destination):
                destination.mkdir(parents=True)
                (destination / ".git").mkdir()

            with mock.patch(
                "scriptorium.installer._install_source", side_effect=fake_install
            ), mock.patch("scriptorium.installer._verify_checkout"):
                report = execute_install(
                    profile="core", target=target, catalog=catalog
                )

            self.assertEqual(report["status"], "installed")
            marker = json.loads(
                (target / ".scriptorium-install.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["profile"], "core")
            self.assertEqual(
                set(marker["components"]), {"scriptorium-spec", "provenance"}
            )
            powershell = (target / "scriptorium-env.ps1").read_text(encoding="utf-8")
            bash = (target / "scriptorium-env.sh").read_text(encoding="utf-8")
            self.assertIn("SCRIPTORIUM_SPEC_ROOT", powershell)
            self.assertIn("SCRIPTORIUM_PROVENANCE_ROOT", powershell)
            self.assertNotIn(str(target), powershell)
            self.assertIn("SCRIPTORIUM_COMPONENTS_ROOT", bash)

    def test_failed_run_keeps_a_failed_ownership_marker_without_deleting(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "managed"
            target.mkdir()

            def fail_after_create(component, destination):
                destination.mkdir(parents=True)
                raise InstallError("synthetic failure", code="command_failed")

            with mock.patch(
                "scriptorium.installer._install_source", side_effect=fail_after_create
            ):
                with self.assertRaisesRegex(InstallError, "synthetic"):
                    execute_install(profile="core", target=target)

            self.assertTrue(target.is_dir())
            self.assertTrue((target / "scriptorium-spec").is_dir())
            marker = json.loads(
                (target / ".scriptorium-install.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["state"], "failed")

    def test_user_home_is_too_broad_for_component_install(self):
        with self.assertRaisesRegex(InstallError, "user home"):
            plan_install(profile="core", target=Path.home())


if __name__ == "__main__":
    unittest.main()
