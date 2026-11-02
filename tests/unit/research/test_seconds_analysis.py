"""Frozen seconds analysis: lag sign, causal returns, boundaries, stale caps, weighting,
discovery rule, deterministic null, data gate and every decision branch (all synthetic)."""

import math
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from moex_arbitrage_lab.research.seconds_analysis import (
    AnalysisDay,
    Cell,
    DayReport,
    DecisionInputs,
    GateInputs,
    Grid,
    PairSums,
    VenueDayStates,
    abs_z_share,
    analyze_month,
    build_day_arrays,
    day_cells,
    decide,
    derangements,
    equal_day_statistic,
    evaluate_data_gate,
    fisher_z,
    month_statistics,
    pearson,
    select_h_star,
    strongest_day_share,
    upper_quantile,
)
from moex_arbitrage_lab.research.seconds_config import ScreenConfig
from moex_arbitrage_lab.research.seconds_series import CommonSegment

NS = 1_000_000_000
OCT = [date(2026, 10, d) for d in (5, 6, 7, 8)]
Days = Callable[..., list[AnalysisDay]]


def _analyze(market: Callable[..., Any], to_days: Days, config: ScreenConfig, **kw: Any) -> Any:
    days = to_days(market(OCT, **kw), config)
    return analyze_month("2026-10", days, config, keep_arrays=True)


def _stat(analysis: Any, lag: int, cap: int = 5) -> float:
    value = analysis.stats[(lag, cap)].statistic
    assert value is not None
    return float(value)


def test_known_comex_lead_of_five_seconds_is_recovered_with_positive_sign(
    frozen: tuple[ScreenConfig, str], market: Callable[..., Any], to_days: Days
) -> None:
    config = frozen[0]
    analysis = _analyze(market, to_days, config, lead_s=5)
    positive = {lag: _stat(analysis, lag) for lag in config.analysis.positive_grid_s}

    assert analysis.included == OCT
    assert select_h_star(positive, config.analysis.positive_grid_s) == 5
    assert _stat(analysis, 5) > 0.8
    assert abs(_stat(analysis, -5)) < 0.1  # the reverse direction is not the effect
    assert max(abs(_stat(analysis, lag)) for lag in (-60, -1, 0, 1, 2, 10, 60)) < 0.1


def test_zero_lag_case_peaks_at_zero(
    frozen: tuple[ScreenConfig, str], market: Callable[..., Any], to_days: Days
) -> None:
    config = frozen[0]
    analysis = _analyze(market, to_days, config, lead_s=0)
    grid = {lag: _stat(analysis, lag) for lag in config.analysis.lag_grid_s}

    assert max(grid, key=lambda lag: grid[lag]) == 0
    assert grid[0] > 0.8
    assert max(abs(grid[lag]) for lag in config.analysis.positive_grid_s) < 0.1


def test_reverse_lead_peaks_at_negative_lag_and_not_on_the_positive_grid(
    frozen: tuple[ScreenConfig, str], market: Callable[..., Any], to_days: Days
) -> None:
    config = frozen[0]
    analysis = _analyze(market, to_days, config, lead_s=-5)
    grid = {lag: _stat(analysis, lag) for lag in config.analysis.lag_grid_s}

    assert max(grid, key=lambda lag: grid[lag]) == -5
    assert max(grid[lag] for lag in config.analysis.positive_grid_s) < 0.1


# --- exact brute-force cross-check of shifting, masking and segment rules ---------------------


def _random_day(seed: int, at: Callable[..., int]) -> AnalysisDay:
    rng = random.Random(seed)  # noqa: S311 - synthetic data
    day = date(2026, 10, 6)
    base = at(day, 7)
    segments = (
        CommonSegment("a@g", base, base + 300, day, "a", "g"),
        CommonSegment("b@g", base + 300, base + 600, day, "b", "g"),  # adjacent: no crossing
        CommonSegment("c@g", base + 1000, base + 1200, day, "c", "g"),
    )

    def states() -> VenueDayStates:
        level = 0.0
        found: dict[int, tuple[float, int]] = {}
        for segment in segments:
            for t in range(segment.start_s, segment.end_s):
                level += rng.gauss(0, 1e-4)
                if rng.random() < 0.85:
                    found[t] = (level, rng.choice((1, 2, 4, 6, 9, 12)) * NS // 2)
        return VenueDayStates(found, frozenset(t for t in found if rng.random() < 0.5))

    return AnalysisDay(day, segments, states(), states(), NS, True)


def _naive(day: AnalysisDay, lag: int, cap: int) -> tuple[int, float | None]:
    def segment_of(t: int) -> int:
        for index, segment in enumerate(day.common):
            if segment.start_s <= t < segment.end_s:
                return index
        return -1

    def ret(states: VenueDayStates, t: int) -> float | None:
        if segment_of(t) < 0 or segment_of(t - 1) != segment_of(t):
            return None
        a, b = states.states.get(t - 1), states.states.get(t)
        if a is None or b is None or max(a[1], b[1]) > cap * NS:
            return None
        return b[0] - a[0]

    xs: list[float] = []
    ys: list[float] = []
    for segment in day.common:
        for t in range(segment.start_s, segment.end_s):
            if segment_of(t + lag) != segment_of(t):
                continue
            x, y = ret(day.comex, t), ret(day.moex, t + lag)
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
    n = len(xs)
    if n < 3:
        return n, None
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    cov = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = math.fsum((x - mx) ** 2 for x in xs)
    vy = math.fsum((y - my) ** 2 for y in ys)
    return n, cov / math.sqrt(vx * vy)


@pytest.mark.parametrize("seed", [1, 2])
def test_vectorised_cells_equal_a_naive_pairwise_computation(
    frozen: tuple[ScreenConfig, str], at: Callable[..., int], seed: int
) -> None:
    config = frozen[0]
    day = _random_day(seed, at)
    arrays = build_day_arrays(day, Grid.for_days([day]), config.analysis.staleness_caps_s)
    cells = day_cells(arrays, config.analysis.lag_grid_s, config.analysis.staleness_caps_s, config)

    for (lag, cap), cell in cells.items():
        n, r = _naive(day, lag, cap)
        assert cell.sums.n == n, (lag, cap)
        if r is None:
            assert cell.r is None
        else:
            assert cell.r is not None
            assert abs(cell.r - r) < 1e-9, (lag, cap)


def test_pairs_never_cross_adjacent_segment_boundaries(
    frozen: tuple[ScreenConfig, str], at: Callable[..., int]
) -> None:
    config = frozen[0]
    day_date = date(2026, 10, 6)
    base = at(day_date, 7)
    common = (
        CommonSegment("a@g", base, base + 100, day_date, "a", "g"),
        CommonSegment("b@g", base + 100, base + 200, day_date, "b", "g"),
    )
    rng = random.Random(3)  # noqa: S311 - synthetic data
    full = {t: (rng.gauss(0, 1e-3), NS // 2) for t in range(base - 10, base + 210)}
    day = AnalysisDay(
        day_date,
        common,
        VenueDayStates(full, frozenset()),
        VenueDayStates(full, frozenset()),
        NS,
        True,
    )
    arrays = build_day_arrays(day, Grid.for_days([day]), (5,))
    cells = day_cells(arrays, (-5, 0, 5, 60), (5,), config)

    # Valid returns: t in [start+1, end) per segment; pairs keep t and t+lag in one segment.
    assert cells[(0, 5)].sums.n == 2 * 99
    assert cells[(5, 5)].sums.n == 2 * 94
    assert cells[(-5, 5)].sums.n == 2 * 94
    assert cells[(60, 5)].sums.n == 2 * 39
    assert arrays.counts["moex"].invalid["segment_first_second"] == 2


def test_staleness_caps_one_five_ten(
    frozen: tuple[ScreenConfig, str], at: Callable[..., int]
) -> None:
    day_date = date(2026, 10, 6)
    base = at(day_date, 7)
    common = (CommonSegment("a@g", base, base + 10, day_date, "a", "g"),)
    ages = [1, 1, 1, 6, 6, 1, 1, 11, 1, 1]
    moex = VenueDayStates(
        {base + k: (0.001 * k, age * NS) for k, age in enumerate(ages)}, frozenset()
    )
    comex = VenueDayStates({base + k: (0.0, NS) for k in range(10)}, frozenset())
    arrays = build_day_arrays(
        AnalysisDay(day_date, common, moex, comex, NS, True),
        Grid(7 * 3600, 10),
        (1, 5, 10),
    )
    counts = arrays.counts["moex"]

    assert counts.valid_returns == {1: 4, 5: 4, 10: 7}
    assert counts.invalid["stale_cap_1s"] == 5
    assert counts.invalid["stale_cap_10s"] == 2
    assert counts.stale_fraction[5] == pytest.approx(0.6)
    assert arrays.counts["comex"].valid_returns == {1: 9, 5: 9, 10: 9}


def test_day_quality_rules_exclude_with_reason_codes(
    frozen: tuple[ScreenConfig, str], market: Callable[..., Any], to_days: Days
) -> None:
    config = frozen[0]
    sparse = to_days(market(OCT[:1], lead_s=5, moex_prob=0.15), config)[0]
    short = to_days(market(OCT[1:2], lead_s=5, seconds=2000), config)[0]
    good, coarse, missing, broken = to_days(
        market([*OCT[2:4], date(2026, 10, 9), date(2026, 10, 12)], lead_s=5), config
    )
    coarse = replace(coarse, moex_quantum_ns=2 * NS)
    missing = replace(missing, moex_quantum_ns=None)
    broken = replace(broken, reconciled=False)
    analysis = analyze_month(
        "2026-10", [sparse, short, good, coarse, missing, broken], config, keep_arrays=False
    )
    reasons = {r.trading_date: r.reasons for r in analysis.days}

    assert "moex_stale_fraction_ge_max" in reasons[OCT[0]]
    assert reasons[OCT[1]] == ["paired_seconds_below_min"]
    assert reasons[OCT[2]] == []
    assert reasons[OCT[3]] == ["timestamp_quantum_too_coarse"]
    assert reasons[date(2026, 10, 9)] == ["timestamp_quantum_missing"]
    assert reasons[date(2026, 10, 12)] == ["reconciliation_failed"]
    assert analysis.included == [OCT[2]]


# --- aggregation -----------------------------------------------------------------------------


def _sums(xs: list[float], ys: list[float]) -> PairSums:
    return PairSums(
        len(xs),
        math.fsum(xs),
        math.fsum(ys),
        math.fsum(x * x for x in xs),
        math.fsum(y * y for y in ys),
        math.fsum(x * y for x, y in zip(xs, ys, strict=True)),
    )


def test_equal_day_weighting_differs_from_pooled(frozen: tuple[ScreenConfig, str]) -> None:
    config = frozen[0]
    rng = random.Random(5)  # noqa: S311 - synthetic data
    xa = [rng.gauss(0, 1) for _ in range(5000)]
    ya = [x + rng.gauss(0, 0.3) for x in xa]
    xb = [rng.gauss(0, 1) for _ in range(40)]
    yb = [-x + rng.gauss(0, 0.3) for x in xb]
    reports = []
    for day, (xs, ys) in zip(
        (date(2026, 10, 5), date(2026, 10, 6)), ((xa, ya), (xb, yb)), strict=True
    ):
        sums = _sums(xs, ys)
        r = pearson(sums, 3)
        assert r is not None
        cell = Cell(day, 5, 5, sums, r, fisher_z(r, 0.999999))
        reports.append(DayReport(day, True, [], {}, sums.n, {(5, 5): cell}))
    stat = month_statistics(reports, (5,), (5,), config)[(5, 5)]
    za, zb = (rep.cells[(5, 5)].z for rep in reports)
    assert za is not None
    assert zb is not None

    assert stat.statistic == pytest.approx(math.tanh((za + zb) / 2))
    assert stat.statistic is not None
    assert stat.statistic < 0.0
    assert stat.pooled_r is not None
    assert stat.pooled_r > 0.9
    assert stat.n_days == 2
    assert stat.pooled_n == 5040


def test_correlation_helpers() -> None:
    assert math.isfinite(fisher_z(1.0, 0.999999))
    assert pearson(PairSums(10, 0.0, 1.0, 0.0, 1.0, 0.0), 3) is None  # no predictor variance
    assert pearson(PairSums(2, 1.0, 1.0, 1.0, 1.0, 1.0), 3) is None
    assert equal_day_statistic([]) is None


def test_strongest_day_share_definition() -> None:
    assert strongest_day_share([1.0, 1.0, 1.0, 1.0]) == pytest.approx(0.25)
    assert strongest_day_share([3.0, 1.0, 1.0, -1.0]) == pytest.approx(0.75)
    assert strongest_day_share([-2.0, -1.0, -1.0]) == pytest.approx(0.5)
    assert strongest_day_share([1.0, -1.0]) == math.inf
    assert strongest_day_share([]) == math.inf
    assert abs_z_share([3.0, 1.0, 1.0, -1.0]) == pytest.approx(0.5)


def test_h_star_rule_ties_negative_and_undefined() -> None:
    grid = (1, 2, 5, 10, 30, 60)

    assert select_h_star({1: 0.1, 2: 0.3, 5: 0.3, 10: 0.2, 30: 0.0, 60: 0.0}, grid) == 2
    assert select_h_star({1: -0.5, 2: -0.1, 5: -0.2, 10: -0.3, 30: -0.4, 60: -0.6}, grid) == 2
    assert select_h_star({1: None, 2: None, 5: -0.01, 10: None, 30: None, 60: None}, grid) == 5
    assert select_h_star(dict.fromkeys(grid), grid) is None


def test_derangement_null_is_deterministic_and_has_no_fixed_points() -> None:
    first = derangements(16, 2000, 20260901)
    again = derangements(16, 2000, 20260901)
    other = derangements(16, 2000, 1)

    assert first == again
    assert first != other
    assert len(first) == 2000
    assert all(all(p[k] != k for k in range(16)) for p in first)
    assert upper_quantile([float(v) for v in range(1, 101)], 0.95) == 95.0
    with pytest.raises(ValueError, match="two days"):
        derangements(1, 10, 0)


# --- data gate and decision ------------------------------------------------------------------

GATE_OK = GateInputs(
    included_days=15,
    min_paired_each_day=3600,
    max_stale_fraction_seen=0.49,
    timestamp_semantics_ok=True,
    session_map_published=True,
    reconciliation_ok=True,
    strongest_day_share=0.34,
)


@pytest.mark.parametrize(
    ("change", "item"),
    [
        ({"included_days": 14}, "min_confirmation_days"),
        ({"min_paired_each_day": 3599}, "min_paired_seconds_each_day"),
        ({"min_paired_each_day": None}, "min_paired_seconds_each_day"),
        ({"max_stale_fraction_seen": 0.5}, "stale_fraction_below_max"),
        ({"max_stale_fraction_seen": None}, "stale_fraction_below_max"),
        ({"timestamp_semantics_ok": False}, "timestamp_semantics_and_session_map_published"),
        ({"session_map_published": False}, "timestamp_semantics_and_session_map_published"),
        ({"reconciliation_ok": False}, "reconciliation_without_unresolved_mismatch"),
        ({"strongest_day_share": 0.35}, "strongest_day_share_below_max"),
        ({"strongest_day_share": math.inf}, "strongest_day_share_below_max"),
    ],
)
def test_every_data_gate_item(
    frozen: tuple[ScreenConfig, str], change: dict[str, Any], item: str
) -> None:
    config = frozen[0]
    assert evaluate_data_gate(GATE_OK, config)["passed"] is True

    result = evaluate_data_gate(replace(GATE_OK, **change), config)

    assert result[item] is False
    assert result["passed"] is False
    assert sum(1 for key, ok in result.items() if key != "passed" and not ok) == 1


DECISION_OK = DecisionInputs(
    gate={"passed": True},
    timestamp_semantics_ok=True,
    h_star=5,
    primary_statistic=0.3,
    null_threshold=0.02,
    positive_day_share=0.8,
    reverse_statistic=0.01,
    circular_shift_statistic=0.0,
    impossible_time_statistic=-0.01,
    cap_statistics={1: 0.2, 5: 0.3, 10: 0.3},
    strongest_day_share=0.1,
)


def test_all_conditions_met_is_screen_positive(frozen: tuple[ScreenConfig, str]) -> None:
    category, conditions = decide(DECISION_OK, frozen[0])

    assert category == "SCREEN_POSITIVE"
    assert len(conditions) == 7
    assert all(conditions.values())


@pytest.mark.parametrize(
    ("change", "condition"),
    [
        ({"primary_statistic": 0.015}, "c2_primary_positive_above_null_threshold"),
        ({"primary_statistic": 0.02}, "c2_primary_positive_above_null_threshold"),
        ({"primary_statistic": -0.3}, "c2_primary_positive_above_null_threshold"),
        ({"null_threshold": None}, "c2_primary_positive_above_null_threshold"),
        ({"positive_day_share": 0.59}, "c3_positive_day_share_at_least_min"),
        ({"positive_day_share": None}, "c3_positive_day_share_at_least_min"),
        ({"reverse_statistic": 0.3}, "c4_reverse_weaker_in_absolute_value"),
        ({"reverse_statistic": -0.31}, "c4_reverse_weaker_in_absolute_value"),
        ({"reverse_statistic": None}, "c4_reverse_weaker_in_absolute_value"),
        ({"circular_shift_statistic": 0.02}, "c5_controls_do_not_reproduce"),
        ({"impossible_time_statistic": 0.2}, "c5_controls_do_not_reproduce"),
        ({"impossible_time_statistic": None}, "c5_controls_do_not_reproduce"),
        ({"cap_statistics": {1: -0.1, 5: 0.3, 10: -0.1}}, "c6_sign_kept_across_caps"),
        ({"cap_statistics": {1: 0.1, 5: -0.01, 10: 0.1}}, "c6_sign_kept_across_caps"),
        ({"cap_statistics": {1: 0.1, 5: None, 10: 0.1}}, "c6_sign_kept_across_caps"),
        ({"strongest_day_share": 0.35}, "c7_no_single_day_domination"),
    ],
)
def test_each_failed_condition_gives_no_screen_signal(
    frozen: tuple[ScreenConfig, str], change: dict[str, Any], condition: str
) -> None:
    category, conditions = decide(replace(DECISION_OK, **change), frozen[0])

    assert category == "NO_SCREEN_SIGNAL"
    assert conditions[condition] is False


def test_boundary_values_that_still_pass(frozen: tuple[ScreenConfig, str]) -> None:
    for change in (
        {"positive_day_share": 0.60},
        {"circular_shift_statistic": 0.019},
        {"cap_statistics": {1: -0.1, 5: 0.3, 10: 0.2}},
        {"reverse_statistic": -0.29},
    ):
        assert decide(replace(DECISION_OK, **change), frozen[0])[0] == "SCREEN_POSITIVE"


def test_gate_failure_timestamp_and_missing_h_star_categories(
    frozen: tuple[ScreenConfig, str],
) -> None:
    config = frozen[0]

    assert decide(replace(DECISION_OK, gate={"passed": False}), config)[0] == "DATA_INSUFFICIENT"
    assert decide(replace(DECISION_OK, h_star=None), config)[0] == "DATA_INSUFFICIENT"
    assert (
        decide(replace(DECISION_OK, timestamp_semantics_ok=False), config)[0]
        == "TIMESTAMP_SEMANTICS_INSUFFICIENT"
    )


def test_lags_longer_than_the_grid_give_no_pairs(
    frozen: tuple[ScreenConfig, str], at: Callable[..., int]
) -> None:
    config = frozen[0]
    day_date = date(2026, 10, 6)
    base = at(day_date, 7)
    common = (CommonSegment("a@g", base, base + 20, day_date, "a", "g"),)
    states = VenueDayStates({t: (t * 1e-4, NS // 2) for t in range(base, base + 20)}, frozenset())
    day = AnalysisDay(day_date, common, states, states, NS, True)
    arrays = build_day_arrays(day, Grid.for_days([day]), (5,))
    cells = day_cells(arrays, config.analysis.lag_grid_s, (5,), config)

    assert cells[(60, 5)].sums.n == 0
    assert cells[(-60, 5)].sums.n == 0
    assert cells[(10, 5)].sums.n == 9
