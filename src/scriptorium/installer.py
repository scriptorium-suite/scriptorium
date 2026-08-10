"""Preview-first installer for pinned Scriptorium component profiles."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from . import __version__
from .components import (
    Component,
    ComponentCatalog,
    ComponentCatalogError,
    load_component_catalog,
)


MARKER_NAME = ".scriptorium-install.json"
POWERSHELL_ENV_NAME = "scriptorium-env.ps1"
BASH_ENV_NAME = "scriptorium-env.sh"
REPORT_VERSION = 1
MAX_ASSET_ENTRIES = 100
MAX_ASSET_BYTES = 10 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 300
DOWNLOAD_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 64 * 1024


class InstallError(RuntimeError):
    """A component install plan cannot be executed safely."""

    def __init__(self, message: str, *, code: str = "install_error") -> None:
        super().__init__(message)
        self.code = code


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _is_linklike(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InstallError("cannot inspect install target", code="target_unreadable") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _check_target(target: Path) -> None:
    current = Path(target.anchor) if target.anchor else Path()
    parts = target.parts[1:] if target.anchor else target.parts
    for index, part in enumerate(parts):
        current /= part
        if _is_linklike(current):
            raise InstallError(
                "install target contains a symlink, junction, or reparse point",
                code="target_link",
            )
        if current.exists() and index < len(parts) - 1 and not current.is_dir():
            raise InstallError("install target has a non-directory component", code="target_invalid")
    if target.exists() and not target.is_dir():
        raise InstallError("install target is not a directory", code="target_invalid")
    if target == Path(target.anchor):
        raise InstallError("install target cannot be a filesystem root", code="target_too_broad")
    try:
        user_home = Path.home().resolve(strict=False)
    except (OSError, RuntimeError):
        user_home = None
    if user_home is not None and target.resolve(strict=False) == user_home:
        raise InstallError("install target cannot be the user home directory", code="target_too_broad")


def _selected_components(
    profile: str, catalog: ComponentCatalog
) -> tuple[Component, ...]:
    component_ids = catalog.profiles.get(profile)
    if component_ids is None:
        raise InstallError("unknown component profile", code="unknown_profile")
    return tuple(catalog.components[component_id] for component_id in component_ids)


def _catalog_or_install_error() -> ComponentCatalog:
    try:
        return load_component_catalog()
    except ComponentCatalogError as exc:
        raise InstallError("component catalog is invalid", code="catalog_invalid") from exc


def _existing_marker(target: Path) -> dict[str, object] | None:
    marker = target / MARKER_NAME
    if not marker.exists():
        return None
    if _is_linklike(marker) or not marker.is_file():
        raise InstallError("install marker is not a regular file", code="marker_invalid")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("install marker is unreadable", code="marker_invalid") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"format_version", "profile", "state", "components"}
        or data.get("format_version") != REPORT_VERSION
        or not isinstance(data.get("profile"), str)
        or not data.get("profile")
        or data.get("state") not in {"installing", "installed", "failed"}
        or not isinstance(data.get("components"), dict)
        or not all(
            isinstance(name, str)
            and bool(name)
            and isinstance(revision, str)
            and len(revision) == 40
            and all(character in "0123456789abcdef" for character in revision)
            for name, revision in data["components"].items()
        )
    ):
        raise InstallError("install marker format is unsupported", code="marker_invalid")
    return data


def _component_action(
    component: Component, *, existing: bool, local_asset_ready: bool
) -> dict[str, object]:
    available = component.delivery != "release-asset" or local_asset_ready or (
        component.artifact_status == "published"
    )
    return {
        "component": component.component_id,
        "version": component.version,
        "stability": component.stability,
        "delivery": component.delivery,
        "destination": component.directory,
        "revision": component.revision,
        "action": "verify" if existing else "install",
        "available": available,
        "artifact_name": component.artifact_name,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise InstallError("release asset is unreadable", code="asset_unreadable") from exc
    return digest.hexdigest()


def _download_release_asset(component: Component, destination: Path) -> None:
    if component.artifact_url is None:
        raise InstallError("release asset is unpublished", code="artifact_unpublished")
    request = Request(
        component.artifact_url,
        headers={"User-Agent": f"scriptorium/{__version__}"},
    )
    try:
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            final = urlsplit(response.geturl())
            hostname = final.hostname or ""
            if (
                final.scheme != "https"
                or (
                    hostname != "github.com"
                    and not hostname.endswith(".githubusercontent.com")
                )
                or final.username is not None
                or final.password is not None
                or final.port not in {None, 443}
            ):
                raise InstallError(
                    "release asset redirected outside the trusted host boundary",
                    code="asset_download",
                )
            declared_size = response.headers.get("Content-Length")
            if declared_size is not None:
                try:
                    parsed_size = int(declared_size)
                except ValueError as exc:
                    raise InstallError(
                        "release asset size is invalid", code="asset_download"
                    ) from exc
                if parsed_size < 0 or parsed_size > MAX_ASSET_BYTES:
                    raise InstallError(
                        "release asset is too large", code="asset_invalid"
                    )
            total = 0
            with destination.open("xb") as stream:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ASSET_BYTES:
                        raise InstallError(
                            "release asset is too large", code="asset_invalid"
                        )
                    stream.write(chunk)
    except InstallError:
        raise
    except (OSError, URLError, ValueError) as exc:
        raise InstallError(
            "release asset download failed", code="asset_download"
        ) from exc


def _validate_local_asset(component: Component, asset: Path) -> Path:
    requested = _absolute(asset)
    if _is_linklike(requested) or not requested.is_file():
        raise InstallError("release asset is not a regular file", code="asset_invalid")
    try:
        if requested.stat().st_size > MAX_ASSET_BYTES:
            raise InstallError("release asset is too large", code="asset_invalid")
    except OSError as exc:
        raise InstallError("release asset is unreadable", code="asset_unreadable") from exc
    if requested.name != component.artifact_name:
        raise InstallError("release asset filename does not match the catalog", code="asset_invalid")
    if _file_sha256(requested) != component.artifact_sha256:
        raise InstallError("release asset checksum does not match the catalog", code="asset_checksum")
    return requested


def plan_install(
    *,
    profile: str,
    target: Path,
    asset: Path | None = None,
    catalog: ComponentCatalog | None = None,
) -> dict[str, object]:
    selected_catalog = catalog or _catalog_or_install_error()
    components = _selected_components(profile, selected_catalog)
    release_components = tuple(
        component for component in components if component.delivery == "release-asset"
    )
    if asset is not None and len(release_components) != 1:
        raise InstallError(
            "--asset requires a profile with exactly one release asset",
            code="asset_not_applicable",
        )
    local_asset = (
        _validate_local_asset(release_components[0], asset)
        if asset is not None
        else None
    )
    resolved_target = _absolute(target)
    _check_target(resolved_target)
    marker = _existing_marker(resolved_target) if resolved_target.exists() else None
    if resolved_target.exists():
        unexpected = [
            entry.name
            for entry in resolved_target.iterdir()
            if entry.name != MARKER_NAME
        ]
        if unexpected and marker is None:
            raise InstallError(
                "non-empty install target is not owned by Scriptorium",
                code="target_not_owned",
            )
    for component in components:
        destination = resolved_target / component.directory
        if destination.exists() and _is_linklike(destination):
            raise InstallError("component destination is a link", code="target_link")
    actions = [
        _component_action(
            component,
            existing=(resolved_target / component.directory).exists(),
            local_asset_ready=(
                local_asset is not None and component in release_components
            ),
        )
        for component in components
    ]
    unavailable = sum(not action["available"] for action in actions)
    return {
        "format_version": REPORT_VERSION,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "install",
        "mode": "preview",
        "status": "planned" if not unavailable else "action-required",
        "exit_code": 0,
        "profile": profile,
        "actions": actions,
        "summary": {
            "components": len(actions),
            "available": len(actions) - unavailable,
            "unavailable": unavailable,
        },
        "network": (
            "not-required"
            if local_asset is not None and len(release_components) == len(components)
            else "required-on-run"
        ),
        "writes": "none",
        "limitations": (
            ["One or more release assets have not been published yet."]
            if unavailable
            else []
        ),
    }


def _command_environment() -> dict[str, str]:
    blocked_fragments = ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in blocked_fragments)
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["PYTHONUTF8"] = "1"
    return environment


def _run_command(arguments: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            env=_command_environment(),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallError("component install command timed out", code="command_timeout") from exc
    except OSError as exc:
        raise InstallError("required install command is unavailable", code="command_missing") from exc
    if completed.returncode != 0:
        raise InstallError("component install command failed", code="command_failed")
    return completed.stdout.strip()


def _verify_checkout(path: Path, revision: str) -> None:
    actual = _run_command(["git", "-C", str(path), "rev-parse", "HEAD"])
    if actual != revision:
        raise InstallError("component revision does not match the catalog", code="revision_mismatch")
    if _run_command(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=normal"]
    ):
        raise InstallError("component checkout contains local changes", code="revision_mismatch")


def _install_source(component: Component, destination: Path) -> None:
    _run_command(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            component.repository,
            str(destination),
        ]
    )
    _run_command(
        ["git", "-C", str(destination), "checkout", "--detach", component.revision]
    )
    _verify_checkout(destination, component.revision)
    if component.delivery in {"python-source", "workspace-source"}:
        _run_command(["uv", "sync", "--locked"], cwd=destination)


def _safe_archive_entries(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, ...]:
    entries = tuple(archive.infolist())
    names = [entry.filename for entry in entries]
    folded_names = [name.casefold() for name in names]
    if (
        not entries
        or len(entries) > MAX_ASSET_ENTRIES
        or len(set(names)) != len(names)
        or len(set(folded_names)) != len(folded_names)
    ):
        raise InstallError("release asset entry set is invalid", code="asset_invalid")
    total = 0
    for entry in entries:
        name = entry.filename
        pure = PurePosixPath(name)
        mode = (entry.external_attr >> 16) & 0o170000
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or (pure.parts and ":" in pure.parts[0])
            or mode == stat.S_IFLNK
        ):
            raise InstallError("release asset contains an unsafe entry", code="asset_invalid")
        total += entry.file_size
        if total > MAX_ASSET_BYTES:
            raise InstallError("release asset is too large", code="asset_invalid")
    return entries


def _install_release_asset(
    component: Component, asset: Path, destination: Path
) -> None:
    destination.mkdir()
    stored = destination / component.artifact_name
    shutil.copyfile(asset, stored)
    if _file_sha256(stored) != component.artifact_sha256:
        raise InstallError("release asset changed while copying", code="asset_checksum")
    unpacked = destination / "unpacked"
    unpacked.mkdir()
    try:
        with zipfile.ZipFile(stored, "r") as archive:
            entries = _safe_archive_entries(archive)
            for entry in entries:
                output = unpacked.joinpath(*PurePosixPath(entry.filename).parts)
                if entry.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(entry))
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, InstallError):
            raise
        raise InstallError("release asset could not be unpacked", code="asset_invalid") from exc


def _verify_release_asset(component: Component, destination: Path) -> None:
    stored = destination / str(component.artifact_name)
    if _is_linklike(stored) or not stored.is_file():
        raise InstallError("installed release asset is missing", code="asset_invalid")
    if _file_sha256(stored) != component.artifact_sha256:
        raise InstallError("installed release asset checksum changed", code="asset_checksum")
    unpacked = destination / "unpacked"
    if _is_linklike(unpacked) or not unpacked.is_dir():
        raise InstallError("installed release asset is incomplete", code="asset_invalid")
    try:
        with zipfile.ZipFile(stored, "r") as archive:
            for entry in _safe_archive_entries(archive):
                if entry.is_dir():
                    continue
                output = unpacked.joinpath(*PurePosixPath(entry.filename).parts)
                if _is_linklike(output) or not output.is_file():
                    raise InstallError("installed release asset is incomplete", code="asset_invalid")
                if output.read_bytes() != archive.read(entry):
                    raise InstallError("installed release asset content changed", code="asset_checksum")
    except zipfile.BadZipFile as exc:
        raise InstallError("installed release asset is invalid", code="asset_invalid") from exc


def _write_marker(
    target: Path,
    profile: str,
    components: tuple[Component, ...],
    *,
    state: str,
) -> None:
    marker = target / MARKER_NAME
    temporary = target / f".{MARKER_NAME}.{uuid4().hex}.tmp"
    payload = {
        "format_version": REPORT_VERSION,
        "profile": profile,
        "state": state,
        "components": {
            component.component_id: component.revision for component in components
        },
    }
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        raise InstallError("install marker could not be written", code="marker_invalid") from exc
    try:
        os.replace(temporary, marker)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise InstallError("install marker could not be published", code="marker_invalid") from exc


def _environment_roots(components: tuple[Component, ...]) -> dict[str, str]:
    roots = {}
    for component in components:
        if component.component_id == "scriptorium-spec":
            roots["SCRIPTORIUM_SPEC_ROOT"] = component.directory
        elif component.component_id == "provenance":
            roots["SCRIPTORIUM_PROVENANCE_ROOT"] = component.directory
        elif component.component_id == "steward":
            roots["SCRIPTORIUM_STEWARD_ROOT"] = component.directory
        elif component.component_id == "lectern":
            roots["SCRIPTORIUM_LECTERN_ROOT"] = component.directory
    return roots


def _write_environment_scripts(
    target: Path, components: tuple[Component, ...]
) -> None:
    roots = _environment_roots(components)
    powershell = [
        "$ScriptoriumComponents = Split-Path -Parent $MyInvocation.MyCommand.Path"
    ]
    bash = [
        'SCRIPTORIUM_COMPONENTS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
        "export SCRIPTORIUM_COMPONENTS_ROOT",
    ]
    for name, directory in roots.items():
        powershell.append(
            f"$env:{name} = Join-Path $ScriptoriumComponents '{directory}'"
        )
        bash.extend(
            [
                f'{name}="$SCRIPTORIUM_COMPONENTS_ROOT/{directory}"',
                f"export {name}",
            ]
        )
    executable_dirs = [
        component.directory
        for component in components
        if component.delivery in {"python-source", "workspace-source"}
    ]
    powershell.append("$ScriptoriumCommandPaths = @()")
    for directory in executable_dirs:
        powershell.extend(
            [
                f"$Candidate = Join-Path $ScriptoriumComponents '{directory}\\.venv\\Scripts'",
                "if (Test-Path -LiteralPath $Candidate) { $ScriptoriumCommandPaths += $Candidate }",
            ]
        )
        bash.append(
            f'if [ -d "$SCRIPTORIUM_COMPONENTS_ROOT/{directory}/.venv/bin" ]; then '
            f'PATH="$SCRIPTORIUM_COMPONENTS_ROOT/{directory}/.venv/bin:$PATH"; fi'
        )
    powershell.append(
        "$env:PATH = ($ScriptoriumCommandPaths + @($env:PATH)) -join [IO.Path]::PathSeparator"
    )
    bash.append("export PATH")
    powershell_path = target / POWERSHELL_ENV_NAME
    bash_path = target / BASH_ENV_NAME
    if _is_linklike(powershell_path) or _is_linklike(bash_path):
        raise InstallError("environment script is a link", code="target_link")
    powershell_path.write_text(
        "\n".join(powershell) + "\n", encoding="utf-8"
    )
    bash_path.write_text("\n".join(bash) + "\n", encoding="utf-8")


def execute_install(
    *,
    profile: str,
    target: Path,
    asset: Path | None = None,
    catalog: ComponentCatalog | None = None,
) -> dict[str, object]:
    selected_catalog = catalog or _catalog_or_install_error()
    components = _selected_components(profile, selected_catalog)
    preview = plan_install(
        profile=profile, target=target, asset=asset, catalog=selected_catalog
    )
    if preview["summary"]["unavailable"]:
        raise InstallError(
            "selected profile contains an unpublished release asset",
            code="artifact_unpublished",
        )
    resolved_target = _absolute(target)
    resolved_target.mkdir(parents=True, exist_ok=True)
    _write_marker(resolved_target, profile, components, state="installing")
    try:
        for component in components:
            destination = resolved_target / component.directory
            if destination.exists():
                if component.delivery == "release-asset":
                    _verify_release_asset(component, destination)
                else:
                    _verify_checkout(destination, component.revision)
                continue
            if component.delivery == "release-asset":
                if asset is None:
                    with TemporaryDirectory(prefix="scriptorium-asset-") as temporary:
                        downloaded = Path(temporary) / str(component.artifact_name)
                        _download_release_asset(component, downloaded)
                        _install_release_asset(
                            component,
                            _validate_local_asset(component, downloaded),
                            destination,
                        )
                else:
                    _install_release_asset(
                        component, _validate_local_asset(component, asset), destination
                    )
            else:
                _install_source(component, destination)
        _write_environment_scripts(resolved_target, components)
        _write_marker(resolved_target, profile, components, state="installed")
    except Exception:
        try:
            _write_marker(resolved_target, profile, components, state="failed")
        except OSError:
            pass
        raise
    report = dict(preview)
    report.update(
        {
            "mode": "run",
            "status": "installed",
            "writes": "managed-component-directories-and-marker",
        }
    )
    return report


def format_install_report(report: dict[str, object]) -> str:
    lines = [
        f"Scriptorium install ({report['mode']})",
        f"Profile: {report['profile']}",
        f"Result: {str(report['status']).upper()}",
    ]
    for action in report["actions"]:
        availability = "ready" if action["available"] else "not published"
        lines.append(
            f"- {action['component']} {action['version']}: "
            f"{action['action']} ({availability})"
        )
    lines.append(f"Network on run: {report['network']}")
    if report["mode"] == "run":
        lines.append("Next on Windows: . .\\scriptorium-env.ps1")
    return "\n".join(lines)
