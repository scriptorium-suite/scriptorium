"""Read-only installation diagnostics for the Scriptorium suite."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from . import __version__
from .demo import (
    PASSTHROUGH_ENV_NAMES,
    DemoError,
    find_script,
    load_compatibility,
    resolve_component_root,
)
from .host import HostInstallError, inspect_host_installation
from .init import parse_project_frontmatter
from .path_selection import format_path_selection, resolve_codex_home


TARGETS = ("demo", "public-alpha")
PROBE_TIMEOUT_SECONDS = 5
PROVENANCE_BASE_COMMANDS = (
    "prov-ingest-library",
    "prov-ingest-vault",
    "prov-search",
    "prov-mcp",
)
STATUS_LABELS = {
    "pass": "PASS",
    "warn": "WARN",
    "fail": "FAIL",
    "info": "INFO",
    "manual": "MANUAL",
}


class DoctorError(RuntimeError):
    """A failure in the diagnostic command itself, rather than a failed check."""


def _check(
    check_id: str,
    *,
    target: str,
    required_for: tuple[str, ...] = (),
    passed: bool,
    summary: str,
    details: dict[str, Any] | None = None,
    remediation: str | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    if manual:
        status = "manual"
    elif passed:
        status = "pass"
    elif target in required_for:
        status = "fail"
    else:
        status = "info"
    return {
        "id": check_id,
        "status": status,
        "required_for": list(required_for),
        "summary": summary,
        "details": details or {},
        "remediation": None if status == "pass" else remediation,
        "network": {
            "suite_managed_action": "not-requested",
            "os_egress": "not-observed",
        },
    }


def _probe_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in PASSTHROUGH_ENV_NAMES
    }
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    if extra:
        env.update(extra)
    return env


def _run_probe(
    command: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=_probe_environment(extra_env),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DoctorError(f"probe could not run: {Path(command[0]).name}: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise DoctorError(
            f"probe returned exit {completed.returncode}: {Path(command[0]).name}"
        )
    return completed.stdout.strip()


def _project_metadata(root: Path) -> dict[str, str]:
    try:
        with open(root / "pyproject.toml", "rb") as handle:
            project = tomllib.load(handle)["project"]
        return {"name": str(project["name"]), "version": str(project["version"])}
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise DoctorError(f"cannot read project metadata from {root}") from exc


def _platform_check(target: str) -> dict[str, Any]:
    system = platform.system()
    return _check(
        "platform.windows",
        target=target,
        required_for=("public-alpha",),
        passed=system == "Windows",
        summary=(
            f"Windows release target detected ({platform.release()})"
            if system == "Windows"
            else f"{system} is outside the first Public Alpha support target"
        ),
        details={"system": system, "release": platform.release()},
        remediation="Use Windows for the first supported Public Alpha deployment.",
    )


def _python_check(target: str) -> dict[str, Any]:
    version = platform.python_version()
    passed = sys.version_info >= (3, 11)
    return _check(
        "runtime.python",
        target=target,
        required_for=TARGETS,
        passed=passed,
        summary=f"Python {version} at {sys.executable}" if passed else f"Python {version} is below 3.11",
        details={"version": version, "path": sys.executable, "minimum": "3.11"},
        remediation="Install Python 3.11 or 3.12 and recreate the Scriptorium environment.",
    )


def _git_check(target: str) -> dict[str, Any]:
    command = shutil.which("git")
    version = None
    if command:
        try:
            version = _run_probe([command, "--version"])
            if not version.startswith("git version "):
                raise DoctorError("Git returned an unrecognized version string")
        except DoctorError:
            command = None
            version = None
    return _check(
        "runtime.git",
        target=target,
        required_for=("public-alpha",),
        passed=bool(command),
        summary=version or "Git is unavailable or its version probe failed",
        details={"path": command, "version": version},
        remediation="Install Git for Windows and make git.exe available on PATH.",
    )


def _resolve_component(name: str, explicit: Path | None) -> tuple[Path | None, str | None]:
    try:
        return resolve_component_root(name, explicit), None
    except (DemoError, OSError, RuntimeError) as exc:
        return None, str(exc)


def _spec_check(
    target: str,
    expected_version: str,
    explicit: Path | None,
) -> tuple[dict[str, Any], Path | None]:
    root, error = _resolve_component("scriptorium-spec", explicit)
    if root is None:
        return (
            _check(
                "component.spec",
                target=target,
                required_for=TARGETS,
                passed=False,
                summary="scriptorium-spec source checkout was not found",
                details={"expected_version": expected_version, "error": error},
                remediation="Clone scriptorium-spec beside the umbrella or pass --spec-root.",
            ),
            None,
        )

    errors: list[str] = []
    metadata: dict[str, str] = {}
    try:
        metadata = _project_metadata(root)
        identity_ok = metadata["name"] == "scriptorium-spec"
        version_ok = metadata["version"] == expected_version
        if not identity_ok:
            errors.append(f"unexpected project name {metadata['name']!r}")
        if not version_ok:
            errors.append(f"source version {metadata['version']} != {expected_version}")
        if identity_ok and version_ok:
            validator = root / "tools" / "validate.py"
            example = root / "examples" / "library-kb.v1.1.example.json"
            if not validator.is_file() or not example.is_file():
                errors.append("validator smoke-test files are missing")
            else:
                _run_probe([sys.executable, str(validator), str(example)], cwd=root)
    except DoctorError as exc:
        errors.append(str(exc))
    passed = not errors
    return (
        _check(
            "component.spec",
            target=target,
            required_for=TARGETS,
            passed=passed,
            summary=(
                f"scriptorium-spec {metadata.get('version')} validator smoke passed"
                if passed
                else "; ".join(errors)
            ),
            details={
                "path": str(root),
                "expected_version": expected_version,
                "source_version": metadata.get("version"),
            },
            remediation="Use the compatible scriptorium-spec checkout and restore tools/validate.py plus its example.",
        ),
        root,
    )


def _steward_check(
    target: str,
    expected_version: str,
    explicit: Path | None,
) -> tuple[dict[str, Any], Path | None]:
    root, error = _resolve_component("steward", explicit)
    if root is None:
        return (
            _check(
                "component.steward",
                target=target,
                required_for=TARGETS,
                passed=False,
                summary="Steward source checkout was not found; Literature profile is unavailable",
                details={"expected_version": expected_version, "error": error},
                remediation="Clone Steward beside the umbrella or pass --steward-root, then install it.",
            ),
            None,
        )

    errors: list[str] = []
    metadata: dict[str, str] = {}
    runtime_version = None
    command = None
    try:
        metadata = _project_metadata(root)
        identity_ok = metadata["name"] == "scriptorium-steward"
        version_ok = metadata["version"] == expected_version
        if not identity_ok:
            errors.append(f"unexpected project name {metadata['name']!r}")
        if not version_ok:
            errors.append(f"source version {metadata['version']} != {expected_version}")
        if identity_ok and version_ok:
            command = find_script(root, "steward")
            output = _run_probe([str(command), "--version"], cwd=root)
            match = re.fullmatch(r"steward\s+(\d+\.\d+\.\d+)\s*", output)
            if not match:
                errors.append("runtime returned an unrecognized version string")
            else:
                runtime_version = match.group(1)
                if runtime_version != expected_version:
                    errors.append(f"runtime version {runtime_version} != {expected_version}")
    except (DemoError, DoctorError) as exc:
        errors.append(str(exc))
    passed = not errors
    return (
        _check(
            "component.steward",
            target=target,
            required_for=TARGETS,
            passed=passed,
            summary=(
                f"Steward source/runtime {expected_version} matched"
                if passed
                else "; ".join(errors)
            ),
            details={
                "path": str(root),
                "command": str(command) if command else None,
                "expected_version": expected_version,
                "source_version": metadata.get("version"),
                "runtime_version": runtime_version,
            },
            remediation="Install the compatible Steward checkout into its or the active virtual environment.",
        ),
        root,
    )


def _provenance_runtime_version(command: Path, root: Path) -> str:
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        separators=(",", ":"),
    ) + "\n"
    output = _run_probe(
        [str(command)],
        cwd=root,
        input_text=request,
        extra_env={"PROVENANCE_HOME": os.devnull, "PROVENANCE_VAULT": os.devnull},
    )
    for line in output.splitlines():
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(response, dict) or response.get("id") != 0:
            continue
        if response.get("jsonrpc") != "2.0" or "error" in response:
            continue
        result = response.get("result")
        if not isinstance(result, dict):
            continue
        server = result.get("serverInfo")
        if not isinstance(server, dict):
            continue
        if server.get("name") == "provenance" and server.get("version"):
            return str(server["version"])
    raise DoctorError("Provenance MCP initialize returned no valid runtime version")


def _provenance_check(
    target: str,
    expected_version: str,
    explicit: Path | None,
) -> tuple[dict[str, Any], Path | None]:
    root, error = _resolve_component("provenance", explicit)
    if root is None:
        return (
            _check(
                "component.provenance",
                target=target,
                required_for=TARGETS,
                passed=False,
                summary="Provenance source checkout was not found",
                details={"expected_version": expected_version, "error": error},
                remediation="Clone Provenance beside the umbrella or pass --provenance-root, then install it.",
            ),
            None,
        )

    errors: list[str] = []
    metadata: dict[str, str] = {}
    commands: dict[str, Path] = {}
    runtime_version = None
    try:
        metadata = _project_metadata(root)
        identity_ok = metadata["name"] == "provenance"
        version_ok = metadata["version"] == expected_version
        if not identity_ok:
            errors.append(f"unexpected project name {metadata['name']!r}")
        if not version_ok:
            errors.append(f"source version {metadata['version']} != {expected_version}")
        if identity_ok and version_ok:
            commands = {
                name: find_script(root, name) for name in PROVENANCE_BASE_COMMANDS
            }
            command_parents = {path.parent.resolve() for path in commands.values()}
            if len(command_parents) != 1:
                errors.append("Provenance commands resolve from different environments")
            if os.name != "nt":
                not_executable = [
                    name for name, path in commands.items() if not os.access(path, os.X_OK)
                ]
                if not_executable:
                    errors.append(
                        "Provenance commands are not executable: " + ", ".join(not_executable)
                    )
            if not errors:
                runtime_version = _provenance_runtime_version(commands["prov-mcp"], root)
                if runtime_version != expected_version:
                    errors.append(f"runtime version {runtime_version} != {expected_version}")
    except (DemoError, DoctorError) as exc:
        errors.append(str(exc))
    passed = not errors
    return (
        _check(
            "component.provenance",
            target=target,
            required_for=TARGETS,
            passed=passed,
            summary=(
                f"Provenance source/runtime {expected_version} and four public commands matched"
                if passed
                else "; ".join(errors)
            ),
            details={
                "path": str(root),
                "commands": {name: str(path) for name, path in commands.items()},
                "expected_version": expected_version,
                "source_version": metadata.get("version"),
                "runtime_version": runtime_version,
            },
            remediation=(
                "Install the compatible Provenance checkout in one environment "
                "and expose all four public commands."
            ),
        ),
        root,
    )


def _workspace_check(target: str, explicit: Path | None) -> dict[str, Any]:
    configured = explicit
    if configured is None:
        value = os.environ.get("SCRIPTORIUM_WORKSPACE") or os.environ.get("PROVENANCE_VAULT")
        configured = Path(value) if value else None
    try:
        resolved = configured.expanduser().resolve() if configured else None
    except (OSError, RuntimeError):
        resolved = None
    projects_path = resolved / "Projects" if resolved else None
    project_note = None
    if projects_path and projects_path.is_dir() and os.access(projects_path, os.R_OK):
        try:
            candidates = projects_path.glob("*.md")
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    with open(candidate, "rb") as handle:
                        project = parse_project_frontmatter(handle.read(65536))
                except OSError:
                    continue
                if project is not None:
                    project_note = candidate
                    break
        except OSError:
            project_note = None
    passed = project_note is not None
    return _check(
        "workspace.markdown",
        target=target,
        required_for=("public-alpha",),
        passed=passed,
        summary=(
            f"Compatible Markdown workspace detected at {resolved}"
            if passed
            else "No workspace with a compatible Projects/*.md project note was selected"
        ),
        details={
            "path": str(resolved) if resolved else None,
            "projects_path": str(projects_path) if projects_path else None,
            "project_note": str(project_note) if project_note else None,
            "markdown_detected": passed,
            "write_access": "not tested",
        },
        remediation=(
            "Pass --workspace PATH with a Projects/*.md note using complete "
            "project/1.x frontmatter."
        ),
    )


def _provenance_home_check(
    target: str,
    explicit: Path | None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    configured = explicit
    if configured is None:
        value = os.environ.get("PROVENANCE_HOME")
        configured = Path(value) if value else None
    resolved = None
    resolved_workspace = None
    access_ok = False
    separation_conflict = False
    error = None
    if configured is not None:
        try:
            resolved = configured.expanduser().resolve(strict=True)
            if not resolved.is_dir():
                error = "configured path is not a directory"
            elif not os.access(resolved, os.R_OK | os.W_OK):
                error = "configured directory is not readable and writable"
            else:
                access_ok = True
        except (OSError, RuntimeError) as exc:
            error = f"configured path cannot be resolved: {type(exc).__name__}"
    else:
        error = "no explicit Provenance data root was selected"

    if workspace is not None:
        try:
            resolved_workspace = workspace.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            resolved_workspace = None
    if resolved is not None and resolved_workspace is not None:
        if (
            resolved == resolved_workspace
            or resolved_workspace in resolved.parents
            or resolved in resolved_workspace.parents
        ):
            separation_conflict = True
            error = (
                "Provenance data root and Markdown workspace must be separate, "
                "non-nested directories"
            )

    passed = access_ok and not separation_conflict
    if resolved_workspace is None:
        separation = "not-tested"
    elif separation_conflict:
        separation = "conflict"
    elif resolved is not None:
        separation = "separate"
    else:
        separation = "not-tested"
    return _check(
        "provenance.data-root",
        target=target,
        required_for=("public-alpha",),
        passed=passed,
        summary=(
            f"Explicit Provenance data root is available at {resolved}"
            if passed
            else "No usable explicit Provenance data root was selected"
        ),
        details={
            "path": str(resolved) if resolved else None,
            "source": "argument" if explicit is not None else "environment",
            "workspace": str(resolved_workspace) if resolved_workspace else None,
            "workspace_separation": separation,
            "read_write_access_hint": access_ok,
            "write_probe": "not-run",
            "error": error,
        },
        remediation=(
            "Pass --provenance-home PATH or set PROVENANCE_HOME to an existing, "
            "user-owned data directory separate from and not nested with the Markdown "
            "workspace; Scriptorium never falls back to the cwd."
        ),
    )


def _agent_host_check(target: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    hosts: list[dict[str, str]] = []
    failures: list[str] = []
    for name, command_name in (("codex", "codex"), ("claude-code", "claude")):
        command = shutil.which(command_name)
        if not command:
            continue
        try:
            output = _run_probe([command, "--version"])
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if not lines:
                raise DoctorError(f"{name} returned an empty version string")
            hosts.append({"name": name, "path": command, "version": lines[0]})
        except DoctorError:
            failures.append(name)
    passed = bool(hosts)
    if passed:
        summary = "Detected agent host(s): " + ", ".join(host["name"] for host in hosts)
        if failures:
            summary += "; version probe failed: " + ", ".join(failures)
    elif failures:
        summary = "Agent host command(s) were found but their version probes failed"
    else:
        summary = "Neither Codex nor Claude Code was detected"
    return (
        _check(
            "host.cli",
            target=target,
            required_for=("public-alpha",),
            passed=passed,
            summary=summary,
            details={
                "hosts": hosts,
                "failed_probes": failures,
                "authentication": "not tested",
            },
            remediation="Install Codex or Claude Code, then run its login flow separately.",
        ),
        hosts,
    )


def _host_adapter_check(
    target: str,
    hosts: list[dict[str, str]],
    workspace: Path | None,
) -> dict[str, Any]:
    detected = {host["name"] for host in hosts}
    inspection: dict[str, Any] = {
        "workspace": str(workspace) if workspace else None,
        "manifest": {"path": None, "state": "missing"},
        "hosts": [],
    }
    error = None
    if workspace is not None:
        try:
            inspection = inspect_host_installation(workspace=workspace)
        except HostInstallError as exc:
            error = str(exc)
    adapters = inspection.get("hosts", [])
    for adapter in adapters:
        adapter["cli_detected"] = adapter.get("name") in detected
    ready = [
        adapter["name"]
        for adapter in adapters
        if adapter.get("state") == "canonical" and adapter.get("name") in detected
    ]
    if ready:
        summary = "Canonical adapter matched detected host(s): " + ", ".join(ready)
    elif workspace is None:
        summary = "No workspace was selected for project-scoped host adapter inspection"
    elif error:
        summary = "Packaged host adapter could not be inspected"
    elif detected:
        summary = "No detected agent host has a canonical adapter in the selected workspace"
    else:
        summary = "Host adapter cannot be activated until a supported host CLI is detected"
    return _check(
        "host.adapter",
        target=target,
        required_for=("public-alpha",),
        passed=bool(ready),
        summary=summary,
        details={
            "detected_hosts": sorted(detected),
            "ready_hosts": ready,
            "workspace": inspection.get("workspace"),
            "manifest": inspection.get("manifest"),
            "adapters": adapters,
            "error": error or inspection.get("error"),
            "live_discovery": "not-tested",
        },
        remediation=(
            "Run `scriptorium host install codex --workspace PATH` for Codex, or "
            "replace `codex` with `claude-code`; then open or restart that host."
        ),
    )


def _codex_scan_location() -> dict[str, Any]:
    requested, source, state = resolve_codex_home()
    if requested is None:
        return {
            "root": None,
            "source": source,
            "sessions": None,
            "state": state,
            "root_exists": False,
            "sessions_exist": False,
        }
    try:
        root = requested.resolve(strict=False)
    except (OSError, RuntimeError):
        root = requested
    sessions = root / "sessions"
    return {
        "root": str(root),
        "source": source,
        "sessions": str(sessions),
        "state": state,
        "root_exists": root.is_dir(),
        "sessions_exist": sessions.is_dir(),
    }


def _agent_capture_check(
    target: str,
    host_adapter: dict[str, Any],
) -> dict[str, Any]:
    ready_hosts = set(host_adapter.get("details", {}).get("ready_hosts", []))
    codex_ready = "codex" in ready_hosts
    claude_ready = "claude-code" in ready_hosts
    manual = claude_ready and not codex_ready
    codex_scan = _codex_scan_location()
    codex_home_unavailable = bool(
        codex_ready and codex_scan["state"] != "ready"
    )
    if codex_home_unavailable:
        summary = (
            "Codex log home is unavailable; capture will report zero sessions until "
            "Codex creates it or the configured location is corrected"
        )
    elif codex_ready:
        summary = (
            "Codex capture can use the explicit local-log scan in `scriptorium pull` "
            f"from {codex_scan['root']}"
        )
    elif claude_ready:
        summary = (
            "Claude Code skill is installed, but its opt-in SessionEnd enqueue hook "
            "requires separate live verification"
        )
    else:
        summary = "No canonical host adapter provides a verified session capture path"
    return _check(
        "capture.agent-session",
        target=target,
        required_for=("public-alpha",),
        passed=codex_ready,
        manual=manual,
        summary=summary,
        details={
            "ready_hosts": sorted(ready_hosts),
            "codex_scan": (
                "configured-home-missing"
                if codex_home_unavailable
                and codex_scan["source"] == "CODEX_HOME"
                and codex_scan["state"] == "missing"
                else "home-unavailable"
                if codex_home_unavailable
                else "available"
                if codex_ready
                else "unavailable"
            ),
            "codex_scan_location": codex_scan,
            "action_required": (
                "run Codex once to create its log home or correct CODEX_HOME"
                if codex_home_unavailable
                else None
            ),
            "claude_session_end": "manual-verification" if claude_ready else "not-selected",
            "trigger_parity_claimed": False,
        },
        remediation=(
            "Use the canonical Codex adapter for the first executable capture path, "
            "or separately configure and live-verify Provenance's opt-in Claude Code "
            "SessionEnd enqueue hook."
        ),
    )


def _entry_pull_check(
    target: str,
    provenance_root: Path | None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    command = None
    baseline_commands: dict[str, Path] = {}
    command_parent = None
    baseline_parent = None
    capabilities: dict[str, Any] | None = None
    error = None
    required_capabilities = {
        "dry_run_default": True,
        "explicit_run": True,
        "structured_report": True,
        "pull_lock": True,
        "codex_scan": True,
        "applies_checked_approvals": True,
        "model_calls": False,
        "installs_hooks": False,
        "network_requests": False,
    }
    try:
        if provenance_root is None:
            raise DoctorError("compatible Provenance source checkout is unavailable")
        command = find_script(provenance_root, "prov-sync-pull")
        baseline_commands = {
            name: find_script(provenance_root, name)
            for name in PROVENANCE_BASE_COMMANDS
        }
        baseline_parents = {
            path.parent.resolve() for path in baseline_commands.values()
        }
        if len(baseline_parents) != 1:
            raise DoctorError(
                "baseline Provenance commands resolve from different environments"
            )
        baseline_parent = next(iter(baseline_parents))
        command_parent = command.parent.resolve()
        if command_parent != baseline_parent:
            raise DoctorError(
                "public pull command resolves from a different Provenance environment"
            )
        output = _run_probe(
            [str(command), "--capabilities", "--json"],
            cwd=provenance_root,
            extra_env={"PROVENANCE_HOME": os.devnull, "PROVENANCE_VAULT": os.devnull},
        )
        payload = json.loads(output)
        if not isinstance(payload, dict):
            raise DoctorError("pull capability probe did not return a JSON object")
        generated_by = payload.get("generated_by")
        capabilities = payload.get("capabilities")
        if (
            payload.get("format_version") != 1
            or payload.get("operation") != "pull.capabilities"
            or payload.get("status") != "ok"
            or type(payload.get("exit_code")) is not int
            or payload.get("exit_code") != 0
            or not isinstance(generated_by, dict)
            or generated_by.get("name") != "provenance"
            or (
                expected_version is not None
                and generated_by.get("version") != expected_version
            )
            or not isinstance(capabilities, dict)
        ):
            raise DoctorError("pull capability probe returned an incompatible shape")
        mismatched = [
            name
            for name, expected in required_capabilities.items()
            if capabilities.get(name) is not expected
        ]
        if mismatched:
            raise DoctorError(
                "pull capability probe is missing guarantees: " + ", ".join(mismatched)
            )
    except (DemoError, DoctorError, json.JSONDecodeError, OSError, RuntimeError) as exc:
        error = str(exc)
    passed = error is None
    return _check(
        "entry.pull",
        target=target,
        required_for=("public-alpha",),
        passed=passed,
        summary=(
            "Machine-readable on-demand pull capability probe passed"
            if passed
            else "The on-demand pull entry is unavailable or lacks required guarantees"
        ),
        details={
            "command": str(command) if command else None,
            "command_parent": str(command_parent) if command_parent else None,
            "baseline_commands": {
                name: str(path) for name, path in baseline_commands.items()
            },
            "baseline_command_parent": (
                str(baseline_parent) if baseline_parent else None
            ),
            "implemented": passed,
            "expected_version": expected_version,
            "capabilities": capabilities,
            "error": error,
        },
        remediation=(
            "Install the compatible Provenance build exposing `prov-sync-pull`, then "
            "reinstall the Scriptorium suite entry in the same environment."
        ),
    )


def _registry_application(executable: str) -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    views = (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            candidate = Path(os.path.expandvars(str(value))).expanduser()
            if candidate.is_file():
                return candidate.resolve()
    return None


def _find_application(executable: str, candidates: tuple[Path, ...]) -> Path | None:
    command = shutil.which(executable)
    if command:
        return Path(command).resolve()
    registered = _registry_application(executable)
    if registered:
        return registered
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _environment_path(name: str, *parts: str) -> Path:
    root = os.environ.get(name)
    return Path(root, *parts) if root else Path(os.devnull) / Path(*parts)


def _application_checks(target: str) -> list[dict[str, Any]]:
    powerpoint = _find_application(
        "POWERPNT.EXE",
        (
            _environment_path("ProgramFiles", "Microsoft Office", "root", "Office16", "POWERPNT.EXE"),
            _environment_path("ProgramFiles(x86)", "Microsoft Office", "root", "Office16", "POWERPNT.EXE"),
        ),
    )
    zotero = _find_application(
        "zotero.exe",
        (
            _environment_path("ProgramFiles", "Zotero", "zotero.exe"),
            _environment_path("ProgramFiles(x86)", "Zotero", "zotero.exe"),
            _environment_path("LOCALAPPDATA", "Programs", "Zotero", "zotero.exe"),
        ),
    )
    obsidian = _find_application(
        "Obsidian.exe",
        (
            _environment_path("LOCALAPPDATA", "Obsidian", "Obsidian.exe"),
            _environment_path("LOCALAPPDATA", "Programs", "Obsidian", "Obsidian.exe"),
        ),
    )
    return [
        _check(
            "integration.powerpoint",
            target=target,
            passed=bool(powerpoint),
            summary=(
                f"PowerPoint detected at {powerpoint}"
                if powerpoint
                else "PowerPoint was not detected; .pptx generation can still work"
            ),
            details={"path": str(powerpoint) if powerpoint else None, "launch_test": "not run"},
            remediation="Install PowerPoint only if local editing or PowerPoint-specific QA is needed.",
        ),
        _check(
            "integration.zotero",
            target=target,
            passed=bool(zotero),
            summary=(
                f"Zotero detected at {zotero}"
                if zotero
                else "Zotero was not detected; file-based literature workflows remain available"
            ),
            details={"path": str(zotero) if zotero else None, "local_api": "not tested"},
            remediation="Install Zotero only when direct library governance is desired.",
        ),
        _check(
            "integration.obsidian",
            target=target,
            passed=bool(obsidian),
            summary=(
                f"Obsidian detected at {obsidian}"
                if obsidian
                else "Obsidian was not detected; plain Markdown remains fully supported"
            ),
            details={"path": str(obsidian) if obsidian else None, "plugins": "not tested"},
            remediation="Install Obsidian only if its navigation and plugin experience is desired.",
        ),
    ]


def _resolve_lectern_root(explicit: Path | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    else:
        configured = os.environ.get("SCRIPTORIUM_LECTERN_ROOT")
        if configured and configured.strip():
            candidates.append(Path(configured))
        else:
            source_parent = Path(__file__).resolve().parents[2].parent
            candidates.extend(
                [
                    source_parent / "Academic-Slides-Agent",
                    Path.cwd() / "Academic-Slides-Agent",
                    Path.cwd().parent / "Academic-Slides-Agent",
                ]
            )
    for candidate in candidates:
        try:
            root = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if (root / "apps" / "cli" / "pyproject.toml").is_file():
            return root
    return None


def _lectern_check(target: str, explicit: Path | None) -> tuple[dict[str, Any], Path | None]:
    root = _resolve_lectern_root(explicit)
    command = None
    version = None
    errors: list[str] = []
    if root:
        try:
            metadata = _project_metadata(root / "apps" / "cli")
            if metadata["name"] != "asa-cli":
                errors.append(f"unexpected project name {metadata['name']!r}")
            else:
                version = metadata["version"]
                command = find_script(root, "lectern")
        except (DemoError, DoctorError) as exc:
            errors.append(str(exc))
    else:
        errors.append("Lectern source checkout was not found")
    passed = bool(root and command and not errors)
    return (
        _check(
            "integration.lectern",
            target=target,
            passed=passed,
            summary=(
                f"Lectern {version} command detected at {command}"
                if passed
                else "; ".join(errors)
            ),
            details={
                "path": str(root) if root else None,
                "command": str(command) if command else None,
                "source_version": version,
                "provider": "not tested",
            },
            remediation="Clone Academic-Slides-Agent and install apps/cli to enable the Slides profile.",
        ),
        root,
    )


def _browser_bundle_errors(manifest: Path) -> list[str]:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["manifest.json is not readable JSON"]
    if not isinstance(data, dict):
        return ["manifest.json must contain an object"]

    errors: list[str] = []
    if data.get("manifest_version") != 3:
        errors.append("manifest_version must be 3")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        errors.append("name is missing")
    version = data.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){0,3}", version):
        errors.append("version must contain one to four numeric components")

    root = manifest.parent.resolve()

    def check_entry(value: Any, label: str) -> None:
        if not isinstance(value, str) or not value:
            errors.append(f"{label} is missing")
            return
        try:
            entry = (root / value).resolve()
            entry.relative_to(root)
        except (OSError, ValueError):
            errors.append(f"{label} is outside the extension bundle")
            return
        if not entry.is_file():
            errors.append(f"{label} does not exist")

    scripts = data.get("content_scripts")
    if not isinstance(scripts, list) or not scripts:
        errors.append("content_scripts is missing")
    else:
        for index, script in enumerate(scripts):
            if not isinstance(script, dict):
                errors.append(f"content_scripts[{index}] must be an object")
                continue
            matches = script.get("matches")
            if (
                not isinstance(matches, list)
                or not matches
                or not all(isinstance(value, str) and value for value in matches)
            ):
                errors.append(f"content_scripts[{index}].matches is missing")
            javascript = script.get("js")
            if not isinstance(javascript, list) or not javascript:
                errors.append(f"content_scripts[{index}].js is missing")
                continue
            for script_index, value in enumerate(javascript):
                check_entry(value, f"content_scripts[{index}].js[{script_index}]")

    action = data.get("action")
    popup = action.get("default_popup") if isinstance(action, dict) else None
    check_entry(popup, "action.default_popup")
    return errors


def _browser_extension_check(target: str, provenance_root: Path | None) -> dict[str, Any]:
    manifest = provenance_root / "01-capture" / "manifest.json" if provenance_root else None
    validation_errors = _browser_bundle_errors(manifest) if manifest and manifest.is_file() else []
    available = bool(manifest and manifest.is_file() and not validation_errors)
    if available:
        summary = (
            "Browser extension bundle passed static validation; Chrome installation, "
            "permissions, and login require manual verification"
        )
        remediation = (
            "Load Provenance/01-capture as an unpacked extension only when "
            "web-history capture is needed."
        )
    elif manifest and manifest.is_file():
        summary = "Browser extension bundle failed static validation: " + "; ".join(
            validation_errors
        )
        remediation = "Repair the Provenance browser-extension bundle before loading it."
    else:
        summary = "Browser extension bundle was not found; local agent-log capture is unaffected"
        remediation = (
            "Install the optional Provenance browser-extension bundle only when "
            "web-history capture is needed."
        )
    return _check(
        "capture.browser-extension",
        target=target,
        passed=available,
        manual=available,
        summary=summary,
        details={
            "manifest": str(manifest) if manifest and manifest.is_file() else None,
            "bundle_validation": "passed" if available else validation_errors or "not found",
            "browser_installation": "not tested",
            "scope": "ChatGPT and Claude web history only",
        },
        remediation=remediation,
    )


def _egress(hosts: list[dict[str, str]]) -> list[dict[str, Any]]:
    zotero_configured = bool(
        os.environ.get("ZOTERO_API_KEY")
        or os.environ.get("ZOTERO_LOCAL", "").lower() in {"1", "true", "yes"}
    )
    return [
        {
            "owner": "scriptorium-doctor",
            "status": "not-requested",
            "detail": (
                "No suite-managed network action or GUI launch is requested; "
                "OS-level subprocess egress is not observed."
            ),
        },
        {
            "owner": "agent-host",
            "status": "detected" if hosts else "not-detected",
            "detail": "Model egress is host-managed when the user invokes an agent; authentication was not tested.",
        },
        {
            "owner": "steward",
            "status": "environment-detected" if zotero_configured else "not-detected",
            "detail": (
                "Only the environment layer was inspected. Zotero access occurs only "
                "when configured; secret values are never reported."
            ),
        },
        {
            "owner": "lectern",
            "status": "not-tested",
            "detail": "Provider and parser egress are Lectern-managed and were not invoked.",
        },
    ]


def _readiness(checks: list[dict[str, Any]], target: str) -> str:
    missing = [
        check
        for check in checks
        if target in check["required_for"] and check["status"] != "pass"
    ]
    return "incomplete" if missing else "ready"


def _check_status(checks: list[dict[str, Any]], check_id: str) -> str | None:
    for check in checks:
        if check["id"] == check_id:
            return check["status"]
    return None


def _build_report(
    target: str,
    checks: list[dict[str, Any]],
    egress: list[dict[str, Any]],
) -> dict[str, Any]:
    demo_readiness = _readiness(checks, "demo")
    public_readiness = _readiness(checks, "public-alpha")
    selected_readiness = _readiness(checks, target)
    if selected_readiness == "incomplete":
        status = "incomplete"
        exit_code = 1
    else:
        status = "ready"
        exit_code = 0

    steward = _check_status(checks, "component.steward")
    zotero = _check_status(checks, "integration.zotero")
    lectern = _check_status(checks, "integration.lectern")
    browser = _check_status(checks, "capture.browser-extension")
    literature = "unavailable" if steward != "pass" else (
        "detected" if zotero == "pass" else "file-only"
    )
    slides = "detected" if lectern == "pass" else "unavailable"
    web_history = "manual" if browser == "manual" else "unavailable"

    counts = {name: 0 for name in STATUS_LABELS}
    for check in checks:
        counts[check["status"]] += 1
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "target": target,
        "status": status,
        "exit_code": exit_code,
        "readiness": {
            "demo": demo_readiness,
            "public_alpha": public_readiness,
            "literature": literature,
            "slides": slides,
            "web_history": web_history,
        },
        "checks": checks,
        "egress": egress,
        "summary": counts,
        "limitations": [
            "Agent authentication and a live model call are not tested.",
            "GUI applications are detected without launching them.",
            "Browser-extension installation and permissions require manual verification.",
            "Workspace write access and OS-level network behavior are not observed.",
            "The JSON report contains local filesystem paths; review it before sharing.",
        ],
    }


def run_doctor(
    *,
    target: str = "public-alpha",
    spec_root: Path | None = None,
    steward_root: Path | None = None,
    provenance_root: Path | None = None,
    lectern_root: Path | None = None,
    workspace: Path | None = None,
    provenance_home: Path | None = None,
) -> dict[str, Any]:
    if target not in TARGETS:
        raise DoctorError(f"unsupported doctor target: {target}")
    try:
        expected = load_compatibility()
        for component in ("scriptorium-spec", "steward", "provenance"):
            if component not in expected:
                raise KeyError(component)
    except (AttributeError, KeyError, OSError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise DoctorError("packaged compatibility manifest is invalid") from exc

    checks = [_platform_check(target), _python_check(target), _git_check(target)]
    spec, _ = _spec_check(target, expected["scriptorium-spec"], spec_root)
    steward, _ = _steward_check(target, expected["steward"], steward_root)
    provenance, resolved_provenance = _provenance_check(
        target, expected["provenance"], provenance_root
    )
    workspace_check = _workspace_check(target, workspace)
    workspace_path = (
        Path(workspace_check["details"]["path"])
        if workspace_check["details"].get("path")
        else None
    )
    provenance_home_check = _provenance_home_check(
        target, provenance_home, workspace_path
    )
    checks.extend([spec, provenance, steward, workspace_check, provenance_home_check])
    agent, hosts = _agent_host_check(target)
    host_adapter = _host_adapter_check(target, hosts, workspace_path)
    checks.extend(
        [
            agent,
            host_adapter,
            _agent_capture_check(target, host_adapter),
            _entry_pull_check(target, resolved_provenance, expected["provenance"]),
        ]
    )
    lectern, _ = _lectern_check(target, lectern_root)
    checks.append(lectern)
    checks.extend(_application_checks(target))
    checks.append(_browser_extension_check(target, resolved_provenance))
    return _build_report(target, checks, _egress(hosts))


def format_doctor_report(report: dict[str, Any]) -> str:
    lines = [
        f"Scriptorium doctor {report['generated_by']['version']} -- target: {report['target']}",
        (
            "Network: no suite-managed action requested; "
            "OS-level subprocess egress not observed"
        ),
    ]
    selection_lines = format_path_selection(
        report,
        conflict_guidance="review the selected environment values.",
    )
    if selection_lines:
        lines.extend(["", *selection_lines])
    lines.append("")
    for check in report["checks"]:
        lines.append(
            f"{STATUS_LABELS[check['status']]:<5} {check['id']:<30} {check['summary']}"
        )
        if check["status"] in {"fail", "warn"} and check.get("remediation"):
            lines.append(f"      Fix: {check['remediation']}")
        elif check["status"] == "manual" and check.get("remediation"):
            lines.append(f"      Action: {check['remediation']}")
        elif check["status"] == "info" and check.get("remediation"):
            lines.append(f"      Next: {check['remediation']}")
    readiness = report["readiness"]
    lines.extend(
        [
            "",
            f"Demo readiness:         {readiness['demo'].upper()}",
            f"Public Alpha readiness: {readiness['public_alpha'].upper()}",
            f"Literature capability:  {readiness['literature'].upper()}",
            f"Slides capability:      {readiness['slides'].upper()}",
            f"Web-history capability: {readiness['web_history'].upper()}",
            "",
            (
                f"Result: {report['status'].upper()} -- "
                f"{report['summary']['fail']} failed, "
                f"{report['summary']['warn']} check warnings, "
                f"{len(report.get('warnings', []))} path-selection warnings, "
                f"{report['summary']['info']} informational, "
                f"{report['summary']['manual']} manual"
            ),
        ]
    )
    lines.extend(["", "Not tested:"])
    lines.extend(f"  - {limitation}" for limitation in report["limitations"])
    return "\n".join(lines)
