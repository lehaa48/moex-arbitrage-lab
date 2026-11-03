"""Time-of-day aligned bar returns of the H-0001 v0.3 multi-scale test (pure, stdlib only).

Frozen definitions (docs/experiments/H-0001_MULTISCALE_V0_3.md, section "Bars"):

- input states come from the frozen v0.2 one-second series: ``states[t]`` of a venue is the
  last observation with time ``< t + 1 s`` inside the same common segment, its age measured at
  ``t + 1 s`` (``research.seconds_store.SecondsRun.iter_days``); nothing is re-parsed;
- month grid origin ``lo`` = minimum common-segment start offset (seconds from trading-date
  00:00 UTC) over the month plan, rounded DOWN to a multiple of 60 s; the day origin is
  ``o = trading-date 00:00 UTC + lo``, so bars are time-of-day aligned across days;
- bar ``u`` of width ``D`` covers ``[o + uD, o + (u+1)D)``; endpoints ``e0 = o + uD - 1`` (state
  just before the bar starts) and ``e1 = o + (u+1)D - 1`` (state just before it ends);
- ``seg[u]`` = index of the common segment containing BOTH ``e0`` and ``e1``, else -1 (the first
  bar of a segment has ``e0`` before the segment start and is never usable: no carry across
  segment, break or day boundaries);
- bar return ``r(u) = ln S(e1) - ln S(e0)``, valid iff ``seg[u] >= 0``, both states exist and
  both ages ``<= max(staleness_floor_s, D)``; zero returns are kept;
- per venue/day/width accounting: ``slots`` (bars with ``seg >= 0``) = ``valid`` + ``no_state``
  + ``stale``, checked exactly (fail closed).

Only backward-looking states are used: ``S(e1)`` depends on observations strictly before
``o + (u+1)D``, the end of bar ``u``.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from moex_arbitrage_lab.research.seconds_analysis import (
    AnalysisDay,
    AnalysisError,
    VenueDayStates,
)
from moex_arbitrage_lab.research.seconds_config import NS_PER_SECOND, date_start_ns
from moex_arbitrage_lab.research.seconds_series import CommonSegment

__all__ = [
    "BarDay",
    "BarSeries",
    "build_bar_day",
    "month_origin_s",
    "n_bars_for",
]

Plan = Sequence[tuple[date, Sequence[CommonSegment]]]
COUNT_KEYS = ("slots", "valid", "no_state", "stale")


@dataclass(slots=True)
class BarSeries:
    """Per-bar return, validity (1.0/0.0) and squared return of one venue."""

    ret: list[float]
    ok: list[float]
    sq: list[float]


@dataclass(slots=True)
class BarDay:
    trading_date: date
    width_s: int
    seg: list[int]
    comex: BarSeries
    moex: BarSeries
    # {"comex": {"slots", "valid", "no_state", "stale"}, "moex": {...}}
    counts: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def n_bars(self) -> int:
        return len(self.seg)


def _day_start_s(day: date) -> int:
    return date_start_ns(day) // NS_PER_SECOND


def month_origin_s(plan: Plan) -> int:
    """``lo``: minimum common-segment start offset of the month, rounded down to 60 s (0 for a
    plan without segments)."""
    starts = [segment.start_s - _day_start_s(day) for day, common in plan for segment in common]
    if not starts:
        return 0
    return (min(starts) // 60) * 60


def n_bars_for(plan: Plan, lo_s: int, width_s: int) -> int:
    """Number of bars from ``lo_s`` covering the maximum common-segment end of the month."""
    if width_s <= 0:
        raise AnalysisError("bar width must be positive")
    ends = [segment.end_s - _day_start_s(day) for day, common in plan for segment in common]
    if not ends:
        return 0
    span = max(ends) - lo_s
    return max(-(-span // width_s), 0)


def _segment_index(
    common: Sequence[CommonSegment], origin: int, width_s: int, n_bars: int
) -> list[int]:
    seg = [-1] * n_bars
    span_end = origin + n_bars * width_s
    ordered = sorted((segment.start_s, segment.end_s) for segment in common)
    for (_, previous_end), (next_start, _) in itertools.pairwise(ordered):
        if next_start < previous_end:
            raise AnalysisError("common segments overlap")
    for index, segment in enumerate(common):
        if segment.start_s < origin or segment.end_s > span_end:
            raise AnalysisError("common segment outside the bar grid")
        if segment.end_s <= segment.start_s:
            continue
        # e0 = origin + u*w - 1 >= start  and  e1 = origin + (u+1)*w - 1 < end
        first = -(-(segment.start_s - origin + 1) // width_s)
        last = (segment.end_s - origin) // width_s - 1
        for u in range(max(first, 0), min(last, n_bars - 1) + 1):
            if seg[u] != -1:
                raise AnalysisError("common segments overlap")
            seg[u] = index
    return seg


def _venue_bars(
    states: VenueDayStates, seg: Sequence[int], origin: int, width_s: int, cap_ns: int
) -> tuple[BarSeries, dict[str, int]]:
    n_bars = len(seg)
    series = BarSeries([0.0] * n_bars, [0.0] * n_bars, [0.0] * n_bars)
    counts = dict.fromkeys(COUNT_KEYS, 0)
    lookup = states.states
    for u in range(n_bars):
        if seg[u] < 0:
            continue
        counts["slots"] += 1
        start = lookup.get(origin + u * width_s - 1)
        end = lookup.get(origin + (u + 1) * width_s - 1)
        if start is None or end is None:
            counts["no_state"] += 1
            continue
        if start[1] > cap_ns or end[1] > cap_ns:
            counts["stale"] += 1
            continue
        value = end[0] - start[0]
        series.ret[u] = value
        series.ok[u] = 1.0
        series.sq[u] = value * value
        counts["valid"] += 1
    if counts["valid"] + counts["no_state"] + counts["stale"] != counts["slots"]:
        raise AnalysisError("bar reconciliation failed: slots != valid + no_state + stale")
    if counts["valid"] != round(sum(series.ok)):
        raise AnalysisError("bar reconciliation failed: valid count != validity mask")
    return series, counts


def build_bar_day(
    day: AnalysisDay, width_s: int, staleness_floor_s: int, lo_s: int, n_bars: int
) -> BarDay:
    """Bars of one day at one width on the month grid ``lo_s`` with ``n_bars`` slots."""
    if width_s <= 0 or 60 % width_s:
        raise AnalysisError("bar width must be a positive divisor of 60 s")
    if staleness_floor_s <= 0 or n_bars < 0:
        raise AnalysisError("staleness floor must be positive and n_bars non-negative")
    origin = _day_start_s(day.trading_date) + lo_s
    cap_ns = max(staleness_floor_s, width_s) * NS_PER_SECOND
    seg = _segment_index(day.common, origin, width_s, n_bars)
    comex, comex_counts = _venue_bars(day.comex, seg, origin, width_s, cap_ns)
    moex, moex_counts = _venue_bars(day.moex, seg, origin, width_s, cap_ns)
    return BarDay(
        trading_date=day.trading_date,
        width_s=width_s,
        seg=seg,
        comex=comex,
        moex=moex,
        counts={"comex": comex_counts, "moex": moex_counts},
    )
