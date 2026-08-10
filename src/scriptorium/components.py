"""Versioned component catalog for the Scriptorium product entry point."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib import resources


ASSET_PACKAGE = "scriptorium.assets"
CATALOG_NAME = "component-catalog.toml"
CATALOG_VERSION = 1
COMPONENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DIRECTORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STABILITIES = {"stable", "candidate", "experimental"}
DELIVERIES = {"source", "python-source", "workspace-source", "release-asset"}


class ComponentCatalogError(RuntimeError):
    """The packaged component catalog is missing or internally inconsistent."""


@dataclass(frozen=True)
class Component:
    component_id: str
    display_name: str
    version: str
    repository: str
    revision: str
    stability: str
    delivery: str
    directory: str
    owner: str | None = None
    artifact_name: str | None = None
    artifact_status: str | None = None
    artifact_sha256: str | None = None


@dataclass(frozen=True)
class ComponentCatalog:
    default_profile: str
    profiles: dict[str, tuple[str, ...]]
    components: dict[str, Component]


def _required_string(data: dict[str, object], name: str, *, where: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ComponentCatalogError(f"{where}.{name} must be a non-empty string")
    return value


def _load_component(component_id: str, raw: object) -> Component:
    where = f"components.{component_id}"
    if not COMPONENT_ID_RE.fullmatch(component_id) or not isinstance(raw, dict):
        raise ComponentCatalogError(f"{where} is invalid")
    data = dict(raw)
    allowed = {
        "display_name",
        "version",
        "repository",
        "revision",
        "stability",
        "delivery",
        "directory",
        "owner",
        "artifact_name",
        "artifact_status",
        "artifact_sha256",
    }
    extra = set(data) - allowed
    if extra:
        raise ComponentCatalogError(f"{where} has unsupported fields")
    revision = _required_string(data, "revision", where=where)
    if not REVISION_RE.fullmatch(revision):
        raise ComponentCatalogError(f"{where}.revision must be a full lowercase commit id")
    repository = _required_string(data, "repository", where=where)
    if not repository.startswith("https://github.com/") or not repository.endswith(".git"):
        raise ComponentCatalogError(f"{where}.repository must be an HTTPS GitHub Git URL")
    stability = _required_string(data, "stability", where=where)
    if stability not in STABILITIES:
        raise ComponentCatalogError(f"{where}.stability is invalid")
    delivery = _required_string(data, "delivery", where=where)
    if delivery not in DELIVERIES:
        raise ComponentCatalogError(f"{where}.delivery is invalid")
    owner = data.get("owner")
    artifact_name = data.get("artifact_name")
    artifact_status = data.get("artifact_status")
    artifact_sha256 = data.get("artifact_sha256")
    for name, value in (
        ("owner", owner),
        ("artifact_name", artifact_name),
        ("artifact_status", artifact_status),
        ("artifact_sha256", artifact_sha256),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ComponentCatalogError(f"{where}.{name} must be a non-empty string")
    if delivery == "release-asset" and not artifact_name:
        raise ComponentCatalogError(f"{where}.artifact_name is required")
    if delivery == "release-asset" and (
        not isinstance(artifact_sha256, str)
        or not SHA256_RE.fullmatch(artifact_sha256)
    ):
        raise ComponentCatalogError(f"{where}.artifact_sha256 is required")
    directory = _required_string(data, "directory", where=where)
    if not DIRECTORY_RE.fullmatch(directory) or directory in {".", ".."}:
        raise ComponentCatalogError(f"{where}.directory must be one safe path segment")
    return Component(
        component_id=component_id,
        display_name=_required_string(data, "display_name", where=where),
        version=_required_string(data, "version", where=where),
        repository=repository,
        revision=revision,
        stability=stability,
        delivery=delivery,
        directory=directory,
        owner=owner,
        artifact_name=artifact_name,
        artifact_status=artifact_status,
        artifact_sha256=artifact_sha256,
    )


def load_component_catalog() -> ComponentCatalog:
    try:
        payload = resources.files(ASSET_PACKAGE).joinpath(CATALOG_NAME).read_bytes()
        raw = tomllib.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ComponentCatalogError("component catalog is unreadable") from exc
    if set(raw) != {"catalog_version", "default_profile", "profiles", "components"}:
        raise ComponentCatalogError("component catalog top-level fields are invalid")
    if raw["catalog_version"] != CATALOG_VERSION:
        raise ComponentCatalogError("unsupported component catalog version")
    raw_components = raw["components"]
    raw_profiles = raw["profiles"]
    if not isinstance(raw_components, dict) or not isinstance(raw_profiles, dict):
        raise ComponentCatalogError("components and profiles must be tables")
    components = {
        component_id: _load_component(component_id, value)
        for component_id, value in raw_components.items()
    }
    for component in components.values():
        if component.owner is not None and component.owner not in components:
            raise ComponentCatalogError(
                f"components.{component.component_id}.owner is unknown"
            )
    profiles: dict[str, tuple[str, ...]] = {}
    for profile, value in raw_profiles.items():
        if not COMPONENT_ID_RE.fullmatch(profile) or not isinstance(value, list):
            raise ComponentCatalogError(f"profiles.{profile} is invalid")
        if not value or not all(isinstance(item, str) for item in value):
            raise ComponentCatalogError(f"profiles.{profile} must be a non-empty string array")
        component_ids = tuple(value)
        if len(set(component_ids)) != len(component_ids):
            raise ComponentCatalogError(f"profiles.{profile} contains duplicates")
        if any(component_id not in components for component_id in component_ids):
            raise ComponentCatalogError(f"profiles.{profile} references an unknown component")
        profiles[profile] = component_ids
    default_profile = raw["default_profile"]
    if not isinstance(default_profile, str) or default_profile not in profiles:
        raise ComponentCatalogError("default_profile is unknown")
    return ComponentCatalog(
        default_profile=default_profile,
        profiles=profiles,
        components=components,
    )


def build_component_report(profile: str | None = None) -> dict[str, object]:
    catalog = load_component_catalog()
    selected_profile = profile or catalog.default_profile
    if selected_profile not in catalog.profiles:
        raise ComponentCatalogError("unknown component profile")
    selected = set(catalog.profiles[selected_profile])
    rows = []
    for component_id, component in catalog.components.items():
        rows.append(
            {
                "id": component_id,
                "display_name": component.display_name,
                "version": component.version,
                "stability": component.stability,
                "delivery": component.delivery,
                "directory": component.directory,
                "selected": component_id in selected,
                "owner": component.owner,
                "artifact_name": component.artifact_name,
                "artifact_status": component.artifact_status,
                "artifact_sha256": component.artifact_sha256,
                "repository": component.repository,
                "revision": component.revision,
            }
        )
    return {
        "format_version": 1,
        "operation": "components",
        "status": "ok",
        "exit_code": 0,
        "default_profile": catalog.default_profile,
        "selected_profile": selected_profile,
        "profiles": {
            name: list(component_ids)
            for name, component_ids in catalog.profiles.items()
        },
        "components": rows,
    }


def format_component_report(report: dict[str, object]) -> str:
    lines = [
        "Scriptorium component catalog",
        f"Selected profile: {report['selected_profile']}",
        "",
    ]
    for row in report["components"]:
        marker = "*" if row["selected"] else " "
        ownership = f"; owned by {row['owner']}" if row["owner"] else ""
        availability = (
            f"; {row['artifact_status']}" if row["artifact_status"] else ""
        )
        lines.append(
            f"{marker} {row['id']} {row['version']} "
            f"[{row['stability']}, {row['delivery']}{ownership}{availability}]"
        )
    lines.append("")
    lines.append("* selected by this profile")
    return "\n".join(lines)
