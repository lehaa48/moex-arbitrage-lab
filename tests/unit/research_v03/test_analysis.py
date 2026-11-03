"""v0.3 month analysis on synthetic months: eligibility, family-wise max-T p values, pass flags,
reverse blocking, data gate, determinism and the combined H-0001 decision (all synthetic).

The frozen config is used unchanged (R = 2000 replicates, seed 20261001)."""

import hashlib
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from moex_arbitrage_lab.research.seconds_analysis import (
    AnalysisDay,
    derangements,
    fisher_z,
    pearson,
)
from moex_arbitrage_lab.research_v03.analysis import (
    MultiscaleResult,
    analyze,
    cell_sums,
    combined_decision,
    day_z,
)
from moex_arbitrage_lab.research_v03.bars import build_bar_day, month_origin_s, n_bars_for
from moex_arbitrage_lab.research_v03.config import MultiscaleConfig, load_multiscale_config

CONFIG = Path(__file__).resolve().parents[3] / "config/experiments/h0001_multiscale_v0_3.toml"
R = 2000
P_MIN = 1 / (R + 1)
WIDTHS = (5, 10, 30, 60)
MonthFactory = Callable[..., list[AnalysisDay]]
BAD_DAYS = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]


@pytest.fixture(scope="module")
def config() -> MultiscaleConfig:
    return load_multiscale_config(CONFIG)[0]


@pytest.fixture(scope="module")
def lead_days(month_factory: MonthFactory, october: list[date]) -> list[AnalysisDay]:
    return month_factory(october, kind="lead", seed=101)


@pytest.fixture(scope="module")
def positive(
    lead_days: list[AnalysisDay], month_factory: MonthFactory, config: MultiscaleConfig
) -> MultiscaleResult:
    coarse, short, unreconciled = (
        month_factory([BAD_DAYS[0]], kind="lead", seed=5)[0],
        month_factory([BAD_DAYS[1]], kind="lead", seed=6, length_s=1000)[0],
        month_factory([BAD_DAYS[2]], kind="lead", seed=7)[0],
    )
    bad = [
        replace(coarse, moex_quantum_ns=2_000_000_000),
        short,
        replace(unreconciled, reconciled=False),
    ]
    return analyze([*lead_days, *bad], config)


@pytest.fixture(scope="module")
def independent(
    month_factory: MonthFactory, october: list[date], config: MultiscaleConfig
) -> MultiscaleResult:
    return analyze(month_factory(october, kind="independent", seed=202), config)


@pytest.fixture(scope="module")
def reverse(
    month_factory: MonthFactory, october: list[date], config: MultiscaleConfig
) -> MultiscaleResult:
    return analyze(month_factory(october, kind="both", seed=303), config)


@pytest.fixture(scope="module")
def insufficient(lead_days: list[AnalysisDay], config: MultiscaleConfig) -> MultiscaleResult:
    return analyze(lead_days[:14], config)


def _p_values(result: MultiscaleResult) -> list[float]:
    return [stat.p_familywise for stat in result.stats.values() if stat.p_familywise is not None]


def test_comex_leading_by_one_bar_is_screen_positive(
    positive: MultiscaleResult, october: list[date]
) -> None:
    five = positive.widths[5]

    assert positive.category == "SCREEN_POSITIVE"
    assert positive.month == "2026-10"
    assert positive.lo_s == 7 * 3600
    assert positive.eligible_days == october
    assert positive.data_gate["passed"] is True
    assert 5 in positive.passing_widths
    assert five.passes is True
    assert five.reverse_blocked is False
    assert all(five.conditions.values())
    assert five.statistic_forward is not None
    assert five.statistic_forward > 0.9
    assert five.p_fw == pytest.approx(P_MIN)
    assert five.positive_day_share == 1.0
    assert five.strongest_day_share < 0.35
    assert five.statistic_reverse is not None
    assert abs(five.statistic_reverse) < 0.2
    # Delay 5 s: next-bar r ~ 5/width at every width, all positive.
    for width in WIDTHS:
        value = positive.stats[(width, 1)].statistic
        assert value is not None
        assert value > 0, width
    assert positive.stats[(5, 1)].n_days == 16


def test_eligibility_is_data_quality_only_and_needs_r_at_every_width(
    positive: MultiscaleResult,
) -> None:
    by_date = {day.trading_date: day for day in positive.days}
    coarse, short, unreconciled = (by_date[d] for d in BAD_DAYS)

    assert len(positive.days) == 19
    assert not coarse.eligible
    assert "timestamp_quantum_too_coarse" in coarse.reasons
    assert not unreconciled.eligible
    assert "reconciliation_failed" in unreconciled.reasons
    # 1000 s segment: 15 bars of 60 s (14 pairs < 30) -> r undefined at 60 s only.
    assert not short.eligible
    assert short.cells[(60, 1)].r is None
    assert short.cells[(60, -1)].r is None
    assert short.cells[(30, 1)].r is not None
    assert short.reasons == ["r_undefined_width_60s_forward", "r_undefined_width_60s_reverse"]
    for day in positive.days:
        if day.trading_date not in BAD_DAYS:
            assert day.eligible, day.trading_date
            assert day.reasons == []


def test_independent_noise_gives_no_screen_signal(independent: MultiscaleResult) -> None:
    assert independent.category == "NO_SCREEN_SIGNAL"
    assert independent.data_gate["passed"] is True
    assert len(independent.eligible_days) == 16
    assert independent.passing_widths == []
    assert independent.reverse_blocked_widths == []
    for width in WIDTHS:
        value = independent.stats[(width, 1)].statistic
        assert value is not None
        assert abs(value) < 0.1, width


def test_fewer_than_fifteen_eligible_days_is_data_insufficient(
    insufficient: MultiscaleResult,
) -> None:
    assert insufficient.category == "DATA_INSUFFICIENT"
    assert insufficient.data_gate["passed"] is False
    assert len(insufficient.eligible_days) == 14
    # Strong data, but no width can pass or be blocked without the data gate.
    assert insufficient.passing_widths == []
    assert insufficient.reverse_blocked_widths == []
    statistic = insufficient.stats[(5, 1)].statistic
    assert statistic is not None
    assert statistic > 0.9


def test_strong_family_significant_reverse_direction_blocks_the_width(
    reverse: MultiscaleResult,
) -> None:
    five = reverse.widths[5]

    assert five.reverse_blocked is True
    assert five.passes is False
    assert five.conditions["c1_forward_positive_and_familywise_significant"] is True
    assert five.conditions["c2_positive_day_share_at_least_min"] is True
    assert five.conditions["c3_strongest_day_share_below_max"] is True
    assert five.conditions["c4_reverse_not_blocking"] is False
    assert five.p_rev == pytest.approx(P_MIN)
    assert five.statistic_forward is not None
    assert five.statistic_reverse is not None
    assert five.statistic_reverse > 0.25 * five.statistic_forward
    assert 5 in reverse.reverse_blocked_widths
    assert 10 in reverse.reverse_blocked_widths
    # Fixed synthetic seed: forward and reverse are equally strong at every width, so no width
    # passes (5 and 10 s are blocked; 30 and 60 s are too weak or blocked).
    assert reverse.passing_widths == []
    assert reverse.category == "NO_SCREEN_SIGNAL"


def test_p_values_have_the_permutation_form_and_floor(
    positive: MultiscaleResult, independent: MultiscaleResult, reverse: MultiscaleResult
) -> None:
    for result in (positive, independent, reverse):
        values = _p_values(result)
        assert len(values) == 2 * len(WIDTHS)
        for p in values:
            count = p * (R + 1) - 1
            assert count == pytest.approx(round(count), abs=1e-6)
            assert P_MIN - 1e-12 <= p <= 1.0
    assert min(_p_values(positive)) == pytest.approx(P_MIN)
    assert min(_p_values(independent)) > 0.05 / 4


def test_null_uses_one_shared_set_of_derangements(
    positive: MultiscaleResult, independent: MultiscaleResult
) -> None:
    expected = hashlib.sha256(repr(derangements(16, R, 20261001)).encode("ascii")).hexdigest()

    for result in (positive, independent):
        assert result.null["seed"] == 20261001
        assert result.null["replicates"] == R
        assert result.null["permutations_sha256"] == expected
        for stat in result.stats.values():
            assert stat.null_defined_replicates == R
            assert stat.null_sd is not None
            assert stat.null_sd > 0


def test_t_value_is_standardised_by_the_null_population_moments(
    positive: MultiscaleResult,
) -> None:
    for stat in positive.stats.values():
        assert stat.mean_z is not None
        assert stat.null_mean is not None
        assert stat.null_sd is not None
        assert stat.t_value == pytest.approx((stat.mean_z - stat.null_mean) / stat.null_sd)
        assert stat.statistic == pytest.approx(math.tanh(stat.mean_z))


def test_same_seed_gives_an_identical_result(
    month_factory: MonthFactory,
    october: list[date],
    config: MultiscaleConfig,
    independent: MultiscaleResult,
) -> None:
    again = analyze(month_factory(october, kind="independent", seed=202), config)

    assert repr(again.as_json()) == repr(independent.as_json())


def test_day_z_is_the_fisher_z_of_the_pair_correlation(
    lead_days: list[AnalysisDay], config: MultiscaleConfig
) -> None:
    plan = [(day.trading_date, day.common) for day in lead_days]
    lo = month_origin_s(plan)
    bars = build_bar_day(lead_days[0], 10, 5, lo, n_bars_for(plan, lo, 10))

    for lag in (1, -1):
        r = pearson(cell_sums(bars, lag), 30)
        assert r is not None
        assert day_z(bars, lag, config) == pytest.approx(fisher_z(r, 0.999999))


@pytest.mark.parametrize(
    ("v02", "scenario", "expected"),
    [
        ("SCREEN_POSITIVE", "independent", "PROCEED"),
        ("SCREEN_POSITIVE", "insufficient", "PROCEED"),
        ("NO_SCREEN_SIGNAL", "positive", "PROCEED"),
        ("DATA_INSUFFICIENT", "positive", "PROCEED"),
        ("NO_SCREEN_SIGNAL", "independent", "CLOSED_NOT_CONFIRMED"),
        ("DATA_INSUFFICIENT", "insufficient", "CLOSED_NOT_CONFIRMED"),
        ("TIMESTAMP_SEMANTICS_INSUFFICIENT", "independent", "CLOSED_NOT_CONFIRMED"),
    ],
)
def test_combined_decision_table(
    v02: str, scenario: str, expected: str, request: pytest.FixtureRequest
) -> None:
    result: MultiscaleResult = request.getfixturevalue(scenario)

    assert combined_decision(v02, result) == expected


def test_combined_decision_reverse_blocked_row(reverse: MultiscaleResult) -> None:
    # A copy in which no width passes (the shared fixture is never mutated).
    widths = {width: replace(item, passes=False) for width, item in reverse.widths.items()}
    blocked_only = replace(reverse, category="NO_SCREEN_SIGNAL", widths=widths)

    assert combined_decision("NO_SCREEN_SIGNAL", blocked_only) == (
        "INVESTIGATE_COMMON_MOVE_OR_CLOCK"
    )
    assert combined_decision("SCREEN_POSITIVE", blocked_only) == "PROCEED"
    assert combined_decision("NO_SCREEN_SIGNAL", reverse) == "INVESTIGATE_COMMON_MOVE_OR_CLOCK"
    assert combined_decision("DATA_INSUFFICIENT", reverse) == "INVESTIGATE_COMMON_MOVE_OR_CLOCK"


def test_combined_decision_refuses_an_unknown_v02_category(
    independent: MultiscaleResult,
) -> None:
    with pytest.raises(ValueError, match="category"):
        combined_decision("POSITIVE", independent)
