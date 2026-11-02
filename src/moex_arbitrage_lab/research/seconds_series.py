"""Deterministic causal one-second series per venue (H-0001 v0.2).

Time semantics (both venues):

- second ``t`` (integer UTC epoch seconds) is the half-open interval ``[t, t + 1 s)``;
- a bar's OHLC/close is available only at the END of its second, ``t + 1 s``: the causal state
  "as of" an instant ``b`` uses only seconds whose end is ``<= b`` (``state_as_of``);
- an empty second has no OHLC (null), volume 0 and ``refreshed = false``; the last known close
  (``state_close``) may be carried forward ONLY inside the same continuous segment. It is never
  carried across a segment, break, day or provider gap, and nothing is ever filled from a later
  observation;
- ``age_ns`` (``age_ms`` in CSV) = end of second ``t`` minus the time of the observation that
  defines the state; it is null while the segment has no observation yet.

MOEX (``build_moex_day``): built from eligible trades ``(event_ts_utc_ns, price, size)`` in input
sequence. Within a second, trades are sorted stably by timestamp (equal timestamps keep their
input sequence), so open = first trade, close = last trade by ``(timestamp, input sequence)``.
The state observation time is the exact raw nanosecond timestamp of the last trade
(``state_obs_ts_utc_ns``); full raw precision is kept there and in ``first_obs_ts_utc_ns``.
Trades outside every continuous segment (for example the opening auction) are excluded and
counted.

COMEX (``read_comex_ohlcv_csv``/``build_comex_series``): Databento GLBX.MDP3 ``ohlcv-1s`` batch
CSV (encoding=csv, compression=none, pretty_px=true, pretty_ts=false, map_symbols=true). A bar
exists only for a second with at least one trade; ``ts_event`` is the integer-nanosecond START
of ``[t, t + 1 s)``. Databento builds these bars from the ``ts_recv`` of the source trade
messages, so the COMEX series reflects the provider's observation (receive) time, not pure
matching-engine event time; this is a limitation of every COMEX/MOEX comparison built here. The
time of the last trade inside a bar is unknown, so the COMEX state observation time is the bar
START and ``age`` is an upper bound (a refreshed second has age exactly 1000 ms).
``trade_count`` is not provided by ``ohlcv-1s`` and stays null; it is never invented.

Common segments are the intersections of MOEX continuous segments with COMEX Globex open
intervals (``comex_open_intervals``), clipped to the period. A return never spans a common
segment boundary (``seconds_analysis``).
"""

import bisect
import itertools
import math
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final, Literal, NamedTuple

from moex_arbitrage_lab.research.seconds_config import (
    NS_PER_SECOND,
    WEEKDAYS,
    ComexConfig,
    PeriodConfig,
    date_start_ns,
)

NS_PER_MS: Final = 1_000_000
SECONDS_CSV_HEADER: Final = (
    "second_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "age_ms",
    "refreshed",
    "session_id",
    "trading_date",
    # Extended, after the minimal schema: causal carried close and the full-precision raw
    # timestamp of the observation that defines it.
    "state_close",
    "state_obs_ts_utc_ns",
    "first_obs_ts_utc_ns",
)
MOEX_TRADES_CSV_HEADER: Final = ("event_ts_utc_ns", "price", "size")
SESSION_NAME: Final = re.compile(r"^[a-z0-9][a-z0-9_.@-]{0,95}$")
_UINT: Final = re.compile(r"^(0|[1-9][0-9]*)$")
_DECIMAL: Final = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")

Venue = Literal["moex", "comex"]
_ZERO: Final = Decimal(0)


class SeriesError(ValueError):
    """Input or construction violates the seconds-series rules."""


class CsvFormatError(SeriesError):
    """A CSV file does not match its frozen format exactly."""


class ComexCsvError(CsvFormatError):
    """The Databento CSV does not match the frozen format exactly."""


class ReconciliationError(SeriesError):
    """Raw -> seconds counts do not reconcile; the output must not be used."""


@dataclass(frozen=True, slots=True)
class Segment:
    """A continuous half-open session interval ``[start_ns, end_ns)`` on whole seconds."""

    name: str
    start_ns: int
    end_ns: int
    trading_date: date

    def __post_init__(self) -> None:
        if not SESSION_NAME.fullmatch(self.name):
            raise SeriesError(f"segment name must match {SESSION_NAME.pattern}: {self.name!r}")
        for value in (self.start_ns, self.end_ns):
            if type(value) is not int or value < 0 or value % NS_PER_SECOND:
                raise SeriesError("segment bounds must be non-negative whole-second UTC ns")
        if self.start_ns >= self.end_ns:
            raise SeriesError(f"segment {self.name} is empty or reversed")

    @property
    def start_s(self) -> int:
        return self.start_ns // NS_PER_SECOND

    @property
    def end_s(self) -> int:
        return self.end_ns // NS_PER_SECOND

    @property
    def seconds(self) -> int:
        return self.end_s - self.start_s


@dataclass(frozen=True, slots=True)
class CommonSegment:
    """MOEX continuous segment intersected with one COMEX open interval (and the period)."""

    name: str
    start_s: int
    end_s: int
    trading_date: date
    moex_session: str
    comex_session: str

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "start_utc_ns": self.start_s * NS_PER_SECOND,
            "end_utc_ns": self.end_s * NS_PER_SECOND,
            "trading_date": self.trading_date.isoformat(),
            "moex_session": self.moex_session,
            "comex_session": self.comex_session,
        }


class SecondRecord(NamedTuple):
    """One second of one venue (immutable; a NamedTuple because millions are built)."""

    second_s: int
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal
    trade_count: int | None
    age_ns: int | None
    refreshed: bool
    session_id: str
    trading_date: date
    state_close: Decimal | None
    state_obs_ts_ns: int | None
    first_obs_ts_ns: int | None


@dataclass(frozen=True, slots=True)
class TimestampDiagnostics:
    """Timestamp facts of the eligible in-segment observations of one series."""

    observations: int
    distinct_timestamps: int
    # gcd of differences between distinct timestamps (None if fewer than two distinct values).
    quantum_ns: int | None
    # gcd of (timestamp mod 1 s) and 1 s: the finest sub-second grid actually observed.
    subsecond_quantum_ns: int | None
    tie_observations: int
    exact_duplicates: int
    input_sequence_inversions: int
    max_gap_ns: int | None
    inter_observation_gap_ns: dict[str, int]
    n_gaps: int

    def as_json(self) -> dict[str, object]:
        return {
            "observations": self.observations,
            "distinct_timestamps": self.distinct_timestamps,
            "quantum_ns": self.quantum_ns,
            "subsecond_quantum_ns": self.subsecond_quantum_ns,
            "tie_observations": self.tie_observations,
            "exact_duplicates": self.exact_duplicates,
            "input_sequence_inversions": self.input_sequence_inversions,
            "max_gap_ns": self.max_gap_ns,
            "inter_observation_gap_ns": dict(self.inter_observation_gap_ns),
            "n_gaps": self.n_gaps,
        }


@dataclass(frozen=True, slots=True)
class SeriesResult:
    """Dense second records of one venue partition plus counts and reconciliation."""

    venue: Venue
    partition: date
    segments: tuple[Segment, ...]
    records: tuple[SecondRecord, ...]
    input_observations: int
    assigned_observations: int
    excluded: dict[str, int]
    timestamps: TimestampDiagnostics
    reconciliation: dict[str, object] = field(default_factory=dict)

    @property
    def refreshed_seconds(self) -> int:
        return sum(1 for record in self.records if record.refreshed)


# --- MOEX ---------------------------------------------------------------------------------------


def _check_segments(segments: Sequence[Segment], trading_date: date | None) -> tuple[Segment, ...]:
    ranked = tuple(sorted(segments, key=lambda s: s.start_ns))
    names = [segment.name for segment in ranked]
    if len(set(names)) != len(names):
        raise SeriesError("segment names must be unique within a partition")
    for previous, current in itertools.pairwise(ranked):
        if current.start_ns < previous.end_ns:
            raise SeriesError(f"segments overlap: {previous.name} and {current.name}")
    if trading_date is not None and any(s.trading_date != trading_date for s in ranked):
        raise SeriesError("every segment must belong to the partition trading date")
    return ranked


def _timestamp_diagnostics(
    timestamps: Sequence[int],
    sorted_groups: Sequence[Sequence[tuple[int, int, Decimal, Decimal]]],
) -> TimestampDiagnostics:
    """``timestamps`` in input sequence; ``sorted_groups`` sorted per segment (gaps and ties
    are measured inside a segment only)."""
    distinct = sorted(set(timestamps))
    quantum: int | None = None
    for left, right in itertools.pairwise(distinct):
        quantum = right - left if quantum is None else math.gcd(quantum, right - left)
    subsecond: int | None = None
    if timestamps:
        subsecond = NS_PER_SECOND
        for value in distinct:
            subsecond = math.gcd(subsecond, value % NS_PER_SECOND)
    ties = 0
    duplicates = 0
    gaps: list[int] = []
    for group in sorted_groups:
        for previous, current in itertools.pairwise(group):
            if current[0] == previous[0]:
                ties += 1
                if current[2] == previous[2] and current[3] == previous[3]:
                    duplicates += 1
            gap = current[0] - previous[0]
            gaps.append(gap)
    inversions = sum(1 for a, b in itertools.pairwise(timestamps) if b < a)
    return TimestampDiagnostics(
        observations=len(timestamps),
        distinct_timestamps=len(distinct),
        quantum_ns=quantum,
        subsecond_quantum_ns=subsecond,
        tie_observations=ties,
        exact_duplicates=duplicates,
        input_sequence_inversions=inversions,
        max_gap_ns=max(gaps, default=None),
        inter_observation_gap_ns=_gap_quantiles(gaps),
        n_gaps=len(gaps),
    )


def _gap_quantiles(gaps: Sequence[int]) -> dict[str, int]:
    """Nearest-rank empirical gap quantiles, deterministic and integer exact."""
    if not gaps:
        return {}
    ranked = sorted(gaps)
    last = len(ranked) - 1
    return {
        label: ranked[round(last * q)]
        for label, q in (("0", 0.0), ("0.5", 0.5), ("0.9", 0.9), ("0.99", 0.99), ("1", 1.0))
    }


def build_moex_day(
    trading_date: date,
    trades: Iterable[tuple[int, Decimal, Decimal]],
    segments: Sequence[Segment],
) -> SeriesResult:
    """Dense causal seconds of one MOEX trading date from eligible trades in input sequence."""
    ranked = _check_segments(segments, trading_date)
    starts = [segment.start_ns for segment in ranked]
    per_segment: list[list[tuple[int, int, Decimal, Decimal]]] = [[] for _ in ranked]
    count = 0
    outside = 0
    for sequence, item in enumerate(trades):
        ts_ns, price, size = item
        if type(ts_ns) is not int or ts_ns < 0:
            raise SeriesError(f"trade {sequence}: event_ts_utc_ns must be a non-negative int")
        for value, name in ((price, "price"), (size, "size")):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise SeriesError(f"trade {sequence}: {name} must be a positive finite Decimal")
        count += 1
        index = bisect.bisect_right(starts, ts_ns) - 1
        if index < 0 or ts_ns >= ranked[index].end_ns:
            outside += 1
            continue
        per_segment[index].append((ts_ns, sequence, price, size))

    records: list[SecondRecord] = []
    in_segment_ts = sorted((obs for group in per_segment for obs in group), key=lambda obs: obs[1])
    for segment, observations in zip(ranked, per_segment, strict=True):
        # Stable: equal timestamps keep their input sequence.
        observations.sort(key=lambda obs: obs[0])
        records.extend(_moex_segment_records(segment, observations))
    timestamps = _timestamp_diagnostics([obs[0] for obs in in_segment_ts], per_segment)
    result = SeriesResult(
        venue="moex",
        partition=trading_date,
        segments=ranked,
        records=tuple(records),
        input_observations=count,
        assigned_observations=count - outside,
        excluded={"outside_continuous_segment": outside},
        timestamps=timestamps,
    )
    volume_in = sum((obs[3] for group in per_segment for obs in group), Decimal(0))
    return _reconciled(result, volume_in)


def _moex_segment_records(
    segment: Segment, observations: Sequence[tuple[int, int, Decimal, Decimal]]
) -> list[SecondRecord]:
    records: list[SecondRecord] = []
    position = 0
    state_close: Decimal | None = None
    state_ts: int | None = None
    total = len(observations)
    for second in range(segment.start_s, segment.end_s):
        end_ns = (second + 1) * NS_PER_SECOND
        first = position
        while position < total and observations[position][0] < end_ns:
            position += 1
        bucket = observations[first:position]
        if bucket:
            prices = [obs[2] for obs in bucket]
            state_close = bucket[-1][2]
            state_ts = bucket[-1][0]
            records.append(
                SecondRecord(
                    second_s=second,
                    open=bucket[0][2],
                    high=max(prices),
                    low=min(prices),
                    close=state_close,
                    volume=sum((obs[3] for obs in bucket), Decimal(0)),
                    trade_count=len(bucket),
                    age_ns=end_ns - state_ts,
                    refreshed=True,
                    session_id=segment.name,
                    trading_date=segment.trading_date,
                    state_close=state_close,
                    state_obs_ts_ns=state_ts,
                    first_obs_ts_ns=bucket[0][0],
                )
            )
        else:
            records.append(
                SecondRecord(
                    second_s=second,
                    open=None,
                    high=None,
                    low=None,
                    close=None,
                    volume=Decimal(0),
                    trade_count=0,
                    age_ns=None if state_ts is None else end_ns - state_ts,
                    refreshed=False,
                    session_id=segment.name,
                    trading_date=segment.trading_date,
                    state_close=state_close,
                    state_obs_ts_ns=state_ts,
                    first_obs_ts_ns=None,
                )
            )
    if position != total:
        raise ReconciliationError(f"segment {segment.name}: unassigned in-segment trades")
    return records


def _reconciled(result: SeriesResult, volume_in: Decimal) -> SeriesResult:
    """Check raw -> seconds identities exactly; raise on any unresolved mismatch."""
    excluded = sum(result.excluded.values())
    expected_seconds = sum(segment.seconds for segment in result.segments)
    volume_out = sum((record.volume for record in result.records), Decimal(0))
    checks: dict[str, bool] = {
        "input_equals_assigned_plus_excluded": result.input_observations
        == result.assigned_observations + excluded,
        "seconds_equal_segment_lengths": len(result.records) == expected_seconds,
        "volume_conserved": volume_out == volume_in,
        "seconds_strictly_increasing": all(
            b.second_s > a.second_s
            for a, b in zip(result.records, result.records[1:], strict=False)
        ),
    }
    if result.venue == "moex":
        counted = sum(record.trade_count or 0 for record in result.records)
        checks["trade_count_equals_assigned"] = counted == result.assigned_observations
    else:
        checks["refreshed_equals_assigned_bars"] = (
            result.refreshed_seconds == result.assigned_observations
        )
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ReconciliationError(f"{result.venue} {result.partition}: {failed}")
    reconciliation: dict[str, object] = {
        "ok": True,
        "checks": checks,
        "input_observations": result.input_observations,
        "assigned_observations": result.assigned_observations,
        "excluded": dict(sorted(result.excluded.items())),
        "seconds": len(result.records),
        "refreshed_seconds": result.refreshed_seconds,
        "volume": str(volume_out),
    }
    return SeriesResult(
        venue=result.venue,
        partition=result.partition,
        segments=result.segments,
        records=result.records,
        input_observations=result.input_observations,
        assigned_observations=result.assigned_observations,
        excluded=result.excluded,
        timestamps=result.timestamps,
        reconciliation=reconciliation,
    )


def read_moex_trades_csv(data: bytes) -> list[tuple[int, Decimal, Decimal]]:
    """Strict reader of the integration file ``event_ts_utc_ns,price,size`` (input sequence)."""
    rows = _strict_csv_rows(data, MOEX_TRADES_CSV_HEADER, "moex trades")
    trades: list[tuple[int, Decimal, Decimal]] = []
    # Each distinct decimal text is validated and parsed once (prices repeat heavily).
    cache: dict[str, Decimal] = {}
    for number, row in rows:
        ts_text, price_text, size_text = row
        if not _UINT.fullmatch(ts_text):
            raise CsvFormatError(f"moex trades line {number}: event_ts_utc_ns is not an integer")
        price = cache.get(price_text)
        if price is None:
            price = cache[price_text] = _decimal(price_text, number, "price", CsvFormatError)
        size = cache.get(size_text)
        if size is None:
            size = cache[size_text] = _decimal(size_text, number, "size", CsvFormatError)
        trades.append((int(ts_text), price, size))
    return trades


# --- COMEX --------------------------------------------------------------------------------------


class ComexBar(NamedTuple):
    """One Databento ohlcv-1s bar (NamedTuple: millions are held at once)."""

    ts_event_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    instrument_id: int
    publisher_id: int
    source_index: int
    row_no: int


def _strict_csv_rows(
    data: bytes,
    header: Sequence[str],
    label: str,
    error: type[CsvFormatError] = CsvFormatError,
) -> Iterator[tuple[int, list[str]]]:
    """Lazily yield (line number, fields); the format checks run on the first ``next``."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise error(f"{label}: BOM (U+FEFF) refused")
    if b"\r" in data:
        raise error(f"{label}: carriage return refused (LF line endings only)")
    if b'"' in data:
        raise error(f"{label}: quoted fields refused")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise error(f"{label}: not UTF-8") from None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise error(f"{label}: empty file (no header)")
    if lines[0] != ",".join(header):
        raise error(f"{label}: header {lines[0]!r} != expected {','.join(header)!r}")
    width = len(header)
    for number in range(1, len(lines)):
        fields = lines[number].split(",")
        if len(fields) != width:
            raise error(f"{label} line {number + 1}: {len(fields)} fields, expected {width}")
        yield number + 1, fields


def _decimal(
    text: str, number: int, name: str, error: type[CsvFormatError] = ComexCsvError
) -> Decimal:
    if not _DECIMAL.fullmatch(text):
        raise error(f"line {number}: {name} {text!r} is not a plain decimal string")
    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover - excluded by the pattern
        raise error(f"line {number}: {name} is not decimal") from None
    if value <= 0:
        raise error(f"line {number}: {name} must be positive")
    return value


def read_comex_ohlcv_csv(data: bytes, config: ComexConfig, source_index: int = 0) -> list[ComexBar]:
    """Strictly parse one Databento ``ohlcv-1s`` CSV file (frozen header, one symbol)."""
    header = config.expected_csv_header
    rows = _strict_csv_rows(data, header, f"comex file {source_index}", ComexCsvError)
    column = {name: header.index(name) for name in header}
    bars: list[ComexBar] = []
    # Each distinct decimal text is validated and parsed once (prices repeat heavily).
    cache: dict[str, Decimal] = {}
    volumes: dict[str, Decimal] = {}
    previous_ts = -1
    for number, fields in rows:
        ts_text = fields[column["ts_event"]]
        if not _UINT.fullmatch(ts_text):
            raise ComexCsvError(f"line {number}: ts_event must be integer nanoseconds")
        ts_event = int(ts_text)
        if ts_event % NS_PER_SECOND:
            raise ComexCsvError(f"line {number}: ts_event is not a whole-second bar start")
        if ts_event <= previous_ts:
            raise ComexCsvError(f"line {number}: ts_event not strictly increasing")
        previous_ts = ts_event
        for name in ("rtype", "publisher_id", "instrument_id", "volume"):
            if not _UINT.fullmatch(fields[column[name]]):
                raise ComexCsvError(f"line {number}: {name} must be an unsigned integer")
        if int(fields[column["rtype"]]) != config.expected_rtype:
            raise ComexCsvError(f"line {number}: rtype != {config.expected_rtype}")
        if fields[column["symbol"]] != config.raw_symbol:
            raise ComexCsvError(f"line {number}: symbol != {config.raw_symbol}")
        prices = []
        for name in ("open", "high", "low", "close"):
            text = fields[column[name]]
            value = cache.get(text)
            if value is None:
                value = cache[text] = _decimal(text, number, name)
            prices.append(value)
        open_, high, low, close = prices
        if not (low <= min(open_, close) and max(open_, close) <= high):
            raise ComexCsvError(f"line {number}: OHLC inconsistent")
        volume_text = fields[column["volume"]]
        volume = volumes.get(volume_text)
        if volume is None:
            volume = volumes[volume_text] = Decimal(int(volume_text))
        if volume <= 0:
            raise ComexCsvError(f"line {number}: a bar must have positive volume")
        bars.append(
            ComexBar(
                ts_event_ns=ts_event,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                instrument_id=int(fields[column["instrument_id"]]),
                publisher_id=int(fields[column["publisher_id"]]),
                source_index=source_index,
                row_no=number,
            )
        )
    return bars


def combine_comex_bars(files: Sequence[Sequence[ComexBar]]) -> tuple[list[ComexBar], int, int]:
    """Merge bars of all files; one instrument and publisher, no duplicate seconds."""
    bars = sorted((bar for group in files for bar in group), key=lambda bar: bar.ts_event_ns)
    for previous, current in itertools.pairwise(bars):
        if current.ts_event_ns == previous.ts_event_ns:
            raise ComexCsvError(f"duplicate bar for ts_event {current.ts_event_ns}")
    instruments = {bar.instrument_id for bar in bars}
    publishers = {bar.publisher_id for bar in bars}
    if len(instruments) > 1:
        raise ComexCsvError(f"more than one instrument_id: {sorted(instruments)}")
    if len(publishers) > 1:
        raise ComexCsvError(f"more than one publisher_id: {sorted(publishers)}")
    return (
        bars,
        (instruments.pop() if instruments else -1),
        (publishers.pop() if publishers else -1),
    )


def _local_minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def comex_open_intervals(config: ComexConfig, period: PeriodConfig) -> tuple[Segment, ...]:
    """Globex open intervals per CME trade date D: [D-1 open_local, D close_local), in UTC,
    for D on the configured weekdays, clipped to the period. Holiday schedules are NOT modelled
    here; affected dates are excluded from the primary universe by the frozen config."""
    offset_ns = -config.utc_offset_hours * 3600 * NS_PER_SECOND
    open_ns = _local_minutes(config.globex_open_local) * 60 * NS_PER_SECOND + offset_ns
    close_ns = _local_minutes(config.globex_close_local) * 60 * NS_PER_SECOND + offset_ns
    weekdays = {WEEKDAYS.index(name) for name in config.trade_weekdays}
    first = period.start_utc.date() - timedelta(days=1)
    last = period.end_utc.date() + timedelta(days=1)
    intervals: list[Segment] = []
    day = first
    while day <= last:
        if day.weekday() in weekdays:
            start = date_start_ns(day - timedelta(days=1)) + open_ns
            end = date_start_ns(day) + close_ns
            start, end = max(start, period.start_ns), min(end, period.end_ns)
            if start < end:
                intervals.append(Segment(f"globex-{day.isoformat()}", start, end, day))
        day += timedelta(days=1)
    return _check_segments(intervals, None)


def build_comex_series(
    bars: Sequence[ComexBar],
    intervals: Sequence[Segment],
    period: PeriodConfig,
    only: frozenset[str] | None = None,
) -> tuple[list[SeriesResult], dict[str, int], dict[str, int]]:
    """Dense causal seconds per Globex open interval; bars outside every interval or the period
    are excluded and counted. ``only`` restricts which intervals get a seconds series (those
    overlapping a MOEX segment); bars of the others stay counted per interval. Returns
    (series, excluded counts, bars per interval name)."""
    groups, excluded = assign_comex_bars(bars, intervals, period)
    per_name = {segment.name: len(group) for segment, group in groups}
    results = [
        build_comex_interval(segment, group)
        for segment, group in groups
        if only is None or segment.name in only
    ]
    if sum(per_name.values()) + sum(excluded.values()) != len(bars):
        raise ReconciliationError("comex: bars != assigned + excluded")
    return results, excluded, per_name


def assign_comex_bars(
    bars: Iterable[ComexBar], intervals: Sequence[Segment], period: PeriodConfig
) -> tuple[list[tuple[Segment, list[ComexBar]]], dict[str, int]]:
    """Assign bars to Globex open intervals; count bars outside the period or every interval."""
    ranked = _check_segments(intervals, None)
    starts = [segment.start_ns for segment in ranked]
    per_interval: list[list[ComexBar]] = [[] for _ in ranked]
    excluded = {"outside_period": 0, "outside_comex_open_interval": 0}
    for bar in bars:
        if not period.start_ns <= bar.ts_event_ns < period.end_ns:
            excluded["outside_period"] += 1
            continue
        index = bisect.bisect_right(starts, bar.ts_event_ns) - 1
        if index < 0 or bar.ts_event_ns >= ranked[index].end_ns:
            excluded["outside_comex_open_interval"] += 1
            continue
        per_interval[index].append(bar)
    return list(zip(ranked, per_interval, strict=True)), excluded


def build_comex_interval(segment: Segment, group: Sequence[ComexBar]) -> SeriesResult:
    """Dense causal seconds of one Globex open interval from its bars."""
    records = _comex_segment_records(segment, group)
    timestamps = [bar.ts_event_ns for bar in group]
    gaps = [b - a for a, b in itertools.pairwise(timestamps)]
    diagnostics = TimestampDiagnostics(
        observations=len(group),
        distinct_timestamps=len(group),
        quantum_ns=NS_PER_SECOND if group else None,
        subsecond_quantum_ns=NS_PER_SECOND if group else None,
        tie_observations=0,
        exact_duplicates=0,
        input_sequence_inversions=0,
        max_gap_ns=max(gaps, default=None),
        inter_observation_gap_ns=_gap_quantiles(gaps),
        n_gaps=len(gaps),
    )
    result = SeriesResult(
        venue="comex",
        partition=segment.trading_date,
        segments=(segment,),
        records=tuple(records),
        input_observations=len(group),
        assigned_observations=len(group),
        excluded={},
        timestamps=diagnostics,
    )
    return _reconciled(result, sum((bar.volume for bar in group), Decimal(0)))


def _comex_segment_records(segment: Segment, bars: Sequence[ComexBar]) -> list[SecondRecord]:
    by_second = {bar.ts_event_ns // NS_PER_SECOND: bar for bar in bars}
    records: list[SecondRecord] = []
    state_close: Decimal | None = None
    state_ts: int | None = None
    name, trading_date = segment.name, segment.trading_date
    for second in range(segment.start_s, segment.end_s):
        end_ns = (second + 1) * NS_PER_SECOND
        bar = by_second.get(second)
        if bar is not None:
            state_close = bar.close
            state_ts = bar.ts_event_ns  # bar START: the last trade time inside is unknown
            records.append(
                SecondRecord(
                    second_s=second,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    trade_count=None,
                    age_ns=end_ns - state_ts,
                    refreshed=True,
                    session_id=segment.name,
                    trading_date=segment.trading_date,
                    state_close=state_close,
                    state_obs_ts_ns=state_ts,
                    first_obs_ts_ns=None,
                )
            )
        else:
            # Positional construction (hot path): fields in SecondRecord declaration sequence.
            records.append(
                SecondRecord(
                    second,
                    None,
                    None,
                    None,
                    None,
                    _ZERO,
                    None,
                    None if state_ts is None else end_ns - state_ts,
                    False,
                    name,
                    trading_date,
                    state_close,
                    state_ts,
                    None,
                )
            )
    return records


# --- causal access and common segments ----------------------------------------------------------


def state_as_of(records: Sequence[SecondRecord], boundary_ns: int) -> SecondRecord | None:
    """Strictly backward: the last record whose second has ENDED at or before ``boundary_ns``.

    A bar of second ``t`` becomes available at ``(t + 1) * 1e9``; at any earlier instant the
    previous second's state is returned. Never interpolates or looks forward.
    """
    seconds = [record.second_s for record in records]
    # Seconds with (t + 1) * 1e9 <= boundary, i.e. t <= boundary // 1e9 - 1.
    index = bisect.bisect_right(seconds, boundary_ns // NS_PER_SECOND - 1) - 1
    return records[index] if index >= 0 else None


def common_segments(
    moex_segments: Sequence[Segment], comex_intervals: Sequence[Segment], period: PeriodConfig
) -> tuple[CommonSegment, ...]:
    """Intersections of MOEX continuous segments with COMEX open intervals inside the period."""
    period_start = period.start_ns // NS_PER_SECOND
    period_end = period.end_ns // NS_PER_SECOND
    found: list[CommonSegment] = []
    for moex in _check_segments(moex_segments, None):
        for comex in comex_intervals:
            start = max(moex.start_s, comex.start_s, period_start)
            end = min(moex.end_s, comex.end_s, period_end)
            if start < end:
                found.append(
                    CommonSegment(
                        name=f"{moex.name}@{comex.name}",
                        start_s=start,
                        end_s=end,
                        trading_date=moex.trading_date,
                        moex_session=moex.name,
                        comex_session=comex.name,
                    )
                )
    return tuple(sorted(found, key=lambda segment: segment.start_s))


# --- CSV serialization --------------------------------------------------------------------------


def _ms_text(age_ns: int | None) -> str:
    if age_ns is None:
        return ""
    return f"{age_ns // NS_PER_MS}.{age_ns % NS_PER_MS:06d}"


def _opt(value: object) -> str:
    return "" if value is None else str(value)


_TOD_TEXT: Final = tuple(
    f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}" for s in range(86_400)
)


def seconds_to_csv(records: Iterable[SecondRecord]) -> bytes:
    """Deterministic CSV (LF, plain decimals, no quoting: no field can contain a comma)."""
    lines = [",".join(SECONDS_CSV_HEADER)]
    day_text: dict[int, str] = {}
    # Carried states repeat for many seconds: format each distinct state once.
    state_key: tuple[Decimal | None, int | None, str, date] | None = None
    state_text = ""
    for r in records:
        day, rest = divmod(r.second_s, 86_400)
        prefix = day_text.get(day)
        if prefix is None:
            prefix = (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(days=day)).strftime("%Y-%m-%d")
            day_text[day] = prefix
        tod = _TOD_TEXT[rest]
        key = (r.state_close, r.state_obs_ts_ns, r.session_id, r.trading_date)
        if key != state_key:
            state_key = key
            state_text = (
                f"{r.session_id},{r.trading_date.isoformat()},{_dec(r.state_close)},"
                f"{_opt(r.state_obs_ts_ns)}"
            )
        flag = "true" if r.refreshed else "false"
        if r.refreshed:
            bar = f"{_dec(r.open)},{_dec(r.high)},{_dec(r.low)},{_dec(r.close)},{_dec(r.volume)}"
        else:
            bar = f",,,,{_dec(r.volume)}"
        lines.append(
            f"{prefix}T{tod}Z,{bar},"
            f"{_opt(r.trade_count)},{_ms_text(r.age_ns)},{flag},{state_text},"
            f"{_opt(r.first_obs_ts_ns)}"
        )
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _dec(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


_SECOND_TEXT: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\dZ$")


def _parse_second(text: str, cache: dict[str, int] | None = None, *, strict: bool = True) -> int:
    """``YYYY-MM-DDTHH:MM:SSZ`` -> UTC epoch seconds (date parsed once per day). ``strict``
    checks the full pattern; the light path checks only the shape (its file is SHA-verified)."""
    if len(text) != 20 or text[10] != "T" or (strict and not _SECOND_TEXT.fullmatch(text)):
        raise SeriesError(f"second_utc {text!r} is not YYYY-MM-DDTHH:MM:SSZ")
    prefix = text[:10]
    base = None if cache is None else cache.get(prefix)
    if base is None:
        base = date_start_ns(date.fromisoformat(prefix)) // NS_PER_SECOND
        if cache is not None:
            cache[prefix] = base
    return base + int(text[11:13]) * 3600 + int(text[14:16]) * 60 + int(text[17:19])


def _opt_decimal(text: str) -> Decimal | None:
    return None if text == "" else Decimal(text)


def _opt_int(text: str) -> int | None:
    return None if text == "" else int(text)


def _age_from_ms(text: str) -> int | None:
    if text == "":
        return None
    whole, _, fraction = text.partition(".")
    if len(fraction) != 6 or not whole.isdigit() or not fraction.isdigit():
        raise SeriesError(f"age_ms {text!r} is not the fixed 6-decimal form")
    return int(whole) * NS_PER_MS + int(fraction)


def seconds_from_csv(data: bytes) -> list[SecondRecord]:
    """Strict inverse of ``seconds_to_csv``."""
    rows = _strict_csv_rows(data, SECONDS_CSV_HEADER, "seconds csv")
    records: list[SecondRecord] = []
    cache: dict[str, int] = {}
    for _number, f in rows:
        if f[8] not in ("true", "false"):
            raise SeriesError("refreshed must be true or false")
        records.append(
            SecondRecord(
                second_s=_parse_second(f[0], cache),
                open=_opt_decimal(f[1]),
                high=_opt_decimal(f[2]),
                low=_opt_decimal(f[3]),
                close=_opt_decimal(f[4]),
                volume=Decimal(f[5]),
                trade_count=_opt_int(f[6]),
                age_ns=_age_from_ms(f[7]),
                refreshed=f[8] == "true",
                session_id=f[9],
                trading_date=date.fromisoformat(f[10]),
                state_close=_opt_decimal(f[11]),
                state_obs_ts_ns=_opt_int(f[12]),
                first_obs_ts_ns=_opt_int(f[13]),
            )
        )
    return records


def seconds_from_csv_light(data: bytes) -> list[tuple[int, str, str, str, bool]]:
    """Fast reader for analysis: (second_s, state_close text, age_ms text, session_id,
    refreshed); header and field count are checked exactly. Row values are validated by
    ``seconds_from_csv`` semantics at build time and protected by the output SHA-256 recorded
    in the manifest, which the loader verifies before calling this."""
    lines = data.decode("utf-8").split("\n")
    if lines[0] != ",".join(SECONDS_CSV_HEADER):
        raise SeriesError("seconds csv header mismatch")
    width = len(SECONDS_CSV_HEADER)
    cache: dict[str, int] = {}
    rows: list[tuple[int, str, str, str, bool]] = []
    for line in lines[1:]:
        if not line:
            continue
        f = line.split(",")
        if len(f) != width:
            raise SeriesError("seconds csv row has the wrong number of fields")
        rows.append((_parse_second(f[0], cache, strict=False), f[11], f[7], f[9], f[8] == "true"))
    return rows
