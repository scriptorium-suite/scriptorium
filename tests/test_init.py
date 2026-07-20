import ctypes
import os
import importlib
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from scriptorium.config import load_config, resolve_config_path
from scriptorium.doctor import _provenance_home_check, _workspace_check
from scriptorium.init import (
    InitError,
    PROGRESS_BEGIN,
    PROGRESS_END,
    format_init_report,
    parse_project_frontmatter,
    run_init,
)


init_module = importlib.import_module("scriptorium.init")


class InitTests(unittest.TestCase):
    def paths(self, root: Path) -> dict:
        return {
            "workspace": root / "研究 workspace",
            "provenance_home": root / "private provenance",
            "project_id": "catalyst-screening",
            "title": "催化剂 Screening Study",
            "hosts": ["codex", "claude-code"],
            "config_dir": root / "suite config",
            "today": date(2026, 7, 16),
        }

    def test_preview_is_byte_zero_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)

            report = run_init(**arguments)

            self.assertEqual(report["operation"], "init")
            self.assertEqual(report["mode"], "preview")
            self.assertEqual(report["status"], "planned")
            self.assertEqual(report["exit_code"], 0)
            self.assertGreater(report["summary"]["create"], 0)
            self.assertFalse(arguments["workspace"].exists())
            self.assertFalse(arguments["provenance_home"].exists())
            self.assertFalse(arguments["config_dir"].exists())
            for change in report["changes"]:
                self.assertNotIn(str(root), change["path"])
                self.assertIn(change["root"], {"workspace", "provenance_home", "config"})

    def test_run_creates_unicode_project_config_and_private_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            linked_repo = root / "代码 repo with spaces"
            linked_repo.mkdir()

            report = run_init(
                **arguments,
                linked_repo=linked_repo,
                idea="用 calibrated uncertainty 缩小实验搜索空间。",
                run=True,
            )

            workspace = arguments["workspace"].resolve()
            home = arguments["provenance_home"].resolve()
            note = workspace / "Projects" / "catalyst-screening.md"
            self.assertEqual(report["status"], "initialized")
            self.assertEqual(report["exit_code"], 0)
            self.assertTrue(home.is_dir())
            for relative in ("Projects", "Inbox", "_planning"):
                self.assertTrue((workspace / relative).is_dir())
            text = note.read_text(encoding="utf-8")
            self.assertIn('title: "催化剂 Screening Study"', text)
            self.assertIn("status: planned", text)
            self.assertIn('stage: ""', text)
            self.assertIn("next_actions: []", text)
            self.assertIn('blocked_by: ""', text)
            repo_value = os.path.abspath(linked_repo).replace(chr(92), "/")
            self.assertIn(f'linked_repo: "{repo_value}"', text)
            self.assertIn('linked_conversations: "catalyst-screening"', text)
            self.assertIn('updated: "2026-07-16"', text)
            self.assertIn("## Research intuition", text)
            self.assertIn("## Research question", text)
            self.assertIn("## Evidence and literature", text)
            self.assertIn("## Next actions", text)
            self.assertIn("## Progress log", text)
            self.assertEqual(text.count(PROGRESS_BEGIN), 1)
            self.assertEqual(text.count(PROGRESS_END), 1)
            parsed_note = parse_project_frontmatter(note.read_bytes())
            self.assertIsNotNone(parsed_note)
            self.assertEqual(parsed_note["next_actions"], [])
            self.assertEqual(parsed_note["linked_literature"], [])
            self.assertEqual(parsed_note["updated"], "2026-07-16")
            self.assertEqual(
                list((workspace / "Projects").glob(".scriptorium-init-*.tmp")), []
            )
            self.assertEqual(
                list(resolve_config_path(arguments["config_dir"]).parent.glob(
                    ".scriptorium-init-*.tmp"
                )),
                [],
            )

            config = load_config(arguments["config_dir"])
            self.assertIsNotNone(config)
            self.assertEqual(
                config.workspace, Path(os.path.abspath(arguments["workspace"]))
            )
            self.assertEqual(
                config.provenance_home,
                Path(os.path.abspath(arguments["provenance_home"])),
            )
            self.assertEqual(config.hosts, ("claude-code", "codex"))
            self.assertEqual(config.default_project, "catalyst-screening")
            self.assertEqual(_workspace_check("public-alpha", workspace)["status"], "pass")
            self.assertEqual(
                _provenance_home_check("public-alpha", home, workspace)["status"],
                "pass",
            )

    def test_idempotency_preserves_user_edited_project_and_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            first = run_init(**arguments, idea="Initial idea", run=True)
            self.assertEqual(first["status"], "initialized")
            note = arguments["workspace"] / "Projects" / "catalyst-screening.md"
            note.write_bytes(note.read_bytes() + b"\nHuman edit outside the managed region.\n")
            config_path = resolve_config_path(arguments["config_dir"])
            note_before = note.read_bytes()
            config_before = config_path.read_bytes()
            second_arguments = dict(arguments)
            second_arguments.update(
                title="A different requested title",
                idea="A different requested idea",
            )

            second = run_init(**second_arguments, run=True)

            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(second["summary"]["create"], 0)
            self.assertEqual(note.read_bytes(), note_before)
            self.assertEqual(config_path.read_bytes(), config_before)

    def test_existing_human_workspace_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            arguments["workspace"].mkdir()
            human_file = arguments["workspace"] / "my notes.md"
            human_file.write_bytes(b"human-owned\r\ncontent\r\n")

            report = run_init(**arguments, run=True)

            self.assertEqual(report["exit_code"], 0)
            self.assertEqual(human_file.read_bytes(), b"human-owned\r\ncontent\r\n")

    def test_project_identity_conflict_is_exit_one_and_writes_nothing_else(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            projects = arguments["workspace"] / "Projects"
            projects.mkdir(parents=True)
            note = projects / "catalyst-screening.md"
            original = b"---\nschema_version: project/1.0\nproject_id: another-id\n---\nHuman\n"
            note.write_bytes(original)

            report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["exit_code"], 1)
            self.assertEqual(report["conflict"]["code"], "project-identity-conflict")
            self.assertEqual(note.read_bytes(), original)
            self.assertFalse(resolve_config_path(arguments["config_dir"]).exists())
            self.assertFalse(arguments["provenance_home"].exists())

    def test_existing_matching_project_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            run_init(**arguments, run=True)
            note = arguments["workspace"] / "Projects" / "catalyst-screening.md"
            custom = note.read_bytes() + b"\nA reviewed conclusion.\n"
            note.write_bytes(custom)

            report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "unchanged")
            self.assertEqual(note.read_bytes(), custom)

    def test_matching_identity_without_complete_contract_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            projects = arguments["workspace"] / "Projects"
            projects.mkdir(parents=True)
            note = projects / "catalyst-screening.md"
            note.write_text(
                "---\n"
                "schema_version: project/1.0\n"
                "project_id: catalyst-screening\n"
                "---\n\n"
                "Human-owned draft\n",
                encoding="utf-8",
            )

            report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["conflict"]["code"], "project-identity-conflict")
            self.assertFalse(resolve_config_path(arguments["config_dir"]).exists())

    def test_existing_config_with_different_roots_conflicts_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.paths(root)
            run_init(**first, run=True)
            second = dict(first)
            second["workspace"] = root / "second workspace"
            second["project_id"] = "second-project"
            second["title"] = "Second project"

            report = run_init(**second, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["exit_code"], 1)
            self.assertEqual(report["conflict"]["code"], "config-selection-conflict")
            self.assertFalse(second["workspace"].exists())

    def test_existing_config_with_different_default_project_conflicts_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.paths(root)
            run_init(**first, run=True)
            second = dict(first)
            second["project_id"] = "second-project"
            second["title"] = "Second project"

            report = run_init(**second, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["exit_code"], 1)
            self.assertEqual(report["conflict"]["code"], "config-selection-conflict")
            self.assertFalse(
                (second["workspace"] / "Projects" / "second-project.md").exists()
            )

    def test_workspace_and_home_overlap_is_rejected_in_both_directions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                (root / "same", root / "same"),
                (root / "workspace", root / "workspace" / "private"),
                (root / "private", root / "private" / "workspace"),
            )
            for workspace, home in cases:
                with self.subTest(workspace=workspace, home=home):
                    with self.assertRaisesRegex(InitError, "separate, non-nested"):
                        run_init(
                            workspace=workspace,
                            provenance_home=home,
                            project_id="safe-project",
                            title="Safe project",
                            hosts=["codex"],
                            config_dir=root / "config",
                        )
                    self.assertFalse(workspace.exists())
                    self.assertFalse(home.exists())

    def test_config_path_inside_workspace_or_home_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "private"
            for config_dir in (workspace, home):
                with self.subTest(config_dir=config_dir):
                    with self.assertRaisesRegex(InitError, "suite config must be outside"):
                        run_init(
                            workspace=workspace,
                            provenance_home=home,
                            project_id="safe-project",
                            title="Safe project",
                            hosts=["codex"],
                            config_dir=config_dir,
                        )
                    self.assertFalse(workspace.exists())
                    self.assertFalse(home.exists())

    def test_linklike_workspace_or_managed_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace_link = root / "workspace-link"
            try:
                workspace_link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are not available")
            with self.assertRaisesRegex(InitError, "symlink, junction, or reparse"):
                run_init(
                    workspace=workspace_link,
                    provenance_home=root / "home",
                    project_id="safe-project",
                    title="Safe project",
                    hosts=["codex"],
                    config_dir=root / "config",
                )
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "Projects").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(InitError, "link-like"):
                run_init(
                    workspace=workspace,
                    provenance_home=root / "home",
                    project_id="safe-project",
                    title="Safe project",
                    hosts=["codex"],
                    config_dir=root / "config",
                )

    def test_mocked_linklike_managed_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            arguments["workspace"].mkdir()
            unsafe = arguments["workspace"] / "Projects"
            with mock.patch(
                "scriptorium.init._is_linklike",
                side_effect=lambda path: path == unsafe,
            ):
                with self.assertRaisesRegex(InitError, "link-like"):
                    run_init(**arguments)

    def test_reparse_detection_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            unsafe = arguments["workspace"]
            with mock.patch(
                "scriptorium.init._is_linklike",
                side_effect=lambda path: path == unsafe,
            ):
                with self.assertRaisesRegex(InitError, "reparse point"):
                    run_init(**arguments)

    def test_linked_repo_must_be_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            with self.assertRaisesRegex(InitError, "linked repository does not exist"):
                run_init(**arguments, linked_repo=root / "missing")
            file_path = root / "repo.txt"
            file_path.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(InitError, "not a directory"):
                run_init(**arguments, linked_repo=file_path)

    def test_invalid_identity_title_hosts_and_marker_idea_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = self.paths(root)
            cases = (
                ({"project_id": "Bad_ID"}, "kebab-case"),
                ({"project_id": "bad--id"}, "kebab-case"),
                ({"title": "  "}, "single-line"),
                ({"title": "two\nlines"}, "single-line"),
                ({"hosts": []}, "at least one"),
                ({"hosts": ["unknown"]}, "supported"),
                ({"hosts": ["codex", "codex"]}, "duplicates"),
                ({"idea": PROGRESS_BEGIN}, "managed progress-log markers"),
                ({"title": f"Unsafe {PROGRESS_END}"}, "managed progress-log markers"),
            )
            for overrides, message in cases:
                arguments = dict(base)
                arguments.update(overrides)
                with self.subTest(overrides=overrides):
                    with self.assertRaisesRegex(InitError, message):
                        run_init(**arguments)

    def test_exclusive_create_race_preserves_the_other_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            config_path = resolve_config_path(arguments["config_dir"])
            external = b"external writer won\n"
            real_create = init_module._create_bound_file

            def race_create(parent, leaf, payload, **kwargs):
                if kwargs["root"] == "config":
                    config_path.write_bytes(external)
                return real_create(parent, leaf, payload, **kwargs)

            with mock.patch.object(
                init_module, "_create_bound_file", side_effect=race_create
            ):
                report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["exit_code"], 1)
            self.assertEqual(report["conflict"]["code"], "concurrent-create-conflict")
            self.assertEqual(config_path.read_bytes(), external)
            self.assertEqual(
                list(config_path.parent.glob(".scriptorium-init-*.tmp")), []
            )
            self.assertTrue(
                (arguments["workspace"] / "Projects" / "catalyst-screening.md").is_file()
            )

    def test_default_linked_repo_is_the_resolved_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)

            run_init(**arguments, run=True)

            note = arguments["workspace"] / "Projects" / "catalyst-screening.md"
            workspace_value = str(arguments["workspace"].absolute()).replace("\\", "/")
            self.assertIn(
                f'linked_repo: "{workspace_value}"',
                note.read_text(encoding="utf-8"),
            )

    def test_numeric_project_id_is_quoted_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            arguments.update(project_id="01", title="Numeric identity")

            first = run_init(**arguments, run=True)
            second = run_init(**arguments, run=True)

            note = arguments["workspace"] / "Projects" / "01.md"
            text = note.read_text(encoding="utf-8")
            self.assertEqual(first["status"], "initialized")
            self.assertEqual(second["status"], "unchanged")
            self.assertIn('project_id: "01"', text)
            self.assertIn('linked_conversations: "01"', text)
            self.assertEqual(parse_project_frontmatter(note.read_bytes())["project_id"], "01")

    def test_project_race_never_publishes_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            project_path = (
                arguments["workspace"] / "Projects" / "catalyst-screening.md"
            )
            config_path = resolve_config_path(arguments["config_dir"])
            external = b"external project writer\n"
            real_create = init_module._create_bound_file

            def race_create(parent, leaf, payload, **kwargs):
                if kwargs["root"] == "workspace":
                    project_path.write_bytes(external)
                return real_create(parent, leaf, payload, **kwargs)

            with mock.patch.object(
                init_module, "_create_bound_file", side_effect=race_create
            ):
                report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["conflict"]["code"], "concurrent-create-conflict")
            self.assertEqual(project_path.read_bytes(), external)
            self.assertFalse(config_path.exists())
            self.assertEqual(
                list(project_path.parent.glob(".scriptorium-init-*.tmp")), []
            )

    def test_project_is_published_before_config_discovery_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            calls = []
            real_create = init_module._create_bound_file

            def record_create(parent, leaf, payload, **kwargs):
                calls.append((kwargs["root"], kwargs["relative"]))
                return real_create(parent, leaf, payload, **kwargs)

            with mock.patch.object(
                init_module, "_create_bound_file", side_effect=record_create
            ):
                report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "initialized")
            self.assertEqual(
                calls,
                [
                    ("workspace", "Projects/catalyst-screening.md"),
                    ("config", "scriptorium/config.toml"),
                ],
            )

    def test_strict_project_frontmatter_rejects_non_string_or_ambiguous_scalars(self):
        valid = (
            b"---\n"
            b"schema_version: project/1.0\n"
            b"project_id: catalyst-screening\n"
            b"title: Valid title\n"
            b"status: planned\n"
            b"---\n"
        )
        self.assertEqual(
            parse_project_frontmatter(valid)["project_id"], "catalyst-screening"
        )
        c_sharp = valid.replace(b"title: Valid title", b"title: C# Catalyst")
        self.assertEqual(parse_project_frontmatter(c_sharp)["title"], "C# Catalyst")
        invalid_replacements = (
            (b"title: Valid title", b"title: 42"),
            (b"title: Valid title", b"title: .5"),
            (b"title: Valid title", b"title: 0123"),
            (b"title: Valid title", b"title: []"),
            (b"title: Valid title", b"title: null"),
            (b"title: Valid title", b"title: true"),
            (b"title: Valid title", b'title: "unterminated'),
            (b"title: Valid title", b"title: bad:\tvalue"),
            (b"title: Valid title", b"title: %directive"),
            (b"title: Valid title", b"title: ,flow"),
            (b"status: planned", b"status: planned\nstatus: active"),
            (b"project_id: catalyst-screening", b"project_id: 42"),
            (b"status: planned", b"status: planned\nnot valid yaml"),
            (b"status: planned", b"status: planned\n  orphan: value"),
        )
        for old, new in invalid_replacements:
            with self.subTest(new=new):
                self.assertIsNone(parse_project_frontmatter(valid.replace(old, new)))
        self.assertIsNone(parse_project_frontmatter(b"  " + valid))
        commented = valid.replace(
            b"title: Valid title", b'title: "Quoted # title" # comment'
        )
        self.assertEqual(
            parse_project_frontmatter(commented)["title"], "Quoted # title"
        )
        apostrophe = valid.replace(
            b"title: Valid title", b"title: Bob's study # comment"
        )
        self.assertEqual(parse_project_frontmatter(apostrophe)["title"], "Bob's study")

    def test_known_optional_fields_are_strictly_typed(self):
        base = (
            b"---\n"
            b"schema_version: project/1.0\n"
            b"project_id: catalyst-screening\n"
            b"title: Valid title\n"
            b"status: planned\n"
            b"---\n"
        )
        invalid_lines = (
            b"next_actions:\n",
            b"next_actions:\n  - 42\n",
            b"stage: []\n",
            b"blocked_by: null\n",
            b"linked_repo: true\n",
            b"linked_conversations: 42\n",
            b"updated: 2026-02-30\n",
            b"priority: urgent\n",
            b"next_actions: [\"one\", 2]\n",
            b"linked_literature: {}\n",
            b"stage: exploration\nstage: writing\n",
        )
        for invalid in invalid_lines:
            with self.subTest(invalid=invalid):
                payload = base.replace(b"---\n", b"---\n" + invalid, 1)
                self.assertIsNone(parse_project_frontmatter(payload))

    def test_known_optional_string_arrays_accept_safe_flow_and_block_forms(self):
        payload = (
            b"---\n"
            b"schema_version: project/1.0\n"
            b"project_id: catalyst-screening\n"
            b"title: Valid title\n"
            b"status: active\n"
            b"stage: exploration\n"
            b"priority: high\n"
            b"next_actions: [\"read paper\", \"design test\"]\n"
            b"linked_literature:\n"
            b"  - DEMO0001\n"
            b"  - \"DEMO0002\"\n"
            b"blocked_by: \"\"\n"
            b"linked_repo: \"C:/ScriptoriumDemo/research\"\n"
            b"linked_conversations: catalyst-screening\n"
            b"updated: \"2026-07-16\"\n"
            b"custom_extension: 42\n"
            b"---\n"
        )

        parsed = parse_project_frontmatter(payload)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["next_actions"], ["read paper", "design test"])
        self.assertEqual(parsed["linked_literature"], ["DEMO0001", "DEMO0002"])
        self.assertEqual(parsed["priority"], "high")

    def test_project_portfolio_convention_frontmatter_is_compatible(self):
        payload = (
            "---\n"
            "schema_version: project/1.0\n"
            "project_id: synthetic-xq17-calibration\n"
            "title: '[SYNTHETIC] XQ-17 calibration demo'\n"
            "status: active            # planned | active | paused | done | archived\n"
            "stage: synthetic-validation\n"
            "priority: high\n"
            "next_actions: [generate fixture set, validate synthetic drift]\n"
            "blocked_by: \"\"\n"
            "linked_literature: [DEMO0001, DEMO0002]\n"
            "linked_repo: C:/ScriptoriumDemo/projects/synthetic-xq17-calibration\n"
            "linked_conversations: synthetic-xq17-calibration\n"
            "updated: 2099-01-15\n"
            "future_extension: allowed\n"
            "---\n"
        ).encode("utf-8")

        parsed = parse_project_frontmatter(payload)

        self.assertIsNotNone(parsed)
        self.assertEqual(
            parsed["next_actions"],
            ["generate fixture set", "validate synthetic drift"],
        )
        self.assertEqual(
            parsed["linked_literature"],
            ["DEMO0001", "DEMO0002"],
        )
        self.assertEqual(parsed["updated"], "2099-01-15")

    def test_unknown_composite_extensions_are_allowed_conservatively(self):
        base = (
            b"---\n"
            b"schema_version: project/1.0\n"
            b"project_id: catalyst-screening\n"
            b"title: Valid title\n"
            b"status: planned\n"
            b"{}"
            b"---\n"
        )
        extensions = (
            b"key_metrics:\n  synthetic_score: 0.8\n  synthetic_error: 12.4\n",
            b"custom_tags:\n  - synthetic\n  - demo\n",
            b"custom_summary: |\n  First line\n  Second # literal line\n",
        )
        for extension in extensions:
            with self.subTest(extension=extension):
                self.assertIsNotNone(
                    parse_project_frontmatter(base.replace(b"{}", extension))
                )

        known_orphan = base.replace(
            b"{}", b"stage: exploration\n  orphan: value\n"
        )
        unknown_scalar_orphan = base.replace(
            b"{}", b"extension: scalar\n  orphan: value\n"
        )
        self.assertIsNone(parse_project_frontmatter(known_orphan))
        self.assertIsNone(parse_project_frontmatter(unknown_scalar_orphan))

    def test_flow_string_arrays_respect_quotes_and_reject_non_strings(self):
        base = (
            b"---\n"
            b"schema_version: project/1.0\n"
            b"project_id: catalyst-screening\n"
            b"title: Valid title\n"
            b"status: planned\n"
            b"{}"
            b"---\n"
        )
        valid_cases = (
            (
                b'next_actions: [plain item, "quoted, comma", \'single, comma\']\n',
                ["plain item", "quoted, comma", "single, comma"],
            ),
            (
                'linked_literature: [中文条目, C:/ScriptoriumDemo/literature, "escaped \\\"quote\\\""]\n'.encode(
                    "utf-8"
                ),
                ["中文条目", "C:/ScriptoriumDemo/literature", 'escaped "quote"'],
            ),
        )
        for line, expected in valid_cases:
            with self.subTest(line=line):
                parsed = parse_project_frontmatter(base.replace(b"{}", line))
                key = line.split(b":", 1)[0].decode("ascii")
                self.assertEqual(parsed[key], expected)

        invalid_lines = (
            b"next_actions: [one,, two]\n",
            b"next_actions: [one,]\n",
            b"next_actions: [null]\n",
            b"next_actions: [42]\n",
            b"next_actions: [[nested]]\n",
            b"next_actions: [{nested: value}]\n",
            b'next_actions: ["unterminated]\n',
        )
        for line in invalid_lines:
            with self.subTest(line=line):
                self.assertIsNone(
                    parse_project_frontmatter(base.replace(b"{}", line))
                )

    def test_invalid_required_frontmatter_scalar_is_a_no_write_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            projects = arguments["workspace"] / "Projects"
            projects.mkdir(parents=True)
            note = projects / "catalyst-screening.md"
            original = (
                b"---\n"
                b"schema_version: project/1.0\n"
                b"project_id: catalyst-screening\n"
                b"title: 42\n"
                b"status: planned\n"
                b"---\nHuman\n"
            )
            note.write_bytes(original)

            report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(note.read_bytes(), original)
            self.assertFalse(resolve_config_path(arguments["config_dir"]).exists())

    def test_project_identity_change_before_config_publish_removes_new_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            config_path = resolve_config_path(arguments["config_dir"])
            checks = []
            real_assert = init_module._assert_bound_file_current

            def change_on_second_check(parent, leaf, descriptor):
                checks.append(leaf)
                if len(checks) == 2:
                    raise InitError("injected project identity change")
                return real_assert(parent, leaf, descriptor)

            with mock.patch.object(
                init_module,
                "_assert_bound_file_current",
                side_effect=change_on_second_check,
            ):
                report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["conflict"]["code"], "project-identity-conflict")
            self.assertFalse(config_path.exists())
            self.assertTrue(
                (arguments["workspace"] / "Projects" / "catalyst-screening.md").is_file()
            )

    def test_project_identity_change_after_config_publish_withdraws_new_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            config_path = resolve_config_path(arguments["config_dir"])
            checks = []
            real_assert = init_module._assert_bound_file_current

            def change_on_third_check(parent, leaf, descriptor):
                checks.append(leaf)
                if len(checks) == 3:
                    raise InitError("injected post-publish project identity change")
                return real_assert(parent, leaf, descriptor)

            with mock.patch.object(
                init_module,
                "_assert_bound_file_current",
                side_effect=change_on_third_check,
            ):
                report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["conflict"]["code"], "project-identity-conflict")
            self.assertFalse(config_path.exists())

    def test_posix_failed_config_withdrawal_is_not_silently_downgraded(self):
        created = mock.Mock(st_mode=stat.S_IFREG | 0o644, st_dev=1, st_ino=2)
        linkat = mock.Mock(return_value=0)
        libc = mock.Mock(linkat=linkat)
        callback = mock.Mock(side_effect=ValueError("project changed"))
        with (
            mock.patch.object(init_module.os, "name", "posix"),
            mock.patch.object(init_module.os, "open", return_value=77),
            mock.patch.object(init_module.os, "fstat", return_value=created),
            mock.patch.object(init_module.os, "stat", return_value=created),
            mock.patch.object(init_module.os, "unlink", side_effect=OSError("busy")),
            mock.patch.object(init_module.os, "fsync"),
            mock.patch.object(init_module.os, "close"),
            mock.patch.object(init_module, "_write_all"),
            mock.patch.object(ctypes, "CDLL", return_value=libc),
            mock.patch.object(ctypes, "get_errno", return_value=0),
        ):
            with self.assertRaisesRegex(
                InitError, "cannot withdraw invalid config discovery marker"
            ):
                init_module._create_bound_file(
                    10,
                    "config.toml",
                    b"config",
                    root="config",
                    relative="scriptorium/config.toml",
                    after_publish=callback,
                )

    def test_posix_rollback_never_unlinks_a_foreign_final_inode(self):
        created = mock.Mock(st_mode=stat.S_IFREG | 0o644, st_dev=1, st_ino=2)
        foreign = mock.Mock(st_mode=stat.S_IFREG | 0o644, st_dev=1, st_ino=3)
        linkat = mock.Mock(return_value=0)
        libc = mock.Mock(linkat=linkat)
        unlink = mock.Mock()
        with (
            mock.patch.object(init_module.os, "name", "posix"),
            mock.patch.object(init_module.os, "open", return_value=77),
            mock.patch.object(init_module.os, "fstat", return_value=created),
            mock.patch.object(
                init_module.os, "stat", side_effect=[created, foreign, foreign]
            ),
            mock.patch.object(init_module.os, "unlink", unlink),
            mock.patch.object(init_module.os, "fsync"),
            mock.patch.object(init_module.os, "close"),
            mock.patch.object(init_module, "_write_all"),
            mock.patch.object(ctypes, "CDLL", return_value=libc),
            mock.patch.object(ctypes, "get_errno", return_value=0),
        ):
            with self.assertRaisesRegex(
                InitError, "cannot withdraw invalid config discovery marker"
            ):
                init_module._create_bound_file(
                    10,
                    "config.toml",
                    b"config",
                    root="config",
                    relative="scriptorium/config.toml",
                    after_publish=mock.Mock(side_effect=ValueError("changed")),
                )
        unlink.assert_not_called()

    def test_posix_success_fsyncs_directory_after_temp_cleanup(self):
        created = mock.Mock(st_mode=stat.S_IFREG | 0o644, st_dev=1, st_ino=2)
        linkat = mock.Mock(return_value=0)
        libc = mock.Mock(linkat=linkat)
        fsync = mock.Mock()
        with (
            mock.patch.object(init_module.os, "name", "posix"),
            mock.patch.object(init_module.os, "open", return_value=77),
            mock.patch.object(init_module.os, "fstat", return_value=created),
            mock.patch.object(init_module.os, "stat", return_value=created),
            mock.patch.object(init_module.os, "unlink"),
            mock.patch.object(init_module.os, "fsync", fsync),
            mock.patch.object(init_module.os, "close"),
            mock.patch.object(init_module, "_write_all"),
            mock.patch.object(ctypes, "CDLL", return_value=libc),
            mock.patch.object(ctypes, "get_errno", return_value=0),
        ):
            init_module._create_bound_file(
                10,
                "config.toml",
                b"config",
                root="config",
                relative="scriptorium/config.toml",
            )

        self.assertEqual(
            fsync.call_args_list,
            [mock.call(77), mock.call(10), mock.call(10)],
        )

    @unittest.skipUnless(os.name == "nt", "Windows handle cleanup")
    def test_windows_temp_cleanup_failure_is_not_silently_ignored(self):
        with (
            mock.patch.object(init_module, "_nt_open_relative", return_value=42),
            mock.patch.object(
                init_module,
                "_write_windows_handle",
                side_effect=InitError("injected write failure"),
            ),
            mock.patch.object(
                init_module, "_nt_delete_by_handle", return_value=-1
            ) as delete,
            mock.patch.object(init_module, "_win_close"),
        ):
            with self.assertRaisesRegex(
                InitError, "cannot clean temporary managed file"
            ):
                init_module._create_bound_file(
                    10,
                    "config.toml",
                    b"config",
                    root="config",
                    relative="scriptorium/config.toml",
                )

        delete.assert_called_once_with(42)

    @unittest.skipUnless(os.name == "nt", "Windows handle policy")
    def test_windows_directory_handles_and_relative_files_deny_delete_sharing(self):
        with tempfile.TemporaryDirectory() as temporary:
            arguments = self.paths(Path(temporary))
            calls = []
            publishes = []
            real_open = init_module._nt_open_relative
            real_publish = init_module._nt_publish_no_replace

            def record_open(parent, leaf, **kwargs):
                calls.append((parent, leaf, dict(kwargs)))
                return real_open(parent, leaf, **kwargs)

            def record_publish(handle, parent, leaf):
                publishes.append((handle, parent, leaf))
                return real_publish(handle, parent, leaf)

            with (
                mock.patch.object(
                    init_module, "_nt_open_relative", side_effect=record_open
                ),
                mock.patch.object(
                    init_module, "_nt_publish_no_replace", side_effect=record_publish
                ),
            ):
                report = run_init(**arguments, run=True)

            self.assertEqual(report["status"], "initialized")
            self.assertFalse(init_module._WINDOWS_DIRECTORY_SHARE & 0x00000004)
            directory_calls = [call for call in calls if call[2]["directory"]]
            self.assertTrue(directory_calls)
            for parent, leaf, kwargs in directory_calls:
                self.assertIsInstance(parent, int)
                self.assertTrue(leaf)
                self.assertEqual(
                    kwargs["share"], init_module._WINDOWS_DIRECTORY_SHARE
                )
                self.assertFalse(kwargs["share"] & 0x00000004)
            file_calls = [call for call in calls if not call[2]["directory"]]
            create_file_calls = [call for call in file_calls if call[2]["create"]]
            self.assertEqual(len(create_file_calls), 2)
            self.assertTrue(
                all(
                    call[1].startswith(".scriptorium-init-")
                    for call in create_file_calls
                )
            )
            self.assertTrue(all(isinstance(call[0], int) for call in file_calls))
            self.assertEqual(
                [call[2] for call in publishes],
                ["catalyst-screening.md", "config.toml"],
            )
            self.assertTrue(all(isinstance(call[1], int) for call in publishes))

    @unittest.skipUnless(os.name == "nt", "Windows rename protection")
    def test_windows_held_workspace_handle_blocks_ordinary_directory_swap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            moved = root / "moved-workspace"
            blocked = []
            real_create = init_module._create_bound_file

            def attempt_swap(parent, leaf, payload, **kwargs):
                if kwargs["root"] == "workspace":
                    try:
                        arguments["workspace"].rename(moved)
                    except OSError:
                        blocked.append(True)
                    else:
                        self.fail("workspace rename bypassed held no-delete handles")
                return real_create(parent, leaf, payload, **kwargs)

            with mock.patch.object(
                init_module, "_create_bound_file", side_effect=attempt_swap
            ):
                report = run_init(**arguments, run=True)

            self.assertEqual(blocked, [True])
            self.assertEqual(report["status"], "initialized")
            self.assertFalse(moved.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX directory-fd protection")
    def test_posix_workspace_swap_is_detected_before_config_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.paths(root)
            moved = root / "moved-workspace"
            real_create = init_module._create_bound_file

            def swap_workspace(parent, leaf, payload, **kwargs):
                if kwargs["root"] == "workspace":
                    arguments["workspace"].rename(moved)
                    (arguments["workspace"] / "Projects").mkdir(parents=True)
                return real_create(parent, leaf, payload, **kwargs)

            with mock.patch.object(
                init_module, "_create_bound_file", side_effect=swap_workspace
            ):
                with self.assertRaisesRegex(InitError, "path identity changed"):
                    run_init(**arguments, run=True)

            self.assertTrue(
                (moved / "Projects" / "catalyst-screening.md").is_file()
            )
            self.assertFalse(
                (arguments["workspace"] / "Projects" / "catalyst-screening.md").exists()
            )
            self.assertFalse(resolve_config_path(arguments["config_dir"]).exists())

    def test_format_report_has_no_absolute_change_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = run_init(**self.paths(Path(temporary)))
            output = format_init_report(report)
            self.assertIn("Scriptorium init", output)
            self.assertIn("Result: PLANNED", output)
            self.assertIn("Network: no action requested", output)
            self.assertNotIn(str(Path(temporary)), "\n".join(
                f"{change['root']}:{change['path']}" for change in report["changes"]
            ))


if __name__ == "__main__":
    unittest.main()
