"""Project-scoped host adapter installation for supported agent hosts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from importlib import resources
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4

from . import __version__


HOSTS = ("codex", "claude-code")
SKILL_NAME = "scriptorium-research"
ASSET_ID = f"skills/{SKILL_NAME}/SKILL.md"
MANIFEST_PATH = ".scriptorium/host-adapters.v1.json"
LOCK_PATH = ".scriptorium/host-install.lock"
HOST_TARGETS = {
    "codex": f".agents/skills/{SKILL_NAME}/SKILL.md",
    "claude-code": f".claude/skills/{SKILL_NAME}/SKILL.md",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class HostInstallError(RuntimeError):
    """The installer could not form or apply a trustworthy plan."""


class _HostInstallConflict(RuntimeError):
    """A predictable no-clobber or path-safety conflict."""


def _normalize_bytes(payload: bytes, *, label: str) -> bytes:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _HostInstallConflict(f"{label} is not valid UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _frontmatter_valid(payload: bytes) -> bool:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---\n", 4)
    if end < 0:
        return False
    frontmatter = text[4:end]
    name = re.search(r"(?m)^name:\s*([^\n]+?)\s*$", frontmatter)
    description = re.search(r"(?m)^description:\s*([^\n]+?)\s*$", frontmatter)
    return bool(
        name
        and name.group(1).strip().strip("'\"") == SKILL_NAME
        and description
        and description.group(1).strip().strip("'\"")
    )


def _load_skill() -> bytes:
    try:
        asset = (
            resources.files("scriptorium.assets")
            .joinpath("skills")
            .joinpath(SKILL_NAME)
            .joinpath("SKILL.md")
        )
        payload = _normalize_bytes(asset.read_bytes(), label="packaged host skill")
    except (_HostInstallConflict, OSError, TypeError) as exc:
        raise HostInstallError("packaged host skill is unavailable or invalid") from exc
    if not _frontmatter_valid(payload):
        raise HostInstallError("packaged host skill has invalid Agent Skills frontmatter")
    return payload


def _relative_parts(value: str) -> tuple[str, ...]:
    if (
        not value
        or "\\" in value
        or "//" in value
        or value.endswith("/")
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or PureWindowsPath(value).drive
    ):
        raise _HostInstallConflict(f"unsafe managed relative path: {value!r}")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _HostInstallConflict(f"unsafe managed relative path: {value!r}")
    return parts


def _is_link_or_junction(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _HostInstallConflict(f"cannot inspect managed path: {path}") from exc
    try:
        is_junction = getattr(path, "is_junction", None)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(metadata, "st_file_attributes", 0)
        return (
            path.is_symlink()
            or bool(is_junction and is_junction())
            or bool(reparse_flag and attributes & reparse_flag)
        )
    except OSError as exc:
        raise _HostInstallConflict(f"cannot inspect managed path: {path}") from exc


def _resolve_workspace(path: Path) -> Path:
    requested = path.expanduser()
    if _is_link_or_junction(requested):
        raise _HostInstallConflict(f"workspace cannot be a symlink or junction: {requested}")
    if not requested.exists():
        raise _HostInstallConflict(f"workspace does not exist: {requested}")
    if not requested.is_dir():
        raise _HostInstallConflict(f"workspace is not a directory: {requested}")
    try:
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _HostInstallConflict(f"workspace cannot be resolved: {requested}") from exc
    return resolved


def _safe_destination(root: Path, relative: str) -> Path:
    current = root
    parts = _relative_parts(relative)
    for index, part in enumerate(parts):
        current = current / part
        if _is_link_or_junction(current):
            raise _HostInstallConflict(
                f"managed path contains a symlink or junction: {current}"
            )
        if current.exists() and index < len(parts) - 1 and not current.is_dir():
            raise _HostInstallConflict(f"managed path parent is not a directory: {current}")
    try:
        current.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _HostInstallConflict(f"managed path escapes the workspace: {current}") from exc
    return current


def _ensure_managed_parent(root: Path, relative: str) -> None:
    current = root
    parts = _relative_parts(relative)
    for part in parts[:-1]:
        current = current / part
        if _is_link_or_junction(current):
            raise _HostInstallConflict(
                f"managed path contains a symlink or junction: {current}"
            )
        if current.exists():
            if not current.is_dir():
                raise _HostInstallConflict(
                    f"managed path parent is not a directory: {current}"
                )
            continue
        try:
            current.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise HostInstallError(f"cannot create managed directory: {current}") from exc
        if _is_link_or_junction(current) or not current.is_dir():
            raise _HostInstallConflict(f"managed directory changed during installation: {current}")


@contextmanager
def _install_lock(root: Path) -> Iterator[None]:
    parent = root / ".scriptorium"
    parent_existed = parent.exists()
    _ensure_managed_parent(root, MANIFEST_PATH)
    lock = _safe_destination(root, LOCK_PATH)
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise _HostInstallConflict(
            f"another host installation is active or was interrupted: {lock}"
        ) from exc
    except OSError as exc:
        raise HostInstallError(f"cannot acquire host installation lock: {lock}") from exc
    try:
        yield
    finally:
        try:
            if _is_link_or_junction(lock) or not lock.is_dir():
                raise HostInstallError(f"host installation lock changed unexpectedly: {lock}")
            lock.rmdir()
        except HostInstallError:
            raise
        except OSError as exc:
            raise HostInstallError(f"cannot release host installation lock: {lock}") from exc
        if not parent_existed:
            try:
                parent.rmdir()
            except OSError:
                pass


def _empty_manifest() -> dict[str, Any]:
    return {"format_version": 1, "managed_by": "scriptorium", "files": {}}


def _load_manifest(root: Path) -> tuple[dict[str, Any], Path, bool, str | None]:
    path = _safe_destination(root, MANIFEST_PATH)
    if not path.exists():
        return _empty_manifest(), path, False, None
    if not path.is_file():
        raise _HostInstallConflict(f"host adapter manifest is not a regular file: {path}")
    try:
        payload = _normalize_bytes(path.read_bytes(), label="host adapter manifest")
        data = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _HostInstallConflict(
            f"host adapter manifest is unreadable or invalid: {path}"
        ) from exc
    if (
        not isinstance(data, dict)
        or data.get("format_version") != 1
        or data.get("managed_by") != "scriptorium"
        or not isinstance(data.get("files"), dict)
    ):
        raise _HostInstallConflict(f"host adapter manifest has an unsupported format: {path}")
    for relative, entry in data["files"].items():
        _relative_parts(relative)
        if (
            not isinstance(entry, dict)
            or entry.get("host") not in HOSTS
            or not isinstance(entry.get("asset_id"), str)
            or not isinstance(entry.get("sha256"), str)
            or not _SHA256_RE.fullmatch(entry["sha256"])
        ):
            raise _HostInstallConflict(
                f"host adapter manifest has an invalid file record: {relative}"
            )
    return data, path, True, _sha256(payload)


def _read_managed_file(path: Path) -> bytes:
    if not path.is_file():
        raise _HostInstallConflict(f"managed target is not a regular file: {path}")
    try:
        return _normalize_bytes(path.read_bytes(), label=f"managed target {path}")
    except OSError as exc:
        raise _HostInstallConflict(f"managed target is unreadable: {path}") from exc


def _plan_file(
    *,
    path: Path,
    relative: str,
    host: str,
    desired_digest: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    record = manifest["files"].get(relative)
    if record and (record.get("host") != host or record.get("asset_id") != ASSET_ID):
        raise _HostInstallConflict(f"manifest ownership conflicts with {relative}")
    if not path.exists():
        return {
            "asset_id": ASSET_ID,
            "relative_path": relative,
            "destination": str(path),
            "action": "create",
            "current_sha256": None,
            "desired_sha256": desired_digest,
            "reason_code": "missing",
        }

    current = _read_managed_file(path)
    current_digest = _sha256(current)
    if current_digest == desired_digest:
        action = "unchanged"
        reason = "canonical"
    elif record and current_digest == record["sha256"]:
        action = "update"
        reason = "managed-version-outdated"
    elif record:
        raise _HostInstallConflict(
            f"managed target was modified after installation: {relative}"
        )
    else:
        raise _HostInstallConflict(f"unmanaged target has different content: {relative}")
    return {
        "asset_id": ASSET_ID,
        "relative_path": relative,
        "destination": str(path),
        "action": action,
        "current_sha256": current_digest,
        "desired_sha256": desired_digest,
        "reason_code": reason,
    }


def _manifest_payload(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_payload(
    *,
    root: Path,
    relative: str,
    payload: bytes,
    action: str,
    expected_current_digest: str | None,
) -> None:
    path = _safe_destination(root, relative)
    _ensure_managed_parent(root, relative)
    path = _safe_destination(root, relative)

    if action == "create":
        if path.exists() or _is_link_or_junction(path):
            raise _HostInstallConflict(f"managed target appeared during installation: {path}")
        descriptor = None
        created = False
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            created = True
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            try:
                if created and path.is_file() and not _is_link_or_junction(path):
                    path.unlink()
            except OSError:
                pass
            raise HostInstallError(f"cannot create managed file: {path}") from exc
        return

    if action != "update" or expected_current_digest is None:
        raise HostInstallError(f"unsupported managed write action: {action}")
    current = _read_managed_file(path)
    if _sha256(current) != expected_current_digest:
        raise _HostInstallConflict(f"managed target changed during installation: {path}")
    temporary = path.with_name(f".{path.name}.scriptorium-{uuid4().hex}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if (
            _is_link_or_junction(path)
            or _sha256(_read_managed_file(path)) != expected_current_digest
        ):
            raise _HostInstallConflict(f"managed target changed during installation: {path}")
        os.replace(temporary, path)
    except _HostInstallConflict:
        raise
    except OSError as exc:
        raise HostInstallError(f"cannot update managed file: {path}") from exc
    finally:
        try:
            if temporary.exists() and not _is_link_or_junction(temporary):
                temporary.unlink()
        except (OSError, _HostInstallConflict):
            pass


def _base_report(
    *, host: str, workspace: str, dry_run: bool, target: str | None
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "host.install",
        "mode": "dry-run" if dry_run else "install",
        "requested_host": host,
        "workspace": workspace,
        "target": target,
        "status": "conflict",
        "exit_code": 1,
        "files": [],
        "manifest": {"path": None, "action": "none"},
        "summary": {"create": 0, "update": 0, "unchanged": 0, "conflict": 0},
        "safety": {
            "unmanaged_overwrite": "refused",
            "links": "rejected",
            "global_config": "not-modified",
            "hooks": "not-installed",
            "authentication": "not-tested",
            "network": "not-requested",
        },
        "limitations": [
            "Host discovery and authentication are not live-tested by this command.",
            "Open or restart the host in this workspace before verifying skill discovery.",
            "No capture hook or global host setting is installed.",
            "An interrupted process can leave an empty fail-closed installation lock.",
        ],
    }


def run_host_install(
    *, workspace: Path, host: str, dry_run: bool = False
) -> dict[str, Any]:
    """Plan and, unless dry-run, install one project-scoped host adapter."""
    if host not in HOSTS:
        raise HostInstallError(f"unsupported host: {host}")
    payload = _load_skill()
    desired_digest = _sha256(payload)
    report = _base_report(
        host=host,
        workspace=str(workspace.expanduser()),
        dry_run=dry_run,
        target=None,
    )
    try:
        root = _resolve_workspace(workspace)
        relative = HOST_TARGETS[host]
        target = _safe_destination(root, relative)
    except _HostInstallConflict as exc:
        report["summary"]["conflict"] = 1
        report["conflict"] = {"reason": str(exc)}
        return report

    report["workspace"] = str(root)
    report["target"] = str(target)
    try:
        lock = nullcontext() if dry_run else _install_lock(root)
        with lock:
            manifest, manifest_path, manifest_exists, manifest_digest = _load_manifest(root)
            file_plan = _plan_file(
                path=target,
                relative=relative,
                host=host,
                desired_digest=desired_digest,
                manifest=manifest,
            )
            report["files"] = [file_plan]
            report["summary"][file_plan["action"]] = 1

            updated_manifest = json.loads(json.dumps(manifest))
            updated_manifest["files"][relative] = {
                "host": host,
                "asset_id": ASSET_ID,
                "sha256": desired_digest,
            }
            manifest_changed = updated_manifest != manifest
            manifest_action = "create" if not manifest_exists else (
                "update" if manifest_changed else "unchanged"
            )
            report["manifest"] = {
                "path": str(manifest_path),
                "action": manifest_action,
            }

            writes_planned = (
                file_plan["action"] in {"create", "update"} or manifest_changed
            )
            if dry_run:
                report["status"] = "planned" if writes_planned else "unchanged"
                report["exit_code"] = 0
                return report

            if file_plan["action"] in {"create", "update"}:
                _write_payload(
                    root=root,
                    relative=relative,
                    payload=payload,
                    action=file_plan["action"],
                    expected_current_digest=file_plan["current_sha256"],
                )
            if manifest_changed or not manifest_exists:
                _write_payload(
                    root=root,
                    relative=MANIFEST_PATH,
                    payload=_manifest_payload(updated_manifest),
                    action="update" if manifest_exists else "create",
                    expected_current_digest=manifest_digest,
                )
    except _HostInstallConflict as exc:
        report["status"] = "conflict"
        report["exit_code"] = 1
        report["summary"]["conflict"] = 1
        report["conflict"] = {"reason": str(exc)}
        return report

    report["status"] = "installed" if writes_planned else "unchanged"
    report["exit_code"] = 0
    return report


def inspect_host_installation(*, workspace: Path) -> dict[str, Any]:
    """Inspect canonical adapter files without changing the workspace."""
    payload = _load_skill()
    desired_digest = _sha256(payload)
    result: dict[str, Any] = {
        "workspace": str(workspace.expanduser()),
        "manifest": {"path": None, "state": "missing"},
        "hosts": [],
    }
    try:
        root = _resolve_workspace(workspace)
    except _HostInstallConflict as exc:
        result["workspace_state"] = "unsafe"
        result["error"] = str(exc)
        return result
    result["workspace"] = str(root)
    result["workspace_state"] = "available"
    try:
        manifest, manifest_path, manifest_exists, _ = _load_manifest(root)
        result["manifest"] = {
            "path": str(manifest_path),
            "state": "valid" if manifest_exists else "missing",
        }
    except _HostInstallConflict as exc:
        manifest = _empty_manifest()
        manifest_path = root.joinpath(*_relative_parts(MANIFEST_PATH))
        result["manifest"] = {
            "path": str(manifest_path),
            "state": "invalid",
            "error": str(exc),
        }

    for host in HOSTS:
        relative = HOST_TARGETS[host]
        item: dict[str, Any] = {
            "name": host,
            "relative_path": relative,
            "target_path": None,
            "state": "missing",
            "adapter_version": __version__,
            "expected_sha256": desired_digest,
            "actual_sha256": None,
            "frontmatter_valid": False,
            "discovery": "not-live-tested",
        }
        try:
            path = _safe_destination(root, relative)
            item["target_path"] = str(path)
            if not path.exists():
                result["hosts"].append(item)
                continue
            current = _read_managed_file(path)
            actual_digest = _sha256(current)
            item["actual_sha256"] = actual_digest
            item["frontmatter_valid"] = _frontmatter_valid(current)
            record = manifest["files"].get(relative)
            record_matches = bool(
                record
                and record.get("host") == host
                and record.get("asset_id") == ASSET_ID
            )
            if actual_digest == desired_digest:
                item["state"] = (
                    "canonical"
                    if record_matches and record.get("sha256") == desired_digest
                    else "unregistered"
                )
            elif record_matches and record.get("sha256") == actual_digest:
                item["state"] = "outdated"
            else:
                item["state"] = "modified"
        except _HostInstallConflict as exc:
            item["state"] = "unsafe"
            item["error"] = str(exc)
        result["hosts"].append(item)
    return result


def format_host_install_report(report: dict[str, Any]) -> str:
    """Render a stable, color-free installer report."""
    lines = [
        f"Scriptorium host install {report['generated_by']['version']}",
        f"Host: {report['requested_host']}",
        f"Workspace: {report['workspace']}",
        f"Mode: {report['mode']}",
        "Network: no action requested",
        "",
    ]
    for file_plan in report.get("files", []):
        lines.append(
            f"{file_plan['action'].upper():<9} {file_plan['relative_path']}"
        )
    if report.get("manifest", {}).get("path"):
        lines.append(
            f"{report['manifest']['action'].upper():<9} {MANIFEST_PATH}"
        )
    if report["status"] == "conflict":
        reason = report.get("conflict", {}).get("reason", "installation refused")
        lines.append(f"CONFLICT  {reason}")
    lines.extend(
        [
            "",
            f"Result: {report['status'].upper()}",
            "No global config, hook, credential, login, GUI, or network action was changed.",
        ]
    )
    if report["status"] in {"installed", "unchanged"}:
        lines.append(
            "Next: open or restart the selected host in this workspace, "
            "then verify its skills list."
        )
    elif report["status"] == "planned":
        lines.append("Next: rerun the same command without --dry-run to install.")
    return "\n".join(lines)
