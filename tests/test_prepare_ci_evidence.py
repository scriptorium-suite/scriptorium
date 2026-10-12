import importlib.util
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "tools" / "prepare_ci_evidence.py"
SPEC = importlib.util.spec_from_file_location("prepare_ci_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)
ASSETS = Path(__file__).parents[1] / "src" / "scriptorium" / "assets"


def _write_demo(root: Path) -> None:
    reviewed_runtime = {
        binding["runtime"]: name
        for name, binding in EVIDENCE.REVIEWED_FIXTURES.items()
    }
    special = {
        "demo-report.json",
        "workspace/Reports/context-capsule.json",
        "workspace/Reports/provenance-search.txt",
        "provenance/search-index.db",
        *reviewed_runtime,
    }
    for relative_text in EVIDENCE.DEMO_ARTIFACTS:
        if relative_text in special:
            continue
        path = root / relative_text
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            path.write_text('{"synthetic": true}\n', encoding="utf-8")
        elif path.suffix == ".jsonl":
            path.write_text('{"synthetic": true}\n', encoding="utf-8")
        else:
            path.write_text("Synthetic evidence\n", encoding="utf-8")

    for runtime, name in reviewed_runtime.items():
        target = root / runtime
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ASSETS / name, target)

    context = {
        "capsule_version": "context-capsule/0.1",
        "project": {
            "project_id": "synthetic-catalyst-discovery",
            "title": "Synthetic Catalyst Discovery",
            "status": "active",
            "stage": "literature review",
        },
        "next_actions": ["Synthetic action one", "Synthetic action two"],
        "blocked_by": "",
        "recent_progress": [],
        "literature": [
            {"id": "DEMO0001", "citekey": "chen2022PhysicsInformed"},
            {"id": "DEMO0002", "citekey": "liu2024ActiveLearning"},
        ],
        "research_artifacts": [
            {
                "kind": "lineage-graph",
                "schema_version": "lineage-graph/1.0",
                "id": "lineage-graph:3e9acb9d381f6cc8e5656cb2",
                "trust": "reference_only",
                "source_ids": [
                    "chen2022PhysicsInformed",
                    "liu2024ActiveLearning",
                ],
            },
            {
                "kind": "parsed-paper",
                "schema_version": "parsed-paper/1.0",
                "id": "parsed-paper:99a28055d54f97d091186c8f",
                "trust": "reference_only",
                "source_ids": ["liu2024ActiveLearning"],
            },
            {
                "kind": "reading-note",
                "schema_version": "reading-note/1.0",
                "id": "reading-note:99a28055d54f97d091186c8f",
                "trust": "reference_only",
                "source_ids": ["liu2024ActiveLearning"],
            },
            {
                "kind": "review",
                "schema_version": "review/1.0",
                "id": "review:17e460b9e69804efd461ed41",
                "trust": "reference_only",
                "source_ids": [
                    "chen2022PhysicsInformed",
                    "liu2024ActiveLearning",
                ],
            },
        ],
        "reference_leads": {
            "gaps": ["[SYNTHETIC] Generated evidence gap."],
            "priority_reads": ["liu2024ActiveLearning"],
        },
        "trust": {
            "project_state": "human_or_approved",
            "recent_progress": "auto_applied_low_risk_not_approved_claims",
            "research_artifacts": "reference_only_not_approved_claims",
        },
        "limits": {"max_markdown_chars": 8000, "truncated": False},
    }
    context_path = root / "workspace" / "Reports" / "context-capsule.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(context), encoding="utf-8")
    search_path = root / "workspace" / "Reports" / "provenance-search.txt"
    search_path.write_text(
        "3 hits for: calibration\n"
        f"id={EVIDENCE.EXPECTED_SEARCH_IDS[0]}\n"
        f"id={EVIDENCE.EXPECTED_SEARCH_IDS[1]}\n"
        f"id={EVIDENCE.EXPECTED_SEARCH_IDS[2]}\n",
        encoding="utf-8",
    )

    database = root / "provenance" / "search-index.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE conv USING fts5("
            "conversation_id, source, date, category, title, body)"
        )
        connection.execute("CREATE TABLE idxmeta(k TEXT, v TEXT)")
        connection.execute(
            "INSERT INTO idxmeta VALUES (?, ?)",
            ("tokenizer", "unicode61"),
        )
        rows = []
        expected_hits = frozenset(EVIDENCE.EXPECTED_SEARCH_IDS)
        for record_id, source, category in EVIDENCE.EXPECTED_SEARCH_RECORDS:
            body = (
                "[SYNTHETIC] calibration"
                if record_id in expected_hits
                else "[SYNTHETIC] unrelated fixture"
            )
            rows.append(
                (
                    record_id,
                    source,
                    "2100-01-01",
                    category,
                    f"[SYNTHETIC] {record_id}",
                    body,
                )
            )
        connection.executemany(
            "INSERT INTO conv VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()

    report = {
        "format_version": 1,
        "generated_by": {
            "name": "scriptorium",
            "version": EVIDENCE.SCRIPTORIUM_VERSION,
        },
        "demo_status": "passed",
        "components": EVIDENCE.DEMO_COMPONENTS,
        "stages": [
            {"name": name, "exit_code": 0}
            for name in EVIDENCE.DEMO_STAGE_NAMES
        ],
        "assertions": {
            name: True for name in EVIDENCE.DEMO_ASSERTION_KEYS
        },
        "artifacts": list(EVIDENCE.DEMO_ARTIFACTS),
        "expected_artifacts": list(EVIDENCE.DEMO_ARTIFACTS),
        "safety": EVIDENCE.DEMO_SAFETY,
        "suite_readiness": EVIDENCE.DEMO_SUITE_READINESS,
    }
    (root / "demo-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )


def _write_doctor(path: Path) -> None:
    checks = [
        {"id": "synthetic.pass", "status": "pass"},
        {"id": "synthetic.info", "status": "info"},
        {"id": "synthetic.manual", "status": "manual"},
    ]
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "generated_by": {
                    "name": "scriptorium",
                    "version": EVIDENCE.SCRIPTORIUM_VERSION,
                },
                "target": "demo",
                "status": "ready",
                "exit_code": 0,
                "readiness": {
                    "demo": "ready",
                    "public_alpha": "incomplete",
                    "literature": "file-only",
                    "slides": "detected",
                    "web_history": "manual",
                },
                "summary": {
                    "pass": 1,
                    "warn": 0,
                    "fail": 0,
                    "info": 1,
                    "manual": 1,
                },
                "egress": [],
                "limitations": [],
                "checks": checks,
                "private_unknown": r"D:\a\scriptorium",
            }
        ),
        encoding="utf-8",
    )


def _write_install(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "operation": "windows-install-lifecycle",
                "status": "passed",
                "exit_code": 0,
                "components": EVIDENCE.INSTALL_COMPONENTS,
                "platform": {
                    "implementation": "cpython",
                    "python": "3.12.10",
                    "windows": True,
                },
                "safety": EVIDENCE.INSTALL_SAFETY,
                "lifecycle": EVIDENCE.INSTALL_LIFECYCLE,
                "stages": [
                    {"name": name, "status": "passed"}
                    for name in EVIDENCE.INSTALL_STAGE_NAMES
                ],
                "private_unknown": "researcher@example.com",
            }
        ),
        encoding="utf-8",
    )


def _attestation() -> dict:
    return EVIDENCE.build_run_attestation(
        repository="scriptorium-suite/scriptorium",
        subject_commit="a" * 40,
        checkout_commit="b" * 40,
        base_commit="c" * 40,
        checkout_parents=("c" * 40, "a" * 40),
        checkout_mode="pull-request-merge",
        run_id=123,
        run_attempt=1,
        event_name="pull_request",
        runner_os="Windows",
        artifact_name="scriptorium-v0.2.0-windows-evidence",
        test_context="windows-e2e",
    )


class DoctorSummaryTests(unittest.TestCase):
    def test_summary_omits_paths_and_detailed_checks(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "doctor.json"
            _write_doctor(path)
            report = json.loads(path.read_text(encoding="utf-8"))

        summary = EVIDENCE.build_doctor_summary(report)
        serialized = json.dumps(summary)

        self.assertNotIn("checks", summary)
        self.assertNotIn("private_unknown", summary)
        self.assertNotIn(r"D:\\a", serialized)
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(summary["check_count"], 3)

    def test_failed_or_inconsistent_doctor_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "doctor.json"
            _write_doctor(path)
            base = json.loads(path.read_text(encoding="utf-8"))
        mutations = (
            ("status", "failed"),
            ("exit_code", 1),
            ("exit_code", False),
            ("format_version", True),
            ("generated_by", {"name": "scriptorium", "version": "9.9.9"}),
            ("summary", {"pass": 99, "warn": 0, "fail": 0, "info": 1, "manual": 1}),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                report = dict(base)
                report[field] = value
                with self.assertRaisesRegex(
                    EVIDENCE.EvidenceError,
                    "^invalid-doctor-evidence$",
                ):
                    EVIDENCE.build_doctor_summary(report)

        coerced_count = json.loads(json.dumps(base))
        coerced_count["summary"]["pass"] = True
        with self.assertRaisesRegex(
            EVIDENCE.EvidenceError,
            "^invalid-doctor-evidence$",
        ):
            EVIDENCE.build_doctor_summary(coerced_count)


class InstallAndDemoSummaryTests(unittest.TestCase):
    def test_install_summary_is_strict_and_allowlisted(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "install.json"
            _write_install(path)
            report = json.loads(path.read_text(encoding="utf-8"))
        summary = EVIDENCE.build_install_lifecycle_summary(report)

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["exit_code"], 0)
        self.assertTrue(summary["stages"]["expected_sequence_verified"])
        self.assertNotIn("private_unknown", json.dumps(summary))

        for field, value in (
            ("status", "failed"),
            ("exit_code", 1),
            ("exit_code", False),
            ("format_version", True),
            ("stages", []),
            ("components", {}),
            ("lifecycle", {}),
        ):
            with self.subTest(field=field):
                changed = dict(report)
                changed[field] = value
                with self.assertRaisesRegex(
                    EVIDENCE.EvidenceError,
                    "^invalid-install-lifecycle-evidence$",
                ):
                    EVIDENCE.build_install_lifecycle_summary(changed)

        coerced_safety = json.loads(json.dumps(report))
        coerced_safety["safety"]["temporary_workspace"] = 1
        with self.assertRaisesRegex(
            EVIDENCE.EvidenceError,
            "^invalid-install-lifecycle-evidence$",
        ):
            EVIDENCE.build_install_lifecycle_summary(coerced_safety)

    def test_demo_summary_rejects_failed_or_partial_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            demo = Path(raw) / "demo"
            demo.mkdir()
            _write_demo(demo)
            report = json.loads(
                (demo / "demo-report.json").read_text(encoding="utf-8")
            )
        summary = EVIDENCE.build_demo_summary(report)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["stages"]["zero_exit_count"], 13)
        self.assertEqual(len(summary["assertions"]), 10)

        failed = dict(report)
        failed["demo_status"] = "failed"
        with self.assertRaisesRegex(
            EVIDENCE.EvidenceError,
            "^invalid-demo-evidence$",
        ):
            EVIDENCE.build_demo_summary(failed)

        partial = dict(report)
        partial["assertions"] = dict(report["assertions"])
        partial["assertions"].pop("mcp_context_verified")
        with self.assertRaisesRegex(
            EVIDENCE.EvidenceError,
            "^invalid-demo-evidence$",
        ):
            EVIDENCE.build_demo_summary(partial)

        coercions = []
        format_coercion = json.loads(json.dumps(report))
        format_coercion["format_version"] = True
        coercions.append(format_coercion)
        exit_coercion = json.loads(json.dumps(report))
        exit_coercion["stages"][0]["exit_code"] = False
        coercions.append(exit_coercion)
        assertion_coercion = json.loads(json.dumps(report))
        assertion_coercion["assertions"]["contract_validated"] = 1
        coercions.append(assertion_coercion)
        safety_coercion = json.loads(json.dumps(report))
        safety_coercion["safety"]["synthetic_data"] = 1
        coercions.append(safety_coercion)
        for changed in coercions:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(
                    EVIDENCE.EvidenceError,
                    "^invalid-demo-evidence$",
                ):
                    EVIDENCE.build_demo_summary(changed)

    def test_context_limit_types_are_exact(self):
        with tempfile.TemporaryDirectory() as raw:
            demo = Path(raw) / "demo"
            demo.mkdir()
            _write_demo(demo)
            context = json.loads(
                (demo / "workspace" / "Reports" / "context-capsule.json")
                .read_text(encoding="utf-8")
            )
        for field, value in (
            ("max_markdown_chars", 8000.0),
            ("truncated", 0),
        ):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(context))
                changed["limits"][field] = value
                with self.assertRaisesRegex(
                    EVIDENCE.EvidenceError,
                    "^invalid-demo-evidence$",
                ):
                    EVIDENCE.build_context_summary(changed)


class RunAttestationTests(unittest.TestCase):
    def test_commit_parent_parser_ignores_message_and_signature_continuations(self):
        commit_object = (
            b"tree " + b"0" * 40 + b"\n"
            b"parent " + b"c" * 40 + b"\n"
            b"parent " + b"a" * 40 + b"\n"
            b"author Synthetic <synthetic@example.invalid> 1 +0000\n"
            b"committer Synthetic <synthetic@example.invalid> 1 +0000\n"
            b"gpgsig -----BEGIN PGP SIGNATURE-----\n"
            b" parent " + b"d" * 40 + b"\n"
            b" -----END PGP SIGNATURE-----\n"
            b"\n"
            b"parent " + b"e" * 40 + b" is commit-message text\n"
        )
        self.assertEqual(
            EVIDENCE.parse_git_commit_parents(commit_object),
            ("c" * 40, "a" * 40),
        )

        malformed = commit_object.replace(
            b"parent " + b"c" * 40,
            b"parent not-a-commit",
            1,
        )
        with self.assertRaisesRegex(
            EVIDENCE.EvidenceError,
            "^invalid-checkout-relationship$",
        ):
            EVIDENCE.parse_git_commit_parents(malformed)

    def test_commit_parent_probe_works_at_a_shallow_merge_boundary(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git is unavailable")

        def run(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [git, "-C", str(repository), *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            shallow = root / "shallow"
            subprocess.run(
                [git, "init", "--initial-branch=main", str(source)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            run(source, "config", "user.name", "Synthetic CI")
            run(source, "config", "user.email", "synthetic@example.invalid")
            run(source, "config", "commit.gpgsign", "false")
            (source / "fixture.txt").write_text("base\n", encoding="utf-8")
            run(source, "add", "fixture.txt")
            run(source, "commit", "-m", "base")
            base = run(source, "rev-parse", "HEAD").stdout.decode().strip()

            run(source, "checkout", "-b", "feature")
            (source / "feature.txt").write_text("feature\n", encoding="utf-8")
            run(source, "add", "feature.txt")
            run(source, "commit", "-m", "feature")
            head = run(source, "rev-parse", "HEAD").stdout.decode().strip()
            run(source, "checkout", "main")
            run(source, "merge", "--no-ff", "feature", "-m", "merge")
            merge = run(source, "rev-parse", "HEAD").stdout.decode().strip()

            subprocess.run(
                [git, "clone", "--depth", "1", source.as_uri(), str(shallow)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            shallow_rev_list = (
                run(shallow, "rev-list", "--parents", "-n", "1", "HEAD")
                .stdout.decode()
                .split()
            )
            self.assertEqual(shallow_rev_list, [merge])
            self.assertEqual(
                EVIDENCE.inspect_git_checkout(shallow),
                (merge, (base, head)),
            )

    def test_attestation_records_checkout_base_and_test_context(self):
        attestation = _attestation()

        self.assertEqual(attestation["subject_commit"], "a" * 40)
        self.assertEqual(attestation["checkout"]["commit"], "b" * 40)
        self.assertEqual(attestation["checkout"]["mode"], "pull-request-merge")
        self.assertEqual(
            attestation["checkout"]["relationship"],
            "pull-request-merge-base-first-head-second",
        )
        self.assertIs(attestation["checkout"]["relationship_verified"], True)
        self.assertEqual(attestation["base"]["commit"], "c" * 40)
        self.assertEqual(attestation["test_context"]["name"], "windows-e2e")
        self.assertEqual(attestation["workflow_run"], 123)
        self.assertIn("/actions/runs/123", attestation["workflow_run_url"])

    def test_attestation_rejects_abbreviated_commit_without_echoing_it(self):
        with self.assertRaises(EVIDENCE.EvidenceError) as captured:
            EVIDENCE.build_run_attestation(
                repository="scriptorium-suite/scriptorium",
                subject_commit="private-abbreviated-sha",
                checkout_commit="b" * 40,
                base_commit="c" * 40,
                checkout_parents=(),
                checkout_mode="push",
                run_id=123,
                run_attempt=1,
                event_name="push",
                runner_os="Windows",
                artifact_name="evidence",
                test_context="windows-e2e",
            )

        self.assertEqual(str(captured.exception), "invalid-run-attestation")
        self.assertNotIn("private-abbreviated-sha", str(captured.exception))

    def test_attestation_records_unavailable_zero_before_as_none(self):
        attestation = EVIDENCE.build_run_attestation(
            repository="scriptorium-suite/scriptorium",
            subject_commit="a" * 40,
            checkout_commit="a" * 40,
            base_commit=None,
            checkout_parents=(),
            checkout_mode="push",
            run_id=123,
            run_attempt=1,
            event_name="push",
            runner_os="Windows",
            artifact_name="evidence",
            test_context="windows-e2e",
        )
        self.assertEqual(
            attestation["base"],
            {"commit": None, "available": False},
        )
        self.assertEqual(
            attestation["checkout"]["relationship"],
            "subject-commit-exact",
        )
        dispatch = EVIDENCE.build_run_attestation(
            repository="scriptorium-suite/scriptorium",
            subject_commit="d" * 40,
            checkout_commit="d" * 40,
            base_commit=None,
            checkout_parents=(),
            checkout_mode="workflow-dispatch",
            run_id=123,
            run_attempt=1,
            event_name="workflow_dispatch",
            runner_os="Windows",
            artifact_name="evidence",
            test_context="windows-e2e",
        )
        self.assertEqual(
            dispatch["checkout"]["relationship"],
            "subject-commit-exact",
        )

    def test_attestation_rejects_wrong_checkout_relationships(self):
        cases = (
            {
                "subject_commit": "a" * 40,
                "checkout_commit": "b" * 40,
                "base_commit": "c" * 40,
                "checkout_parents": ("a" * 40, "c" * 40),
                "checkout_mode": "pull-request-merge",
                "event_name": "pull_request",
            },
            {
                "subject_commit": "a" * 40,
                "checkout_commit": "b" * 40,
                "base_commit": "c" * 40,
                "checkout_parents": ("c" * 40,),
                "checkout_mode": "pull-request-merge",
                "event_name": "pull_request",
            },
            {
                "subject_commit": "a" * 40,
                "checkout_commit": "b" * 40,
                "base_commit": "c" * 40,
                "checkout_parents": (
                    "c" * 40,
                    "a" * 40,
                    "d" * 40,
                ),
                "checkout_mode": "pull-request-merge",
                "event_name": "pull_request",
            },
            {
                "subject_commit": "a" * 40,
                "checkout_commit": "b" * 40,
                "base_commit": None,
                "checkout_parents": (),
                "checkout_mode": "push",
                "event_name": "push",
            },
            {
                "subject_commit": "a" * 40,
                "checkout_commit": "b" * 40,
                "base_commit": None,
                "checkout_parents": (),
                "checkout_mode": "workflow-dispatch",
                "event_name": "workflow_dispatch",
            },
        )
        for case in cases:
            with self.subTest(event_name=case["event_name"]):
                with self.assertRaisesRegex(
                    EVIDENCE.EvidenceError,
                    "^invalid-run-attestation$",
                ):
                    EVIDENCE.build_run_attestation(
                        repository="scriptorium-suite/scriptorium",
                        run_id=123,
                        run_attempt=1,
                        runner_os="Windows",
                        artifact_name="evidence",
                        test_context="windows-e2e",
                        **case,
                    )

    def test_attestation_rejects_boolean_run_counts(self):
        with self.assertRaisesRegex(
            EVIDENCE.EvidenceError,
            "^invalid-run-attestation$",
        ):
            EVIDENCE.build_run_attestation(
                repository="scriptorium-suite/scriptorium",
                subject_commit="a" * 40,
                checkout_commit="a" * 40,
                base_commit=None,
                checkout_parents=(),
                checkout_mode="push",
                run_id=True,
                run_attempt=1,
                event_name="push",
                runner_os="Windows",
                artifact_name="evidence",
                test_context="windows-e2e",
            )


class PrivacyScanTests(unittest.TestCase):
    def test_scan_accepts_synthetic_text_json_and_jsonl(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "report.json").write_text(
                '{"synthetic": true, "source": "DEMO0001"}\n',
                encoding="utf-8",
            )
            (root / "events.jsonl").write_text(
                '{"event": {"status": "passed"}}\n',
                encoding="utf-8",
            )
            (root / "README.md").write_text("Synthetic evidence\n", encoding="utf-8")

            self.assertEqual(EVIDENCE.scan_public_evidence([root]), 3)

    def test_scan_rejects_sensitive_values_with_content_independent_error(self):
        cases = {
            "email": "researcher@example.com",
            "unicode_email": "研究者@例子.测试",
            "fullwidth_email": "researcher＠example.com",
            "path": r"C:\Users\Researcher\private.md",
            "unc": r"\\server\private\paper.md",
            "posix": "/tmp/runner/private.md",
            "root_path": "/root/private.md",
            "workspace_path": "/workspace/private.md",
            "mount_path": "/mnt/c/private.md",
            "classic_token": "ghp_abcdefghijklmnopqrstuvwxyz1234",
            "fine_grained_token": "github_pat_abcdefghijklmnopqrstuvwxyz_123456",
            "url_credential": "https://reader:secret@example.invalid/simple",
            "sentinel": "PRIVATE_RESEARCH_SENTINEL",
        }
        for label, value in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / "public-report.txt"
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(EVIDENCE.EvidenceError) as captured:
                    EVIDENCE.scan_public_evidence([path])
                self.assertEqual(
                    str(captured.exception),
                    "sensitive-content-detected",
                )
                self.assertNotIn(value, str(captured.exception))
                self.assertNotIn(path.name, str(captured.exception))

    def test_scan_decodes_nested_json_and_jsonl_before_scanning(self):
        cases = {
            "nested.json": (
                '{"outer":{"items":[{"value":"researcher\\u0040example.com"}]}}\n'
            ),
            "nested.jsonl": (
                '{"outer":{"items":[{"value":"github\\u005fpat'
                '_abcdefghijklmnopqrstuvwxyz_123456"}]}}\n'
            ),
        }
        for name, payload in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / name
                path.write_text(payload, encoding="utf-8")
                with self.assertRaisesRegex(
                    EVIDENCE.EvidenceError,
                    "^sensitive-content-detected$",
                ):
                    EVIDENCE.scan_public_evidence([path])

    def test_scan_rejects_sqlite_and_other_binary_files(self):
        for name in ("search-index.db", "archive.zip"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / name
                path.write_bytes(b"SQLite format 3\x00" if name.endswith(".db") else b"PK")
                with self.assertRaisesRegex(
                    EVIDENCE.EvidenceError,
                    "^unsupported-evidence-format$",
                ):
                    EVIDENCE.scan_public_evidence([path])

    def test_scan_rejects_hidden_path_component(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            hidden = root / "visible" / ".private"
            hidden.mkdir(parents=True)
            (hidden / "report.json").write_text(
                '{"synthetic": true}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^hidden-evidence-path$",
            ):
                EVIDENCE.scan_public_evidence([root])

    def test_scan_rejects_sensitive_directory_name_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sensitive = root / "researcher@example.com"
            sensitive.mkdir()
            (sensitive / "report.json").write_text(
                '{"synthetic": true}\n',
                encoding="utf-8",
            )

            with self.assertRaises(EVIDENCE.EvidenceError) as captured:
                EVIDENCE.scan_public_evidence([root])

            self.assertEqual(str(captured.exception), "sensitive-content-detected")
            self.assertNotIn(sensitive.name, str(captured.exception))

    def test_scan_rejects_linklike_entries_without_echoing_names(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "private-table.json"
            path.write_text('{"synthetic": true}\n', encoding="utf-8")

            with mock.patch.object(EVIDENCE, "_is_linklike", return_value=True):
                with self.assertRaises(EVIDENCE.EvidenceError) as captured:
                    EVIDENCE.scan_public_evidence([path])

            self.assertEqual(str(captured.exception), "unsafe-path-boundary")
            self.assertNotIn(path.name, str(captured.exception))
            self.assertNotIn("table", str(captured.exception))

    def test_allowlisted_file_rejects_multiple_hard_links(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "report.json"
            alias = root / "alias.json"
            source.write_text('{"synthetic": true}\n', encoding="utf-8")
            try:
                alias.hardlink_to(source)
            except OSError as exc:
                self.skipTest(
                    f"hard-link creation is unavailable: {exc.__class__.__name__}"
                )

            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^unsafe-path-boundary$",
            ):
                EVIDENCE.scan_public_evidence([source])

    def test_path_chain_rejects_symlink_ancestor(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.mkdir()
            linked = root / "linked"
            try:
                linked.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc.__class__.__name__}")
            report = linked / "report.json"
            report.write_text('{"synthetic": true}\n', encoding="utf-8")

            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^unsafe-path-boundary$",
            ):
                EVIDENCE.scan_public_evidence([report])

    def test_windows_reparse_attribute_is_linklike(self):
        fake_path = mock.Mock()
        fake_path.is_symlink.return_value = False
        fake_path.stat.return_value = SimpleNamespace(st_file_attributes=0x400)

        with mock.patch.object(
            EVIDENCE.stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
            create=True,
        ):
            self.assertTrue(EVIDENCE._is_linklike(fake_path))


class PrepareEvidenceTests(unittest.TestCase):
    def test_prepare_publishes_only_summaries_and_reviewed_fixtures(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doctor = root / "doctor.json"
            demo = root / "demo"
            install = root / "install.json"
            output = root / "output"
            demo.mkdir()
            _write_doctor(doctor)
            _write_demo(demo)
            _write_install(install)
            with closing(
                sqlite3.connect(demo / "provenance" / "search-index.db")
            ) as connection:
                connection.execute(
                    "UPDATE conv SET body = body || ? "
                    "WHERE conversation_id = ?",
                    (" researcher@example.com", "lit:DEMO0001"),
                )
                connection.commit()
            (demo / "private-unlisted.txt").write_text(
                "researcher@example.com\n",
                encoding="utf-8",
            )

            count = EVIDENCE.prepare_evidence(
                doctor_report=doctor,
                demo_root=demo,
                install_report=install,
                fixture_root=ASSETS,
                output_dir=output,
                attestation=_attestation(),
            )

            payload_paths = {
                "context-summary.json",
                "demo-summary.json",
                "doctor-summary.json",
                "install-lifecycle-summary.json",
                "search-index-summary.json",
                *{
                    f"fixtures/{name}"
                    for name in EVIDENCE.REVIEWED_FIXTURES
                },
            }
            self.assertEqual(count, len(payload_paths) + 1)
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                [
                    "context-summary.json",
                    "demo-summary.json",
                    "doctor-summary.json",
                    "fixtures",
                    "install-lifecycle-summary.json",
                    "run-attestation.json",
                    "search-index-summary.json",
                ],
            )
            self.assertFalse(any(output.rglob("*.db")))
            self.assertFalse((output / "private-unlisted.txt").exists())
            serialized = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("researcher@example.com", serialized)
            self.assertNotIn(r"D:\a\scriptorium", serialized)

            search_summary = json.loads(
                (output / "search-index-summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(search_summary["sqlite_quick_check"], "ok")
            self.assertEqual(search_summary["sqlite_integrity_check"], "ok")
            self.assertFalse(search_summary["source_artifact_shared"])

            for name, binding in EVIDENCE.REVIEWED_FIXTURES.items():
                self.assertEqual(
                    EVIDENCE._sha256(output / "fixtures" / name),
                    binding["sha256"],
                )

            stored_attestation = json.loads(
                (output / "run-attestation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                stored_attestation["payload_file_count"],
                len(payload_paths),
            )
            self.assertEqual(
                {item["path"] for item in stored_attestation["payload_files"]},
                payload_paths,
            )
            EVIDENCE.verify_payload_file_manifest(
                output,
                stored_attestation["payload_files"],
            )
            staged_files = {
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file()
            }
            visible_files = {
                relative
                for relative in staged_files
                if not any(part.startswith(".") for part in Path(relative).parts)
            }
            self.assertEqual(staged_files, visible_files)
            self.assertEqual(
                staged_files - {"run-attestation.json"},
                payload_paths,
            )

            target = output / "fixtures" / "synthetic-project.md"
            target.write_text("Changed synthetic note\n", encoding="utf-8")
            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^payload-manifest-mismatch$",
            ):
                EVIDENCE.verify_payload_file_manifest(
                    output,
                    stored_attestation["payload_files"],
                )

    def test_reviewed_fixture_hash_and_runtime_binding_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            reviewed = root / "reviewed"
            demo = root / "demo"
            reviewed.mkdir()
            demo.mkdir()
            for name in EVIDENCE.REVIEWED_FIXTURES:
                shutil.copyfile(ASSETS / name, reviewed / name)
            _write_demo(demo)

            (reviewed / "review.v1.json").write_text(
                '{"synthetic": true}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^invalid-reviewed-fixture$",
            ):
                EVIDENCE.copy_reviewed_fixtures(
                    fixture_root=reviewed,
                    demo_root=demo,
                    destination=root / "output-one",
                )

            shutil.copyfile(ASSETS / "review.v1.json", reviewed / "review.v1.json")
            runtime = demo / EVIDENCE.REVIEWED_FIXTURES["review.v1.json"]["runtime"]
            runtime.write_text('{"synthetic": true}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^invalid-reviewed-fixture$",
            ):
                EVIDENCE.copy_reviewed_fixtures(
                    fixture_root=reviewed,
                    demo_root=demo,
                    destination=root / "output-two",
                )

    def test_search_result_and_sqlite_integrity_are_cross_checked(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            demo = root / "demo"
            demo.mkdir()
            _write_demo(demo)
            database = demo / "provenance" / "search-index.db"
            report = EVIDENCE.build_demo_summary(
                EVIDENCE.load_json(demo / "demo-report.json")
            )
            context = EVIDENCE.build_context_summary(
                EVIDENCE.load_json(
                    demo / "workspace" / "Reports" / "context-capsule.json"
                )
            )
            search = demo / "workspace" / "Reports" / "provenance-search.txt"
            search.write_text(
                "3 hits for: calibration\nid=lit:WRONG\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^invalid-demo-evidence$",
            ):
                EVIDENCE.build_search_index_summary(
                    database=demo / "provenance" / "search-index.db",
                    search_report=search,
                    demo_summary=report,
                    context_summary=context,
                )

            search.write_text(
                "3 hits for: calibration\n"
                f"id={EVIDENCE.EXPECTED_SEARCH_IDS[0]}\n"
                f"id={EVIDENCE.EXPECTED_SEARCH_IDS[1]}\n"
                f"id={EVIDENCE.EXPECTED_SEARCH_IDS[2]}\n",
                encoding="utf-8",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "DELETE FROM conv WHERE conversation_id = ?",
                    (EVIDENCE.EXPECTED_SEARCH_RECORDS[-1][0],),
                )
                connection.commit()
            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^invalid-demo-evidence$",
            ):
                EVIDENCE.build_search_index_summary(
                    database=database,
                    search_report=search,
                    demo_summary=report,
                    context_summary=context,
                )

            database.unlink()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE notes (body TEXT)")
                connection.execute(
                    "INSERT INTO notes VALUES (?)",
                    ("[SYNTHETIC] healthy but unrelated",),
                )
                connection.commit()
            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^invalid-demo-evidence$",
            ):
                EVIDENCE.build_search_index_summary(
                    database=database,
                    search_report=search,
                    demo_summary=report,
                    context_summary=context,
                )

            database.write_bytes(b"not-a-sqlite-database" * 2)
            with self.assertRaisesRegex(
                EVIDENCE.EvidenceError,
                "^invalid-demo-evidence$",
            ):
                EVIDENCE.build_search_index_summary(
                    database=database,
                    search_report=search,
                    demo_summary=report,
                    context_summary=context,
                )

    def test_prepare_checks_every_input_and_output_path_chain(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doctor = root / "doctor.json"
            demo = root / "demo"
            install = root / "install.json"
            output = root / "output"
            demo.mkdir()
            _write_doctor(doctor)
            _write_demo(demo)
            _write_install(install)

            real_check = EVIDENCE._assert_path_chain_safe
            with mock.patch.object(
                EVIDENCE,
                "_assert_path_chain_safe",
                wraps=real_check,
            ) as checked:
                EVIDENCE.prepare_evidence(
                    doctor_report=doctor,
                    demo_root=demo,
                    install_report=install,
                    fixture_root=ASSETS,
                    output_dir=output,
                    attestation=_attestation(),
                )

            checked_paths = {call.args[0] for call in checked.call_args_list}
            self.assertTrue(
                {doctor, demo, install, ASSETS, output}.issubset(checked_paths)
            )


if __name__ == "__main__":
    unittest.main()
