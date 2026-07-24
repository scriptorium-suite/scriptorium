#!/usr/bin/env python3
"""Offline clean-install lifecycle acceptance for the Scriptorium runtime.

Only explicitly supplied local source checkouts are used. The runner stages the
public package trees into a temporary directory, builds local wheels without an
index or build isolation, then verifies install, uninstall, reinstall, doctor,
and the credential-free demo in a brand-new virtual environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path
from typing import Any


PACKAGES = {
    "scriptorium-suite": Path("src") / "scriptorium",
    "scriptorium-steward": Path("src") / "steward",
    "provenance": Path("provenance"),
}
MODULES = {
    "scriptorium-suite": "scriptorium",
    "scriptorium-steward": "steward",
    "provenance": "provenance",
}
ENTRIES = ("scriptorium", "steward", "prov-sync-pull")
SYSTEM_ENV = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TERM",
    "WINDIR",
}
RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.+-][A-Za-z0-9.-]+)?$")


class LifecycleFailure(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.safe_message = message


def project_identity(root: Path, expected_name: str) -> dict[str, str]:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise LifecycleFailure("validate-sources", "A required source is unavailable.") from exc
    if root.is_symlink() or not resolved.is_dir():
        raise LifecycleFailure("validate-sources", "A source root is unsafe.")
    pyproject = resolved / "pyproject.toml"
    if pyproject.is_symlink() or not pyproject.is_file():
        raise LifecycleFailure("validate-sources", "A source lacks pyproject.toml.")
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        name, version = project["name"], project["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise LifecycleFailure("validate-sources", "A package identity is invalid.") from exc
    if name != expected_name or not isinstance(version, str) or not version:
        raise LifecycleFailure("validate-sources", "A package identity does not match.")
    return {"name": name, "version": version}


def release_relation(previous: str, current: str) -> str:
    old, new = RELEASE.fullmatch(previous), RELEASE.fullmatch(current)
    if old is None or new is None:
        return "unsupported"
    old_core = tuple(int(value) for value in old.groups())
    new_core = tuple(int(value) for value in new.groups())
    if old_core < new_core:
        return "previous-is-older"
    if old_core == new_core:
        return "same"
    return "previous-is-newer"


def isolated_environment(root: Path, scripts: Path | None = None) -> dict[str, str]:
    profile = root / "profile"
    runtime_temp = root / "runtime-temp"
    appdata = profile / "AppData" / "Roaming"
    localappdata = profile / "AppData" / "Local"
    for directory in (profile, runtime_temp, appdata, localappdata, root / "pip-cache"):
        directory.mkdir(parents=True, exist_ok=True)
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in SYSTEM_ENV
    }
    path = [str(scripts)] if scripts is not None else []
    if env.get("PATH"):
        path.append(env["PATH"])
    env.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(localappdata),
            "TEMP": str(runtime_temp),
            "TMP": str(runtime_temp),
            "TMPDIR": str(runtime_temp),
            "CODEX_HOME": str(profile / ".codex"),
            "SCRIPTORIUM_CONFIG_DIR": str(root / "config"),
            "PIP_CACHE_DIR": str(root / "pip-cache"),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PATH": os.pathsep.join(path),
        }
    )
    return env


def run(
    command: list[str | Path],
    *,
    stage: str,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [str(value) for value in command],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise LifecycleFailure(stage, "A local subprocess could not complete.") from exc
    if result.returncode != 0:
        raise LifecycleFailure(stage, "A local subprocess returned a failure.")
    return result


def copy_public_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise LifecycleFailure("stage-sources", "A public source tree is unsafe.")
    destination.mkdir(parents=True, exist_ok=False)
    for item in sorted(source.rglob("*"), key=lambda path: path.as_posix()):
        relative = item.relative_to(source)
        if "__pycache__" in relative.parts or item.suffix in {".pyc", ".pyo"}:
            continue
        if item.is_symlink():
            raise LifecycleFailure("stage-sources", "A public source contains a link.")
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        else:
            raise LifecycleFailure("stage-sources", "A public source contains a special file.")


def copy_metadata(source: Path, destination: Path) -> None:
    for filename in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        candidate = source / filename
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise LifecycleFailure("stage-sources", "A metadata file is unsafe.")
        shutil.copy2(candidate, destination / filename)


def stage_package(source: Path, destination: Path, distribution: str) -> Path:
    package = PACKAGES.get(distribution)
    if package is None or destination.exists() or destination.is_symlink():
        raise LifecycleFailure("stage-sources", "A package staging request is invalid.")
    destination.mkdir(parents=True)
    copy_metadata(source, destination)
    if not (destination / "pyproject.toml").is_file():
        raise LifecycleFailure("stage-sources", "Staged metadata is incomplete.")
    copy_public_tree(source / package, destination / package)
    return destination


def stage_spec(source: Path, destination: Path) -> Path:
    project_identity(source, "scriptorium-spec")
    if destination.exists() or destination.is_symlink():
        raise LifecycleFailure("stage-sources", "The Spec staging target is occupied.")
    destination.mkdir(parents=True)
    copy_metadata(source, destination)
    for directory in ("tools", "examples", "schemas"):
        copy_public_tree(source / directory, destination / directory)
    return destination


def build_wheel(
    source: Path,
    *,
    distribution: str,
    label: str,
    root: Path,
    env: dict[str, str],
) -> tuple[Path, Path]:
    staged = stage_package(
        source,
        root / "staged-sources" / label,
        distribution,
    )
    wheel_dir = root / "wheels" / label
    wheel_dir.mkdir(parents=True)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            wheel_dir,
            staged,
        ],
        stage=f"build-{label}",
        cwd=root,
        env=env,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1 or wheels[0].is_symlink():
        raise LifecycleFailure(f"build-{label}", "The local wheel build was ambiguous.")
    return wheels[0], staged


def venv_paths(root: Path) -> tuple[Path, Path]:
    scripts = root / ("Scripts" if os.name == "nt" else "bin")
    return scripts / ("python.exe" if os.name == "nt" else "python"), scripts


def entry(scripts: Path, name: str) -> Path:
    return scripts / (f"{name}.exe" if os.name == "nt" else name)


def install(
    python: Path,
    wheels: list[Path],
    *,
    cwd: Path,
    env: dict[str, str],
    stage: str,
    force: bool = False,
) -> None:
    command: list[str | Path] = [
        python,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
    ]
    if force:
        command.append("--force-reinstall")
    run([*command, *wheels], stage=stage, cwd=cwd, env=env)


def package_state(
    python: Path, *, cwd: Path, env: dict[str, str], stage: str
) -> dict[str, dict[str, Any]]:
    code = (
        "import importlib.metadata as m, importlib.util, json\n"
        f"items={tuple(MODULES.items())!r}\n"
        "result={}\n"
        "for dist,module in items:\n"
        "  try: version=m.version(dist)\n"
        "  except m.PackageNotFoundError: version=None\n"
        "  result[dist]={'version':version,'importable':importlib.util.find_spec(module) is not None}\n"
        "print(json.dumps(result,sort_keys=True))\n"
    )
    output = run(
        [python, "-I", "-c", code],
        stage=stage,
        cwd=cwd,
        env=env,
    ).stdout
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise LifecycleFailure(stage, "Package verification did not return JSON.") from exc
    if not isinstance(value, dict):
        raise LifecycleFailure(stage, "Package verification returned an invalid value.")
    return value


def verify_installed(
    python: Path,
    scripts: Path,
    expected: dict[str, str],
    *,
    cwd: Path,
    env: dict[str, str],
    stage: str,
) -> None:
    state = package_state(python, cwd=cwd, env=env, stage=stage)
    for distribution, version in expected.items():
        item = state.get(distribution)
        if (
            not isinstance(item, dict)
            or item.get("version") != version
            or item.get("importable") is not True
        ):
            raise LifecycleFailure(stage, "An installed package failed verification.")
    for name in ENTRIES:
        candidate = entry(scripts, name)
        if candidate.is_symlink() or not candidate.is_file():
            raise LifecycleFailure(stage, "A required console entry is unavailable.")
    if run(
        [entry(scripts, "scriptorium"), "--version"],
        stage=stage,
        cwd=cwd,
        env=env,
    ).stdout.strip() != f"scriptorium {expected['scriptorium-suite']}":
        raise LifecycleFailure(stage, "The Scriptorium version did not match.")
    if run(
        [entry(scripts, "steward"), "--version"],
        stage=stage,
        cwd=cwd,
        env=env,
    ).stdout.strip() != f"steward {expected['scriptorium-steward']}":
        raise LifecycleFailure(stage, "The Steward version did not match.")
    try:
        capabilities = json.loads(
            run(
                [entry(scripts, "prov-sync-pull"), "--capabilities", "--json"],
                stage=stage,
                cwd=cwd,
                env=env,
            ).stdout
        )
    except json.JSONDecodeError as exc:
        raise LifecycleFailure(stage, "The Provenance capability probe was invalid.") from exc
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("operation") != "pull.capabilities"
        or capabilities.get("generated_by", {}).get("version") != expected["provenance"]
    ):
        raise LifecycleFailure(stage, "The Provenance version did not match.")


def uninstall(
    python: Path, scripts: Path, *, cwd: Path, env: dict[str, str]
) -> None:
    run(
        [python, "-m", "pip", "uninstall", "--yes", *PACKAGES],
        stage="uninstall",
        cwd=cwd,
        env=env,
    )
    state = package_state(python, cwd=cwd, env=env, stage="verify-uninstalled")
    if any(
        not isinstance(item, dict)
        or item.get("version") is not None
        or item.get("importable") is not False
        for item in state.values()
    ):
        raise LifecycleFailure("verify-uninstalled", "A package remained after uninstall.")
    if any(entry(scripts, name).exists() for name in ENTRIES):
        raise LifecycleFailure("verify-uninstalled", "A console entry remained after uninstall.")


def all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            for text in (*all_strings(key), *all_strings(item))
        ]
    if isinstance(value, list):
        return [text for item in value for text in all_strings(item)]
    return []


def verify_demo(
    scripts: Path,
    *,
    root: Path,
    spec: Path,
    steward: Path,
    provenance: Path,
    env: dict[str, str],
) -> None:
    command = entry(scripts, "scriptorium")
    common = [
        "--spec-root",
        spec,
        "--steward-root",
        steward,
        "--provenance-root",
        provenance,
    ]
    run(
        [command, "doctor", "--target", "demo", "--json", *common],
        stage="doctor-after-reinstall",
        cwd=root,
        env=env,
    )
    output = root / "synthetic-demo"
    run(
        [command, "demo", "--output", output, *common],
        stage="demo-after-reinstall",
        cwd=root,
        env=env,
    )
    try:
        report = json.loads((output / "demo-report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleFailure("demo-after-reinstall", "The demo report was invalid.") from exc
    if not isinstance(report, dict) or report.get("demo_status") != "passed":
        raise LifecycleFailure("demo-after-reinstall", "The synthetic demo did not pass.")
    forbidden = str(root).replace("\\", "/").casefold()
    if any(forbidden in text.replace("\\", "/").casefold() for text in all_strings(report)):
        raise LifecycleFailure("demo-after-reinstall", "The demo report exposed its local path.")


def write_report(path: Path, report: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise LifecycleFailure("write-report", "The report destination already exists.")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise LifecycleFailure("write-report", "The report parent is unavailable.") from exc
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise LifecycleFailure("write-report", "The temporary report target is occupied.")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise LifecycleFailure("write-report", "The lifecycle report could not be written.") from exc


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--scriptorium-root", type=Path, required=True)
    value.add_argument("--spec-root", type=Path, required=True)
    value.add_argument("--steward-root", type=Path, required=True)
    value.add_argument("--provenance-root", type=Path, required=True)
    value.add_argument("--previous-scriptorium-root", type=Path)
    value.add_argument("--report", type=Path)
    value.add_argument("--require-windows", action="store_true")
    return value


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.require_windows and os.name != "nt":
        raise LifecycleFailure("platform", "This acceptance run requires Windows.")
    roots = {
        "scriptorium-suite": args.scriptorium_root,
        "scriptorium-steward": args.steward_root,
        "provenance": args.provenance_root,
    }
    identities = {
        name: project_identity(root, name) for name, root in roots.items()
    }
    expected = {name: value["version"] for name, value in identities.items()}
    stages = [{"name": "validate-sources", "status": "passed"}]
    limitations = [
        "OS-level network egress is not instrumented; pip is constrained to local sources with no index.",
        "Live Codex/Claude sessions and PowerPoint editing remain manual acceptance items.",
    ]
    transition: dict[str, Any] = {"status": "not-requested"}
    if args.previous_scriptorium_root is None:
        limitations.append(
            "Upgrade/downgrade is not automated because no older local source was supplied."
        )
        previous = None
        relation = "not-requested"
    else:
        previous = project_identity(
            args.previous_scriptorium_root, "scriptorium-suite"
        )
        relation = release_relation(previous["version"], expected["scriptorium-suite"])
        transition.update(
            {
                "status": "pending",
                "previous_version": previous["version"],
                "current_version": expected["scriptorium-suite"],
                "relation": relation,
            }
        )
    try:
        import setuptools  # noqa: F401
    except ImportError as exc:
        raise LifecycleFailure(
            "build-tooling", "The local setuptools build backend is unavailable."
        ) from exc
    stages.append({"name": "build-tooling", "status": "passed"})

    with tempfile.TemporaryDirectory(prefix="scriptorium-install-lifecycle-") as raw:
        root = Path(raw)
        build_env = isolated_environment(root / "build-environment")
        wheels: list[Path] = []
        staged: dict[str, Path] = {}
        for distribution, source in roots.items():
            wheel, staged_root = build_wheel(
                source.resolve(strict=True),
                distribution=distribution,
                label=distribution,
                root=root,
                env=build_env,
            )
            wheels.append(wheel)
            staged[distribution] = staged_root
            stages.append({"name": f"build-{distribution}", "status": "passed"})
        spec = stage_spec(
            args.spec_root.resolve(strict=True),
            root / "staged-sources" / "scriptorium-spec",
        )
        stages.append({"name": "stage-scriptorium-spec", "status": "passed"})

        previous_wheel = None
        if previous is not None and relation == "previous-is-older":
            previous_wheel, _ = build_wheel(
                args.previous_scriptorium_root.resolve(strict=True),
                distribution="scriptorium-suite",
                label="previous-scriptorium-suite",
                root=root,
                env=build_env,
            )
            stages.append(
                {"name": "build-previous-scriptorium-suite", "status": "passed"}
            )
        elif previous is not None:
            transition["status"] = "not-covered"
            limitations.append(
                "Upgrade/downgrade is not claimed because the supplied versions are not an older-to-current pair."
            )

        environment = root / "clean-venv"
        try:
            venv.EnvBuilder(with_pip=True, symlinks=False).create(environment)
        except OSError as exc:
            raise LifecycleFailure("create-clean-venv", "The clean venv could not be created.") from exc
        python, scripts = venv_paths(environment)
        if not python.is_file():
            raise LifecycleFailure("create-clean-venv", "The clean venv lacks Python.")
        stages.append({"name": "create-clean-venv", "status": "passed"})
        runtime_env = isolated_environment(root / "runtime-environment", scripts)

        lifecycle = {
            "clean_install": "not-run",
            "uninstall": "not-run",
            "reinstall": "not-run",
            "doctor_and_demo": "not-run",
            "version_transition": transition,
        }
        install(python, wheels, cwd=root, env=runtime_env, stage="clean-install")
        verify_installed(
            python, scripts, expected, cwd=root, env=runtime_env, stage="verify-clean-install"
        )
        lifecycle["clean_install"] = "passed"
        stages.extend(
            [
                {"name": "clean-install", "status": "passed"},
                {"name": "verify-clean-install", "status": "passed"},
            ]
        )
        uninstall(python, scripts, cwd=root, env=runtime_env)
        lifecycle["uninstall"] = "passed"
        stages.extend(
            [
                {"name": "uninstall", "status": "passed"},
                {"name": "verify-uninstalled", "status": "passed"},
            ]
        )
        install(python, wheels, cwd=root, env=runtime_env, stage="reinstall")
        verify_installed(
            python, scripts, expected, cwd=root, env=runtime_env, stage="verify-reinstall"
        )
        lifecycle["reinstall"] = "passed"
        stages.extend(
            [
                {"name": "reinstall", "status": "passed"},
                {"name": "verify-reinstall", "status": "passed"},
            ]
        )
        if previous_wheel is not None and previous is not None:
            install(
                python,
                [previous_wheel],
                cwd=root,
                env=runtime_env,
                stage="verified-downgrade",
                force=True,
            )
            old_expected = dict(expected)
            old_expected["scriptorium-suite"] = previous["version"]
            verify_installed(
                python, scripts, old_expected, cwd=root, env=runtime_env, stage="verify-downgrade"
            )
            install(
                python,
                [wheels[0]],
                cwd=root,
                env=runtime_env,
                stage="verified-upgrade",
                force=True,
            )
            verify_installed(
                python, scripts, expected, cwd=root, env=runtime_env, stage="verify-upgrade"
            )
            transition["status"] = "passed"
            stages.extend(
                [
                    {"name": "verified-downgrade", "status": "passed"},
                    {"name": "verify-downgrade", "status": "passed"},
                    {"name": "verified-upgrade", "status": "passed"},
                    {"name": "verify-upgrade", "status": "passed"},
                ]
            )
        verify_demo(
            scripts,
            root=root,
            spec=spec,
            steward=staged["scriptorium-steward"],
            provenance=staged["provenance"],
            env=runtime_env,
        )
        lifecycle["doctor_and_demo"] = "passed"
        stages.extend(
            [
                {"name": "doctor-after-reinstall", "status": "passed"},
                {"name": "demo-after-reinstall", "status": "passed"},
            ]
        )

    return {
        "format_version": 1,
        "operation": "windows-install-lifecycle",
        "status": "passed",
        "components": identities,
        "platform": {
            "implementation": sys.implementation.name,
            "python": ".".join(str(value) for value in sys.version_info[:3]),
            "windows": os.name == "nt",
        },
        "safety": {
            "data": "synthetic-only",
            "network_index": "disabled",
            "build_isolation": "disabled",
            "runtime_dependencies_added": False,
            "temporary_workspace": True,
        },
        "lifecycle": lifecycle,
        "stages": stages,
        "limitations": limitations,
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = execute(args)
        exit_code = 0
    except LifecycleFailure as exc:
        report = {
            "format_version": 1,
            "operation": "windows-install-lifecycle",
            "status": "failed",
            "failure": {"stage": exc.stage, "message": exc.safe_message},
        }
        exit_code = 1
    except Exception:
        report = {
            "format_version": 1,
            "operation": "windows-install-lifecycle",
            "status": "failed",
            "failure": {
                "stage": "internal",
                "message": "The lifecycle runner encountered an internal failure.",
            },
        }
        exit_code = 2
    if args.report is not None:
        try:
            write_report(args.report, report)
        except LifecycleFailure as exc:
            report = {
                "format_version": 1,
                "operation": "windows-install-lifecycle",
                "status": "failed",
                "failure": {"stage": exc.stage, "message": exc.safe_message},
            }
            exit_code = 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
