"""v0.3 bars (pure): endpoint convention, segment membership, staleness cap on both endpoints,
missing states, zero returns, month origin and bar count (all synthetic, hand-computed)."""

from collections.abc import Callable
from datetime import date

import pytest

from moex_arbitrage_lab.research.seconds_analysis import AnalysisDay
from moex_arbitrage_lab.research.seconds_series import CommonSegment
from moex_arbitrage_lab.research_v03.bars import (
    BarDay,
    BarSeries,
    build_bar_day,
    month_origin_s,
    n_bars_for,
)

NS = 1_000_000_000
FRESH = 400_000_000
DAY = date(2026, 9, 1)
LO = 7 * 3600  # 07:00 UTC, a multiple of 60
DayFactory = Callable[..., AnalysisDay]


def _ok(series: BarSeries) -> list[bool]:
    return [bool(value) for value in series.ok]


def test_endpoints_are_the_states_just_before_the_bar_starts_and_ends(
    day_factory: DayFactory, midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    # Segment [o, o + 30); non-linear log prices so that any off-by-one shows.
    comex = {o + k: (k * k * 1e-6, FRESH) for k in range(30)}
    moex = {o + k: (0.5, FRESH) for k in range(30)}  # constant: zero returns
    day = day_factory(DAY, [(LO, LO + 30)], comex, moex)

    bars = build_bar_day(day, 5, 5, LO, 7)

    assert isinstance(bars, BarDay)
    assert bars.trading_date == DAY
    assert bars.width_s == 5
    # u=0: e0 = o - 1 lies before the segment (bar touching the segment start is invalid);
    # u=6: e1 = o + 34 lies after the segment end.
    assert bars.seg == [-1, 0, 0, 0, 0, 0, -1]
    assert _ok(bars.comex) == [False, True, True, True, True, True, False]
    for u in range(1, 6):
        e0, e1 = 5 * u - 1, 5 * u + 4
        expected = (e1 * e1 - e0 * e0) * 1e-6
        assert bars.comex.ret[u] == pytest.approx(expected, abs=1e-15)
        assert bars.comex.sq[u] == pytest.approx(expected * expected, abs=1e-20)
    # Zero returns are kept as valid observations.
    assert _ok(bars.moex) == [False, True, True, True, True, True, False]
    assert [bars.moex.ret[u] for u in range(1, 6)] == [0.0] * 5
    assert len(bars.comex.ret) == len(bars.moex.ret) == len(bars.comex.sq) == 7


def test_bars_spanning_two_segments_are_invalid(
    day_factory: DayFactory, midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    # Two contiguous but distinct common segments [o, o+20) and [o+20, o+60).
    states = {o + k: (k * 1e-4, FRESH) for k in range(60)}
    day = day_factory(DAY, [(LO, LO + 20), (LO + 20, LO + 60)], states, states)

    bars = build_bar_day(day, 10, 5, LO, 6)

    # u=1: (o+9, o+19) in seg 0; u=2: (o+19, o+29) spans seg 0 -> seg 1; u=3..5 in seg 1.
    assert bars.seg == [-1, 0, -1, 1, 1, 1]
    assert _ok(bars.comex) == [False, True, False, True, True, True]
    assert _ok(bars.moex) == _ok(bars.comex)


def test_bar_after_a_gap_between_segments_is_invalid(
    day_factory: DayFactory, midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    # Segments [o, o+20) and [o+30, o+60): bar u=3 starts exactly at the second segment start.
    states = {o + k: (k * 1e-4, FRESH) for k in [*range(20), *range(30, 60)]}
    day = day_factory(DAY, [(LO, LO + 20), (LO + 30, LO + 60)], states, states)

    bars = build_bar_day(day, 10, 5, LO, 6)

    assert bars.seg == [-1, 0, -1, -1, 1, 1]
    assert _ok(bars.comex) == [False, True, False, False, True, True]


def test_staleness_cap_is_max_of_floor_and_width_on_both_endpoints(
    day_factory: DayFactory, midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    ages = dict.fromkeys(range(60), FRESH)
    ages[9] = 5 * NS  # equal to the 5 s cap: allowed
    ages[19] = 5 * NS + 1  # above the 5 s cap, below the 10 s cap
    ages[39] = 10 * NS + 1  # above the 10 s cap
    comex = {o + k: (k * 1e-4, ages[k]) for k in range(60)}
    moex = {o + k: (k * 2e-4, FRESH) for k in range(60)}
    day = day_factory(DAY, [(LO, LO + 60)], comex, moex)

    five = build_bar_day(day, 5, 5, LO, 12)
    ten = build_bar_day(day, 10, 5, LO, 6)

    # Width 5 (cap 5 s): o+19 is e1 of u=3 and e0 of u=4; o+39 is e1 of u=7 and e0 of u=8.
    assert _ok(five.comex) == [
        *[False, True, True],
        *[False, False, True, True],
        *[False, False, True, True, True],
    ]
    assert _ok(five.moex) == [False, *[True] * 11]
    # Width 10 (cap 10 s): o+19 is usable; o+39 is e1 of u=3 and e0 of u=4.
    assert _ok(ten.comex) == [False, True, True, False, False, True]
    assert _ok(ten.moex) == [False, *[True] * 5]


def test_staleness_floor_applies_when_wider_than_the_bar(
    day_factory: DayFactory, midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    ages = dict.fromkeys(range(30), FRESH)
    ages[14] = 8 * NS  # e1 of u=2 and e0 of u=3 at width 5
    comex = {o + k: (k * 1e-4, ages[k]) for k in range(30)}
    day = day_factory(DAY, [(LO, LO + 30)], comex, comex)

    floor_five = build_bar_day(day, 5, 5, LO, 6)
    floor_ten = build_bar_day(day, 5, 10, LO, 6)

    assert _ok(floor_five.comex) == [False, True, False, False, True, True]
    assert _ok(floor_ten.comex) == [False, *[True] * 5]


def test_missing_endpoint_state_is_invalid_but_inner_gaps_are_not(
    day_factory: DayFactory, midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    comex = {o + k: (k * 1e-4, FRESH) for k in range(30) if k != 14}  # endpoint of u=2/u=3
    moex = {o + k: (k * 1e-4, FRESH) for k in range(30) if k not in (11, 12, 13)}  # inner only
    day = day_factory(DAY, [(LO, LO + 30)], comex, moex)

    bars = build_bar_day(day, 5, 5, LO, 6)

    assert bars.seg == [-1, 0, 0, 0, 0, 0]
    assert _ok(bars.comex) == [False, True, False, False, True, True]
    assert _ok(bars.moex) == [False, *[True] * 5]


def test_counts_reconcile_with_the_bar_flags(
    day_factory: DayFactory, midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    comex = {o + k: (k * 1e-4, FRESH) for k in range(60) if k != 14}
    moex = {o + k: (k * 1e-4, 6 * NS if k == 29 else FRESH) for k in range(60)}
    day = day_factory(DAY, [(LO, LO + 20), (LO + 20, LO + 60)], comex, moex)

    bars = build_bar_day(day, 5, 5, LO, 12)

    assert isinstance(bars.counts, dict)
    # Every bar is either outside the segments or a valid/invalid slot of each venue.
    slots = sum(1 for s in bars.seg if s >= 0)
    assert slots == 10  # u=1..3 in seg 0, u=4 spans, u=5..11 in seg 1 (o+59 is the last state)
    assert sum(_ok(bars.comex)) == slots - 2  # o+14 missing: u=2 and u=3
    assert sum(_ok(bars.moex)) == slots - 2  # o+29 stale: u=5 and u=6
    assert bars.counts["comex"] == {"slots": 10, "valid": 8, "no_state": 2, "stale": 0}
    assert bars.counts["moex"] == {"slots": 10, "valid": 8, "no_state": 0, "stale": 2}
    for venue in ("comex", "moex"):
        counts = bars.counts[venue]
        assert counts["slots"] == counts["valid"] + counts["no_state"] + counts["stale"]


def test_month_origin_is_the_minimum_segment_start_rounded_down_to_a_minute(
    midnight: Callable[[date], int],
) -> None:
    def segment(day: date, start: int, end: int) -> CommonSegment:
        base = midnight(day)
        return CommonSegment("s", base + start, base + end, day, "m", "c")

    d1, d2 = date(2026, 9, 1), date(2026, 9, 2)
    plan = [
        (d1, (segment(d1, LO + 30, LO + 3600),)),
        (d2, (segment(d2, LO + 300, LO + 4000), segment(d2, LO + 5000, LO + 7230))),
    ]

    lo = month_origin_s(plan)

    assert lo == LO
    assert lo % 60 == 0
    assert month_origin_s([(d2, (segment(d2, LO - 1, LO + 100),))]) == LO - 60
    assert month_origin_s([(d1, (segment(d1, LO, LO + 100),))]) == LO
    hi = 7230
    for width in (5, 10, 30, 60):
        n = n_bars_for(plan, lo, width)
        # The bars cover every common segment of the month on the time-of-day grid.
        assert lo + n * width >= LO + hi, width
        assert n >= (LO + hi - lo) // width
