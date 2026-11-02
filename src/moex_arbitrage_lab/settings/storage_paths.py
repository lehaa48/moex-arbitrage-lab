"""Project storage-path guard (ADR-0002, docs/STORAGE_AND_SECRETS_POLICY.md).

Adapted (minimal) from moex-event-alpha ``settings/storage_paths.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-006).

Every project-created file lives inside the repository root, and the root must be exactly
``REQUIRED_ROOT``. ``STORAGE_LAYOUT`` is the single source of runtime locations; each entry is
relative to the root. The guard fails closed:

- the root must be an absolute, existing, plain directory on ``D:`` (a root on ``C:`` is
  refused before the disk is touched), reached without any link or alias in its path, equal
  to ``REQUIRED_ROOT``, with a ``.git`` directory and this project's ``pyproject.toml``;
- a relative path below a storage category must match a lowercase allowlist grammar
  (``[a-z0-9][a-z0-9._=-]*`` per segment, no trailing dot, no reserved device name), so
  absolute and drive-relative paths, ``..``, backslashes, colons, ``~`` short names,
  ``%VAR%``/``$VAR`` references and case variants cannot be expressed;
- every existing component between the root and the target must be a plain file or
  directory: symlinks, junctions and other reparse points are refused even when they point
  back inside the root, because a link can be retargeted later;
- the resolved target must equal the lexical path and stay inside its category;
- an existing target file with more than one hard link is refused.

The checks run before use and cannot exclude a later local race; writers open files with
exclusive creation (``settings/raw_publication.py``). The module never reads the process
environment: callers inject it.
"""

import os
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Final

PROJECT_NAME: Final = "moex-arbitrage-lab"
# Changing the required root is an ADR-0002 decision.
REQUIRED_ROOT: Final = PureWindowsPath("D:/project/moex-arbitrage-lab")
REQUIRED_DRIVE: Final = REQUIRED_ROOT.drive
REFUSED_DRIVES: Final = frozenset({"C:"})
# Optional; if set it must name exactly REQUIRED_ROOT. It can never move storage elsewhere.
STORAGE_ROOT_ENV: Final = "MOEX_ARBITRAGE_STORAGE_ROOT"
_CONSTRUCTION_KEY: Final = object()


class StorageCategory(StrEnum):
    RAW = "raw"
    NORMALIZED = "normalized"
    ALIGNED = "aligned"
    MANIFESTS = "manifests"
    CHECKSUMS = "checksums"
    REPORTS = "reports"
    CACHE = "cache"
    TEMP = "temp"
    LOGS = "logs"
    VIRTUALENV = "virtualenv"


# Relative to the repository root, as in docs/STORAGE_AND_SECRETS_POLICY.md. Every location is
# git-ignored (tests/unit/test_repository_hygiene.py).
STORAGE_LAYOUT: Final[Mapping[StorageCategory, PurePosixPath]] = MappingProxyType(
    {
        StorageCategory.RAW: PurePosixPath("data/raw"),
        StorageCategory.NORMALIZED: PurePosixPath("data/normalized"),
        StorageCategory.ALIGNED: PurePosixPath("data/aligned"),
        StorageCategory.MANIFESTS: PurePosixPath("artifacts/manifests"),
        StorageCategory.CHECKSUMS: PurePosixPath("artifacts/checksums"),
        StorageCategory.REPORTS: PurePosixPath("reports/generated"),
        StorageCategory.CACHE: PurePosixPath("cache"),
        StorageCategory.TEMP: PurePosixPath("tmp"),
        StorageCategory.LOGS: PurePosixPath("logs"),
        # Managed by uv (uv sync); listed so that every project location is in this table.
        StorageCategory.VIRTUALENV: PurePosixPath(".venv"),
    }
)

# Tool cache/temp/output variables, set per process by scripts/project_env.ps1 and
# scripts/project_env.sh (never as global Windows variables). ``None`` means the category
# directory itself. The pytest cache is passed through ``PYTEST_ADDOPTS`` and the pytest base
# temp through ``[tool.pytest.ini_options]``.
TOOL_LOCATION_ENV: Final[Mapping[str, tuple[StorageCategory, str | None]]] = MappingProxyType(
    {
        "UV_CACHE_DIR": (StorageCategory.CACHE, "uv"),
        "PRE_COMMIT_HOME": (StorageCategory.CACHE, "pre-commit"),
        "RUFF_CACHE_DIR": (StorageCategory.CACHE, "ruff"),
        "MYPY_CACHE_DIR": (StorageCategory.CACHE, "mypy"),
        "COVERAGE_FILE": (StorageCategory.CACHE, "coverage/coverage-data"),
        "PYTHONPYCACHEPREFIX": (StorageCategory.CACHE, "pycache"),
        "TMP": (StorageCategory.TEMP, None),
        "TEMP": (StorageCategory.TEMP, None),
        "TMPDIR": (StorageCategory.TEMP, None),
        "UV_PROJECT_ENVIRONMENT": (StorageCategory.VIRTUALENV, None),
    }
)

PYTEST_CACHE_LOCATION: Final = (StorageCategory.CACHE, "pytest")
PYTEST_BASETEMP_LOCATION: Final = (StorageCategory.TEMP, "pytest")

_SEGMENT: Final = re.compile(r"[a-z0-9][a-z0-9._=-]*")
# Keeps root + layout + relative path well under the Windows MAX_PATH of 260 characters.
_MAX_RELATIVE_LENGTH: Final = 160
# Python 3.12 has no os.path.isreserved; a name is reserved whatever its extension.
_RESERVED_NAMES: Final = frozenset(
    {"con", "prn", "aux", "nul"} | {f"{p}{d}" for p in ("com", "lpt") for d in "0123456789"}
)
_UNRESOLVED_VARIABLE: Final = re.compile(r"%[^%]*%|\$\{?[A-Za-z_]|^~")


class StoragePathError(RuntimeError):
    """A storage path is refused; nothing may be read or written there."""


class ProjectRootError(StoragePathError):
    pass


class UnsafeRelativePathError(StoragePathError):
    pass


class PathEscapeError(StoragePathError):
    pass


def default_project_root() -> Path:
    """Repository root derived from this file's location, without following links."""
    return Path(__file__).absolute().parents[3]


def validate_relative_path(relative: str) -> tuple[str, ...]:
    """Return the segments of a safe relative POSIX path or raise."""
    if not relative or len(relative) > _MAX_RELATIVE_LENGTH:
        raise UnsafeRelativePathError(
            f"relative path must have 1 to {_MAX_RELATIVE_LENGTH} characters"
        )
    segments = tuple(relative.split("/"))
    for segment in segments:
        if not _SEGMENT.fullmatch(segment) or segment.endswith("."):
            raise UnsafeRelativePathError(
                f"each segment must match [a-z0-9][a-z0-9._=-]* without a trailing dot: "
                f"{relative!r}"
            )
        if segment.split(".", 1)[0] in _RESERVED_NAMES:
            raise UnsafeRelativePathError(f"segment is a reserved device name: {relative!r}")
    return segments


def _same_path(left: Path | PureWindowsPath, right: Path | PureWindowsPath) -> bool:
    return os.path.normcase(left) == os.path.normcase(right)


def _drive_of(path: Path | PureWindowsPath) -> str:
    return PureWindowsPath(path).drive.upper()


def _is_link_or_reparse_point(status: os.stat_result) -> bool:
    attributes: int = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _lstat(path: Path) -> os.stat_result | None:
    """``lstat`` that returns None for a missing path and fails closed on other errors."""
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StoragePathError(f"cannot inspect {path}: {error.strerror}") from None


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Validated repository root; built only through ``resolve_project_paths``."""

    root: Path
    _key: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._key is not _CONSTRUCTION_KEY:
            raise ProjectRootError("ProjectPaths is built only through resolve_project_paths")

    def category_dir(self, category: StorageCategory) -> Path:
        """Return the checked directory of ``category`` (it need not exist yet)."""
        return self._checked(category, ())

    def resolve(self, category: StorageCategory, relative: str) -> Path:
        """Return the checked location of ``relative`` inside ``category``."""
        return self._checked(category, validate_relative_path(relative))

    def relative_to_root(self, path: Path) -> str:
        """Return ``path`` as a POSIX path relative to the root (for manifests)."""
        return PurePosixPath(*path.relative_to(self.root).parts).as_posix()

    def _checked(self, category: StorageCategory, segments: tuple[str, ...]) -> Path:
        base = self.root.joinpath(*STORAGE_LAYOUT[category].parts)
        target = base.joinpath(*segments)
        current = self.root
        for part in target.relative_to(self.root).parts:
            current = current / part
            status = _lstat(current)
            if status is None:
                break
            if _is_link_or_reparse_point(status):
                raise PathEscapeError(f"link or reparse point inside the project tree: {current}")
        resolved = target.resolve(strict=False)
        if not _same_path(resolved, target) or not resolved.is_relative_to(base):
            raise PathEscapeError(f"path does not resolve to itself inside its category: {target}")
        status = _lstat(resolved)
        if status is not None and stat.S_ISREG(status.st_mode) and status.st_nlink > 1:
            raise PathEscapeError(f"file has more than one hard link: {target}")
        return resolved


def resolve_project_paths() -> ProjectPaths:
    """Validate this checkout as the project root and return the path policy, or raise."""
    return _resolve_project_paths(default_project_root(), REQUIRED_ROOT)


def _resolve_project_paths(candidate: Path, required_root: Path | PureWindowsPath) -> ProjectPaths:
    """Validate ``candidate`` against ``required_root``; tests inject a synthetic root."""
    if not candidate.is_absolute():
        raise ProjectRootError(f"project root must be an absolute path: {candidate}")
    # Checked before the disk is touched: project storage on C: is never acceptable.
    for path in (candidate, required_root):
        if _drive_of(path) in REFUSED_DRIVES:
            raise ProjectRootError(f"project storage on {_drive_of(path)} is refused: {path}")
    status = _lstat(candidate)
    if status is None or not stat.S_ISDIR(status.st_mode) or _is_link_or_reparse_point(status):
        raise ProjectRootError(f"project root is not an existing plain directory: {candidate}")
    resolved = candidate.resolve(strict=True)
    if not _same_path(resolved, candidate):
        raise ProjectRootError(f"project root is reached through a link or alias: {candidate}")
    if not _same_path(resolved, required_root):
        raise ProjectRootError(f"project root must be {required_root}: {candidate}")
    git_status = _lstat(resolved / ".git")
    if git_status is None or not stat.S_ISDIR(git_status.st_mode):
        raise ProjectRootError(f"project root must be the main Git checkout: {candidate}")
    try:
        with (resolved / "pyproject.toml").open("rb") as handle:
            name = tomllib.load(handle).get("project", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError):
        name = None
    if name != PROJECT_NAME:
        raise ProjectRootError(f"not the {PROJECT_NAME} repository root: {candidate}")
    return ProjectPaths(resolved, _CONSTRUCTION_KEY)


def _location_violation(name: str, value: str | None, expected: Path) -> str | None:
    if not value:
        return f"{name} is not set; expected {expected}"
    # The value itself is never echoed: it may be a pasted secret.
    if _UNRESOLVED_VARIABLE.search(value):
        return f"{name} contains an unresolved variable reference; expected {expected}"
    path = Path(value)
    if not path.is_absolute() or not _same_path(path.resolve(strict=False), expected):
        return f"{name} points elsewhere; expected {expected}"
    return None


def tool_location_violations(env: Mapping[str, str], paths: ProjectPaths) -> list[str]:
    """Describe each tool cache/temp variable in ``env`` that is unset or misdirected."""
    violations: list[str] = []
    for name, (category, relative) in TOOL_LOCATION_ENV.items():
        expected = (
            paths.category_dir(category) if relative is None else paths.resolve(category, relative)
        )
        violation = _location_violation(name, env.get(name), expected)
        if violation is not None:
            violations.append(violation)
    return violations


def storage_root_violations(env: Mapping[str, str], paths: ProjectPaths) -> list[str]:
    """``STORAGE_ROOT_ENV`` may be unset/empty or name exactly the validated root."""
    value = env.get(STORAGE_ROOT_ENV)
    if not value:
        return []
    violation = _location_violation(STORAGE_ROOT_ENV, value, paths.root)
    return [] if violation is None else [violation]


def environment_violations(env: Mapping[str, str], paths: ProjectPaths) -> list[str]:
    """All storage-relevant environment violations (tool locations and root override)."""
    return storage_root_violations(env, paths) + tool_location_violations(env, paths)
