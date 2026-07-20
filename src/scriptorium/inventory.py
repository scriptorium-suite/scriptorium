"""Content-free inventory preview for explicitly selected research sources."""

from __future__ import annotations

import ctypes
import os
import stat
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .init import (
    InitError,
    _DirectoryBinding,
    _open_posix_directory_tree,
    _open_windows_directory_tree,
    _posix_open_child_directory,
)

if os.name == "nt":
    from .init import (
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ,
        _FILE_TRAVERSE,
        _SYNCHRONIZE,
        _nt_open_relative,
        _win_identity,
        _win_info,
    )


MAX_FILES = 20_000
MAX_DIRECTORIES = 10_000
MAX_ENTRIES = 30_000
MAX_ROOTS = 256

_REPARSE_POINT = 0x400
_DRIVE_REMOTE = 4

_SOURCE_SUFFIXES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".pdf": "pdf",
}
_CONVERSATION_SUFFIXES = {".json", ".jsonl", ".html", ".zip"}
_ZOTERO_SUFFIXES = {".bib", ".bibtex", ".ris", ".rdf", ".json"}

_SUMMARY_FIELDS = (
    "roots_requested",
    "roots_scanned",
    "files_seen",
    "candidates",
    "markdown",
    "pdf",
    "ai_conversation",
    "zotero_export",
    "unsupported",
    "reparse_skipped",
)
_ERROR_ORDER = (
    "source_roots_overlap",
    "source_root_remote",
    "source_root_missing",
    "source_root_reparse",
    "source_root_special",
    "source_root_unreadable",
    "scan_limit_reached",
    "source_identity_changed",
    "source_scan_failed",
)
_SAFE_ERROR_CODES = frozenset(_ERROR_ORDER)
_LIMITATIONS = (
    "Candidates are classified by declared source type and filename suffix only; file contents are not validated.",
    "Actual migration, copying, parsing, indexing, and writeback are not implemented by inventory preview.",
    "Duplicate detection and file fingerprints are not calculated.",
    "An over-limit roots_requested value is a lower-bound sentinel; the full iterable is not consumed.",
    "OS, antivirus, filesystem access-time, and external process side effects are not sandboxed or observed.",
    "On Windows, selected objects are metadata-bound and cannot be renamed, deleted, or opened for data write until the preview ends.",
    "Source files remain authoritative at their original locations.",
)


class InventoryError(RuntimeError):
    """The inventory API invocation or an internal invariant was untrustworthy."""


class _InventoryFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ScanBudget:
    def __init__(self) -> None:
        self.entries = 0
        self.directories = 0

    def observe_entry(self) -> None:
        self.entries += 1
        if self.entries > MAX_ENTRIES:
            raise _InventoryFailure("scan_limit_reached")

    def discover_directory(self) -> None:
        self.directories += 1
        if self.directories > MAX_DIRECTORIES:
            raise _InventoryFailure("scan_limit_reached")


class _BoundRoot:
    def __init__(
        self,
        *,
        kind: str,
        path: Path,
        metadata: os.stat_result,
        binding: _DirectoryBinding,
        object_identity: tuple[int, ...],
        component_identities: tuple[tuple[int, ...], ...],
    ) -> None:
        self.kind = kind
        self.path = path
        self.metadata = metadata
        self.binding = binding
        self._object_identity = object_identity
        self.component_identities = component_identities

    @property
    def object_identity(self) -> tuple[int, ...]:
        return self._object_identity

    def close(self) -> None:
        self.binding.close()


class _PosixFrame:
    def __init__(
        self,
        descriptor: int,
        expected: tuple[int, int, int, int, int],
        *,
        owns_descriptor: bool,
    ) -> None:
        self.descriptor = descriptor
        self.expected = expected
        self.owns_descriptor = owns_descriptor
        self.iterator = None


def _empty_summary(*, roots_requested: int) -> dict[str, int]:
    summary = {field: 0 for field in _SUMMARY_FIELDS}
    summary["roots_requested"] = roots_requested
    return summary


def _coerce_group(
    value: Iterable[os.PathLike[str] | str] | None, *, limit: int
) -> tuple[list[Path], bool]:
    if value is None:
        return [], False
    if isinstance(value, (str, bytes, os.PathLike)):
        raise InventoryError("inventory source groups must be path collections")
    try:
        items = iter(value)
    except TypeError as exc:
        raise InventoryError("inventory source groups must be iterable") from exc
    roots: list[Path] = []
    for item in items:
        if len(roots) >= limit:
            return roots, True
        if isinstance(item, bytes) or not isinstance(item, (str, os.PathLike)):
            raise InventoryError("inventory source entries must be path-like")
        try:
            roots.append(Path(item).expanduser())
        except (TypeError, ValueError, OSError) as exc:
            raise InventoryError("inventory source entries must be path-like") from exc
    return roots, False


def _is_unc_syntax(path: Path) -> bool:
    raw = os.fspath(path)
    return raw.startswith(("\\\\", "//"))


def _absolute_without_resolve(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, ValueError, TypeError) as exc:
        raise _InventoryFailure("source_root_unreadable") from exc


def _windows_drive_type(path: Path) -> int:
    if os.name != "nt":
        return 0
    anchor = path.anchor
    if not anchor:
        raise OSError("drive root is unavailable")
    get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    return int(get_drive_type(anchor))


def _is_linklike(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(getattr(metadata, "st_ctime_ns", 0)),
        int(getattr(metadata, "st_mtime_ns", 0)),
        int(metadata.st_mode),
    )


def _object_identity(metadata: os.stat_result) -> tuple[int, int]:
    return (int(metadata.st_dev), int(metadata.st_ino))


def _windows_handle_matches_metadata(handle: int, metadata: os.stat_result) -> bool:
    info = _win_info(handle)
    identity = _win_identity(info)
    file_index = (int(identity[1]) << 32) | int(identity[2])
    return int(metadata.st_ino) == file_index


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _roots_overlap(left: Path, right: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(left), os.fspath(right)))
    except ValueError:
        return False
    normalized_common = os.path.normcase(os.path.normpath(common))
    return normalized_common in {_normalized_path(left), _normalized_path(right)}


def _classify(name: str, kind: str, summary: dict[str, int]) -> None:
    suffix = Path(name).suffix.casefold()
    summary["files_seen"] += 1
    if summary["files_seen"] > MAX_FILES:
        raise _InventoryFailure("scan_limit_reached")

    category: str | None = None
    if kind == "source":
        category = _SOURCE_SUFFIXES.get(suffix)
    elif kind == "conversation" and suffix in _CONVERSATION_SUFFIXES:
        category = "ai_conversation"
    elif kind == "zotero" and suffix in _ZOTERO_SUFFIXES:
        category = "zotero_export"
    elif kind not in {"source", "conversation", "zotero"}:
        raise InventoryError("inventory source kind is unsupported")

    if category is None:
        summary["unsupported"] += 1
        return
    summary[category] += 1
    summary["candidates"] += 1


def _recheck_identity(
    path: Path, expected: tuple[int, int, int, int, int]
) -> None:
    try:
        current = os.lstat(path)
    except (OSError, ValueError) as exc:
        raise _InventoryFailure("source_identity_changed") from exc
    if _is_linklike(current) or _identity(current) != expected:
        raise _InventoryFailure("source_identity_changed")


def _bound_root_metadata(
    path: Path, metadata: os.stat_result, binding: _DirectoryBinding
) -> os.stat_result:
    if binding.leaf is None:
        raise _InventoryFailure("source_identity_changed")
    try:
        if os.name == "nt":
            current = os.lstat(path)
        elif stat.S_ISDIR(metadata.st_mode):
            current = os.fstat(binding.leaf)
        else:
            current = os.stat(
                path.name, dir_fd=binding.leaf, follow_symlinks=False
            )
    except (OSError, ValueError) as exc:
        raise _InventoryFailure("source_identity_changed") from exc
    if _is_linklike(current):
        raise _InventoryFailure("source_identity_changed")
    return current


def _bound_leaf_metadata(
    path: Path, binding: _DirectoryBinding
) -> os.stat_result:
    if binding.leaf is None:
        raise _InventoryFailure("source_root_unreadable")
    try:
        return (
            os.lstat(path)
            if os.name == "nt"
            else os.stat(
                path.name,
                dir_fd=binding.leaf,
                follow_symlinks=False,
            )
        )
    except FileNotFoundError as exc:
        raise _InventoryFailure("source_root_missing") from exc
    except (OSError, ValueError) as exc:
        raise _InventoryFailure("source_root_unreadable") from exc


def _bind_explicit_root(kind: str, path: Path) -> _BoundRoot:
    is_anchor = path == Path(path.anchor)
    directory_path = path if is_anchor else path.parent
    try:
        binding = (
            _open_windows_directory_tree(
                directory_path, create=False, share=_FILE_SHARE_READ
            )
            if os.name == "nt"
            else _open_posix_directory_tree(directory_path, create=False)
        )
    except (InitError, OSError, ValueError) as exc:
        raise _InventoryFailure("source_root_unreadable") from exc

    try:
        if is_anchor:
            if binding.leaf is None:
                raise _InventoryFailure("source_root_unreadable")
            try:
                metadata = (
                    os.lstat(path)
                    if os.name == "nt"
                    else os.fstat(binding.leaf)
                )
            except (OSError, ValueError) as exc:
                raise _InventoryFailure("source_root_unreadable") from exc
        else:
            metadata = _bound_leaf_metadata(path, binding)
            if _is_linklike(metadata):
                raise _InventoryFailure("source_root_reparse")
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    child = (
                        _nt_open_relative(
                            binding.leaf,
                            path.name,
                            directory=True,
                            create=False,
                            access=(
                                _FILE_READ_ATTRIBUTES
                                | _FILE_TRAVERSE
                                | _SYNCHRONIZE
                            ),
                            share=_FILE_SHARE_READ,
                        )
                        if os.name == "nt"
                        else _posix_open_child_directory(
                            binding.leaf, path.name, create=False
                        )
                    )
                except (InitError, OSError, ValueError) as exc:
                    raise _InventoryFailure("source_identity_changed") from exc
                binding.entries.append(child)
                binding.leaf = child
            elif stat.S_ISREG(metadata.st_mode):
                if os.name == "nt":
                    try:
                        file_handle = _nt_open_relative(
                            binding.leaf,
                            path.name,
                            directory=False,
                            create=False,
                            access=_FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                            share=_FILE_SHARE_READ,
                        )
                    except (InitError, OSError, ValueError) as exc:
                        raise _InventoryFailure(
                            "source_identity_changed"
                        ) from exc
                    binding.entries.append(file_handle)
            else:
                raise _InventoryFailure("source_root_special")

        if _is_linklike(metadata):
            raise _InventoryFailure("source_root_reparse")
        if not (
            stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISREG(metadata.st_mode)
        ):
            raise _InventoryFailure("source_root_special")

        current_root = _bound_root_metadata(path, metadata, binding)
        if _identity(current_root) != _identity(metadata):
            raise _InventoryFailure("source_identity_changed")

        if os.name == "nt":
            try:
                root_handle = (
                    binding.entries[-1]
                    if stat.S_ISREG(metadata.st_mode)
                    else binding.leaf
                )
                if root_handle is None or not _windows_handle_matches_metadata(
                    root_handle, current_root
                ):
                    raise _InventoryFailure("source_identity_changed")
                component_identities = tuple(
                    _win_identity(_win_info(handle))
                    for handle in binding.entries
                )
            except (InitError, OSError, ValueError) as exc:
                raise _InventoryFailure("source_identity_changed") from exc
            root_identity = component_identities[-1]
        else:
            component_identity_items: list[tuple[int, ...]] = []
            for descriptor in binding.entries:
                try:
                    current = os.fstat(descriptor)
                except OSError as exc:
                    raise _InventoryFailure("source_identity_changed") from exc
                if _is_linklike(current):
                    raise _InventoryFailure("source_identity_changed")
                component_identity_items.append(_object_identity(current))
            root_identity = _object_identity(current_root)
            if stat.S_ISREG(metadata.st_mode):
                component_identity_items.append(root_identity)
            component_identities = tuple(component_identity_items)

        return _BoundRoot(
            kind=kind,
            path=path,
            metadata=current_root,
            binding=binding,
            object_identity=tuple(root_identity),
            component_identities=component_identities,
        )
    except Exception:
        binding.close()
        raise


def _bound_roots_overlap(bound_roots: list[_BoundRoot]) -> bool:
    for index, left in enumerate(bound_roots):
        for right in bound_roots[index + 1 :]:
            if (
                left.object_identity in right.component_identities
                or right.object_identity in left.component_identities
            ):
                return True
    return False


def _scan_windows_directory(
    bound: _BoundRoot,
    summary: dict[str, int],
    budget: _ScanBudget,
    directory_identities: list[tuple[Path, tuple[int, int, int, int, int]]],
) -> None:
    if bound.binding.leaf is None:
        raise _InventoryFailure("source_identity_changed")
    budget.discover_directory()
    stack = [(bound.path, _identity(bound.metadata), bound.binding.leaf)]
    while stack:
        directory, expected, directory_handle = stack.pop()
        _recheck_identity(directory, expected)
        directory_identities.append((directory, expected))
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    budget.observe_entry()
                    try:
                        entry_metadata = os.stat(
                            entry.path, follow_symlinks=False
                        )
                    except (OSError, ValueError) as exc:
                        raise _InventoryFailure("source_scan_failed") from exc
                    if _is_linklike(entry_metadata):
                        summary["reparse_skipped"] += 1
                    elif stat.S_ISDIR(entry_metadata.st_mode):
                        budget.discover_directory()
                        try:
                            child_handle = _nt_open_relative(
                                directory_handle,
                                entry.name,
                                directory=True,
                                create=False,
                                access=(
                                    _FILE_READ_ATTRIBUTES
                                    | _FILE_TRAVERSE
                                    | _SYNCHRONIZE
                                ),
                                share=_FILE_SHARE_READ,
                            )
                        except (InitError, OSError, ValueError) as exc:
                            raise _InventoryFailure(
                                "source_identity_changed"
                            ) from exc
                        bound.binding.entries.append(child_handle)
                        child_path = directory / entry.name
                        try:
                            current = os.stat(
                                child_path, follow_symlinks=False
                            )
                        except (OSError, ValueError) as exc:
                            raise _InventoryFailure(
                                "source_identity_changed"
                            ) from exc
                        if (
                            _is_linklike(current)
                            or _identity(current) != _identity(entry_metadata)
                            or not _windows_handle_matches_metadata(
                                child_handle, current
                            )
                        ):
                            raise _InventoryFailure("source_identity_changed")
                        stack.append(
                            (child_path, _identity(current), child_handle)
                        )
                    elif stat.S_ISREG(entry_metadata.st_mode):
                        _classify(entry.name, bound.kind, summary)
                    else:
                        raise _InventoryFailure("source_scan_failed")
        except _InventoryFailure:
            raise
        except (OSError, ValueError) as exc:
            raise _InventoryFailure("source_scan_failed") from exc
        _recheck_identity(directory, expected)


def _recheck_posix_descriptor(
    descriptor: int, expected: tuple[int, int, int, int, int]
) -> None:
    try:
        current = os.fstat(descriptor)
    except OSError as exc:
        raise _InventoryFailure("source_identity_changed") from exc
    if _is_linklike(current) or _identity(current) != expected:
        raise _InventoryFailure("source_identity_changed")


def _scan_posix_directory(
    bound: _BoundRoot, summary: dict[str, int], budget: _ScanBudget
) -> None:
    if bound.binding.leaf is None:
        raise _InventoryFailure("source_identity_changed")
    budget.discover_directory()
    frames = [
        _PosixFrame(
            bound.binding.leaf,
            _identity(bound.metadata),
            owns_descriptor=False,
        )
    ]
    try:
        while frames:
            frame = frames[-1]
            if frame.iterator is None:
                _recheck_posix_descriptor(frame.descriptor, frame.expected)
                try:
                    frame.iterator = os.scandir(frame.descriptor)
                except (OSError, ValueError) as exc:
                    raise _InventoryFailure("source_scan_failed") from exc
            try:
                entry = next(frame.iterator)
            except StopIteration:
                frame.iterator.close()
                frame.iterator = None
                _recheck_posix_descriptor(frame.descriptor, frame.expected)
                frames.pop()
                if frame.owns_descriptor:
                    os.close(frame.descriptor)
                    frame.owns_descriptor = False
                continue
            except (OSError, ValueError) as exc:
                raise _InventoryFailure("source_scan_failed") from exc
            budget.observe_entry()
            try:
                entry_metadata = os.stat(
                    entry.name,
                    dir_fd=frame.descriptor,
                    follow_symlinks=False,
                )
            except (OSError, ValueError) as exc:
                raise _InventoryFailure("source_scan_failed") from exc
            if _is_linklike(entry_metadata):
                summary["reparse_skipped"] += 1
            elif stat.S_ISDIR(entry_metadata.st_mode):
                budget.discover_directory()
                child_descriptor: int | None = None
                try:
                    child_descriptor = _posix_open_child_directory(
                        frame.descriptor, entry.name, create=False
                    )
                    current = os.fstat(child_descriptor)
                except (InitError, OSError, ValueError) as exc:
                    if child_descriptor is not None:
                        try:
                            os.close(child_descriptor)
                        except OSError:
                            pass
                    raise _InventoryFailure(
                        "source_identity_changed"
                    ) from exc
                if _identity(current) != _identity(entry_metadata):
                    os.close(child_descriptor)
                    raise _InventoryFailure("source_identity_changed")
                frames.append(
                    _PosixFrame(
                        child_descriptor,
                        _identity(current),
                        owns_descriptor=True,
                    )
                )
            elif stat.S_ISREG(entry_metadata.st_mode):
                _classify(entry.name, bound.kind, summary)
            else:
                raise _InventoryFailure("source_scan_failed")
    finally:
        for frame in reversed(frames):
            if frame.iterator is not None:
                frame.iterator.close()
            if frame.owns_descriptor:
                try:
                    os.close(frame.descriptor)
                except OSError:
                    pass


def _scan_bound_root(
    bound: _BoundRoot,
    summary: dict[str, int],
    budget: _ScanBudget,
    directory_identities: list[tuple[Path, tuple[int, int, int, int, int]]],
) -> None:
    if stat.S_ISREG(bound.metadata.st_mode):
        _classify(bound.path.name, bound.kind, summary)
        current = _bound_root_metadata(
            bound.path, bound.metadata, bound.binding
        )
        if _identity(current) != _identity(bound.metadata):
            raise _InventoryFailure("source_identity_changed")
        return
    if os.name == "nt":
        _scan_windows_directory(
            bound, summary, budget, directory_identities
        )
    else:
        _scan_posix_directory(bound, summary, budget)


def _routing_preview(summary: dict[str, int]) -> dict[str, int]:
    return {
        "workspace-review": summary["markdown"],
        "literature-reference": summary["pdf"],
        "provenance-import-review": summary["ai_conversation"],
        "steward-review": summary["zotero_export"],
    }


def _error_items(errors: Counter[str]) -> list[dict[str, int | str]]:
    return [
        {"code": code, "count": errors[code]}
        for code in _ERROR_ORDER
        if errors[code]
    ]


def _build_report(
    *, summary: dict[str, int], status: str, errors: Counter[str]
) -> dict[str, Any]:
    exit_code = 1 if status == "partial" else 0
    if status == "partial":
        action_required = [
            {
                "type": "resolve-source-roots",
                "count": max(sum(errors.values()), 1),
            }
        ]
    elif summary["candidates"]:
        action_required = [
            {
                "type": "review-routing-preview",
                "count": summary["candidates"],
            }
        ]
    else:
        action_required = []
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "inventory",
        "mode": "preview",
        "status": status,
        "exit_code": exit_code,
        "summary": dict(summary),
        "routing_preview": _routing_preview(summary),
        "action_required": action_required,
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
        "errors": _error_items(errors),
        "limitations": list(_LIMITATIONS),
    }


def run_inventory(
    *,
    sources: Iterable[os.PathLike[str] | str] | None,
    conversation_exports: Iterable[os.PathLike[str] | str] | None,
    zotero_exports: Iterable[os.PathLike[str] | str] | None,
) -> dict[str, Any]:
    """Build a metadata-only inventory for explicitly selected local roots."""

    groups: list[tuple[str, list[Path]]] = []
    remaining = MAX_ROOTS
    for kind, value in (
        ("source", sources),
        ("conversation", conversation_exports),
        ("zotero", zotero_exports),
    ):
        roots, exceeded = _coerce_group(value, limit=remaining)
        groups.append((kind, roots))
        remaining -= len(roots)
        if exceeded:
            return _build_report(
                summary=_empty_summary(roots_requested=MAX_ROOTS + 1),
                status="partial",
                errors=Counter({"scan_limit_reached": 1}),
            )
    requested = [(kind, path) for kind, paths in groups for path in paths]
    if not requested:
        raise InventoryError("at least one explicit source root is required")

    summary = _empty_summary(roots_requested=len(requested))
    errors: Counter[str] = Counter()
    absolute_roots: list[tuple[str, Path]] = []
    for kind, path in requested:
        try:
            if _is_unc_syntax(path):
                raise _InventoryFailure("source_root_remote")
            absolute = _absolute_without_resolve(path)
            if _is_unc_syntax(absolute):
                raise _InventoryFailure("source_root_remote")
            if os.name == "nt" and _windows_drive_type(absolute) == _DRIVE_REMOTE:
                raise _InventoryFailure("source_root_remote")
            absolute_roots.append((kind, absolute))
        except _InventoryFailure as exc:
            errors[exc.code] += 1
        except (AttributeError, OSError, ValueError):
            errors["source_root_unreadable"] += 1

    if not errors:
        for index, (_kind, root) in enumerate(absolute_roots):
            for _other_kind, other in absolute_roots[index + 1 :]:
                if _roots_overlap(root, other):
                    errors["source_roots_overlap"] += 1

    if errors:
        return _build_report(
            summary=_empty_summary(roots_requested=len(requested)),
            status="partial",
            errors=errors,
        )

    directory_identities: list[
        tuple[Path, tuple[int, int, int, int, int]]
    ] = []
    bound_roots: list[_BoundRoot] = []
    try:
        for kind, root in absolute_roots:
            bound_roots.append(_bind_explicit_root(kind, root))
        if _bound_roots_overlap(bound_roots):
            raise _InventoryFailure("source_roots_overlap")

        budget = _ScanBudget()
        for bound in bound_roots:
            _scan_bound_root(
                bound, summary, budget, directory_identities
            )
        for directory, expected in directory_identities:
            _recheck_identity(directory, expected)
        for bound in bound_roots:
            current = _bound_root_metadata(
                bound.path, bound.metadata, bound.binding
            )
            if _identity(current) != _identity(bound.metadata):
                raise _InventoryFailure("source_identity_changed")
    except _InventoryFailure as exc:
        errors[exc.code] += 1
        report = _build_report(
            summary=_empty_summary(roots_requested=len(requested)),
            status="partial",
            errors=errors,
        )
    except Exception as exc:
        raise InventoryError("inventory scan failed unexpectedly") from exc
    else:
        summary["roots_scanned"] = len(bound_roots)
        report = _build_report(
            summary=summary,
            status="planned" if summary["candidates"] else "noop",
            errors=errors,
        )
    finally:
        for bound in reversed(bound_roots):
            bound.close()
    return report


def _safe_count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def format_inventory_report(report: dict[str, Any]) -> str:
    """Render only allowlisted aggregate inventory facts."""

    if not isinstance(report, dict):
        raise InventoryError("inventory report must be an object")
    summary_value = report.get("summary")
    summary = summary_value if isinstance(summary_value, dict) else {}
    routing_value = report.get("routing_preview")
    routing = routing_value if isinstance(routing_value, dict) else {}
    status = report.get("status")
    safe_status = status if status in {"planned", "noop", "partial"} else "unknown"
    errors_value = report.get("errors")
    safe_errors: list[str] = []
    if isinstance(errors_value, list):
        for item in errors_value:
            if not isinstance(item, dict) or item.get("code") not in _SAFE_ERROR_CODES:
                continue
            count = _safe_count(item.get("count"))
            if count:
                safe_errors.append(f"{item['code']}={count}")

    lines = [
        f"Scriptorium inventory {__version__}",
        f"Status: {safe_status.upper()}",
        (
            "Roots: "
            f"{_safe_count(summary.get('roots_scanned'))}/"
            f"{_safe_count(summary.get('roots_requested'))} scanned"
        ),
        f"Files seen: {_safe_count(summary.get('files_seen'))}",
        f"Candidates: {_safe_count(summary.get('candidates'))}",
        (
            "Categories: "
            f"markdown={_safe_count(summary.get('markdown'))}, "
            f"pdf={_safe_count(summary.get('pdf'))}, "
            f"ai_conversation={_safe_count(summary.get('ai_conversation'))}, "
            f"zotero_export={_safe_count(summary.get('zotero_export'))}, "
            f"unsupported={_safe_count(summary.get('unsupported'))}, "
            f"reparse_skipped={_safe_count(summary.get('reparse_skipped'))}"
        ),
        (
            "Routing preview: "
            f"workspace-review={_safe_count(routing.get('workspace-review'))}, "
            f"literature-reference={_safe_count(routing.get('literature-reference'))}, "
            "provenance-import-review="
            f"{_safe_count(routing.get('provenance-import-review'))}, "
            f"steward-review={_safe_count(routing.get('steward-review'))}"
        ),
    ]
    if safe_errors:
        lines.append("Errors: " + ", ".join(safe_errors))
    lines.append(
        "Safety: no writes; content not read; paths suppressed; links not followed."
    )
    if safe_status == "partial":
        lines.append("Next: resolve the explicit source roots and rerun inventory.")
    elif safe_status == "noop":
        lines.append("Next: no supported candidates were found; no migration is planned.")
    elif safe_status == "planned":
        lines.append(
            "Next: review the aggregate routing preview; actual migration is not implemented."
        )
    else:
        lines.append("Next: no trusted inventory action is available.")
    return "\n".join(lines)
