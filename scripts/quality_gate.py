"""Offline P0 quality gate: format check, lint, strict types, tests with coverage.

Adapted from moex-event-alpha ``scripts/quality_gate.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-005).

Run from the repository root, after the fail-closed bootstrap (ADR-0002):
    . .\\scripts\\project_env.ps1   (PowerShell)  or  . scripts/project_env.sh   (bash)
    uv run --locked --offline python scripts/quality_gate.py --scope full

``--scope changed`` runs only the P0 test suite (the relevant tests for the code P0 owns);
``--scope full`` is the complete gate. Both refuse to start unless the checkout passes the
storage guard and every tool cache, temp and virtualenv variable points inside the
repository. No step contacts the network: network tests are deselected by the pytest
configuration and none exist in P0. Stops at the first failing step and exits non-zero.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

from moex_arbitrage_lab.settings import (
    StorageCategory,
    StoragePathError,
    environment_violations,
    resolve_project_paths,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

TESTS: Final = ("tests", "--cov", "--cov-report=term-missing")
SCOPES: Final[dict[str, list[tuple[str, list[str]]]]] = {
    "changed": [("tests", ["-m", "pytest", *TESTS])],
    "full": [
        ("format", ["-m", "ruff", "format", "--check", "."]),
        ("lint", ["-m", "ruff", "check", "."]),
        ("types", ["-m", "mypy"]),
        ("tests", ["-m", "pytest", *TESTS]),
    ],
}


def storage_preflight() -> list[str]:
    """Return the reasons the gate must not run here (empty when it may)."""
    try:
        paths = resolve_project_paths()
        problems = environment_violations(os.environ, paths)
        virtualenv = paths.category_dir(StorageCategory.VIRTUALENV)
    except StoragePathError as error:
        return [str(error)]
    if not Path(sys.executable).absolute().is_relative_to(virtualenv):
        problems.append(f"interpreter is not the project virtualenv in {virtualenv}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scope", choices=sorted(SCOPES), default="full")
    scope = parser.parse_args(argv).scope
    problems = storage_preflight()
    if problems:
        for problem in problems:
            print(f"REFUSED (ADR-0002): {problem}", flush=True)
        print("FAILED: storage preflight; run scripts/project_env.* first", flush=True)
        return 1
    for name, args in SCOPES[scope]:
        print(f"==> {name}: python {' '.join(args)}", flush=True)
        result = subprocess.run([sys.executable, *args], cwd=REPO_ROOT, check=False)
        if result.returncode != 0:
            print(f"FAILED: {name} (exit {result.returncode})", flush=True)
            return result.returncode
    print(f"PASSED: all quality gate steps (scope={scope})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
