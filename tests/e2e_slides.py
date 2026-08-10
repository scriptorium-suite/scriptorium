#!/usr/bin/env python3
"""Synthetic, offline Steward -> Spec -> Lectern golden-path acceptance.

The script launches each repository through a separate Python process, creates
only synthetic fixtures under a temporary directory, and deletes them at exit.
It never reads a user library, research directory, agent profile, or credential.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable


SCRIPTORIUM_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STEWARD_ROOT = SCRIPTORIUM_ROOT.parent / "steward"
DEFAULT_SPEC_ROOT = SCRIPTORIUM_ROOT.parent / "scriptorium-spec"
DEFAULT_LECTERN_ROOT = SCRIPTORIUM_ROOT.parent / "Academic-Slides-Agent"
LECTERN_WORKER = Path(__file__).with_name("_e2e_lectern_worker.py")
SYSTEM_ENV_NAMES = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TERM",
    "WINDIR",
}
SYNTHETIC_PAPERS = (
    {
        "key": "SYNTHA01",
        "title": "[SYNTHETIC] Offline catalyst benchmark",
        "authors": ["Synthetic Researcher A"],
        "year": "2025",
        "doi": "10.0000/scriptorium-golden-a",
        "tldr": "A synthetic benchmark records catalyst signal A.",
        "abstract": "No real research data is used in this generated fixture.",
        "folders": ["Synthetic/GoldenPath"],
        "marker": "SYNTHETIC-PAPER-1-EVIDENCE",
    },
    {
        "key": "SYNTHB02",
        "title": "[SYNTHETIC] Offline catalyst replication",
        "authors": ["Synthetic Researcher B"],
        "year": "2026",
        "doi": "10.0000/scriptorium-golden-b",
        "tldr": "A synthetic replication records catalyst signal B.",
        "abstract": "The values and narrative are fabricated for software testing.",
        "folders": ["Synthetic/GoldenPath"],
        "marker": "SYNTHETIC-PAPER-2-EVIDENCE",
    },
)
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
SECRET_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16})\b"
)
HOME_PATH_PATTERN = re.compile(
    r"(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^<>\"'\s]+|"
    r"/(?:home|Users)/[^<>\"'\s]+)",
    re.IGNORECASE,
)


class E2EFailure(RuntimeError):
    """A cross-repository acceptance invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise E2EFailure(message)


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(lines: Iterable[str]) -> bytes:
    """Build a deterministic, one-page PDF with extractable Helvetica text."""
    rendered_lines = list(lines)
    require(bool(rendered_lines), "A synthetic PDF needs at least one text line")
    commands = ["BT", "/F1 10 Tf", "72 740 Td", "12 TL"]
    for index, line in enumerate(rendered_lines):
        if index:
            commands.append("T*")
        commands.append(f"({_pdf_escape(line)}) Tj")
    commands.append("ET")
    stream = ("\n".join(commands) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream"
        ),
    ]

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def synthetic_pdf_lines(paper: dict[str, Any]) -> list[str]:
    base = [
        str(paper["marker"]),
        str(paper["title"]),
        f"DOI {paper['doi']}",
        "This document is fully synthetic and exists only for offline acceptance.",
        "No private library, conversation, account, experiment, or credential is used.",
    ]
    base.extend(
        f"Synthetic observation {index:02d}: catalyst signal remains reproducible "
        "under a fabricated control condition."
        for index in range(1, 14)
    )
    return base


def isolated_environment(base: Path) -> dict[str, str]:
    profile = base / "profile"
    runtime_temp = base / "runtime-temp"
    directories = {
        "HOME": profile,
        "USERPROFILE": profile,
        "APPDATA": profile / "AppData" / "Roaming",
        "LOCALAPPDATA": profile / "AppData" / "Local",
        "TEMP": runtime_temp,
        "TMP": runtime_temp,
        "TMPDIR": runtime_temp,
        "XDG_CACHE_HOME": profile / ".cache",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    env = {
        name: os.environ[name]
        for name in SYSTEM_ENV_NAMES
        if name in os.environ
    }
    env.update({name: str(path) for name, path in directories.items()})
    env.update(
        {
            "ASA_PDF_PARSER": "pdfplumber",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def component_python(root: Path, *, require_venv: bool = False) -> Path:
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if require_venv:
        raise E2EFailure(
            "Lectern has no .venv; run `uv sync --all-packages --locked --no-dev`"
        )
    return Path(sys.executable)


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    label: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise E2EFailure(f"{label} could not execute") from exc
    if completed.returncode != 0:
        raise E2EFailure(
            f"{label} failed with exit {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    return completed


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise E2EFailure(f"Expected valid JSON artifact: {path.name}") from exc
    require(isinstance(value, dict), f"Expected JSON object: {path.name}")
    return value


def artifact_chunks(path: Path) -> Iterable[bytes]:
    if path.suffix.lower() == ".pptx":
        try:
            with zipfile.ZipFile(path) as archive:
                for name in sorted(archive.namelist()):
                    if not name.endswith("/"):
                        yield archive.read(name)
        except (OSError, zipfile.BadZipFile) as exc:
            raise E2EFailure(f"Generated PPTX is not a valid package: {path.name}") from exc
        return
    yield path.read_bytes()


def assert_public_artifacts_safe(
    paths: Iterable[Path], *, forbidden_literals: Iterable[str | Path]
) -> None:
    literals = [
        variant
        for value in forbidden_literals
        for variant in {
            str(value),
            str(value).replace("\\", "/"),
            str(value).replace("/", "\\"),
        }
        if variant
    ]
    for path in paths:
        for chunk in artifact_chunks(path):
            for literal in literals:
                if (
                    literal.encode("utf-8", errors="ignore") in chunk
                    or literal.encode("utf-16le", errors="ignore") in chunk
                ):
                    raise E2EFailure(
                        f"Transferable artifact exposed a local value: {path.name}"
                    )
            texts = (
                chunk.decode("utf-8", errors="ignore"),
                chunk.decode("latin-1", errors="ignore"),
            )
            for text in texts:
                if "pdfPaths" in text:
                    raise E2EFailure(
                        f"Transferable artifact retained source paths: {path.name}"
                    )
                if EMAIL_PATTERN.search(text):
                    raise E2EFailure(
                        f"Transferable artifact contains an email: {path.name}"
                    )
                if SECRET_PATTERN.search(text):
                    raise E2EFailure(
                        f"Transferable artifact contains a credential pattern: {path.name}"
                    )
                if HOME_PATH_PATTERN.search(text):
                    raise E2EFailure(
                        f"Transferable artifact contains a home path: {path.name}"
                    )


def create_library_kb(source_dir: Path) -> Path:
    items: list[dict[str, Any]] = []
    for index, paper in enumerate(SYNTHETIC_PAPERS, start=1):
        pdf_path = source_dir / f"synthetic-paper-{index}.pdf"
        pdf_path.write_bytes(build_pdf(synthetic_pdf_lines(paper)))
        item = {
            key: value
            for key, value in paper.items()
            if key != "marker"
        }
        item["pdfPaths"] = [str(pdf_path)]
        items.append(item)
    kb_path = source_dir / "synthetic-library.json"
    kb_path.write_text(
        json.dumps(
            {"schema_version": "library-kb/1.1", "items": items},
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return kb_path


def validate_handoff(meta: dict[str, Any], handoff_dir: Path) -> None:
    require(meta.get("schema_version") == "handoff/1.1", "Steward did not emit handoff/1.1")
    require(meta.get("report_type") == "literature", "Handoff has the wrong report type")
    require(meta.get("title") == "[SYNTHETIC] Offline evidence review", "Handoff title changed")
    require(
        isinstance(meta.get("key"), str) and len(meta["key"]) == 8,
        "Handoff lacks an eight-character report key",
    )
    papers = meta.get("papers")
    require(isinstance(papers, list) and len(papers) == 2, "Handoff lost a paper")
    for expected, actual in zip(SYNTHETIC_PAPERS, papers):
        require(actual.get("title") == expected["title"], "Handoff changed a title")
        require(actual.get("doi") == expected["doi"], "Handoff changed a DOI")
        require("key" not in actual, "Handoff duplicated a per-paper key")
        pdf_name = actual.get("pdfFilename")
        require(
            isinstance(pdf_name, str) and (handoff_dir / pdf_name).is_file(),
            "Handoff omitted a staged PDF",
        )


def run_e2e(steward_root: Path, spec_root: Path, lectern_root: Path) -> dict[str, Any]:
    for label, root in (
        ("Steward", steward_root),
        ("Spec", spec_root),
        ("Lectern", lectern_root),
    ):
        require(root.is_dir(), f"{label} checkout not found")

    with tempfile.TemporaryDirectory(prefix="scriptorium-slides-golden-") as raw_base:
        base = Path(raw_base)
        source_dir = base / "synthetic-input"
        staging_dir = base / "handoff"
        output_dir = base / "lectern-output"
        source_dir.mkdir()
        staging_dir.mkdir()
        output_dir.mkdir()
        env = isolated_environment(base)
        kb_path = create_library_kb(source_dir)

        steward_python = component_python(steward_root)
        run_command(
            [
                str(steward_python),
                "-m",
                "steward.cli",
                "pick",
                *(str(paper["key"]) for paper in SYNTHETIC_PAPERS),
                "--kb",
                str(kb_path),
                "--staging",
                str(staging_dir),
                "--report-type",
                "literature",
                "--report-title",
                "[SYNTHETIC] Offline evidence review",
            ],
            cwd=steward_root,
            env=env,
            label="Steward pick",
        )
        handoff_dirs = [path for path in staging_dir.iterdir() if path.is_dir()]
        require(len(handoff_dirs) == 1, "Steward did not create exactly one handoff")
        handoff_dir = handoff_dirs[0]
        meta_path = handoff_dir / "meta.json"
        meta = read_json(meta_path)
        validate_handoff(meta, handoff_dir)

        spec_python = component_python(spec_root)
        validated = run_command(
            [str(spec_python), str(spec_root / "tools" / "validate.py"), str(meta_path)],
            cwd=spec_root,
            env=env,
            label="Spec validation",
        )
        require("ok" in validated.stdout.lower(), "Spec validator did not accept metadata")

        lectern_python = component_python(lectern_root, require_venv=True)
        worker = run_command(
            [
                str(lectern_python),
                str(LECTERN_WORKER),
                "--handoff",
                str(handoff_dir),
                "--output-dir",
                str(output_dir),
            ],
            cwd=lectern_root,
            env=env,
            label="Lectern golden path",
        )
        try:
            report = json.loads(worker.stdout)
        except json.JSONDecodeError as exc:
            raise E2EFailure("Lectern worker did not emit one JSON object") from exc
        require(isinstance(report, dict), "Lectern worker report is not an object")
        require(report.get("status") == "passed", "Lectern worker did not pass")
        require(
            report.get("production_graph_exercised") is True,
            "Lectern production-graph proof is absent",
        )
        require(report.get("hard_stop_blocked_pptx") is True, "Hard-stop proof is absent")
        require(report.get("editable_after_reopen") is True, "PPTX editability proof is absent")

        transferable = [
            path
            for root in (handoff_dir, output_dir)
            for path in root.rglob("*")
            if path.is_file()
        ]
        generated_pptx = list(output_dir.rglob("*.pptx"))
        require(len(generated_pptx) == 2, "Expected approved and edited PPTX outputs")
        forbidden = [
            base,
            steward_root,
            spec_root,
            lectern_root,
            Path.home(),
            os.environ.get("HOME", ""),
            os.environ.get("USERPROFILE", ""),
        ]
        assert_public_artifacts_safe(transferable, forbidden_literals=forbidden)
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steward-root", type=Path, default=DEFAULT_STEWARD_ROOT)
    parser.add_argument("--spec-root", type=Path, default=DEFAULT_SPEC_ROOT)
    parser.add_argument("--lectern-root", type=Path, default=DEFAULT_LECTERN_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_e2e(
            args.steward_root.resolve(),
            args.spec_root.resolve(),
            args.lectern_root.resolve(),
        )
    except E2EFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: synthetic Steward handoff, Spec validation, Lectern evidence, "
        f"approval hard-stop, and editable PPTX ({report['approved_slide_count']} slides)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
