"""Thin orchestration wrapper for Provenance's public pull command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .demo import (
    DemoError,
    find_script,
    load_compatibility,
    project_version,
    resolve_component_root,
)
from .host import HostInstallError, inspect_host_installation


PULL_PREVIEW_TIMEOUT_SECONDS = 60
PUBLIC_COMMAND = "prov-sync-pull"
PASSTHROUGH_ENV_NAMES = {
    "APPDATA",
    "CODEX_HOME",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}
VALID_STATUSES_BY_EXIT = {
    0: {"planned", "noop", "completed", "action-required"},
    1: {"partial", "blocked"},
    2: {"error"},
}
SUMMARY_FIELDS = (
    "projects",
    "notes_ingested",
    "notes_updated",
    "notes_unchanged",
    "notes_planned",
    "notes_unstable",
    "codex_found",
    "codex_enqueued",
    "worker_events",
    "planned_scaffolds",
    "planned_applies",
    "scaffolded",
    "applied",
    "draft_only",
    "unresolved_events",
    "pending_fill",
    "checked_approvals",
    "approved",
    "pending_approval",
)
STAGE_COUNT_FIELDS = {
    "pull_lock": (),
    "sync_state": ("files", "records", "invalid"),
    "portfolio": ("projects",),
    "notes": ("ingested", "updated", "unchanged", "planned", "unstable", "errors"),
    "codex_scan": ("found", "enqueued"),
    "worker": (
        "events",
        "would_scaffold",
        "would_apply",
        "scaffolded",
        "applied",
        "no_transcript",
        "unresolved_project",
        "errors",
        "draft_only",
    ),
    "pending": ("fills",),
    "approvals_apply": ("checked", "applied"),
    "approvals_refresh": ("pending",),
}
SAFE_STAGE_STATUSES = {
    "ok",
    "planned",
    "skipped",
    "partial",
    "blocked",
    "error",
    "action-required",
}
EGRESS_VALUES = {
    "suite_managed": {"not-requested"},
    "host_managed": {"not-invoked"},
    "optional_connectors": {"not-invoked"},
}
SAFE_ACTION_TYPES = {
    "run-confirmation",
    "agent-fill",
    "human-approval",
    "workspace-review",
    "project-resolution",
}
SAFE_ERROR_CODES = {
    "invalid_configuration",
    "invalid_sync_jsonl",
    "stage_failed",
    "note_ingest_failed",
    "worker_busy",
    "worker_event_failed",
    "worker_state_failed",
    "approvals_not_owned",
    "unsafe_data_root",
    "pull_busy",
    "lock_release_failed",
    "internal_error",
}
ENTRY_LIMITATIONS = (
    "Pending summary fills require an in-session agent review.",
    "Unresolved events require an explicit project mapping before summary creation.",
    "Only approvals already checked by the user are applied.",
    "The project filter narrows Codex discovery only; other stages remain workspace-wide.",
)


class PullError(RuntimeError):
    """The entry could not produce a trustworthy pull report."""


def _resolve_directory(path: Path, *, label: str) -> Path:
    requested = path.expanduser()
    try:
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PullError(
            f"{label} does not exist or cannot be resolved: {requested}"
        ) from exc
    if not resolved.is_dir():
        raise PullError(f"{label} is not a directory: {requested}")
    return resolved


def _validate_data_boundary(workspace: Path, provenance_home: Path) -> None:
    if (
        provenance_home == workspace
        or workspace in provenance_home.parents
        or provenance_home in workspace.parents
    ):
        raise PullError(
            "Provenance home and the Markdown workspace must be separate, "
            "non-nested directories because the data root contains protected sync state"
        )


def _configured_codex_home() -> Path | None:
    value = os.environ.get("CODEX_HOME")
    if not value or not value.strip():
        return None
    requested = Path(os.path.expandvars(value.strip())).expanduser()
    try:
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PullError("CODEX_HOME does not exist or cannot be resolved") from exc
    if not resolved.is_dir():
        raise PullError("CODEX_HOME is not a directory")
    return resolved


def _compatibility_version() -> str:
    try:
        value = load_compatibility()["provenance"]
    except (DemoError, KeyError, OSError, TypeError, ValueError) as exc:
        raise PullError(
            "packaged Provenance compatibility data is unavailable"
        ) from exc
    if not value:
        raise PullError("packaged Provenance compatibility version is empty")
    return value


def _resolve_provenance(
    explicit: Path | None, *, expected_version: str
) -> tuple[Path | None, Path]:
    root: Path | None = None
    if explicit is not None:
        root = _resolve_directory(explicit, label="Provenance root")
    else:
        try:
            root = resolve_component_root("provenance")
        except (DemoError, OSError, RuntimeError):
            root = None

    if root is not None:
        try:
            actual_version = project_version(root)
        except DemoError as exc:
            raise PullError("Provenance source metadata is unavailable") from exc
        if actual_version != expected_version:
            raise PullError(
                "incompatible Provenance source version: "
                f"expected {expected_version}, found {actual_version}"
            )
        try:
            return root, find_script(root, PUBLIC_COMMAND)
        except DemoError as exc:
            raise PullError(
                f"public command '{PUBLIC_COMMAND}' is unavailable"
            ) from exc

    installed = shutil.which(PUBLIC_COMMAND)
    if not installed:
        raise PullError(f"public command '{PUBLIC_COMMAND}' is unavailable")
    return None, Path(installed).resolve()


def _canonical_hosts(workspace: Path) -> list[str]:
    try:
        inspection = inspect_host_installation(workspace=workspace)
    except HostInstallError as exc:
        raise PullError("canonical host adapter inspection failed") from exc
    if inspection.get("workspace_state") != "available":
        raise PullError("workspace is unsafe for canonical host adapter inspection")
    if inspection.get("manifest", {}).get("state") == "invalid":
        raise PullError("canonical host adapter manifest is invalid")
    return [
        str(item["name"])
        for item in inspection.get("hosts", [])
        if isinstance(item, dict) and item.get("state") == "canonical"
    ]


def _pull_environment(*, provenance_home: Path, workspace: Path) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in PASSTHROUGH_ENV_NAMES
    }
    env.update(
        {
            "PROVENANCE_HOME": str(provenance_home),
            "PROVENANCE_VAULT": str(workspace),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def _aggregate_count(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PullError(f"public pull command report count '{field}' is invalid")
    return value


def _rebuild_egress(value: dict[str, Any]) -> dict[str, str]:
    rebuilt = {}
    for key, allowed_values in EGRESS_VALUES.items():
        candidate = value.get(key)
        if not isinstance(candidate, str) or candidate not in allowed_values:
            raise PullError("public pull command egress guarantees are incompatible")
        rebuilt[key] = candidate
    return rebuilt


def _rebuild_summary(value: dict[str, Any]) -> dict[str, int]:
    if any(key not in value for key in SUMMARY_FIELDS):
        raise PullError(
            "public pull command summary is missing required aggregate counts"
        )
    return {
        key: _aggregate_count(value[key], field=f"summary.{key}")
        for key in SUMMARY_FIELDS
    }


def _rebuild_stages(value: list[Any]) -> list[dict[str, Any]]:
    rebuilt = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise PullError("public pull command stage entry is invalid")
        stage_id = item["id"]
        if stage_id not in STAGE_COUNT_FIELDS:
            continue
        if stage_id in seen:
            raise PullError("public pull command contains a duplicate stage")
        seen.add(stage_id)
        status = item.get("status")
        if not isinstance(status, str) or status not in SAFE_STAGE_STATUSES:
            raise PullError("public pull command stage status is incompatible")
        stage = {"id": stage_id, "status": status}
        if "counts" in item:
            raw_counts = item["counts"]
            if not isinstance(raw_counts, dict):
                raise PullError("public pull command stage counts are invalid")
            stage["counts"] = {
                key: _aggregate_count(
                    raw_counts[key], field=f"stages.{stage_id}.counts.{key}"
                )
                for key in STAGE_COUNT_FIELDS[stage_id]
                if key in raw_counts
            }
        rebuilt.append(stage)
    return rebuilt


def _rebuild_actions(value: list[Any]) -> list[dict[str, Any]]:
    rebuilt = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise PullError("public pull command action entry is invalid")
        action_type = item.get("type")
        if not isinstance(action_type, str) or action_type not in SAFE_ACTION_TYPES:
            raise PullError("public pull command action type is incompatible")
        if action_type in seen:
            raise PullError("public pull command contains a duplicate action")
        seen.add(action_type)
        action = {"type": action_type}
        if "count" in item:
            action["count"] = _aggregate_count(
                item["count"], field=f"action_required.{action_type}.count"
            )
        elif action_type != "run-confirmation":
            raise PullError("public pull command action count is missing")
        rebuilt.append(action)
    return rebuilt


def _rebuild_errors(value: list[Any]) -> list[dict[str, Any]]:
    rebuilt = []
    for item in value:
        if not isinstance(item, dict):
            raise PullError("public pull command error entry is invalid")
        code = item.get("code")
        if not isinstance(code, str) or code not in SAFE_ERROR_CODES:
            raise PullError("public pull command error code is incompatible")
        error = {"code": code}
        if "count" in item:
            error["count"] = _aggregate_count(
                item["count"], field=f"errors.{code}.count"
            )
        rebuilt.append(error)
    return rebuilt


def _parse_component_report(
    stdout: str,
    *,
    expected_mode: str,
    expected_version: str,
    process_exit_code: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PullError(
            "public pull command returned non-JSON or extra stdout"
        ) from exc
    if not isinstance(report, dict):
        raise PullError("public pull command did not return a JSON object")
    format_version = report.get("format_version")
    if (
        isinstance(format_version, bool)
        or not isinstance(format_version, int)
        or format_version != 1
        or report.get("operation") != "pull"
    ):
        raise PullError("public pull command returned an incompatible report envelope")
    if not isinstance(report.get("mode"), str) or report.get("mode") != expected_mode:
        raise PullError("public pull command reported a different execution mode")

    exit_code = report.get("exit_code")
    if (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code not in VALID_STATUSES_BY_EXIT
    ):
        raise PullError("public pull command returned an unsupported exit code")
    if exit_code != process_exit_code:
        raise PullError("public pull command exit code disagrees with its JSON report")
    status = report.get("status")
    if not isinstance(status, str) or status not in VALID_STATUSES_BY_EXIT[exit_code]:
        raise PullError("public pull command returned an incompatible status")

    for key, expected_type in (
        ("egress", dict),
        ("stages", list),
        ("summary", dict),
        ("action_required", list),
        ("errors", list),
        ("limitations", list),
    ):
        if not isinstance(report.get(key), expected_type):
            raise PullError(f"public pull command report field '{key}' is invalid")

    producer = report.get("generated_by")
    if not isinstance(producer, dict):
        raise PullError("public pull command report has no producer evidence")
    if (
        producer.get("name") != "provenance"
        or producer.get("version") != expected_version
    ):
        raise PullError("public pull command producer is incompatible with this entry")
    if "entry" in report:
        raise PullError("public pull command used the reserved 'entry' report field")
    trusted = {
        "format_version": format_version,
        "operation": "pull",
        "mode": expected_mode,
        "status": status,
        "exit_code": exit_code,
        "egress": _rebuild_egress(report["egress"]),
        "stages": _rebuild_stages(report["stages"]),
        "summary": _rebuild_summary(report["summary"]),
        "action_required": _rebuild_actions(report["action_required"]),
        "errors": _rebuild_errors(report["errors"]),
        "limitations": list(ENTRY_LIMITATIONS),
    }
    return trusted, {"name": "provenance", "version": expected_version}


def run_pull(
    *,
    workspace: Path,
    provenance_home: Path,
    provenance_root: Path | None = None,
    project: str | None = None,
    run: bool = False,
) -> dict[str, Any]:
    """Invoke the compatible public Provenance pull command once."""
    resolved_workspace = _resolve_directory(workspace, label="workspace")
    resolved_home = _resolve_directory(provenance_home, label="Provenance home")
    _validate_data_boundary(resolved_workspace, resolved_home)
    expected_version = _compatibility_version()
    resolved_root, command = _resolve_provenance(
        provenance_root, expected_version=expected_version
    )
    canonical_hosts = _canonical_hosts(resolved_workspace)
    scan_codex = "codex" in canonical_hosts
    codex_home = _configured_codex_home() if scan_codex else None
    mode = "run" if run else "preview"

    argv = [
        str(command),
        "--workspace",
        str(resolved_workspace),
        "--provenance-home",
        str(resolved_home),
        "--json",
    ]
    if project is not None:
        argv.extend(["--project", project])
    if scan_codex:
        argv.append("--scan-codex")
        if codex_home is not None:
            argv.extend(["--codex-home", str(codex_home)])
    if run:
        argv.append("--run")

    try:
        completed = subprocess.run(
            argv,
            cwd=resolved_root or resolved_workspace,
            env=_pull_environment(
                provenance_home=resolved_home, workspace=resolved_workspace
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=None if run else PULL_PREVIEW_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PullError(f"public command '{PUBLIC_COMMAND}' timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise PullError(f"public command '{PUBLIC_COMMAND}' could not run") from exc

    component_report, producer = _parse_component_report(
        completed.stdout,
        expected_mode=mode,
        expected_version=expected_version,
        process_exit_code=completed.returncode,
    )
    report = dict(component_report)
    report["generated_by"] = {"name": "scriptorium", "version": __version__}
    report["entry"] = {
        "public_command": PUBLIC_COMMAND,
        "component_generated_by": producer,
        "expected_component_version": expected_version,
        "canonical_hosts": canonical_hosts,
        "scan_codex": scan_codex,
        "codex_home_source": (
            "CODEX_HOME" if codex_home is not None else "profile-default"
        ),
        "component_exit_code": completed.returncode,
        "stdout": "parsed-and-suppressed",
        "stderr": "suppressed" if completed.stderr else "empty",
    }
    return report


def format_pull_report(report: dict[str, Any]) -> str:
    """Render only aggregate, non-content-bearing pull information."""
    stage_counts = Counter(
        str(stage.get("status", "unknown"))
        for stage in report.get("stages", [])
        if isinstance(stage, dict)
    )
    count_summary = {
        str(key): value
        for key, value in report.get("summary", {}).items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    actions = []
    for item in report.get("action_required", []):
        if not isinstance(item, dict) or item.get("type") not in SAFE_ACTION_TYPES:
            continue
        count = item.get("count")
        suffix = (
            f"={count}"
            if isinstance(count, int) and not isinstance(count, bool)
            else ""
        )
        actions.append(f"{item['type']}{suffix}")
    errors = []
    for item in report.get("errors", []):
        if isinstance(item, dict) and item.get("code") in SAFE_ERROR_CODES:
            errors.append(str(item["code"]))
    lines = [
        f"Scriptorium pull {report['generated_by']['version']}",
        f"Mode: {report['mode']}",
        f"Status: {str(report['status']).upper()}",
        f"Codex scan: {'enabled' if report['entry']['scan_codex'] else 'disabled'}",
        "Stages: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(stage_counts.items()))
            or "none"
        ),
        "Summary: "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(count_summary.items()))
            or "no numeric counts"
        ),
        "Actions: " + (", ".join(actions) or "none"),
        "Errors: " + (", ".join(sorted(set(errors))) or "none"),
        f"Limitations: {len(report.get('limitations', []))}",
        "Raw component output: suppressed",
    ]
    return "\n".join(lines)
