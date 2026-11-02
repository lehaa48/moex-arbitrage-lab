"""Fail-closed validation of downloaded batch files before any raw publication.

Raw bytes are never modified; validation only reads them. A file is rejected (nothing is
published) when it is empty, HTML, not UTF-8, has an unexpected suffix, a CSV header without
the required columns, a ragged row, a non-integer ``ts_event`` (``pretty_ts=false`` keeps
integer nanoseconds), a ``ts_event`` outside the frozen interval, an ``instrument_id`` other than
the one resolved in Step A, a ``symbol`` other than ``GCZ6`` or a non-decimal price/volume.

Descriptive QA (rtype/publisher sets, second alignment, ties, non-monotonic steps, OHLC
consistency) is measured and reported, never used to reject: interpretation belongs to the
seconds-series workstream.

``ohlcv-1s`` semantics (official schema docs, carried into every report): a bar exists only for
seconds with at least one trade; ``ts_event`` is the start of ``[t, t+1s)``; bars are built from
the ``ts_recv`` of the source trade messages, i.e. provider observation time, not matching-engine
event time. ``trade_count`` is not part of the schema.
"""

import csv
import io
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

from moex_arbitrage_lab.settings.storage_paths import StoragePathError, validate_relative_path
from moex_arbitrage_lab.sources.databento_history.client import looks_like_html
from moex_arbitrage_lab.sources.databento_history.request import (
    END_UTC_NS,
    FROZEN_REQUEST,
    START_UTC_NS,
)

# VERIFY against https://databento.com/docs/schemas-and-data-formats/ohlcv before Step A:
# CSV columns of ohlcv-1s (``symbol`` is added by map_symbols=true).
REQUIRED_CSV_COLUMNS: Final = (
    "ts_event",
    "rtype",
    "publisher_id",
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "symbol",
)
CSV_SUFFIX: Final = ".csv"
AUX_SUFFIX: Final = ".json"
ALLOWED_SUFFIXES: Final = (CSV_SUFFIX, AUX_SUFFIX)
# Storage path grammar of one segment (settings/storage_paths.py), stricter max length.
PROVIDER_FILENAME: Final = re.compile(r"^[a-z0-9][a-z0-9._=-]{0,95}$")
OHLCV_1S_SEMANTICS: Final = (
    "ohlcv-1s: a bar exists only for seconds with at least one trade; ts_event is the start of "
    "[t, t+1s); bars are built from ts_recv of source trade messages (provider observation "
    "time, not matching-engine event time); trade_count is not provided"
)
_NANOS_PER_SECOND: Final = 1_000_000_000
_DIGITS: Final = re.compile(r"^[0-9]{1,19}$")


class PayloadRejectedError(ValueError):
    """A downloaded file failed validation; it is never published."""

    def __init__(self, filename: str, reason: str) -> None:
        self.filename = filename
        self.reason = reason
        super().__init__(f"{filename}: {reason}")


@dataclass(frozen=True, slots=True)
class CsvSummary:
    header: tuple[str, ...]
    rows: int
    ts_event_min: int
    ts_event_max: int
    instrument_ids: tuple[int, ...]
    symbols: tuple[str, ...]
    rtypes: tuple[str, ...]
    publisher_ids: tuple[str, ...]
    not_second_aligned: int
    ts_event_ties: int
    ts_event_decreasing_steps: int
    ohlc_inconsistent: int
    zero_volume: int

    def as_metadata(self) -> dict[str, str]:
        """Short text facts for the manifest ``response_metadata`` (<= 256 chars each)."""
        return {
            "csv_header": ",".join(self.header)[:256],
            "csv_rows": str(self.rows),
            "ts_event_min": str(self.ts_event_min),
            "ts_event_max": str(self.ts_event_max),
            "instrument_ids": ",".join(str(i) for i in self.instrument_ids)[:256],
            "symbols": ",".join(self.symbols)[:256],
            "rtypes": ",".join(self.rtypes)[:256],
            "publisher_ids": ",".join(self.publisher_ids)[:256],
            "not_second_aligned": str(self.not_second_aligned),
            "ts_event_ties": str(self.ts_event_ties),
            "ts_event_decreasing_steps": str(self.ts_event_decreasing_steps),
            "ohlc_inconsistent": str(self.ohlc_inconsistent),
            "zero_volume": str(self.zero_volume),
        }


@dataclass(frozen=True, slots=True)
class FileValidation:
    filename: str
    kind: str  # "csv" or "aux_json"
    csv: CsvSummary | None = None
    metadata: dict[str, str] = field(default_factory=dict)


def check_provider_filename(filename: str) -> str:
    if not PROVIDER_FILENAME.fullmatch(filename) or filename.endswith("."):
        raise PayloadRejectedError("<unrepresentable>", "provider file name is not representable")
    try:
        validate_relative_path(filename)
    except StoragePathError:
        raise PayloadRejectedError(
            filename, "provider file name is refused by the storage guard"
        ) from None
    if not filename.endswith(ALLOWED_SUFFIXES):
        raise PayloadRejectedError(filename, "unexpected file suffix (compression must be none)")
    return filename


def _decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(value) from None
    if not number.is_finite():
        raise ValueError(value)
    return number


def validate_csv(filename: str, data: bytes, instrument_ids: frozenset[int]) -> CsvSummary:
    """One linear pass; any structural/identity problem rejects the whole file."""
    if not data:
        raise PayloadRejectedError(filename, "empty file")
    if looks_like_html(data):
        raise PayloadRejectedError(filename, "HTML instead of CSV")
    if data.startswith(b"\xef\xbb\xbf"):
        raise PayloadRejectedError(filename, "unexpected byte-order mark")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PayloadRejectedError(filename, "not UTF-8") from None
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = tuple(next(reader))
    except StopIteration:
        raise PayloadRejectedError(filename, "no header") from None
    except csv.Error:
        raise PayloadRejectedError(filename, "malformed header") from None
    missing = [c for c in REQUIRED_CSV_COLUMNS if c not in header]
    if missing or len(set(header)) != len(header) or not header or header[0] != "ts_event":
        raise PayloadRejectedError(filename, f"unexpected CSV header (missing {missing})")
    index = {name: position for position, name in enumerate(header)}

    rows = 0
    ts_min = ts_max = -1
    previous: int | None = None
    seen_ids: set[int] = set()
    seen_symbols: set[str] = set()
    rtypes: set[str] = set()
    publishers: set[str] = set()
    not_aligned = ties = decreasing = inconsistent = zero_volume = 0
    try:
        for line_number, row in enumerate(reader, start=2):
            if not row:
                raise PayloadRejectedError(filename, f"empty line {line_number}")
            if len(row) != len(header):
                raise PayloadRejectedError(filename, f"ragged row at line {line_number}")
            ts_text = row[index["ts_event"]]
            if not _DIGITS.fullmatch(ts_text):
                raise PayloadRejectedError(filename, f"non-integer ts_event at line {line_number}")
            ts = int(ts_text)
            if not START_UTC_NS <= ts < END_UTC_NS:
                raise PayloadRejectedError(
                    filename, f"ts_event outside interval, line {line_number}"
                )
            id_text = row[index["instrument_id"]]
            if not _DIGITS.fullmatch(id_text) or int(id_text) not in instrument_ids:
                raise PayloadRejectedError(
                    filename, f"unexpected instrument_id, line {line_number}"
                )
            symbol = row[index["symbol"]]
            if symbol != FROZEN_REQUEST.symbols:
                raise PayloadRejectedError(filename, f"unexpected symbol at line {line_number}")
            try:
                o, h, low, c = (_decimal(row[index[k]]) for k in ("open", "high", "low", "close"))
                volume = _decimal(row[index["volume"]])
            except ValueError:
                raise PayloadRejectedError(
                    filename, f"non-decimal price/volume at line {line_number}"
                ) from None
            if volume < 0 or volume != volume.to_integral_value():
                raise PayloadRejectedError(filename, f"invalid volume at line {line_number}")
            rows += 1
            seen_ids.add(int(id_text))
            seen_symbols.add(symbol)
            rtypes.add(row[index["rtype"]])
            publishers.add(row[index["publisher_id"]])
            ts_min = ts if ts_min < 0 else min(ts_min, ts)
            ts_max = max(ts_max, ts)
            not_aligned += ts % _NANOS_PER_SECOND != 0
            if previous is not None:
                ties += ts == previous
                decreasing += ts < previous
            previous = ts
            inconsistent += not (low <= min(o, c) and max(o, c) <= h and low <= h)
            zero_volume += volume == 0
    except csv.Error:
        raise PayloadRejectedError(filename, "malformed CSV") from None
    if rows == 0:
        raise PayloadRejectedError(filename, "no data rows")
    return CsvSummary(
        header=header,
        rows=rows,
        ts_event_min=ts_min,
        ts_event_max=ts_max,
        instrument_ids=tuple(sorted(seen_ids)),
        symbols=tuple(sorted(seen_symbols)),
        rtypes=tuple(sorted(rtypes)),
        publisher_ids=tuple(sorted(publishers)),
        not_second_aligned=not_aligned,
        ts_event_ties=ties,
        ts_event_decreasing_steps=decreasing,
        ohlc_inconsistent=inconsistent,
        zero_volume=zero_volume,
    )


def validate_aux_json(filename: str, data: bytes) -> dict[str, str]:
    if not data:
        raise PayloadRejectedError(filename, "empty file")
    if looks_like_html(data):
        raise PayloadRejectedError(filename, "HTML instead of JSON")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PayloadRejectedError(filename, "invalid JSON") from None
    if not isinstance(document, dict | list):
        raise PayloadRejectedError(filename, "JSON is neither an object nor an array")
    return {"aux_json_type": type(document).__name__}


def validate_file(filename: str, data: bytes, instrument_ids: frozenset[int]) -> FileValidation:
    check_provider_filename(filename)
    if filename.endswith(CSV_SUFFIX):
        summary = validate_csv(filename, data, instrument_ids)
        return FileValidation(filename, "csv", summary, summary.as_metadata())
    return FileValidation(filename, "aux_json", None, validate_aux_json(filename, data))
