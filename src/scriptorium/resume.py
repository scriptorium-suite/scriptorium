"""Read-only Scriptorium entry for a bounded Provenance context capsule."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import __version__
from .demo import DemoError, find_script, load_compatibility, project_version, resolve_component_root
from .path_selection import format_path_selection


PUBLIC_COMMAND = "prov-context"
RESUME_TIMEOUT_SECONDS = 30
CAPSULE_VERSION = "context-capsule/0.1"
ARTIFACT_KINDS = {"parsed-paper", "reading-note", "review", "lineage-graph"}
ARTIFACT_SCHEMAS = {
    "parsed-paper/1.0",
    "reading-note/1.0",
    "review/1.0",
    "lineage-graph/1.0",
}
PASSTHROUGH_ENV_NAMES = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}
ENTRY_LIMITATIONS = (
    "The capsule contains human-authored or approved project state only.",
    "Literature and research artifacts remain reference-only, not approved scientific claims.",
    "The capsule is local research context and must be reviewed before sharing.",
)
_WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\[^\\\s]+[\\/])")
_PRIVATE_POSIX_PATH = re.compile(r"(?:^|[\s\"'])(?:/(?:home|Users|root|mnt|private|tmp)/)")


class ResumeError(RuntimeError):
    """A trustworthy context capsule could not be produced."""


def _resolve_directory(path: Path, *, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ResumeError(f"{label} does not exist or cannot be resolved") from exc
    if not resolved.is_dir():
        raise ResumeError(f"{label} is not a directory")
    return resolved


def _compatibility_version() -> str:
    try:
        version = load_compatibility()["provenance"]
    except (DemoError, KeyError, OSError, TypeError, ValueError) as exc:
        raise ResumeError("packaged Provenance compatibility data is unavailable") from exc
    if not version:
        raise ResumeError("packaged Provenance compatibility version is empty")
    return version


def _resolve_provenance(
    explicit: Path | None, *, expected_version: str
) -> tuple[Path | None, Path]:
    root: Path | None = None
    if explicit is not None:
        root = _resolve_directory(explicit, label="Provenance root")
    else:
        try:
            root = resolve_component_root("provenance")
        except (DemoError, OSError, RuntimeError) as exc:
            configured = os.environ.get("SCRIPTORIUM_PROVENANCE_ROOT")
            if configured and configured.strip():
                raise ResumeError("configured Provenance root is unavailable") from exc

    if root is not None:
        try:
            actual_version = project_version(root)
        except DemoError as exc:
            raise ResumeError("Provenance source metadata is unavailable") from exc
        if actual_version != expected_version:
            raise ResumeError(
                "incompatible Provenance source version: "
                f"expected {expected_version}, found {actual_version}"
            )
        try:
            return root, find_script(root, PUBLIC_COMMAND)
        except DemoError as exc:
            raise ResumeError(f"public command '{PUBLIC_COMMAND}' is unavailable") from exc

    installed = shutil.which(PUBLIC_COMMAND)
    if not installed:
        raise ResumeError(f"public command '{PUBLIC_COMMAND}' is unavailable")
    return None, Path(installed).resolve()


def _resume_environment(provenance_home: Path) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in PASSTHROUGH_ENV_NAMES
    }
    env.update(
        {
            "PROVENANCE_HOME": str(provenance_home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def _object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ResumeError(f"context capsule field '{label}' is incompatible")
    return value


def _text(value: Any, *, label: str, maximum: int = 4000) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ResumeError(f"context capsule field '{label}' is incompatible")
    if _WINDOWS_PATH.search(value) or _PRIVATE_POSIX_PATH.search(value):
        raise ResumeError("context capsule contains a suppressed local path")
    return value


def _text_list(
    value: Any, *, label: str, maximum_items: int = 100, maximum_text: int = 2000
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ResumeError(f"context capsule field '{label}' is incompatible")
    return [
        _text(item, label=f"{label}[]", maximum=maximum_text) for item in value
    ]


def _rebuild_project(value: Any) -> dict[str, str]:
    fields = {
        "project_id",
        "title",
        "status",
        "stage",
        "priority",
        "updated",
        "goal",
        "conclusion",
    }
    project = _object(value, fields=fields, label="project")
    return {
        key: _text(project[key], label=f"project.{key}")
        for key in (
            "project_id",
            "title",
            "status",
            "stage",
            "priority",
            "updated",
            "goal",
            "conclusion",
        )
    }


def _rebuild_recent_progress(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 30:
        raise ResumeError("context capsule field 'recent_progress' is incompatible")
    rebuilt = []
    for item in value:
        entry = _object(item, fields={"date", "items"}, label="recent_progress[]")
        rebuilt.append(
            {
                "date": _text(entry["date"], label="recent_progress[].date", maximum=64),
                "items": _text_list(
                    entry["items"], label="recent_progress[].items", maximum_items=30
                ),
            }
        )
    return rebuilt


def _rebuild_literature(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ResumeError("context capsule field 'literature' is incompatible")
    rebuilt = []
    fields = {"id", "citekey", "title", "year", "read_status", "tldr"}
    for item in value:
        entry = _object(item, fields=fields, label="literature[]")
        year = entry["year"]
        if year is not None and (
            isinstance(year, bool) or not isinstance(year, (int, str))
        ):
            raise ResumeError("context capsule field 'literature[].year' is incompatible")
        rebuilt.append(
            {
                "id": _text(entry["id"], label="literature[].id", maximum=256),
                "citekey": _text(
                    entry["citekey"], label="literature[].citekey", maximum=256
                ),
                "title": _text(entry["title"], label="literature[].title"),
                "year": year,
                "read_status": _text(
                    entry["read_status"], label="literature[].read_status", maximum=128
                ),
                "tldr": _text(entry["tldr"], label="literature[].tldr"),
            }
        )
    return rebuilt


def _rebuild_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ResumeError("context capsule field 'research_artifacts' is incompatible")
    rebuilt = []
    fields = {
        "kind",
        "schema_version",
        "id",
        "title",
        "status",
        "source_ids",
        "summary",
        "trust",
    }
    for item in value:
        entry = _object(item, fields=fields, label="research_artifacts[]")
        kind = _text(entry["kind"], label="research_artifacts[].kind", maximum=64)
        schema_version = _text(
            entry["schema_version"],
            label="research_artifacts[].schema_version",
            maximum=64,
        )
        if kind not in ARTIFACT_KINDS or schema_version not in ARTIFACT_SCHEMAS:
            raise ResumeError("context capsule research artifact type is incompatible")
        if entry["trust"] != "reference_only":
            raise ResumeError("context capsule research artifact trust is incompatible")
        rebuilt.append(
            {
                "kind": kind,
                "schema_version": schema_version,
                "id": _text(entry["id"], label="research_artifacts[].id", maximum=256),
                "title": _text(entry["title"], label="research_artifacts[].title"),
                "status": _text(
                    entry["status"], label="research_artifacts[].status", maximum=128
                ),
                "source_ids": _text_list(
                    entry["source_ids"],
                    label="research_artifacts[].source_ids",
                    maximum_items=100,
                    maximum_text=256,
                ),
                "summary": _text(entry["summary"], label="research_artifacts[].summary"),
                "trust": "reference_only",
            }
        )
    return rebuilt


def _parse_capsule(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ResumeError("public context command returned non-JSON or extra stdout") from exc
    fields = {
        "capsule_version",
        "project",
        "next_actions",
        "blocked_by",
        "recent_progress",
        "literature",
        "research_artifacts",
        "reference_leads",
        "trust",
        "limits",
    }
    capsule = _object(value, fields=fields, label="root")
    if capsule["capsule_version"] != CAPSULE_VERSION:
        raise ResumeError("public context command returned an incompatible capsule version")

    leads = _object(
        capsule["reference_leads"],
        fields={"gaps", "priority_reads"},
        label="reference_leads",
    )
    trust = _object(
        capsule["trust"],
        fields={"project_state", "recent_progress", "research_artifacts"},
        label="trust",
    )
    if trust != {
        "project_state": "human_or_approved",
        "recent_progress": "auto_applied_low_risk_not_approved_claims",
        "research_artifacts": "reference_only_not_approved_claims",
    }:
        raise ResumeError("public context command returned incompatible trust semantics")
    limits = _object(
        capsule["limits"], fields={"max_markdown_chars", "truncated"}, label="limits"
    )
    if limits.get("max_markdown_chars") != 8000 or not isinstance(
        limits.get("truncated"), bool
    ):
        raise ResumeError("public context command returned incompatible limits")

    return {
        "capsule_version": CAPSULE_VERSION,
        "project": _rebuild_project(capsule["project"]),
        "next_actions": _text_list(capsule["next_actions"], label="next_actions"),
        "blocked_by": _text(capsule["blocked_by"], label="blocked_by"),
        "recent_progress": _rebuild_recent_progress(capsule["recent_progress"]),
        "literature": _rebuild_literature(capsule["literature"]),
        "research_artifacts": _rebuild_artifacts(capsule["research_artifacts"]),
        "reference_leads": {
            "gaps": _text_list(leads["gaps"], label="reference_leads.gaps"),
            "priority_reads": _text_list(
                leads["priority_reads"], label="reference_leads.priority_reads"
            ),
        },
        "trust": dict(trust),
        "limits": dict(limits),
    }


def run_resume(
    *, provenance_home: Path, project: str, provenance_root: Path | None = None
) -> dict[str, Any]:
    """Read one project capsule through Provenance's public command."""
    resolved_home = _resolve_directory(provenance_home, label="Provenance home")
    expected_version = _compatibility_version()
    resolved_root, command = _resolve_provenance(
        provenance_root, expected_version=expected_version
    )
    if not isinstance(project, str) or not project.strip():
        raise ResumeError("project is required")
    cwd = resolved_root or resolved_home
    environment = _resume_environment(resolved_home)
    try:
        version_probe = subprocess.run(
            [str(command), "--version"],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=RESUME_TIMEOUT_SECONDS,
            check=False,
        )
        if (
            version_probe.returncode != 0
            or version_probe.stdout.strip() != expected_version
        ):
            raise ResumeError(
                f"public command '{PUBLIC_COMMAND}' has an incompatible runtime version"
            )
        argv = [str(command), "--project", project, "--json"]
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=RESUME_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ResumeError(f"public command '{PUBLIC_COMMAND}' timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise ResumeError(f"public command '{PUBLIC_COMMAND}' could not run") from exc
    if completed.returncode != 0:
        raise ResumeError(f"public command '{PUBLIC_COMMAND}' did not return a capsule")

    capsule = _parse_capsule(completed.stdout)
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "resume",
        "status": "ready",
        "exit_code": 0,
        "capsule": capsule,
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "not-invoked",
            "optional_connectors": "not-invoked",
        },
        "entry": {
            "public_command": PUBLIC_COMMAND,
            "component_generated_by": {
                "name": "provenance",
                "version": expected_version,
            },
            "expected_component_version": expected_version,
            "component_exit_code": completed.returncode,
            "stdout": "parsed-and-suppressed",
            "stderr": "suppressed" if completed.stderr else "empty",
        },
        "limitations": list(ENTRY_LIMITATIONS),
    }


def format_resume_report(report: dict[str, Any]) -> str:
    """Render the trusted capsule without exposing component diagnostics or paths."""
    capsule = report["capsule"]
    project = capsule["project"]
    lines = [
        f"Scriptorium resume {report['generated_by']['version']}",
        f"Project: {project['title']} ({project['project_id']})",
        f"State: {project['status']} / {project['stage']}",
    ]
    if project["goal"]:
        lines.extend(["", "Goal:", project["goal"]])
    if project["conclusion"]:
        lines.extend(["", "Approved conclusion:", project["conclusion"]])
    if capsule["next_actions"]:
        lines.extend(["", "Next actions:"])
        lines.extend(f"- {item}" for item in capsule["next_actions"])
    if capsule["blocked_by"]:
        lines.extend(["", "Blocked by:", capsule["blocked_by"]])
    if capsule["recent_progress"]:
        lines.extend(["", "Recent progress (auto-applied low-risk context):"])
        for entry in capsule["recent_progress"]:
            lines.extend(f"- {entry['date']}: {item}" for item in entry["items"])
    if capsule["literature"] or capsule["research_artifacts"]:
        lines.extend(
            [
                "",
                "Reference context:",
                f"- Literature records: {len(capsule['literature'])}",
                f"- Research artifacts: {len(capsule['research_artifacts'])}",
                "- Trust: reference-only; verify against primary evidence.",
            ]
        )
    if capsule["limits"]["truncated"]:
        lines.extend(["", "Note: the bounded capsule was truncated."])
    lines.extend(
        format_path_selection(
            report,
            conflict_guidance="pass an explicit --provenance-home to select the intended local data root.",
        )
    )
    return "\n".join(lines)
