"""Source QA for one parsed T-Bank daily file: measured, never assumed (P1, TS section 5).

Measured on the eligible (``TRADE_SOURCE == EXCHANGE``) rows unless a key says otherwise.
Integers are UTC nanoseconds; the only floats are plain ratios rounded to 6 decimals. No
lead-lag statistic, no cross-source logic and no adequacy threshold is computed here: the
``lag_support`` status only states what the timestamp representation and documentation can
support; data density is explicitly left unassessed.
"""

import itertools
import math
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from fractions import Fraction
from typing import Final

from moex_arbitrage_lab.contracts.trade_v1 import TradeV1
from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig
from moex_arbitrage_lab.sources.tbank_history.parser import PARSER_VERSION, ParseResult

QA_VERSION: Final = "tbank_history_qa_v1"
LAG_STATUS: Final = (
    "UNSUPPORTED_BY_DATA",
    "TIMESTAMP_SEMANTICS_INSUFFICIENT",
    "QUANTUM_SUPPORTED_DENSITY_UNASSESSED",
)
QUANTILES: Final = ("0", "0.01", "0.1", "0.25", "0.5", "0.75", "0.9", "0.99", "1")
_NS_PER_S: Final = 1_000_000_000


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def iso_utc(ns: int) -> str:
    """Exact ISO-8601 UTC text with all nine fractional digits."""
    seconds, fraction = divmod(ns, _NS_PER_S)
    stamp = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.{fraction:09d}Z"


def nearest_rank_quantiles(values: Sequence[int]) -> dict[str, int | None]:
    """Nearest-rank quantiles (rank = max(1, ceil(p * n))) of ``values``; exact integers."""
    ordered = sorted(values)
    result: dict[str, int | None] = {}
    for label in QUANTILES:
        if not ordered:
            result[label] = None
            continue
        rank = max(1, math.ceil(Fraction(label) * len(ordered)))
        result[label] = ordered[rank - 1]
    return result


def _gaps(sorted_ts: Sequence[int]) -> list[int]:
    return [b - a for a, b in itertools.pairwise(sorted_ts)]


def _ties(sorted_ts: Sequence[int]) -> dict[str, object]:
    groups = Counter(sorted_ts)
    tied_groups = [count for count in groups.values() if count > 1]
    tied_rows = sum(count - 1 for count in tied_groups)
    return {
        "unique_event_ts": len(groups),
        "tied_rows": tied_rows,
        "tie_groups": len(tied_groups),
        "max_tie_group_size": max(groups.values(), default=0),
        "tied_row_share": _ratio(tied_rows, len(sorted_ts)),
    }


def _observed_quantum(sorted_ts: Sequence[int]) -> int | None:
    quantum = 0
    for gap in _gaps(sorted_ts):
        if gap:
            quantum = math.gcd(quantum, gap)
    return quantum or None


def _session_entries(
    config: TbankHistoryConfig, timestamps: Sequence[int]
) -> tuple[list[dict[str, object]], list[int]]:
    entries: list[dict[str, object]] = []
    within: list[int] = []
    for window in config.sessions:
        inside = sorted(ts for ts in timestamps if window.start_utc_ns <= ts < window.end_utc_ns)
        gaps = _gaps(inside)
        within.extend(gaps)
        first = inside[0] if inside else None
        last = inside[-1] if inside else None
        entries.append(
            {
                "name": window.name,
                "trading_date": window.trading_date.isoformat(),
                "continuous": window.continuous,
                "window_start_utc_ns": window.start_utc_ns,
                "window_end_utc_ns": window.end_utc_ns,
                "window_start_utc": iso_utc(window.start_utc_ns),
                "window_end_utc": iso_utc(window.end_utc_ns),
                "n_events": len(inside),
                "first_event_utc_ns": first,
                "last_event_utc_ns": last,
                "first_event_utc": None if first is None else iso_utc(first),
                "last_event_utc": None if last is None else iso_utc(last),
                "window_start_to_first_event_ns": (
                    None if first is None else first - window.start_utc_ns
                ),
                "last_event_to_window_end_ns": None if last is None else window.end_utc_ns - last,
                "inter_trade_gap_ns": {"n_gaps": len(gaps), **nearest_rank_quantiles(gaps)},
            }
        )
    return entries, within


def _lag_status(quantum_finer: bool, clock_documented: bool) -> str:
    if not quantum_finer:
        return "UNSUPPORTED_BY_DATA"
    if not clock_documented:
        return "TIMESTAMP_SEMANTICS_INSUFFICIENT"
    return "QUANTUM_SUPPORTED_DENSITY_UNASSESSED"


def compute_qa(result: ParseResult, config: TbankHistoryConfig) -> dict[str, object]:
    """Deterministic, JSON-serializable source QA (keys are strings)."""
    eligible: tuple[TradeV1, ...] = result.eligible
    file_order = [t.event_ts_utc_ns for t in eligible]
    timestamps = [ts for ts in file_order if ts is not None]
    sorted_ts = sorted(timestamps)

    # Fractional digits are a property of the file representation: parsed rows.
    max_digits = max(result.fractional_digits, default=None)
    representation_quantum = None if max_digits is None else 10 ** (9 - max_digits)
    observed_quantum = _observed_quantum(sorted_ts)

    non_monotonic = 0
    previous: int | None = None
    for ts in timestamps:
        if previous is not None and ts < previous:
            non_monotonic += 1
        previous = ts

    seen: set[tuple[object, ...]] = set()
    duplicates = 0
    for trade in eligible:
        key = (trade.event_ts_utc_ns, trade.price, trade.size, trade.trade_source)
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)

    sessions, within_session_gaps = _session_entries(config, timestamps)
    overall_gaps = _gaps(sorted_ts)

    lag_support = []
    for horizon in config.lag_buckets_ns:
        finer = representation_quantum is not None and representation_quantum < horizon
        lag_support.append(
            {
                "horizon_ns": horizon,
                "representation_quantum_ns": representation_quantum,
                "observed_quantum_ns": observed_quantum,
                "quantum_finer_than_horizon": finer,
                "clock_accuracy_documented": config.clock_accuracy_documented,
                "eligible_events": len(timestamps),
                "fraction_of_gaps_le_horizon": _ratio(
                    sum(1 for gap in within_session_gaps if gap <= horizon),
                    len(within_session_gaps),
                ),
                "status": _lag_status(finer, config.clock_accuracy_documented),
            }
        )

    return {
        "qa_version": QA_VERSION,
        "parser_version": PARSER_VERSION,
        "scope": "eligible rows (TRADE_SOURCE == EXCHANGE) unless stated otherwise",
        "counts": {
            "csv_data_rows": result.reconciliation.csv_data_rows,
            "target_rows": result.reconciliation.target_rows,
            "parsed": len(result.trades),
            "eligible": len(eligible),
            "null_event_ts": len(file_order) - len(timestamps),
            "null_timestamp_rows_excluded": result.reconciliation.parse_failures["null_timestamp"],
        },
        "timestamp": {
            "event_ts_type": config.event_ts_type,
            "clock_accuracy_documented": config.clock_accuracy_documented,
            "fractional_digits_scope": "parsed rows",
            "fractional_digits": {str(k): v for k, v in sorted(result.fractional_digits.items())},
            "max_fractional_digits": max_digits,
            "representation_quantum_ns": representation_quantum,
            "observed_quantum_ns": observed_quantum,
        },
        "ties": _ties(sorted_ts),
        "non_monotonic_in_file": non_monotonic,
        "inter_trade_gap_ns": {
            "overall": {"n_gaps": len(overall_gaps), **nearest_rank_quantiles(overall_gaps)},
            "within_sessions": {
                "n_gaps": len(within_session_gaps),
                **nearest_rank_quantiles(within_session_gaps),
            },
            "quantile_method": "nearest-rank: value at rank max(1, ceil(p*n)) of sorted gaps",
        },
        "potential_duplicates": duplicates,
        "potential_duplicates_key": ["event_ts_utc_ns", "price", "size", "trade_source"],
        "actions": dict(sorted(Counter(t.action.value for t in eligible).items())),
        "domain": {
            "non_positive_price": sum(1 for t in eligible if t.price <= 0),
            "non_positive_size": sum(1 for t in eligible if t.size <= 0),
            "price_ticks_null": sum(1 for t in eligible if t.price_ticks is None),
        },
        "sessions": sessions,
        "composition": {key: dict(value) for key, value in result.composition.items()},
        "composition_scope": "target-instrument rows (TICKER_CC == config.ticker_cc), raw values",
        "lag_support": lag_support,
        "lag_support_gap_scope": "within-session gaps of eligible rows",
    }
