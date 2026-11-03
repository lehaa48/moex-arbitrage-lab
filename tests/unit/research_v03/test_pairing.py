"""v0.3 pairing (pure): lag +1/-1 pairs only within one common segment, sufficient sums on a
hand-computed example and Pearson/Fisher z against an independent computation."""

import math
import random
import statistics
from collections.abc import Callable
from datetime import date

import pytest

from moex_arbitrage_lab.research.seconds_analysis import (
    AnalysisDay,
    PairSums,
    fisher_z,
    pearson,
)
from moex_arbitrage_lab.research_v03.analysis import cell_sums
from moex_arbitrage_lab.research_v03.bars import BarDay, BarSeries, build_bar_day

DAY = date(2026, 9, 1)
LO = 7 * 3600
FRESH = 400_000_000


def _series(values: list[float | None]) -> BarSeries:
    ret = [0.0 if v is None else v for v in values]
    return BarSeries(
        ret=ret,
        ok=[0.0 if v is None else 1.0 for v in values],
        sq=[r * r for r in ret],
    )


def _bar_day(seg: list[int], comex: list[float | None], moex: list[float | None]) -> BarDay:
    return BarDay(
        trading_date=DAY,
        width_s=5,
        seg=seg,
        comex=_series(comex),
        moex=_series(moex),
        counts={},
    )


def _close(actual: PairSums, expected: PairSums) -> None:
    assert actual.n == expected.n
    for name in ("sx", "sy", "sxx", "syy", "sxy"):
        assert getattr(actual, name) == pytest.approx(getattr(expected, name)), name


def test_forward_and_reverse_pairs_never_cross_a_segment_boundary() -> None:
    # Bars 0,1 in segment 0 and bars 2,3 in segment 1, all valid.
    bars = _bar_day([0, 0, 1, 1], [1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])

    forward = cell_sums(bars, 1)
    reverse = cell_sums(bars, -1)

    # +1: COMEX u with MOEX u+1 -> (0,1) and (2,3); (1,2) crosses the boundary.
    _close(forward, PairSums(2, 1.0 + 3.0, 20.0 + 40.0, 1.0 + 9.0, 400.0 + 1600.0, 20.0 + 120.0))
    # -1: COMEX u with MOEX u-1 -> (1,0) and (3,2).
    _close(reverse, PairSums(2, 2.0 + 4.0, 10.0 + 30.0, 4.0 + 16.0, 100.0 + 900.0, 20.0 + 120.0))


def test_invalid_bars_and_outside_bars_are_not_paired() -> None:
    bars = _bar_day(
        [-1, 0, 0, 0, 0, -1],
        [5.0, 1.0, None, 2.0, 3.0, 7.0],
        [5.0, 1.0, 1.0, None, 2.0, 7.0],
    )

    forward = cell_sums(bars, 1)
    reverse = cell_sums(bars, -1)

    # +1 candidates (1,2) (2,3) (3,4): (2,3) has both bars invalid; (0,1) and (4,5) touch a
    # bar outside the segments although both values are set.
    _close(forward, PairSums(2, 1.0 + 2.0, 1.0 + 2.0, 1.0 + 4.0, 1.0 + 4.0, 1.0 + 4.0))
    # -1 candidates (2,1) (3,2) (4,3): COMEX 2 invalid, (3,2) valid, MOEX 3 invalid.
    _close(reverse, PairSums(1, 2.0, 1.0, 4.0, 1.0, 2.0))


def test_hand_computed_sums_from_built_bars(
    day_factory: Callable[..., AnalysisDay], midnight: Callable[[date], int]
) -> None:
    o = midnight(DAY) + LO
    # Endpoint seconds at width 10: o+9, o+19, ..., o+59; segments [o, o+20), [o+20, o+60).
    comex_at = {9: 0.0, 19: 1.0, 29: 3.0, 39: 6.0, 49: 10.0, 59: 15.0}
    moex_at = {9: 0.0, 19: 2.0, 29: 0.0, 39: -1.0, 49: 1.0, 59: 4.0}
    comex = {o + k: (v, FRESH) for k, v in comex_at.items()}
    moex = {o + k: (v, FRESH) for k, v in moex_at.items()}
    day = day_factory(DAY, [(LO, LO + 20), (LO + 20, LO + 60)], comex, moex)
    bars = build_bar_day(day, 10, 5, LO, 6)
    assert bars.seg == [-1, 0, -1, 1, 1, 1]

    # COMEX returns u1..u5 = 1, -, 3, 4, 5; MOEX = 2, -, -1, 2, 3.
    _close(cell_sums(bars, 1), PairSums(2, 7.0, 5.0, 25.0, 13.0, 18.0))
    _close(cell_sums(bars, -1), PairSums(2, 9.0, 1.0, 41.0, 5.0, 6.0))


def test_pearson_and_fisher_z_match_an_independent_computation() -> None:
    rng = random.Random(11)  # noqa: S311 - synthetic data
    x = [rng.gauss(0.0, 1.0) for _ in range(40)]
    y = [0.0, *[0.6 * x[u] + rng.gauss(0.0, 1.0) for u in range(39)]]  # y[u+1] ~ x[u]
    bars = _bar_day([0] * 40, list(x), list(y))

    sums = cell_sums(bars, 1)
    r = pearson(sums, 30)

    expected = statistics.correlation(x[:39], y[1:])
    assert sums.n == 39
    assert r is not None
    assert r == pytest.approx(expected, rel=1e-9)
    assert fisher_z(r, 0.999999) == pytest.approx(math.atanh(expected), rel=1e-9)
    # Defined only with at least min_pairs pairs.
    assert pearson(sums, 40) is None
    assert fisher_z(1.0, 0.999999) == pytest.approx(math.atanh(0.999999))
