"""Strict suite-level configuration loading and rendering."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


CONFIG_ENV = "SCRIPTORIUM_CONFIG_DIR"
CONFIG_RELATIVE_PATH = Path("scriptorium") / "config.toml"
SUPPORTED_HOSTS = frozenset({"codex", "claude-code"})
_FIELDS = frozenset(
    {"format_version", "workspace", "provenance_home", "hosts", "default_project"}
)
_PROJECT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class ConfigError(ValueError):
    """Raised when the suite configuration is invalid or unreadable."""


def _is_absolute(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _validate_path(name: str, value: object) -> Path:
    if not isinstance(value, Path):
        raise ConfigError(f"{name} must be a Path")
    if not _is_absolute(str(value)):
        raise ConfigError(f"{name} must be an absolute path")
    return value


def _path_from_toml(name: str, value: object) -> Path:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    if not _is_absolute(value):
        raise ConfigError(f"{name} must be an absolute path")
    return Path(value)


def _validate_hosts(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ConfigError("hosts must be an array")
    if not value:
        raise ConfigError("hosts must not be empty")
    if any(not isinstance(host, str) or host not in SUPPORTED_HOSTS for host in value):
        raise ConfigError("hosts contains an unsupported host")
    hosts = tuple(value)
    if len(set(hosts)) != len(hosts):
        raise ConfigError("hosts must not contain duplicates")
    if hosts != tuple(sorted(hosts)):
        raise ConfigError("hosts must be sorted")
    return hosts


def _validate_project_id(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError("default_project must be a string")
    if _PROJECT_ID.fullmatch(value) is None:
        raise ConfigError("default_project must be a kebab-case identifier")
    return value


@dataclass(frozen=True, slots=True)
class SuiteConfig:
    """Validated suite orchestration configuration."""

    workspace: Path
    provenance_home: Path
    hosts: tuple[str, ...]
    default_project: str
    format_version: int = 1

    def __post_init__(self) -> None:
        if type(self.format_version) is not int or self.format_version != 1:
            raise ConfigError("format_version must be the integer 1")
        _validate_path("workspace", self.workspace)
        _validate_path("provenance_home", self.provenance_home)
        validated_hosts = _validate_hosts(self.hosts)
        _validate_project_id(self.default_project)
        object.__setattr__(self, "hosts", validated_hosts)


def resolve_config_path(config_dir: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the suite config path without reading or creating it."""

    if config_dir is not None:
        family_root = Path(config_dir).expanduser()
    elif CONFIG_ENV in os.environ:
        family_root = Path(os.environ[CONFIG_ENV]).expanduser()
    else:
        family_root = Path.home() / ".config" / "scriptorium"

    if not family_root.is_absolute():
        raise ConfigError("configuration root must be an absolute path")
    return family_root / CONFIG_RELATIVE_PATH


def load_config(
    config_dir: str | os.PathLike[str] | None = None,
) -> SuiteConfig | None:
    """Load strict suite configuration, or return ``None`` when absent."""

    path = resolve_config_path(config_dir)
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError("unable to read suite configuration") from exc

    try:
        data = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("suite configuration is not valid UTF-8 TOML") from exc

    unknown = set(data) - _FIELDS
    if unknown:
        raise ConfigError(f"unknown configuration fields: {', '.join(sorted(unknown))}")
    missing = _FIELDS - set(data)
    if missing:
        raise ConfigError(f"missing configuration fields: {', '.join(sorted(missing))}")

    if type(data["format_version"]) is not int or data["format_version"] != 1:
        raise ConfigError("format_version must be the integer 1")

    return SuiteConfig(
        format_version=data["format_version"],
        workspace=_path_from_toml("workspace", data["workspace"]),
        provenance_home=_path_from_toml("provenance_home", data["provenance_home"]),
        hosts=_validate_hosts(data["hosts"]),
        default_project=_validate_project_id(data["default_project"]),
    )


def render_config(config: SuiteConfig) -> bytes:
    """Render a validated configuration as canonical UTF-8 TOML."""

    if not isinstance(config, SuiteConfig):
        raise TypeError("config must be a SuiteConfig")
    SuiteConfig(
        format_version=config.format_version,
        workspace=config.workspace,
        provenance_home=config.provenance_home,
        hosts=config.hosts,
        default_project=config.default_project,
    )

    quote = lambda value: json.dumps(value, ensure_ascii=False)
    hosts = ", ".join(quote(host) for host in config.hosts)
    payload = (
        f"format_version = {config.format_version}\n"
        f"workspace = {quote(str(config.workspace))}\n"
        f"provenance_home = {quote(str(config.provenance_home))}\n"
        f"hosts = [{hosts}]\n"
        f"default_project = {quote(config.default_project)}\n"
    )
    return payload.encode("utf-8")
