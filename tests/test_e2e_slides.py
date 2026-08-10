from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("e2e_slides.py")
MODULE_SPEC = importlib.util.spec_from_file_location("e2e_slides", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
e2e_slides = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(e2e_slides)


class SyntheticSlidesE2ETest(unittest.TestCase):
    def test_pdf_has_valid_offsets_and_extractable_markers(self) -> None:
        payload = e2e_slides.build_pdf(
            ["SYNTHETIC-PAPER-1-EVIDENCE", r"escaped (value) \ test"]
        )
        self.assertTrue(payload.startswith(b"%PDF-1.4"))
        self.assertTrue(payload.endswith(b"%%EOF\n"))
        self.assertIn(b"SYNTHETIC-PAPER-1-EVIDENCE", payload)
        xref = payload.index(b"xref\n")
        lines = payload[xref:].splitlines()
        self.assertEqual(lines[1], b"0 6")
        for object_number, row in enumerate(lines[3:8], start=1):
            offset = int(row[:10])
            self.assertTrue(
                payload[offset:].startswith(f"{object_number} 0 obj\n".encode("ascii"))
            )

    def test_isolated_environment_drops_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with patch.dict(
                os.environ,
                {
                    "PATH": os.environ.get("PATH", ""),
                    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                    "OPENAI_API_KEY": "sk-not-allowed-in-child",
                    "ANTHROPIC_API_KEY": "also-not-allowed",
                    "GITHUB_TOKEN": "ghp_not_allowed_in_child",
                },
                clear=True,
            ):
                env = e2e_slides.isolated_environment(Path(raw))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["ASA_PDF_PARSER"], "pdfplumber")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_privacy_scan_accepts_synthetic_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw) / "meta.json"
            artifact.write_text(
                '{"schema_version":"handoff/1.1","title":"[SYNTHETIC] Safe"}',
                encoding="utf-8",
            )
            e2e_slides.assert_public_artifacts_safe(
                [artifact], forbidden_literals=["C:/Users/private"]
            )

    def test_privacy_scan_rejects_paths_email_and_secrets(self) -> None:
        unsafe_values = (
            "C:/Users/private/research.pdf",
            "researcher@example.org",
            "sk-0123456789ABCDEFGHIJ",
            '"pdfPaths":["synthetic.pdf"]',
        )
        for index, value in enumerate(unsafe_values):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as raw:
                    artifact = Path(raw) / f"unsafe-{index}.txt"
                    artifact.write_text(value, encoding="utf-8")
                    with self.assertRaises(e2e_slides.E2EFailure):
                        e2e_slides.assert_public_artifacts_safe(
                            [artifact],
                            forbidden_literals=["C:/Users/private"],
                        )


if __name__ == "__main__":
    unittest.main()
