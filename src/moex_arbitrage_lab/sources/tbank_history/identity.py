"""Fail-closed instrument identity of one per-instrument daily file and of a whole period.

The archive is requested per instrument (``instrumentId=<TICKER>_<CLASS_CODE>``), so:

- every parsed CSV row must carry ``TICKER_CC == config.ticker_cc``; a single row with any
  other ``TICKER_CC`` rejects the whole file (``other_ticker_rows``);
- the target rows of a file must carry exactly one ``INSTRUMENT_UID`` value, and it must be a
  canonical UUID (8-4-4-4-12 hex); an empty value, a second value or a non-UUID value rejects
  the file;
- across a period, every accepted file must carry the same UID (``uid_changed_across_period``).

The UID is only *observed from the archive*; it is not independently verified against an
instrument directory (that needs an authenticated API). The status string says so. The ticker +
class code is the archive's request identifier, not a claim of global uniqueness.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig
from moex_arbitrage_lab.sources.tbank_history.parser import ParseResult

UID_STATUS: Final = "OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED"
IDENTITY_REJECTION_REASONS: Final = (
    "other_ticker_rows",
    "no_target_rows",
    "uid_missing",
    "uid_not_uuid",
    "uid_multiple",
    "uid_config_mismatch",
    "uid_changed_across_period",
)
_UUID: Final = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", re.ASCII
)


class IdentityRejectedError(ValueError):
    """The file (or period) fails the identity rules; ``reason`` is stable, ``counts`` are
    non-secret row counts for the report."""

    def __init__(self, reason: str, counts: dict[str, int] | None = None) -> None:
        if reason not in IDENTITY_REJECTION_REASONS:
            raise ValueError(f"unknown identity rejection reason: {reason!r}")
        self.reason = reason
        self.counts = dict(counts or {})
        super().__init__(f"{reason}: {self.counts}" if self.counts else reason)


@dataclass(frozen=True, slots=True)
class FileIdentity:
    ticker_cc: str
    instrument_uid: str
    target_rows: int
    other_ticker_rows: int
    uid_status: str = UID_STATUS

    def to_json(self) -> dict[str, object]:
        return {
            "ticker_cc": self.ticker_cc,
            "instrument_uid": self.instrument_uid,
            "uid_status": self.uid_status,
            "target_rows": self.target_rows,
            "other_ticker_rows": self.other_ticker_rows,
            "rule": "every row TICKER_CC == ticker_cc; exactly one canonical-UUID INSTRUMENT_UID",
        }


def is_uuid(value: str) -> bool:
    return _UUID.fullmatch(value) is not None


def check_file_identity(result: ParseResult, config: TbankHistoryConfig) -> FileIdentity:
    """Return the file's identity or raise ``IdentityRejectedError``."""
    other = result.reconciliation.parse_failures.get("other_instrument", 0)
    target = result.reconciliation.target_rows
    counts = {"target_rows": target, "other_ticker_rows": other}
    if other:
        raise IdentityRejectedError("other_ticker_rows", counts)
    if target == 0:
        raise IdentityRejectedError("no_target_rows", counts)
    uids = result.instrument_uids
    if sum(uids.values()) != target:
        raise ValueError("identity: UID counts do not reconcile with target rows")
    if uids.get("", 0):
        raise IdentityRejectedError("uid_missing", {**counts, "empty_uid_rows": uids[""]})
    if len(uids) != 1:
        raise IdentityRejectedError("uid_multiple", {**counts, "distinct_uids": len(uids)})
    (uid,) = uids
    if not is_uuid(uid):
        raise IdentityRejectedError("uid_not_uuid", counts)
    if config.instrument_uid is not None and uid != config.instrument_uid:
        raise IdentityRejectedError("uid_config_mismatch", counts)
    # ``instrument_uid_mismatch`` parse failures imply a second UID or a config mismatch, both
    # refused above, so every target row of an accepted file carries ``uid``.
    return FileIdentity(
        ticker_cc=config.ticker_cc,
        instrument_uid=uid,
        target_rows=target,
        other_ticker_rows=0,
    )


def check_period_identity(uids: Iterable[str]) -> str | None:
    """The single UID shared by all accepted files (``None`` if there is none yet); raise
    ``IdentityRejectedError("uid_changed_across_period")`` if they differ."""
    distinct = sorted(set(uids))
    if len(distinct) > 1:
        raise IdentityRejectedError("uid_changed_across_period", {"distinct_uids": len(distinct)})
    return distinct[0] if distinct else None
