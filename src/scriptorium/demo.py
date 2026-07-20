"""Offline synthetic golden path for the Scriptorium suite."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__


MARKER_NAME = ".scriptorium-demo"
MARKER_VALUE = "scriptorium-demo/1\n"
ASSET_PACKAGE = "scriptorium.assets"
STAGE_TIMEOUT_SECONDS = 30
COMPONENT_DIRS = {
    "scriptorium-spec": "scriptorium-spec",
    "steward": "steward",
    "provenance": "Provenance",
}
COMPONENT_ENVS = {
    "scriptorium-spec": "SCRIPTORIUM_SPEC_ROOT",
    "steward": "SCRIPTORIUM_STEWARD_ROOT",
    "provenance": "SCRIPTORIUM_PROVENANCE_ROOT",
}
PASSTHROUGH_ENV_NAMES = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}


class DemoError(RuntimeError):
    """A user-actionable demo failure."""


def load_compatibility() -> dict[str, str]:
    payload = resources.files(ASSET_PACKAGE).joinpath("compatibility.toml").read_bytes()
    data = tomllib.loads(payload.decode("utf-8"))
    return {str(key): str(value) for key, value in data["components"].items()}


def _is_linklike(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _safe_destination(root: Path, path: Path) -> None:
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as exc:
        raise DemoError(f"managed demo path escapes the output root: {path}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if _is_linklike(current):
            raise DemoError(f"managed demo path contains a symlink or junction: {current}")
        resolved = current.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise DemoError(f"managed demo path resolves outside the output root: {current}")


def prepare_output(path: Path) -> Path:
    requested = path.expanduser().absolute()
    try:
        if _is_linklike(requested):
            raise DemoError(f"output directory cannot be a symlink or junction: {requested}")
        root = requested.resolve()
        marker = root / MARKER_NAME
        if _is_linklike(marker):
            raise DemoError(f"output marker cannot be a symlink or junction: {marker}")
        if root.exists():
            entries = list(root.iterdir())
            if entries and not marker.exists():
                raise DemoError(
                    f"output directory is not an owned demo directory: {root}; "
                    "choose an empty or new --output path"
                )
            if marker.exists():
                if not marker.is_file():
                    raise DemoError(f"output marker is not a regular file: {marker}")
                if marker.read_text(encoding="utf-8") != MARKER_VALUE:
                    raise DemoError(f"output marker is not recognized: {marker}")
        root.mkdir(parents=True, exist_ok=True)
        _write_managed_bytes(root, marker, MARKER_VALUE.encode("utf-8"))
        return root
    except DemoError:
        raise
    except OSError as exc:
        raise DemoError(f"cannot prepare output directory {requested}: {exc}") from exc


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_component_root(component: str, explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    else:
        configured = os.environ.get(COMPONENT_ENVS[component])
        if configured and configured.strip():
            candidates.append(Path(configured))
        else:
            source_parent = _source_root().parent
            candidates.extend(
                [
                    source_parent / COMPONENT_DIRS[component],
                    Path.cwd() / COMPONENT_DIRS[component],
                    Path.cwd().parent / COMPONENT_DIRS[component],
                ]
            )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "pyproject.toml").is_file():
            return resolved
    flag = component.replace("scriptorium-", "").replace("provenance", "provenance")
    raise DemoError(
        f"cannot find {component}; pass --{flag}-root or set {COMPONENT_ENVS[component]}"
    )


def project_version(root: Path) -> str:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise DemoError(f"cannot read component version from {root / 'pyproject.toml'}: {exc}") from exc


def find_script(root: Path, name: str) -> Path:
    active_scripts = Path(sys.executable).resolve().parent
    candidates = [
        root / ".venv" / "Scripts" / f"{name}.exe",
        root / ".venv" / "Scripts" / name,
        root / ".venv" / "bin" / name,
        active_scripts / f"{name}.exe",
        active_scripts / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    installed = shutil.which(name)
    if installed:
        return Path(installed).resolve()
    raise DemoError(
        f"public command '{name}' is unavailable for {root.name}; "
        "install that source checkout in a virtual environment first"
    )


def _write_asset(name: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(resources.files(ASSET_PACKAGE).joinpath(name).read_bytes())


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"cannot read expected JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DemoError(f"expected a JSON object: {path}")
    return value


def _write_managed_bytes(root: Path, path: Path, payload: bytes) -> None:
    _safe_destination(root, path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _safe_destination(root, path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(root: Path, path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write_managed_bytes(root, path, payload)


def _write_failed_report(root: Path, path: Path, report: dict[str, Any]) -> None:
    try:
        _write_json(root, path, report)
    except (DemoError, OSError):
        pass


def isolated_environment(*, provenance_home: Path, workspace: Path, config_dir: Path) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in PASSTHROUGH_ENV_NAMES
    }
    isolated_home = config_dir / "home"
    isolated_temp = config_dir / "tmp"
    isolated_appdata = isolated_home / "AppData" / "Roaming"
    isolated_local_appdata = isolated_home / "AppData" / "Local"
    env.update(
        {
            "APPDATA": str(isolated_appdata),
            "HOME": str(isolated_home),
            "LOCALAPPDATA": str(isolated_local_appdata),
            "USERPROFILE": str(isolated_home),
            "XDG_CONFIG_HOME": str(config_dir),
            "TEMP": str(isolated_temp),
            "TMP": str(isolated_temp),
            "TMPDIR": str(isolated_temp),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PROVENANCE_HOME": str(provenance_home),
            "PROVENANCE_VAULT": str(workspace),
            "SCRIPTORIUM_CONFIG_DIR": str(config_dir),
        }
    )
    return env


def _run_stage(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stages: list[dict[str, Any]],
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=STAGE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DemoError(f"{name} could not run: {exc}") from exc
    stages.append({"name": name, "exit_code": completed.returncode})
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
        raise DemoError(f"{name} failed with exit {completed.returncode}: {detail}")
    print(f"[ok] {name}")
    return completed


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise DemoError(message)


def _clear_managed_files(root: Path, paths: list[Path]) -> None:
    for path in paths:
        _safe_destination(root, path)
        if path.exists():
            if not path.is_file():
                raise DemoError(f"managed demo file path is not a file: {path}")
            path.unlink()


def _mcp_text(response: dict[str, Any], request_id: int) -> str:
    _assert(response.get("jsonrpc") == "2.0", f"MCP response {request_id} has no JSON-RPC version")
    _assert("error" not in response, f"MCP response {request_id} returned an error")
    result = response.get("result")
    _assert(isinstance(result, dict), f"MCP response {request_id} has no result object")
    _assert(not result.get("isError"), f"MCP tool {request_id} reported an in-band error")
    content = result.get("content")
    _assert(isinstance(content, list) and content, f"MCP response {request_id} has no content")
    texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    _assert(bool(texts), f"MCP response {request_id} has no text content")
    return "\n".join(texts)


def _present_artifacts(root: Path, expected: list[str]) -> list[str]:
    present: list[str] = []
    for relative in expected:
        path = root / Path(relative)
        try:
            _safe_destination(root, path)
        except DemoError:
            continue
        if relative == "demo-report.json" or path.is_file():
            present.append(relative)
    return present


def run_demo(
    output: Path,
    *,
    spec_root: Path | None = None,
    steward_root: Path | None = None,
    provenance_root: Path | None = None,
) -> Path:
    root = prepare_output(output)
    report_path = root / "demo-report.json"
    report: dict[str, Any] = {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "demo_status": "running",
        "suite_readiness": {
            "status": "not-verified",
            "reason_codes": ["agent-host-not-invoked", "lectern-not-invoked"],
        },
        "safety": {
            "synthetic_data": True,
            "agent_step": "recorded fixture; no live agent invoked",
            "credentials_requested": [],
            "network_policy": "this demo path implements no network action",
            "network_observation": "not measured by an OS-level sandbox",
            "config_policy": "documented component homes are redirected under the output root",
        },
        "components": {},
        "stages": [],
        "assertions": {},
        "artifacts": [],
        "expected_artifacts": [
            "fixtures/library-kb.v1.1.json",
            "workspace/Projects/synthetic-catalyst-discovery.md",
            "workspace/Reviews/ai4science-materials.md",
            "workspace/Reports/provenance-search.txt",
            "workspace/Reports/provenance-mcp.jsonl",
            "provenance/memory/library.json",
            "provenance/memory/projects.json",
            "provenance/search-index.db",
            "demo-report.json",
        ],
        "limitations": [
            "A successful demo does not assert full Public Alpha readiness.",
            "Codex and Claude Code hosts are not invoked by the credential-free demo.",
            "Lectern is not invoked because slide generation requires a separately configured provider path.",
            "Network and filesystem isolation are policy-constrained here, not observed by an OS-level sandbox.",
        ],
    }

    try:
        fixtures = root / "fixtures"
        workspace = root / "workspace"
        provenance_home = root / "provenance"
        input_path = root / "work" / "review.input.json"
        draft_path = fixtures / "review-draft.v1.json"
        review_path = workspace / "Reviews" / "ai4science-materials.md"
        kb_path = fixtures / "library-kb.v1.1.json"
        project_path = workspace / "Projects" / "synthetic-catalyst-discovery.md"
        search_path = workspace / "Reports" / "provenance-search.txt"
        mcp_path = workspace / "Reports" / "provenance-mcp.jsonl"
        workspace_readme = workspace / "README.md"
        prompt_path = input_path.parent / "REVIEW-PROMPT.md"
        library_memory_path = provenance_home / "memory" / "library.json"
        project_memory_path = provenance_home / "memory" / "projects.json"
        search_index_path = provenance_home / "search-index.db"
        config_dir = root / "config"

        _clear_managed_files(
            root,
            [
                report_path,
                kb_path,
                draft_path,
                project_path,
                workspace_readme,
                input_path,
                prompt_path,
                review_path,
                search_path,
                mcp_path,
                library_memory_path,
                project_memory_path,
                search_index_path,
            ],
        )
        for directory in [
            input_path.parent,
            review_path.parent,
            search_path.parent,
            provenance_home / "inbox",
            config_dir / "home",
            config_dir / "home" / "AppData" / "Local",
            config_dir / "home" / "AppData" / "Roaming",
            config_dir / "tmp",
        ]:
            _safe_destination(root, directory)

        roots = {
            "scriptorium-spec": resolve_component_root("scriptorium-spec", spec_root),
            "steward": resolve_component_root("steward", steward_root),
            "provenance": resolve_component_root("provenance", provenance_root),
        }
        expected = load_compatibility()
        actual = {name: project_version(path) for name, path in roots.items()}
        for name, version in expected.items():
            _assert(
                actual.get(name) == version,
                f"incompatible {name}: expected {version}, found {actual.get(name, 'missing')}",
            )
        report["components"] = {
            name: {
                "expected_version": expected[name],
                "source_version": actual[name],
                "runtime_version": None,
                "compatibility": "matched",
            }
            for name in expected
        }
        report["components"]["scriptorium-spec"]["runtime_version"] = actual["scriptorium-spec"]

        steward = find_script(roots["steward"], "steward")
        prov_ingest_library = find_script(roots["provenance"], "prov-ingest-library")
        prov_ingest_vault = find_script(roots["provenance"], "prov-ingest-vault")
        prov_search = find_script(roots["provenance"], "prov-search")
        prov_mcp = find_script(roots["provenance"], "prov-mcp")
        provenance_scripts = [prov_ingest_library, prov_ingest_vault, prov_search, prov_mcp]
        _assert(
            len({path.parent.resolve() for path in provenance_scripts}) == 1,
            "Provenance commands resolve to different script environments; install one compatible source checkout",
        )
        validator = roots["scriptorium-spec"] / "tools" / "validate.py"
        _assert(validator.is_file(), f"spec validator not found: {validator}")

        _write_asset("library-kb.v1.1.json", kb_path)
        _write_asset("review-draft.v1.json", draft_path)
        _write_asset("synthetic-project.md", project_path)
        _write_asset("workspace-readme.md", workspace_readme)
        input_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        search_path.parent.mkdir(parents=True, exist_ok=True)
        (provenance_home / "inbox").mkdir(parents=True, exist_ok=True)
        (config_dir / "home").mkdir(parents=True, exist_ok=True)
        (config_dir / "home" / "AppData" / "Local").mkdir(parents=True, exist_ok=True)
        (config_dir / "home" / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
        (config_dir / "tmp").mkdir(parents=True, exist_ok=True)

        env = isolated_environment(
            provenance_home=provenance_home,
            workspace=workspace,
            config_dir=config_dir,
        )
        stages = report["stages"]

        _run_stage(
            "validate library-kb/1.1",
            [sys.executable, str(validator), str(kb_path)],
            cwd=root,
            env=env,
            stages=stages,
        )
        report["assertions"]["contract_validated"] = True

        version_result = _run_stage(
            "check Steward version",
            [str(steward), "--version"],
            cwd=root,
            env=env,
            stages=stages,
        )
        version_match = re.fullmatch(r"steward\s+(\d+\.\d+\.\d+)\s*", version_result.stdout)
        _assert(bool(version_match), "Steward returned an unrecognized version string")
        steward_runtime_version = version_match.group(1) if version_match else ""
        _assert(
            steward_runtime_version == expected["steward"],
            f"Steward executable version mismatch: expected {expected['steward']}, found {steward_runtime_version}",
        )
        report["components"]["steward"]["runtime_version"] = steward_runtime_version

        _run_stage(
            "Steward review scaffold",
            [
                str(steward),
                "review",
                "scaffold",
                "--topic",
                "AI4Science/Materials",
                "--kb",
                str(kb_path),
                "--out",
                str(input_path),
                "--force",
            ],
            cwd=root,
            env=env,
            stages=stages,
        )
        review_input = _load_json(input_path)
        selected_keys = [paper.get("key") for paper in review_input.get("papers", [])]
        _assert(
            review_input.get("count") == 2 and selected_keys == ["DEMO0001", "DEMO0002"],
            "Steward topic boundary did not select exactly the two expected synthetic papers",
        )
        report["assertions"]["steward_topic_boundary"] = True

        _run_stage(
            "Steward review assemble",
            [
                str(steward),
                "review",
                "assemble",
                "--input",
                str(input_path),
                "--draft",
                str(draft_path),
                "--out",
                str(review_path),
                "--force",
            ],
            cwd=root,
            env=env,
            stages=stages,
        )
        review_text = review_path.read_text(encoding="utf-8")
        _assert(
            "DEMO0001" in review_text and "DEMO0002" in review_text and "DEMO0003" not in review_text,
            "assembled review does not preserve the expected authoritative paper boundary",
        )
        report["assertions"]["review_assembled"] = True

        _run_stage(
            "Provenance library ingest",
            [str(prov_ingest_library), str(kb_path)],
            cwd=root,
            env=env,
            stages=stages,
        )
        library_memory = _load_json(library_memory_path)
        library_items = library_memory.get("items") or []
        citekeys = {
            item.get("key"): item.get("citekey")
            for item in library_items
            if isinstance(item, dict)
        }
        _assert(
            library_memory.get("schema_version") == "library-kb/1.1"
            and library_memory.get("count") == 3
            and citekeys.get("DEMO0002") == "liu2024ActiveLearning",
            "Provenance did not preserve the expected library-kb/1.1 snapshot and citekey",
        )

        _run_stage(
            "Provenance project ingest",
            [str(prov_ingest_vault), str(workspace)],
            cwd=root,
            env=env,
            stages=stages,
        )
        project_memory = _load_json(project_memory_path)
        projects = project_memory.get("projects") or {}
        _assert(
            project_memory.get("count") == 1 and "synthetic-catalyst-discovery" in projects,
            "Provenance did not ingest the synthetic Markdown project",
        )
        report["assertions"]["memory_ingested"] = True

        _run_stage(
            "Provenance search build",
            [str(prov_search), "--build"],
            cwd=root,
            env=env,
            stages=stages,
        )
        _assert(search_index_path.is_file(), "Provenance search index was not created")
        search_result = _run_stage(
            "Provenance literature search",
            [str(prov_search), "--exact", "calibration"],
            cwd=root,
            env=env,
            stages=stages,
        )
        _assert(
            "id=lit:DEMO0002" in search_result.stdout,
            "Provenance search returned no expected synthetic literature hit",
        )
        search_path.write_bytes(search_result.stdout.encode("utf-8"))
        report["assertions"]["search_hit_verified"] = True

        requests = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_portfolio", "arguments": {}}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_current_context", "arguments": {"project_id": "synthetic-catalyst-discovery"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_brain", "arguments": {"query": "calibration", "limit": 5}},
            },
        ]
        request_text = "\n".join(json.dumps(item, separators=(",", ":")) for item in requests) + "\n"
        mcp_result = _run_stage(
            "Provenance MCP context",
            [str(prov_mcp)],
            cwd=root,
            env=env,
            stages=stages,
            input_text=request_text,
        )
        responses: dict[int, dict[str, Any]] = {}
        for line in mcp_result.stdout.splitlines():
            if line.strip().startswith("{"):
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(response, dict) and isinstance(response.get("id"), int):
                    responses[response["id"]] = response
        _assert(set(responses) == {0, 1, 2, 3}, "Provenance MCP did not return all four responses")
        initialize = responses[0]
        _assert(
            initialize.get("jsonrpc") == "2.0"
            and "error" not in initialize
            and isinstance(initialize.get("result"), dict),
            "Provenance MCP initialize response is invalid",
        )
        server_info = initialize["result"].get("serverInfo") or {}
        _assert(server_info.get("name") == "provenance", "MCP server is not Provenance")
        provenance_runtime_version = str(server_info.get("version") or "")
        _assert(
            provenance_runtime_version == expected["provenance"],
            f"Provenance runtime version mismatch: expected {expected['provenance']}, found {provenance_runtime_version}",
        )
        report["components"]["provenance"]["runtime_version"] = provenance_runtime_version
        portfolio_text = _mcp_text(responses[1], 1)
        context_text = _mcp_text(responses[2], 2)
        search_text = _mcp_text(responses[3], 3)
        _assert(
            "Synthetic Catalyst Discovery" in portfolio_text
            and "Physics-informed learning for materials discovery" in context_text
            and "Active learning for closed-loop materials experiments" in context_text
            and "id=lit:DEMO0002" in search_text,
            "Provenance MCP responses did not surface the expected project and literature context",
        )
        mcp_path.write_bytes(mcp_result.stdout.encode("utf-8"))
        report["assertions"]["mcp_context_verified"] = True

        report["demo_status"] = "passed"
        report["artifacts"] = _present_artifacts(root, report["expected_artifacts"])
        _write_json(root, report_path, report)
        return report_path
    except DemoError as exc:
        report["demo_status"] = "failed"
        report["error"] = str(exc)
        report["artifacts"] = _present_artifacts(root, report["expected_artifacts"])
        _write_failed_report(root, report_path, report)
        raise
    except Exception as exc:
        wrapped = DemoError(f"unexpected demo failure ({type(exc).__name__}): {exc}")
        report["demo_status"] = "failed"
        report["error"] = str(wrapped)
        report["artifacts"] = _present_artifacts(root, report["expected_artifacts"])
        _write_failed_report(root, report_path, report)
        raise wrapped from exc
