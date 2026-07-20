from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium import init as init_module
from scriptorium import inventory as inventory_module
from scriptorium.inventory import (
    InventoryError,
    format_inventory_report,
    run_inventory,
)


TOP_LEVEL_FIELDS = {
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
SUMMARY_FIELDS = {
    "roots_requested",
    "roots_scanned",
    "files_seen",
    "candidates",
    "markdown",
    "pdf",
    "ai_conversation",
    "zotero_export",
    "unsupported",
    "reparse_skipped",
}
GENERIC_WRITE = 0x40000000


def scan_argument_matches(value: object, target: Path) -> bool:
    """Match a path-based Windows scan or descriptor-based POSIX scan."""
    if isinstance(value, int):
        try:
            return os.path.samestat(
                os.fstat(value), os.stat(target, follow_symlinks=False)
            )
        except OSError:
            return False
    try:
        return Path(value) == target
    except TypeError:
        return False


def snapshot_tree(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture bytes and write-relevant metadata, including empty directories."""
    snapshot: dict[str, tuple[object, ...]] = {}
    candidates = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    for path in candidates:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        common = (metadata.st_mode, metadata.st_mtime_ns, metadata.st_ctime_ns)
        if path.is_symlink():
            snapshot[relative] = ("link", os.readlink(path), *common)
        elif path.is_dir():
            snapshot[relative] = ("directory", *common)
        elif path.is_file():
            snapshot[relative] = (
                "file",
                metadata.st_size,
                path.read_bytes(),
                *common,
            )
        else:
            snapshot[relative] = ("other", *common)
    return snapshot


def private_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for key, item in value.items():
            values.extend(private_strings(key))
            values.extend(private_strings(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(private_strings(item))
        return values
    return []


def create_windows_junction(link: Path, target: Path) -> bool:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        for item in value.values():
            keys.update(nested_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(nested_keys(item))
        return keys
    return set()


class InventoryTests(unittest.TestCase):
    def run_with(
        self,
        *,
        sources: list[Path] | None = None,
        conversation_exports: list[Path] | None = None,
        zotero_exports: list[Path] | None = None,
    ) -> dict:
        return run_inventory(
            sources=sources or [],
            conversation_exports=conversation_exports or [],
            zotero_exports=zotero_exports or [],
        )

    def assert_candidate_counts_discarded(self, report: dict) -> None:
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["exit_code"], 1)
        for field in SUMMARY_FIELDS - {"roots_requested"}:
            self.assertEqual(report["summary"][field], 0, field)
        self.assertEqual(
            report["routing_preview"],
            {
                "workspace-review": 0,
                "literature-reference": 0,
                "provenance-import-review": 0,
                "steward-review": 0,
            },
        )
        self.assertEqual(report["action_required"][0]["type"], "resolve-source-roots")
        self.assertGreaterEqual(report["action_required"][0]["count"], 1)

    def test_classifies_explicit_sources_without_guessing_or_parsing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ordinary = root / "ordinary"
            conversation = root / "conversation exports"
            zotero = root / "zotero exports"
            (ordinary / "nested").mkdir(parents=True)
            conversation.mkdir()
            zotero.mkdir()

            for relative in (
                "idea.MD",
                "draft.markdown",
                "paper.PDF",
                "random.json",
                "zotero.sqlite",
                "nested/experiment.pdf",
            ):
                (ordinary / relative).write_bytes(b"not parsed")
            for name in (
                "chat.JSON",
                "rollout.JSONL",
                "page.HTML",
                "bundle.ZIP",
                "not-a-conversation.pdf",
            ):
                (conversation / name).write_bytes(b"not parsed")
            for name in (
                "library.BIB",
                "library.BIBTEX",
                "library.RIS",
                "library.RDF",
                "library.JSON",
                "zotero.sqlite",
            ):
                (zotero / name).write_bytes(b"not parsed")

            report = self.run_with(
                sources=[ordinary],
                conversation_exports=[conversation],
                zotero_exports=[zotero],
            )

        self.assertEqual((report["status"], report["exit_code"]), ("planned", 0))
        self.assertEqual(
            report["summary"],
            {
                "roots_requested": 3,
                "roots_scanned": 3,
                "files_seen": 17,
                "candidates": 13,
                "markdown": 2,
                "pdf": 2,
                "ai_conversation": 4,
                "zotero_export": 5,
                "unsupported": 4,
                "reparse_skipped": 0,
            },
        )
        self.assertEqual(
            report["action_required"],
            [{"type": "review-routing-preview", "count": 13}],
        )
        self.assertEqual(
            report["routing_preview"],
            {
                "workspace-review": 2,
                "literature-reference": 2,
                "provenance-import-review": 4,
                "steward-review": 5,
            },
        )
        self.assertNotIn("command", json.dumps(report))

    def test_candidate_payloads_are_never_opened(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sources"
            conversation = root / "conversations"
            zotero = root / "zotero"
            source.mkdir()
            conversation.mkdir()
            zotero.mkdir()
            (source / "note.md").write_bytes(b"private markdown")
            (source / "paper.pdf").write_bytes(b"not a pdf")
            (conversation / "chat.zip").write_bytes(b"not a zip")
            (zotero / "library.json").write_bytes(b"not json")

            content_read = AssertionError("candidate payload was opened")
            native_read_patch = (
                mock.patch("scriptorium.init._ReadFile", side_effect=content_read)
                if os.name == "nt"
                else mock.patch(
                    "scriptorium.inventory.os.read", side_effect=content_read
                )
            )
            with (
                mock.patch("builtins.open", side_effect=content_read),
                mock.patch.object(Path, "open", side_effect=content_read),
                mock.patch.object(Path, "read_bytes", side_effect=content_read),
                mock.patch.object(Path, "read_text", side_effect=content_read),
                native_read_patch,
            ):
                report = self.run_with(
                    sources=[source],
                    conversation_exports=[conversation],
                    zotero_exports=[zotero],
                )

        self.assertEqual(report["summary"]["candidates"], 4)
        self.assertEqual(report["safety"]["content"], "not-read")

    def test_report_is_strictly_allowlisted_and_suppresses_private_values(self):
        with tempfile.TemporaryDirectory(prefix="private-research-root-") as temporary:
            root = Path(temporary)
            sentinel = "confidential-catalyst-result"
            source = root / f"{sentinel}-folder"
            source.mkdir()
            (source / f"{sentinel}.md").write_bytes(
                f"# {sentinel}\nsecret body".encode("utf-8")
            )

            report = self.run_with(sources=[source])
            rendered = format_inventory_report(report)

        self.assertEqual(set(report), TOP_LEVEL_FIELDS)
        self.assertEqual(set(report["summary"]), SUMMARY_FIELDS)
        self.assertEqual(report["operation"], "inventory")
        self.assertEqual(report["mode"], "preview")
        self.assertEqual(report["errors"], [])
        self.assertTrue(all(isinstance(item, str) for item in report["limitations"]))
        self.assertEqual(
            report["routing_preview"],
            {
                "workspace-review": 1,
                "literature-reference": 0,
                "provenance-import-review": 0,
                "steward-review": 0,
            },
        )
        self.assertEqual(
            report["egress"],
            {
                "suite_managed": "not-requested",
                "host_managed": "not-invoked",
                "optional_connectors": "not-invoked",
            },
        )
        self.assertEqual(
            report["safety"],
            {
                "writes": "none",
                "content": "not-read",
                "paths": "suppressed",
                "links": "not-followed",
                "roots": "explicit-only",
            },
        )
        serialized = json.dumps(report, ensure_ascii=False)
        for forbidden in (str(root), str(source), sentinel, "secret body"):
            self.assertNotIn(forbidden, serialized)
            self.assertNotIn(forbidden, rendered)
        self.assertFalse(
            {
                "path",
                "absolute_path",
                "relative_path",
                "filename",
                "size",
                "hash",
                "sha256",
                "mtime",
                "content_body",
            }
            & nested_keys(report)
        )
        self.assertNotIn("content", private_strings(report["routing_preview"]))

    def test_preview_preserves_the_entire_tree_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "科研 source 🧪"
            conversation = root / "AI 对话"
            source.mkdir()
            conversation.mkdir()
            (source / "研究笔记.MD").write_bytes("私有正文".encode("utf-8"))
            (source / "论文.PdF").write_bytes(b"pdf bytes")
            (conversation / "会话.JsOnL").write_bytes(b"conversation bytes")
            before = snapshot_tree(root)

            first = self.run_with(
                sources=[source], conversation_exports=[conversation]
            )
            middle = snapshot_tree(root)
            second = self.run_with(
                sources=[source], conversation_exports=[conversation]
            )
            after = snapshot_tree(root)

        self.assertEqual(before, middle)
        self.assertEqual(middle, after)
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["markdown"], 1)
        self.assertEqual(first["summary"]["pdf"], 1)
        self.assertEqual(first["summary"]["ai_conversation"], 1)

    def test_unrecognized_files_produce_a_complete_noop_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "measurements.csv").write_bytes(b"x,y\n1,2\n")
            (source / "zotero.sqlite").write_bytes(b"not accepted")

            report = self.run_with(sources=[source])

        self.assertEqual((report["status"], report["exit_code"]), ("noop", 0))
        self.assertEqual(report["summary"]["files_seen"], 2)
        self.assertEqual(report["summary"]["unsupported"], 2)
        self.assertEqual(report["summary"]["candidates"], 0)
        self.assertEqual(report["action_required"], [])
        rendered = format_inventory_report(report)
        self.assertIn("no supported candidates", rendered)
        self.assertNotIn("review the aggregate routing preview", rendered)

    def test_single_file_sources_support_unicode_and_casefolded_extensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "科研 files 🧪"
            root.mkdir()
            markdown = root / "想法.Md"
            conversation = root / "对话.HtMl"
            zotero = root / "文献.RiS"
            markdown.write_bytes(b"markdown")
            conversation.write_bytes(b"conversation")
            zotero.write_bytes(b"zotero")

            report = self.run_with(
                sources=[markdown],
                conversation_exports=[conversation],
                zotero_exports=[zotero],
            )

        self.assertEqual(report["summary"]["roots_scanned"], 3)
        self.assertEqual(report["summary"]["markdown"], 1)
        self.assertEqual(report["summary"]["ai_conversation"], 1)
        self.assertEqual(report["summary"]["zotero_export"], 1)

    def test_root_symlink_is_rejected_without_candidate_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "outside.md").write_bytes(b"outside")
            link = root / "source-link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            report = self.run_with(sources=[link])

        self.assert_candidate_counts_discarded(report)
        self.assertEqual(report["summary"]["roots_requested"], 1)
        self.assertEqual(report["summary"]["roots_scanned"], 0)

    def test_linked_ancestor_is_rejected_without_candidate_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            nested = target / "nested"
            nested.mkdir(parents=True)
            (nested / "outside.pdf").write_bytes(b"outside")
            link = root / "linked-parent"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            report = self.run_with(sources=[link / "nested"])

        self.assert_candidate_counts_discarded(report)
        self.assertEqual(report["summary"]["roots_scanned"], 0)

    def test_nested_reparse_is_skipped_and_never_traversed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            outside = root / "outside"
            source.mkdir()
            outside.mkdir()
            (source / "inside.md").write_bytes(b"inside")
            (outside / "must-not-count.pdf").write_bytes(b"outside")
            linked = source / "linked-outside"
            try:
                linked.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            report = self.run_with(sources=[source])

        self.assertEqual((report["status"], report["exit_code"]), ("planned", 0))
        self.assertEqual(report["summary"]["markdown"], 1)
        self.assertEqual(report["summary"]["pdf"], 0)
        self.assertEqual(report["summary"]["reparse_skipped"], 1)

    @unittest.skipUnless(os.name == "nt", "Windows UNC behavior")
    def test_unc_source_is_rejected_before_scanning(self):
        unc = Path(r"\\127.0.0.1\scriptorium-private-share")
        with mock.patch("scriptorium.inventory.os.scandir") as scan:
            report = self.run_with(sources=[unc])

        scan.assert_not_called()
        self.assert_candidate_counts_discarded(report)
        self.assertEqual(report["summary"]["roots_scanned"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows mapped-drive behavior")
    def test_mapped_remote_source_is_rejected_before_scanning(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            with (
                mock.patch("scriptorium.inventory._windows_drive_type", return_value=4),
                mock.patch("scriptorium.inventory.os.scandir") as scan,
            ):
                report = self.run_with(sources=[source])

        scan.assert_not_called()
        self.assert_candidate_counts_discarded(report)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_root_and_ancestor_junctions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            nested = target / "nested"
            nested.mkdir(parents=True)
            (nested / "outside.md").write_bytes(b"outside")
            junction = root / "junction"
            if not create_windows_junction(junction, target):
                self.skipTest("directory junctions are unavailable")

            root_report = self.run_with(sources=[junction])
            ancestor_report = self.run_with(sources=[junction / "nested"])

        self.assert_candidate_counts_discarded(root_report)
        self.assert_candidate_counts_discarded(ancestor_report)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_nested_junction_is_counted_without_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            outside = root / "outside"
            source.mkdir()
            outside.mkdir()
            (source / "inside.md").write_bytes(b"inside")
            (outside / "must-not-count.pdf").write_bytes(b"outside")
            junction = source / "linked-outside"
            if not create_windows_junction(junction, outside):
                self.skipTest("directory junctions are unavailable")

            report = self.run_with(sources=[source])

        self.assertEqual((report["status"], report["exit_code"]), ("planned", 0))
        self.assertEqual(report["summary"]["markdown"], 1)
        self.assertEqual(report["summary"]["pdf"], 0)
        self.assertEqual(report["summary"]["reparse_skipped"], 1)

    def test_duplicate_overlapping_and_cross_kind_sources_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent"
            child = parent / "child"
            child.mkdir(parents=True)
            (child / "note.md").write_bytes(b"note")

            reports = (
                self.run_with(sources=[parent, parent]),
                self.run_with(sources=[parent, child]),
                self.run_with(sources=[parent], conversation_exports=[parent]),
            )

        for report in reports:
            with self.subTest(summary=report["summary"]):
                self.assert_candidate_counts_discarded(report)
                self.assertEqual(report["summary"]["roots_scanned"], 0)

    def test_scan_limit_discards_candidate_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "one.md").write_bytes(b"one")
            (source / "two.pdf").write_bytes(b"two")

            with mock.patch("scriptorium.inventory.MAX_FILES", 1):
                report = self.run_with(sources=[source])

        self.assert_candidate_counts_discarded(report)

    def test_root_and_directory_budgets_fail_closed_before_unbounded_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            child = first / "child"
            child.mkdir(parents=True)
            second.mkdir()

            with (
                mock.patch("scriptorium.inventory.MAX_ROOTS", 1),
                mock.patch("scriptorium.inventory.os.scandir") as scan,
            ):
                roots_report = self.run_with(sources=[first, second])
            scan.assert_not_called()

            with mock.patch("scriptorium.inventory.MAX_DIRECTORIES", 1):
                directories_report = self.run_with(sources=[first])

        self.assert_candidate_counts_discarded(roots_report)
        self.assert_candidate_counts_discarded(directories_report)

    def test_root_budget_stops_consuming_an_oversized_iterable(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            consumed = 0

            def oversized_roots():
                nonlocal consumed
                for _ in range(4):
                    consumed += 1
                    yield source
                raise AssertionError("root iterable was consumed past the limit")

            with (
                mock.patch("scriptorium.inventory.MAX_ROOTS", 2),
                mock.patch("scriptorium.inventory.os.scandir") as scan,
            ):
                report = run_inventory(
                    sources=oversized_roots(),
                    conversation_exports=[],
                    zotero_exports=[],
                )
            scan.assert_not_called()

        self.assertEqual(consumed, 3)
        self.assertEqual(report["summary"]["roots_requested"], 3)
        self.assert_candidate_counts_discarded(report)

    def test_entry_budget_counts_linklike_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            outside = root / "outside"
            source.mkdir()
            outside.mkdir()
            links = [source / "first-link", source / "second-link"]
            if os.name == "nt":
                for link in links:
                    if not create_windows_junction(link, outside):
                        self.skipTest("directory junctions are unavailable")
            else:
                try:
                    for link in links:
                        link.symlink_to(outside, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"directory symlinks are unavailable: {exc}")

            with mock.patch("scriptorium.inventory.MAX_ENTRIES", 1):
                report = self.run_with(sources=[source])

        self.assert_candidate_counts_discarded(report)

    def test_hardlink_aliases_across_source_kinds_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.md"
            alias = root / "alias.json"
            original.write_bytes(b"private")
            try:
                os.link(original, alias)
            except OSError as exc:
                self.skipTest(f"hardlinks are unavailable: {exc}")

            report = self.run_with(
                sources=[original], conversation_exports=[alias]
            )

        self.assert_candidate_counts_discarded(report)
        self.assertEqual(
            report["errors"], [{"code": "source_roots_overlap", "count": 1}]
        )

    def test_permission_failure_discards_previously_observed_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            blocked = source / "blocked"
            blocked.mkdir(parents=True)
            (source / "seen-first.md").write_bytes(b"private")
            (blocked / "hidden.pdf").write_bytes(b"private")
            real_scandir = os.scandir

            def deny(path):
                if scan_argument_matches(path, blocked):
                    raise PermissionError("private blocked path")
                return real_scandir(path)

            with mock.patch("scriptorium.inventory.os.scandir", side_effect=deny):
                report = self.run_with(sources=[source])

        self.assert_candidate_counts_discarded(report)
        self.assertNotIn("private blocked path", json.dumps(report))

    def test_enumeration_error_is_a_content_free_partial_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()

            class FailingScandir:
                def __init__(self):
                    self.closed = False

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, traceback):
                    self.close()
                    return False

                def __iter__(self):
                    return self

                def __next__(self):
                    raise OSError("private enumeration failure")

                def close(self):
                    self.closed = True

            failing = FailingScandir()
            with mock.patch(
                "scriptorium.inventory.os.scandir", return_value=failing
            ):
                report = self.run_with(sources=[source])

        self.assertTrue(failing.closed)
        self.assert_candidate_counts_discarded(report)
        self.assertEqual(
            report["errors"], [{"code": "source_scan_failed", "count": 1}]
        )
        self.assertNotIn("private enumeration failure", json.dumps(report))

    def test_bound_root_blocks_or_isolates_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            moved = base / "moved"
            source.mkdir()
            (source / "original.md").write_bytes(b"private")
            real_scandir = os.scandir
            attempted = False
            replaced = False
            blocked = False

            def swap_before_scan(path):
                nonlocal attempted, replaced, blocked
                if not attempted and scan_argument_matches(path, source):
                    attempted = True
                    try:
                        source.rename(moved)
                    except OSError:
                        blocked = True
                    else:
                        replaced = True
                        source.mkdir()
                        (source / "replacement.pdf").write_bytes(b"replacement")
                return real_scandir(path)

            with mock.patch(
                "scriptorium.inventory.os.scandir", side_effect=swap_before_scan
            ):
                report = self.run_with(sources=[source])

        self.assertTrue(attempted)
        if os.name == "nt":
            self.assertTrue(blocked)
            self.assertFalse(replaced)
            self.assertEqual((report["status"], report["exit_code"]), ("planned", 0))
            self.assertEqual(report["summary"]["markdown"], 1)
            self.assertEqual(report["summary"]["pdf"], 0)
        else:
            self.assertTrue(replaced)
            self.assertFalse(blocked)
            self.assert_candidate_counts_discarded(report)

    @unittest.skipUnless(os.name == "nt", "Windows sharing behavior")
    def test_windows_binding_blocks_generic_write_handles(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "note.md").write_bytes(b"private")
            real_scandir = os.scandir
            attempted = False
            blocked = False

            def try_write_open(path):
                nonlocal attempted, blocked
                if not attempted and scan_argument_matches(path, source):
                    attempted = True
                    handle = init_module._CreateFileW(
                        str(source),
                        GENERIC_WRITE,
                        init_module._FILE_SHARE_READ
                        | init_module._FILE_SHARE_WRITE,
                        None,
                        init_module._OPEN_EXISTING,
                        init_module._FILE_FLAG_OPEN_REPARSE_POINT
                        | init_module._FILE_FLAG_BACKUP_SEMANTICS,
                        None,
                    )
                    blocked = handle == init_module._INVALID_HANDLE_VALUE
                    if not blocked:
                        init_module._win_close(handle)
                return real_scandir(path)

            with mock.patch(
                "scriptorium.inventory.os.scandir",
                side_effect=try_write_open,
            ):
                report = self.run_with(sources=[source])

        self.assertTrue(attempted)
        self.assertTrue(blocked)
        self.assertEqual((report["status"], report["exit_code"]), ("planned", 0))

    def test_ancestor_replacement_before_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            trusted = base / "trusted"
            selected = trusted / "selected"
            moved = base / "moved"
            outside = base / "outside"
            outside_selected = outside / "selected"
            selected.mkdir(parents=True)
            outside_selected.mkdir(parents=True)
            (selected / "inside.md").write_bytes(b"inside")
            (outside_selected / "outside.pdf").write_bytes(b"outside")

            if os.name == "nt":
                probe = base / "junction-probe"
                if not create_windows_junction(probe, outside):
                    self.skipTest("directory junctions are unavailable")
                probe.rmdir()
                binding_name = "_open_windows_directory_tree"
            else:
                probe = base / "symlink-probe"
                try:
                    probe.symlink_to(outside, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"directory symlinks are unavailable: {exc}")
                probe.unlink()
                binding_name = "_open_posix_directory_tree"

            real_binding = getattr(inventory_module, binding_name)
            swapped = False

            def swap_before_binding(path, *, create=True, share=None):
                nonlocal swapped
                if not swapped and Path(path) == trusted:
                    swapped = True
                    trusted.rename(moved)
                    if os.name == "nt":
                        if not create_windows_junction(trusted, outside):
                            raise OSError("junction replacement failed")
                    else:
                        trusted.symlink_to(outside, target_is_directory=True)
                if os.name == "nt":
                    return real_binding(path, create=create, share=share)
                return real_binding(path, create=create)

            with (
                mock.patch(
                    f"scriptorium.inventory.{binding_name}",
                    side_effect=swap_before_binding,
                ),
                mock.patch("scriptorium.inventory.os.scandir") as scan,
            ):
                report = self.run_with(sources=[selected])

            scan.assert_not_called()

        self.assertTrue(swapped)
        self.assert_candidate_counts_discarded(report)

    def test_directory_change_after_enumeration_discards_candidate_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "original.md").write_bytes(b"private")
            initial_metadata = source.stat()
            real_scandir = os.scandir
            mutated = False

            class MutatingScandir:
                def __init__(self, path):
                    self.matches_source = scan_argument_matches(path, source)
                    self.iterator = real_scandir(path)
                    self.closed = False

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, traceback):
                    self.close()
                    return False

                def __iter__(self):
                    return self

                def __next__(self):
                    return next(self.iterator)

                def close(self):
                    nonlocal mutated
                    if self.closed:
                        return
                    self.iterator.close()
                    self.closed = True
                    if not mutated and self.matches_source:
                        mutated = True
                        (source / "late.pdf").write_bytes(b"late")
                        os.utime(
                            source,
                            ns=(
                                initial_metadata.st_atime_ns,
                                initial_metadata.st_mtime_ns + 1_000_000_000,
                            ),
                        )

            with mock.patch(
                "scriptorium.inventory.os.scandir", side_effect=MutatingScandir
            ):
                report = self.run_with(sources=[source])

        self.assertTrue(mutated)
        self.assert_candidate_counts_discarded(report)

    def test_missing_and_special_sources_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing"
            missing_report = self.run_with(sources=[missing])
            self.assert_candidate_counts_discarded(missing_report)
            rendered = format_inventory_report(missing_report)
            self.assertIn("resolve the explicit source roots", rendered)
            self.assertNotIn("review the aggregate routing preview", rendered)

            if not hasattr(os, "mkfifo"):
                return
            special = root / "special.pipe"
            try:
                os.mkfifo(special)
            except (OSError, NotImplementedError):
                return
            special_report = self.run_with(sources=[special])
            self.assert_candidate_counts_discarded(special_report)

    def test_invalid_api_values_raise_inventory_error(self):
        invalid_calls = (
            {"sources": None, "conversation_exports": [], "zotero_exports": []},
            {"sources": [object()], "conversation_exports": [], "zotero_exports": []},
            {"sources": [], "conversation_exports": [], "zotero_exports": []},
        )
        for arguments in invalid_calls:
            with self.subTest(arguments=arguments), self.assertRaises(InventoryError):
                run_inventory(**arguments)


if __name__ == "__main__":
    unittest.main()
