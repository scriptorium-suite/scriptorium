"""Command-line entry point for the Scriptorium suite."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, SuiteConfig, load_config
from .demo import DemoError, run_demo
from .doctor import DoctorError, TARGETS, format_doctor_report, run_doctor
from .host import (
    HOSTS,
    HostInstallError,
    format_host_install_report,
    run_host_install,
)
from .inventory import format_inventory_report, run_inventory
from .init import InitError, format_init_report, run_init
from .migration import (
    MIGRATION_LIMITATIONS,
    REPORT_VERSION as MIGRATION_REPORT_VERSION,
    MigrationError,
    apply_migration,
    format_migration_report,
    load_migration,
    plan_migration,
    reapply_migration,
    rollback_migration,
    verify_migration,
)
from .path_selection import (
    attach_path_selection,
    codex_home_selection,
    root_selection,
    select_configured_path,
    selection_warnings,
)
from .pull import PullError, format_pull_report, run_pull
from .resume import ResumeError, format_resume_report, run_resume
from .status import StatusError, format_status_report, run_status


class _JsonUsageError(RuntimeError):
    """Argparse rejected an invocation that requested JSON output."""


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _JsonUsageError(message)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def build_parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser_class = _JsonArgumentParser if json_errors else argparse.ArgumentParser
    parser = parser_class(
        prog="scriptorium",
        description="Agent-native research workflow suite (Public Alpha candidate).",
    )
    parser.add_argument("--version", action="version", version=f"scriptorium {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser(
        "demo",
        help="run the offline, credential-free synthetic research walkthrough",
    )
    demo.add_argument(
        "--output",
        type=Path,
        default=Path("scriptorium-demo"),
        help="isolated output directory (default: ./scriptorium-demo)",
    )
    demo.add_argument("--spec-root", type=Path, help="source checkout of scriptorium-spec")
    demo.add_argument("--steward-root", type=Path, help="source checkout of Steward")
    demo.add_argument("--provenance-root", type=Path, help="source checkout of Provenance")

    initialize = commands.add_parser(
        "init",
        help="preview or create a minimal real research workspace and suite config",
    )
    initialize.add_argument("--workspace", type=Path, required=True)
    initialize.add_argument("--provenance-home", type=Path, required=True)
    initialize.add_argument("--project-id", required=True)
    initialize.add_argument("--title", required=True)
    initialize.add_argument(
        "--host",
        action="append",
        choices=HOSTS,
        required=True,
        dest="hosts",
        help="selected agent host; repeat to select both",
    )
    initialize.add_argument(
        "--linked-repo",
        type=Path,
        help="existing session working directory (default: workspace)",
    )
    initialize.add_argument(
        "--idea",
        help="optional user-authored research intuition for the project note",
    )
    initialize.add_argument(
        "--config-dir",
        type=Path,
        help="configuration family root (default: SCRIPTORIUM_CONFIG_DIR or ~/.config/scriptorium)",
    )
    initialize.add_argument(
        "--run",
        action="store_true",
        help="create the reviewed plan (default: write-free preview)",
    )
    initialize.add_argument(
        "--json", action="store_true", dest="json_output", help="write JSON to stdout"
    )

    doctor = commands.add_parser(
        "doctor",
        help="run read-only installation and capability diagnostics",
    )
    doctor.add_argument(
        "--target",
        choices=TARGETS,
        default="public-alpha",
        help="readiness target to evaluate (default: public-alpha)",
    )
    doctor.add_argument("--json", action="store_true", dest="json_output", help="write JSON to stdout")
    doctor.add_argument("--spec-root", type=Path, help="source checkout of scriptorium-spec")
    doctor.add_argument("--steward-root", type=Path, help="source checkout of Steward")
    doctor.add_argument("--provenance-root", type=Path, help="source checkout of Provenance")
    doctor.add_argument(
        "--provenance-home",
        type=Path,
        help="existing Provenance data root (or set PROVENANCE_HOME)",
    )
    doctor.add_argument("--lectern-root", type=Path, help="source checkout of Lectern")
    doctor.add_argument("--workspace", type=Path, help="existing Markdown workspace to inspect")
    doctor.add_argument(
        "--config-dir",
        type=Path,
        help="configuration family root used when workspace/data paths are omitted",
    )

    pull = commands.add_parser(
        "pull",
        help="preview or run the on-demand Provenance capture and sync sequence",
    )
    pull.add_argument(
        "--workspace",
        type=Path,
        help="existing Markdown research workspace (or use suite config)",
    )
    pull.add_argument(
        "--provenance-home",
        type=Path,
        help="existing Provenance data root (or use suite config)",
    )
    pull.add_argument("--provenance-root", type=Path, help="source checkout of Provenance")
    pull.add_argument(
        "--project",
        help="limit Codex log discovery to one registered project id",
    )
    pull.add_argument(
        "--run",
        action="store_true",
        help="execute the plan (default: write-free preview)",
    )
    pull.add_argument(
        "--json", action="store_true", dest="json_output", help="write JSON to stdout"
    )
    pull.add_argument(
        "--config-dir",
        type=Path,
        help="configuration family root used when workspace/data paths are omitted",
    )

    resume = commands.add_parser(
        "resume",
        help="read a bounded, reviewable project context capsule",
    )
    resume.add_argument(
        "--provenance-home",
        type=Path,
        help="existing Provenance data root (or use suite config)",
    )
    resume.add_argument("--provenance-root", type=Path, help="source checkout of Provenance")
    resume.add_argument(
        "--project",
        help="registered project id (or use the suite default project)",
    )
    resume.add_argument(
        "--json", action="store_true", dest="json_output", help="write JSON to stdout"
    )
    resume.add_argument(
        "--config-dir",
        type=Path,
        help="configuration family root used when data root/project are omitted",
    )

    status = commands.add_parser(
        "status",
        help="show content-free readiness and pending research workflow counts",
    )
    status.add_argument(
        "--json", action="store_true", dest="json_output", help="write JSON to stdout"
    )
    status.add_argument("--spec-root", type=Path, help="source checkout of scriptorium-spec")
    status.add_argument("--steward-root", type=Path, help="source checkout of Steward")
    status.add_argument("--provenance-root", type=Path, help="source checkout of Provenance")
    status.add_argument("--lectern-root", type=Path, help="source checkout of Lectern")
    status.add_argument(
        "--workspace",
        type=Path,
        help="existing Markdown research workspace (or use suite config)",
    )
    status.add_argument(
        "--provenance-home",
        type=Path,
        help="existing Provenance data root (or use suite config)",
    )
    status.add_argument(
        "--project",
        help="limit Codex log discovery to one registered project id",
    )
    status.add_argument(
        "--config-dir",
        type=Path,
        help="configuration family root used when workspace/data paths are omitted",
    )

    inventory = commands.add_parser(
        "inventory",
        help="inventory explicit local research sources and preview safe routing",
    )
    inventory.add_argument(
        "--source",
        action="append",
        type=Path,
        default=[],
        help="local Markdown/PDF file or directory; repeat for multiple roots",
    )
    inventory.add_argument(
        "--conversation-export",
        action="append",
        type=Path,
        default=[],
        help="explicit local AI conversation export file or directory; repeat as needed",
    )
    inventory.add_argument(
        "--zotero-export",
        action="append",
        type=Path,
        default=[],
        help="explicit local Zotero export file or directory; repeat as needed",
    )
    inventory.add_argument(
        "--json", action="store_true", dest="json_output", help="write JSON to stdout"
    )

    migrate = commands.add_parser(
        "migrate",
        help="plan, apply, verify, or roll back an explicit Markdown/PDF migration",
    )
    migrate_commands = migrate.add_subparsers(
        dest="migrate_command", required=True
    )

    def add_migration_identity(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--workspace",
            type=Path,
            required=True,
            help="existing research workspace",
        )
        command.add_argument(
            "--batch-id",
            required=True,
            help="stable local migration batch identifier",
        )
        command.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="write aggregate JSON to stdout",
        )

    migrate_plan = migrate_commands.add_parser(
        "plan",
        help="preview an explicit migration without writing",
    )
    add_migration_identity(migrate_plan)
    migrate_plan.add_argument(
        "--source",
        action="append",
        type=Path,
        required=True,
        help="selected Markdown/PDF file or directory; repeat as needed",
    )

    migrate_apply = migrate_commands.add_parser(
        "apply",
        help="apply selected sources or resume an existing batch",
    )
    add_migration_identity(migrate_apply)
    migrate_apply.add_argument(
        "--source",
        action="append",
        type=Path,
        default=[],
        help="required for a new batch; omit to resume an existing batch",
    )

    migrate_verify = migrate_commands.add_parser(
        "verify",
        help="verify an existing batch by workspace and batch identifier",
    )
    add_migration_identity(migrate_verify)

    migrate_rollback = migrate_commands.add_parser(
        "rollback",
        help="remove unchanged files owned by an existing batch",
    )
    add_migration_identity(migrate_rollback)

    host = commands.add_parser(
        "host",
        help="manage explicit, project-scoped agent host adapters",
    )
    host_commands = host.add_subparsers(dest="host_command", required=True)
    host_install = host_commands.add_parser(
        "install",
        help="install the canonical research skill without changing global host settings",
    )
    host_install.add_argument("host", choices=HOSTS, help="agent host to configure")
    host_install.add_argument(
        "--workspace",
        type=Path,
        help="existing research workspace (or use suite config)",
    )
    host_install.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the no-clobber installation without writing files",
    )
    host_install.add_argument(
        "--json", action="store_true", dest="json_output", help="write JSON to stdout"
    )
    host_install.add_argument(
        "--config-dir",
        type=Path,
        help="configuration family root used when --workspace is omitted",
    )
    return parser


def _configured_path(
    explicit: Path | None,
    environment_names: tuple[str, ...],
    configured: Path | None,
) -> Path | None:
    selected, _ = select_configured_path(explicit, environment_names, configured)
    return selected


def _has_nonblank_environment(names: tuple[str, ...]) -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return True
    return False


def _load_suite_config(
    config_dir: Path | None, *, needed: bool
) -> SuiteConfig | None:
    if not needed and config_dir is None:
        return None
    return load_config(config_dir)


def _configured_project(
    explicit: str | None,
    *,
    workspace: Path,
    suite_config: SuiteConfig | None,
) -> str | None:
    if explicit is not None:
        return explicit
    if suite_config is None:
        return None
    try:
        selected_workspace = workspace.expanduser().resolve(strict=False)
        configured_workspace = suite_config.workspace.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if selected_workspace != configured_workspace:
        return None
    return suite_config.default_project


def _init_error_report(*, run: bool) -> dict[str, object]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "init",
        "mode": "run" if run else "preview",
        "status": "error",
        "exit_code": 2,
        "changes": [],
        "summary": {"create": 0, "unchanged": 0, "conflict": 0},
        "safety": {
            "preview_writes": "none",
            "unmanaged_overwrite": "refused",
            "credentials": "not-requested",
            "hooks": "not-installed",
            "models": "not-invoked",
        },
        "errors": [{"code": "entry_error"}],
    }


def _doctor_error_report(*, target: str) -> dict[str, object]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "target": target,
        "status": "error",
        "exit_code": 2,
        "readiness": {},
        "checks": [],
        "egress": [],
        "summary": {},
        "errors": [{"code": "entry_error"}],
        "limitations": ["No trusted diagnostic report was available."],
    }


def _pull_error_report(*, run: bool) -> dict[str, object]:
    return {
        "format_version": 1,
        "generated_by": {
            "name": "scriptorium",
            "version": __version__,
        },
        "operation": "pull",
        "mode": "run" if run else "preview",
        "status": "error",
        "exit_code": 2,
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "not-invoked",
            "optional_connectors": "not-invoked",
        },
        "stages": [],
        "summary": {},
        "action_required": [],
        "errors": [{"code": "entry_error"}],
        "limitations": ["No trusted component report was available."],
        "entry": {
            "public_command": "prov-sync-pull",
            "component_exit_code": None,
            "stdout": "suppressed",
            "stderr": "suppressed",
        },
    }


def _resume_error_report() -> dict[str, object]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "resume",
        "status": "error",
        "exit_code": 2,
        "capsule": None,
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "not-invoked",
            "optional_connectors": "not-invoked",
        },
        "entry": {
            "public_command": "prov-context",
            "component_exit_code": None,
            "stdout": "suppressed",
            "stderr": "suppressed",
        },
        "errors": [{"code": "entry_error"}],
        "limitations": ["No trusted context capsule was available."],
    }


def _status_error_report() -> dict[str, object]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "status",
        "status": "error",
        "exit_code": 2,
        "readiness": {},
        "freshness": {
            "state": "unknown",
            "basis": "not-available",
            "last_successful_pull": "not-reported",
        },
        "workflow": {},
        "action_required": [],
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "unknown",
            "optional_connectors": "not-invoked",
        },
        "errors": [{"code": "entry_error"}],
        "limitations": ["No trusted status report was available."],
    }


def _inventory_error_report() -> dict[str, object]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "inventory",
        "mode": "preview",
        "status": "error",
        "exit_code": 2,
        "summary": {
            "roots_requested": 0,
            "roots_scanned": 0,
            "files_seen": 0,
            "candidates": 0,
            "markdown": 0,
            "pdf": 0,
            "ai_conversation": 0,
            "zotero_export": 0,
            "unsupported": 0,
            "reparse_skipped": 0,
        },
        "routing_preview": {
            "workspace-review": 0,
            "literature-reference": 0,
            "provenance-import-review": 0,
            "steward-review": 0,
        },
        "action_required": [],
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "not-invoked",
            "optional_connectors": "not-invoked",
        },
        "safety": {
            "writes": "none",
            "content": "not-read",
            "paths": "suppressed",
            "links": "not-followed",
            "roots": "explicit-only",
        },
        "errors": [{"code": "entry_error"}],
        "limitations": ["No trusted inventory preview was available."],
    }


def _migration_error_report(
    *, operation: str, code: str = "entry_error"
) -> dict[str, object]:
    if operation not in {"plan", "apply", "verify", "rollback"}:
        operation = "migration"
    return {
        "schema_version": MIGRATION_REPORT_VERSION,
        "operation": operation,
        "status": "error",
        "summary": {
            "sources_requested": 0,
            "files": 0,
            "markdown": 0,
            "pdf": 0,
            "bytes": 0,
            "changed": 0,
            "unchanged": 0,
        },
        "errors": [{"code": code}],
        "limitations": list(MIGRATION_LIMITATIONS),
    }


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_command = bool(
        raw_argv
        and raw_argv[0] in {
            "init",
            "pull",
            "resume",
            "status",
            "inventory",
            "migrate",
        }
        and "--json" in raw_argv
    )
    private_usage_command = bool(
        raw_argv and raw_argv[0] in {"inventory", "resume", "migrate"}
    )
    migration_operation = (
        raw_argv[1]
        if len(raw_argv) > 1
        and raw_argv[0] == "migrate"
        and raw_argv[1] in {"plan", "apply", "verify", "rollback"}
        else "migration"
    )
    try:
        args = build_parser(
            json_errors=json_command or private_usage_command
        ).parse_args(raw_argv)
    except _JsonUsageError:
        if private_usage_command and not json_command:
            print(
                f"ERROR: invalid {raw_argv[0]} invocation; "
                f"review scriptorium {raw_argv[0]} --help.",
                file=sys.stderr,
            )
            return 2
        if raw_argv and raw_argv[0] == "init":
            error_report = _init_error_report(run="--run" in raw_argv)
        elif raw_argv and raw_argv[0] == "pull":
            error_report = _pull_error_report(run="--run" in raw_argv)
        elif raw_argv and raw_argv[0] == "resume":
            error_report = _resume_error_report()
        elif raw_argv and raw_argv[0] == "inventory":
            error_report = _inventory_error_report()
        elif raw_argv and raw_argv[0] == "migrate":
            error_report = _migration_error_report(
                operation=migration_operation
            )
        else:
            error_report = _status_error_report()
        print(
            json.dumps(error_report, ensure_ascii=False, indent=2)
        )
        return 2
    if args.command == "demo":
        try:
            demo_report = run_demo(
                args.output,
                spec_root=args.spec_root,
                steward_root=args.steward_root,
                provenance_root=args.provenance_root,
            )
        except DemoError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        root = demo_report.parent
        print("Demo complete: contract validation, review assembly, local memory, search, and MCP context passed.")
        print(f"Review: {root / 'workspace' / 'Reviews' / 'ai4science-materials.md'}")
        print(f"Search evidence: {root / 'workspace' / 'Reports' / 'provenance-search.txt'}")
        print(f"Report: {demo_report}")
        return 0
    if args.command == "init":
        try:
            init_report = run_init(
                workspace=args.workspace,
                provenance_home=args.provenance_home,
                project_id=args.project_id,
                title=args.title,
                hosts=args.hosts,
                linked_repo=args.linked_repo,
                idea=args.idea,
                config_dir=args.config_dir,
                run=args.run,
            )
        except InitError as exc:
            if args.json_output:
                print(
                    json.dumps(
                        _init_error_report(run=args.run),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(init_report, ensure_ascii=False, indent=2))
        else:
            print(format_init_report(init_report))
        return int(init_report["exit_code"])
    if args.command == "doctor":
        selections = {
            "spec_root": root_selection(args.spec_root, "SCRIPTORIUM_SPEC_ROOT"),
            "steward_root": root_selection(
                args.steward_root, "SCRIPTORIUM_STEWARD_ROOT"
            ),
            "provenance_root": root_selection(
                args.provenance_root, "SCRIPTORIUM_PROVENANCE_ROOT"
            ),
            "lectern_root": root_selection(
                args.lectern_root, "SCRIPTORIUM_LECTERN_ROOT"
            ),
            "codex_home": codex_home_selection(),
        }
        warnings: list[dict[str, object]] = []
        try:
            needs_config = (
                args.config_dir is not None
                or args.workspace is None
                or args.provenance_home is None
            )
            suite_config = _load_suite_config(
                args.config_dir, needed=needs_config
            )
            workspace, workspace_selection = select_configured_path(
                args.workspace,
                ("SCRIPTORIUM_WORKSPACE", "PROVENANCE_VAULT"),
                suite_config.workspace if suite_config else None,
            )
            provenance_home, data_root_selection = select_configured_path(
                args.provenance_home,
                ("PROVENANCE_HOME",),
                suite_config.provenance_home if suite_config else None,
            )
            selections = {
                "workspace": workspace_selection,
                "data_root": data_root_selection,
                **selections,
            }
            warnings = selection_warnings(selections)
            doctor_report = run_doctor(
                target=args.target,
                spec_root=args.spec_root,
                steward_root=args.steward_root,
                provenance_root=args.provenance_root,
                provenance_home=provenance_home,
                lectern_root=args.lectern_root,
                workspace=workspace,
            )
            attach_path_selection(doctor_report, selections, warnings)
        except (ConfigError, DoctorError) as exc:
            if args.json_output:
                error_report = _doctor_error_report(target=args.target)
                attach_path_selection(error_report, selections, warnings)
                print(
                    json.dumps(
                        error_report,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(doctor_report, ensure_ascii=False, indent=2))
        else:
            print(format_doctor_report(doctor_report))
        return int(doctor_report["exit_code"])
    if args.command == "pull":
        selections = {
            "provenance_root": root_selection(
                args.provenance_root, "SCRIPTORIUM_PROVENANCE_ROOT"
            ),
            "codex_home": codex_home_selection(),
        }
        warnings: list[dict[str, object]] = []
        try:
            needs_config = (
                args.config_dir is not None
                or args.workspace is None
                or args.provenance_home is None
            )
            suite_config = _load_suite_config(
                args.config_dir, needed=needs_config
            )
            workspace, workspace_selection = select_configured_path(
                args.workspace,
                ("SCRIPTORIUM_WORKSPACE", "PROVENANCE_VAULT"),
                suite_config.workspace if suite_config else None,
            )
            provenance_home, data_root_selection = select_configured_path(
                args.provenance_home,
                ("PROVENANCE_HOME",),
                suite_config.provenance_home if suite_config else None,
            )
            selections = {
                "workspace": workspace_selection,
                "data_root": data_root_selection,
                **selections,
            }
            warnings = selection_warnings(selections)
            if workspace is None or provenance_home is None:
                raise PullError(
                    "workspace and Provenance home are required via flags, environment, or suite config"
                )
            if args.run and warnings:
                raise PullError(
                    "environment path selection conflicts with suite config; "
                    "pass explicit --workspace and --provenance-home paths before --run"
                )
            pull_report = run_pull(
                workspace=workspace,
                provenance_home=provenance_home,
                provenance_root=args.provenance_root,
                project=_configured_project(
                    args.project,
                    workspace=workspace,
                    suite_config=suite_config,
                ),
                run=args.run,
            )
            attach_path_selection(pull_report, selections, warnings)
        except (ConfigError, PullError) as exc:
            if args.json_output:
                error_report = _pull_error_report(run=args.run)
                attach_path_selection(error_report, selections, warnings)
                print(
                    json.dumps(
                        error_report,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(pull_report, ensure_ascii=False, indent=2))
        else:
            print(format_pull_report(pull_report))
        return int(pull_report["exit_code"])
    if args.command == "resume":
        selections = {
            "provenance_root": root_selection(
                args.provenance_root, "SCRIPTORIUM_PROVENANCE_ROOT"
            ),
        }
        warnings: list[dict[str, object]] = []
        try:
            needs_config = (
                args.config_dir is not None
                or args.provenance_home is None
                or args.project is None
            )
            suite_config = _load_suite_config(args.config_dir, needed=needs_config)
            provenance_home, data_root_selection = select_configured_path(
                args.provenance_home,
                ("PROVENANCE_HOME",),
                suite_config.provenance_home if suite_config else None,
            )
            selections = {
                "data_root": data_root_selection,
                **selections,
            }
            warnings = selection_warnings(selections)
            project = args.project or (
                suite_config.default_project if suite_config is not None else None
            )
            if provenance_home is None or project is None:
                raise ResumeError(
                    "Provenance home and project are required via flags, environment, or suite config"
                )
            resume_report = run_resume(
                provenance_home=provenance_home,
                provenance_root=args.provenance_root,
                project=project,
            )
            attach_path_selection(resume_report, selections, warnings)
        except (ConfigError, ResumeError):
            if args.json_output:
                error_report = _resume_error_report()
                attach_path_selection(error_report, selections, warnings)
                print(json.dumps(error_report, ensure_ascii=False, indent=2))
            else:
                print(
                    "ERROR: context capsule unavailable; run scriptorium doctor for local diagnostics.",
                    file=sys.stderr,
                )
            return 2
        if args.json_output:
            print(json.dumps(resume_report, ensure_ascii=False, indent=2))
        else:
            print(format_resume_report(resume_report))
        return int(resume_report["exit_code"])
    if args.command == "status":
        selections = {
            "spec_root": root_selection(args.spec_root, "SCRIPTORIUM_SPEC_ROOT"),
            "steward_root": root_selection(
                args.steward_root, "SCRIPTORIUM_STEWARD_ROOT"
            ),
            "provenance_root": root_selection(
                args.provenance_root, "SCRIPTORIUM_PROVENANCE_ROOT"
            ),
            "lectern_root": root_selection(
                args.lectern_root, "SCRIPTORIUM_LECTERN_ROOT"
            ),
            "codex_home": codex_home_selection(),
        }
        warnings: list[dict[str, object]] = []
        try:
            needs_config = (
                args.config_dir is not None
                or args.workspace is None
                or args.provenance_home is None
            )
            suite_config = _load_suite_config(
                args.config_dir, needed=needs_config
            )
            workspace, workspace_selection = select_configured_path(
                args.workspace,
                ("SCRIPTORIUM_WORKSPACE", "PROVENANCE_VAULT"),
                suite_config.workspace if suite_config else None,
            )
            provenance_home, data_root_selection = select_configured_path(
                args.provenance_home,
                ("PROVENANCE_HOME",),
                suite_config.provenance_home if suite_config else None,
            )
            selections = {
                "workspace": workspace_selection,
                "data_root": data_root_selection,
                **selections,
            }
            warnings = selection_warnings(selections)
            if workspace is None or provenance_home is None:
                raise StatusError(
                    "workspace and Provenance home are required via flags, environment, or suite config"
                )
            status_report = run_status(
                workspace=workspace,
                provenance_home=provenance_home,
                project=_configured_project(
                    args.project,
                    workspace=workspace,
                    suite_config=suite_config,
                ),
                spec_root=args.spec_root,
                steward_root=args.steward_root,
                provenance_root=args.provenance_root,
                lectern_root=args.lectern_root,
            )
            attach_path_selection(status_report, selections, warnings)
        # Status is a content-free boundary; unexpected probe failures must not escape.
        except Exception:
            if args.json_output:
                error_report = _status_error_report()
                attach_path_selection(error_report, selections, warnings)
                print(
                    json.dumps(
                        error_report,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(
                    "ERROR: status report unavailable; "
                    "run scriptorium doctor for local diagnostics.",
                    file=sys.stderr,
                )
            return 2
        if args.json_output:
            print(json.dumps(status_report, ensure_ascii=False, indent=2))
        else:
            print(format_status_report(status_report))
        return int(status_report["exit_code"])
    if args.command == "inventory":
        try:
            inventory_report = run_inventory(
                sources=args.source,
                conversation_exports=args.conversation_export,
                zotero_exports=args.zotero_export,
            )
            inventory_exit_code = int(inventory_report["exit_code"])
            inventory_output = (
                json.dumps(inventory_report, ensure_ascii=False, indent=2)
                if args.json_output
                else format_inventory_report(inventory_report)
            )
        # Inventory is content-free; filesystem errors and paths must not escape.
        except Exception:
            if args.json_output:
                print(
                    json.dumps(
                        _inventory_error_report(),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(
                    "ERROR: inventory preview unavailable; verify the explicit local source roots.",
                    file=sys.stderr,
                )
            return 2
        print(inventory_output)
        return inventory_exit_code
    if args.command == "migrate":
        try:
            if args.migrate_command == "plan":
                migration_result = plan_migration(
                    args.source,
                    workspace=args.workspace,
                    batch_id=args.batch_id,
                )
            elif args.migrate_command == "apply":
                migration_result = (
                    apply_migration(
                        plan_migration(
                            args.source,
                            workspace=args.workspace,
                            batch_id=args.batch_id,
                        )
                    )
                    if args.source
                    else reapply_migration(
                        workspace=args.workspace,
                        batch_id=args.batch_id,
                    )
                )
            elif args.migrate_command == "verify":
                migration_result = verify_migration(
                    workspace=args.workspace,
                    batch_id=args.batch_id,
                )
            else:
                migration_result = rollback_migration(
                    load_migration(
                        workspace=args.workspace,
                        batch_id=args.batch_id,
                    )
                )
        except MigrationError as exc:
            if args.json_output:
                print(
                    json.dumps(
                        _migration_error_report(
                            operation=args.migrate_command,
                            code=exc.code,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(
                    f"ERROR: migrate {args.migrate_command} failed ({exc.code}).",
                    file=sys.stderr,
                )
            return 2
        # Migration reports are a privacy boundary; unexpected details stay local.
        except Exception:
            if args.json_output:
                print(
                    json.dumps(
                        _migration_error_report(
                            operation=args.migrate_command,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(
                    f"ERROR: migrate {args.migrate_command} failed (entry_error).",
                    file=sys.stderr,
                )
            return 2
        if args.json_output:
            print(
                json.dumps(
                    migration_result.report,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(format_migration_report(migration_result.report))
        return 0
    if args.command == "host" and args.host_command == "install":
        try:
            needs_config = (
                args.config_dir is not None
                or (
                    args.workspace is None
                    and not _has_nonblank_environment(
                        ("SCRIPTORIUM_WORKSPACE", "PROVENANCE_VAULT")
                    )
                )
            )
            suite_config = _load_suite_config(
                args.config_dir, needed=needs_config
            )
            workspace = _configured_path(
                args.workspace,
                ("SCRIPTORIUM_WORKSPACE", "PROVENANCE_VAULT"),
                suite_config.workspace if suite_config else None,
            )
            if workspace is None:
                raise HostInstallError(
                    "workspace is required via --workspace, environment, or suite config"
                )
            install_report = run_host_install(
                workspace=workspace,
                host=args.host,
                dry_run=args.dry_run,
            )
        except (ConfigError, HostInstallError) as exc:
            if args.json_output:
                print(
                    json.dumps(
                        {
                            "format_version": 1,
                            "operation": "host.install",
                            "status": "error",
                            "exit_code": 2,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(install_report, ensure_ascii=False, indent=2))
        else:
            print(format_host_install_report(install_report))
        return int(install_report["exit_code"])
    return 2
