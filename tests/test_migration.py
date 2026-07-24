import copy
import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scriptorium import migration as migration_module
from scriptorium.migration import (
    MANIFEST_PRIVACY,
    MANIFEST_VERSION,
    MigrationError,
    apply_migration,
    plan_migration,
    rollback_migration,
)


def snapshot(root: Path) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result[relative] = path.read_bytes() if path.is_file() else None
    return result


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.workspace = self.base / "research workspace"
        self.sources = self.base / "selected sources"
        self.platform_state = self.base / "platform state"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.platform_state),
                "XDG_STATE_HOME": str(self.platform_state),
            },
            clear=False,
        )
        self.environment.start()
        self.state_root = (
            self.platform_state / "Scriptorium" / "state"
            if os.name == "nt"
            else self.platform_state / "scriptorium"
        )
        self.workspace.mkdir()
        self.sources.mkdir()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def write_sources(self):
        (self.sources / "notes").mkdir()
        markdown = self.sources / "notes" / "idea.md"
        pdf = self.sources / "paper.pdf"
        ignored = self.sources / "raw.txt"
        markdown.write_text("# Synthetic idea\n", encoding="utf-8")
        pdf.write_bytes(b"%PDF-1.4\nsynthetic\n")
        ignored.write_text("not selected by suffix", encoding="utf-8")
        return markdown, pdf, ignored

    def plan(self, sources=None, *, batch_id="batch-001", state_root=None):
        return plan_migration(
            sources if sources is not None else [self.sources],
            workspace=self.workspace,
            batch_id=batch_id,
            state_root=self.state_root if state_root is None else state_root,
        )

    def target(self, plan, index=0) -> Path:
        return Path(plan.manifest["entries"][index]["target"])

    def manifest_path(self, plan) -> Path:
        return migration_module._manifest_path(plan.manifest)

    def test_plan_is_read_only_private_and_repr_is_path_free(self):
        self.write_sources()
        workspace_before = snapshot(self.workspace)
        sources_before = snapshot(self.sources)

        planned = self.plan()

        self.assertEqual(snapshot(self.workspace), workspace_before)
        self.assertEqual(snapshot(self.sources), sources_before)
        self.assertFalse(self.state_root.exists())
        self.assertEqual(planned.report["status"], "planned")
        self.assertEqual(planned.report["summary"]["files"], 2)
        self.assertEqual(planned.report["summary"]["markdown"], 1)
        self.assertEqual(planned.report["summary"]["pdf"], 1)
        self.assertNotIn(str(self.base), json.dumps(planned.report))
        self.assertNotIn(str(self.base), repr(planned))
        self.assertEqual(planned.manifest["schema_version"], MANIFEST_VERSION)
        self.assertEqual(planned.manifest["privacy"], MANIFEST_PRIVACY)
        self.assertEqual(Path(planned.manifest["state_root"]), self.state_root)

    def test_default_state_root_uses_platform_local_state_outside_workspace(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        local_state = self.base / "platform state"
        environment = {
            "LOCALAPPDATA": str(local_state),
            "XDG_STATE_HOME": str(local_state),
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            planned = plan_migration(
                [source], workspace=self.workspace, batch_id="default-state"
            )
        expected = (
            local_state / "Scriptorium" / "state"
            if os.name == "nt"
            else local_state / "scriptorium"
        )
        self.assertEqual(Path(planned.manifest["state_root"]), expected)
        self.assertFalse(expected.exists())

    def test_noncanonical_explicit_state_root_is_rejected_without_path(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        with self.assertRaises(MigrationError) as raised:
            self.plan([source], state_root=self.workspace / ".private")
        self.assertEqual(raised.exception.code, "noncanonical_state_root")
        self.assertNotIn(str(self.base), str(raised.exception))

    def test_canonical_state_root_inside_workspace_is_rejected(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        local_state = self.workspace / "platform state"
        with mock.patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(local_state),
                "XDG_STATE_HOME": str(local_state),
            },
            clear=False,
        ):
            with self.assertRaises(MigrationError) as raised:
                plan_migration(
                    [source], workspace=self.workspace, batch_id="overlap"
                )
        self.assertEqual(raised.exception.code, "state_workspace_overlap")

    def test_canonical_state_root_inside_source_root_is_rejected(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        with mock.patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.sources),
                "XDG_STATE_HOME": str(self.sources),
            },
            clear=False,
        ):
            with self.assertRaises(MigrationError) as raised:
                plan_migration(
                    [self.sources],
                    workspace=self.workspace,
                    batch_id="source-overlap",
                )
        self.assertEqual(raised.exception.code, "state_root_in_source")

    def test_source_directory_inside_canonical_state_root_is_rejected(self):
        source = self.state_root / "candidate inputs"
        source.mkdir(parents=True)
        (source / "idea.md").write_text("synthetic", encoding="utf-8")
        with self.assertRaises(MigrationError) as raised:
            self.plan([source])
        self.assertEqual(raised.exception.code, "source_state_overlap")
        self.assertFalse((self.workspace / "Sources").exists())

    def test_source_file_inside_canonical_state_root_is_rejected(self):
        source = self.state_root / "candidate.md"
        source.parent.mkdir(parents=True)
        source.write_text("synthetic", encoding="utf-8")
        with self.assertRaises(MigrationError) as raised:
            self.plan([source])
        self.assertEqual(raised.exception.code, "source_state_overlap")
        self.assertFalse((self.workspace / "Sources").exists())

    def test_apply_copies_supported_files_and_keeps_manifest_outside_workspace(self):
        self.write_sources()
        sources_before = snapshot(self.sources)
        applied = apply_migration(self.plan())

        self.assertEqual(applied.report["status"], "applied")
        self.assertEqual(applied.report["summary"]["changed"], 2)
        self.assertEqual(snapshot(self.sources), sources_before)
        for entry in applied.manifest["entries"]:
            self.assertEqual(
                Path(entry["target"]).read_bytes(), Path(entry["source"]).read_bytes()
            )
            self.assertEqual(entry["state"], "created")
            self.assertEqual(
                entry["file_identity"],
                migration_module._file_identity(Path(entry["target"])),
            )
        self.assertTrue(self.manifest_path(applied).is_file())
        self.assertFalse((self.workspace / ".scriptorium").exists())
        self.assertNotIn(str(self.base), json.dumps(applied.report))
        self.assertNotIn(str(self.base), repr(applied))

    def test_publication_uses_same_directory_hard_link_create(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        planned = self.plan([source])
        target = self.target(planned)

        with mock.patch(
            "scriptorium.migration.os.link", wraps=os.link
        ) as link:
            apply_migration(planned)

        self.assertEqual(link.call_count, 1)
        stage, published = map(Path, link.call_args.args)
        self.assertEqual(published, target)
        self.assertEqual(stage.parent, target.parent)
        self.assertTrue(stage.is_file())
        self.assertTrue(os.path.samefile(stage, target))

    def test_existing_target_is_never_adopted_or_overwritten(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        planned = self.plan([source])
        destination = self.target(planned)
        destination.parent.mkdir(parents=True)
        destination.write_text("user owned", encoding="utf-8")

        with self.assertRaises(MigrationError) as raised:
            apply_migration(planned)

        self.assertEqual(raised.exception.code, "target_exists")
        self.assertEqual(destination.read_text(encoding="utf-8"), "user owned")
        self.assertFalse(self.manifest_path(planned).exists())

    def test_hard_link_failure_is_safe_and_retryable(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        planned = self.plan([source])
        target = self.target(planned)

        with mock.patch(
            "scriptorium.migration.os.link",
            side_effect=OSError(errno.EPERM, "synthetic"),
        ):
            with self.assertRaises(MigrationError) as raised:
                apply_migration(planned)

        self.assertEqual(raised.exception.code, "atomic_publish_unavailable")
        self.assertFalse(target.exists())
        recovered = apply_migration(planned)
        self.assertEqual(recovered.report["status"], "applied")
        self.assertEqual(target.read_text(encoding="utf-8"), "synthetic")

    def test_cross_volume_publish_error_has_no_overwrite_fallback(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        planned = self.plan([source])

        with mock.patch(
            "scriptorium.migration.os.link",
            side_effect=OSError(errno.EXDEV, "synthetic"),
        ):
            with self.assertRaises(MigrationError) as raised:
                apply_migration(planned)

        self.assertEqual(
            raised.exception.code, "cross_volume_publish_unsupported"
        )
        self.assertFalse(self.target(planned).exists())

    def test_source_change_is_rejected_before_target_write(self):
        markdown, _, _ = self.write_sources()
        planned = self.plan()
        markdown.write_text("changed after preview\n", encoding="utf-8")

        with self.assertRaises(MigrationError) as raised:
            apply_migration(planned)

        self.assertEqual(raised.exception.code, "source_hash_changed")
        self.assertFalse((self.workspace / "Sources").exists())
        self.assertFalse(self.manifest_path(planned).exists())

    def test_repeat_apply_stays_idempotent_when_sources_are_gone(self):
        self.write_sources()
        planned = self.plan()
        first = apply_migration(planned)
        target_mtimes = {
            Path(entry["target"]): Path(entry["target"]).stat().st_mtime_ns
            for entry in first.manifest["entries"]
        }
        manifest_path = self.manifest_path(first)
        manifest_mtime = manifest_path.stat().st_mtime_ns
        for path in sorted(self.sources.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            else:
                path.rmdir()

        second = apply_migration(planned)

        self.assertEqual(second.report["status"], "unchanged")
        self.assertEqual(second.report["summary"]["changed"], 0)
        self.assertEqual(second.report["summary"]["unchanged"], 2)
        self.assertEqual(manifest_path.stat().st_mtime_ns, manifest_mtime)
        self.assertEqual(
            {path: path.stat().st_mtime_ns for path in target_mtimes}, target_mtimes
        )

    def test_new_process_can_load_verify_reapply_and_rollback_without_sources(self):
        self.write_sources()
        planned = self.plan(batch_id="process-recovery")
        applied = apply_migration(planned)
        targets = [Path(entry["target"]) for entry in applied.manifest["entries"]]
        for path in sorted(self.sources.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            else:
                path.rmdir()

        source_root = Path(__file__).resolve().parents[1] / "src"
        code = """
import json
import sys
from scriptorium.migration import (
    load_migration,
    reapply_migration,
    rollback_migration,
    verify_migration,
)
workspace, batch_id = sys.argv[1:3]
loaded = load_migration(workspace=workspace, batch_id=batch_id)
verified = verify_migration(workspace=workspace, batch_id=batch_id)
reapplied = reapply_migration(workspace=workspace, batch_id=batch_id)
rolled_back = rollback_migration(
    load_migration(workspace=workspace, batch_id=batch_id)
)
print(json.dumps({
    "load": loaded.report["status"],
    "verify": verified.report["status"],
    "reapply": reapplied.report["status"],
    "rollback": rolled_back.report["status"],
}))
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(source_root), environment.get("PYTHONPATH", "")]
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(self.workspace),
                "process-recovery",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "load": "applied",
                "verify": "applied",
                "reapply": "unchanged",
                "rollback": "rolled-back",
            },
        )
        self.assertNotIn(str(self.base), completed.stdout)
        self.assertNotIn(str(self.base), completed.stderr)
        self.assertTrue(all(not target.exists() for target in targets))

    def test_runtime_state_tampering_is_covered_by_integrity(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        manifest = copy.deepcopy(self.plan([source]).manifest)
        manifest["run_state"] = "applied"

        with self.assertRaises(MigrationError) as raised:
            apply_migration(manifest)

        self.assertEqual(raised.exception.code, "manifest_integrity_failed")

    def test_invalid_but_resealed_state_combination_is_rejected(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        manifest = copy.deepcopy(self.plan([source]).manifest)
        manifest["run_state"] = "applied"
        migration_module._seal_manifest(manifest)

        with self.assertRaises(MigrationError) as raised:
            apply_migration(manifest)

        self.assertEqual(raised.exception.code, "invalid_state_transition")

    def test_tampered_escape_is_rejected_even_when_digests_are_recomputed(self):
        source = self.sources / "idea.md"
        source.write_text("synthetic", encoding="utf-8")
        manifest = copy.deepcopy(self.plan([source]).manifest)
        manifest["entries"][0]["relative_target"] = "../outside.md"
        manifest["entries"][0]["target"] = str(self.base / "outside.md")
        migration_module._seal_manifest(manifest, new_plan=True)

        with self.assertRaises(MigrationError) as raised:
            apply_migration(manifest)

        self.assertEqual(raised.exception.code, "target_escape")
        self.assertFalse((self.base / "outside.md").exists())

    def test_rollback_removes_owned_files_only_and_is_idempotent(self):
        self.write_sources()
        planned = self.plan()
        applied = apply_migration(planned)
        sibling = self.workspace / "Sources" / "Imported" / "user-owned.md"
        sibling.write_text("keep", encoding="utf-8")
        anchors = [
            migration_module._stage(applied.manifest, entry)
            for entry in applied.manifest["entries"]
        ]
        self.assertTrue(all(anchor.is_file() for anchor in anchors))

        rolled_back = rollback_migration(planned)

        self.assertEqual(rolled_back.report["status"], "rolled-back")
        self.assertEqual(rolled_back.report["summary"]["changed"], 2)
        self.assertTrue(sibling.is_file())
        self.assertTrue(
            all(not Path(entry["target"]).exists() for entry in applied.manifest["entries"])
        )
        self.assertTrue(all(not anchor.exists() for anchor in anchors))
        repeated = rollback_migration(planned)
        self.assertEqual(repeated.report["status"], "unchanged")
        self.assertTrue(sibling.is_file())
        self.assertNotIn(str(self.base), json.dumps(rolled_back.report))

    def test_rollback_persists_delete_intent_and_recovers_after_state_write_failure(self):
        self.write_sources()
        planned = self.plan()
        applied = apply_migration(planned)
        targets = [Path(entry["target"]) for entry in applied.manifest["entries"]]
        real_persist = migration_module._persist_manifest
        failed = False

        def fail_after_first_delete(manifest):
            nonlocal failed
            if (
                not failed
                and manifest["run_state"] == "rolling-back"
                and manifest["entries"][0]["state"] == "deleted"
            ):
                failed = True
                raise MigrationError("state_write_failed")
            return real_persist(manifest)

        with mock.patch(
            "scriptorium.migration._persist_manifest",
            side_effect=fail_after_first_delete,
        ):
            with self.assertRaises(MigrationError) as raised:
                rollback_migration(applied)

        self.assertEqual(raised.exception.code, "state_write_failed")
        self.assertFalse(targets[0].exists())
        stored = json.loads(self.manifest_path(planned).read_text(encoding="utf-8"))
        self.assertEqual(stored["entries"][0]["state"], "delete-pending")

        recovered = rollback_migration(planned)
        self.assertEqual(recovered.report["status"], "rolled-back")
        self.assertTrue(all(not target.exists() for target in targets))

    def test_modified_target_fails_closed_before_any_rollback_deletion(self):
        self.write_sources()
        applied = apply_migration(self.plan())
        targets = [Path(entry["target"]) for entry in applied.manifest["entries"]]
        targets[-1].write_bytes(b"user modification")

        with self.assertRaises(MigrationError) as raised:
            rollback_migration(applied)

        self.assertEqual(raised.exception.code, "owned_target_changed")
        self.assertTrue(all(path.exists() for path in targets))

    def test_same_byte_replacement_is_not_deleted_by_rollback(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic replacement sentinel")
        applied = apply_migration(self.plan([source]))
        entry = applied.manifest["entries"][0]
        target = Path(entry["target"])
        anchor = migration_module._stage(applied.manifest, entry)
        self.assertTrue(os.path.samefile(anchor, target))

        target.unlink()
        target.write_bytes(source.read_bytes())
        self.assertFalse(os.path.samefile(anchor, target))

        with self.assertRaises(MigrationError) as raised:
            rollback_migration(applied)

        self.assertEqual(raised.exception.code, "owned_target_changed")
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertTrue(anchor.is_file())

    def test_target_replaced_after_preflight_is_not_deleted(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic post-preflight sentinel")
        applied = apply_migration(self.plan([source]))
        entry = applied.manifest["entries"][0]
        target = Path(entry["target"])
        anchor = migration_module._stage(applied.manifest, entry)
        real_preflight = migration_module._preflight_rollback
        replaced = False

        def replace_after_preflight(manifest, **kwargs):
            nonlocal replaced
            real_preflight(manifest, **kwargs)
            if not replaced:
                replaced = True
                target.unlink()
                target.write_bytes(source.read_bytes())

        with mock.patch(
            "scriptorium.migration._preflight_rollback",
            side_effect=replace_after_preflight,
        ):
            with self.assertRaises(MigrationError) as raised:
                rollback_migration(applied)

        self.assertEqual(raised.exception.code, "owned_target_changed")
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertTrue(anchor.is_file())
        self.assertFalse(os.path.samefile(anchor, target))
        self.assertFalse(
            any(target.parent.glob(".scriptorium-*.rollback"))
        )

    def test_preoccupied_quarantine_never_overwrites_or_deletes_either_file(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic quarantine source")
        applied = apply_migration(self.plan([source]))
        entry = applied.manifest["entries"][0]
        target = Path(entry["target"])
        anchor = migration_module._stage(applied.manifest, entry)
        token = "2" * 32
        quarantine = target.parent / (
            f".scriptorium-{applied.manifest['batch_id']}-{entry['id']}-"
            f"{token}.rollback"
        )
        quarantine.write_bytes(b"user quarantine occupant")

        with mock.patch(
            "scriptorium.migration.secrets.token_hex", return_value=token
        ):
            with self.assertRaises(MigrationError) as raised:
                rollback_migration(applied)

        self.assertEqual(raised.exception.code, "quarantine_conflict")
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(quarantine.read_bytes(), b"user quarantine occupant")
        self.assertTrue(anchor.is_file())

    def test_restore_blocked_preserves_moved_replacement_and_new_target(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic restore source")
        applied = apply_migration(self.plan([source]))
        entry = applied.manifest["entries"][0]
        target = Path(entry["target"])
        anchor = migration_module._stage(applied.manifest, entry)
        real_preflight = migration_module._preflight_rollback
        real_rename = migration_module._rename_noreplace
        replaced = False
        reoccupied = False

        def replace_after_preflight(manifest, **kwargs):
            nonlocal replaced
            real_preflight(manifest, **kwargs)
            if not replaced:
                replaced = True
                target.unlink()
                target.write_bytes(source.read_bytes())

        def reoccupy_after_move(old, new):
            nonlocal reoccupied
            real_rename(old, new)
            if old == target and not reoccupied:
                reoccupied = True
                target.write_bytes(b"new target occupant")

        with mock.patch(
            "scriptorium.migration._preflight_rollback",
            side_effect=replace_after_preflight,
        ), mock.patch(
            "scriptorium.migration._rename_noreplace",
            side_effect=reoccupy_after_move,
        ):
            with self.assertRaises(MigrationError) as raised:
                rollback_migration(applied)

        self.assertEqual(raised.exception.code, "rollback_restore_blocked")
        self.assertEqual(target.read_bytes(), b"new target occupant")
        stored = migration_module.load_migration(
            workspace=self.workspace, batch_id="batch-001"
        ).manifest
        quarantine = migration_module._quarantine(
            stored, stored["entries"][0]
        )
        self.assertEqual(quarantine.read_bytes(), source.read_bytes())
        self.assertTrue(anchor.is_file())

    def test_random_stage_collision_preserves_the_competing_file(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic collision source")
        planned = self.plan([source])
        entry = planned.manifest["entries"][0]
        target = Path(entry["target"])
        target.parent.mkdir(parents=True)
        collision_name = (
            f".scriptorium-{planned.manifest['batch_id']}-{entry['id']}-"
            f"{'0' * 32}.stage"
        )
        collision = target.parent / collision_name
        collision.write_bytes(b"user replacement")

        with mock.patch(
            "scriptorium.migration.secrets.token_hex",
            side_effect=["0" * 32, "1" * 32],
        ):
            applied = apply_migration(planned)

        self.assertEqual(collision.read_bytes(), b"user replacement")
        anchor = migration_module._stage(
            applied.manifest, applied.manifest["entries"][0]
        )
        self.assertNotEqual(anchor, collision)
        self.assertEqual(anchor.read_bytes(), source.read_bytes())

    def test_complete_unclaimed_stage_after_state_crash_does_not_block_retry(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic unclaimed stage")
        planned = self.plan([source])
        real_persist = migration_module._persist_manifest
        failed = False

        def fail_before_stage_claim(manifest):
            nonlocal failed
            entry = manifest["entries"][0]
            if (
                not failed
                and entry["state"] == "creating"
                and entry["stage_name"] is not None
            ):
                failed = True
                raise MigrationError("state_write_failed")
            return real_persist(manifest)

        with mock.patch(
            "scriptorium.migration._persist_manifest",
            side_effect=fail_before_stage_claim,
        ):
            with self.assertRaises(MigrationError) as raised:
                apply_migration(planned)

        self.assertEqual(raised.exception.code, "state_write_failed")
        target_parent = self.target(planned).parent
        orphaned = list(target_parent.glob(".scriptorium-*.stage"))
        self.assertEqual(len(orphaned), 1)

        recovered = apply_migration(planned)

        anchor = migration_module._stage(
            recovered.manifest, recovered.manifest["entries"][0]
        )
        self.assertNotEqual(anchor, orphaned[0])
        self.assertEqual(orphaned[0].read_bytes(), source.read_bytes())
        self.assertEqual(anchor.read_bytes(), source.read_bytes())

    def test_stage_replaced_after_identity_persist_is_rejected_and_preserved(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic claimed stage")
        planned = self.plan([source])
        real_persist = migration_module._persist_manifest
        replaced = False
        replacement = None

        def replace_after_stage_claim(manifest):
            nonlocal replaced, replacement
            real_persist(manifest)
            entry = manifest["entries"][0]
            if (
                not replaced
                and entry["state"] == "creating"
                and entry["stage_name"] is not None
            ):
                replaced = True
                replacement = migration_module._stage(manifest, entry)
                replacement.unlink()
                replacement.write_bytes(source.read_bytes())

        with mock.patch(
            "scriptorium.migration._persist_manifest",
            side_effect=replace_after_stage_claim,
        ):
            with self.assertRaises(MigrationError) as raised:
                apply_migration(planned)

        self.assertEqual(raised.exception.code, "owned_target_changed")
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.read_bytes(), source.read_bytes())
        self.assertFalse(self.target(planned).exists())

    def test_hash_mismatch_preserves_unclaimed_stage_for_manual_review(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic original")
        planned = self.plan([source])
        real_verify = migration_module._verify_source
        calls = 0

        def change_after_second_verification(entry):
            nonlocal calls
            real_verify(entry)
            calls += 1
            if calls == 2:
                source.write_bytes(b"synthetic changed")

        with mock.patch(
            "scriptorium.migration._verify_source",
            side_effect=change_after_second_verification,
        ):
            with self.assertRaises(MigrationError) as raised:
                apply_migration(planned)

        self.assertEqual(raised.exception.code, "source_hash_changed")
        target = self.target(planned)
        self.assertFalse(target.exists())
        orphaned = list(target.parent.glob(".scriptorium-*.stage"))
        self.assertEqual(len(orphaned), 1)
        self.assertEqual(orphaned[0].read_bytes(), b"synthetic changed")

    def test_changed_stage_is_not_deleted_during_apply_recovery(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic stage sentinel")
        planned = self.plan([source])
        with mock.patch(
            "scriptorium.migration.os.link",
            side_effect=OSError(errno.EPERM, "synthetic"),
        ):
            with self.assertRaises(MigrationError):
                apply_migration(planned)

        stored = json.loads(self.manifest_path(planned).read_text(encoding="utf-8"))
        entry = stored["entries"][0]
        anchor = migration_module._stage(stored, entry)
        anchor.unlink()
        anchor.write_bytes(b"user replacement")

        with self.assertRaises(MigrationError) as raised:
            apply_migration(planned)

        self.assertEqual(raised.exception.code, "staging_conflict")
        self.assertEqual(anchor.read_bytes(), b"user replacement")

    def test_delete_pending_same_byte_anchor_replacement_is_not_deleted(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic anchor sentinel")
        applied = apply_migration(self.plan([source]))
        current = copy.deepcopy(applied.manifest)
        current["run_state"] = "rolling-back"
        pending = current["entries"][0]
        pending["state"] = "delete-pending"
        pending["rollback_phase"] = "anchor"
        pending["quarantine_name"] = migration_module._new_internal_name(
            current, pending, "rollback"
        )
        migration_module._persist_manifest(current)

        entry = current["entries"][0]
        target = Path(entry["target"])
        anchor = migration_module._stage(current, entry)
        target.unlink()
        anchor.unlink()
        anchor.write_bytes(source.read_bytes())
        self.assertNotEqual(
            entry["file_identity"], migration_module._file_identity(anchor)
        )

        with self.assertRaises(MigrationError) as raised:
            rollback_migration(current)

        self.assertEqual(raised.exception.code, "owned_target_changed")
        self.assertEqual(anchor.read_bytes(), source.read_bytes())

    def test_delete_pending_crash_recovers_with_persisted_anchor_identity(self):
        source = self.sources / "idea.md"
        source.write_bytes(b"synthetic crash sentinel")
        applied = apply_migration(self.plan([source]))
        current = copy.deepcopy(applied.manifest)
        current["run_state"] = "rolling-back"
        pending = current["entries"][0]
        pending["state"] = "delete-pending"
        pending["rollback_phase"] = "anchor"
        pending["quarantine_name"] = migration_module._new_internal_name(
            current, pending, "rollback"
        )
        migration_module._persist_manifest(current)

        entry = current["entries"][0]
        target = Path(entry["target"])
        anchor = migration_module._stage(current, entry)
        target.unlink()
        self.assertTrue(anchor.is_file())
        self.assertEqual(
            entry["file_identity"], migration_module._file_identity(anchor)
        )

        recovered = rollback_migration(current)

        self.assertEqual(recovered.report["status"], "rolled-back")
        self.assertFalse(target.exists())
        self.assertFalse(anchor.exists())

    def test_each_persisted_quarantine_phase_recovers_after_crash(self):
        phases = (
            "target",
            "target-quarantined",
            "anchor",
            "anchor-quarantined",
        )
        for index, phase in enumerate(phases, start=1):
            with self.subTest(phase=phase):
                source = self.sources / f"phase-{index}.md"
                source.write_bytes(f"synthetic {phase}".encode())
                applied = apply_migration(
                    self.plan([source], batch_id=f"phase-{index}")
                )
                current = copy.deepcopy(applied.manifest)
                current["run_state"] = "rolling-back"
                entry = current["entries"][0]
                entry["state"] = "delete-pending"
                entry["rollback_phase"] = phase
                entry["quarantine_name"] = migration_module._new_internal_name(
                    current, entry, "rollback"
                )
                target = Path(entry["target"])
                anchor = migration_module._stage(current, entry)
                quarantine = migration_module._quarantine(current, entry)
                migration_module._persist_manifest(current)

                if phase.startswith("anchor"):
                    target.unlink()
                    migration_module._rename_noreplace(anchor, quarantine)
                else:
                    migration_module._rename_noreplace(target, quarantine)

                recovered = rollback_migration(current)

                self.assertEqual(recovered.report["status"], "rolled-back")
                self.assertFalse(target.exists())
                self.assertFalse(anchor.exists())
                self.assertFalse(quarantine.exists())

    def test_directory_without_supported_files_is_an_error(self):
        (self.sources / "data.txt").write_text("synthetic", encoding="utf-8")
        with self.assertRaises(MigrationError) as raised:
            self.plan()
        self.assertEqual(raised.exception.code, "no_supported_files")
        self.assertEqual(snapshot(self.workspace), {})

    def test_unsupported_explicit_file_is_rejected(self):
        unsupported = self.sources / "data.csv"
        unsupported.write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaises(MigrationError) as raised:
            self.plan([unsupported])
        self.assertEqual(raised.exception.code, "unsupported_source_type")

    def test_file_and_directory_selection_deduplicates_same_source(self):
        markdown, _, _ = self.write_sources()
        planned = self.plan([self.sources, markdown])
        sources = [entry["source"] for entry in planned.manifest["entries"]]
        self.assertEqual(len(sources), len(set(sources)))
        self.assertEqual(len(sources), 2)

    def test_reparse_attribute_is_rejected(self):
        metadata = SimpleNamespace(
            st_mode=stat.S_IFDIR, st_file_attributes=0x400
        )
        self.assertTrue(migration_module._linklike(metadata))

    def test_nested_directory_symlink_is_rejected_when_supported(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.md").write_text("private", encoding="utf-8")
        linked = self.sources / "linked"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")
        with self.assertRaises(MigrationError) as raised:
            self.plan()
        self.assertEqual(raised.exception.code, "link_or_reparse_rejected")

    def test_kernel_lock_is_released_when_process_exits_without_cleanup(self):
        lock = self.state_root / "migrations" / ("a" * 20) / "exit.lock"
        source_root = Path(__file__).resolve().parents[1] / "src"
        code = (
            "import os,sys;"
            "from pathlib import Path;"
            "from scriptorium.migration import _kernel_lock;"
            "lock=Path(sys.argv[1]);"
            "ctx=_kernel_lock(lock);"
            "ctx.__enter__();"
            "os._exit(0)"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(source_root), environment.get("PYTHONPATH", "")]
        )
        completed = subprocess.run(
            [sys.executable, "-c", code, str(lock)],
            env=environment,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        with migration_module._kernel_lock(lock):
            pass


if __name__ == "__main__":
    unittest.main()
