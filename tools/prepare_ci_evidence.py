from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import unicodedata
from contextlib import closing
from pathlib import Path
from typing import Iterable


class EvidenceError(RuntimeError):
    pass


SCRIPTORIUM_VERSION = "0.2.0"
SPEC_VERSION = "2.3.0"
STEWARD_VERSION = "0.2.0"
PROVENANCE_VERSION = "0.18.0"
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt"}
DOCTOR_STATUSES = ("pass", "warn", "fail", "info", "manual")
DOCTOR_READINESS_KEYS = {
    "demo",
    "public_alpha",
    "literature",
    "slides",
    "web_history",
}
INSTALL_COMPONENTS = {
    "provenance": {"name": "provenance", "version": PROVENANCE_VERSION},
    "scriptorium-steward": {
        "name": "scriptorium-steward",
        "version": STEWARD_VERSION,
    },
    "scriptorium-suite": {
        "name": "scriptorium-suite",
        "version": SCRIPTORIUM_VERSION,
    },
}
INSTALL_LIFECYCLE = {
    "clean_install": "passed",
    "doctor_and_demo": "passed",
    "reinstall": "passed",
    "uninstall": "passed",
    "version_transition": {
        "current_version": SCRIPTORIUM_VERSION,
        "previous_version": "0.1.0",
        "relation": "previous-is-older",
        "status": "passed",
    },
}
INSTALL_STAGE_NAMES = (
    "validate-sources",
    "build-tooling",
    "build-scriptorium-suite",
    "build-scriptorium-steward",
    "build-provenance",
    "stage-scriptorium-spec",
    "build-previous-scriptorium-suite",
    "create-clean-venv",
    "clean-install",
    "verify-clean-install",
    "uninstall",
    "verify-uninstalled",
    "reinstall",
    "verify-reinstall",
    "verified-downgrade",
    "verify-downgrade",
    "verified-upgrade",
    "verify-upgrade",
    "doctor-after-reinstall",
    "demo-after-reinstall",
)
INSTALL_SAFETY = {
    "build_isolation": "disabled",
    "data": "synthetic-only",
    "network_index": "disabled",
    "runtime_dependencies_added": False,
    "temporary_workspace": True,
}
DEMO_COMPONENTS = {
    "scriptorium-spec": {
        "compatibility": "matched",
        "expected_version": SPEC_VERSION,
        "runtime_version": SPEC_VERSION,
        "source_version": SPEC_VERSION,
    },
    "steward": {
        "compatibility": "matched",
        "expected_version": STEWARD_VERSION,
        "runtime_version": STEWARD_VERSION,
        "source_version": STEWARD_VERSION,
    },
    "provenance": {
        "compatibility": "matched",
        "expected_version": PROVENANCE_VERSION,
        "runtime_version": PROVENANCE_VERSION,
        "source_version": PROVENANCE_VERSION,
    },
}
DEMO_STAGE_NAMES = (
    "validate library-kb/1.1",
    "validate literature research contracts",
    "check Steward version",
    "Steward review scaffold",
    "Steward review assemble",
    "Provenance library ingest",
    "Provenance project ingest",
    "Provenance research artifact ingest",
    "Provenance research artifact idempotency",
    "Provenance context capsule",
    "Provenance search build",
    "Provenance literature search",
    "Provenance MCP context",
)
DEMO_ASSERTION_KEYS = (
    "contract_validated",
    "research_contracts_validated",
    "steward_topic_boundary",
    "review_assembled",
    "memory_ingested",
    "research_artifacts_ingested",
    "research_ingest_idempotent",
    "context_capsule_reference_only",
    "search_hit_verified",
    "mcp_context_verified",
)
DEMO_ARTIFACTS = (
    "fixtures/library-kb.v1.1.json",
    "fixtures/parsed-paper.v1.json",
    "fixtures/reading-note.v1.json",
    "fixtures/review.v1.json",
    "fixtures/lineage-graph.v1.json",
    "workspace/Projects/synthetic-catalyst-discovery.md",
    "workspace/Reviews/ai4science-materials.md",
    "workspace/Reports/provenance-search.txt",
    "workspace/Reports/context-capsule.json",
    "workspace/Reports/provenance-mcp.jsonl",
    "provenance/memory/library.json",
    "provenance/memory/projects.json",
    "provenance/memory/research-artifacts.json",
    "provenance/search-index.db",
    "demo-report.json",
)
DEMO_SAFETY = {
    "synthetic_data": True,
    "agent_step": "recorded fixture; no live agent invoked",
    "credentials_requested": [],
    "network_policy": "this demo path implements no network action",
    "network_observation": "not measured by an OS-level sandbox",
    "config_policy": (
        "documented component homes are redirected under the output root"
    ),
}
DEMO_SUITE_READINESS = {
    "status": "not-verified",
    "reason_codes": ["agent-host-not-invoked", "lectern-not-invoked"],
}
REVIEWED_FIXTURES = {
    "library-kb.v1.1.json": {
        "runtime": "fixtures/library-kb.v1.1.json",
        "sha256": "67cc1cd70305f25cbd66e56423ec140a78b69d378ebb811af870a8d542e148ac",
    },
    "lineage-graph.v1.json": {
        "runtime": "fixtures/lineage-graph.v1.json",
        "sha256": "855437c1336d15861ffda369c36069d99ccdf21bf115e2c7ed36d5e7321d1378",
    },
    "parsed-paper.v1.json": {
        "runtime": "fixtures/parsed-paper.v1.json",
        "sha256": "d4ff6907ef270df7da512983687920b37b92aa88c838e1fa96b0adcf2c35b2f5",
    },
    "reading-note.v1.json": {
        "runtime": "fixtures/reading-note.v1.json",
        "sha256": "e87979d028f06b9f7939ca9eebc3b3c963226eba8ee5625150827c7a2213b7fe",
    },
    "review.v1.json": {
        "runtime": "fixtures/review.v1.json",
        "sha256": "072c06fb8ec613becf0bef4728c1a54c204fa6b27d7ae8eb8545cd81abf35eba",
    },
    "synthetic-project.md": {
        "runtime": "workspace/Projects/synthetic-catalyst-discovery.md",
        "sha256": "97adc89a8dd53df9e98a92740156d0078823e31affd4cddf931273ec7bc0e303",
    },
}
REVIEWED_FIXTURE_MARKERS = {
    "library-kb.v1.1.json": (
        '"schema_version": "library-kb/1.1"',
        '"key": "DEMO0001"',
        '"key": "DEMO0002"',
        '"key": "DEMO0003"',
    ),
    "lineage-graph.v1.json": (
        '"schema_version": "lineage-graph/1.0"',
        "chen2022PhysicsInformed",
        "liu2024ActiveLearning",
        "[SYNTHETIC]",
    ),
    "parsed-paper.v1.json": (
        '"schema_version": "parsed-paper/1.0"',
        '"id": "liu2024ActiveLearning"',
        "scriptorium synthetic fixture",
        "[SYNTHETIC]",
    ),
    "reading-note.v1.json": (
        '"schema_version": "reading-note/1.0"',
        '"zotero_key": "DEMO0002"',
        "liu2024ActiveLearning",
        "[SYNTHETIC]",
    ),
    "review.v1.json": (
        '"schema_version": "review/1.0"',
        "chen2022PhysicsInformed",
        "liu2024ActiveLearning",
        "[SYNTHETIC]",
    ),
    "synthetic-project.md": (
        "project_id: synthetic-catalyst-discovery",
        "linked_literature: [DEMO0001, DEMO0002]",
    ),
}
EXPECTED_SEARCH_IDS = (
    "lit:DEMO0002",
    "research:reading-note:99a28055d54f97d091186c8f",
    "research:review:17e460b9e69804efd461ed41",
)
EXPECTED_SEARCH_RECORDS = (
    ("lit:DEMO0001", "literature", "literature"),
    ("lit:DEMO0002", "literature", "literature"),
    ("lit:DEMO0003", "literature", "literature"),
    (
        "research:lineage-graph:3e9acb9d381f6cc8e5656cb2",
        "lineage-graph",
        "research-artifact",
    ),
    (
        "research:parsed-paper:99a28055d54f97d091186c8f",
        "parsed-paper",
        "research-artifact",
    ),
    (
        "research:reading-note:99a28055d54f97d091186c8f",
        "reading-note",
        "research-artifact",
    ),
    (
        "research:review:17e460b9e69804efd461ed41",
        "review",
        "research-artifact",
    ),
)
SEARCH_CONV_COLUMNS = (
    "conversation_id",
    "source",
    "date",
    "category",
    "title",
    "body",
)
SEARCH_CONV_SCHEMAS = {
    (
        "CREATE VIRTUAL TABLE conv USING fts5("
        "conversation_id, source, date, category, title, body, "
        "tokenize='trigram')"
    ): "trigram",
    (
        "CREATE VIRTUAL TABLE conv USING fts5("
        "conversation_id, source, date, category, title, body)"
    ): "unicode61",
}
EXPECTED_LITERATURE = (
    ("DEMO0001", "chen2022PhysicsInformed"),
    ("DEMO0002", "liu2024ActiveLearning"),
)
EXPECTED_RESEARCH_ARTIFACTS = (
    (
        "lineage-graph",
        "lineage-graph/1.0",
        "lineage-graph:3e9acb9d381f6cc8e5656cb2",
        "reference_only",
        ("chen2022PhysicsInformed", "liu2024ActiveLearning"),
    ),
    (
        "parsed-paper",
        "parsed-paper/1.0",
        "parsed-paper:99a28055d54f97d091186c8f",
        "reference_only",
        ("liu2024ActiveLearning",),
    ),
    (
        "reading-note",
        "reading-note/1.0",
        "reading-note:99a28055d54f97d091186c8f",
        "reference_only",
        ("liu2024ActiveLearning",),
    ),
    (
        "review",
        "review/1.0",
        "review:17e460b9e69804efd461ed41",
        "reference_only",
        ("chen2022PhysicsInformed", "liu2024ActiveLearning"),
    ),
)
EXPECTED_CONTEXT_TRUST = {
    "project_state": "human_or_approved",
    "recent_progress": "auto_applied_low_risk_not_approved_claims",
    "research_artifacts": "reference_only_not_approved_claims",
}
SENSITIVE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?iu)(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@(?:[\w-]+\.)+[\w-]{2,}",
        r"(?i)(?<![A-Z0-9])[A-Z]:[\\/]+",
        r"(?i)(?:^|[\s\"'])\\{2,4}[^\\/\s\"']+[\\/]{1,2}",
        (
            r"(?i)(?:^|[\s\"'])/(?:home|users|tmp|var/tmp|root|workspace|mnt)"
            r"(?:/[^/\s\"']+)?"
        ),
        r"\bPRIVATE_[A-Z0-9_]*\b",
        r"\bsk-[A-Za-z0-9_-]{16,}\b",
        r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
        r"\bglpat-[A-Za-z0-9_-]{20,}\b",
        r"\bhf_[A-Za-z0-9]{20,}\b",
        r"\bnpm_[A-Za-z0-9]{20,}\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bAIza[0-9A-Za-z_-]{35}\b",
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
        r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
        r"(?i)\b[A-Z][A-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@",
    )
)


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid-json-evidence") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("invalid-json-evidence")
    return payload


def _is_exact_int(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def _is_exact_bool(value: object, expected: bool) -> bool:
    return type(value) is bool and value is expected


def parse_git_commit_parents(commit_object: bytes) -> tuple[str, ...]:
    if not isinstance(commit_object, bytes) or len(commit_object) > 1024 * 1024:
        raise EvidenceError("invalid-checkout-relationship")
    header, separator, _message = commit_object.partition(b"\n\n")
    lines = header.split(b"\n")
    if (
        separator != b"\n\n"
        or not lines
        or re.fullmatch(rb"tree [0-9a-f]{40}", lines[0]) is None
    ):
        raise EvidenceError("invalid-checkout-relationship")

    parents: list[str] = []
    continuation_key: bytes | None = None
    for line in lines[1:]:
        if line.startswith(b" "):
            if continuation_key not in {
                b"gpgsig",
                b"gpgsig-sha256",
                b"mergetag",
            }:
                raise EvidenceError("invalid-checkout-relationship")
            continue
        match = re.fullmatch(rb"([a-z][a-z0-9-]*) (.+)", line)
        if match is None:
            raise EvidenceError("invalid-checkout-relationship")
        key, value = match.groups()
        continuation_key = key
        if key == b"parent":
            if re.fullmatch(rb"[0-9a-f]{40}", value) is None:
                raise EvidenceError("invalid-checkout-relationship")
            parents.append(value.decode("ascii"))
    return tuple(parents)


def inspect_git_checkout(repository: Path) -> tuple[str, tuple[str, ...]]:
    def git_output(*arguments: str) -> bytes:
        try:
            result = subprocess.run(
                ["git", "-C", os.fspath(repository), *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise EvidenceError("invalid-checkout-relationship") from exc
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise EvidenceError("invalid-checkout-relationship")
        return result.stdout

    commit_bytes = git_output("rev-parse", "HEAD").strip()
    if re.fullmatch(rb"[0-9a-f]{40}", commit_bytes) is None:
        raise EvidenceError("invalid-checkout-relationship")
    commit = commit_bytes.decode("ascii")
    parents = parse_git_commit_parents(
        git_output("cat-file", "-p", commit)
    )
    return commit, parents


def build_doctor_summary(report: dict) -> dict:
    readiness = report.get("readiness")
    summary = report.get("summary")
    checks = report.get("checks")
    if (
        not _is_exact_int(report.get("format_version"), 1)
        or report.get("generated_by")
        != {"name": "scriptorium", "version": SCRIPTORIUM_VERSION}
        or report.get("target") != "demo"
        or report.get("status") != "ready"
        or not _is_exact_int(report.get("exit_code"), 0)
        or not isinstance(readiness, dict)
        or set(readiness) != DOCTOR_READINESS_KEYS
        or readiness.get("demo") != "ready"
        or readiness.get("public_alpha") != "incomplete"
        or readiness.get("literature") not in {"detected", "file-only"}
        or readiness.get("slides") not in {"detected", "unavailable"}
        or readiness.get("web_history") not in {"manual", "unavailable"}
        or not isinstance(summary, dict)
        or set(summary) != set(DOCTOR_STATUSES)
        or not all(type(summary.get(name)) is int for name in DOCTOR_STATUSES)
        or not isinstance(checks, list)
        or not checks
    ):
        raise EvidenceError("invalid-doctor-evidence")

    counted = {name: 0 for name in DOCTOR_STATUSES}
    seen_ids: set[str] = set()
    for item in checks:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in seen_ids
            or item.get("status") not in counted
        ):
            raise EvidenceError("invalid-doctor-evidence")
        seen_ids.add(item["id"])
        counted[item["status"]] += 1
    if summary != counted or summary["fail"] != 0 or summary["warn"] != 0:
        raise EvidenceError("invalid-doctor-evidence")

    return {
        "format_version": 1,
        "evidence_type": "scriptorium-doctor-summary",
        "generated_by": {"name": "scriptorium", "version": SCRIPTORIUM_VERSION},
        "target": "demo",
        "status": "ready",
        "exit_code": 0,
        "readiness": dict(readiness),
        "summary": dict(summary),
        "check_count": len(checks),
        "privacy": {
            "source_report_shared": False,
            "filesystem_paths_included": False,
        },
        "limitations": [
            "Detailed checks, egress details, and filesystem paths are intentionally omitted."
        ],
    }


def build_install_lifecycle_summary(report: dict) -> dict:
    stages = report.get("stages")
    expected_stages = [
        {"name": name, "status": "passed"} for name in INSTALL_STAGE_NAMES
    ]
    platform = report.get("platform")
    if (
        not _is_exact_int(report.get("format_version"), 1)
        or report.get("operation") != "windows-install-lifecycle"
        or report.get("status") != "passed"
        or not _is_exact_int(report.get("exit_code"), 0)
        or report.get("components") != INSTALL_COMPONENTS
        or report.get("lifecycle") != INSTALL_LIFECYCLE
        or stages != expected_stages
        or report.get("safety") != INSTALL_SAFETY
        or not _is_exact_bool(
            report.get("safety", {}).get("runtime_dependencies_added"),
            False,
        )
        or not _is_exact_bool(
            report.get("safety", {}).get("temporary_workspace"),
            True,
        )
        or not isinstance(platform, dict)
        or platform.get("implementation") != "cpython"
        or platform.get("windows") is not True
        or not isinstance(platform.get("python"), str)
        or not re.fullmatch(r"3\.12\.\d+", platform["python"])
    ):
        raise EvidenceError("invalid-install-lifecycle-evidence")

    return {
        "format_version": 1,
        "evidence_type": "windows-install-lifecycle-summary",
        "operation": "windows-install-lifecycle",
        "status": "passed",
        "exit_code": 0,
        "components": {
            name: value["version"] for name, value in INSTALL_COMPONENTS.items()
        },
        "lifecycle": INSTALL_LIFECYCLE,
        "stages": {
            "expected_count": len(INSTALL_STAGE_NAMES),
            "passed_count": len(stages),
            "expected_sequence_verified": True,
        },
        "platform": {
            "implementation": "cpython",
            "python_minor": "3.12",
            "windows": True,
        },
        "safety": INSTALL_SAFETY,
        "privacy": {
            "source_report_shared": False,
            "unknown_source_fields_included": False,
        },
    }


def build_run_attestation(
    *,
    repository: str,
    subject_commit: str,
    checkout_commit: str,
    base_commit: str | None,
    checkout_parents: tuple[str, ...],
    checkout_mode: str,
    run_id: int,
    run_attempt: int,
    event_name: str,
    runner_os: str,
    artifact_name: str,
    test_context: str,
) -> dict:
    for value in (subject_commit, checkout_commit):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise EvidenceError("invalid-run-attestation")
    if base_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", base_commit):
        raise EvidenceError("invalid-run-attestation")
    if not isinstance(checkout_parents, tuple) or any(
        not isinstance(parent, str)
        or re.fullmatch(r"[0-9a-f]{40}", parent) is None
        for parent in checkout_parents
    ):
        raise EvidenceError("invalid-run-attestation")
    expected_modes = {
        "pull_request": "pull-request-merge",
        "push": "push",
        "workflow_dispatch": "workflow-dispatch",
    }
    if expected_modes.get(event_name) != checkout_mode:
        raise EvidenceError("invalid-run-attestation")
    if event_name == "pull_request":
        # GitHub's PR merge ref is a Git merge commit: base first, PR head second.
        if (
            base_commit is None
            or checkout_parents != (base_commit, subject_commit)
        ):
            raise EvidenceError("invalid-run-attestation")
        relationship = "pull-request-merge-base-first-head-second"
    else:
        if checkout_commit != subject_commit:
            raise EvidenceError("invalid-run-attestation")
        relationship = "subject-commit-exact"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise EvidenceError("invalid-run-attestation")
    if (
        type(run_id) is not int
        or type(run_attempt) is not int
        or run_id < 1
        or run_attempt < 1
    ):
        raise EvidenceError("invalid-run-attestation")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_name):
        raise EvidenceError("invalid-run-attestation")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", test_context):
        raise EvidenceError("invalid-run-attestation")

    return {
        "format_version": 1,
        "evidence_type": "github-actions-run-attestation",
        "repository": repository,
        "subject_commit": subject_commit,
        "checkout": {
            "commit": checkout_commit,
            "mode": checkout_mode,
            "relationship": relationship,
            "relationship_verified": True,
        },
        "base": {
            "commit": base_commit,
            "available": base_commit is not None,
        },
        "test_context": {
            "name": test_context,
            "event_name": event_name,
            "runner_os": runner_os,
        },
        "workflow_run": run_id,
        "workflow_run_attempt": run_attempt,
        "workflow_run_url": f"https://github.com/{repository}/actions/runs/{run_id}",
        "artifact_name": artifact_name,
        "scope": "CI-generated synthetic integration evidence",
        "limitations": [
            "GitHub API metadata is authoritative for the final run conclusion and artifact digest.",
            "This attestation does not claim external-user or scientific-result validation.",
        ],
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _contains_sensitive_text(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return any(pattern.search(normalized) for pattern in SENSITIVE_PATTERNS)


def _json_contains_sensitive_value(value: object) -> bool:
    if isinstance(value, str):
        return _contains_sensitive_text(value)
    if isinstance(value, list):
        return any(_json_contains_sensitive_value(item) for item in value)
    if isinstance(value, dict):
        return any(
            _json_contains_sensitive_value(key)
            or _json_contains_sensitive_value(item)
            for key, item in value.items()
        )
    return False


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(
            path.stat(follow_symlinks=False),
            "st_file_attributes",
            0,
        )
    except OSError as exc:
        raise EvidenceError("unsafe-path-boundary") from exc
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_path_chain_safe(path: Path) -> None:
    absolute = _absolute_without_resolving(path)
    if _has_hidden_component(absolute.parts):
        raise EvidenceError("hidden-evidence-path")
    for candidate in (absolute, *absolute.parents):
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise EvidenceError("unsafe-path-boundary") from exc
        if _is_linklike(candidate):
            raise EvidenceError("unsafe-path-boundary")


def _has_hidden_component(parts: Iterable[str]) -> bool:
    return any(part.startswith(".") and part not in {".", ".."} for part in parts)


def _assert_single_link_regular_file(path: Path) -> os.stat_result:
    _assert_path_chain_safe(path)
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise EvidenceError("unsafe-path-boundary") from exc
    if (
        _is_linklike(path)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise EvidenceError("unsafe-path-boundary")
    return metadata


def _walk_regular_files(root: Path) -> list[Path]:
    files: list[Path] = []

    def reject_walk_error(_: OSError) -> None:
        raise EvidenceError("unsafe-path-boundary")

    for current_raw, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=reject_walk_error,
        followlinks=False,
    ):
        current = Path(current_raw)
        if _is_linklike(current):
            raise EvidenceError("unsafe-path-boundary")
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            candidate = current / name
            if name.startswith("."):
                raise EvidenceError("hidden-evidence-path")
            if _is_linklike(candidate):
                raise EvidenceError("unsafe-path-boundary")
        for name in file_names:
            candidate = current / name
            if name.startswith("."):
                raise EvidenceError("hidden-evidence-path")
            _assert_single_link_regular_file(candidate)
            files.append(candidate)
    return files


def _iter_files(paths: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        _assert_path_chain_safe(path)
        if not path.exists():
            raise EvidenceError("missing-evidence-input")
        if _is_linklike(path):
            raise EvidenceError("unsafe-path-boundary")
        if path.name.startswith("."):
            raise EvidenceError("hidden-evidence-path")
        if path.is_file():
            _assert_single_link_regular_file(path)
            files.add(path)
            continue
        if not path.is_dir():
            raise EvidenceError("unsafe-path-boundary")
        files.update(_walk_regular_files(path))
    if not files:
        raise EvidenceError("empty-evidence-set")
    return sorted(files)


def _load_json_lines(text: str) -> list[object]:
    values: list[object] = []
    try:
        for line in text.splitlines():
            if line.strip():
                values.append(json.loads(line))
    except json.JSONDecodeError as exc:
        raise EvidenceError("invalid-json-evidence") from exc
    if not values:
        raise EvidenceError("invalid-json-evidence")
    return values


def scan_public_evidence(paths: Iterable[Path]) -> int:
    selected = list(paths)
    files = _iter_files(selected)
    for path in files:
        relative_labels: list[str] = []
        for root in selected:
            if root.is_file() and path == root:
                relative_labels.append(path.name)
                continue
            if root.is_dir():
                try:
                    relative_labels.append(path.relative_to(root).as_posix())
                except ValueError:
                    pass
        if not relative_labels:
            relative_labels.append(path.name)
        if any(_contains_sensitive_text(value) for value in relative_labels):
            raise EvidenceError("sensitive-content-detected")
        suffix = path.suffix.lower()
        if suffix not in TEXT_SUFFIXES:
            raise EvidenceError("unsupported-evidence-format")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise EvidenceError("invalid-text-encoding") from exc
        if _contains_sensitive_text(text):
            raise EvidenceError("sensitive-content-detected")
        if suffix == ".json":
            try:
                decoded: object = json.loads(text)
            except json.JSONDecodeError as exc:
                raise EvidenceError("invalid-json-evidence") from exc
            if _json_contains_sensitive_value(decoded):
                raise EvidenceError("sensitive-content-detected")
        elif suffix == ".jsonl":
            if any(
                _json_contains_sensitive_value(item)
                for item in _load_json_lines(text)
            ):
                raise EvidenceError("sensitive-content-detected")
    return len(files)


def build_demo_summary(report: dict) -> dict:
    expected_assertions = {name: True for name in DEMO_ASSERTION_KEYS}
    stages = report.get("stages")
    assertions = report.get("assertions")
    safety = report.get("safety")
    stages_match = (
        isinstance(stages, list)
        and len(stages) == len(DEMO_STAGE_NAMES)
        and all(
            isinstance(item, dict)
            and set(item) == {"name", "exit_code"}
            and item.get("name") == name
            and _is_exact_int(item.get("exit_code"), 0)
            for item, name in zip(stages, DEMO_STAGE_NAMES)
        )
    )
    assertions_match = (
        isinstance(assertions, dict)
        and set(assertions) == set(DEMO_ASSERTION_KEYS)
        and all(assertions.get(name) is True for name in DEMO_ASSERTION_KEYS)
    )
    if (
        not _is_exact_int(report.get("format_version"), 1)
        or report.get("generated_by")
        != {"name": "scriptorium", "version": SCRIPTORIUM_VERSION}
        or report.get("demo_status") != "passed"
        or report.get("components") != DEMO_COMPONENTS
        or not stages_match
        or not assertions_match
        or report.get("artifacts") != list(DEMO_ARTIFACTS)
        or report.get("expected_artifacts") != list(DEMO_ARTIFACTS)
        or safety != DEMO_SAFETY
        or not _is_exact_bool(safety.get("synthetic_data"), True)
        or report.get("suite_readiness") != DEMO_SUITE_READINESS
    ):
        raise EvidenceError("invalid-demo-evidence")
    return {
        "format_version": 1,
        "evidence_type": "scriptorium-demo-summary",
        "generated_by": {"name": "scriptorium", "version": SCRIPTORIUM_VERSION},
        "status": "passed",
        "components": {
            name: values["runtime_version"]
            for name, values in DEMO_COMPONENTS.items()
        },
        "stages": {
            "expected_count": len(DEMO_STAGE_NAMES),
            "zero_exit_count": len(DEMO_STAGE_NAMES),
            "expected_sequence_verified": True,
        },
        "assertions": expected_assertions,
        "artifacts": {
            "expected_count": len(DEMO_ARTIFACTS),
            "exact_manifest_verified": True,
        },
        "safety": {
            "synthetic_data": True,
            "credentials_requested": [],
            "live_agent_invoked": False,
        },
        "suite_readiness": DEMO_SUITE_READINESS,
        "privacy": {
            "source_report_shared": False,
            "runtime_derived_payloads_shared": False,
        },
    }


def _sha256(path: Path) -> str:
    _assert_single_link_regular_file(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvidenceError("unsafe-path-boundary") from exc
    return digest.hexdigest()


def _validate_fixture_markers(root: Path) -> None:
    for name, markers in REVIEWED_FIXTURE_MARKERS.items():
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise EvidenceError("invalid-reviewed-fixture") from exc
        if any(marker not in text for marker in markers):
            raise EvidenceError("invalid-reviewed-fixture")


def copy_reviewed_fixtures(
    *,
    fixture_root: Path,
    demo_root: Path,
    destination: Path,
) -> int:
    for root in (fixture_root, demo_root, destination):
        _assert_path_chain_safe(root)
    if not fixture_root.is_dir() or not demo_root.is_dir():
        raise EvidenceError("invalid-reviewed-fixture")

    for name, binding in REVIEWED_FIXTURES.items():
        reviewed = fixture_root / name
        runtime = demo_root / binding["runtime"]
        if (
            _sha256(reviewed) != binding["sha256"]
            or _sha256(runtime) != binding["sha256"]
        ):
            raise EvidenceError("invalid-reviewed-fixture")
    _validate_fixture_markers(fixture_root)

    destination.mkdir(parents=True, exist_ok=True)
    for name in REVIEWED_FIXTURES:
        shutil.copyfile(fixture_root / name, destination / name)
        if _sha256(destination / name) != REVIEWED_FIXTURES[name]["sha256"]:
            raise EvidenceError("invalid-reviewed-fixture")
    return len(REVIEWED_FIXTURES)


def _record_signatures(
    value: object,
    fields: tuple[str, ...],
    *,
    tuple_fields: frozenset[str] = frozenset(),
) -> tuple[tuple[object, ...], ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise EvidenceError("invalid-demo-evidence")
    rows: list[tuple[object, ...]] = []
    for item in value:
        row: list[object] = []
        for field in fields:
            field_value = item.get(field)
            if field in tuple_fields:
                if not isinstance(field_value, list):
                    raise EvidenceError("invalid-demo-evidence")
                field_value = tuple(field_value)
            row.append(field_value)
        rows.append(tuple(row))
    return tuple(rows)


def build_context_summary(context: dict) -> dict:
    project = context.get("project")
    reference_leads = context.get("reference_leads")
    next_actions = context.get("next_actions")
    gaps = (
        reference_leads.get("gaps")
        if isinstance(reference_leads, dict)
        else None
    )
    expected_top = {
        "capsule_version",
        "project",
        "next_actions",
        "blocked_by",
        "recent_progress",
        "literature",
        "research_artifacts",
        "reference_leads",
        "trust",
        "limits",
    }
    literature = _record_signatures(
        context.get("literature"),
        ("id", "citekey"),
    )
    research = _record_signatures(
        context.get("research_artifacts"),
        ("kind", "schema_version", "id", "trust", "source_ids"),
        tuple_fields=frozenset({"source_ids"}),
    )
    if (
        set(context) != expected_top
        or context.get("capsule_version") != "context-capsule/0.1"
        or not isinstance(project, dict)
        or tuple(
            project.get(key) for key in ("project_id", "title", "status", "stage")
        )
        != (
            "synthetic-catalyst-discovery",
            "Synthetic Catalyst Discovery",
            "active",
            "literature review",
        )
        or not isinstance(next_actions, list)
        or len(next_actions) != 2
        or not all(isinstance(item, str) for item in next_actions)
        or context.get("recent_progress") != []
        or context.get("blocked_by") != ""
        or literature != EXPECTED_LITERATURE
        or research != EXPECTED_RESEARCH_ARTIFACTS
        or context.get("trust") != EXPECTED_CONTEXT_TRUST
        or context.get("limits") != {"max_markdown_chars": 8000, "truncated": False}
        or not _is_exact_int(
            context.get("limits", {}).get("max_markdown_chars"),
            8000,
        )
        or not _is_exact_bool(
            context.get("limits", {}).get("truncated"),
            False,
        )
        or not isinstance(reference_leads, dict)
        or reference_leads.get("priority_reads") != ["liu2024ActiveLearning"]
        or not isinstance(gaps, list)
        or len(gaps) != 1
        or not isinstance(gaps[0], str)
        or not gaps[0].startswith("[SYNTHETIC]")
    ):
        raise EvidenceError("invalid-demo-evidence")

    return {
        "format_version": 1,
        "evidence_type": "synthetic-context-summary",
        "capsule_version": "context-capsule/0.1",
        "project_id": "synthetic-catalyst-discovery",
        "project_status": "active",
        "project_stage": "literature review",
        "next_action_count": 2,
        "recent_progress_count": 0,
        "literature_ids": ["DEMO0001", "DEMO0002"],
        "research_artifact_kinds": [
            "lineage-graph",
            "parsed-paper",
            "reading-note",
            "review",
        ],
        "research_artifact_trust": "reference_only",
        "project_state_trust": "human_or_approved",
        "source_report_shared": False,
    }


def build_search_index_summary(
    *,
    database: Path,
    search_report: Path,
    demo_summary: dict,
    context_summary: dict,
) -> dict:
    database_stat = _assert_single_link_regular_file(database)
    _assert_single_link_regular_file(search_report)
    if database_stat.st_size <= 16:
        raise EvidenceError("invalid-demo-evidence")
    try:
        search_text = search_report.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EvidenceError("invalid-demo-evidence") from exc
    found_ids = tuple(re.findall(r"\bid=([A-Za-z0-9:._-]+)", search_text))
    if (
        not search_text.startswith("3 hits for: calibration")
        or found_ids != EXPECTED_SEARCH_IDS
        or demo_summary.get("assertions", {}).get("search_hit_verified") is not True
        or "DEMO0002" not in context_summary.get("literature_ids", [])
    ):
        raise EvidenceError("invalid-demo-evidence")

    # Security boundary: SQLite is opened read-only and never copied to staging.
    uri = f"{_absolute_without_resolving(database).as_uri()}?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            quick = connection.execute("PRAGMA quick_check").fetchone()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            schema_row = connection.execute(
                "SELECT type, sql FROM sqlite_master WHERE name = 'conv'"
            ).fetchone()
            conv_columns = tuple(
                row[1] for row in connection.execute("PRAGMA table_info(conv)")
            )
            idxmeta_columns = tuple(
                row[1] for row in connection.execute("PRAGMA table_info(idxmeta)")
            )
            indexed_records = tuple(
                connection.execute(
                    "SELECT conversation_id, source, category "
                    "FROM conv ORDER BY conversation_id"
                )
            )
            tokenizer_rows = tuple(
                connection.execute(
                    "SELECT k, v FROM idxmeta ORDER BY k"
                )
            )
            query_hits = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT conversation_id FROM conv "
                    "WHERE conv MATCH ?",
                    ("calibration",),
                )
            )
    except sqlite3.Error as exc:
        raise EvidenceError("invalid-demo-evidence") from exc
    normalized_schema = (
        re.sub(r"\s+", " ", schema_row[1].strip())
        if (
            isinstance(schema_row, tuple)
            and len(schema_row) == 2
            and schema_row[0] == "table"
            and isinstance(schema_row[1], str)
        )
        else None
    )
    expected_tokenizer = SEARCH_CONV_SCHEMAS.get(normalized_schema)
    if (
        quick != ("ok",)
        or integrity != ("ok",)
        or expected_tokenizer is None
        or conv_columns != SEARCH_CONV_COLUMNS
        or idxmeta_columns != ("k", "v")
        or indexed_records != EXPECTED_SEARCH_RECORDS
        or tokenizer_rows != (("tokenizer", expected_tokenizer),)
        or len(query_hits) != len(EXPECTED_SEARCH_IDS)
        or frozenset(query_hits) != frozenset(EXPECTED_SEARCH_IDS)
    ):
        raise EvidenceError("invalid-demo-evidence")

    return {
        "format_version": 1,
        "evidence_type": "synthetic-search-index-summary",
        "synthetic_data": True,
        "query": "calibration",
        "hit_count": 3,
        "expected_result_ids": list(EXPECTED_SEARCH_IDS),
        "indexed_record_count": len(EXPECTED_SEARCH_RECORDS),
        "expected_index_ids": [
            record[0] for record in EXPECTED_SEARCH_RECORDS
        ],
        "conv_schema_verified": True,
        "tokenizer": expected_tokenizer,
        "sqlite_quick_check": "ok",
        "sqlite_integrity_check": "ok",
        "source_artifact_shared": False,
        "source_search_report_shared": False,
        "omission_reason": (
            "Raw SQLite and search text are intentionally omitted because deleted "
            "pages, metadata, and derived text are not reviewed public fixtures."
        ),
    }


def build_payload_file_manifest(root: Path) -> list[dict]:
    entries = []
    for path in _iter_files([root]):
        relative = path.relative_to(root).as_posix()
        if relative == "run-attestation.json":
            continue
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        )
    return entries


def verify_payload_file_manifest(root: Path, expected: list[dict]) -> None:
    actual = build_payload_file_manifest(root)
    if actual != expected:
        raise EvidenceError("payload-manifest-mismatch")


def prepare_evidence(
    *,
    doctor_report: Path,
    demo_root: Path,
    install_report: Path,
    fixture_root: Path,
    output_dir: Path,
    attestation: dict,
) -> int:
    for path in (
        doctor_report,
        demo_root,
        install_report,
        fixture_root,
        output_dir,
    ):
        _assert_path_chain_safe(path)
    if output_dir.exists() and (
        not output_dir.is_dir() or any(output_dir.iterdir())
    ):
        raise EvidenceError("invalid-evidence-output")
    output_dir.mkdir(parents=True, exist_ok=True)
    _assert_path_chain_safe(output_dir)

    for source in (doctor_report, install_report):
        _assert_single_link_regular_file(source)
    for relative in DEMO_ARTIFACTS:
        _assert_single_link_regular_file(demo_root / relative)

    demo_summary = build_demo_summary(load_json(demo_root / "demo-report.json"))
    context_path = demo_root / "workspace" / "Reports" / "context-capsule.json"
    search_path = demo_root / "workspace" / "Reports" / "provenance-search.txt"
    context_summary = build_context_summary(load_json(context_path))
    scan_public_evidence([context_path, search_path])

    write_json(
        output_dir / "doctor-summary.json",
        build_doctor_summary(load_json(doctor_report)),
    )
    write_json(
        output_dir / "install-lifecycle-summary.json",
        build_install_lifecycle_summary(load_json(install_report)),
    )
    write_json(
        output_dir / "demo-summary.json",
        demo_summary,
    )
    write_json(
        output_dir / "context-summary.json",
        context_summary,
    )
    write_json(
        output_dir / "search-index-summary.json",
        build_search_index_summary(
            database=demo_root / "provenance" / "search-index.db",
            search_report=search_path,
            demo_summary=demo_summary,
            context_summary=context_summary,
        ),
    )
    copy_reviewed_fixtures(
        fixture_root=fixture_root,
        demo_root=demo_root,
        destination=output_dir / "fixtures",
    )
    payload_files = build_payload_file_manifest(output_dir)
    stored_attestation = dict(attestation)
    stored_attestation["payload_file_count"] = len(payload_files)
    stored_attestation["payload_files"] = payload_files
    write_json(output_dir / "run-attestation.json", stored_attestation)
    verify_payload_file_manifest(output_dir, payload_files)
    return scan_public_evidence([output_dir])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and scan shareable Scriptorium CI evidence."
    )
    parser.add_argument("--doctor-report", type=Path, required=True)
    parser.add_argument("--demo-root", type=Path, required=True)
    parser.add_argument("--install-report", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--subject-commit", required=True)
    parser.add_argument("--checkout-commit", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--checkout-repository", type=Path, required=True)
    parser.add_argument("--checkout-mode", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--runner-os", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--test-context", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        inspected_commit, checkout_parents = inspect_git_checkout(
            args.checkout_repository
        )
        if inspected_commit != args.checkout_commit:
            raise EvidenceError("invalid-checkout-relationship")
        attestation = build_run_attestation(
            repository=args.repository,
            subject_commit=args.subject_commit,
            checkout_commit=args.checkout_commit,
            base_commit=(
                None if args.base_commit == "none" else args.base_commit
            ),
            checkout_parents=checkout_parents,
            checkout_mode=args.checkout_mode,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            event_name=args.event_name,
            runner_os=args.runner_os,
            artifact_name=args.artifact_name,
            test_context=args.test_context,
        )
        count = prepare_evidence(
            doctor_report=args.doctor_report,
            demo_root=args.demo_root,
            install_report=args.install_report,
            fixture_root=args.fixture_root,
            output_dir=args.output_dir,
            attestation=attestation,
        )
    except EvidenceError as exc:
        print(f"evidence-error: {exc}")
        return 1
    except OSError:
        print("evidence-error: io-failure")
        return 1
    print(f"public-evidence-ready files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
