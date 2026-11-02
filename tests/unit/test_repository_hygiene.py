"""Repository hygiene (AGENTS.md, migration rows M-016..M-018): no secrets, no market data,
no legacy namespace, no broker/vendor SDK, a fresh lock and git-ignored runtime state.

Forbidden words are assembled from parts so that this file does not match its own scan.
"""

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from moex_arbitrage_lab.settings import STORAGE_LAYOUT

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
# Generated or environment state; never scanned as project source.
SKIP_DIRS = {".git", ".venv", "cache", "tmp", "logs", ".mypy_cache", ".ruff_cache"}
CODE_DIRS = ("src", "scripts", "tests")

LEGACY_NAMESPACE = "moex" + "_event_" + "alpha"
FORBIDDEN_IMPORTS = re.compile(
    r"^\s*(from|import)\s+("
    + "|".join(
        [
            "t" + "invest",
            "tink" + "off",
            "t_" + "tech",
            "data" + "bento",
            "gr" + "pc",
            "google" + r"\.protobuf",
        ]
    )
    + r")\b",
    re.MULTILINE,
)
FORBIDDEN_LOCK_PACKAGES = (
    "gr" + "pcio",
    "t-" + "tech",
    "tink" + "off",
    "data" + "bento",
    "proto" + "buf",
)
MARKET_DATA_SUFFIXES = (".dbn", ".parquet", ".csv", ".csv.gz", ".zst", ".feather", ".arrow")
SECRET_ASSIGNMENT = re.compile(
    r"(TOKEN|API_KEY|SECRET|PASSWORD)[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9._\-]{8,}", re.IGNORECASE
)


def _walk(skip: set[str]) -> list[Path]:
    """Files below the root; ``skip`` holds root-relative POSIX dirs that are pruned (links
    are never followed)."""
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(REPO_ROOT, followlinks=False):
        here = Path(current)
        dirnames[:] = [
            d for d in dirnames if (here / d).relative_to(REPO_ROOT).as_posix() not in skip
        ]
        files.extend(here / name for name in filenames)
    return files


def _project_files() -> list[Path]:
    return _walk(SKIP_DIRS)


def _code_files() -> list[Path]:
    return [
        path
        for directory in CODE_DIRS
        for path in (REPO_ROOT / directory).rglob("*")
        if path.is_file() and path.suffix in {".py", ".ps1", ".sh"} and path != SELF
    ]


def test_no_legacy_namespace_in_code_or_configuration() -> None:
    files = [*_code_files(), REPO_ROOT / "pyproject.toml", REPO_ROOT / "uv.lock"]
    offenders = [p for p in files if LEGACY_NAMESPACE in p.read_text(encoding="utf-8")]

    assert offenders == []


def test_no_broker_or_vendor_sdk_imports() -> None:
    offenders = [
        p for p in _code_files() if FORBIDDEN_IMPORTS.search(p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_rejected_legacy_subsystems_are_absent() -> None:
    package = REPO_ROOT / "src" / "moex_arbitrage_lab"
    present = {p.name for p in package.iterdir() if p.is_dir() and p.name != "__pycache__"}

    assert present == {"contracts", "settings"}
    for rejected in ("collectors", "storage", "replay", "monitoring", "execution", "risk"):
        assert not (package / rejected).exists()
    for module in ("credentials", "credential_manager", "network_policy", "source_registry"):
        assert not list(package.rglob(f"{module}.py"))


def test_fresh_lock_has_no_legacy_broker_or_grpc_graph() -> None:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    names = {package["name"] for package in lock["package"]}

    assert not [n for n in names if any(word in n for word in FORBIDDEN_LOCK_PACKAGES)]
    assert {"pydantic", "pytest", "mypy", "ruff"} <= names
    hosts = set(re.findall(r"https://([^/\"]+)", (REPO_ROOT / "uv.lock").read_text("utf-8")))
    assert hosts <= {"pypi.org", "files.pythonhosted.org"}


def test_runtime_dependencies_are_minimal() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["dependencies"] == ["pydantic>=2.9,<3"]
    assert "sources" not in project.get("tool", {}).get("uv", {})


def test_no_secret_values_or_env_file() -> None:
    assert not (REPO_ROOT / ".env").exists()
    offenders: list[str] = []
    for path in _project_files():
        if path == SELF or path.suffix in {".lock", ".png", ".jpg"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in SECRET_ASSIGNMENT.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}")
    # Synthetic test fakes are allowed only in tests and must say so on the same line.
    real = [
        o
        for o in offenders
        if not o.startswith("tests")
        or "synthetic" not in _line(REPO_ROOT / o.rsplit(":", 1)[0], int(o.rsplit(":", 1)[1]))
    ]
    assert real == []


def _line(path: Path, number: int) -> str:
    return path.read_text(encoding="utf-8").splitlines()[number - 1].lower()


def test_no_market_data_files_in_the_project() -> None:
    offenders = [
        p.relative_to(REPO_ROOT)
        # Generated state (tmp, logs, artifacts) is scanned too; only Git internals, the
        # virtualenv and the uv package cache (third-party archives) are pruned.
        for p in _walk({".git", ".venv", "cache/uv"})
        if p.name.lower().endswith(MARKET_DATA_SUFFIXES)
    ]
    data_dir = REPO_ROOT / "data"

    assert offenders == []
    assert not data_dir.exists() or not any(p.is_file() for p in data_dir.rglob("*"))


@pytest.mark.parametrize("location", sorted(str(p) for p in STORAGE_LAYOUT.values()))
def test_runtime_locations_are_git_ignored(location: str) -> None:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "check-ignore", "-q", "--no-index", f"{location}/x"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, location
