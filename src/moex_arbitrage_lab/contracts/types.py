"""Shared strict field types for the versioned contracts.

Semantics adapted from moex-event-alpha ``contracts/envelope.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-008): integer UTC nanoseconds,
int64 bounds, identifiers without whitespace/control characters, lowercase SHA-256 hex,
normalized relative POSIX paths, no binary floats.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Annotated, Final

from pydantic import AfterValidator, BeforeValidator, Field, StringConstraints

INT64_MAX: Final = 2**63 - 1

Int64 = Annotated[int, Field(ge=-INT64_MAX - 1, le=INT64_MAX)]
NonNegativeInt64 = Annotated[int, Field(ge=0, le=INT64_MAX)]
# Integer nanoseconds since the Unix epoch, UTC. Stored as given: no implied clock accuracy.
UtcNanos = NonNegativeInt64

# No whitespace and no control characters (Unicode Cc), so joins cannot differ by padding.
IDENTIFIER_PATTERN: Final = r"^[^\s\x00-\x1f\x7f-\x9f]+$"
Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=256, pattern=IDENTIFIER_PATTERN)
]
SourceIdentifier = Annotated[
    str, StringConstraints(min_length=1, max_length=2048, pattern=IDENTIFIER_PATTERN)
]
# Text kept exactly as supplied (spaces allowed), but never empty and never control chars.
SourceText = Annotated[
    str, StringConstraints(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$")
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

_PATH_FORBIDDEN: Final = frozenset('\\:*?"<>|%$~')


def _reject_binary_float(value: object) -> object:
    # A binary float has already lost the source's decimal precision.
    if isinstance(value, float):
        raise ValueError("decimal fields accept Decimal or a decimal string, never a float")
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            raise ValueError("not a decimal string") from None
    return value


def _require_finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    return value


ExactDecimal = Annotated[
    Decimal, BeforeValidator(_reject_binary_float), AfterValidator(_require_finite)
]


def _relative_posix_path(value: str) -> str:
    parts = value.split("/")
    if (
        not value
        or len(value) > 1024
        or value.startswith("/")
        or any(part in ("", ".", "..") for part in parts)
        or not set(value).isdisjoint(_PATH_FORBIDDEN)
        or any(char.isspace() or not char.isprintable() for char in value)
        or PurePosixPath(value).is_absolute()
    ):
        raise ValueError("must be a normalized relative POSIX path inside the project")
    return value


RelativePosixPath = Annotated[str, AfterValidator(_relative_posix_path)]

# Explicit contract identity shared by trade_v1 and the acquisition manifest:
# ``<VENUE>:<ROOT>:<YYYY-MM-DD expiry>``. A real calendar date is mandatory, so continuous or
# ambiguous identifiers (``GC.c.0``, ``...:CONT``, ``...:Z6``) cannot be expressed.
_CANONICAL_CONTRACT: Final = re.compile(
    r"^(?P<venue>[A-Z]+):(?P<root>[A-Z0-9]+):(?P<expiry>\d{4}-\d{2}-\d{2})$"
)


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    venue: str
    root: str
    expiry: date


def parse_canonical_contract(value: str) -> ContractIdentity:
    """Split a canonical contract identifier or raise ``ValueError``."""
    match = _CANONICAL_CONTRACT.fullmatch(value)
    if match is None:
        raise ValueError("canonical_contract must be <VENUE>:<ROOT>:<YYYY-MM-DD expiry>")
    try:
        expiry = date.fromisoformat(match["expiry"])
    except ValueError:
        raise ValueError("canonical_contract expiry is not a calendar date") from None
    return ContractIdentity(match["venue"], match["root"], expiry)


def _canonical_contract(value: str) -> str:
    parse_canonical_contract(value)
    return value


CanonicalContract = Annotated[str, AfterValidator(_canonical_contract)]


def sha256_hex(payload: bytes) -> str:
    """Lowercase hex SHA-256 of ``payload``."""
    return hashlib.sha256(payload).hexdigest()
