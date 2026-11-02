"""Report this checkout's runtime locations and verify the current process (ADR-0002).

Adapted from moex-event-alpha ``scripts/check_storage_paths.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-004).

This is an evidence check, not the first line of defense: the fail-closed bootstrap
(scripts/project_env.ps1 or scripts/project_env.sh) refuses an unsafe checkout before uv or
Python starts. Run after the bootstrap:
    uv run --locked --offline python scripts/check_storage_paths.py
Prints every runtime location (the path-resolution report) and exits non-zero on any
violation. Read-only: it creates, moves and deletes nothing, and never prints a secret value.
"""

import os
import stat
import sys
from pathlib import Path

from moex_arbitrage_lab.settings import (
    KNOWN_SECRET_ENV_NAMES,
    PYTEST_CACHE_LOCATION,
    REQUIRED_DRIVE,
    STORAGE_ROOT_ENV,
    StorageCategory,
    StoragePathError,
    TradingModeError,
    environment_violations,
    load_runtime_safety,
    redacted_environment,
    resolve_project_paths,
)
from moex_arbitrage_lab.settings.runtime import OrderCapabilityError


def _links_below(directory: Path) -> list[Path]:
    """Every symlink/junction/reparse point below ``directory`` (links are not followed)."""
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        for name in [*dirnames, *filenames]:
            entry = Path(current) / name
            status = entry.lstat()
            attributes: int = getattr(status, "st_file_attributes", 0)
            if stat.S_ISLNK(status.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                found.append(entry)
        # Never descend into a reparse point that os.walk might treat as a directory.
        dirnames[:] = [d for d in dirnames if Path(current, d) not in found]
    return found


def main() -> int:
    violations: list[str] = []
    try:
        paths = resolve_project_paths()
        locations = {category: paths.category_dir(category) for category in StorageCategory}
        pytest_cache = paths.resolve(*PYTEST_CACHE_LOCATION)
    except StoragePathError as error:
        print(f"FAILED: {error}")
        return 1
    try:
        safety = load_runtime_safety(os.environ)
        print(f"runtime: trading_mode={safety.trading_mode} order_capability=False")
    except (TradingModeError, OrderCapabilityError) as error:
        violations.append(str(error))

    print(f"repository root: {paths.root}")
    print("path-resolution report (category, location, state):")
    for category, location in locations.items():
        state = "present" if location.is_dir() else "absent (created on first use)"
        print(f"  {category.value:<11} {location}  [{state}]")
        if location.drive.upper() != REQUIRED_DRIVE or not location.is_relative_to(paths.root):
            violations.append(f"{category.value} resolves outside the root: {location}")
        if location.is_dir():
            violations.extend(f"link or reparse point: {link}" for link in _links_below(location))

    violations.extend(environment_violations(os.environ, paths))
    if not locations[StorageCategory.TEMP].is_dir():
        violations.append("project temp dir does not exist; run scripts/project_env first")
    expected_addopts = f"-o cache_dir={pytest_cache.as_posix()}"
    if os.environ.get("PYTEST_ADDOPTS", "").lower() != expected_addopts.lower():
        violations.append(f"PYTEST_ADDOPTS must be exactly {expected_addopts}")
    if os.environ.get("UV_PYTHON_DOWNLOADS") != "never":
        violations.append("UV_PYTHON_DOWNLOADS must be 'never'")
    virtualenv = locations[StorageCategory.VIRTUALENV]
    interpreter = Path(sys.executable).absolute()
    for label, location in (("interpreter", interpreter), ("sys.prefix", Path(sys.prefix))):
        print(f"  {label:<11} {location}")
        if not location.is_relative_to(virtualenv):
            violations.append(f"{label} is outside the project virtualenv: {location}")
    # The base interpreter is a pre-existing, read-only toolchain install; nothing is written
    # there. It is reported so that reviewers see where it lives.
    print(f"  base python {Path(sys.base_prefix)}  [read-only toolchain, not project state]")

    shown = redacted_environment(os.environ, KNOWN_SECRET_ENV_NAMES | {STORAGE_ROOT_ENV})
    for name in sorted(KNOWN_SECRET_ENV_NAMES):
        print(f"  secret env  {name}: {shown.get(name, '<unset>')} (P0 never reads it)")

    for violation in violations:
        print(f"VIOLATION: {violation}")
    if violations:
        print("FAILED: run scripts/project_env.ps1 or scripts/project_env.sh first")
        return 1
    print("PASSED: runtime locations, tool caches, temp and virtualenv are inside the repository")
    return 0


if __name__ == "__main__":
    sys.exit(main())
