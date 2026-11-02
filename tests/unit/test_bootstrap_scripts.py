"""Fail-closed bootstrap scripts, storage report, quality-gate preflight and stage launcher.

Path matrix (same for PowerShell and Git Bash, migration rows M-002/M-003): the real root
passes and exports every TOOL_LOCATION_ENV variable inside the project; a copy of the scripts
under any other root, a mismatching storage-root override, or a root reached through a
junction is refused and creates nothing. Only local tools are started (powershell.exe from
System32 and bash.exe from the Git installation), by absolute path.
"""

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from moex_arbitrage_lab.settings import (
    STORAGE_ROOT_ENV,
    TOOL_LOCATION_ENV,
    StorageCategory,
    resolve_project_paths,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows bootstrap")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
POWERSHELL = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / (
    "System32/WindowsPowerShell/v1.0/powershell.exe"
)
_GIT = shutil.which("git")
GIT_BASH = Path(_GIT).resolve().parents[1] / "usr" / "bin" / "bash.exe" if _GIT else None
EXPORTED = sorted({*TOOL_LOCATION_ENV, "PYTEST_ADDOPTS", "UV_PYTHON_DOWNLOADS"})
Junction = Callable[[Path, Path], None]


def _clean_env(**extra: str) -> dict[str, str]:
    keep = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "USERPROFILE", "TEMP", "TMP"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(extra)
    return env


def _run(args: list[str], cwd: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=_clean_env(**extra),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _powershell(script_dir: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    printer = "; ".join(
        f"[Console]::WriteLine('{name}=' + [Environment]::GetEnvironmentVariable('{name}'))"
        for name in EXPORTED
    )
    command = f". '{script_dir / 'project_env.ps1'}'; {printer}"
    args = [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive"]
    args += ["-ExecutionPolicy", "Bypass", "-Command", command]
    return _run(args, script_dir.parent, **extra)


def _git_bash(script_dir: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    assert GIT_BASH is not None
    printer = " ".join(f'"{name}=${{{name}-}}"' for name in EXPORTED)
    command = f". scripts/project_env.sh && printf '%s\\n' {printer}"
    return _run([str(GIT_BASH), "--noprofile", "--norc", "-c", command], script_dir.parent, **extra)


RUNNERS = {
    "powershell": pytest.param(
        _powershell,
        marks=pytest.mark.skipif(not POWERSHELL.is_file(), reason="powershell.exe missing"),
    ),
    "git_bash": pytest.param(
        _git_bash,
        marks=pytest.mark.skipif(
            GIT_BASH is None or not GIT_BASH.is_file(), reason="Git Bash missing"
        ),
    ),
}
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _exports(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if re.match(r"^[A-Z_]+=", line))


@pytest.mark.parametrize("runner", RUNNERS.values(), ids=RUNNERS.keys())
def test_valid_root_exports_every_location_inside_the_project(runner: Runner) -> None:
    result = runner(SCRIPTS)

    assert result.returncode == 0, result.stderr
    exports = _exports(result.stdout)
    paths = resolve_project_paths()
    for name, (category, relative) in TOOL_LOCATION_ENV.items():
        expected = paths.category_dir(category)
        if relative is not None:
            expected = expected / relative
        assert os.path.normcase(Path(exports[name])) == os.path.normcase(expected), name
    pytest_cache = paths.resolve(StorageCategory.CACHE, "pytest").as_posix()
    assert exports["PYTEST_ADDOPTS"].lower() == f"-o cache_dir={pytest_cache}".lower()
    assert exports["UV_PYTHON_DOWNLOADS"] == "never"


@pytest.mark.parametrize("runner", RUNNERS.values(), ids=RUNNERS.keys())
def test_scripts_under_another_root_are_refused_and_create_nothing(
    runner: Runner, tmp_path: Path
) -> None:
    fake = tmp_path / "moex-arbitrage-lab"
    (fake / "scripts").mkdir(parents=True)
    (fake / ".git").mkdir()
    shutil.copy2(REPO_ROOT / "pyproject.toml", fake / "pyproject.toml")
    for name in ("project_env.ps1", "project_env.sh", "project_env_check.sh"):
        shutil.copy2(SCRIPTS / name, fake / "scripts" / name)

    result = runner(fake / "scripts")

    assert result.returncode != 0
    # PowerShell names the required root; Git Bash may already refuse the /tmp mount form.
    assert "project_env refused" in result.stderr
    assert sorted(p.name for p in fake.iterdir()) == [".git", "pyproject.toml", "scripts"]


@pytest.mark.parametrize("runner", RUNNERS.values(), ids=RUNNERS.keys())
def test_scripts_reached_through_a_junction_are_refused(
    runner: Runner, tmp_path: Path, junction: Junction
) -> None:
    alias = tmp_path / "alias"
    junction(alias, REPO_ROOT)

    result = runner(alias / "scripts")

    assert result.returncode != 0
    assert "refused" in result.stderr


@pytest.mark.parametrize("runner", RUNNERS.values(), ids=RUNNERS.keys())
@pytest.mark.parametrize("override", ["C:\\elsewhere", "D:\\project\\other"])
def test_mismatching_storage_root_override_is_refused(runner: Runner, override: str) -> None:
    result = runner(SCRIPTS, **{STORAGE_ROOT_ENV: override})

    assert result.returncode != 0
    assert STORAGE_ROOT_ENV in result.stderr


def test_scripts_export_exactly_the_python_tool_locations() -> None:
    ps1 = (SCRIPTS / "project_env.ps1").read_text(encoding="utf-8")
    check = (SCRIPTS / "project_env_check.sh").read_text(encoding="utf-8")
    ps_names = set(re.findall(r"^\s+([A-Z_]+) = ", ps1, flags=re.MULTILINE))
    sh_names = set(re.findall(r"^\s+_mal_emit ([A-Z_]+) ", check, flags=re.MULTILINE))

    assert ps_names == sh_names == set(EXPORTED)


# --- stage launcher and quality-gate preflight ----------------------------------------------


# tbank_history, databento_history and research are implemented since H-0001 v0.2
# (ADR-0005); the launcher must still fail clearly for modules that do not exist.
@pytest.mark.parametrize(
    "module",
    [
        "moex_arbitrage_lab.normalization",
        "moex_arbitrage_lab.sources.cme_datamine",
    ],
)
def test_future_stage_entrypoints_fail_clearly(module: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "stage_entrypoint.py"), "P2", module, "estimate"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 3
    assert f"{module} is not implemented yet" in result.stdout
    assert "no network request was made" in result.stdout


@pytest.mark.parametrize(
    "argv", [[], ["P9", "moex_arbitrage_lab.research"], ["P1", "os"], ["P1", "../evil"]]
)
def test_stage_launcher_rejects_malformed_arguments(argv: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "stage_entrypoint.py"), *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 2


def test_quality_gate_refuses_a_misdirected_environment() -> None:
    env = {**os.environ, "TEMP": r"C:\Windows\Temp"}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "quality_gate.py"), "--scope", "changed"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1
    assert "REFUSED (ADR-0002): TEMP points elsewhere" in result.stdout
