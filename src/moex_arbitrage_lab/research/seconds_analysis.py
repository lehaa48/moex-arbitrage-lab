"""Frozen seconds analysis of the H-0001 v0.2 screen (pure functions, stdlib only).

Returns: ``r_t = log(P_t / P_{t-1})`` from causal state closes of one venue, defined only when
seconds ``t - 1`` and ``t`` lie in the SAME common segment, both states exist and both ages are
``<= cap``. Pairing: COMEX return at ``t`` with MOEX return at ``t + lag``; both seconds must lie
in the same common segment. Positive lag => COMEX leads MOEX.

Per day/lag/cap: Pearson r, paired N, per-venue refresh and stale fractions, exclusion counts.
Month statistic: equal-day-weighted mean of Fisher-z daily r, back-transformed with tanh;
pooled r (sums over days) is a diagnostic only.

Frozen definitions (also recorded in the config and every artifact):

- stale/excluded fraction of a venue/day at a cap = 1 - valid returns / common-segment second
  slots (invalid = first second of a segment, no state at t or t-1, or age above the cap);
- day inclusion (``exclude_day``): reconciled, common segments present, MOEX timestamp quantum
  <= 1 s, stale fraction < 0.5 per venue at the primary cap, minimum paired N over the positive
  grid at the primary cap >= 3600, and r defined (non-degenerate) at every positive lag at the
  primary cap. These are data-quality rules; none uses a correlation value;
- strongest-day share = max_d(s * z_d) / |sum_d z_d| with s = sign(sum_d z_d), over included
  confirmation days at h* and the primary cap (+inf if the sum is 0);
- null: whole-day derangements; replicate statistic = tanh(mean_j z(COMEX day pi(j), MOEX day
  j)), both days on one time-of-day grid; one-sided threshold = ceil(q * R)-th smallest value;
- max-statistic: per replicate the maximum over the positive grid (same derangements);
- circular-shift placebo: each day's COMEX return arrays rotated by ``circular_shift_s`` on the
  time-of-day grid, paired with the same day's MOEX at h*;
- impossible-time/future-leak control: COMEX of the NEXT included day (same time of day, lag
  h*) paired with MOEX of day d - information that occurs about one day after the MOEX
  interval has completed, so it cannot causally precede it (expected null; an effect indicates
  a day/time alignment leak);
- a control "reproduces" the primary effect when its statistic is positive and at least the
  null threshold; the day-mismatch control reproduces it when the observed statistic does not
  exceed the null threshold.
"""

import math
import operator
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

from moex_arbitrage_lab.research.seconds_config import (
    NS_PER_SECOND,
    ScreenConfig,
    date_start_ns,
)
from moex_arbitrage_lab.research.seconds_series import CommonSegment, SecondRecord

Category = Literal[
    "SCREEN_POSITIVE", "NO_SCREEN_SIGNAL", "DATA_INSUFFICIENT", "TIMESTAMP_SEMANTICS_INSUFFICIENT"
]
CATEGORIES: Final = (
    "SCREEN_POSITIVE",
    "NO_SCREEN_SIGNAL",
    "DATA_INSUFFICIENT",
    "TIMESTAMP_SEMANTICS_INSUFFICIENT",
)
SECONDS_PER_DAY: Final = 86_400
_MUL: Final = operator.mul


class AnalysisError(ValueError):
    """The analysis inputs violate the frozen protocol."""


# --- inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VenueDayStates:
    """Causal states of one venue on one analysis day: second -> (log close, age ns), only for
    seconds inside the day's common segments that have a state; ``refreshed`` seconds."""

    states: Mapping[int, tuple[float, int]]
    refreshed: frozenset[int]


@dataclass(frozen=True, slots=True)
class AnalysisDay:
    trading_date: date
    common: tuple[CommonSegment, ...]
    moex: VenueDayStates
    comex: VenueDayStates
    moex_quantum_ns: int | None
    reconciled: bool


def venue_states_from_records(
    records: Iterable[SecondRecord], common: Sequence[CommonSegment], venue: str
) -> VenueDayStates:
    """States of one venue inside ``common``; each record's session must be the segment's."""
    states: dict[int, tuple[float, int]] = {}
    refreshed: set[int] = set()
    for record in records:
        for segment in common:
            if segment.start_s <= record.second_s < segment.end_s:
                expected = segment.moex_session if venue == "moex" else segment.comex_session
                if record.session_id != expected:
                    raise AnalysisError(f"{venue} second {record.second_s} in wrong session")
                if record.refreshed:
                    refreshed.add(record.second_s)
                if record.state_close is not None and record.age_ns is not None:
                    states[record.second_s] = (math.log(record.state_close), record.age_ns)
                break
    return VenueDayStates(states, frozenset(refreshed))


@dataclass(frozen=True, slots=True)
class Grid:
    """Time-of-day grid ``[lo, lo + length)`` in seconds relative to trading-date 00:00 UTC."""

    lo: int
    length: int

    @staticmethod
    def for_days(days: Sequence[AnalysisDay]) -> "Grid":
        return Grid.for_segments((day.trading_date, day.common) for day in days)

    @staticmethod
    def for_segments(items: Iterable[tuple[date, Sequence[CommonSegment]]]) -> "Grid":
        bounds = [
            (
                segment.start_s - date_start_ns(day) // NS_PER_SECOND,
                segment.end_s - date_start_ns(day) // NS_PER_SECOND,
            )
            for day, common in items
            for segment in common
        ]
        if not bounds:
            return Grid(0, 1)
        lo = min(b[0] for b in bounds)
        hi = max(b[1] for b in bounds)
        return Grid(lo, hi - lo)


# --- per-day arrays ----------------------------------------------------------------------------


@dataclass(slots=True)
class VenueArrays:
    ret: list[float]
    ok: list[float]
    sq: list[float]


@dataclass(frozen=True, slots=True)
class VenueCounts:
    """Aligned per-venue/day accounting: slots = valid + invalid (per cap)."""

    slots: int
    refreshed_seconds: int
    valid_returns: dict[int, int]
    stale_fraction: dict[int, float | None]
    invalid: dict[str, int]

    def as_json(self) -> dict[str, object]:
        return {
            "slots": self.slots,
            "refreshed_seconds": self.refreshed_seconds,
            "refresh_fraction": None if self.slots == 0 else self.refreshed_seconds / self.slots,
            "valid_returns": {str(cap): n for cap, n in self.valid_returns.items()},
            "stale_or_excluded_fraction": {
                str(cap): value for cap, value in self.stale_fraction.items()
            },
            "invalid": dict(self.invalid),
        }


@dataclass(slots=True)
class DayArrays:
    trading_date: date
    seg: list[int]
    slots: int
    comex: dict[int, VenueArrays]
    moex: dict[int, VenueArrays]
    counts: dict[str, VenueCounts]


def _venue_arrays(
    states: VenueDayStates,
    seg: Sequence[int],
    origin: int,
    caps_s: Sequence[int],
) -> tuple[dict[int, VenueArrays], VenueCounts]:
    length = len(seg)
    arrays = {cap: VenueArrays([0.0] * length, [0.0] * length, [0.0] * length) for cap in caps_s}
    limits = {cap: cap * NS_PER_SECOND for cap in caps_s}
    invalid: dict[str, int] = {"segment_first_second": 0, "no_state": 0}
    invalid.update({f"stale_cap_{cap}s": 0 for cap in caps_s})
    valid = dict.fromkeys(caps_s, 0)
    slots = 0
    refreshed = 0
    lookup = states.states
    for k in range(length):
        segment = seg[k]
        if segment < 0:
            continue
        slots += 1
        t = origin + k
        if t in states.refreshed:
            refreshed += 1
        if k == 0 or seg[k - 1] != segment:
            invalid["segment_first_second"] += 1
            continue
        current = lookup.get(t)
        previous = lookup.get(t - 1)
        if current is None or previous is None:
            invalid["no_state"] += 1
            continue
        age = max(current[1], previous[1])
        value = current[0] - previous[0]
        for cap in caps_s:
            if age <= limits[cap]:
                target = arrays[cap]
                target.ret[k] = value
                target.ok[k] = 1.0
                target.sq[k] = value * value
                valid[cap] += 1
            else:
                invalid[f"stale_cap_{cap}s"] += 1
    stale_fraction = {cap: (None if slots == 0 else 1.0 - valid[cap] / slots) for cap in caps_s}
    for cap in caps_s:
        # Stale counts are per cap; the other reasons apply to every cap.
        accounted = (
            valid[cap]
            + invalid["segment_first_second"]
            + invalid["no_state"]
            + invalid[f"stale_cap_{cap}s"]
        )
        if accounted != slots:
            raise AnalysisError("aligned reconciliation failed: slots != valid + invalid")
    counts = VenueCounts(slots, refreshed, valid, stale_fraction, invalid)
    return arrays, counts


def build_day_arrays(day: AnalysisDay, grid: Grid, caps_s: Sequence[int]) -> DayArrays:
    origin = date_start_ns(day.trading_date) // NS_PER_SECOND + grid.lo
    seg = [-1] * grid.length
    for index, segment in enumerate(day.common):
        a, b = segment.start_s - origin, segment.end_s - origin
        if a < 0 or b > grid.length:
            raise AnalysisError("common segment outside the analysis grid")
        if any(value != -1 for value in seg[a:b]):
            raise AnalysisError("common segments overlap")
        seg[a:b] = [index] * (b - a)
    comex, comex_counts = _venue_arrays(day.comex, seg, origin, caps_s)
    moex, moex_counts = _venue_arrays(day.moex, seg, origin, caps_s)
    slots = sum(1 for value in seg if value >= 0)
    return DayArrays(
        trading_date=day.trading_date,
        seg=seg,
        slots=slots,
        comex=comex,
        moex=moex,
        counts={"comex": comex_counts, "moex": moex_counts},
    )


def _shift(values: list[float], lag: int, fill: float = 0.0) -> list[float]:
    """``out[k] = values[k + lag]`` with ``fill`` outside the array."""
    length = len(values)
    if lag >= 0:
        return values[lag:] + [fill] * min(lag, length)
    return [fill] * min(-lag, length) + values[: max(length + lag, 0)]


def _pair_mask(seg: list[int], lag: int) -> list[float]:
    """1.0 where seconds k and k + lag lie in the same common segment."""
    length = len(seg)
    if lag >= 0:
        shifted = seg[lag:] + [-1] * min(lag, length)
    else:
        shifted = [-1] * min(-lag, length) + seg[: max(length + lag, 0)]
    return [1.0 if s >= 0 and s == s2 else 0.0 for s, s2 in zip(seg, shifted, strict=True)]


@dataclass(slots=True)
class ShiftedOutcome:
    """MOEX arrays shifted by ``lag`` and masked by the same-segment rule of ``seg``."""

    value: list[float]
    ok: list[float]
    sq: list[float]


def shifted_outcome(moex: VenueArrays, mask: list[float], lag: int) -> ShiftedOutcome:
    value = list(map(_MUL, _shift(moex.ret, lag), mask))
    ok = list(map(_MUL, _shift(moex.ok, lag), mask))
    return ShiftedOutcome(value, ok, list(map(_MUL, value, value)))


@dataclass(frozen=True, slots=True)
class PairSums:
    n: int
    sx: float
    sy: float
    sxx: float
    syy: float
    sxy: float

    def __add__(self, other: "PairSums") -> "PairSums":
        return PairSums(
            self.n + other.n,
            self.sx + other.sx,
            self.sy + other.sy,
            self.sxx + other.sxx,
            self.syy + other.syy,
            self.sxy + other.sxy,
        )


ZERO_SUMS: Final = PairSums(0, 0.0, 0.0, 0.0, 0.0, 0.0)


def pair_sums(predictor: VenueArrays, outcome: ShiftedOutcome) -> PairSums:
    return PairSums(
        n=round(math.sumprod(predictor.ok, outcome.ok)),
        sx=math.sumprod(predictor.ret, outcome.ok),
        sy=math.sumprod(predictor.ok, outcome.value),
        sxx=math.sumprod(predictor.sq, outcome.ok),
        syy=math.sumprod(predictor.ok, outcome.sq),
        sxy=math.sumprod(predictor.ret, outcome.value),
    )


def pearson(sums: PairSums, min_pairs: int) -> float | None:
    """Pearson r from sufficient statistics; None if undefined (too few pairs or no variance)."""
    n = sums.n
    if n < min_pairs:
        return None
    vx = n * sums.sxx - sums.sx * sums.sx
    vy = n * sums.syy - sums.sy * sums.sy
    if vx <= 0.0 or vy <= 0.0:
        return None
    r = (n * sums.sxy - sums.sx * sums.sy) / math.sqrt(vx * vy)
    return max(-1.0, min(1.0, r))


def fisher_z(r: float, clamp: float) -> float:
    return math.atanh(max(-clamp, min(clamp, r)))


def equal_day_statistic(zs: Sequence[float]) -> float | None:
    """tanh of the equal-weight mean of daily Fisher z values (None for no days)."""
    if not zs:
        return None
    return math.tanh(math.fsum(zs) / len(zs))


def strongest_day_share(zs: Sequence[float]) -> float:
    """max_d(s * z_d) / |sum_d z_d|, s = sign(sum); +inf if undefined (no days, zero sum)."""
    total = math.fsum(zs)
    if not zs or total == 0.0:
        return math.inf
    sign = 1.0 if total > 0 else -1.0
    return max(sign * z for z in zs) / abs(total)


def abs_z_share(zs: Sequence[float]) -> float:
    """Diagnostic only: max_d |z_d| / sum_d |z_d|."""
    total = math.fsum(abs(z) for z in zs)
    return math.inf if total == 0.0 else max(abs(z) for z in zs) / total


# --- per-day cells -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cell:
    trading_date: date
    lag_s: int
    cap_s: int
    sums: PairSums
    r: float | None
    z: float | None


def day_cells(
    arrays: DayArrays, lags: Sequence[int], caps_s: Sequence[int], config: ScreenConfig
) -> dict[tuple[int, int], Cell]:
    analysis = config.analysis
    cells: dict[tuple[int, int], Cell] = {}
    for lag in lags:
        mask = _pair_mask(arrays.seg, lag)
        for cap in caps_s:
            sums = pair_sums(arrays.comex[cap], shifted_outcome(arrays.moex[cap], mask, lag))
            r = pearson(sums, analysis.min_pairs_for_correlation)
            z = None if r is None else fisher_z(r, analysis.fisher_r_clamp)
            cells[(lag, cap)] = Cell(arrays.trading_date, lag, cap, sums, r, z)
    return cells


@dataclass(slots=True)
class DayReport:
    trading_date: date
    included: bool
    reasons: list[str]
    counts: dict[str, VenueCounts]
    min_paired_positive_grid: int
    cells: dict[tuple[int, int], Cell]


def day_quality(
    day: AnalysisDay,
    arrays: DayArrays,
    cells: Mapping[tuple[int, int], Cell],
    config: ScreenConfig,
) -> tuple[list[str], int]:
    """Reason codes that exclude the day (empty list = included) and min paired N."""
    gate = config.data_gate
    cap = config.analysis.primary_cap_s
    reasons: list[str] = []
    if not day.reconciled:
        reasons.append("reconciliation_failed")
    if not day.common or arrays.slots == 0:
        reasons.append("no_common_segment")
    if day.moex_quantum_ns is None:
        reasons.append("timestamp_quantum_missing")
    elif day.moex_quantum_ns > gate.max_timestamp_quantum_ns:
        reasons.append("timestamp_quantum_too_coarse")
    for venue in ("moex", "comex"):
        fraction = arrays.counts[venue].stale_fraction[cap]
        if fraction is None or fraction >= gate.max_stale_fraction:
            reasons.append(f"{venue}_stale_fraction_ge_max")
    positive = [cells[(lag, cap)] for lag in config.analysis.positive_grid_s]
    min_paired = min(cell.sums.n for cell in positive)
    if min_paired < gate.min_paired_seconds_per_day:
        reasons.append("paired_seconds_below_min")
    if any(cell.r is None for cell in positive):
        reasons.append("degenerate_correlation")
    return reasons, min_paired


@dataclass(frozen=True, slots=True)
class MonthStat:
    lag_s: int
    cap_s: int
    statistic: float | None
    mean_z: float | None
    n_days: int
    pooled_r: float | None
    pooled_n: int


@dataclass(slots=True)
class MonthAnalysis:
    month: str
    grid: Grid
    days: list[DayReport]
    stats: dict[tuple[int, int], MonthStat]
    # Primary-cap arrays of included days (time-of-day aligned), for the null and placebos.
    primary_arrays: dict[date, DayArrays] = field(default_factory=dict)

    @property
    def included(self) -> list[date]:
        return [report.trading_date for report in self.days if report.included]


def month_statistics(
    reports: Sequence[DayReport], lags: Sequence[int], caps_s: Sequence[int], config: ScreenConfig
) -> dict[tuple[int, int], MonthStat]:
    stats: dict[tuple[int, int], MonthStat] = {}
    included = [report for report in reports if report.included]
    for lag in lags:
        for cap in caps_s:
            cells = [report.cells[(lag, cap)] for report in included]
            zs = [cell.z for cell in cells if cell.z is not None]
            pooled = ZERO_SUMS
            for cell in cells:
                pooled = pooled + cell.sums
            stats[(lag, cap)] = MonthStat(
                lag_s=lag,
                cap_s=cap,
                statistic=equal_day_statistic(zs),
                mean_z=None if not zs else math.fsum(zs) / len(zs),
                n_days=len(zs),
                pooled_r=pearson(pooled, config.analysis.min_pairs_for_correlation),
                pooled_n=pooled.n,
            )
    return stats


def analyze_month(
    month: str,
    days: Iterable[AnalysisDay],
    config: ScreenConfig,
    *,
    keep_arrays: bool,
    grid: Grid | None = None,
) -> MonthAnalysis:
    """Per-day cells, quality and month statistics. With ``grid`` given, ``days`` may be a lazy
    iterable in ascending date sequence (one day in memory at a time); otherwise it is
    materialized to derive the grid."""
    analysis = config.analysis
    if grid is None:
        days = sorted(days, key=lambda d: d.trading_date)
        grid = Grid.for_days(days)
    reports: list[DayReport] = []
    kept: dict[date, DayArrays] = {}
    previous: date | None = None
    for day in days:
        if previous is not None and day.trading_date <= previous:
            raise AnalysisError("analysis days must be in strictly ascending date sequence")
        previous = day.trading_date
        arrays = build_day_arrays(day, grid, analysis.staleness_caps_s)
        cells = day_cells(arrays, analysis.lag_grid_s, analysis.staleness_caps_s, config)
        reasons, min_paired = day_quality(day, arrays, cells, config)
        for cell in cells.values():
            for venue in ("moex", "comex"):
                if cell.sums.n > arrays.counts[venue].valid_returns[cell.cap_s]:
                    raise AnalysisError("aligned reconciliation failed: pairs exceed returns")
        reports.append(
            DayReport(day.trading_date, not reasons, reasons, arrays.counts, min_paired, cells)
        )
        if keep_arrays and not reasons:
            primary = analysis.primary_cap_s
            kept[day.trading_date] = DayArrays(
                trading_date=day.trading_date,
                seg=arrays.seg,
                slots=arrays.slots,
                comex={primary: arrays.comex[primary]},
                moex={primary: arrays.moex[primary]},
                counts=arrays.counts,
            )
    stats = month_statistics(reports, analysis.lag_grid_s, analysis.staleness_caps_s, config)
    return MonthAnalysis(month, grid, reports, stats, kept)


# --- discovery ---------------------------------------------------------------------------------


def select_h_star(
    statistics: Mapping[int, float | None], positive_grid: Sequence[int]
) -> int | None:
    """argmax over the positive grid; ties -> smaller lag; computed even if all <= 0.
    None only when no lag has a defined statistic (no included discovery day)."""
    best: int | None = None
    best_value = -math.inf
    for lag in sorted(positive_grid):
        value = statistics.get(lag)
        if value is not None and value > best_value:
            best, best_value = lag, value
    return best


# --- null, controls, gate, decision ------------------------------------------------------------


def derangements(n: int, replicates: int, seed: int) -> list[tuple[int, ...]]:
    """Deterministic whole-day derangements (no day paired with itself)."""
    if n < 2:
        raise AnalysisError("a day-mismatch null needs at least two days")
    rng = random.Random(seed)  # noqa: S311 - deterministic research resampling, not security
    base = list(range(n))
    found: list[tuple[int, ...]] = []
    while len(found) < replicates:
        candidate = base[:]
        rng.shuffle(candidate)
        if all(candidate[k] != k for k in range(n)):
            found.append(tuple(candidate))
    return found


def upper_quantile(values: Sequence[float], q: float) -> float:
    """One-sided threshold: the ceil(q * R)-th smallest value."""
    ranked = sorted(values)
    index = max(math.ceil(q * len(ranked)) - 1, 0)
    return ranked[index]


def z_matrix(
    arrays: Sequence[DayArrays], lags: Sequence[int], cap: int, config: ScreenConfig
) -> dict[int, list[list[float | None]]]:
    """``m[lag][i][j]`` = Fisher z of COMEX day i x MOEX day j (time-of-day aligned; segment
    rule of MOEX day j); computed once, so replicates are cheap."""
    analysis = config.analysis
    matrix: dict[int, list[list[float | None]]] = {}
    for lag in lags:
        rows: list[list[float | None]] = [[None] * len(arrays) for _ in arrays]
        for j, outcome_day in enumerate(arrays):
            outcome = shifted_outcome(outcome_day.moex[cap], _pair_mask(outcome_day.seg, lag), lag)
            for i, predictor_day in enumerate(arrays):
                r = pearson(
                    pair_sums(predictor_day.comex[cap], outcome),
                    analysis.min_pairs_for_correlation,
                )
                rows[i][j] = None if r is None else fisher_z(r, analysis.fisher_r_clamp)
        matrix[lag] = rows
    return matrix


def permutation_null(
    matrix: Mapping[int, list[list[float | None]]], perms: Sequence[tuple[int, ...]]
) -> dict[int, list[float]]:
    null: dict[int, list[float]] = {}
    for lag, rows in matrix.items():
        values: list[float] = []
        for perm in perms:
            zs = [rows[perm[j]][j] for j in range(len(perm))]
            defined = [z for z in zs if z is not None]
            values.append(math.nan if not defined else math.tanh(math.fsum(defined) / len(defined)))
        null[lag] = values
    return null


def circular_shift_statistic(
    arrays: Sequence[DayArrays], lag: int, shift_s: int, cap: int, config: ScreenConfig
) -> float | None:
    analysis = config.analysis
    zs: list[float] = []
    for day in arrays:
        source = day.comex[cap]
        length = len(source.ret)
        s = shift_s % length
        rotated = VenueArrays(
            source.ret[s:] + source.ret[:s],
            source.ok[s:] + source.ok[:s],
            source.sq[s:] + source.sq[:s],
        )
        outcome = shifted_outcome(day.moex[cap], _pair_mask(day.seg, lag), lag)
        r = pearson(pair_sums(rotated, outcome), analysis.min_pairs_for_correlation)
        if r is not None:
            zs.append(fisher_z(r, analysis.fisher_r_clamp))
    return equal_day_statistic(zs)


def impossible_time_statistic(
    matrix_lag: list[list[float | None]],
) -> tuple[float | None, int]:
    """COMEX of the next included day i = j + 1 predicting MOEX of day j (matrix[i][j])."""
    zs = [matrix_lag[j + 1][j] for j in range(len(matrix_lag) - 1)]
    defined = [z for z in zs if z is not None]
    return equal_day_statistic(defined), len(defined)


@dataclass(frozen=True, slots=True)
class GateInputs:
    included_days: int
    min_paired_each_day: int | None
    max_stale_fraction_seen: float | None
    timestamp_semantics_ok: bool
    session_map_published: bool
    reconciliation_ok: bool
    strongest_day_share: float


def evaluate_data_gate(inputs: GateInputs, config: ScreenConfig) -> dict[str, bool]:
    gate = config.data_gate
    items = {
        "min_confirmation_days": inputs.included_days >= gate.min_confirmation_days,
        "min_paired_seconds_each_day": inputs.min_paired_each_day is not None
        and inputs.min_paired_each_day >= gate.min_paired_seconds_per_day,
        "stale_fraction_below_max": inputs.max_stale_fraction_seen is not None
        and inputs.max_stale_fraction_seen < gate.max_stale_fraction,
        "timestamp_semantics_and_session_map_published": inputs.timestamp_semantics_ok
        and inputs.session_map_published,
        "reconciliation_without_unresolved_mismatch": inputs.reconciliation_ok,
        "strongest_day_share_below_max": inputs.strongest_day_share < gate.max_strongest_day_share,
    }
    items["passed"] = all(items.values())
    return items


@dataclass(frozen=True, slots=True)
class DecisionInputs:
    gate: Mapping[str, bool]
    timestamp_semantics_ok: bool
    h_star: int | None
    primary_statistic: float | None
    null_threshold: float | None
    positive_day_share: float | None
    reverse_statistic: float | None
    circular_shift_statistic: float | None
    impossible_time_statistic: float | None
    cap_statistics: Mapping[int, float | None]
    strongest_day_share: float


def _reproduces(control: float | None, threshold: float | None) -> bool:
    # Undefined control or threshold cannot show that the effect is not reproduced.
    if control is None or threshold is None or math.isnan(control):
        return True
    return control > 0 and control >= threshold


def decide(inputs: DecisionInputs, config: ScreenConfig) -> tuple[Category, dict[str, bool]]:
    decision = config.decision
    primary = inputs.primary_statistic
    threshold = inputs.null_threshold
    above_null = (
        primary is not None and threshold is not None and primary > 0 and primary > threshold
    )
    caps = [cap for cap in config.analysis.staleness_caps_s if cap in inputs.cap_statistics]
    positive_caps = [
        cap for cap in caps if (value := inputs.cap_statistics[cap]) is not None and value > 0
    ]
    conditions = {
        "c1_data_gate_passed": bool(inputs.gate.get("passed", False)),
        "c2_primary_positive_above_null_threshold": above_null,
        "c3_positive_day_share_at_least_min": inputs.positive_day_share is not None
        and inputs.positive_day_share >= decision.min_positive_day_share,
        "c4_reverse_weaker_in_absolute_value": primary is not None
        and inputs.reverse_statistic is not None
        and abs(inputs.reverse_statistic) < abs(primary),
        "c5_controls_do_not_reproduce": (
            above_null
            and not _reproduces(inputs.circular_shift_statistic, threshold)
            and not _reproduces(inputs.impossible_time_statistic, threshold)
        ),
        "c6_sign_kept_across_caps": len(positive_caps) >= decision.min_caps_same_sign
        and decision.required_cap_s in positive_caps,
        "c7_no_single_day_domination": inputs.strongest_day_share < decision.max_single_day_share,
    }
    category: Category
    if not inputs.timestamp_semantics_ok:
        category = "TIMESTAMP_SEMANTICS_INSUFFICIENT"
    elif not conditions["c1_data_gate_passed"] or inputs.h_star is None:
        category = "DATA_INSUFFICIENT"
    elif all(conditions.values()):
        category = "SCREEN_POSITIVE"
    else:
        category = "NO_SCREEN_SIGNAL"
    return category, conditions


def finite_or_none(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else value
