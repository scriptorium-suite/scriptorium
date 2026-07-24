#!/usr/bin/env python3
"""Isolated cross-repository E2E for the public init-to-pull loop.

Run from the Scriptorium repository with the current Python interpreter:

    python tests/e2e_pull.py

The test launches ``python -m scriptorium`` as a user would. It does not import
Provenance internals, install packages, access a real agent profile, or make
network requests.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPTORIUM_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SCRIPTORIUM_ROOT / "src"
DEFAULT_PROVENANCE_ROOT = SCRIPTORIUM_ROOT.parent / "Provenance"
BEGIN_MARKER = "<!-- scriptorium:progress-log:begin -->"
END_MARKER = "<!-- scriptorium:progress-log:end -->"
HUMAN_TEXT = "Human-owned research rationale must remain byte-identical."
TRAILING_HUMAN_TEXT = "Human-owned trailing notes must also remain untouched."
TIMELINE_FACT = "Captured the synthetic Codex research session through the public pull entry."
SECOND_TIMELINE_FACT = "Continued the synthetic project from the approved context capsule."
CONCLUSION = "The synthetic research loop is ready for product-level validation."
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


class E2EFailure(RuntimeError):
    """The public pull flow violated an externally observable invariant."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise E2EFailure(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise E2EFailure(f"Expected valid JSON at {path}") from exc
    if not isinstance(value, dict):
        raise E2EFailure(f"Expected a JSON object at {path}")
    return value


def string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for key, item in value.items():
            values.extend(string_values(key))
            values.extend(string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(string_values(item))
        return values
    return []


def contains_key(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return expected in value or any(
            contains_key(item, expected) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_key(item, expected) for item in value)
    return False


def require_private_values_absent(
    value: Any, *, label: str, forbidden: list[str | Path]
) -> None:
    normalized_values = [item.replace("\\", "/").casefold() for item in string_values(value)]
    for candidate in forbidden:
        needle = str(candidate).replace("\\", "/").casefold()
        if needle and any(needle in item for item in normalized_values):
            raise E2EFailure(f"{label} leaked a private session identifier or path")


def snapshot_tree(root: Path) -> dict[str, tuple[Any, ...]]:
    """Capture bytes plus write-relevant metadata, including empty directories."""
    snapshot: dict[str, tuple[Any, ...]] = {}
    candidates = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    for path in candidates:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            snapshot[relative] = ("link", os.readlink(path), metadata.st_mtime_ns)
        elif path.is_dir():
            snapshot[relative] = ("directory", metadata.st_mtime_ns)
        elif path.is_file():
            snapshot[relative] = (
                "file",
                metadata.st_size,
                metadata.st_mtime_ns,
                path.read_bytes(),
            )
        else:
            snapshot[relative] = ("other", metadata.st_mode, metadata.st_mtime_ns)
    return snapshot


def human_regions(note_bytes: bytes) -> tuple[bytes, bytes]:
    """Return body bytes outside the managed progress-log marker pair."""
    newline = b"\r\n" if b"\r\n" in note_bytes else b"\n"
    closing_fence = newline + b"---" + newline
    close_frontmatter = note_bytes.find(closing_fence, 4)
    require(close_frontmatter >= 0, "Project note lost its closing frontmatter fence")
    body = note_bytes[close_frontmatter + len(closing_fence) :]
    begin = body.find(BEGIN_MARKER.encode("utf-8"))
    end = body.find(END_MARKER.encode("utf-8"), begin + len(BEGIN_MARKER))
    require(begin >= 0 and end >= 0, "Project note lost its frozen progress-log markers")
    return body[:begin], body[end + len(END_MARKER.encode("utf-8")) :]


def isolated_environment(base: Path) -> tuple[dict[str, str], Path]:
    profile = base / "profile"
    appdata = profile / "AppData" / "Roaming"
    localappdata = profile / "AppData" / "Local"
    codex_home = profile / ".codex"
    runtime_temp = base / "runtime-temp"
    fake_bin = base / "fake-bin"
    for directory in (
        profile,
        appdata,
        localappdata,
        codex_home,
        runtime_temp,
        fake_bin,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        fake_codex = fake_bin / "codex.cmd"
        fake_codex.write_text(
            "@echo off\r\necho codex-cli e2e\r\n",
            encoding="ascii",
        )
    else:
        fake_codex = fake_bin / "codex"
        fake_codex.write_text(
            "#!/bin/sh\nprintf 'codex-cli e2e\\n'\n",
            encoding="ascii",
        )
        fake_codex.chmod(0o755)

    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in SYSTEM_ENV_NAMES
    }
    env.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "CODEX_HOME": str(codex_home),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(localappdata),
            "TEMP": str(runtime_temp),
            "TMP": str(runtime_temp),
            "TMPDIR": str(runtime_temp),
            "PYTHONPATH": str(SOURCE_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "SCRIPTORIUM_CONFIG_DIR": str(base / "config-root"),
        }
    )
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    return env, profile


def invoke_json(
    arguments: list[str],
    *,
    env: dict[str, str],
    expected_exit: int | tuple[int, ...] = 0,
) -> dict[str, Any]:
    command = [sys.executable, "-m", "scriptorium", *arguments]
    try:
        completed = subprocess.run(
            command,
            cwd=SCRIPTORIUM_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise E2EFailure(f"Could not execute: {' '.join(command[:4])}") from exc

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise E2EFailure(
            "Command did not emit one JSON object: "
            f"exit={completed.returncode} stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise E2EFailure("Command JSON output was not an object")
    expected_exits = (
        (expected_exit,) if isinstance(expected_exit, int) else expected_exit
    )
    if completed.returncode not in expected_exits:
        raise E2EFailure(
            "Command returned an unexpected exit code: "
            f"expected={expected_exits} actual={completed.returncode} report={payload!r}"
        )
    require(
        payload.get("exit_code") == completed.returncode,
        "Process and JSON report exit codes disagree",
    )
    return payload


def enqueue_public_event(
    event: dict[str, Any], *, env: dict[str, str], provenance_home: Path
) -> None:
    enqueue_env = dict(env)
    enqueue_env["PROVENANCE_HOME"] = str(provenance_home)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "provenance.synclayer.enqueue"],
            cwd=SCRIPTORIUM_ROOT,
            env=enqueue_env,
            input=json.dumps(event),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise E2EFailure("Could not invoke the public enqueue entry") from exc
    require(completed.returncode == 0, "Public enqueue entry returned a failure")


def inspect_unresolved(
    *, env: dict[str, str], provenance_home: Path, show_paths: bool = False
) -> dict[str, Any]:
    arguments = [
        "--provenance-home",
        str(provenance_home),
        "--json",
    ]
    if show_paths:
        arguments.append("--show-paths")
    return invoke_provenance_json(
        "provenance.synclayer.unresolved", arguments, env=env
    )


def invoke_provenance_json(
    module: str,
    arguments: list[str],
    *,
    env: dict[str, str],
    input_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    command = [sys.executable, "-m", module, *arguments]
    try:
        completed = subprocess.run(
            command,
            cwd=SCRIPTORIUM_ROOT,
            env=env,
            input=(
                json.dumps(input_payload, ensure_ascii=False)
                if input_payload is not None
                else None
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise E2EFailure(f"Could not invoke public Provenance module {module}") from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise E2EFailure(f"{module} did not emit one JSON object") from exc
    require(completed.returncode == 0, f"{module} returned a failure")
    require(isinstance(payload, dict), f"{module} JSON was not an object")
    if "exit_code" in payload:
        require(payload["exit_code"] == 0, f"{module} report was not successful")
    return payload


def read_pending_scaffolds(
    *, env: dict[str, str], provenance_home: Path
) -> list[dict[str, Any]]:
    listing = invoke_provenance_json(
        "provenance.synclayer.summarize_driver",
        ["--provenance-home", str(provenance_home), "--json"],
        env=env,
    )
    summary_ids = listing.get("summary_ids")
    require(isinstance(summary_ids, list), "Pending list omitted summary IDs")
    scaffolds = []
    for summary_id in summary_ids:
        require(isinstance(summary_id, str), "Pending list returned an invalid ID")
        detail = invoke_provenance_json(
            "provenance.synclayer.summarize_driver",
            [
                summary_id,
                "--provenance-home",
                str(provenance_home),
                "--json",
            ],
            env=env,
        )
        scaffold = detail.get("scaffold")
        require(isinstance(scaffold, dict), "Pending detail omitted its scaffold")
        scaffolds.append(scaffold)
    return scaffolds


def pull_arguments(
    *,
    provenance_root: Path,
    run: bool,
    workspace: Path | None = None,
    provenance_home: Path | None = None,
    project: str | None = "alpha",
) -> list[str]:
    arguments = [
        "pull",
        "--provenance-root",
        str(provenance_root),
        "--json",
    ]
    if workspace is not None:
        arguments.extend(["--workspace", str(workspace)])
    if provenance_home is not None:
        arguments.extend(["--provenance-home", str(provenance_home)])
    if project is not None:
        arguments.extend(["--project", project])
    if run:
        arguments.append("--run")
    return arguments


def status_arguments(*, provenance_root: Path) -> list[str]:
    return [
        "status",
        "--provenance-root",
        str(provenance_root),
        "--json",
    ]


def resume_arguments(*, provenance_root: Path) -> list[str]:
    return [
        "resume",
        "--provenance-root",
        str(provenance_root),
        "--json",
    ]


def init_arguments(
    *,
    workspace: Path,
    provenance_home: Path,
    project_id: str,
    title: str,
    host: str,
    linked_repo: Path | None,
    run: bool,
    idea: str | None = None,
) -> list[str]:
    arguments = [
        "init",
        "--workspace",
        str(workspace),
        "--provenance-home",
        str(provenance_home),
        "--project-id",
        project_id,
        "--title",
        title,
        "--host",
        host,
        "--json",
    ]
    if linked_repo is not None:
        arguments.extend(["--linked-repo", str(linked_repo)])
    if idea is not None:
        arguments.extend(["--idea", idea])
    if run:
        arguments.append("--run")
    return arguments


def write_rollout(
    profile: Path,
    linked_repo: Path,
    *,
    session_id: str = "e2e-codex-session",
    filename: str = "rollout-e2e-pull.jsonl",
) -> Path:
    rollout = (
        profile
        / ".codex"
        / "sessions"
        / "2026"
        / "07"
        / "16"
        / filename
    )
    rollout.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rows = [
        {
            "type": "session_meta",
            "timestamp": now.isoformat(),
            "payload": {
                "session_id": session_id,
                "cwd": linked_repo.as_posix(),
            },
        },
        {
            "type": "event_msg",
            "timestamp": now.isoformat(),
            "payload": {
                "type": "user_message",
                "message": "Validate the synthetic research capture loop.",
            },
        },
        {
            "type": "event_msg",
            "timestamp": now.isoformat(),
            "payload": {
                "type": "agent_message",
                "message": "Prepared deterministic local evidence without network access.",
            },
        },
    ]
    rollout.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    stable_time = time.time() - 600
    os.utime(rollout, (stable_time, stable_time))
    return rollout


def submit_fill(
    summary_id: str,
    *,
    env: dict[str, str],
    provenance_home: Path,
    timeline: list[str] | None = None,
    include_high_value_claims: bool = True,
) -> None:
    fixture = {
        "schema_version": "summary-fill/1.0",
        "timeline": timeline or [
            TIMELINE_FACT,
            "Confirmed that preview, execution, and approval remain separate actions.",
        ],
    }
    if include_high_value_claims:
        fixture.update(
            {
                "status": "active",
                "stage": "product validation",
                "next_actions": ["Review the isolated E2E evidence before release."],
                "conclusion": CONCLUSION,
                "blocked_by": "",
                "confidence": "high",
            }
        )
    report = invoke_provenance_json(
        "provenance.synclayer.pending_fill",
        [summary_id, "--provenance-home", str(provenance_home), "--json"],
        env=env,
        input_payload=fixture,
    )
    require(report.get("status") == "accepted", "Public fill command refused the fill")


def run_unresolved_gate_e2e(provenance_root: Path) -> None:
    with tempfile.TemporaryDirectory(
        prefix="scriptorium-unresolved-e2e-"
    ) as temporary:
        base = Path(temporary)
        env, profile = isolated_environment(base)
        workspace = base / "workspace"
        provenance_home = base / "provenance-home"
        workspace.mkdir()
        provenance_home.mkdir()
        private_session = "unresolved-e2e-session"
        private_cwd = base / "unmapped-private-research"
        private_cwd.mkdir()
        transcript = (
            profile
            / ".claude"
            / "projects"
            / "unmapped-private-research"
            / f"{private_session}.jsonl"
        )
        transcript.parent.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        transcript.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False)
                for row in (
                    {
                        "type": "user",
                        "cwd": str(private_cwd),
                        "timestamp": now,
                        "message": {"content": "Preserve this unresolved research session."},
                    },
                    {
                        "type": "assistant",
                        "cwd": str(private_cwd),
                        "timestamp": now,
                        "message": {"content": "Wait for an explicit project mapping."},
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )
        enqueue_public_event(
            {
                "source": "claude-code",
                "session_id": private_session,
                "transcript_path": str(transcript),
                "cwd": str(private_cwd),
                "hook_event_name": "SessionEnd",
                "reason": "exit",
            },
            env=env,
            provenance_home=provenance_home,
        )

        before_preview = snapshot_tree(base)
        preview = invoke_json(
            pull_arguments(
                workspace=workspace,
                provenance_home=provenance_home,
                provenance_root=provenance_root,
                run=False,
            ),
            env=env,
        )
        require(snapshot_tree(base) == before_preview, "Unresolved preview wrote state")
        require(
            preview.get("summary", {}).get("unresolved_events") == 1,
            "Unresolved preview did not report one event",
        )
        require(
            preview.get("summary", {}).get("pending_fill") == 0,
            "Unresolved preview exposed an agent-fill task",
        )
        preview_actions = {
            item.get("type") for item in preview.get("action_required", [])
        }
        require(
            preview_actions == {"run-confirmation", "project-resolution"},
            "Unresolved preview actions changed",
        )

        applied = invoke_json(
            pull_arguments(
                workspace=workspace,
                provenance_home=provenance_home,
                provenance_root=provenance_root,
                run=True,
            ),
            env=env,
        )
        require(applied.get("status") == "action-required", "Unresolved run status changed")
        require(
            applied.get("action_required")
            == [{"type": "project-resolution", "count": 1}],
            "Unresolved run did not stop at project resolution",
        )
        require(
            (provenance_home / "sync-state" / "inflight.jsonl").is_file(),
            "Unresolved event was retired instead of retained",
        )
        require(
            not (provenance_home / "sync-state" / "pending").exists(),
            "Unresolved event created a pending scaffold",
        )
        require(
            not (provenance_home / "sync-state" / "drafts").exists(),
            "Unresolved event created a session-summary draft",
        )
        require_private_values_absent(
            applied,
            label="Unresolved report",
            forbidden=[private_session, private_cwd, base],
        )

        before_inspection = snapshot_tree(base)
        opaque = inspect_unresolved(env=env, provenance_home=provenance_home)
        require(snapshot_tree(base) == before_inspection, "Unresolved inspection wrote state")
        require(opaque.get("count") == 1, "Unresolved inspector did not find the event")
        require(opaque.get("paths") == "suppressed", "Default inspection exposed paths")
        require_private_values_absent(
            opaque,
            label="Default unresolved inspection",
            forbidden=[private_session, transcript, private_cwd, base],
        )
        resolution = opaque.get("unresolved", [{}])[0]
        require(
            set(resolution) == {"resolution_id", "repo_label"},
            "Default unresolved identity was not aggregate-only",
        )

        local = inspect_unresolved(
            env=env, provenance_home=provenance_home, show_paths=True
        )
        require(
            local.get("unresolved", [{}])[0].get("resolution_id")
            == resolution.get("resolution_id"),
            "Explicit local inspection changed the stable resolution id",
        )
        require(
            local.get("unresolved", [{}])[0].get("cwd") == str(private_cwd),
            "Explicit local inspection did not reveal the cwd needed for mapping",
        )
        require_private_values_absent(
            local,
            label="Explicit unresolved inspection",
            forbidden=[private_session, transcript],
        )

        initialized = invoke_json(
            init_arguments(
                workspace=workspace,
                provenance_home=provenance_home,
                project_id="recovered",
                title="Recovered E2E project",
                host="claude-code",
                linked_repo=private_cwd,
                run=True,
            ),
            env=env,
        )
        require(
            initialized.get("status") == "initialized",
            "Public init did not create the recovered project mapping",
        )
        require(
            (workspace / "Projects" / "recovered.md").is_file(),
            "Public init omitted the recovered project note",
        )

        recovered = invoke_json(
            pull_arguments(
                workspace=workspace,
                provenance_home=provenance_home,
                provenance_root=provenance_root,
                run=True,
            ),
            env=env,
        )
        require(
            recovered.get("summary", {}).get("unresolved_events") == 0,
            "Mapped event remained unresolved",
        )
        require(
            recovered.get("summary", {}).get("scaffolded") == 1,
            "Mapped event did not resume into one pending scaffold",
        )
        require(
            recovered.get("action_required")
            == [{"type": "agent-fill", "count": 1}],
            "Mapped event did not resume at the agent-fill boundary",
        )
        scaffolds = read_pending_scaffolds(env=env, provenance_home=provenance_home)
        require(len(scaffolds) == 1, "Mapped event did not create exactly one pending item")
        scaffold = scaffolds[0]
        require(scaffold.get("project") == "recovered", "Recovered scaffold has no project")
        require(
            scaffold.get("session_id") == private_session,
            "A different event was scaffolded after project resolution",
        )
        require(
            inspect_unresolved(env=env, provenance_home=provenance_home).get("count") == 0,
            "Resolved event remained in the unresolved inspection view",
        )


def run_e2e(provenance_root: Path) -> None:
    provenance_root = provenance_root.expanduser().resolve()
    require(
        (provenance_root / "pyproject.toml").is_file(),
        f"Provenance source checkout not found: {provenance_root}",
    )

    with tempfile.TemporaryDirectory(prefix="scriptorium-pull-e2e-") as temporary:
        base = Path(temporary)
        env, profile = isolated_environment(base)
        workspace = base / "workspace"
        provenance_home = base / "provenance-home"
        agent_cwd = workspace

        initial_idea = HUMAN_TEXT
        before_init_preview = snapshot_tree(base)
        init_preview = invoke_json(
            init_arguments(
                workspace=workspace,
                provenance_home=provenance_home,
                project_id="alpha",
                title="Alpha",
                host="codex",
                linked_repo=None,
                idea=initial_idea,
                run=False,
            ),
            env=env,
        )
        require(
            snapshot_tree(base) == before_init_preview,
            "Public init preview changed the isolated filesystem",
        )
        require(init_preview.get("mode") == "preview", "Init preview mode changed")
        require(
            init_preview.get("status") == "planned",
            "Init preview did not produce a write plan",
        )

        initialized = invoke_json(
            init_arguments(
                workspace=workspace,
                provenance_home=provenance_home,
                project_id="alpha",
                title="Alpha",
                host="codex",
                linked_repo=None,
                idea=initial_idea,
                run=True,
            ),
            env=env,
        )
        require(
            initialized.get("status") == "initialized",
            "Public init did not initialize the research project",
        )
        project_note = workspace / "Projects" / "alpha.md"
        config_path = (
            Path(env["SCRIPTORIUM_CONFIG_DIR"])
            / "scriptorium"
            / "config.toml"
        )
        require(project_note.is_file(), "Public init omitted the project note")
        require(config_path.is_file(), "Public init omitted the suite config")
        initialized_project = project_note.read_bytes()
        initialized_config = config_path.read_bytes()
        initialized_tree = snapshot_tree(base)

        repeated_init = invoke_json(
            init_arguments(
                workspace=workspace,
                provenance_home=provenance_home,
                project_id="alpha",
                title="Alpha",
                host="codex",
                linked_repo=None,
                idea=initial_idea,
                run=True,
            ),
            env=env,
        )
        require(
            repeated_init.get("status") == "unchanged",
            "Repeated public init was not idempotent",
        )
        require(
            repeated_init.get("summary", {}).get("create") == 0,
            "Repeated public init planned new managed state",
        )
        require(
            snapshot_tree(base) == initialized_tree,
            "Repeated public init changed initialized state",
        )
        require(
            project_note.read_bytes() == initialized_project,
            "Repeated public init changed the project note bytes",
        )
        require(
            config_path.read_bytes() == initialized_config,
            "Repeated public init changed the suite config bytes",
        )
        with project_note.open("ab") as handle:
            handle.write(f"\n{TRAILING_HUMAN_TEXT}\n".encode("utf-8"))

        rollout = write_rollout(profile, agent_cwd)
        host_report = invoke_json(
            [
                "host",
                "install",
                "codex",
                "--json",
            ],
            env=env,
        )
        require(host_report.get("operation") == "host.install", "Host install envelope changed")

        doctor = invoke_json(
            [
                "doctor",
                "--target",
                "public-alpha",
                "--provenance-root",
                str(provenance_root),
                "--json",
            ],
            env=env,
            expected_exit=(0, 1),
        )
        doctor_checks = {
            item.get("id"): item
            for item in doctor.get("checks", [])
            if isinstance(item, dict)
        }
        for check_id in ("workspace.markdown", "provenance.data-root"):
            require(
                doctor_checks.get(check_id, {}).get("status") == "pass",
                f"Doctor did not accept initialized {check_id}",
            )
        baseline_human = human_regions(project_note.read_bytes())

        before_status = snapshot_tree(base)
        initial_status = invoke_json(
            status_arguments(provenance_root=provenance_root),
            env=env,
        )
        require(
            snapshot_tree(base) == before_status,
            "Status changed the isolated filesystem",
        )
        require(
            initial_status.get("operation") == "status"
            and initial_status.get("status") == "attention",
            "Status did not surface the pending Codex rollout",
        )
        require(
            initial_status.get("workflow", {}).get("codex_found") == 1,
            "Status did not report the pending Codex rollout count",
        )
        require_private_values_absent(
            initial_status,
            label="Status report",
            forbidden=[base, agent_cwd, rollout, "e2e-codex-session"],
        )

        def configured_pull(run: bool) -> list[str]:
            return pull_arguments(
                provenance_root=provenance_root,
                run=run,
                project=None,
            )

        before_preview = snapshot_tree(base)
        preview = invoke_json(
            configured_pull(run=False),
            env=env,
        )
        after_preview = snapshot_tree(base)
        require(before_preview == after_preview, "Preview changed the isolated filesystem")
        require(preview.get("mode") == "preview", "Preview reported the wrong mode")
        require(preview.get("status") == "planned", "Preview did not form an executable plan")
        require(preview.get("entry", {}).get("scan_codex") is True, "Canonical Codex scan was not enabled")
        preview_found = preview.get("summary", {}).get("codex_found")
        require(
            preview_found == 1,
            f"Preview did not discover exactly one rollout (found={preview_found!r})",
        )
        report_private_values = ["e2e-codex-session", base, agent_cwd, rollout]
        require_private_values_absent(
            preview, label="Preview report", forbidden=report_private_values
        )

        first_run = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(first_run.get("summary", {}).get("codex_enqueued") == 1, "First run did not enqueue the rollout")
        require(first_run.get("summary", {}).get("scaffolded") == 1, "First run did not create one scaffold")
        require(first_run.get("summary", {}).get("pending_fill") == 1, "First run did not stop at the agent-fill gate")
        require_private_values_absent(
            first_run, label="First-run report", forbidden=report_private_values
        )

        before_pending_status = snapshot_tree(base)
        pending_status = invoke_json(
            status_arguments(provenance_root=provenance_root),
            env=env,
        )
        require(
            snapshot_tree(base) == before_pending_status,
            "Pending status changed the isolated filesystem",
        )
        require(
            pending_status.get("freshness", {}).get("state") == "review-required",
            "Status did not surface the agent-fill review gate",
        )
        require(
            pending_status.get("workflow", {}).get("pending_fill") == 1,
            "Status did not report the pending fill count",
        )
        require_private_values_absent(
            pending_status,
            label="Pending status report",
            forbidden=report_private_values,
        )

        scaffolds = read_pending_scaffolds(env=env, provenance_home=provenance_home)
        require(len(scaffolds) == 1, "Expected exactly one isolated pending scaffold")
        scaffold = scaffolds[0]
        summary_id = scaffold.get("summary_id")
        require(isinstance(summary_id, str) and summary_id, "Scaffold has no stable summary id")
        require(scaffold.get("project") == "alpha", "Scaffold resolved to the wrong project")
        require(not contains_key(scaffold, "cwd"), "Scaffold exposed a cwd key")
        require_private_values_absent(
            scaffold,
            label="Agent-facing scaffold",
            forbidden=[base, agent_cwd, rollout],
        )
        submit_fill(summary_id, env=env, provenance_home=provenance_home)

        applied = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(applied.get("summary", {}).get("applied") == 1, "Filled scaffold was not applied")
        require(applied.get("summary", {}).get("approved") == 0, "Unchecked claims were approved")
        require(applied.get("summary", {}).get("pending_approval") == 1, "High-value claim did not enter Approvals")
        require_private_values_absent(
            applied, label="Apply report", forbidden=report_private_values
        )

        timeline_marker = f"<!-- entry:{summary_id} -->"
        approved_marker = f"<!-- entry:{summary_id}#approved -->"
        note_after_timeline = project_note.read_bytes()
        timeline_text = note_after_timeline.decode("utf-8")
        require(timeline_text.count(timeline_marker) == 1, "Timeline was not appended exactly once")
        require(TIMELINE_FACT in timeline_text, "Timeline fact is absent from the project note")
        require(approved_marker not in timeline_text, "High-value claim bypassed approval")
        require("status: planned" in timeline_text, "Unchecked status changed project frontmatter")
        require(human_regions(note_after_timeline) == baseline_human, "Timeline changed human-owned prose")

        approvals_path = workspace / "Approvals.md"
        require(approvals_path.is_file(), "Approvals.md was not generated")
        approvals_unchecked = approvals_path.read_bytes()
        approvals_text = approvals_unchecked.decode("utf-8")
        require("- [ ] **APPROVE ALL**" in approvals_text, "Approvals lacks the global approval gate")
        require(f"<!-- draft:{summary_id} -->" in approvals_text, "Approvals lacks the staged claim")

        unchecked = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(unchecked.get("summary", {}).get("approved") == 0, "Unchecked rerun approved a claim")
        require(unchecked.get("summary", {}).get("pending_approval") == 1, "Unchecked rerun lost the approval")
        require_private_values_absent(
            unchecked, label="Unchecked report", forbidden=report_private_values
        )
        require(project_note.read_bytes() == note_after_timeline, "Unchecked rerun changed the project note")
        require(approvals_path.read_bytes() == approvals_unchecked, "Unchecked rerun changed Approvals.md")

        approvals_path.write_text(
            approvals_text.replace(
                "- [ ] **APPROVE ALL**", "- [x] **APPROVE ALL**", 1
            ),
            encoding="utf-8",
        )
        approved = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(approved.get("summary", {}).get("approved") == 1, "Checked high-value claim was not committed")
        require(approved.get("summary", {}).get("pending_approval") == 0, "Committed claim stayed pending")
        require_private_values_absent(
            approved, label="Approval report", forbidden=report_private_values
        )
        note_after_approval = project_note.read_bytes()
        approved_text = note_after_approval.decode("utf-8")
        require(approved_text.count(timeline_marker) == 1, "Approval duplicated the timeline")
        require(approved_text.count(approved_marker) == 1, "Approved claim was not appended exactly once")
        require("status: active" in approved_text, "Approved status did not reach project frontmatter")
        require("stage: product validation" in approved_text, "Approved stage did not reach project frontmatter")
        require(CONCLUSION in approved_text, "Approved conclusion did not reach the progress log")
        require(human_regions(note_after_approval) == baseline_human, "Approval changed human-owned prose")

        before_first_resume = snapshot_tree(base)
        first_resume = invoke_json(
            resume_arguments(provenance_root=provenance_root),
            env=env,
        )
        require(
            snapshot_tree(base) == before_first_resume,
            "Context resume changed the isolated filesystem",
        )
        first_capsule = first_resume.get("capsule", {})
        first_project = first_capsule.get("project", {})
        first_progress = [
            item
            for block in first_capsule.get("recent_progress", [])
            if isinstance(block, dict)
            for item in block.get("items", [])
        ]
        require(
            first_project.get("status") == "active"
            and first_project.get("stage") == "product validation",
            "First resumed session did not receive approved project state",
        )
        require(
            first_project.get("conclusion") == CONCLUSION,
            "First resumed session did not receive the approved conclusion",
        )
        require(
            TIMELINE_FACT in first_progress,
            "First resumed session did not receive prior low-risk progress",
        )
        require_private_values_absent(
            first_resume,
            label="First resume capsule",
            forbidden=report_private_values,
        )

        approvals_after_approval = approvals_path.read_bytes()
        final = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(final.get("summary", {}).get("approved") == 0, "Final rerun re-approved the claim")
        require(final.get("summary", {}).get("pending_approval") == 0, "Final rerun recreated a pending claim")
        require_private_values_absent(
            final, label="Final report", forbidden=report_private_values
        )
        require(project_note.read_bytes() == note_after_approval, "Final rerun changed the project note")
        require(approvals_path.read_bytes() == approvals_after_approval, "Final rerun changed Approvals.md")
        require(human_regions(project_note.read_bytes()) == baseline_human, "Final rerun changed human-owned prose")

        second_session_id = "e2e-codex-session-two"
        second_rollout = write_rollout(
            profile,
            agent_cwd,
            session_id=second_session_id,
            filename="rollout-e2e-pull-two.jsonl",
        )
        second_private_values = [
            *report_private_values,
            second_session_id,
            second_rollout,
        ]
        second_run = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(
            second_run.get("summary", {}).get("codex_enqueued") == 1,
            "Second session was not enqueued exactly once",
        )
        require(
            second_run.get("summary", {}).get("scaffolded") == 1,
            "Second session did not create one pending scaffold",
        )
        require_private_values_absent(
            second_run,
            label="Second-session report",
            forbidden=second_private_values,
        )

        second_scaffolds = read_pending_scaffolds(
            env=env, provenance_home=provenance_home
        )
        require(
            len(second_scaffolds) == 1,
            "Second session did not expose exactly one pending scaffold",
        )
        second_summary_id = second_scaffolds[0].get("summary_id")
        require(
            isinstance(second_summary_id, str) and second_summary_id != summary_id,
            "Second session did not receive a distinct stable summary id",
        )
        submit_fill(
            second_summary_id,
            env=env,
            provenance_home=provenance_home,
            timeline=[SECOND_TIMELINE_FACT],
            include_high_value_claims=False,
        )
        second_applied = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(
            second_applied.get("summary", {}).get("applied") == 1,
            "Second session timeline was not applied",
        )
        require(
            second_applied.get("summary", {}).get("pending_approval") == 0,
            "Timeline-only second session created a high-value approval",
        )
        require_private_values_absent(
            second_applied,
            label="Second-session apply report",
            forbidden=second_private_values,
        )
        note_after_second = project_note.read_bytes()
        require(
            note_after_second.decode("utf-8").count(SECOND_TIMELINE_FACT) == 1,
            "Second session timeline was not appended exactly once",
        )
        require(
            human_regions(note_after_second) == baseline_human,
            "Second session changed human-owned prose",
        )

        before_second_resume = snapshot_tree(base)
        second_resume = invoke_json(
            resume_arguments(provenance_root=provenance_root),
            env=env,
        )
        require(
            snapshot_tree(base) == before_second_resume,
            "Second context resume changed the isolated filesystem",
        )
        second_progress = [
            item
            for block in second_resume.get("capsule", {}).get("recent_progress", [])
            if isinstance(block, dict)
            for item in block.get("items", [])
        ]
        require(
            TIMELINE_FACT in second_progress and SECOND_TIMELINE_FACT in second_progress,
            "Second resumed session could not recover both prior session increments",
        )
        require_private_values_absent(
            second_resume,
            label="Second resume capsule",
            forbidden=second_private_values,
        )

        second_idempotent = invoke_json(
            configured_pull(run=True),
            env=env,
        )
        require(
            second_idempotent.get("summary", {}).get("applied") == 0,
            "Second-session idempotency rerun re-applied a summary",
        )
        require(
            project_note.read_bytes() == note_after_second,
            "Second-session idempotency rerun changed the project note",
        )

        before_final_status = snapshot_tree(base)
        final_status = invoke_json(
            status_arguments(provenance_root=provenance_root),
            env=env,
        )
        require(
            snapshot_tree(base) == before_final_status,
            "Final status changed the isolated filesystem",
        )
        require(
            final_status.get("status") == "ready"
            and final_status.get("freshness", {}).get("state") == "current",
            "Status did not return to ready after the workflow settled",
        )
        require(
            final_status.get("action_required") == [],
            "Settled status still requested an action",
        )
        require_private_values_absent(
            final_status,
            label="Final status report",
            forbidden=report_private_values,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provenance-root",
        type=Path,
        default=DEFAULT_PROVENANCE_ROOT,
        help="Provenance source checkout (default: sibling ../Provenance)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_unresolved_gate_e2e(args.provenance_root.expanduser().resolve())
        run_e2e(args.provenance_root)
    except E2EFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: init, config fallback, doctor, status, unresolved gate, pull, approval, and idempotency loop"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
