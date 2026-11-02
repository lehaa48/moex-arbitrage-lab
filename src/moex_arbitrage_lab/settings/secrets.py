"""Secret redaction for diagnostics (docs/STORAGE_AND_SECRETS_POLICY.md).

P0 never loads, requests or stores credentials. These helpers only guarantee that anything
printed about the environment (bootstrap/storage reports, errors) cannot echo a secret value.
"""

import re
from collections.abc import Mapping
from typing import Final

# Names from .env.example; values are read by later approved stages only, never by P0.
KNOWN_SECRET_ENV_NAMES: Final = frozenset({"TBANK_INVEST_TOKEN", "DATABENTO_API_KEY"})
REDACTED: Final = "<redacted>"
EMPTY: Final = "<empty>"

_SECRET_NAME: Final = re.compile(
    r"token|secret|passw(or)?d|api[_-]?key|auth|credential|cookie|bearer|private[_-]?key",
    re.IGNORECASE,
)


def is_secret_name(name: str) -> bool:
    """True if ``name`` looks like it holds a secret (variable, header or parameter name)."""
    return name in KNOWN_SECRET_ENV_NAMES or _SECRET_NAME.search(name) is not None


def redact(value: str | None) -> str:
    """Never return any part of ``value``; only whether it is present."""
    return EMPTY if not value else REDACTED


def redacted_environment(env: Mapping[str, str], names: frozenset[str]) -> dict[str, str]:
    """Return ``names`` from ``env`` for display; secret-looking names are redacted."""
    shown: dict[str, str] = {}
    for name in sorted(names):
        value = env.get(name)
        if value is None:
            continue
        shown[name] = redact(value) if is_secret_name(name) else value
    return shown
