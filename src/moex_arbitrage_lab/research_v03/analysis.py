"""Frozen statistics of the H-0001 v0.3 multi-scale confirmation (pure, stdlib only).

Definitions (docs/experiments/H-0001_MULTISCALE_V0_3.md, sections "Statistics" and
"Decision"); bars are built by ``research_v03.bars``:

- pairing at lag ``k`` bars: COMEX ``r_C(u)`` with MOEX ``r_M(u + k)``, both valid and
  ``seg[u] == seg[u + k] >= 0``; forward ``k = +1`` (COMEX leads), reverse control ``k = -1``;
- per day/width/lag: Pearson ``r`` over the pairs, defined iff ``N >= min_pairs_per_cell`` and
  both variances > 0; ``z = atanh(clamp(r, +-fisher_r_clamp))``;
- day eligibility (once for all widths, data quality only): reconciled, common segments
  present, MOEX timestamp quantum <= ``max_timestamp_quantum_ns``, and ``r`` defined at every
  width for both lags; no correlation value, sign or stale fraction is used;
- month statistic per width/lag: ``stat = tanh(mean_d z_d)`` over eligible days; pooled ``r``
  (sums over eligible days) is a diagnostic only;
- null: ``R`` deterministic whole-day derangements (``derangements(n, R, seed)``), ONE list
  shared by every width and both lags; ``z[i][j]`` = COMEX day ``i`` x MOEX day ``j`` on the same
  bar grid (same-segment rule of MOEX day ``j``; the diagonal is the observed day cell);
  replicate mean ``m_b = mean_j z[pi_b(j)][j]`` over defined cells (undefined cells skipped);
- standardisation per width/lag: ``T = (mean_z - mu_null) / sigma_null`` with the population
  mean/SD of the defined ``m_b`` (undefined if ``sigma_null = 0``); ``T_b`` likewise;
- forward family-wise p (one-sided single-step max-T over the widths):
  ``p_fw(D) = (1 + #{b : max_D' T_b(D', +1) >= T(D, +1)}) / (R + 1)``;
- reverse family-wise p (two-sided): ``p_rev(D) = (1 + #{b : max_D' |T_b(D', -1)| >=
  |T(D, -1)|}) / (R + 1)``;
- a replicate whose maximum is undefined (no width with a defined ``T_b``) is counted as an
  exceedance (conservative); an undefined observed ``T`` gives an undefined p (cannot pass);
- ``positive_day_share(D)`` = share of eligible days with ``z(D, +1) > 0``;
  ``strongest_day_share(D)`` = ``max_d(s * z_d) / |sum_d z_d|``, ``s = sign(sum)`` (+inf if 0);
- data gate: ``DATA_INSUFFICIENT`` if fewer than ``min_eligible_days`` eligible days;
- a width passes iff (1) ``stat(D, +1) > 0`` and ``p_fw(D) <= alpha``, (2) positive day share
  ``>= min_positive_day_share``, (3) strongest day share ``< max_strongest_day_share``, (4)
  ``p_rev(D) > alpha`` or ``|stat(D, -1)| <= reverse_max_ratio * stat(D, +1)``; meeting 1-3 and
  failing only 4 is ``reverse_blocked``; pass and reverse_blocked flags require the data gate;
- category: ``SCREEN_POSITIVE`` if any width passes, else ``NO_SCREEN_SIGNAL`` (gate passed)
  or ``DATA_INSUFFICIENT``;
- combined H-0001 decision: ``PROCEED`` if v0.2 or v0.3 is ``SCREEN_POSITIVE``; else
  ``INVESTIGATE_COMMON_MOVE_OR_CLOCK`` if some v0.3 width is reverse_blocked; else
  ``CLOSED_NOT_CONFIRMED``.

Trades/bars only: a positive result is predictive evidence at most, never profit.
"""

import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

from moex_arbitrage_lab.research.seconds_analysis import (
    CATEGORIES,
    ZERO_SUMS,
    AnalysisDay,
    AnalysisError,
    PairSums,
    derangements,
    equal_day_statistic,
    fisher_z,
    pearson,
    strongest_day_share,
)
from moex_arbitrage_lab.research.seconds_config import month_of
from moex_arbitrage_lab.research.seconds_series import CommonSegment
from moex_arbitrage_lab.research_v03.bars import (
    BarDay,
    BarSeries,
    build_bar_day,
    month_origin_s,
    n_bars_for,
)
from moex_arbitrage_lab.research_v03.config import MultiscaleConfig

__all__ = [
    "CombinedDecision",
    "DayCell",
    "DayResult",
    "MultiscaleResult",
    "WidthDecision",
    "WidthLagStat",
    "analyze",
    "cell_sums",
    "combined_decision",
    "day_cell",
    "day_z",
    "z_matrix",
]

V03Category = Literal["SCREEN_POSITIVE", "NO_SCREEN_SIGNAL", "DATA_INSUFFICIENT"]
CombinedDecision = Literal["PROCEED", "INVESTIGATE_COMMON_MOVE_OR_CLOCK", "CLOSED_NOT_CONFIRMED"]
COMBINED_DECISIONS: Final = ("PROCEED", "INVESTIGATE_COMMON_MOVE_OR_CLOCK", "CLOSED_NOT_CONFIRMED")


# --- pairing -----------------------------------------------------------------------------------


@dataclass(slots=True)
class _Outcome:
    """MOEX bars shifted by ``lag`` and masked by the same-segment rule."""

    value: list[float]
    ok: list[float]
    sq: list[float]


def _shift(values: Sequence[float], lag: int) -> list[float]:
    """``out[u] = values[u + lag]`` with 0.0 outside the array."""
    length = len(values)
    if lag >= 0:
        return list(values[lag:]) + [0.0] * min(lag, length)
    return [0.0] * min(-lag, length) + list(values[: max(length + lag, 0)])


def _pair_mask(seg: Sequence[int], lag: int) -> list[float]:
    """1.0 where bars ``u`` and ``u + lag`` lie in the same common segment (``>= 0``)."""
    length = len(seg)
    if lag >= 0:
        shifted = list(seg[lag:]) + [-1] * min(lag, length)
    else:
        shifted = [-1] * min(-lag, length) + list(seg[: max(length + lag, 0)])
    return [1.0 if s >= 0 and s == s2 else 0.0 for s, s2 in zip(seg, shifted, strict=True)]


def _outcome(moex: BarSeries, seg: Sequence[int], lag: int) -> _Outcome:
    mask = _pair_mask(seg, lag)
    value = [a * b for a, b in zip(_shift(moex.ret, lag), mask, strict=True)]
    ok = [a * b for a, b in zip(_shift(moex.ok, lag), mask, strict=True)]
    return _Outcome(value, ok, [v * v for v in value])


def _sums(predictor: BarSeries, outcome: _Outcome) -> PairSums:
    if len(predictor.ret) != len(outcome.value):
        raise AnalysisError("COMEX and MOEX bars are not on the same grid")
    return PairSums(
        n=round(math.sumprod(predictor.ok, outcome.ok)),
        sx=math.sumprod(predictor.ret, outcome.ok),
        sy=math.sumprod(predictor.ok, outcome.value),
        sxx=math.sumprod(predictor.sq, outcome.ok),
        syy=math.sumprod(predictor.ok, outcome.sq),
        sxy=math.sumprod(predictor.ret, outcome.value),
    )


def cell_sums(bar_day: BarDay, lag: int) -> PairSums:
    """Pair sums of COMEX bar ``u`` with MOEX bar ``u + lag`` of the same day."""
    return _sums(bar_day.comex, _outcome(bar_day.moex, bar_day.seg, lag))


@dataclass(frozen=True, slots=True)
class DayCell:
    width_s: int
    lag: int
    sums: PairSums
    r: float | None
    z: float | None


def day_cell(bar_day: BarDay, lag: int, config: MultiscaleConfig) -> DayCell:
    sums = cell_sums(bar_day, lag)
    r = pearson(sums, config.analysis.min_pairs_per_cell)
    z = None if r is None else fisher_z(r, config.analysis.fisher_r_clamp)
    return DayCell(bar_day.width_s, lag, sums, r, z)


def day_z(bar_day: BarDay, lag: int, config: MultiscaleConfig) -> float | None:
    """Fisher z of one day/width/lag (None if r is undefined)."""
    return day_cell(bar_day, lag, config).z


def z_matrix(
    bar_days: Sequence[BarDay], lag: int, config: MultiscaleConfig
) -> list[list[float | None]]:
    """``m[i][j]`` = Fisher z of COMEX day ``i`` x MOEX day ``j`` (same bar grid, same-segment
    rule of MOEX day ``j``); the diagonal equals the observed day cells."""
    analysis = config.analysis
    count = len(bar_days)
    rows: list[list[float | None]] = [[None] * count for _ in range(count)]
    for j, outcome_day in enumerate(bar_days):
        outcome = _outcome(outcome_day.moex, outcome_day.seg, lag)
        for i, predictor_day in enumerate(bar_days):
            r = pearson(_sums(predictor_day.comex, outcome), analysis.min_pairs_per_cell)
            rows[i][j] = None if r is None else fisher_z(r, analysis.fisher_r_clamp)
    return rows


# --- per day -----------------------------------------------------------------------------------


@dataclass(slots=True)
class DayResult:
    trading_date: date
    eligible: bool
    reasons: list[str]
    reconciled: bool
    moex_quantum_ns: int | None
    common_seconds: int
    # (width_s, lag) -> cell; width_s -> venue -> counts
    cells: dict[tuple[int, int], DayCell]
    counts: dict[int, dict[str, dict[str, int]]]

    def as_json(self) -> dict[str, object]:
        return {
            "trading_date": self.trading_date.isoformat(),
            "eligible": self.eligible,
            "eligibility_reasons": list(self.reasons),
            "reconciled": self.reconciled,
            "moex_timestamp_quantum_ns": self.moex_quantum_ns,
            "common_seconds": self.common_seconds,
            "bar_counts": {
                str(width): {venue: dict(c) for venue, c in venues.items()}
                for width, venues in sorted(self.counts.items())
            },
            "cells": [
                {
                    "width_s": cell.width_s,
                    "lag_bars": cell.lag,
                    "paired_n": cell.sums.n,
                    "r": cell.r,
                    "fisher_z": cell.z,
                }
                for _key, cell in sorted(self.cells.items())
            ],
        }


def _lag_name(lag: int, config: MultiscaleConfig) -> str:
    return "forward" if lag == config.bars.forward_lag_bars else "reverse"


def day_reasons(
    day: AnalysisDay, cells: dict[tuple[int, int], DayCell], config: MultiscaleConfig
) -> list[str]:
    """Reason codes that make the day ineligible (empty list = eligible); data quality only."""
    reasons: list[str] = []
    if not day.reconciled:
        reasons.append("reconciliation_failed")
    if not day.common or sum(s.end_s - s.start_s for s in day.common) <= 0:
        reasons.append("no_common_segment")
    if day.moex_quantum_ns is None:
        reasons.append("timestamp_quantum_missing")
    elif day.moex_quantum_ns > config.data_gate.max_timestamp_quantum_ns:
        reasons.append("timestamp_quantum_too_coarse")
    for width in config.bars.widths_s:
        for lag in config.lags:
            if cells[(width, lag)].r is None:
                reasons.append(f"r_undefined_width_{width}s_{_lag_name(lag, config)}")
    return reasons


# --- month -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WidthLagStat:
    width_s: int
    lag: int
    statistic: float | None
    mean_z: float | None
    n_days: int
    pooled_r: float | None
    pooled_n: int
    null_mean: float | None
    null_sd: float | None
    null_defined_replicates: int
    t_value: float | None
    p_unadjusted_one_sided: float | None
    # forward lag: one-sided max-T p_fw; reverse lag: two-sided max|T| p_rev.
    p_familywise: float | None

    def as_json(self) -> dict[str, object]:
        return {
            "width_s": self.width_s,
            "lag_bars": self.lag,
            "equal_day_r": self.statistic,
            "mean_fisher_z": self.mean_z,
            "n_days": self.n_days,
            "pooled_r_diagnostic": self.pooled_r,
            "pooled_n": self.pooled_n,
            "null_mean": self.null_mean,
            "null_sd": self.null_sd,
            "null_defined_replicates": self.null_defined_replicates,
            "t_value": self.t_value,
            "p_unadjusted_one_sided": self.p_unadjusted_one_sided,
            "p_familywise": self.p_familywise,
        }


@dataclass(frozen=True, slots=True)
class WidthDecision:
    width_s: int
    statistic_forward: float | None
    statistic_reverse: float | None
    p_fw: float | None
    p_rev: float | None
    positive_day_share: float | None
    strongest_day_share: float
    conditions: dict[str, bool]
    passes: bool
    reverse_blocked: bool

    def as_json(self) -> dict[str, object]:
        return {
            "width_s": self.width_s,
            "statistic_forward": self.statistic_forward,
            "statistic_reverse": self.statistic_reverse,
            "p_fw": self.p_fw,
            "p_rev": self.p_rev,
            "positive_day_share": self.positive_day_share,
            "strongest_day_share": self.strongest_day_share,
            "conditions": dict(self.conditions),
            "passes": self.passes,
            "reverse_blocked": self.reverse_blocked,
        }


@dataclass(slots=True)
class MultiscaleResult:
    month: str | None
    lo_s: int
    n_bars: dict[int, int]
    days: list[DayResult]
    stats: dict[tuple[int, int], WidthLagStat]
    widths: dict[int, WidthDecision]
    null: dict[str, object]
    data_gate: dict[str, object]
    category: V03Category
    eligible_days: list[date] = field(default_factory=list)

    @property
    def passing_widths(self) -> list[int]:
        return [width for width, item in sorted(self.widths.items()) if item.passes]

    @property
    def reverse_blocked_widths(self) -> list[int]:
        return [width for width, item in sorted(self.widths.items()) if item.reverse_blocked]

    def as_json(self) -> dict[str, object]:
        return {
            "month": self.month,
            "grid_origin_lo_s": self.lo_s,
            "n_bars": {str(width): n for width, n in sorted(self.n_bars.items())},
            "eligible_days": [d.isoformat() for d in self.eligible_days],
            "n_eligible_days": len(self.eligible_days),
            "days": [day.as_json() for day in self.days],
            "width_lag_statistics": [stat.as_json() for _key, stat in sorted(self.stats.items())],
            "widths": [item.as_json() for _width, item in sorted(self.widths.items())],
            "passing_widths": self.passing_widths,
            "reverse_blocked_widths": self.reverse_blocked_widths,
            "null": dict(self.null),
            "data_gate": dict(self.data_gate),
            "category": self.category,
        }


def _resolve_plan(
    days: Iterable[AnalysisDay],
    plan: Sequence[tuple[date, Sequence[CommonSegment]]] | None,
) -> tuple[Iterable[AnalysisDay], list[tuple[date, tuple[CommonSegment, ...]]]]:
    if plan is None:
        materialized = sorted(days, key=lambda d: d.trading_date)
        return materialized, [(d.trading_date, tuple(d.common)) for d in materialized]
    return days, [(d, tuple(common)) for d, common in plan]


def _month(plan: Sequence[tuple[date, Sequence[CommonSegment]]], month: str | None) -> str | None:
    months = {month_of(d) for d, _common in plan}
    if month is not None:
        if months - {month}:
            raise AnalysisError(f"analysis days outside the month {month}")
        return month
    if len(months) > 1:
        raise AnalysisError("analysis days span more than one month")
    return next(iter(months), None)


def _population(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    mean = math.fsum(values) / len(values)
    variance = math.fsum((v - mean) ** 2 for v in values) / len(values)
    return mean, math.sqrt(variance)


def _standardise(value: float | None, mean: float | None, sd: float | None) -> float | None:
    if value is None or mean is None or sd is None or not sd > 0.0:
        return None
    return (value - mean) / sd


def _p_value(count: int, replicates: int) -> float:
    return (1 + count) / (replicates + 1)


@dataclass(slots=True)
class _NullCell:
    mean: float | None
    sd: float | None
    defined: int
    t_observed: float | None
    t_replicates: list[float | None]


def _null_cell(
    matrix: list[list[float | None]],
    perms: Sequence[tuple[int, ...]],
    observed_mean_z: float | None,
) -> _NullCell:
    means: list[float | None] = []
    for perm in perms:
        defined = [z for j in range(len(perm)) if (z := matrix[perm[j]][j]) is not None]
        means.append(None if not defined else math.fsum(defined) / len(defined))
    finite = [m for m in means if m is not None]
    mu, sd = _population(finite)
    return _NullCell(
        mean=mu,
        sd=sd,
        defined=len(finite),
        t_observed=_standardise(observed_mean_z, mu, sd),
        t_replicates=[_standardise(m, mu, sd) for m in means],
    )


def _max_or_none(values: Iterable[float | None]) -> float | None:
    defined = [v for v in values if v is not None]
    return max(defined) if defined else None


def _exceed_count(maxima: Sequence[float | None], observed: float) -> int:
    # Undefined replicate maximum counts as an exceedance (conservative).
    return sum(1 for m in maxima if m is None or m >= observed)


def analyze(
    days: Iterable[AnalysisDay],
    config: MultiscaleConfig,
    *,
    month: str | None = None,
    plan: Sequence[tuple[date, Sequence[CommonSegment]]] | None = None,
) -> MultiscaleResult:
    """Bars, day cells, eligibility, month statistics, null, p values, pass flags, category.

    With ``plan`` (the month plan from the manifest), ``days`` may be a lazy iterable in
    ascending date order (one ``AnalysisDay`` in memory at a time; only bar arrays of eligible
    days are kept); every planned day must be delivered with identical common segments.
    Without ``plan`` the days are materialized and the plan is derived from them.
    """
    widths = config.bars.widths_s
    forward, reverse = config.lags
    lags = (forward, reverse)
    source, resolved = _resolve_plan(days, plan)
    resolved_month = _month(resolved, month)
    planned = dict(resolved)
    if len(planned) != len(resolved):
        raise AnalysisError("duplicate date in the month plan")
    lo = month_origin_s(resolved)
    n_bars = {width: n_bars_for(resolved, lo, width) for width in widths}

    reports: list[DayResult] = []
    kept: dict[int, list[BarDay]] = {width: [] for width in widths}
    previous: date | None = None
    for day in source:
        if previous is not None and day.trading_date <= previous:
            raise AnalysisError("analysis days must be in strictly ascending date sequence")
        previous = day.trading_date
        if planned.get(day.trading_date) != tuple(day.common):
            raise AnalysisError(f"{day.trading_date}: day does not match the month plan")
        bar_days = {
            width: build_bar_day(day, width, config.bars.staleness_floor_s, lo, n_bars[width])
            for width in widths
        }
        cells = {
            (width, lag): day_cell(bar_days[width], lag, config) for width in widths for lag in lags
        }
        for (width, _lag), cell in cells.items():
            counts = bar_days[width].counts
            if cell.sums.n > min(counts["comex"]["valid"], counts["moex"]["valid"]):
                raise AnalysisError("bar reconciliation failed: pairs exceed valid bars")
        reasons = day_reasons(day, cells, config)
        reports.append(
            DayResult(
                trading_date=day.trading_date,
                eligible=not reasons,
                reasons=reasons,
                reconciled=day.reconciled,
                moex_quantum_ns=day.moex_quantum_ns,
                common_seconds=sum(s.end_s - s.start_s for s in day.common),
                cells=cells,
                counts={width: bar_days[width].counts for width in widths},
            )
        )
        if not reasons:
            for width in widths:
                kept[width].append(bar_days[width])
        del bar_days
    if {report.trading_date for report in reports} != set(planned):
        raise AnalysisError("delivered days differ from the month plan")

    eligible = [report for report in reports if report.eligible]
    eligible_dates = [report.trading_date for report in eligible]
    zs: dict[tuple[int, int], list[float]] = {}
    pooled: dict[tuple[int, int], PairSums] = {}
    for width in widths:
        for lag in lags:
            values: list[float] = []
            total = ZERO_SUMS
            for report in eligible:
                cell = report.cells[(width, lag)]
                if cell.z is None:
                    raise AnalysisError("eligible day with an undefined cell")
                values.append(cell.z)
                total = total + cell.sums
            zs[(width, lag)] = values
            pooled[(width, lag)] = total

    inference = config.inference
    replicates = inference.replicates
    null_cells: dict[tuple[int, int], _NullCell] = {}
    null_summary: dict[str, object] = {
        "method": inference.null_method,
        "seed": inference.seed,
        "replicates": replicates,
        "computed": False,
    }
    if len(eligible) >= 2:
        perms = derangements(len(eligible), replicates, inference.seed)
        for width in widths:
            for lag in lags:
                matrix = z_matrix(kept[width], lag, config)
                for k, z in enumerate(zs[(width, lag)]):
                    if matrix[k][k] != z:
                        raise AnalysisError("null matrix diagonal differs from the day cells")
                values = zs[(width, lag)]
                mean_z = math.fsum(values) / len(values)
                null_cells[(width, lag)] = _null_cell(matrix, perms, mean_z)
        null_summary.update(
            {
                "computed": True,
                "n_days": len(eligible),
                "permutations_sha256": hashlib.sha256(repr(perms).encode("ascii")).hexdigest(),
                "shared_by_all_widths_and_lags": True,
            }
        )
    del kept

    forward_maxima: list[float | None] = []
    reverse_maxima: list[float | None] = []
    if null_cells:
        for b in range(replicates):
            forward_maxima.append(
                _max_or_none(null_cells[(w, forward)].t_replicates[b] for w in widths)
            )
            reverse_maxima.append(
                _max_or_none(
                    None if (t := null_cells[(w, reverse)].t_replicates[b]) is None else abs(t)
                    for w in widths
                )
            )
        null_summary["forward_undefined_maxima"] = sum(1 for m in forward_maxima if m is None)
        null_summary["reverse_undefined_maxima"] = sum(1 for m in reverse_maxima if m is None)

    stats: dict[tuple[int, int], WidthLagStat] = {}
    for width in widths:
        for lag in lags:
            values = zs[(width, lag)]
            cell_null = null_cells.get((width, lag))
            t_obs = None if cell_null is None else cell_null.t_observed
            p_unadjusted: float | None = None
            p_familywise: float | None = None
            if cell_null is not None and t_obs is not None:
                p_unadjusted = _p_value(_exceed_count(cell_null.t_replicates, t_obs), replicates)
                if lag == forward:
                    p_familywise = _p_value(_exceed_count(forward_maxima, t_obs), replicates)
                else:
                    p_familywise = _p_value(_exceed_count(reverse_maxima, abs(t_obs)), replicates)
            stats[(width, lag)] = WidthLagStat(
                width_s=width,
                lag=lag,
                statistic=equal_day_statistic(values),
                mean_z=None if not values else math.fsum(values) / len(values),
                n_days=len(values),
                pooled_r=pearson(pooled[(width, lag)], config.analysis.min_pairs_per_cell),
                pooled_n=pooled[(width, lag)].n,
                null_mean=None if cell_null is None else cell_null.mean,
                null_sd=None if cell_null is None else cell_null.sd,
                null_defined_replicates=0 if cell_null is None else cell_null.defined,
                t_value=t_obs,
                p_unadjusted_one_sided=p_unadjusted,
                p_familywise=p_familywise,
            )

    gate_passed = len(eligible) >= config.data_gate.min_eligible_days
    data_gate: dict[str, object] = {
        "eligible_days": len(eligible),
        "min_eligible_days": config.data_gate.min_eligible_days,
        "passed": gate_passed,
    }
    decisions = {
        width: _width_decision(width, stats, zs[(width, forward)], gate_passed, config)
        for width in widths
    }
    category: V03Category
    if not gate_passed:
        category = "DATA_INSUFFICIENT"
    elif any(item.passes for item in decisions.values()):
        category = "SCREEN_POSITIVE"
    else:
        category = "NO_SCREEN_SIGNAL"
    return MultiscaleResult(
        month=resolved_month,
        lo_s=lo,
        n_bars=n_bars,
        days=reports,
        stats=stats,
        widths=decisions,
        null=null_summary,
        data_gate=data_gate,
        category=category,
        eligible_days=eligible_dates,
    )


def _width_decision(
    width: int,
    stats: dict[tuple[int, int], WidthLagStat],
    forward_zs: Sequence[float],
    gate_passed: bool,
    config: MultiscaleConfig,
) -> WidthDecision:
    forward, reverse = config.lags
    decision = config.decision
    alpha = config.inference.familywise_alpha
    fw = stats[(width, forward)]
    rev = stats[(width, reverse)]
    positive_share = (
        None if not forward_zs else sum(1 for z in forward_zs if z > 0) / len(forward_zs)
    )
    share = strongest_day_share(forward_zs)
    stat_fw, stat_rev = fw.statistic, rev.statistic
    reverse_not_significant = rev.p_familywise is not None and rev.p_familywise > alpha
    reverse_small = (
        stat_fw is not None
        and stat_rev is not None
        and abs(stat_rev) <= decision.reverse_max_ratio * stat_fw
    )
    conditions = {
        "c1_forward_positive_and_familywise_significant": stat_fw is not None
        and stat_fw > 0
        and fw.p_familywise is not None
        and fw.p_familywise <= alpha,
        "c2_positive_day_share_at_least_min": positive_share is not None
        and positive_share >= decision.min_positive_day_share,
        "c3_strongest_day_share_below_max": share < decision.max_strongest_day_share,
        "c4_reverse_not_blocking": reverse_not_significant or reverse_small,
    }
    first_three = (
        conditions["c1_forward_positive_and_familywise_significant"]
        and conditions["c2_positive_day_share_at_least_min"]
        and conditions["c3_strongest_day_share_below_max"]
    )
    return WidthDecision(
        width_s=width,
        statistic_forward=stat_fw,
        statistic_reverse=stat_rev,
        p_fw=fw.p_familywise,
        p_rev=rev.p_familywise,
        positive_day_share=positive_share,
        strongest_day_share=share,
        conditions=conditions,
        passes=gate_passed and first_three and conditions["c4_reverse_not_blocking"],
        reverse_blocked=gate_passed and first_three and not conditions["c4_reverse_not_blocking"],
    )


def combined_decision(v02_category: str, v03: MultiscaleResult) -> CombinedDecision:
    """Combined H-0001 decision for GDZ6/GCZ6 (agreed with the user 2026-11-03)."""
    if v02_category not in CATEGORIES:
        raise AnalysisError(f"unknown v0.2 category {v02_category!r}")
    if v02_category == "SCREEN_POSITIVE" or v03.category == "SCREEN_POSITIVE":
        return "PROCEED"
    if any(item.reverse_blocked for item in v03.widths.values()):
        return "INVESTIGATE_COMMON_MOVE_OR_CLOCK"
    return "CLOSED_NOT_CONFIRMED"
