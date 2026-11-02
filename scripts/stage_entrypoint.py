"""Guarded launcher for future stage entrypoints (VS Code P1/P2/P3 tasks).

    uv run --locked --offline python scripts/stage_entrypoint.py P2 <module> [args...]

The research runtime is checked first (trading permanently disabled). If ``<module>`` is not
implemented yet, the launcher exits with code 3 and a clear message instead of trying
anything else; it never contacts the network itself. Once an authorized stage adds the
module, the launcher runs it as ``python -m <module> [args...]``.
"""

import importlib.util
import os
import re
import runpy
import sys
from typing import Final

from moex_arbitrage_lab.settings import load_runtime_safety

NOT_IMPLEMENTED_EXIT: Final = 3
_MODULE: Final = re.compile(r"^moex_arbitrage_lab(\.[a-z_][a-z0-9_]*)+$")


def _module_exists(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not re.fullmatch(r"P[1-4]", argv[0]) or not _MODULE.match(argv[1]):
        print("usage: stage_entrypoint.py P1|P2|P3|P4 moex_arbitrage_lab.<module> [args...]")
        return 2
    stage, module, args = argv[0], argv[1], argv[2:]
    load_runtime_safety(os.environ)
    if not _module_exists(module):
        print(
            f"NOT AVAILABLE: {stage} entrypoint {module} is not implemented yet. "
            f"P0 provides only the interface; {stage} needs separate user authorization. "
            "Nothing was run and no network request was made."
        )
        return NOT_IMPLEMENTED_EXIT
    sys.argv = [module, *args]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
