"""Content-free path precedence evidence for suite entry commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def select_configured_path(
    explicit: Path | None,
    environment_names: tuple[str, ...],
    configured: Path | None,
) -> tuple[Path | None, dict[str, object]]:
    if explicit is not None:
        return explicit, _selection("cli")
    for name in environment_names:
        value = os.environ.get(name)
        if value and value.strip():
            selected = Path(value.strip())
            conflict = bool(
                configured is not None and not _same_path(selected, configured)
            )
            return selected, _selection(
                "environment",
                environment=name,
                suite_config_conflict=conflict,
            )
    return configured, _selection(
        "suite-config" if configured is not None else "unset"
    )


def root_selection(explicit: Path | None, environment_name: str) -> dict[str, object]:
    if explicit is not None:
        return _selection("cli")
    value = os.environ.get(environment_name)
    if value and value.strip():
        return _selection("environment", environment=environment_name)
    return _selection("auto-discovery")


def codex_home_selection() -> dict[str, object]:
    _root, source, state = resolve_codex_home()
    if source != "CODEX_HOME":
        return _selection("profile-default", state=state)
    return _selection(
        "environment",
        environment="CODEX_HOME",
        state=state,
    )


def resolve_codex_home() -> tuple[Path | None, str, str]:
    value = os.environ.get("CODEX_HOME")
    if value and value.strip():
        requested = Path(os.path.expandvars(value.strip())).expanduser()
        source = "CODEX_HOME"
    else:
        profile = None
        for name in ("USERPROFILE", "HOME"):
            candidate = os.environ.get(name)
            if candidate and candidate.strip():
                profile = Path(os.path.expandvars(candidate.strip())).expanduser()
                break
        if profile is None:
            try:
                profile = Path.home()
            except RuntimeError:
                return None, "profile-default", "not-probed"
        requested = profile / ".codex"
        source = "profile-default"
    try:
        resolved = requested.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = requested
    if resolved.is_dir():
        state = "ready"
    elif resolved.exists():
        state = "not-directory"
    else:
        state = "missing"
    return resolved, source, state


def selection_warnings(
    selections: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            "code": "environment_suite_config_conflict",
            "path": name,
            "selected_source": "environment",
            "environment": selection.get("environment"),
        }
        for name, selection in selections.items()
        if selection.get("suite_config_conflict") is True
    ]


def attach_path_selection(
    report: dict[str, Any],
    selections: dict[str, dict[str, object]],
    warnings: list[dict[str, object]],
) -> None:
    report["path_selection"] = selections
    report["warnings"] = warnings


def format_path_selection(
    report: dict[str, Any], *, conflict_guidance: str
) -> list[str]:
    lines: list[str] = []
    selections = report.get("path_selection", {})
    if isinstance(selections, dict) and selections:
        lines.append("Path selection:")
        for name, selection in selections.items():
            if not isinstance(selection, dict):
                continue
            source = str(selection.get("source", "unknown"))
            environment = selection.get("environment")
            state = selection.get("state")
            suffix = f" ({environment})" if environment else ""
            if state and state != "not-probed":
                suffix += f" [{state}]"
            if selection.get("suite_config_conflict") is True:
                suffix += " [CONFLICT: differs from suite config]"
            lines.append(f"  {name}: {source}{suffix}")

    warnings = report.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        conflicted = ", ".join(
            str(item.get("path"))
            for item in warnings
            if isinstance(item, dict) and item.get("path")
        )
        lines.append(
            "WARNING: environment and suite config select different roots for "
            f"{conflicted}; {conflict_guidance}"
        )
    return lines


def _selection(
    source: str,
    *,
    environment: str | None = None,
    state: str | None = None,
    suite_config_conflict: bool = False,
) -> dict[str, object]:
    selection: dict[str, object] = {
        "source": source,
        "environment": environment,
        "suite_config_conflict": suite_config_conflict,
    }
    if state is not None:
        selection["state"] = state
    return selection


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve(strict=False) == right.expanduser().resolve(
            strict=False
        )
    except (OSError, RuntimeError):
        return os.path.normcase(str(left)) == os.path.normcase(str(right))
