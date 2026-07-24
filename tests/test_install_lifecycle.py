import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("e2e_install_lifecycle.py")
SPEC = importlib.util.spec_from_file_location("e2e_install_lifecycle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LIFECYCLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIFECYCLE)


class IdentityTests(unittest.TestCase):
    def test_reads_expected_identity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "pyproject.toml").write_text(
                '[project]\nname="scriptorium-suite"\nversion="0.4.0"\n',
                encoding="utf-8",
            )

            self.assertEqual(
                LIFECYCLE.project_identity(root, "scriptorium-suite"),
                {"name": "scriptorium-suite", "version": "0.4.0"},
            )

    def test_rejects_wrong_distribution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "pyproject.toml").write_text(
                '[project]\nname="other"\nversion="0.4.0"\n',
                encoding="utf-8",
            )

            with self.assertRaises(LIFECYCLE.LifecycleFailure):
                LIFECYCLE.project_identity(root, "scriptorium-suite")


class IsolationTests(unittest.TestCase):
    def test_environment_drops_provider_and_index_credentials(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            scripts = root / "clean-venv" / "Scripts"
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": "system-path",
                    "OPENAI_API_KEY": "private",
                    "PIP_INDEX_URL": "https://user:secret@example.invalid/simple",
                },
                clear=True,
            ):
                env = LIFECYCLE.isolated_environment(root, scripts)

            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("PIP_INDEX_URL", env)
            self.assertEqual(env["PIP_NO_INDEX"], "1")
            self.assertEqual(env["PIP_NO_INPUT"], "1")
            self.assertTrue(env["PATH"].startswith(str(scripts)))

    def test_staging_excludes_unselected_private_directories(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            package = source / "src" / "scriptorium"
            package.mkdir(parents=True)
            (source / "pyproject.toml").write_text(
                '[project]\nname="scriptorium-suite"\nversion="0.4.0"\n',
                encoding="utf-8",
            )
            (source / "README.md").write_text("Public", encoding="utf-8")
            (package / "__init__.py").write_text("__version__='0.4.0'\n", encoding="utf-8")
            private = source / "private-memory"
            private.mkdir()
            (private / "session.jsonl").write_text("private", encoding="utf-8")

            staged = LIFECYCLE.stage_package(
                source,
                root / "staged",
                "scriptorium-suite",
            )

            self.assertTrue((staged / "src" / "scriptorium" / "__init__.py").is_file())
            self.assertFalse((staged / "private-memory").exists())


class TransitionTests(unittest.TestCase):
    def test_only_older_release_pair_is_covered(self):
        self.assertEqual(
            LIFECYCLE.release_relation("0.3.0", "0.4.0"),
            "previous-is-older",
        )
        self.assertEqual(LIFECYCLE.release_relation("0.4.0", "0.4.0"), "same")
        self.assertEqual(
            LIFECYCLE.release_relation("0.5.0", "0.4.0"),
            "previous-is-newer",
        )
        self.assertEqual(LIFECYCLE.release_relation("local", "0.4.0"), "unsupported")


class ReportTests(unittest.TestCase):
    def test_report_is_created_once_and_not_replaced(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "report.json"
            payload = {"status": "passed"}

            LIFECYCLE.write_report(path, payload)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            with self.assertRaises(LIFECYCLE.LifecycleFailure):
                LIFECYCLE.write_report(path, {"status": "changed"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
