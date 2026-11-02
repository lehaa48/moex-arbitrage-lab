"""Deterministic CSV -> ``trade_v1`` parser for one T-Bank daily file.

Rules (P1 task, ADR-0003):

- the header must equal ``config.expected_columns`` exactly, otherwise the whole file is
  rejected (``ValueError``);
- every physical line after the header is one CSV data row (``source_row_no`` = physical line
  number, header = 1; ``source_record_id = "row-<n>"``); a record cannot span lines, so an
  embedded newline makes both halves ``malformed_row``;
- every row ends in exactly one bucket: a parse-failure reason (first failing reason wins, in
  ``PARSE_FAILURE_REASONS`` order) or a parsed trade; parsed trades are eligible only with
  ``TRADE_SOURCE`` exactly ``EXCHANGE``. Rows of other instruments are counted, never converted;
- ``TRADE_TS`` is the provider-documented UTC trade time, parsed with exact integer arithmetic;
  ``trading_date``/``session_id`` come only from the verified session windows of the config;
- ``DIRECTION`` semantics are unverified: ``aggressor_side`` is always ``None``;
- a row whose values cannot form a valid ``trade_v1`` record (e.g. whitespace inside an
  identifier) is counted as ``malformed_row`` rather than silently dropped.
"""

import bisect
import csv
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from typing import Final

from pydantic import ValidationError

from moex_arbitrage_lab.contracts.trade_v1 import TradeAction, TradeV1, trade_to_json_bytes
from moex_arbitrage_lab.contracts.types import INT64_MAX
from moex_arbitrage_lab.sources.tbank_history.config import SessionWindow, TbankHistoryConfig

PARSER_VERSION: Final = "tbank_history_parser_v1"
SOURCE_NAME: Final = "tbank_history"
VENUE: Final = "MOEX"
ELIGIBLE_TRADE_SOURCE: Final = "EXCHANGE"

PARSE_FAILURE_REASONS: Final = (
    "malformed_row",
    "other_instrument",
    "instrument_uid_mismatch",
    "null_timestamp",
    "unparseable_timestamp",
    "unparseable_price",
    "unparseable_quantity",
    "outside_verified_session",
)
INELIGIBILITY_REASONS: Final = ("trade_source_not_exchange",)

_TRADE_TS: Final = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<frac>\d{1,9}))?(?:Z|\+00:00)?",
    re.ASCII,
)
_DECIMAL: Final = re.compile(r"-?\d+(?:\.\d+)?", re.ASCII)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_SURROGATE: Final = re.compile(r"[\udc80-\udcff]")
_UTF8_BOM: Final = "﻿"


class CsvSchemaError(ValueError):
    """The CSV has no header or its header differs from ``config.expected_columns``; the
    whole file is rejected."""


def parse_trade_ts(text: str) -> tuple[int, int]:
    """``(utc_ns, fractional_digits)`` of ``YYYY-MM-DD[ T]HH:MM:SS[.f{1..9}][Z|+00:00]``.

    The value is UTC by provider documentation; no float is involved. Anything else, an
    impossible calendar time or a value outside ``[0, INT64_MAX]`` raises ``ValueError``."""
    match = _TRADE_TS.fullmatch(text)
    if match is None:
        raise ValueError("timestamp does not match the accepted UTC formats")
    day = date.fromisoformat(match["date"])
    moment = datetime(
        day.year, day.month, day.day, int(match["h"]), int(match["m"]), int(match["s"]), tzinfo=UTC
    )
    delta = moment - _EPOCH
    seconds = delta.days * 86_400 + delta.seconds
    fraction = match["frac"] or ""
    utc_ns = seconds * 1_000_000_000 + (int(fraction.ljust(9, "0")) if fraction else 0)
    if not 0 <= utc_ns <= INT64_MAX:
        raise ValueError("timestamp outside the int64 UTC nanosecond range")
    return utc_ns, len(fraction)


def _parse_decimal(text: str) -> Decimal:
    if _DECIMAL.fullmatch(text) is None:
        raise ValueError("not a plain decimal")
    return Decimal(text)


def _price_ticks(price: Decimal, tick_size: Decimal | None) -> int | None:
    if tick_size is None:
        return None
    with localcontext() as context:
        context.prec = 200
        context.traps[Inexact] = True
        try:
            quotient = price / tick_size
        except (Inexact, InvalidOperation):
            return None
    if quotient != quotient.to_integral_value():
        return None
    ticks = int(quotient)
    return ticks if -INT64_MAX - 1 <= ticks <= INT64_MAX else None


@dataclass(frozen=True, slots=True)
class Reconciliation:
    header: tuple[str, ...]
    csv_data_rows: int
    parse_failures: dict[str, int]
    parsed_rows: int
    ineligible: dict[str, int]
    eligible_rows: int
    target_rows: int

    def check(self) -> None:
        """Every CSV data row is in exactly one bucket; raise ``ValueError`` otherwise."""
        if self.csv_data_rows != self.parsed_rows + sum(self.parse_failures.values()):
            raise ValueError("reconciliation: csv rows != parsed + parse failures")
        if self.parsed_rows != self.eligible_rows + sum(self.ineligible.values()):
            raise ValueError("reconciliation: parsed rows != eligible + ineligible")
        if set(self.parse_failures) != set(PARSE_FAILURE_REASONS) or set(self.ineligible) != set(
            INELIGIBILITY_REASONS
        ):
            raise ValueError("reconciliation: reason keys differ from the declared reasons")

    def to_json(self) -> dict[str, object]:
        return {
            "header": list(self.header),
            "csv_data_rows": self.csv_data_rows,
            "parse_failures": dict(self.parse_failures),
            "parse_failures_total": sum(self.parse_failures.values()),
            "parsed_rows": self.parsed_rows,
            "ineligible": dict(self.ineligible),
            "ineligible_total": sum(self.ineligible.values()),
            "eligible_rows": self.eligible_rows,
            "target_rows": self.target_rows,
            "eligibility_rule": f"TRADE_SOURCE == {ELIGIBLE_TRADE_SOURCE!r}",
        }


@dataclass(frozen=True, slots=True)
class ParseResult:
    trades: tuple[TradeV1, ...]
    eligible: tuple[TradeV1, ...]
    reconciliation: Reconciliation
    composition: dict[str, dict[str, int]]
    fractional_digits: dict[int, int]
    # Raw ``INSTRUMENT_UID`` values of all target rows (``""`` = empty/absent) with counts;
    # input to the fail-closed identity check (``identity.check_file_identity``).
    instrument_uids: dict[str, int] = field(default_factory=dict)


def _split_record(line: str, delimiter: str) -> list[str] | None:
    """Fields of one physical line, or ``None`` if it is not exactly one CSV record."""
    try:
        records = list(csv.reader([line], delimiter=delimiter, strict=True))
    except csv.Error:
        return None
    return records[0] if len(records) == 1 else None


def _physical_lines(csv_bytes: bytes) -> list[str]:
    # surrogateescape keeps undecodable bytes visible (as lone surrogates) so that only the
    # affected rows are counted as malformed; the header must still match exactly.
    text = csv_bytes.decode("utf-8", errors="surrogateescape").removeprefix(_UTF8_BOM)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def _session_index(sessions: tuple[SessionWindow, ...]) -> list[int]:
    return [window.start_utc_ns for window in sessions]


def _find_session(
    sessions: tuple[SessionWindow, ...], starts: list[int], ts: int
) -> SessionWindow | None:
    position = bisect.bisect_right(starts, ts) - 1
    if position < 0:
        return None
    window = sessions[position]
    return window if window.start_utc_ns <= ts < window.end_utc_ns else None


def parse_trades(
    csv_bytes: bytes,
    *,
    config: TbankHistoryConfig,
    source_file: str,
    raw_payload_hash: str,
    local_ingest_ts_utc_ns: int,
) -> ParseResult:
    """Parse one daily CSV deterministically (same input -> identical output)."""
    lines = _physical_lines(csv_bytes)
    if not lines:
        raise CsvSchemaError("CSV has no header line")
    header = _split_record(lines[0], config.csv_delimiter)
    if header is None or tuple(header) != config.expected_columns:
        raise CsvSchemaError("CSV header differs from config.expected_columns; file rejected")
    columns = {name: index for index, name in enumerate(config.expected_columns)}
    width = len(config.expected_columns)
    uid_column = columns.get("INSTRUMENT_UID")
    direction_column = columns.get("DIRECTION")
    starts = _session_index(config.sessions)

    failures: Counter[str] = Counter()
    trades: list[TradeV1] = []
    trade_sources: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    fractional: Counter[int] = Counter()
    uids: Counter[str] = Counter()
    target_rows = 0

    for row_no, line in enumerate(lines[1:], start=2):
        fields = _split_record(line, config.csv_delimiter)
        if fields is None or len(fields) != width or any(_SURROGATE.search(f) for f in fields):
            failures["malformed_row"] += 1
            continue
        ticker = fields[columns["TICKER_CC"]]
        if ticker != config.ticker_cc:
            failures["other_instrument"] += 1
            continue
        target_rows += 1
        trade_sources[fields[columns["TRADE_SOURCE"]]] += 1
        if direction_column is not None:
            directions[fields[direction_column]] += 1
        uid = fields[uid_column] if uid_column is not None else ""
        uids[uid] += 1
        if uid and config.instrument_uid is not None and uid != config.instrument_uid:
            failures["instrument_uid_mismatch"] += 1
            continue
        ts_text = fields[columns["TRADE_TS"]]
        if ts_text == "":
            failures["null_timestamp"] += 1
            continue
        try:
            event_ns, digits = parse_trade_ts(ts_text)
        except ValueError:
            failures["unparseable_timestamp"] += 1
            continue
        try:
            price = _parse_decimal(fields[columns["PRICE"]])
        except ValueError:
            failures["unparseable_price"] += 1
            continue
        try:
            size = _parse_decimal(fields[columns["QUANTITY"]])
        except ValueError:
            failures["unparseable_quantity"] += 1
            continue
        window = _find_session(config.sessions, starts, event_ns)
        if window is None:
            failures["outside_verified_session"] += 1
            continue
        trade_source = fields[columns["TRADE_SOURCE"]]
        try:
            trade = TradeV1.model_validate(
                {
                    "source": SOURCE_NAME,
                    "venue": VENUE,
                    "dataset": config.dataset,
                    "instrument_id": uid or config.instrument_uid or config.ticker_cc,
                    "raw_symbol": ticker,
                    "canonical_contract": config.canonical_contract,
                    "asset": "GOLD",
                    "expiry": config.expiry,
                    "currency": config.currency,
                    "price_unit": config.price_unit,
                    "contract_multiplier": config.contract_multiplier,
                    "event_ts_utc_ns": event_ns,
                    "event_ts_type": config.event_ts_type,
                    "receive_ts_utc_ns": None,
                    "capture_ts_utc_ns": None,
                    "local_ingest_ts_utc_ns": local_ingest_ts_utc_ns,
                    "price": price,
                    "price_ticks": _price_ticks(price, config.tick_size),
                    "size": size,
                    # DIRECTION semantics are not proven: never an aggressor side.
                    "aggressor_side": None,
                    "sequence": None,
                    "action": TradeAction(config.trade_action),
                    "flags": None,
                    "trade_source": trade_source or None,
                    "session_id": window.name,
                    "trading_date": window.trading_date,
                    "source_file": source_file,
                    "source_record_id": f"row-{row_no}",
                    "source_row_no": row_no,
                    "schema_version": "trade_v1",
                    "raw_payload_hash": raw_payload_hash,
                }
            )
        except ValidationError:
            failures["malformed_row"] += 1
            continue
        trades.append(trade)
        fractional[digits] += 1

    eligible = tuple(t for t in trades if t.trade_source == ELIGIBLE_TRADE_SOURCE)
    reconciliation = Reconciliation(
        header=tuple(header),
        csv_data_rows=len(lines) - 1,
        parse_failures={reason: failures[reason] for reason in PARSE_FAILURE_REASONS},
        parsed_rows=len(trades),
        ineligible={"trade_source_not_exchange": len(trades) - len(eligible)},
        eligible_rows=len(eligible),
        target_rows=target_rows,
    )
    composition = {
        "TRADE_SOURCE": dict(sorted(trade_sources.items())),
        "DIRECTION": dict(sorted(directions.items())),
    }
    return ParseResult(
        trades=tuple(trades),
        eligible=eligible,
        reconciliation=reconciliation,
        composition=composition,
        fractional_digits=dict(sorted(fractional.items())),
        instrument_uids=dict(sorted(uids.items())),
    )


def parse_digest(result: ParseResult) -> str:
    """SHA-256 of the canonical JSON lines of all parsed trades (determinism evidence)."""
    return hashlib.sha256(
        b"".join(trade_to_json_bytes(t) + b"\n" for t in result.trades)
    ).hexdigest()
