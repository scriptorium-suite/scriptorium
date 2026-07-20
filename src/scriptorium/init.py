"""Safe, no-clobber initialization for a Scriptorium research workspace."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import secrets
import stat
import tomllib
from datetime import date
from pathlib import Path, PurePath
from typing import Any

from . import __version__
from .config import (
    CONFIG_RELATIVE_PATH,
    ConfigError,
    SuiteConfig,
    load_config,
    render_config,
    resolve_config_path,
)
from .host import HOSTS


PROJECT_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PROJECT_SCHEMA_RE = re.compile(r"project/1\.[0-9]+")
PROJECT_STATUSES = {"planned", "active", "paused", "done", "archived"}
_YAML_IMPLICIT = {
    "null", "~", "true", "false", "yes", "no", "on", "off",
    ".nan", ".inf", "+.inf", "-.inf",
}
_YAML_NUMBER = re.compile(
    r"[+-]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9]+)?|"
    r"\.[0-9_]+(?:[eE][+-]?[0-9]+)?|"
    r"0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+)"
)
_YAML_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[Tt ].*)?")
PROGRESS_BEGIN = "<!-- scriptorium:progress-log:begin -->"
PROGRESS_END = "<!-- scriptorium:progress-log:end -->"
WORKSPACE_DIRECTORIES = ("Projects", "Inbox", "_planning")
CONFIG_REPORT_PATH = str(CONFIG_RELATIVE_PATH).replace("\\", "/")


class InitError(RuntimeError):
    """Initialization input or filesystem safety could not be trusted."""


class _InitConflict(RuntimeError):
    """An existing user-owned path conflicts with the initialization plan."""

    def __init__(self, message: str, *, code: str, root: str, path: str) -> None:
        super().__init__(message)
        self.code = code
        self.root = root
        self.path = path


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _is_linklike(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InitError(f"cannot inspect path safely: {path}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _check_existing_components(path: Path, *, label: str) -> None:
    """Reject link-like components before resolving or touching a requested path."""
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for index, part in enumerate(parts):
        current /= part
        if _is_linklike(current):
            raise InitError(f"{label} contains a symlink, junction, or reparse point")
        if current.exists() and index < len(parts) - 1 and not current.is_dir():
            raise InitError(f"{label} has a non-directory path component")


def _resolve_managed_root(path: Path, *, label: str) -> Path:
    requested = _absolute(path)
    _check_existing_components(requested, label=label)
    if requested.exists() and not requested.is_dir():
        raise InitError(f"{label} is not a directory")
    return requested


def _resolve_linked_repo(path: Path | None) -> Path | None:
    if path is None:
        return None
    requested = _absolute(path)
    _check_existing_components(requested, label="linked repository")
    if not requested.exists():
        raise InitError("linked repository does not exist or cannot be resolved")
    if not requested.is_dir():
        raise InitError("linked repository is not a directory")
    return requested


def _validate_separation(workspace: Path, provenance_home: Path) -> None:
    if (
        workspace == provenance_home
        or workspace in provenance_home.parents
        or provenance_home in workspace.parents
    ):
        raise InitError(
            "workspace and Provenance home must be separate, non-nested directories"
        )


def _validate_config_boundary(
    config_path: Path, workspace: Path, provenance_home: Path
) -> None:
    if (
        config_path == workspace
        or workspace in config_path.parents
        or config_path == provenance_home
        or provenance_home in config_path.parents
    ):
        raise InitError(
            "suite config must be outside the workspace and Provenance home"
        )


def _canonical_hosts(hosts: list[str]) -> tuple[str, ...]:
    if not isinstance(hosts, list) or not hosts:
        raise InitError("at least one supported agent host is required")
    if any(not isinstance(host, str) or host not in HOSTS for host in hosts):
        raise InitError("hosts must contain only supported agent host names")
    if len(set(hosts)) != len(hosts):
        raise InitError("hosts must not contain duplicates")
    return tuple(sorted(set(hosts)))


def _validate_text(project_id: str, title: str, idea: str | None) -> None:
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise InitError("project id must be a non-empty kebab-case identifier")
    if (
        not isinstance(title, str)
        or not title.strip()
        or "\n" in title
        or "\r" in title
        or "\x00" in title
    ):
        raise InitError("project title must be non-empty and single-line")
    if PROGRESS_BEGIN in title or PROGRESS_END in title:
        raise InitError("project title cannot contain managed progress-log markers")
    if idea is not None and not isinstance(idea, str):
        raise InitError("research idea must be text")
    if idea is not None and "\x00" in idea:
        raise InitError("research idea cannot contain NUL bytes")
    if idea is not None and (PROGRESS_BEGIN in idea or PROGRESS_END in idea):
        raise InitError("research idea cannot contain managed progress-log markers")


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _project_payload(
    *,
    project_id: str,
    title: str,
    linked_repo: Path | None,
    idea: str | None,
    updated: date,
) -> bytes:
    repo = str(linked_repo).replace("\\", "/")
    normalized_idea = idea.replace("\r\n", "\n").replace("\r", "\n") if idea else ""
    intuition = (
        normalized_idea.strip()
        if normalized_idea.strip()
        else "<!-- Capture the initial research intuition here. -->"
    )
    text = f"""---
schema_version: project/1.0
project_id: {_yaml_string(project_id)}
title: {_yaml_string(title)}
status: planned
stage: ""
next_actions: []
blocked_by: ""
linked_literature: []
linked_repo: {_yaml_string(repo)}
linked_conversations: {_yaml_string(project_id)}
updated: {_yaml_string(updated.isoformat())}
---

# {title}

## Research intuition

{intuition}

## Research question

<!-- Define the research question after review. -->

## Evidence and literature

<!-- Add source-backed evidence and literature here. -->

## Next actions

<!-- Add reviewed, concrete next actions here. -->

## Progress log

{PROGRESS_BEGIN}
{PROGRESS_END}
"""
    return text.encode("utf-8")


def _strip_yaml_comment(raw: str) -> str:
    quote: str | None = None
    escaped = False
    quote_allowed = True
    index = 0
    while index < len(raw):
        character = raw[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif quote == "'":
            if character == quote:
                if index + 1 < len(raw) and raw[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif character in {'"', "'"} and quote_allowed:
            quote = character
            quote_allowed = False
        elif character == "#" and (index == 0 or raw[index - 1].isspace()):
            return raw[:index].rstrip()
        elif character in "[{,:":
            quote_allowed = True
        elif not character.isspace():
            quote_allowed = False
        index += 1
    return raw.rstrip()


def _yaml_string_scalar(raw: str) -> str | None:
    """Parse a conservative YAML string-scalar subset without type guessing."""
    value = _strip_yaml_comment(raw).strip()
    if not value:
        return None
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, str) else None
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            return None
        inner = value[1:-1]
        if "'" in inner.replace("''", ""):
            return None
        return inner.replace("''", "'")
    lowered = value.lower()
    if (
        re.search(r":[ \t]", value) is not None
        or value.startswith(
            ("[", "]", "{", "}", ",", "&", "*", "!", "|", ">", "%", "@", "`")
        )
        or value.startswith(("- ", "? ", ": "))
        or lowered in _YAML_IMPLICIT
        or _YAML_NUMBER.fullmatch(value)
        or _YAML_DATE.fullmatch(value)
    ):
        return None
    return value


def _yaml_flow_string_items(value: str) -> list[str] | None:
    if not value.startswith("[") or not value.endswith("]"):
        return None
    body = value[1:-1]
    if not body.strip():
        return []
    items: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(body):
        character = body[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif quote == "'":
            if character == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character in "[]{}":
            return None
        elif character == ",":
            item = body[start:index].strip()
            if not item:
                return None
            items.append(item)
            start = index + 1
        index += 1
    if quote is not None or escaped:
        return None
    final = body[start:].strip()
    if not final:
        return None
    items.append(final)
    return items


def _yaml_string_array(raw: str, block_items: list[str]) -> list[str] | None:
    value = _strip_yaml_comment(raw).strip()
    if value:
        flow_items = _yaml_flow_string_items(value)
        if flow_items is None:
            return None
        parsed_items = [_yaml_string_scalar(item) for item in flow_items]
        if any(item is None for item in parsed_items):
            return None
        return [item for item in parsed_items if item is not None]
    if not block_items:
        return None
    parsed_items = [_yaml_string_scalar(item) for item in block_items]
    if any(item is None for item in parsed_items):
        return None
    return [item for item in parsed_items if item is not None]


def _yaml_updated_string(raw: str) -> str | None:
    parsed = _yaml_string_scalar(raw)
    if parsed is not None:
        return parsed
    value = _strip_yaml_comment(raw).strip()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def parse_project_frontmatter(payload: bytes) -> dict[str, Any] | None:
    """Validate all known project/1.x frontmatter fields conservatively."""
    try:
        lines = payload.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return None
    if not lines or lines[0] != "---":
        return None
    try:
        end = next(
            index for index, line in enumerate(lines[1:], start=1)
            if line == "---"
        )
    except StopIteration:
        return None
    required = {"schema_version", "project_id", "title", "status"}
    scalar_fields = required | {
        "stage",
        "blocked_by",
        "linked_repo",
        "linked_conversations",
        "updated",
        "priority",
    }
    sequence_fields = {"next_actions", "linked_literature"}
    known_fields = scalar_fields | sequence_fields
    values: dict[str, list[str]] = {key: [] for key in known_fields}
    block_items: dict[str, list[str]] = {key: [] for key in sequence_fields}
    sequence_parent: str | None = None
    extension_parent: str | None = None
    extension_kind: str | None = None
    for line in lines[1:end]:
        if line.startswith("\t"):
            return None
        if not line or line.isspace() or line.lstrip().startswith("#"):
            continue
        if line.startswith(" "):
            item = re.fullmatch(r" +-[ ]+([^\t]+)", line)
            if sequence_parent is not None:
                if item is None:
                    return None
                block_items[sequence_parent].append(item.group(1))
                continue
            if extension_parent is None:
                return None
            stripped = line.lstrip(" ")
            if extension_kind == "text":
                continue
            if re.fullmatch(r"-[ ]+[^\t]+", stripped):
                current_kind = "list"
            elif re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_-]*:[ \t]*(?:[^\t]*)", stripped
            ):
                current_kind = "mapping"
            else:
                return None
            if extension_kind is None:
                extension_kind = current_kind
            elif extension_kind != current_kind:
                return None
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)", line)
        if not match:
            return None
        key, raw = match.groups()
        semantic_raw = _strip_yaml_comment(raw).strip()
        sequence_parent = (
            key if key in sequence_fields and not semantic_raw else None
        )
        extension_parent = None
        extension_kind = None
        if key not in known_fields:
            if not semantic_raw:
                extension_parent = key
            elif semantic_raw in {"|", "|-", "|+", ">", ">-", ">+"}:
                extension_parent = key
                extension_kind = "text"
        if key in known_fields:
            values[key].append(raw)
    if any(len(values[key]) != 1 for key in required):
        return None
    if any(len(values[key]) > 1 for key in known_fields):
        return None
    result: dict[str, Any] = {}
    for key in scalar_fields:
        if not values[key]:
            continue
        parsed = (
            _yaml_updated_string(values[key][0])
            if key == "updated"
            else _yaml_string_scalar(values[key][0])
        )
        if parsed is None:
            return None
        result[key] = parsed
    for key in sequence_fields:
        if not values[key]:
            continue
        parsed_array = _yaml_string_array(values[key][0], block_items[key])
        if parsed_array is None:
            return None
        result[key] = parsed_array
    if any(key not in result for key in required):
        return None
    if (
        not PROJECT_SCHEMA_RE.fullmatch(result["schema_version"])
        or not PROJECT_ID_RE.fullmatch(result["project_id"])
        or not result["title"]
        or result["status"] not in PROJECT_STATUSES
        or (
            "priority" in result
            and result["priority"] not in {"high", "medium", "low"}
        )
    ):
        return None
    return result


def _frontmatter_identity(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _InitConflict(
            "existing project note is unreadable",
            code="project-invalid",
            root="workspace",
            path=path.name,
        ) from exc
    return parse_project_frontmatter(payload)


def _project_identity_matches(
    identity: dict[str, str] | None, project_id: str
) -> bool:
    return bool(identity is not None and identity["project_id"] == project_id)


def _project_action(path: Path, project_id: str) -> str:
    if _is_linklike(path):
        raise InitError("project note cannot be a symlink, junction, or reparse point")
    if not path.exists():
        return "create"
    identity = _frontmatter_identity(path)
    if _project_identity_matches(identity, project_id):
        return "unchanged"
    raise _InitConflict(
        "existing project note has a different or invalid identity",
        code="project-identity-conflict",
        root="workspace",
        path=f"Projects/{project_id}.md",
    )


def _same_config(
    existing: SuiteConfig,
    *,
    workspace: Path,
    provenance_home: Path,
    hosts: tuple[str, ...],
    project_id: str,
) -> bool:
    existing_workspace = _absolute(existing.workspace)
    existing_home = _absolute(existing.provenance_home)
    return (
        existing_workspace == workspace
        and existing_home == provenance_home
        and tuple(existing.hosts) == hosts
        and existing.default_project == project_id
    )


def _config_from_bytes(payload: bytes) -> SuiteConfig | None:
    try:
        data = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    expected = {
        "format_version", "workspace", "provenance_home", "hosts", "default_project"
    }
    if not isinstance(data, dict) or set(data) != expected:
        return None
    if (
        type(data["format_version"]) is not int
        or data["format_version"] != 1
        or not isinstance(data["workspace"], str)
        or not isinstance(data["provenance_home"], str)
        or not isinstance(data["hosts"], list)
        or not isinstance(data["default_project"], str)
    ):
        return None
    try:
        return SuiteConfig(
            workspace=Path(data["workspace"]),
            provenance_home=Path(data["provenance_home"]),
            hosts=tuple(data["hosts"]),
            default_project=data["default_project"],
            format_version=data["format_version"],
        )
    except ConfigError:
        return None


def _config_action(
    *,
    config_dir: Path | None,
    config_path: Path,
    workspace: Path,
    provenance_home: Path,
    hosts: tuple[str, ...],
    project_id: str,
) -> str:
    if _is_linklike(config_path):
        raise InitError("suite config cannot be a symlink, junction, or reparse point")
    if config_path.exists() and not config_path.is_file():
        raise _InitConflict(
            "suite config path is not a regular file",
            code="config-path-conflict",
            root="config",
            path=CONFIG_REPORT_PATH,
        )
    try:
        existing = load_config(config_dir)
    except ConfigError as exc:
        raise _InitConflict(
            "existing suite config is invalid",
            code="config-invalid",
            root="config",
            path=CONFIG_REPORT_PATH,
        ) from exc
    if existing is None:
        if config_path.exists():
            raise _InitConflict(
                "existing suite config could not be loaded",
                code="config-invalid",
                root="config",
                path=CONFIG_REPORT_PATH,
            )
        return "create"
    if _same_config(
        existing,
        workspace=workspace,
        provenance_home=provenance_home,
        hosts=hosts,
        project_id=project_id,
    ):
        return "unchanged"
    raise _InitConflict(
        "existing suite config selects different roots, hosts, or default project",
        code="config-selection-conflict",
        root="config",
        path=CONFIG_REPORT_PATH,
    )


def _directory_action(path: Path, *, root: str, relative: str) -> str:
    if _is_linklike(path):
        raise InitError(f"managed {root} path contains a link-like object")
    if not path.exists():
        return "create"
    if not path.is_dir():
        raise _InitConflict(
            "managed directory path is occupied by a file",
            code="directory-conflict",
            root=root,
            path=relative,
        )
    return "unchanged"


def _change(root: str, path: str, action: str, kind: str) -> dict[str, str]:
    return {"root": root, "path": path, "kind": kind, "action": action}


def _base_report(
    *,
    workspace: Path,
    provenance_home: Path,
    project_id: str,
    hosts: tuple[str, ...],
    run: bool,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "init",
        "mode": "run" if run else "preview",
        "status": "conflict",
        "exit_code": 1,
        "workspace": str(workspace),
        "provenance_home": str(provenance_home),
        "project_id": project_id,
        "hosts": list(hosts),
        "changes": [],
        "summary": {"create": 0, "unchanged": 0, "conflict": 0},
        "safety": {
            "preview_writes": "none",
            "unmanaged_overwrite": "refused",
            "links": "rejected",
            "workspace_home_separation": "enforced",
            "credentials": "not-requested",
            "hooks": "not-installed",
            "models": "not-invoked",
        },
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "not-invoked",
            "optional_connectors": "not-invoked",
        },
        "limitations": [
            "Agent hosts are selected in config but their adapters are not installed.",
            "No model, browser, connector, authentication, or network action is invoked.",
            "Existing project notes and suite configs are never rewritten by init.",
        ],
    }


class _DirectoryBinding:
    """Held directory identities used for relative, no-follow run operations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[int] = []
        self.leaf: int | None = None

    def close(self) -> None:
        for entry in reversed(self.entries):
            try:
                _win_close(entry) if os.name == "nt" else os.close(entry)
            except OSError:
                pass
        self.entries.clear()
        self.leaf = None


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _posix_require_support() -> None:
    dir_fd = getattr(os, "supports_dir_fd", set())
    follow = getattr(os, "supports_follow_symlinks", set())
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or os.open not in dir_fd
        or os.mkdir not in dir_fd
        or os.stat not in dir_fd
        or os.unlink not in dir_fd
        or os.stat not in follow
    ):
        raise InitError("platform cannot bind managed paths safely")


def _posix_open_root(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InitError("cannot bind managed filesystem root") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise InitError("managed filesystem root is not a directory")
    return descriptor


def _posix_open_child_directory(parent: int, leaf: str, *, create: bool) -> int:
    try:
        before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise InitError("managed directory disappeared during initialization")
        try:
            os.mkdir(leaf, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise InitError("cannot create managed directory") from exc
        try:
            before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise InitError("cannot inspect created managed directory") from exc
    except OSError as exc:
        raise InitError("cannot inspect managed directory") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise InitError("managed directory contains a link-like or non-directory entry")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(leaf, flags, dir_fd=parent)
    except OSError as exc:
        raise InitError("cannot bind managed directory") from exc
    after = os.fstat(descriptor)
    if not stat.S_ISDIR(after.st_mode) or not _same_identity(before, after):
        os.close(descriptor)
        raise InitError("managed directory identity changed during initialization")
    return descriptor


def _open_posix_directory_tree(
    path: Path, *, create: bool = True
) -> _DirectoryBinding:
    _posix_require_support()
    binding = _DirectoryBinding(path)
    try:
        anchor = Path(path.anchor)
        descriptor = _posix_open_root(anchor)
        binding.entries.append(descriptor)
        for part in path.parts[1:]:
            descriptor = _posix_open_child_directory(
                descriptor, part, create=create
            )
            binding.entries.append(descriptor)
        binding.leaf = descriptor
        return binding
    except Exception:
        binding.close()
        raise


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _WINDOWS_DIRECTORY_SHARE = _FILE_SHARE_READ | _FILE_SHARE_WRITE
    _GENERIC_READ = 0x80000000
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _FILE_WRITE_DATA = 0x00000002
    _FILE_WRITE_ATTRIBUTES = 0x00000100
    _FILE_TRAVERSE = 0x00000020
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_OPEN_IF = 3
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _OBJ_DONT_REPARSE = 0x00001000
    _OPEN_EXISTING = 3
    _FILE_BEGIN = 0
    _FILE_DISPOSITION_INFORMATION_CLASS = 13
    _FILE_RENAME_INFORMATION_CLASS = 10
    _STATUS_OBJECT_NAME_COLLISION = 0xC0000035
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IO_STATUS_VALUE(ctypes.Union):
        _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [("value", _IO_STATUS_VALUE), ("Information", ctypes.c_size_t)]

    class _FILE_DISPOSITION_INFORMATION(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    class _RENAME_UNION(ctypes.Union):
        _fields_ = [("ReplaceIfExists", wintypes.BYTE), ("Flags", wintypes.DWORD)]

    class _FILE_RENAME_INFORMATION(ctypes.Structure):
        _anonymous_ = ("choice",)
        _fields_ = [
            ("choice", _RENAME_UNION),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.ULONG),
            ("FileName", wintypes.WCHAR * 1),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _KERNEL32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _CloseHandle = _KERNEL32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _GetFileInformationByHandle = _KERNEL32.GetFileInformationByHandle
    _GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _ReadFile = _KERNEL32.ReadFile
    _ReadFile.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    _ReadFile.restype = wintypes.BOOL
    _WriteFile = _KERNEL32.WriteFile
    _WriteFile.argtypes = _ReadFile.argtypes
    _WriteFile.restype = wintypes.BOOL
    _FlushFileBuffers = _KERNEL32.FlushFileBuffers
    _FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _FlushFileBuffers.restype = wintypes.BOOL
    _SetFilePointerEx = _KERNEL32.SetFilePointerEx
    _SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    _SetFilePointerEx.restype = wintypes.BOOL
    _NTDLL = ctypes.WinDLL("ntdll")
    _NtCreateFile = _NTDLL.NtCreateFile
    _NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
        ctypes.POINTER(_OBJECT_ATTRIBUTES), ctypes.POINTER(_IO_STATUS_BLOCK),
        wintypes.LPVOID, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
        wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG,
    ]
    _NtCreateFile.restype = wintypes.LONG
    _NtSetInformationFile = _NTDLL.NtSetInformationFile
    _NtSetInformationFile.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_IO_STATUS_BLOCK), wintypes.LPVOID,
        wintypes.ULONG, wintypes.ULONG,
    ]
    _NtSetInformationFile.restype = wintypes.LONG


def _win_extended_path(path: Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _nt_u32(status: int) -> int:
    return status & 0xFFFFFFFF


def _nt_object_attributes(root: int, leaf: str):
    buffer = ctypes.create_unicode_buffer(leaf)
    length = len(leaf.encode("utf-16-le"))
    name = _UNICODE_STRING(length, length + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES), root, ctypes.pointer(name),
        _OBJ_CASE_INSENSITIVE | _OBJ_DONT_REPARSE, None, None,
    )
    return attributes, name, buffer


def _win_close(handle: int | None) -> None:
    if os.name == "nt" and handle not in (None, _INVALID_HANDLE_VALUE):
        _CloseHandle(handle)


def _win_info(handle: int) -> Any:
    info = _BY_HANDLE_FILE_INFORMATION()
    if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise InitError("cannot inspect bound Windows handle")
    return info


def _win_validate(handle: int, *, directory: bool) -> Any:
    info = _win_info(handle)
    if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise InitError("managed Windows path is a reparse point")
    if bool(info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY) != directory:
        raise InitError("managed Windows path has the wrong object type")
    return info


def _win_identity(info: Any) -> tuple[int, int, int]:
    return (info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow)


def _win_open_anchor(path: Path) -> int:
    handle = _CreateFileW(
        _win_extended_path(path),
        _FILE_READ_ATTRIBUTES | _FILE_TRAVERSE | _SYNCHRONIZE,
        _WINDOWS_DIRECTORY_SHARE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise InitError("cannot bind managed Windows filesystem root")
    try:
        _win_validate(handle, directory=True)
    except Exception:
        _win_close(handle)
        raise
    return handle


def _nt_open_relative(
    parent: int, leaf: str, *, directory: bool, create: bool, access: int, share: int
) -> int:
    attributes, name, buffer = _nt_object_attributes(parent, leaf)
    handle = wintypes.HANDLE()
    iosb = _IO_STATUS_BLOCK()
    options = _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
    options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
    status_code = _NtCreateFile(
        ctypes.byref(handle), access, ctypes.byref(attributes), ctypes.byref(iosb),
        None, _FILE_ATTRIBUTE_NORMAL, share,
        _FILE_OPEN_IF if create and directory else (_FILE_CREATE if create else _FILE_OPEN),
        options, None, 0,
    )
    _ = (name, buffer)
    if status_code < 0:
        if not directory and create and _nt_u32(status_code) == _STATUS_OBJECT_NAME_COLLISION:
            raise FileExistsError(leaf)
        raise InitError("cannot open a relative managed Windows object")
    result = handle.value
    try:
        _win_validate(result, directory=directory)
    except Exception:
        _win_close(result)
        raise
    return result


def _open_windows_directory_tree(
    path: Path, *, create: bool = True, share: int | None = None
) -> _DirectoryBinding:
    binding = _DirectoryBinding(path)
    try:
        child_share = _WINDOWS_DIRECTORY_SHARE if share is None else share
        handle = _win_open_anchor(Path(path.anchor))
        binding.entries.append(handle)
        for part in path.parts[1:]:
            handle = _nt_open_relative(
                handle,
                part,
                directory=True,
                create=create,
                access=_FILE_READ_ATTRIBUTES | _FILE_TRAVERSE | _SYNCHRONIZE,
                share=child_share,
            )
            binding.entries.append(handle)
        binding.leaf = handle
        return binding
    except Exception:
        binding.close()
        raise


@contextlib.contextmanager
def _bound_directory(path: Path):
    binding = (
        _open_windows_directory_tree(path)
        if os.name == "nt"
        else _open_posix_directory_tree(path)
    )
    try:
        yield binding
    finally:
        binding.close()


def _bound_child_directory(binding: _DirectoryBinding, leaf: str) -> int:
    if binding.leaf is None or not leaf or PurePath(leaf).name != leaf:
        raise InitError("invalid managed directory leaf")
    descriptor = (
        _nt_open_relative(
            binding.leaf,
            leaf,
            directory=True,
            create=True,
            access=_FILE_READ_ATTRIBUTES | _FILE_TRAVERSE | _SYNCHRONIZE,
            share=_WINDOWS_DIRECTORY_SHARE,
        )
        if os.name == "nt"
        else _posix_open_child_directory(binding.leaf, leaf, create=True)
    )
    binding.entries.append(descriptor)
    return descriptor


@contextlib.contextmanager
def _bound_regular_file(parent: int, leaf: str):
    if os.name == "nt":
        handle = _nt_open_relative(
            parent,
            leaf,
            directory=False,
            create=False,
            access=_GENERIC_READ | _SYNCHRONIZE,
            share=_FILE_SHARE_READ,
        )
        try:
            yield handle
        finally:
            _win_close(handle)
        return

    try:
        before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except OSError as exc:
        raise InitError("cannot inspect bound managed file") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise InitError("bound managed file is link-like or not regular")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(leaf, flags, dir_fd=parent)
    except OSError as exc:
        raise InitError("cannot open bound managed file") from exc
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or not _same_identity(before, after):
            raise InitError("bound managed file identity changed")
        yield descriptor
    finally:
        os.close(descriptor)


def _read_held_file(descriptor: int) -> bytes:
    if os.name == "nt":
        if not _SetFilePointerEx(descriptor, 0, None, _FILE_BEGIN):
            raise InitError("cannot rewind bound managed file")
        chunks = []
        while True:
            buffer = ctypes.create_string_buffer(65536)
            read = wintypes.DWORD()
            if not _ReadFile(
                descriptor, buffer, len(buffer), ctypes.byref(read), None
            ):
                raise InitError("cannot read bound managed file")
            if not read.value:
                return b"".join(chunks)
            chunks.append(buffer.raw[: read.value])
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise InitError("cannot read bound managed file") from exc


def _assert_bound_file_current(parent: int, leaf: str, descriptor: int) -> None:
    if os.name == "nt":
        current = _nt_open_relative(
            parent,
            leaf,
            directory=False,
            create=False,
            access=_GENERIC_READ | _SYNCHRONIZE,
            share=_FILE_SHARE_READ,
        )
        try:
            if _win_identity(_win_info(current)) != _win_identity(
                _win_info(descriptor)
            ):
                raise InitError("bound managed file path identity changed")
        finally:
            _win_close(current)
        return
    try:
        current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        held = os.fstat(descriptor)
    except OSError as exc:
        raise InitError("bound managed file path identity changed") from exc
    if stat.S_ISLNK(current.st_mode) or not _same_identity(current, held):
        raise InitError("bound managed file path identity changed")


def _read_bound_file(parent: int, leaf: str) -> bytes:
    with _bound_regular_file(parent, leaf) as descriptor:
        return _read_held_file(descriptor)


def _write_windows_handle(handle: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + 65536]
        buffer = ctypes.create_string_buffer(chunk, len(chunk))
        written = wintypes.DWORD()
        if not _WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            raise InitError("cannot write bound managed file")
        if not written.value:
            raise InitError("cannot write bound managed file")
        offset += written.value
    if not _FlushFileBuffers(handle):
        raise InitError("cannot flush bound managed file")


def _nt_delete_by_handle(handle: int) -> int:
    info = _FILE_DISPOSITION_INFORMATION(1)
    iosb = _IO_STATUS_BLOCK()
    return _NtSetInformationFile(
        handle,
        ctypes.byref(iosb),
        ctypes.byref(info),
        ctypes.sizeof(info),
        _FILE_DISPOSITION_INFORMATION_CLASS,
    )


def _nt_publish_no_replace(handle: int, parent: int, leaf: str) -> int:
    encoded = leaf.encode("utf-16-le")
    size = ctypes.sizeof(_FILE_RENAME_INFORMATION) + len(encoded)
    raw = ctypes.create_string_buffer(size)
    info = ctypes.cast(raw, ctypes.POINTER(_FILE_RENAME_INFORMATION)).contents
    info.Flags = 0
    info.RootDirectory = parent
    info.FileNameLength = len(encoded)
    ctypes.memmove(
        ctypes.addressof(raw) + _FILE_RENAME_INFORMATION.FileName.offset,
        encoded,
        len(encoded),
    )
    iosb = _IO_STATUS_BLOCK()
    return _NtSetInformationFile(
        handle,
        ctypes.byref(iosb),
        raw,
        size,
        _FILE_RENAME_INFORMATION_CLASS,
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if not written:
            raise OSError("short write")
        offset += written


def _create_bound_file(
    parent: int,
    leaf: str,
    payload: bytes,
    *,
    root: str,
    relative: str,
    after_write=None,
    after_publish=None,
) -> None:
    if not leaf or PurePath(leaf).name != leaf:
        raise InitError("invalid managed file leaf")
    if os.name == "nt":
        handle = None
        cleanup_requested = False
        for _attempt in range(128):
            temporary = f".scriptorium-init-{secrets.token_hex(16)}.tmp"
            try:
                handle = _nt_open_relative(
                    parent,
                    temporary,
                    directory=False,
                    create=True,
                    access=(
                        _FILE_READ_ATTRIBUTES
                        | _FILE_WRITE_DATA
                        | _FILE_WRITE_ATTRIBUTES
                        | _DELETE
                        | _SYNCHRONIZE
                    ),
                    share=0,
                )
                break
            except FileExistsError:
                continue
        if handle is None:
            raise InitError("cannot allocate a temporary managed file")
        published = False
        try:
            _write_windows_handle(handle, payload)
            if after_write is not None:
                after_write()
            status_code = _nt_publish_no_replace(handle, parent, leaf)
            if status_code < 0:
                if _nt_u32(status_code) == _STATUS_OBJECT_NAME_COLLISION:
                    raise _InitConflict(
                        "managed file appeared during initialization",
                        code="concurrent-create-conflict",
                        root=root,
                        path=relative,
                    )
                raise InitError("cannot publish bound managed file")
            published = True
            if after_publish is not None:
                try:
                    after_publish()
                except Exception as exc:
                    if _nt_delete_by_handle(handle) < 0:
                        raise InitError(
                            "cannot withdraw invalid config discovery marker"
                        ) from exc
                    published = False
                    cleanup_requested = True
                    raise
        except Exception as exc:
            if not published and not cleanup_requested:
                if _nt_delete_by_handle(handle) < 0:
                    raise InitError(
                        "cannot clean temporary managed file"
                    ) from exc
            raise
        finally:
            _win_close(handle)
        return

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    temporary = ""
    for _attempt in range(128):
        temporary = f".scriptorium-init-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(temporary, flags, 0o644, dir_fd=parent)
            break
        except FileExistsError:
            continue
        except OSError as exc:
            raise InitError("cannot allocate a temporary managed file") from exc
    if descriptor is None:
        raise InitError("cannot allocate a temporary managed file")
    identity = os.fstat(descriptor)
    published = False
    try:
        if not stat.S_ISREG(identity.st_mode):
            raise InitError("created managed file is not regular")
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        if after_write is not None:
            after_write()
        import ctypes as posix_ctypes

        libc = posix_ctypes.CDLL(None, use_errno=True)
        linkat = libc.linkat
        linkat.argtypes = [
            posix_ctypes.c_int,
            posix_ctypes.c_char_p,
            posix_ctypes.c_int,
            posix_ctypes.c_char_p,
            posix_ctypes.c_int,
        ]
        linkat.restype = posix_ctypes.c_int
        destination = os.fsencode(leaf)
        result = linkat(descriptor, b"", parent, destination, 0x1000)
        error_code = posix_ctypes.get_errno()
        if result != 0 and error_code in {errno.EPERM, errno.EINVAL, errno.ENOENT}:
            source = os.fsencode(f"/proc/self/fd/{descriptor}")
            result = linkat(-100, source, parent, destination, 0x400)
            error_code = posix_ctypes.get_errno()
        if result != 0:
            if error_code == errno.EEXIST:
                raise _InitConflict(
                    "managed file appeared during initialization",
                    code="concurrent-create-conflict",
                    root=root,
                    path=relative,
                )
            raise InitError("cannot atomically publish bound managed file")
        published = True
        current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        if not _same_identity(identity, current):
            raise InitError("published managed file identity changed")
        if after_publish is not None:
            try:
                after_publish()
            except Exception as exc:
                try:
                    current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                except OSError:
                    raise InitError(
                        "cannot withdraw invalid config discovery marker"
                    ) from exc
                if not _same_identity(identity, current):
                    raise InitError(
                        "cannot withdraw invalid config discovery marker"
                    ) from exc
                try:
                    os.unlink(leaf, dir_fd=parent)
                    os.fsync(parent)
                except OSError:
                    raise InitError(
                        "cannot withdraw invalid config discovery marker"
                    ) from exc
                published = False
                raise
        os.fsync(parent)
        try:
            temp_current = os.stat(
                temporary, dir_fd=parent, follow_symlinks=False
            )
            if _same_identity(identity, temp_current):
                os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise InitError(
                "managed file was published but temporary cleanup failed"
            ) from exc
        try:
            os.fsync(parent)
        except OSError as exc:
            raise InitError(
                "cannot persist managed temporary cleanup"
            ) from exc
    except Exception:
        try:
            temp_current = os.stat(
                temporary, dir_fd=parent, follow_symlinks=False
            )
            if _same_identity(identity, temp_current):
                os.unlink(temporary, dir_fd=parent)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)


def _assert_binding_current(binding: _DirectoryBinding) -> None:
    if binding.leaf is None:
        raise InitError("managed directory binding is closed")
    if os.name == "nt":
        check = _win_open_anchor(binding.path)
        try:
            if _win_identity(_win_info(check)) != _win_identity(_win_info(binding.leaf)):
                raise InitError("managed directory path identity changed")
        finally:
            _win_close(check)
        return
    try:
        current = os.stat(binding.path, follow_symlinks=False)
        held = os.fstat(binding.leaf)
    except OSError as exc:
        raise InitError("managed directory path identity changed") from exc
    if stat.S_ISLNK(current.st_mode) or not _same_identity(current, held):
        raise InitError("managed directory path identity changed")


def run_init(
    *,
    workspace: Path,
    provenance_home: Path,
    project_id: str,
    title: str,
    hosts: list[str],
    linked_repo: Path | None = None,
    idea: str | None = None,
    config_dir: Path | None = None,
    run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Plan or create the minimal local-first Scriptorium project structure."""
    _validate_text(project_id, title, idea)
    if today is not None and not isinstance(today, date):
        raise InitError("today must be a date")
    canonical_hosts = _canonical_hosts(hosts)
    resolved_workspace = _resolve_managed_root(workspace, label="workspace")
    resolved_home = _resolve_managed_root(provenance_home, label="Provenance home")
    _validate_separation(resolved_workspace, resolved_home)
    resolved_repo = _resolve_linked_repo(linked_repo)

    try:
        config_path = _absolute(resolve_config_path(config_dir))
    except (ConfigError, OSError, RuntimeError) as exc:
        raise InitError("suite config path cannot be resolved") from exc
    _check_existing_components(config_path, label="suite config path")
    _validate_config_boundary(config_path, resolved_workspace, resolved_home)

    report = _base_report(
        workspace=resolved_workspace,
        provenance_home=resolved_home,
        project_id=project_id,
        hosts=canonical_hosts,
        run=run,
    )
    project_path = resolved_workspace / "Projects" / f"{project_id}.md"
    try:
        desired_config = SuiteConfig(
            workspace=resolved_workspace,
            provenance_home=resolved_home,
            hosts=canonical_hosts,
            default_project=project_id,
        )
        config_payload = render_config(desired_config)
    except ConfigError as exc:
        raise InitError("suite config could not be rendered") from exc
    project_payload = _project_payload(
        project_id=project_id,
        title=title,
        linked_repo=resolved_repo or resolved_workspace,
        idea=idea,
        updated=today or date.today(),
    )
    try:
        changes = [
            _change(
                "workspace",
                ".",
                _directory_action(resolved_workspace, root="workspace", relative="."),
                "directory",
            )
        ]
        for relative in WORKSPACE_DIRECTORIES:
            changes.append(
                _change(
                    "workspace",
                    relative,
                    _directory_action(
                        resolved_workspace / relative,
                        root="workspace",
                        relative=relative,
                    ),
                    "directory",
                )
            )
        changes.extend(
            [
                _change(
                    "provenance_home",
                    ".",
                    _directory_action(
                        resolved_home, root="provenance_home", relative="."
                    ),
                    "directory",
                ),
                _change(
                    "config",
                    CONFIG_REPORT_PATH,
                    _config_action(
                        config_dir=config_dir,
                        config_path=config_path,
                        workspace=resolved_workspace,
                        provenance_home=resolved_home,
                        hosts=canonical_hosts,
                        project_id=project_id,
                    ),
                    "file",
                ),
                _change(
                    "workspace",
                    f"Projects/{project_id}.md",
                    _project_action(project_path, project_id),
                    "file",
                ),
            ]
        )
    except _InitConflict as exc:
        report["summary"]["conflict"] = 1
        report["conflict"] = {
            "code": exc.code,
            "root": exc.root,
            "path": exc.path,
            "reason": str(exc),
        }
        return report

    report["changes"] = changes
    for change in changes:
        report["summary"][change["action"]] += 1
    creates = report["summary"]["create"]
    if not run:
        report["status"] = "planned" if creates else "unchanged"
        report["exit_code"] = 0
        return report

    project_change = next(
        change for change in changes
        if change["root"] == "workspace" and change["kind"] == "file"
    )
    config_change = next(
        change for change in changes
        if change["root"] == "config" and change["kind"] == "file"
    )
    try:
        with (
            _bound_directory(resolved_workspace) as workspace_binding,
            _bound_directory(resolved_home) as home_binding,
            _bound_directory(config_path.parent) as config_binding,
        ):
            projects = _bound_child_directory(workspace_binding, "Projects")
            _bound_child_directory(workspace_binding, "Inbox")
            _bound_child_directory(workspace_binding, "_planning")

            # The project is authoritative. Publish or revalidate it before the
            # suite config becomes the final discovery marker.
            if project_change["action"] == "create":
                _create_bound_file(
                    projects,
                    project_path.name,
                    project_payload,
                    root="workspace",
                    relative=project_change["path"],
                )

            with _bound_regular_file(projects, project_path.name) as project_file:
                def verify_project_file() -> None:
                    try:
                        _assert_bound_file_current(
                            projects, project_path.name, project_file
                        )
                        project_data = _read_held_file(project_file)
                    except InitError as exc:
                        raise _InitConflict(
                            "project note changed before config publication",
                            code="project-identity-conflict",
                            root="workspace",
                            path=project_change["path"],
                        ) from exc
                    if not _project_identity_matches(
                        parse_project_frontmatter(project_data), project_id
                    ):
                        raise _InitConflict(
                            "project note changed before config publication",
                            code="project-identity-conflict",
                            root="workspace",
                            path=project_change["path"],
                        )

                verify_project_file()
                _assert_binding_current(workspace_binding)
                _assert_binding_current(home_binding)
                _assert_binding_current(config_binding)

                if config_change["action"] == "create":
                    def verify_discovery_roots() -> None:
                        verify_project_file()
                        _assert_binding_current(workspace_binding)
                        _assert_binding_current(home_binding)
                        _assert_binding_current(config_binding)

                    _create_bound_file(
                        config_binding.leaf,
                        config_path.name,
                        config_payload,
                        root="config",
                        relative=config_change["path"],
                        after_write=verify_discovery_roots,
                        after_publish=verify_discovery_roots,
                    )
                else:
                    verify_project_file()
                    try:
                        current_config = _config_from_bytes(
                            _read_bound_file(config_binding.leaf, config_path.name)
                        )
                    except InitError as exc:
                        raise _InitConflict(
                            "existing suite config changed during initialization",
                            code="config-selection-conflict",
                            root="config",
                            path=config_change["path"],
                        ) from exc
                    if current_config is None or not _same_config(
                        current_config,
                        workspace=resolved_workspace,
                        provenance_home=resolved_home,
                        hosts=canonical_hosts,
                        project_id=project_id,
                    ):
                        raise _InitConflict(
                            "existing suite config changed during initialization",
                            code="config-selection-conflict",
                            root="config",
                            path=config_change["path"],
                        )
    except _InitConflict as exc:
        report["status"] = "conflict"
        report["exit_code"] = 1
        report["summary"]["conflict"] = 1
        report["conflict"] = {
            "code": exc.code,
            "root": exc.root,
            "path": exc.path,
            "reason": str(exc),
        }
        return report
    report["status"] = "initialized" if creates else "unchanged"
    report["exit_code"] = 0
    return report


def format_init_report(report: dict[str, Any]) -> str:
    """Render a stable, content-free initialization report."""
    lines = [
        f"Scriptorium init {report['generated_by']['version']}",
        f"Mode: {report['mode']}",
        f"Project: {report['project_id']}",
        "Network: no action requested",
        "",
    ]
    for change in report.get("changes", []):
        lines.append(
            f"{change['action'].upper():<9} {change['root']}:{change['path']}"
        )
    if report.get("status") == "conflict":
        conflict = report.get("conflict", {})
        lines.append(
            f"CONFLICT  {conflict.get('root', 'managed')}:{conflict.get('path', '.')}"
        )
    lines.extend(
        [
            "",
            f"Result: {report['status'].upper()}",
            "No existing file, hook, credential, model, connector, or network setting was changed.",
        ]
    )
    if report["status"] == "planned":
        lines.append("Next: rerun the same command with --run to initialize.")
    return "\n".join(lines)
