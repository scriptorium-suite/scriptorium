import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scriptorium.config import (
    ConfigError,
    SuiteConfig,
    load_config,
    render_config,
    resolve_config_path,
)


VALID_CONFIG = r"""
format_version = 1
workspace = "C:\\research\\workspace"
provenance_home = "D:\\research\\provenance"
hosts = ["claude-code", "codex"]
default_project = "example-project"
"""


class ConfigTests(unittest.TestCase):
    def test_explicit_root_precedes_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            explicit = Path(temporary) / "explicit"
            environment = Path(temporary) / "environment"
            with mock.patch.dict(
                os.environ, {"SCRIPTORIUM_CONFIG_DIR": str(environment)}
            ):
                self.assertEqual(
                    resolve_config_path(explicit),
                    explicit / "scriptorium" / "config.toml",
                )

    def test_environment_root_precedes_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary) / "environment"
            home = Path(temporary) / "home"
            with (
                mock.patch.dict(
                    os.environ, {"SCRIPTORIUM_CONFIG_DIR": str(environment)}
                ),
                mock.patch("scriptorium.config.Path.home", return_value=home),
            ):
                self.assertEqual(
                    resolve_config_path(),
                    environment / "scriptorium" / "config.toml",
                )

    def test_default_root_uses_home_config_family(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("scriptorium.config.Path.home", return_value=home),
            ):
                self.assertEqual(
                    resolve_config_path(),
                    home
                    / ".config"
                    / "scriptorium"
                    / "scriptorium"
                    / "config.toml",
                )

    def test_render_and_load_round_trip_unicode_windows_paths(self):
        config = SuiteConfig(
            workspace=Path(r"C:\研究\工作区"),
            provenance_home=Path(r"D:\资料\Provenance"),
            hosts=("claude-code", "codex"),
            default_project="unicode-project",
        )
        payload = render_config(config)
        self.assertIn("研究".encode(), payload)
        self.assertIn(b'workspace = "C:\\\\', payload)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "scriptorium" / "config.toml"
            path.parent.mkdir()
            path.write_bytes(payload)
            self.assertEqual(load_config(root), config)

    def test_unknown_field_is_rejected_without_echoing_value(self):
        error = self._load_error(VALID_CONFIG + 'api_key = "do-not-print"\n')
        self.assertIn("unknown configuration fields", str(error))
        self.assertNotIn("do-not-print", str(error))

    def test_type_errors_are_rejected(self):
        cases = (
            VALID_CONFIG.replace("format_version = 1", "format_version = true"),
            VALID_CONFIG.replace(
                r'workspace = "C:\\research\\workspace"', "workspace = 42"
            ),
            VALID_CONFIG.replace(
                'hosts = ["claude-code", "codex"]', 'hosts = "codex"'
            ),
            VALID_CONFIG.replace(
                'default_project = "example-project"', "default_project = 42"
            ),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self._load_error(payload)

    def test_relative_workspace_and_provenance_paths_are_rejected(self):
        cases = (
            VALID_CONFIG.replace(
                r'workspace = "C:\\research\\workspace"', 'workspace = "research"'
            ),
            VALID_CONFIG.replace(
                r'provenance_home = "D:\\research\\provenance"',
                'provenance_home = "provenance"',
            ),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self._load_error(payload)

    def test_invalid_hosts_are_rejected(self):
        cases = (
            "hosts = []",
            'hosts = ["cursor"]',
            'hosts = ["codex", "codex"]',
            'hosts = ["codex", "claude-code"]',
        )
        for replacement in cases:
            payload = VALID_CONFIG.replace(
                'hosts = ["claude-code", "codex"]', replacement
            )
            with self.subTest(hosts=replacement):
                self._load_error(payload)

    def test_invalid_project_ids_are_rejected(self):
        for project_id in ("", "Project", "two words", "-leading", "trailing-"):
            payload = VALID_CONFIG.replace(
                'default_project = "example-project"',
                f'default_project = "{project_id}"',
            )
            with self.subTest(project_id=project_id):
                self._load_error(payload)

    def test_missing_config_returns_none_without_creating_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "absent"
            self.assertIsNone(load_config(root))
            self.assertFalse(root.exists())

    def _load_error(self, payload: str) -> ConfigError:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "scriptorium" / "config.toml"
            path.parent.mkdir()
            path.write_text(payload, encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load_config(root)
            return caught.exception


if __name__ == "__main__":
    unittest.main()
