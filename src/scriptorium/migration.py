"""Explicit, local-only Markdown/PDF migration.

Private manifests contain absolute paths and live outside the research
workspace. Public reports contain aggregate counts only.

Threat boundary: this module coordinates cooperative processes for one local
user. It does not defend against a malicious local process changing files
during an operation.
"""

from __future__ import annotations

import copy
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


MANIFEST_VERSION = "migration-manifest/1.0"
REPORT_VERSION = "migration-report/1.0"
MANIFEST_PRIVACY = "local-private"
MIGRATION_LIMITATIONS = (
    "Only explicitly selected Markdown and PDF files or directories are supported.",
    "AI conversations, Zotero libraries, parsing, indexing, and network sources are not supported.",
    "The private path manifest stays in the local state root outside the research workspace.",
    "Coordination assumes one local user and cooperative Scriptorium processes.",
    "Atomic publication requires hard-link support on the local destination filesystem.",
    "Sources may be on another local volume because bytes are staged beside each destination.",
    (
        "A random internal .scriptorium-*.stage hard-link ownership anchor "
        "remains beside each migrated target until rollback."
    ),
    (
        "A crash before an anchor is recorded may leave an unclaimed random stage; "
        "it is never adopted or deleted automatically."
    ),
    (
        "Automatic rollback requires Windows no-replace rename or Linux renameat2; "
        "unsupported platforms fail closed."
    ),
    (
        "Rollback quarantines and re-verifies owned files; foreign replacements are "
        "restored or preserved, and empty directories may remain."
    ),
)

_KINDS = {".md": "markdown", ".markdown": "markdown", ".pdf": "pdf"}
_BATCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ID_RE = re.compile(r"file-[0-9]{6}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")
_REPARSE_POINT = 0x400
_DRIVE_REMOTE = 4
_ROLLBACK_PHASES = {
    "target",
    "target-quarantined",
    "anchor",
    "anchor-quarantined",
}
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RUN_STATES = {
    "planned",
    "applying",
    "applied",
    "rolling-back",
    "rolled-back",
}
_ENTRY_STATES = {
    "planned",
    "creating",
    "created",
    "delete-pending",
    "deleted",
}
_RUN_ENTRY_STATES = {
    "planned": {"planned"},
    "applying": {"planned", "creating", "created"},
    "applied": {"created"},
    "rolling-back": _ENTRY_STATES,
    "rolled-back": {"deleted"},
}
MIGRATION_ERROR_CODES = frozenset(
    {
        "applied_manifest_missing",
        "atomic_publish_unavailable",
        "atomic_quarantine_unavailable",
        "batch_conflict",
        "cross_volume_publish_unsupported",
        "entry_error",
        "file_identity_unavailable",
        "invalid_batch_id",
        "invalid_manifest",
        "invalid_path",
        "invalid_sources",
        "invalid_state_transition",
        "link_or_reparse_rejected",
        "lock_invalid",
        "lock_unavailable",
        "manifest_integrity_failed",
        "migration_already_rolled_back",
        "migration_not_applied",
        "migration_not_found",
        "no_sources_selected",
        "no_supported_files",
        "noncanonical_state_root",
        "owned_target_changed",
        "path_unreadable",
        "remote_path_rejected",
        "rollback_failed",
        "rollback_in_progress",
        "rollback_restore_blocked",
        "source_destination_overlap",
        "source_hash_changed",
        "source_missing",
        "source_not_regular",
        "source_state_overlap",
        "source_unreadable",
        "staging_cleanup_failed",
        "staging_conflict",
        "quarantine_conflict",
        "state_root_in_source",
        "state_root_invalid",
        "state_workspace_overlap",
        "state_write_failed",
        "stored_manifest_invalid",
        "stored_manifest_missing",
        "target_escape",
        "target_exists",
        "target_unwritable",
        "unsupported_source_type",
        "workspace_identity_changed",
        "workspace_missing",
    }
)


class MigrationError(RuntimeError):
    """A path-free migration failure."""

    def __init__(self, code: str) -> None:
        self.code = code if code in MIGRATION_ERROR_CODES else "entry_error"
        super().__init__(f"migration failed: {self.code}")


@dataclass(frozen=True, repr=False)
class MigrationPlan:
    manifest: dict[str, Any] = field(repr=False)
    report: dict[str, Any] = field(repr=False)

    def __repr__(self) -> str:
        return _safe_repr(type(self).__name__, self.report)


@dataclass(frozen=True, repr=False)
class MigrationResult:
    manifest: dict[str, Any] = field(repr=False)
    report: dict[str, Any] = field(repr=False)

    def __repr__(self) -> str:
        return _safe_repr(type(self).__name__, self.report)


def _safe_repr(name: str, report: Mapping[str, Any]) -> str:
    summary = report.get("summary", {})
    files = summary.get("files", 0) if isinstance(summary, Mapping) else 0
    return f"{name}(status={report.get('status')!r}, files={files!r})"


def _absolute(value: os.PathLike[str] | str) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(Path(value).expanduser())))
    except (OSError, TypeError, ValueError) as exc:
        raise MigrationError("invalid_path") from exc


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _linklike(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT)


def _metadata(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MigrationError("path_unreadable") from exc


def _check_components(path: Path) -> None:
    current = path
    components: list[Path] = []
    while True:
        components.append(current)
        if current.parent == current:
            break
        current = current.parent
    for component in reversed(components):
        metadata = _metadata(component)
        if metadata is not None and _linklike(metadata):
            raise MigrationError("link_or_reparse_rejected")


def _reject_remote(path: Path) -> None:
    if os.fspath(path).startswith(("\\\\", "//")):
        raise MigrationError("remote_path_rejected")
    if os.name != "nt" or not path.anchor:
        return
    get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    if int(get_drive_type(path.anchor)) == _DRIVE_REMOTE:
        raise MigrationError("remote_path_rejected")


def _existing_directory(value: os.PathLike[str] | str, code: str) -> Path:
    path = _absolute(value)
    _reject_remote(path)
    _check_components(path)
    metadata = _metadata(path)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode):
        raise MigrationError(code)
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise MigrationError(code) from exc


def _ensure_tree(root: Path, directory: Path) -> None:
    if directory != root and not _inside(root, directory):
        raise MigrationError("target_escape")
    _check_components(directory)
    try:
        root.mkdir(parents=True, exist_ok=True)
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MigrationError("target_unwritable") from exc
    _check_components(directory)
    for candidate in (root, directory):
        metadata = _metadata(candidate)
        if metadata is None or not stat.S_ISDIR(metadata.st_mode):
            raise MigrationError("target_unwritable")


def _default_state_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return _absolute(Path(os.environ["LOCALAPPDATA"]) / "Scriptorium" / "state")
    if os.environ.get("XDG_STATE_HOME"):
        return _absolute(Path(os.environ["XDG_STATE_HOME"]) / "scriptorium")
    return _absolute(Path.home() / ".local" / "state" / "scriptorium")


def _same_path(left: Path, right: Path) -> bool:
    try:
        left_value = os.fspath(left.resolve(strict=False))
        right_value = os.fspath(right.resolve(strict=False))
    except OSError as exc:
        raise MigrationError("state_root_invalid") from exc
    return os.path.normcase(left_value) == os.path.normcase(right_value)


def _private_state_root(
    value: os.PathLike[str] | str | None, workspace: Path
) -> Path:
    canonical = _default_state_root()
    requested = canonical if value is None else _absolute(value)
    if not _same_path(requested, canonical):
        raise MigrationError("noncanonical_state_root")
    root = canonical.resolve(strict=False)
    _reject_remote(root)
    _check_components(root)
    metadata = _metadata(root)
    if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
        raise MigrationError("state_root_invalid")
    if root == workspace or _inside(workspace, root) or _inside(root, workspace):
        raise MigrationError("state_workspace_overlap")
    return root


def _kind(path: Path) -> str | None:
    return _KINDS.get(path.suffix.lower())


def _reject_state_source_overlap(source: Path, private_root: Path) -> None:
    if source == private_root or _inside(source, private_root):
        raise MigrationError("state_root_in_source")
    if _inside(private_root, source):
        raise MigrationError("source_state_overlap")


def _hash_file(path: Path, code: str) -> tuple[str, int]:
    metadata = _metadata(path)
    if metadata is None:
        raise MigrationError(code)
    if _linklike(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise MigrationError(code)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise MigrationError(code) from exc
    return digest.hexdigest(), size


def _selected(
    sources: Iterable[os.PathLike[str] | str] | os.PathLike[str] | str,
) -> list[Path]:
    if isinstance(sources, (str, os.PathLike)):
        values = [sources]
    else:
        try:
            values = list(sources)
        except TypeError as exc:
            raise MigrationError("invalid_sources") from exc
    if not values:
        raise MigrationError("no_sources_selected")
    if any(
        isinstance(value, bytes) or not isinstance(value, (str, os.PathLike))
        for value in values
    ):
        raise MigrationError("invalid_sources")
    return [_absolute(value) for value in values]


def _scan(root: Path) -> list[Path]:
    found: list[Path] = []

    def fail(error: OSError) -> None:
        raise error

    try:
        walker = os.walk(root, topdown=True, onerror=fail, followlinks=False)
        for directory, names, files in walker:
            names.sort(key=str.casefold)
            files.sort(key=str.casefold)
            for name in names:
                metadata = _metadata(Path(directory) / name)
                if metadata is not None and _linklike(metadata):
                    raise MigrationError("link_or_reparse_rejected")
            for name in files:
                path = Path(directory) / name
                metadata = _metadata(path)
                if metadata is not None and _linklike(metadata):
                    raise MigrationError("link_or_reparse_rejected")
                if metadata is not None and stat.S_ISREG(metadata.st_mode) and _kind(path):
                    found.append(path)
    except OSError as exc:
        raise MigrationError("source_unreadable") from exc
    return sorted(found, key=lambda path: os.path.normcase(os.fspath(path)))


def _label(index: int, path: Path, *, file: bool) -> str:
    suffix = path.suffix.lower() if file else ""
    raw = path.stem if file else path.name
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-") or "source"
    return f"{index:03d}-{slug[:64]}{suffix}"


def _relative(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute() or not path.parts:
        raise MigrationError("target_escape")
    if any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in path.parts):
        raise MigrationError("target_escape")
    return path.as_posix()


def _new_internal_name(
    manifest: Mapping[str, Any], entry: Mapping[str, Any], suffix: str
) -> str:
    return (
        f".scriptorium-{manifest['batch_id']}-{entry['id']}-"
        f"{secrets.token_hex(16)}.{suffix}"
    )


def _valid_internal_name(
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    value: Any,
    suffix: str,
) -> bool:
    if not isinstance(value, str) or Path(value).name != value:
        return False
    prefix = f".scriptorium-{manifest['batch_id']}-{entry['id']}-"
    token, separator, actual_suffix = value.removeprefix(prefix).rpartition(".")
    return (
        value.startswith(prefix)
        and separator == "."
        and actual_suffix == suffix
        and _TOKEN_RE.fullmatch(token) is not None
    )


def _stage(
    manifest: Mapping[str, Any], entry: Mapping[str, Any]
) -> Path | None:
    name = entry.get("stage_name")
    if name is None:
        return None
    return Path(entry["target"]).parent / name


def _quarantine(
    manifest: Mapping[str, Any], entry: Mapping[str, Any]
) -> Path | None:
    name = entry.get("quarantine_name")
    if name is None:
        return None
    return Path(entry["target"]).parent / name


def _plan_data(manifest: Mapping[str, Any]) -> dict[str, Any]:
    fixed = (
        "schema_version",
        "privacy",
        "batch_id",
        "workspace",
        "destination_root",
        "state_root",
        "source_roots",
        "limitations",
    )
    entry_fixed = (
        "id",
        "source",
        "target",
        "relative_target",
        "kind",
        "sha256",
        "size",
    )
    data = {key: manifest.get(key) for key in fixed}
    data["entries"] = [
        {key: entry.get(key) for key in entry_fixed}
        for entry in manifest.get("entries", [])
    ]
    return data


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_digest(manifest: Mapping[str, Any]) -> str:
    return _digest(_plan_data(manifest))


def _state_digest(manifest: Mapping[str, Any]) -> str:
    data = copy.deepcopy(dict(manifest))
    data.pop("state_sha256", None)
    return _digest(data)


def _seal_manifest(manifest: dict[str, Any], *, new_plan: bool = False) -> None:
    if new_plan:
        manifest["plan_sha256"] = _plan_digest(manifest)
    manifest["state_sha256"] = _state_digest(manifest)


def _report(
    operation: str,
    status: str,
    entries: list[Mapping[str, Any]],
    *,
    roots: int,
    changed: int = 0,
    unchanged: int = 0,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_VERSION,
        "operation": operation,
        "status": status,
        "summary": {
            "sources_requested": roots,
            "files": len(entries),
            "markdown": sum(entry["kind"] == "markdown" for entry in entries),
            "pdf": sum(entry["kind"] == "pdf" for entry in entries),
            "bytes": sum(entry["size"] for entry in entries),
            "changed": changed,
            "unchanged": unchanged,
        },
        "limitations": list(MIGRATION_LIMITATIONS),
    }


def format_migration_report(report: Mapping[str, Any]) -> str:
    """Render a fixed, path-free aggregate report."""

    operation = report.get("operation")
    if operation not in {"plan", "apply", "load", "verify", "rollback"}:
        operation = "migration"
    status = report.get("status")
    if status not in {
        "planned",
        "applying",
        "applied",
        "unchanged",
        "rolling-back",
        "rolled-back",
    }:
        status = "unknown"
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        summary = {}

    def count(name: str) -> int:
        value = summary.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    return "\n".join(
        (
            f"Migration {operation}: {status}",
            (
                f"Files: {count('files')} "
                f"(Markdown: {count('markdown')}, PDF: {count('pdf')})"
            ),
            f"Bytes: {count('bytes')}",
            f"Changed: {count('changed')}; unchanged: {count('unchanged')}",
            "Private state: canonical local state root; paths suppressed",
        )
    )


def plan_migration(
    sources: Iterable[os.PathLike[str] | str] | os.PathLike[str] | str,
    *,
    workspace: os.PathLike[str] | str,
    batch_id: str,
    state_root: os.PathLike[str] | str | None = None,
) -> MigrationPlan:
    """Build a read-only plan from explicit local inputs."""

    if not isinstance(batch_id, str) or not _BATCH_RE.fullmatch(batch_id):
        raise MigrationError("invalid_batch_id")
    workspace_path = _existing_directory(workspace, "workspace_missing")
    private_root = _private_state_root(state_root, workspace_path)
    destination = workspace_path / "Sources" / "Imported" / batch_id
    requested = _selected(sources)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, source_root in enumerate(requested, start=1):
        _reject_remote(source_root)
        _check_components(source_root)
        metadata = _metadata(source_root)
        if metadata is None:
            raise MigrationError("source_missing")
        if stat.S_ISDIR(metadata.st_mode):
            source_root = source_root.resolve(strict=True)
            _reject_state_source_overlap(source_root, private_root)
            if (
                source_root == destination
                or _inside(source_root, destination)
                or _inside(destination, source_root)
            ):
                raise MigrationError("source_destination_overlap")
            routes = [
                (source, Path(_label(index, source_root, file=False)) / source.relative_to(source_root))
                for source in _scan(source_root)
            ]
        elif stat.S_ISREG(metadata.st_mode):
            source_root = source_root.resolve(strict=True)
            _reject_state_source_overlap(source_root, private_root)
            if not _kind(source_root):
                raise MigrationError("unsupported_source_type")
            if source_root == destination or _inside(destination, source_root):
                raise MigrationError("source_destination_overlap")
            routes = [(source_root, Path(_label(index, source_root, file=True)))]
        else:
            raise MigrationError("source_not_regular")

        for source, relative_path in routes:
            identity = os.path.normcase(os.path.normpath(os.fspath(source)))
            if identity in seen:
                continue
            seen.add(identity)
            relative_target = _relative(relative_path)
            target = _absolute(destination / relative_target)
            digest, size = _hash_file(source, "source_unreadable")
            entry = {
                "id": f"file-{len(entries) + 1:06d}",
                "source": os.fspath(source),
                "target": os.fspath(target),
                "relative_target": relative_target,
                "kind": _kind(source),
                "sha256": digest,
                "size": size,
                "state": "planned",
                "file_identity": None,
                "stage_name": None,
                "rollback_phase": None,
                "quarantine_name": None,
            }
            _check_components(target.parent)
            if _metadata(target) is not None:
                raise MigrationError("target_exists")
            entries.append(entry)

    if not entries:
        raise MigrationError("no_supported_files")
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "privacy": MANIFEST_PRIVACY,
        "batch_id": batch_id,
        "run_state": "planned",
        "workspace": os.fspath(workspace_path),
        "destination_root": os.fspath(destination),
        "state_root": os.fspath(private_root),
        "source_roots": len(requested),
        "entries": entries,
        "limitations": list(MIGRATION_LIMITATIONS),
    }
    _seal_manifest(manifest, new_plan=True)
    return MigrationPlan(
        manifest=manifest,
        report=_report("plan", "planned", entries, roots=len(requested)),
    )


def _unwrap(value: MigrationPlan | MigrationResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, (MigrationPlan, MigrationResult)):
        value = value.manifest
    if not isinstance(value, Mapping):
        raise MigrationError("invalid_manifest")
    return copy.deepcopy(dict(value))


def _validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    manifest = copy.deepcopy(value)
    required = {
        "schema_version", "privacy", "batch_id", "run_state", "workspace",
        "destination_root", "state_root", "source_roots", "entries",
        "limitations", "plan_sha256", "state_sha256",
    }
    if set(manifest) != required:
        raise MigrationError("invalid_manifest")
    if (
        manifest["schema_version"] != MANIFEST_VERSION
        or manifest["privacy"] != MANIFEST_PRIVACY
        or manifest["run_state"] not in _RUN_STATES
        or not isinstance(manifest["batch_id"], str)
        or not _BATCH_RE.fullmatch(manifest["batch_id"])
        or manifest["limitations"] != list(MIGRATION_LIMITATIONS)
        or not isinstance(manifest["source_roots"], int)
        or isinstance(manifest["source_roots"], bool)
        or manifest["source_roots"] < 1
        or not isinstance(manifest["entries"], list)
        or not manifest["entries"]
    ):
        raise MigrationError("invalid_manifest")
    if not all(
        isinstance(manifest[key], str)
        for key in ("workspace", "destination_root", "state_root")
    ):
        raise MigrationError("invalid_manifest")

    workspace = _absolute(manifest["workspace"])
    destination = _absolute(manifest["destination_root"])
    state_root = _absolute(manifest["state_root"])
    if destination != workspace / "Sources" / "Imported" / manifest["batch_id"]:
        raise MigrationError("target_escape")
    if (
        state_root == workspace
        or _inside(workspace, state_root)
        or _inside(state_root, workspace)
    ):
        raise MigrationError("state_workspace_overlap")

    entry_keys = {
        "id", "source", "target", "relative_target", "kind", "sha256",
        "size", "state", "file_identity", "stage_name", "rollback_phase",
        "quarantine_name",
    }
    identifiers: set[str] = set()
    targets: set[str] = set()
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or set(entry) != entry_keys:
            raise MigrationError("invalid_manifest")
        if (
            not isinstance(entry["id"], str)
            or not _ID_RE.fullmatch(entry["id"])
            or entry["id"] in identifiers
            or not isinstance(entry["source"], str)
            or not isinstance(entry["target"], str)
            or not isinstance(entry["relative_target"], str)
            or entry["kind"] not in {"markdown", "pdf"}
            or not isinstance(entry["sha256"], str)
            or not _HASH_RE.fullmatch(entry["sha256"])
            or not isinstance(entry["size"], int)
            or isinstance(entry["size"], bool)
            or entry["size"] < 0
            or entry["state"] not in _ENTRY_STATES
        ):
            raise MigrationError("invalid_manifest")
        identity = entry["file_identity"]
        if identity is not None and (
            not isinstance(identity, dict)
            or set(identity) != {"device", "inode"}
            or any(
                not isinstance(identity[key], int)
                or isinstance(identity[key], bool)
                or identity[key] < 0
                for key in ("device", "inode")
            )
            or identity["inode"] == 0
        ):
            raise MigrationError("invalid_manifest")
        stage_name = entry["stage_name"]
        phase = entry["rollback_phase"]
        quarantine_name = entry["quarantine_name"]
        if stage_name is not None and not _valid_internal_name(
            manifest, entry, stage_name, "stage"
        ):
            raise MigrationError("invalid_manifest")
        if quarantine_name is not None and not _valid_internal_name(
            manifest, entry, quarantine_name, "rollback"
        ):
            raise MigrationError("invalid_manifest")
        if phase is not None and phase not in _ROLLBACK_PHASES:
            raise MigrationError("invalid_manifest")
        if (
            entry["state"] == "planned"
            and any(
                value is not None
                for value in (identity, stage_name, phase, quarantine_name)
            )
        ) or (
            entry["state"] in {"created", "delete-pending"}
            and (identity is None or stage_name is None)
        ) or (
            entry["state"] != "delete-pending"
            and (phase is not None or quarantine_name is not None)
        ) or (
            entry["state"] == "delete-pending"
            and (phase is None or quarantine_name is None)
        ) or (
            (identity is None) != (stage_name is None)
        ):
            raise MigrationError("invalid_state_transition")
        source = Path(entry["source"])
        target = Path(entry["target"])
        relative = _relative(entry["relative_target"])
        expected = _absolute(destination / relative)
        key = os.path.normcase(os.path.normpath(os.fspath(target)))
        if (
            not source.is_absolute()
            or not target.is_absolute()
            or target != expected
            or not _inside(destination, target)
            or _kind(source) != entry["kind"]
            or key in targets
        ):
            raise MigrationError("target_escape")
        identifiers.add(entry["id"])
        targets.add(key)

    if (
        not isinstance(manifest["plan_sha256"], str)
        or not _HASH_RE.fullmatch(manifest["plan_sha256"])
        or not isinstance(manifest["state_sha256"], str)
        or not _HASH_RE.fullmatch(manifest["state_sha256"])
    ):
        raise MigrationError("invalid_manifest")
    if (
        manifest["plan_sha256"] != _plan_digest(manifest)
        or manifest["state_sha256"] != _state_digest(manifest)
    ):
        raise MigrationError("manifest_integrity_failed")
    states = {entry["state"] for entry in manifest["entries"]}
    if not states <= _RUN_ENTRY_STATES[manifest["run_state"]]:
        raise MigrationError("invalid_state_transition")
    return manifest


def _workspace_key(workspace: str) -> str:
    normalized = os.path.normcase(os.path.normpath(workspace)).encode()
    return hashlib.sha256(normalized).hexdigest()[:20]


def _manifest_path(manifest: Mapping[str, Any]) -> Path:
    return (
        Path(manifest["state_root"])
        / "migrations"
        / _workspace_key(manifest["workspace"])
        / f"{manifest['batch_id']}.json"
    )


def _lock_path(manifest: Mapping[str, Any]) -> Path:
    return _manifest_path(manifest).with_suffix(".lock")


def _persist_manifest(manifest: dict[str, Any]) -> None:
    _seal_manifest(manifest)
    path = _manifest_path(manifest)
    _ensure_tree(Path(manifest["state_root"]), path.parent)
    payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    descriptor, name = tempfile.mkstemp(
        prefix=".migration-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise MigrationError("state_write_failed") from exc
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _read_manifest(path: Path) -> dict[str, Any] | None:
    metadata = _metadata(path)
    if metadata is None:
        return None
    if _linklike(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise MigrationError("stored_manifest_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        stored = _validate_manifest(value)
    except (OSError, UnicodeError, json.JSONDecodeError, MigrationError) as exc:
        raise MigrationError("stored_manifest_invalid") from exc
    return stored


def _load_stored(requested: Mapping[str, Any]) -> dict[str, Any] | None:
    stored = _read_manifest(_manifest_path(requested))
    if stored is None:
        return None
    if stored["plan_sha256"] != requested["plan_sha256"]:
        raise MigrationError("batch_conflict")
    return stored


@contextmanager
def _kernel_lock(path: Path):
    """Hold an advisory kernel lock released by close or process exit."""

    _check_components(path.parent)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MigrationError("lock_unavailable") from exc
    _check_components(path.parent)
    metadata = _metadata(path)
    if metadata is not None and (
        _linklike(metadata) or not stat.S_ISREG(metadata.st_mode)
    ):
        raise MigrationError("lock_invalid")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b")
    except OSError as exc:
        raise MigrationError("lock_unavailable") from exc
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    except OSError as exc:
        raise MigrationError("lock_unavailable") from exc
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _stored_identity(
    workspace: os.PathLike[str] | str,
    batch_id: str,
) -> tuple[Path, dict[str, str]]:
    if not isinstance(batch_id, str) or not _BATCH_RE.fullmatch(batch_id):
        raise MigrationError("invalid_batch_id")
    workspace_path = _existing_directory(workspace, "workspace_missing")
    state_root = _private_state_root(None, workspace_path)
    return workspace_path, {
        "workspace": os.fspath(workspace_path),
        "state_root": os.fspath(state_root),
        "batch_id": batch_id,
    }


def load_migration(
    *,
    workspace: os.PathLike[str] | str,
    batch_id: str,
) -> MigrationResult:
    """Load private migration state using only its stable public identity."""

    workspace_path, identity = _stored_identity(workspace, batch_id)
    stored = _read_manifest(_manifest_path(identity))
    if stored is None:
        raise MigrationError("migration_not_found")
    if (
        Path(stored["workspace"]) != workspace_path
        or stored["batch_id"] != batch_id
        or not _same_path(Path(stored["state_root"]), Path(identity["state_root"]))
    ):
        raise MigrationError("stored_manifest_invalid")
    return MigrationResult(
        stored,
        _report(
            "load",
            stored["run_state"],
            stored["entries"],
            roots=stored["source_roots"],
            unchanged=len(stored["entries"]),
        ),
    )


def _content_status(path: Path | None, entry: Mapping[str, Any]) -> str:
    if path is None:
        return "missing"
    metadata = _metadata(path)
    if metadata is None:
        return "missing"
    if _linklike(metadata) or not stat.S_ISREG(metadata.st_mode):
        return "changed"
    digest, size = _hash_file(path, "path_unreadable")
    return "exact" if (digest, size) == (entry["sha256"], entry["size"]) else "changed"


def _identity_from_metadata(metadata: os.stat_result) -> dict[str, int]:
    """Return a stable local identity from already-bound file metadata."""

    if _linklike(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise MigrationError("file_identity_unavailable")
    device = getattr(metadata, "st_dev", None)
    inode = getattr(metadata, "st_ino", None)
    if (
        not isinstance(device, int)
        or isinstance(device, bool)
        or device < 0
        or not isinstance(inode, int)
        or isinstance(inode, bool)
        or inode <= 0
    ):
        raise MigrationError("file_identity_unavailable")
    return {"device": device, "inode": inode}


def _file_identity(path: Path | None) -> dict[str, int] | None:
    """Return a stable local file identity or fail closed when unavailable."""

    if path is None:
        return None
    metadata = _metadata(path)
    if metadata is None:
        return None
    return _identity_from_metadata(metadata)


def _identity_matches(path: Path | None, entry: Mapping[str, Any]) -> bool:
    expected = entry.get("file_identity")
    return isinstance(expected, Mapping) and _file_identity(path) == dict(expected)


def _require_owned_anchor(
    manifest: Mapping[str, Any], entry: Mapping[str, Any]
) -> None:
    anchor = _stage(manifest, entry)
    if (
        _content_status(anchor, entry) != "exact"
        or not _identity_matches(anchor, entry)
    ):
        raise MigrationError("owned_target_changed")


def _require_owned_target(
    manifest: Mapping[str, Any], entry: Mapping[str, Any]
) -> None:
    """Require the published path to remain the anchor's unchanged hard link."""

    target = Path(entry["target"])
    anchor = _stage(manifest, entry)
    if (
        _content_status(target, entry) != "exact"
        or _content_status(anchor, entry) != "exact"
        or not _identity_matches(target, entry)
        or not _identity_matches(anchor, entry)
    ):
        raise MigrationError("owned_target_changed")
    try:
        same_file = os.path.samefile(anchor, target)
    except OSError as exc:
        raise MigrationError("owned_target_changed") from exc
    if not same_file:
        raise MigrationError("owned_target_changed")


def _verify_source(entry: Mapping[str, Any]) -> None:
    actual = _hash_file(Path(entry["source"]), "source_missing")
    if actual != (entry["sha256"], entry["size"]):
        raise MigrationError("source_hash_changed")


def _copy_stage(manifest: dict[str, Any], entry: dict[str, Any]) -> Path:
    """Create or recover one random, manifest-owned same-directory stage."""

    stage = _stage(manifest, entry)
    if stage is not None:
        _require_owned_anchor(manifest, entry)
        return stage
    if entry.get("file_identity") is not None:
        raise MigrationError("owned_target_changed")
    _verify_source(entry)
    target_parent = Path(entry["target"]).parent
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    for _ in range(32):
        name = _new_internal_name(manifest, entry, "stage")
        candidate = target_parent / name
        try:
            descriptor = os.open(candidate, flags, 0o600)
        except FileExistsError:
            # A same-name entry is never adopted or removed.
            continue
        except OSError as exc:
            raise MigrationError("target_unwritable") from exc

        digest = hashlib.sha256()
        size = 0
        identity = None
        try:
            with os.fdopen(descriptor, "wb") as target:
                with Path(entry["source"]).open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                target.flush()
                os.fsync(target.fileno())
                identity = _identity_from_metadata(os.fstat(target.fileno()))
        except OSError as exc:
            # The random entry may no longer be ours after the handle closes.
            # Preserve it for manual inspection instead of unlinking by path.
            raise MigrationError("target_unwritable") from exc
        if (digest.hexdigest(), size) != (entry["sha256"], entry["size"]):
            # Never unlink a path whose ownership was not durably recorded.
            raise MigrationError("source_hash_changed")
        if identity is None:
            raise MigrationError("file_identity_unavailable")

        entry["stage_name"] = name
        entry["file_identity"] = identity
        _persist_manifest(manifest)
        _require_owned_anchor(manifest, entry)
        return candidate
    raise MigrationError("staging_conflict")


def _publish(stage: Path, target: Path) -> None:
    if _metadata(target) is not None:
        raise MigrationError("target_exists")
    try:
        os.link(stage, target)
    except FileExistsError as exc:
        raise MigrationError("target_exists") from exc
    except OSError as exc:
        code = (
            "cross_volume_publish_unsupported"
            if exc.errno == errno.EXDEV
            else "atomic_publish_unavailable"
        )
        raise MigrationError(code) from exc


def _preflight_apply(manifest: Mapping[str, Any], *, new: bool) -> None:
    for entry in manifest["entries"]:
        state = entry["state"]
        target = _content_status(Path(entry["target"]), entry)
        stage = _content_status(_stage(manifest, entry), entry)
        if new or state == "planned":
            if target != "missing":
                raise MigrationError("target_exists")
            _verify_source(entry)
        elif state == "created":
            _require_owned_target(manifest, entry)
        elif state == "creating":
            if target == "changed":
                raise MigrationError("owned_target_changed")
            if target == "exact":
                _require_owned_target(manifest, entry)
            elif stage == "changed":
                raise MigrationError("staging_conflict")
            elif stage == "exact":
                _require_owned_anchor(manifest, entry)
            elif entry.get("stage_name") is not None:
                raise MigrationError("owned_target_changed")
            else:
                _verify_source(entry)
        else:
            raise MigrationError("rollback_in_progress")


def apply_migration(
    plan: MigrationPlan | MigrationResult | Mapping[str, Any],
) -> MigrationResult:
    """Apply a plan with atomic create-if-absent publication."""

    requested = _validate_manifest(_unwrap(plan))
    workspace = _existing_directory(requested["workspace"], "workspace_missing")
    if workspace != Path(requested["workspace"]):
        raise MigrationError("workspace_identity_changed")
    _private_state_root(requested["state_root"], workspace)
    _ensure_tree(Path(requested["state_root"]), _lock_path(requested).parent)

    with _kernel_lock(_lock_path(requested)):
        stored = _load_stored(requested)
        if stored is None:
            if requested["run_state"] != "planned":
                raise MigrationError("stored_manifest_missing")
            current = requested
            _preflight_apply(current, new=True)
            current["run_state"] = "applying"
            _persist_manifest(current)
        else:
            current = stored
            if current["run_state"] == "applied":
                _preflight_apply(current, new=False)
                return MigrationResult(
                    current,
                    _report(
                        "apply", "unchanged", current["entries"],
                        roots=current["source_roots"],
                        unchanged=len(current["entries"]),
                    ),
                )
            if current["run_state"] == "rolled-back":
                raise MigrationError("migration_already_rolled_back")
            if current["run_state"] == "rolling-back":
                raise MigrationError("rollback_in_progress")
            _preflight_apply(current, new=False)

        changed = 0
        for entry in current["entries"]:
            if entry["state"] == "created":
                continue
            target = Path(entry["target"])
            _ensure_tree(workspace, target.parent)
            if entry["state"] == "creating" and _content_status(target, entry) == "exact":
                _require_owned_target(current, entry)
                entry["state"] = "created"
                _persist_manifest(current)
                continue
            if entry["state"] == "planned":
                entry["state"] = "creating"
                _persist_manifest(current)
            stage = _copy_stage(current, entry)
            _publish(stage, target)
            _require_owned_target(current, entry)
            entry["state"] = "created"
            changed += 1
            _persist_manifest(current)

        current["run_state"] = "applied"
        _persist_manifest(current)
        return MigrationResult(
            current,
            _report(
                "apply", "applied", current["entries"],
                roots=current["source_roots"],
                changed=changed,
                unchanged=len(current["entries"]) - changed,
            ),
        )


def _rename_noreplace(source: Path, target: Path) -> None:
    """Atomically move one path without replacing an existing destination."""

    if os.name == "nt":
        try:
            os.rename(source, target)
            return
        except FileExistsError as exc:
            raise MigrationError("quarantine_conflict") from exc
        except OSError as exc:
            if getattr(exc, "winerror", None) in {80, 183}:
                raise MigrationError("quarantine_conflict") from exc
            raise MigrationError("rollback_failed") from exc

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise MigrationError("atomic_quarantine_unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            _AT_FDCWD,
            os.fsencode(source),
            _AT_FDCWD,
            os.fsencode(target),
            _RENAME_NOREPLACE,
        )
        if result == 0:
            return
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise MigrationError("quarantine_conflict")
        if error in {
            errno.ENOSYS,
            errno.EINVAL,
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }:
            raise MigrationError("atomic_quarantine_unavailable")
        raise MigrationError("rollback_failed")

    raise MigrationError("atomic_quarantine_unavailable")


def _require_owned_path(path: Path | None, entry: Mapping[str, Any]) -> None:
    if (
        path is None
        or _content_status(path, entry) != "exact"
        or not _identity_matches(path, entry)
    ):
        raise MigrationError("owned_target_changed")


def _has_owned_identity(path: Path, entry: Mapping[str, Any]) -> bool:
    try:
        return _identity_matches(path, entry)
    except MigrationError as exc:
        if exc.code == "file_identity_unavailable":
            return False
        raise


def _restore_unowned_quarantine(quarantine: Path, source: Path) -> None:
    if _metadata(source) is not None:
        raise MigrationError("rollback_restore_blocked")
    try:
        _rename_noreplace(quarantine, source)
    except MigrationError as exc:
        if exc.code in {"quarantine_conflict", "rollback_failed"}:
            raise MigrationError("rollback_restore_blocked") from exc
        raise
    raise MigrationError("owned_target_changed")


def _move_owned_to_quarantine(
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    source: Path,
) -> None:
    quarantine = _quarantine(manifest, entry)
    if quarantine is None:
        raise MigrationError("invalid_state_transition")
    source_exists = _metadata(source) is not None
    quarantine_exists = _metadata(quarantine) is not None
    if quarantine_exists and source_exists:
        raise MigrationError("quarantine_conflict")
    if not quarantine_exists:
        if not source_exists:
            return
        _rename_noreplace(source, quarantine)
        quarantine_exists = True
    if not quarantine_exists:
        return
    if (
        _content_status(quarantine, entry) != "exact"
        or not _identity_matches(quarantine, entry)
    ):
        _restore_unowned_quarantine(quarantine, source)
    if _metadata(source) is not None and _has_owned_identity(source, entry):
        raise MigrationError("owned_target_changed")


def _delete_owned_quarantine(
    manifest: Mapping[str, Any], entry: Mapping[str, Any]
) -> None:
    quarantine = _quarantine(manifest, entry)
    if quarantine is None or _metadata(quarantine) is None:
        return
    _require_owned_path(quarantine, entry)
    try:
        quarantine.unlink()
    except OSError as exc:
        raise MigrationError("rollback_failed") from exc


def _set_rollback_phase(
    manifest: dict[str, Any],
    entry: dict[str, Any],
    phase: str,
) -> None:
    entry["rollback_phase"] = phase
    entry["quarantine_name"] = _new_internal_name(manifest, entry, "rollback")
    _persist_manifest(manifest)


def _preflight_pending_rollback(
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    allow_recovery: bool,
) -> None:
    phase = entry["rollback_phase"]
    quarantine = _quarantine(manifest, entry)
    quarantine_status = _content_status(quarantine, entry)
    target = Path(entry["target"])
    anchor = _stage(manifest, entry)

    if phase == "target":
        if quarantine_status == "exact" and _identity_matches(quarantine, entry):
            _require_owned_anchor(manifest, entry)
            return
        if quarantine_status != "missing":
            if allow_recovery:
                return
            raise MigrationError("owned_target_changed")
        target_status = _content_status(target, entry)
        if target_status == "exact":
            _require_owned_target(manifest, entry)
        elif target_status == "changed":
            if not allow_recovery:
                raise MigrationError("owned_target_changed")
        else:
            _require_owned_anchor(manifest, entry)
        return

    if phase == "target-quarantined":
        if quarantine_status != "missing":
            _require_owned_path(quarantine, entry)
        _require_owned_anchor(manifest, entry)
        return

    if phase == "anchor":
        if quarantine_status == "exact" and _identity_matches(quarantine, entry):
            return
        if quarantine_status != "missing":
            if allow_recovery:
                return
            raise MigrationError("owned_target_changed")
        if anchor is not None and _metadata(anchor) is not None:
            _require_owned_anchor(manifest, entry)
        return

    if phase == "anchor-quarantined":
        if quarantine_status != "missing":
            _require_owned_path(quarantine, entry)
        return

    raise MigrationError("invalid_state_transition")


def _preflight_rollback(
    manifest: Mapping[str, Any], *, allow_recovery: bool = False
) -> None:
    for entry in manifest["entries"]:
        state = entry["state"]
        target = _content_status(Path(entry["target"]), entry)
        anchor = _content_status(_stage(manifest, entry), entry)
        if state == "created":
            if target == "exact":
                _require_owned_target(manifest, entry)
            elif target == "missing":
                _require_owned_anchor(manifest, entry)
            else:
                raise MigrationError("owned_target_changed")
        elif state == "creating":
            if entry["stage_name"] is None:
                if target != "missing":
                    raise MigrationError("target_exists")
                continue
            if target == "changed":
                raise MigrationError("owned_target_changed")
            if target == "exact":
                _require_owned_target(manifest, entry)
            elif anchor == "exact":
                _require_owned_anchor(manifest, entry)
            elif anchor == "changed":
                raise MigrationError("owned_target_changed")
        elif state == "delete-pending":
            _preflight_pending_rollback(
                manifest, entry, allow_recovery=allow_recovery
            )
        elif state == "planned":
            if target != "missing":
                raise MigrationError("target_exists")


def verify_migration(
    *,
    workspace: os.PathLike[str] | str,
    batch_id: str,
) -> MigrationResult:
    """Verify stored state and owned files without needing the original plan."""

    loaded = load_migration(workspace=workspace, batch_id=batch_id)
    requested = loaded.manifest
    _ensure_tree(Path(requested["state_root"]), _lock_path(requested).parent)
    with _kernel_lock(_lock_path(requested)):
        current = _load_stored(requested)
        if current is None:
            raise MigrationError("migration_not_found")
        if current["run_state"] == "planned":
            _preflight_apply(current, new=True)
        elif current["run_state"] in {"applying", "applied"}:
            _preflight_apply(current, new=False)
        else:
            _preflight_rollback(current)
    return MigrationResult(
        current,
        _report(
            "verify",
            current["run_state"],
            current["entries"],
            roots=current["source_roots"],
            unchanged=len(current["entries"]),
        ),
    )


def rollback_migration(
    applied: MigrationPlan | MigrationResult | Mapping[str, Any],
) -> MigrationResult:
    """Remove unchanged owned files with retryable per-entry state."""

    requested = _validate_manifest(_unwrap(applied))
    workspace = _existing_directory(requested["workspace"], "workspace_missing")
    if workspace != Path(requested["workspace"]):
        raise MigrationError("workspace_identity_changed")
    _private_state_root(requested["state_root"], workspace)
    _ensure_tree(Path(requested["state_root"]), _lock_path(requested).parent)

    with _kernel_lock(_lock_path(requested)):
        current = _load_stored(requested)
        if current is None:
            raise MigrationError("applied_manifest_missing")
        if current["run_state"] == "rolled-back":
            return MigrationResult(
                current,
                _report(
                    "rollback", "unchanged", current["entries"],
                    roots=current["source_roots"],
                    unchanged=len(current["entries"]),
                ),
            )
        if current["run_state"] not in {"applying", "applied", "rolling-back"}:
            raise MigrationError("migration_not_applied")
        _preflight_rollback(current, allow_recovery=True)
        if current["run_state"] != "rolling-back":
            current["run_state"] = "rolling-back"
            _persist_manifest(current)

        changed = 0
        for entry in current["entries"]:
            state = entry["state"]
            target = Path(entry["target"])
            if state == "deleted":
                continue
            if state == "planned":
                entry["state"] = "deleted"
                _persist_manifest(current)
                continue
            if state == "creating" and entry["stage_name"] is None:
                entry["state"] = "deleted"
                _persist_manifest(current)
                continue
            if state != "delete-pending":
                entry["state"] = "delete-pending"
                _set_rollback_phase(current, entry, "target")

            while entry["state"] == "delete-pending":
                phase = entry["rollback_phase"]
                if phase == "target":
                    _move_owned_to_quarantine(current, entry, target)
                    entry["rollback_phase"] = "target-quarantined"
                    _persist_manifest(current)
                    continue
                if phase == "target-quarantined":
                    _delete_owned_quarantine(current, entry)
                    _set_rollback_phase(current, entry, "anchor")
                    continue
                if phase == "anchor":
                    anchor = _stage(current, entry)
                    if anchor is None:
                        raise MigrationError("invalid_state_transition")
                    _move_owned_to_quarantine(current, entry, anchor)
                    entry["rollback_phase"] = "anchor-quarantined"
                    _persist_manifest(current)
                    continue
                if phase == "anchor-quarantined":
                    _delete_owned_quarantine(current, entry)
                    entry["state"] = "deleted"
                    entry["rollback_phase"] = None
                    entry["quarantine_name"] = None
                    changed += 1
                    _persist_manifest(current)
                    continue
                raise MigrationError("invalid_state_transition")

        current["run_state"] = "rolled-back"
        _persist_manifest(current)
        return MigrationResult(
            current,
            _report(
                "rollback", "rolled-back", current["entries"],
                roots=current["source_roots"],
                changed=changed,
                unchanged=len(current["entries"]) - changed,
            ),
        )


def reapply_migration(
    *,
    workspace: os.PathLike[str] | str,
    batch_id: str,
) -> MigrationResult:
    """Resume or repeat apply using only workspace and batch identity."""

    loaded = load_migration(workspace=workspace, batch_id=batch_id)
    return apply_migration(loaded)


__all__ = [
    "MANIFEST_VERSION",
    "MIGRATION_LIMITATIONS",
    "MIGRATION_ERROR_CODES",
    "MigrationError",
    "MigrationPlan",
    "MigrationResult",
    "apply_migration",
    "format_migration_report",
    "load_migration",
    "plan_migration",
    "reapply_migration",
    "rollback_migration",
    "verify_migration",
]
