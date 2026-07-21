"""Content-free suite workflow status aggregation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .doctor import DoctorError, run_doctor
from .path_selection import format_path_selection
from .pull import SAFE_ERROR_CODES, PullError, run_pull


READINESS_KEYS = (
    "demo",
    "public_alpha",
    "literature",
    "slides",
    "web_history",
)
READINESS_VALUES = {
    "demo": {"ready", "incomplete"},
    "public_alpha": {"ready", "incomplete"},
    "literature": {"detected", "file-only", "unavailable"},
    "slides": {"detected", "unavailable"},
    "web_history": {"manual", "unavailable"},
}
WORKFLOW_FIELDS = (
    "projects",
    "notes_planned",
    "notes_unstable",
    "codex_found",
    "worker_events",
    "planned_scaffolds",
    "planned_applies",
    "draft_only",
    "unresolved_events",
    "pending_fill",
    "checked_approvals",
    "pending_approval",
)
PENDING_FIELDS = WORKFLOW_FIELDS[1:]
SOURCE_ACTION_TYPES = {
    "run-confirmation",
    "codex-home-setup",
    "agent-fill",
    "human-approval",
    "workspace-review",
    "project-resolution",
}
ACTION_ORDER = (
    "doctor-remediation",
    "codex-home-setup",
    "project-resolution",
    "agent-fill",
    "human-approval",
    "workspace-review",
    "pull-diagnostics",
    "review-pull-plan",
)
SUMMARY_ACTION_FIELDS = {
    "project-resolution": ("unresolved_events",),
    "agent-fill": ("pending_fill",),
    "human-approval": ("pending_approval",),
    "workspace-review": ("notes_unstable", "draft_only"),
}
PULL_EGRESS = {
    "suite_managed": "not-requested",
    "host_managed": "not-invoked",
    "optional_connectors": "not-invoked",
}
STATUS_EGRESS = {
    "suite_managed": "not-requested",
    "host_managed": "readiness-probes-only",
    "optional_connectors": "not-invoked",
}
LIMITATIONS = [
    "Readiness and pull preview are sequential best-effort observations, not an atomic snapshot.",
    "The last successful pull time is not reported by a stable component contract.",
    "The project filter narrows Codex discovery only; other stages remain workspace-wide.",
    "Agent authentication and a live model call are not tested.",
    "No suite project or data write is authorized; external probe side effects are not OS-observed.",
]


class StatusError(RuntimeError):
    """The entry could not produce a trustworthy content-free status report."""


def _count(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StatusError(f"status source count '{field}' is invalid")
    return value


def _producer(report: dict[str, Any], *, source: str) -> None:
    generated_by = report.get("generated_by")
    if (
        not isinstance(generated_by, dict)
        or generated_by.get("name") != "scriptorium"
        or generated_by.get("version") != __version__
    ):
        raise StatusError(f"{source} report producer is incompatible")


def _doctor_summary(report: dict[str, Any]) -> tuple[dict[str, str], int, str]:
    if (
        not isinstance(report, dict)
        or type(report.get("format_version")) is not int
        or report.get("format_version") != 1
        or report.get("target") != "public-alpha"
    ):
        raise StatusError("doctor report envelope is incompatible")
    _producer(report, source="doctor")
    status = report.get("status")
    exit_code = report.get("exit_code")
    if (
        type(exit_code) is not int
        or (status, exit_code) not in {("ready", 0), ("incomplete", 1)}
    ):
        raise StatusError("doctor report status is incompatible")

    source_readiness = report.get("readiness")
    if not isinstance(source_readiness, dict):
        raise StatusError("doctor readiness is unavailable")
    readiness: dict[str, str] = {}
    for key in READINESS_KEYS:
        value = source_readiness.get(key)
        if not isinstance(value, str) or value not in READINESS_VALUES[key]:
            raise StatusError(f"doctor readiness field '{key}' is incompatible")
        readiness[key] = value
    if readiness["public_alpha"] != ("ready" if status == "ready" else "incomplete"):
        raise StatusError("doctor readiness disagrees with its status")

    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise StatusError("doctor summary is unavailable")
    failures = _count(summary.get("fail"), field="doctor.summary.fail")
    if status == "incomplete" and failures == 0:
        raise StatusError("incomplete doctor report has no failed requirement")
    if status == "ready" and failures:
        raise StatusError("ready doctor report contains failed requirements")
    return readiness, failures, status


def _workflow_summary(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    if (
        not isinstance(report, dict)
        or type(report.get("format_version")) is not int
        or report.get("format_version") != 1
        or report.get("operation") != "pull"
        or report.get("mode") != "preview"
    ):
        raise StatusError("pull preview report envelope is incompatible")
    _producer(report, source="pull preview")

    status = report.get("status")
    exit_code = report.get("exit_code")
    valid = {
        0: {"planned", "noop", "completed", "action-required"},
        1: {"partial", "blocked"},
        2: {"error"},
    }
    if (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or status not in valid.get(exit_code, set())
    ):
        raise StatusError("pull preview status is incompatible")

    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise StatusError("pull preview summary is unavailable")
    workflow: dict[str, Any] = {
        key: _count(summary.get(key), field=f"pull.summary.{key}")
        for key in WORKFLOW_FIELDS
    }

    entry = report.get("entry")
    if not isinstance(entry, dict) or not isinstance(entry.get("scan_codex"), bool):
        raise StatusError("pull preview capture evidence is unavailable")
    workflow["codex_scan"] = "enabled" if entry["scan_codex"] else "disabled"

    egress = report.get("egress")
    if not isinstance(egress, dict) or any(
        egress.get(key) != value for key, value in PULL_EGRESS.items()
    ):
        raise StatusError("pull preview egress guarantees are incompatible")

    raw_errors = report.get("errors")
    if not isinstance(raw_errors, list):
        raise StatusError("pull preview errors are unavailable")
    diagnostic_count = 0
    for item in raw_errors:
        if not isinstance(item, dict) or item.get("code") not in SAFE_ERROR_CODES:
            raise StatusError("pull preview error is incompatible")
        diagnostic_count += (
            _count(item["count"], field=f"pull.errors.{item['code']}")
            if "count" in item
            else 1
        )
    if exit_code == 0 and raw_errors:
        raise StatusError("successful pull preview contains errors")

    raw_actions = report.get("action_required")
    if not isinstance(raw_actions, list):
        raise StatusError("pull preview actions are unavailable")
    action_counts: dict[str, int] = {}
    seen_actions: set[str] = set()
    for item in raw_actions:
        if not isinstance(item, dict) or item.get("type") not in SOURCE_ACTION_TYPES:
            raise StatusError("pull preview action is incompatible")
        action_type = item["type"]
        if action_type in seen_actions:
            raise StatusError("pull preview contains a duplicate action")
        seen_actions.add(action_type)
        if action_type == "run-confirmation":
            if "count" in item:
                raise StatusError("run confirmation must not carry a count")
            continue
        action_counts[action_type] = _count(
            item.get("count"), field=f"pull.action_required.{action_type}"
        )

    for action_type, fields in SUMMARY_ACTION_FIELDS.items():
        count = sum(workflow[field] for field in fields)
        if action_type in action_counts and action_counts[action_type] != count:
            raise StatusError("pull preview action count disagrees with its summary")
        if count:
            action_counts[action_type] = count
    return {
        "status": status,
        "exit_code": exit_code,
        "workflow": workflow,
        "diagnostic_count": max(diagnostic_count, 1) if exit_code else 0,
    }, action_counts


def _actions(
    counts: dict[str, int],
    *,
    pull_diagnostics: int = 0,
    review_pull_plan: bool,
) -> list[dict[str, Any]]:
    if pull_diagnostics:
        counts = {**counts, "pull-diagnostics": pull_diagnostics}
    if review_pull_plan:
        counts = {**counts, "review-pull-plan": 1}
    actions = []
    for action_type in ACTION_ORDER:
        count = counts.get(action_type, 0)
        if not count:
            continue
        action = {"type": action_type, "count": count}
        if action_type == "doctor-remediation":
            action["command"] = "scriptorium doctor"
        elif action_type in {"pull-diagnostics", "review-pull-plan"}:
            action["command"] = "scriptorium pull"
        actions.append(action)
    return actions


def _report(
    *,
    status: str,
    exit_code: int,
    readiness: dict[str, str],
    freshness: str,
    workflow: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "status",
        "status": status,
        "exit_code": exit_code,
        "readiness": readiness,
        "freshness": {
            "state": freshness,
            "basis": (
                "pull-preview"
                if freshness != "unknown"
                else "not-available"
            ),
            "last_successful_pull": "not-reported",
        },
        "workflow": workflow,
        "action_required": actions,
        "egress": dict(STATUS_EGRESS),
        "limitations": list(LIMITATIONS),
    }


def run_status(
    *,
    workspace: Path,
    provenance_home: Path,
    project: str | None = None,
    spec_root: Path | None = None,
    steward_root: Path | None = None,
    provenance_root: Path | None = None,
    lectern_root: Path | None = None,
) -> dict[str, Any]:
    """Aggregate readiness and pull preview without authorizing suite data writes."""
    try:
        doctor_report = run_doctor(
            target="public-alpha",
            spec_root=spec_root,
            steward_root=steward_root,
            provenance_root=provenance_root,
            provenance_home=provenance_home,
            lectern_root=lectern_root,
            workspace=workspace,
        )
    except DoctorError as exc:
        raise StatusError("doctor probe failed") from exc
    readiness, failures, doctor_status = _doctor_summary(doctor_report)
    if doctor_status == "incomplete":
        return _report(
            status="incomplete",
            exit_code=1,
            readiness=readiness,
            freshness="unknown",
            workflow={},
            actions=_actions({"doctor-remediation": failures}, review_pull_plan=False),
        )

    try:
        pull_report = run_pull(
            workspace=workspace,
            provenance_home=provenance_home,
            provenance_root=provenance_root,
            project=project,
            run=False,
        )
    except PullError as exc:
        raise StatusError("pull preview failed") from exc
    pull, action_counts = _workflow_summary(pull_report)
    if pull["exit_code"] == 2:
        return _report(
            status="error",
            exit_code=2,
            readiness=readiness,
            freshness="unknown",
            workflow=pull["workflow"],
            actions=_actions(
                action_counts,
                pull_diagnostics=pull["diagnostic_count"],
                review_pull_plan=False,
            ),
        )
    if pull["exit_code"] == 1:
        return _report(
            status="blocked",
            exit_code=1,
            readiness=readiness,
            freshness="unknown",
            workflow=pull["workflow"],
            actions=_actions(
                action_counts,
                pull_diagnostics=pull["diagnostic_count"],
                review_pull_plan=False,
            ),
        )

    has_pending = any(pull["workflow"][key] for key in PENDING_FIELDS)
    needs_review = any(
        action_counts.get(action_type, 0)
        for action_type in (
            "codex-home-setup",
            "project-resolution",
            "agent-fill",
            "human-approval",
            "workspace-review",
        )
    )
    return _report(
        status="attention" if has_pending or needs_review else "ready",
        exit_code=0,
        readiness=readiness,
        freshness=(
            "review-required"
            if needs_review
            else "changes-pending"
            if has_pending
            else "current"
        ),
        workflow=pull["workflow"],
        actions=_actions(action_counts, review_pull_plan=has_pending),
    )


def format_status_report(report: dict[str, Any]) -> str:
    """Render a compact status summary without local paths or research content."""
    readiness = report.get("readiness", {})
    freshness = report.get("freshness", {})
    workflow = report.get("workflow", {})
    pending = {
        key: workflow[key]
        for key in PENDING_FIELDS
        if isinstance(workflow.get(key), int) and workflow[key]
    }
    actions = [
        f"{item['type']}={item['count']}"
        for item in report.get("action_required", [])
        if isinstance(item, dict)
        and item.get("type") in ACTION_ORDER
        and isinstance(item.get("count"), int)
        and not isinstance(item.get("count"), bool)
    ]
    lines = [
            f"Scriptorium status {report['generated_by']['version']}",
            f"Overall: {str(report['status']).upper()}",
    ]
    lines.extend(
        format_path_selection(
            report,
            conflict_guidance="review the selected environment values.",
        )
    )
    lines.extend(
        [
            f"Public Alpha readiness: {str(readiness.get('public_alpha', 'unknown')).upper()}",
            f"Literature: {str(readiness.get('literature', 'unknown')).upper()}",
            f"Slides: {str(readiness.get('slides', 'unknown')).upper()}",
            f"Web history: {str(readiness.get('web_history', 'unknown')).upper()}",
            f"Freshness: {str(freshness.get('state', 'unknown')).upper()}",
            f"Codex scan: {workflow.get('codex_scan', 'not-run')}",
            "Pending: "
            + (
                ", ".join(f"{key}={value}" for key, value in pending.items())
                or "none"
            ),
            "Actions: " + (", ".join(actions) or "none"),
            "Safety: no suite-authorized data writes; raw content and local paths "
            "suppressed; external probe side effects not OS-observed",
        ]
    )
    return "\n".join(lines)
