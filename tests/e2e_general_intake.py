"""Credential-free general-project intake acceptance path.

This script exercises installed public commands against synthetic files only. It
proves that an engineering repository is registered rather than copied, while
selected Markdown/PDF documents can be previewed, migrated, verified, and rolled
back without exposing local paths in public reports.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class E2EFailure(RuntimeError):
    """The installed public workflow did not satisfy its release contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise E2EFailure(message)


def run_cli(
    executable: str,
    arguments: list[str],
    *,
    environment: dict[str, str],
) -> tuple[str, dict[str, object]]:
    completed = subprocess.run(
        [executable, *arguments, "--json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=environment,
        shell=False,
        timeout=30,
    )
    require(completed.returncode == 0, "installed command returned a failure")
    require(not completed.stderr.strip(), "installed command wrote to stderr")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise E2EFailure("installed command did not return one JSON report") from exc
    require(isinstance(report, dict), "installed command returned a non-object report")
    return completed.stdout, report


def require_private_values_absent(payloads: list[str], values: list[str]) -> None:
    combined = "\n".join(payloads)
    for index, value in enumerate(values):
        variants = {value, value.replace("\\", "\\\\"), value.replace("\\", "/")}
        require(
            all(variant not in combined for variant in variants if variant),
            f"a content-free report exposed private synthetic value {index}",
        )


def find_scriptorium() -> str | None:
    configured = os.environ.get("SCRIPTORIUM_E2E_EXECUTABLE")
    if configured:
        candidate = Path(configured)
        return os.fspath(candidate) if candidate.is_file() else None
    discovered = shutil.which("scriptorium")
    if discovered:
        return discovered
    executable = Path(sys.executable)
    names = ("scriptorium.exe", "scriptorium")
    for directory in (executable.parent, executable.parent / "Scripts"):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return os.fspath(candidate)
    return None


def main() -> int:
    executable = find_scriptorium()
    require(executable is not None, "installed scriptorium command was not found")

    with tempfile.TemporaryDirectory(prefix="scriptorium-general-intake-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        provenance_home = root / "memory"
        linked_repo = root / "linked-system"
        sources = root / "selected-docs"
        config_dir = root / "config"
        linked_repo.mkdir()
        sources.mkdir()
        (linked_repo / "service.py").write_text(
            'print("synthetic")\n', encoding="utf-8"
        )
        (linked_repo / ".env").write_text(
            "SYNTHETIC_ONLY=true\n", encoding="utf-8"
        )
        (sources / "maintenance.md").write_text(
            "# Synthetic maintenance notes\n", encoding="utf-8"
        )
        (sources / "manual.pdf").write_bytes(b"%PDF-1.4\nsynthetic\n")

        environment = os.environ.copy()
        environment.update(
            {
                "APPDATA": os.fspath(root / "appdata"),
                "LOCALAPPDATA": os.fspath(root / "localappdata"),
                "XDG_STATE_HOME": os.fspath(root / "state"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        for directory in (root / "appdata", root / "localappdata", root / "state"):
            directory.mkdir()

        init_arguments = [
            "init",
            "--workspace",
            os.fspath(workspace),
            "--provenance-home",
            os.fspath(provenance_home),
            "--project-id",
            "synthetic-system",
            "--title",
            "Synthetic system maintenance",
            "--template",
            "engineering",
            "--linked-repo",
            os.fspath(linked_repo),
            "--host",
            "codex",
            "--context",
            "Trace a synthetic maintenance issue.",
            "--config-dir",
            os.fspath(config_dir),
        ]
        content_free_outputs: list[str] = []
        raw, preview = run_cli(executable, init_arguments, environment=environment)
        require(preview.get("status") == "planned", "init preview was not planned")
        require(not workspace.exists(), "init preview wrote the workspace")

        raw, initialized = run_cli(
            executable, [*init_arguments, "--run"], environment=environment
        )
        require(
            initialized.get("status") == "initialized", "engineering init did not run"
        )
        project_note = workspace / "Projects" / "synthetic-system.md"
        project_text = project_note.read_text(encoding="utf-8")
        require('profile: "engineering"' in project_text, "engineering profile was lost")
        require(
            os.fspath(linked_repo).replace("\\", "/") in project_text,
            "linked repository was not registered",
        )
        require(
            not any(path.name in {"service.py", ".env"} for path in workspace.rglob("*")),
            "linking a repository copied its files into the workspace",
        )

        raw, inventory = run_cli(
            executable,
            ["inventory", "--source", os.fspath(sources)],
            environment=environment,
        )
        content_free_outputs.append(raw)
        summary = inventory.get("summary")
        require(isinstance(summary, dict), "inventory summary was missing")
        require(summary.get("candidates") == 2, "inventory did not find two documents")
        require(summary.get("markdown") == 1, "inventory lost the Markdown document")
        require(summary.get("pdf") == 1, "inventory lost the PDF document")

        migration_identity = [
            "--workspace",
            os.fspath(workspace),
            "--batch-id",
            "selected-docs-001",
        ]
        raw, plan = run_cli(
            executable,
            ["migrate", "plan", "--source", os.fspath(sources), *migration_identity],
            environment=environment,
        )
        content_free_outputs.append(raw)
        require(plan.get("status") == "planned", "migration preview was not planned")
        require(not (workspace / "Sources").exists(), "migration preview wrote files")

        raw, applied = run_cli(
            executable,
            ["migrate", "apply", "--source", os.fspath(sources), *migration_identity],
            environment=environment,
        )
        content_free_outputs.append(raw)
        applied_summary = applied.get("summary")
        require(isinstance(applied_summary, dict), "migration summary was missing")
        require(applied_summary.get("files") == 2, "migration did not copy two documents")

        raw, verified = run_cli(
            executable,
            ["migrate", "verify", *migration_identity],
            environment=environment,
        )
        content_free_outputs.append(raw)
        require(verified.get("status") == "applied", "migration verification failed")

        raw, rolled_back = run_cli(
            executable,
            ["migrate", "rollback", *migration_identity],
            environment=environment,
        )
        content_free_outputs.append(raw)
        require(rolled_back.get("status") == "rolled-back", "rollback did not finish")
        imported = workspace / "Sources" / "Imported" / "selected-docs-001"
        require(
            not any(path.is_file() for path in imported.rglob("*")),
            "rollback left an owned imported file",
        )
        require(
            (sources / "maintenance.md").read_text(encoding="utf-8")
            == "# Synthetic maintenance notes\n",
            "migration changed its source",
        )

        require_private_values_absent(
            content_free_outputs,
            [
                os.fspath(root),
                os.fspath(workspace),
                os.fspath(linked_repo),
                "service.py",
                ".env",
                "maintenance.md",
                "manual.pdf",
                "SYNTHETIC_ONLY=true",
            ],
        )

    print(
        "PASS: engineering init, linked-repository registration, explicit document "
        "inventory, migration, verification, privacy, and rollback"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except E2EFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
