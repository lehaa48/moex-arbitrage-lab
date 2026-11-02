"""Offline workflows and CLI of the H-0001 v0.2 seconds screen.

    python -m moex_arbitrage_lab.research.seconds_screen build-seconds \\
        --config config/experiments/h0001_seconds_screen_v0_2.toml \\
        --inputs <root-relative inputs JSON> --run-id <id> --code-commit <sha>
    python -m moex_arbitrage_lab.research.seconds_screen discover \\
        --config ... --seconds-run-id <id> --analysis-run-id <id> --code-commit <sha>
    python -m moex_arbitrage_lab.research.seconds_screen confirm \\
        --config ... --seconds-run-id <id> --analysis-run-id <id> --code-commit <sha>

No step contacts any host. ``discover`` reads only discovery-month seconds (``MonthGuard``) and
refuses to run once any confirmation was opened under the same config. ``confirm`` requires the
frozen, checksum-verified discovery artifact with the same config hash, seconds manifest and
research-code hash, writes a create-once ledger marker BEFORE computing any October outcome,
and refuses a second confirmation of the same discovery.

Inputs JSON (``h0001_seconds_inputs_v1``)::

    {"inputs_version": "h0001_seconds_inputs_v1",
     "moex_days": [{"trading_date": "2026-09-02",
                    "trades_csv": "data/normalized/<...>.csv",   # event_ts_utc_ns,price,size
                    "raw_sha256": ["<sha256 of the immutable raw payload>"],
                    "segments": [{"name": "main", "start_utc_ns": 0, "end_utc_ns": 0,
                                  "trading_date": "2026-09-02"}]}],
     "comex_ohlcv_csv": ["data/raw/<...>.csv"]}

``segments`` are the MOEX continuous segments of the date (opening auctions are not
continuous segments). Trades must be the eligible trades in input sequence.
"""

import argparse
import csv
import io
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from moex_arbitrage_lab.research.seconds_analysis import (
    DecisionInputs,
    GateInputs,
    Grid,
    MonthAnalysis,
    abs_z_share,
    analyze_month,
    circular_shift_statistic,
    decide,
    derangements,
    equal_day_statistic,
    evaluate_data_gate,
    finite_or_none,
    impossible_time_statistic,
    permutation_null,
    select_h_star,
    strongest_day_share,
    upper_quantile,
    z_matrix,
)
from moex_arbitrage_lab.research.seconds_config import (
    NS_PER_SECOND,
    ScreenConfig,
    load_screen_config,
)
from moex_arbitrage_lab.research.seconds_series import (
    Segment,
    SeriesResult,
    assign_comex_bars,
    build_comex_interval,
    build_moex_day,
    combine_comex_bars,
    comex_open_intervals,
    common_segments,
    read_comex_ohlcv_csv,
    read_moex_trades_csv,
    seconds_to_csv,
)
from moex_arbitrage_lab.research.seconds_store import (
    SECONDS_MANIFEST,
    SECONDS_MANIFEST_VERSION,
    CreateOnceError,
    HoldoutGuardError,
    IntegrityError,
    MonthGuard,
    SecondsRun,
    StoreError,
    WrittenFile,
    analysis_relative,
    check_code_commit,
    check_run_id,
    code_sha256,
    ledger_relative,
    read_json_with_checksum,
    resolve_config_file,
    resolve_project_file,
    seconds_relative,
    sha256_bytes,
    utc_now_text,
    write_create_once,
    write_json_with_checksum,
)
from moex_arbitrage_lab.settings.raw_publication import verify_published
from moex_arbitrage_lab.settings.storage_paths import (
    ProjectPaths,
    StorageCategory,
    StoragePathError,
    resolve_project_paths,
)
from moex_arbitrage_lab.sources.tbank_history.period import load_template
from moex_arbitrage_lab.sources.tbank_history.period_acquire import PeriodReport
from moex_arbitrage_lab.sources.tbank_history.period_qa import load_period_trades

DISCOVERY_VERSION: Final = "h0001_seconds_discovery_v1"
CONFIRMATION_VERSION: Final = "h0001_seconds_confirmation_v1"
LEDGER_VERSION: Final = "h0001_seconds_confirmation_opened_v1"
DISCOVERY_FILE: Final = "discovery.json"
CONFIRMATION_FILE: Final = "confirmation.json"
TIME_SEMANTICS: Final = {
    "second": "half-open [t, t+1s), t = integer UTC epoch seconds",
    "availability": "a bar close is available only at t+1s (end of its second)",
    "carry": "last known close carried only within the same continuous segment; never across "
    "segment/break/day; nothing backfilled from later observations",
    "age": "end of second t minus the time of the observation defining the state",
    "moex_observation_time": "exact raw trade timestamp (TRADE_TS, UTC ns) of the last trade",
    "comex_observation_time": "Databento ohlcv-1s ts_event = START of the 1 s bar; bars are built "
    "from ts_recv of the source trade messages, i.e. provider observation (receive) time, not "
    "matching-engine event time; age is an upper bound (refreshed second = 1000 ms)",
    "comex_trade_count": "not provided by ohlcv-1s; null",
    "returns": "r_t = log(P_t/P_{t-1}) within one common segment, both ages <= cap",
    "lag": "COMEX return at t paired with MOEX return at t+lag; positive lag => COMEX leads",
}
LIMITATIONS: Final = (
    "COMEX ohlcv-1s bars are timestamped by Databento receive time (ts_recv), not exchange "
    "event time; lags are provider-observation lags.",
    "MOEX T-Bank TRADE_TS clock accuracy and capture point are undocumented.",
    "One-second resolution: sub-second sequencing is not resolved by this screen.",
    "Trades/bars only: a positive screen is predictive evidence at most and makes no claim "
    "about quotes, costs or tradability.",
)

Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SegmentSpec(_Spec):
    name: str
    start_utc_ns: int
    end_utc_ns: int
    trading_date: date


class MoexDaySpec(_Spec):
    trading_date: date
    trades_csv: str
    raw_sha256: tuple[Sha256Text, ...] = Field(min_length=1)
    segments: tuple[SegmentSpec, ...] = Field(min_length=1)


class InputsSpec(_Spec):
    inputs_version: Literal["h0001_seconds_inputs_v1"]
    moex_days: tuple[MoexDaySpec, ...] = ()
    tbank_period_template: str | None = None
    tbank_acquisition_report: str | None = None
    comex_ohlcv_csv: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_moex_source(self) -> Self:
        raw_pair = (
            self.tbank_period_template is not None and self.tbank_acquisition_report is not None
        )
        partial_pair = (self.tbank_period_template is None) != (
            self.tbank_acquisition_report is None
        )
        if partial_pair:
            raise ValueError("tbank raw mode requires both template and acquisition report")
        if bool(self.moex_days) == raw_pair:
            raise ValueError("provide exactly one MOEX source: moex_days or verified T-Bank raw")
        return self


@dataclass(frozen=True, slots=True)
class MoexDayInput:
    trading_date: date
    trades: Sequence[tuple[int, Decimal, Decimal]]
    segments: tuple[Segment, ...]
    input_files: tuple[dict[str, object], ...]
    raw_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComexFileInput:
    path: str
    data: bytes


# --- day universe ------------------------------------------------------------------------------


def day_universe(config: ScreenConfig) -> list[dict[str, object]]:
    """Every calendar date of the period with its pre-outcome primary status."""
    exclusions = {item.date: item.reason for item in config.day_universe.primary_exclusions}
    first = config.period.start_utc.date()
    last = (config.period.end_utc - timedelta(microseconds=1)).date()
    days: list[dict[str, object]] = []
    day = first
    while day <= last:
        reason: str | None = None
        if day.weekday() >= 5:
            reason = config.day_universe.weekend_reason
        elif day in exclusions:
            reason = exclusions[day]
        days.append(
            {
                "date": day.isoformat(),
                "weekday": day.strftime("%A").lower(),
                "primary_candidate": reason is None,
                "calendar_exclusion_reason": reason,
            }
        )
        day += timedelta(days=1)
    return days


# --- build-seconds -----------------------------------------------------------------------------


def _series_qa(result: SeriesResult) -> dict[str, object]:
    coverage: list[dict[str, object]] = []
    carry_checks: list[dict[str, object]] = []
    for segment in result.segments:
        records = [
            record
            for record in result.records
            if segment.start_s <= record.second_s < segment.end_s
        ]
        refreshed = sum(1 for record in records if record.refreshed)
        coverage.append(
            {
                "session_id": segment.name,
                "start_utc_ns": segment.start_ns,
                "end_utc_ns": segment.end_ns,
                "expected_seconds": segment.seconds,
                "seconds_rows": len(records),
                "refreshed_seconds": refreshed,
                "missing_observation_seconds": len(records) - refreshed,
            }
        )
        no_prior_state = all(
            record.state_obs_ts_ns is None or record.state_obs_ts_ns >= segment.start_ns
            for record in records
        )
        carry_checks.append(
            {
                "session_id": segment.name,
                "first_second_utc_ns": segment.start_ns,
                "no_state_observation_before_segment_start": no_prior_state,
            }
        )
    return {
        "input_observations": result.input_observations,
        "assigned_observations": result.assigned_observations,
        "seconds_rows": len(result.records),
        "refreshed_seconds": result.refreshed_seconds,
        "missing_observation_seconds": len(result.records) - result.refreshed_seconds,
        "min_second_utc_ns": None
        if not result.records
        else result.records[0].second_s * NS_PER_SECOND,
        "max_second_utc_ns": None
        if not result.records
        else result.records[-1].second_s * NS_PER_SECOND,
        "session_coverage": coverage,
        "carry_boundary_checks": carry_checks,
        "no_carry_across_boundaries": all(
            bool(item["no_state_observation_before_segment_start"]) for item in carry_checks
        ),
    }


def build_seconds_run(
    paths: ProjectPaths,
    config: ScreenConfig,
    config_sha256: str,
    moex_days: Sequence[MoexDayInput],
    comex_files: Sequence[ComexFileInput],
    run_id: str,
    code_commit: str,
) -> dict[str, object]:
    check_run_id(run_id)
    check_code_commit(code_commit)
    run_dir = paths.resolve(StorageCategory.ALIGNED, seconds_relative(run_id, "x")).parent
    if run_dir.exists():
        raise CreateOnceError(f"seconds run {run_id} already exists")
    dates = [day.trading_date for day in moex_days]
    if len(set(dates)) != len(dates):
        raise StoreError("duplicate MOEX trading date in inputs")
    first = config.period.start_utc.date()
    last = (config.period.end_utc - timedelta(microseconds=1)).date()
    if any(not first <= d <= last for d in dates):
        raise StoreError("MOEX trading date outside the period")

    parsed = [
        read_comex_ohlcv_csv(f.data, config.comex, index) for index, f in enumerate(comex_files)
    ]
    comex_inputs = [
        {"path": f.path, "sha256": sha256_bytes(f.data), "bytes": len(f.data), "bars": len(p)}
        for f, p in zip(comex_files, parsed, strict=True)
    ]
    bars, instrument_id, publisher_id = combine_comex_bars(parsed)
    del parsed
    bars_total = len(bars)
    intervals = comex_open_intervals(config.comex, config.period)
    needed = frozenset(
        segment.comex_session
        for day in moex_days
        for segment in common_segments(day.segments, intervals, config.period)
    )
    groups, comex_excluded = assign_comex_bars(bars, intervals, config.period)
    del bars
    bars_per_interval = {segment.name: len(group) for segment, group in groups}
    if sum(bars_per_interval.values()) + sum(comex_excluded.values()) != bars_total:
        raise StoreError("comex: bars != assigned + excluded")

    comex_entries: list[dict[str, object]] = []
    outputs: list[WrittenFile] = []
    # One interval at a time: build, write, release (bounded memory).
    for segment, group in groups:
        if segment.name not in needed:
            continue
        result = build_comex_interval(segment, group)
        written = write_create_once(
            paths,
            StorageCategory.ALIGNED,
            seconds_relative(run_id, f"comex/{segment.trading_date.isoformat()}.csv"),
            seconds_to_csv(result.records),
        )
        outputs.append(written)
        comex_entries.append(
            {
                "session_id": segment.name,
                "cme_trade_date": segment.trading_date.isoformat(),
                "start_utc_ns": segment.start_ns,
                "end_utc_ns": segment.end_ns,
                "reconciliation": result.reconciliation,
                "timestamps": result.timestamps.as_json(),
                "qa": _series_qa(result),
                "output": {**written.as_json(), "rows": len(result.records)},
            }
        )

    moex_entries: list[dict[str, object]] = []
    for day in sorted(moex_days, key=lambda d: d.trading_date):
        result = build_moex_day(day.trading_date, day.trades, day.segments)
        common = common_segments(result.segments, intervals, config.period)
        written = write_create_once(
            paths,
            StorageCategory.ALIGNED,
            seconds_relative(run_id, f"moex/{day.trading_date.isoformat()}.csv"),
            seconds_to_csv(result.records),
        )
        outputs.append(written)
        moex_entries.append(
            {
                "trading_date": day.trading_date.isoformat(),
                "segments": [
                    {"name": s.name, "start_utc_ns": s.start_ns, "end_utc_ns": s.end_ns}
                    for s in result.segments
                ],
                "common_segments": [segment.as_json() for segment in common],
                "common_seconds": sum(segment.end_s - segment.start_s for segment in common),
                "reconciliation": result.reconciliation,
                "timestamps": result.timestamps.as_json(),
                "qa": _series_qa(result),
                "input_files": list(day.input_files),
                "raw_sha256": list(day.raw_sha256),
                "output": {**written.as_json(), "rows": len(result.records)},
            }
        )

    with_input = {day.isoformat() for day in dates}
    universe = day_universe(config)
    for item in universe:
        item["moex_input"] = item["date"] in with_input
    assigned = sum(bars_per_interval.values())
    manifest: dict[str, object] = {
        "manifest_version": SECONDS_MANIFEST_VERSION,
        "run_id": run_id,
        "created_utc": utc_now_text(),
        "code_commit": code_commit,
        "code_sha256": code_sha256(),
        "config_sha256": config_sha256,
        "period": {"start_utc_ns": config.period.start_ns, "end_utc_ns": config.period.end_ns},
        "time_semantics": TIME_SEMANTICS,
        "comex_identity": {
            "dataset": config.comex.dataset,
            "schema": config.comex.schema_name,
            "raw_symbol": config.comex.raw_symbol,
            "rtype": config.comex.expected_rtype,
            "instrument_id": instrument_id,
            "publisher_id": publisher_id,
            "bar_interval_ns": NS_PER_SECOND,
        },
        "comex_inputs": comex_inputs,
        "comex_bars_total": bars_total,
        "comex_bars_assigned": assigned,
        "comex_excluded": comex_excluded,
        "comex_reconciled": assigned + sum(comex_excluded.values()) == bars_total,
        "comex_intervals": comex_entries,
        # Intervals without overlap with any MOEX input segment get no seconds file.
        "comex_intervals_not_built": [
            {"session_id": s.name, "bars": bars_per_interval[s.name], "reason": "no_moex_overlap"}
            for s in intervals
            if s.name not in needed
        ],
        "moex_days": moex_entries,
        "day_universe": universe,
        "outputs_sha256": sorted(w.sha256 for w in outputs),
    }
    write_json_with_checksum(
        paths, StorageCategory.ALIGNED, seconds_relative(run_id, SECONDS_MANIFEST), manifest
    )
    return manifest


def load_inputs_spec(
    paths: ProjectPaths, relative: str
) -> tuple[list[MoexDayInput], list[ComexFileInput]]:
    spec_path = resolve_project_file(paths, relative)
    spec = InputsSpec.model_validate_json(spec_path.read_bytes())
    moex: list[MoexDayInput] = []
    if spec.moex_days:
        for day in spec.moex_days:
            data = resolve_project_file(paths, day.trades_csv).read_bytes()
            segments = tuple(
                Segment(s.name, s.start_utc_ns, s.end_utc_ns, s.trading_date) for s in day.segments
            )
            moex.append(
                MoexDayInput(
                    trading_date=day.trading_date,
                    trades=read_moex_trades_csv(data),
                    segments=segments,
                    input_files=(
                        {
                            "role": "normalized_integration_fixture",
                            "path": day.trades_csv,
                            "sha256": sha256_bytes(data),
                            "bytes": len(data),
                        },
                    ),
                    raw_sha256=day.raw_sha256,
                )
            )
    else:
        template_relative = spec.tbank_period_template
        report_relative = spec.tbank_acquisition_report
        if template_relative is None or report_relative is None:
            raise StoreError("verified T-Bank raw inputs are incomplete")
        template_path = resolve_config_file(paths, template_relative)
        report_path = resolve_project_file(paths, report_relative)
        loaded = load_template(template_path)
        report_bytes = report_path.read_bytes()
        report = PeriodReport.model_validate_json(report_bytes)
        report_sha = sha256_bytes(report_bytes)
        for raw_day in load_period_trades(paths, report, loaded):
            raw_path = paths.resolve(StorageCategory.RAW, raw_day.raw_relative)
            trades: list[tuple[int, Decimal, Decimal]] = []
            for trade in raw_day.continuous_eligible():
                if trade.event_ts_utc_ns is None:
                    raise StoreError(
                        f"{raw_day.trading_date}: eligible trade has no event timestamp"
                    )
                trades.append((trade.event_ts_utc_ns, trade.price, trade.size))
            segments = tuple(
                Segment(window.name, window.start_utc_ns, window.end_utc_ns, window.trading_date)
                for window in raw_day.continuous_windows
            )
            moex.append(
                MoexDayInput(
                    trading_date=raw_day.trading_date,
                    trades=trades,
                    segments=segments,
                    input_files=(
                        {
                            "role": "verified_raw_payload",
                            "path": f"data/raw/{raw_day.raw_relative}",
                            "sha256": raw_day.payload_sha256,
                            "bytes": raw_path.stat().st_size,
                            "instrument_uid": raw_day.instrument_uid,
                            "eligibility": "TRADE_SOURCE == 'EXCHANGE'; continuous sessions only",
                        },
                        {
                            "role": "tbank_acquisition_report",
                            "path": report_relative,
                            "sha256": report_sha,
                            "bytes": len(report_bytes),
                        },
                        {
                            "role": "tbank_period_template",
                            "path": template_relative,
                            "sha256": loaded.template_sha256,
                            "calendar_sha256": loaded.calendar_sha256,
                        },
                    ),
                    raw_sha256=(raw_day.payload_sha256,),
                )
            )
    comex: list[ComexFileInput] = []
    for path in spec.comex_ohlcv_csv:
        if path.startswith("data/raw/databento/"):
            relative_raw = path.removeprefix("data/raw/")
            manifest = verify_published(paths, relative_raw)
            data = paths.resolve(StorageCategory.RAW, relative_raw).read_bytes()
            if manifest.payload_sha256 != sha256_bytes(data):
                raise IntegrityError(f"{path}: raw publication SHA-256 mismatch")
        else:
            data = resolve_project_file(paths, path).read_bytes()
        comex.append(ComexFileInput(path, data))
    return moex, comex


# --- serialization of analysis -----------------------------------------------------------------


def _month_stats_json(analysis: MonthAnalysis) -> list[dict[str, object]]:
    return [
        {
            "lag_s": stat.lag_s,
            "cap_s": stat.cap_s,
            "equal_day_r": finite_or_none(stat.statistic),
            "mean_fisher_z": finite_or_none(stat.mean_z),
            "n_days": stat.n_days,
            "pooled_r_diagnostic": finite_or_none(stat.pooled_r),
            "pooled_n": stat.pooled_n,
        }
        for (_lag, _cap), stat in sorted(analysis.stats.items())
    ]


def _days_json(analysis: MonthAnalysis) -> list[dict[str, object]]:
    return [
        {
            "trading_date": report.trading_date.isoformat(),
            "included": report.included,
            "exclusion_reasons": report.reasons,
            "min_paired_positive_grid_primary_cap": report.min_paired_positive_grid,
            "counts": {venue: c.as_json() for venue, c in report.counts.items()},
        }
        for report in analysis.days
    ]


def _cells_csv(analysis: MonthAnalysis) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("trading_date", "included", "lag_s", "cap_s", "paired_n", "r", "fisher_z"))
    for report in analysis.days:
        for (lag, cap), cell in sorted(report.cells.items()):
            writer.writerow(
                (
                    report.trading_date.isoformat(),
                    "true" if report.included else "false",
                    lag,
                    cap,
                    cell.sums.n,
                    "" if cell.r is None else repr(cell.r),
                    "" if cell.z is None else repr(cell.z),
                )
            )
    return buffer.getvalue().encode("utf-8")


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _data_manifest(run: SecondsRun, month: str) -> dict[str, object]:
    days = [
        item
        for item in _as_list(run.manifest.get("moex_days"))
        if isinstance(item, dict) and str(item.get("trading_date", "")).startswith(month)
    ]
    return {
        "seconds_run_id": run.run_id,
        "seconds_manifest_sha256": run.manifest_sha256,
        "moex_raw_sha256": {str(d["trading_date"]): d.get("raw_sha256") for d in days},
        "comex_inputs": run.manifest.get("comex_inputs"),
    }


# --- discover ----------------------------------------------------------------------------------


def _ledger_markers(paths: ProjectPaths) -> list[dict[str, object]]:
    relative = ledger_relative("0" * 64).rsplit("/", 1)[0]
    directory = paths.resolve(StorageCategory.REPORTS, relative)
    if not directory.exists():
        return []
    return [
        read_json_with_checksum(paths, StorageCategory.REPORTS, f"{relative}/{path.name}")[0]
        for path in sorted(directory.glob("*.opened.json"))
    ]


def _require_run_code_identity(run: SecondsRun, code_commit: str) -> None:
    if run.manifest.get("code_commit") != code_commit:
        raise IntegrityError("code commit differs from the seconds build")
    if run.manifest.get("code_sha256") != code_sha256():
        raise IntegrityError("research code differs from the seconds build")


def run_discovery(
    paths: ProjectPaths,
    config: ScreenConfig,
    config_sha256: str,
    seconds_run_id: str,
    analysis_run_id: str,
    code_commit: str,
) -> dict[str, object]:
    check_code_commit(code_commit)
    for marker in _ledger_markers(paths):
        if marker.get("config_sha256") == config_sha256:
            raise HoldoutGuardError(
                "a confirmation was already opened under this config; re-discovery would tune "
                "on the confirmation month"
            )
    month = config.period.discovery_month
    run = SecondsRun.open(paths, seconds_run_id, config_sha256)
    _require_run_code_identity(run, code_commit)
    guard = MonthGuard(month)
    plan, missing = run.month_plan(month, guard)
    grid = Grid.for_segments(plan)
    analysis = analyze_month(
        month, run.iter_days(plan, guard), config, keep_arrays=False, grid=grid
    )
    primary = config.analysis.primary_cap_s
    positive = {
        lag: analysis.stats[(lag, primary)].statistic for lag in config.analysis.positive_grid_s
    }
    h_star = select_h_star(positive, config.analysis.positive_grid_s)
    artifact: dict[str, object] = {
        "artifact_version": DISCOVERY_VERSION,
        "created_utc": utc_now_text(),
        "discovery_month": month,
        "h_star_s": h_star,
        "h_star_rule": "argmax of the equal-day Fisher-z statistic over the positive grid at the "
        "primary cap; ties -> smaller lag; chosen even if all values <= 0",
        "positive_grid_statistics": {str(k): finite_or_none(v) for k, v in positive.items()},
        "primary_cap_s": primary,
        "code_commit": code_commit,
        "code_sha256": code_sha256(),
        "config_sha256": config_sha256,
        "data_manifest": _data_manifest(run, month),
        "included_days": [d.isoformat() for d in analysis.included],
        "days": _days_json(analysis),
        "missing_primary_days": missing,
        "calendar": run.day_universe(month),
        "month_statistics": _month_stats_json(analysis),
        "files_opened": sorted(set(run.opened)),
    }
    write_create_once(
        paths,
        StorageCategory.REPORTS,
        analysis_relative(analysis_run_id, "discovery-day-lag-cap.csv"),
        _cells_csv(analysis),
    )
    written = write_json_with_checksum(
        paths, StorageCategory.REPORTS, analysis_relative(analysis_run_id, DISCOVERY_FILE), artifact
    )
    artifact["artifact_sha256"] = written.sha256
    return artifact


# --- confirm -----------------------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def run_confirmation(
    paths: ProjectPaths,
    config: ScreenConfig,
    config_sha256: str,
    seconds_run_id: str,
    analysis_run_id: str,
    code_commit: str,
) -> dict[str, object]:
    check_code_commit(code_commit)
    discovery, discovery_sha = read_json_with_checksum(
        paths, StorageCategory.REPORTS, analysis_relative(analysis_run_id, DISCOVERY_FILE)
    )
    _require(discovery.get("artifact_version") == DISCOVERY_VERSION, "not a discovery artifact")
    _require(discovery.get("config_sha256") == config_sha256, "config changed since discovery")
    _require(discovery.get("code_commit") == code_commit, "code commit changed since discovery")
    _require(discovery.get("code_sha256") == code_sha256(), "research code changed since discovery")
    data_manifest = discovery.get("data_manifest")
    if not isinstance(data_manifest, dict):
        raise IntegrityError("discovery data manifest missing")
    run = SecondsRun.open(paths, seconds_run_id, config_sha256)
    _require_run_code_identity(run, code_commit)
    _require(
        data_manifest.get("seconds_manifest_sha256") == run.manifest_sha256,
        "seconds manifest differs from the one frozen at discovery",
    )
    h_star_value = discovery.get("h_star_s")
    on_grid = (
        isinstance(h_star_value, int)
        and not isinstance(h_star_value, bool)
        and h_star_value in config.analysis.positive_grid_s
    )
    _require(h_star_value is None or on_grid, "discovery h* is not on the frozen positive grid")
    h_star = h_star_value if on_grid and isinstance(h_star_value, int) else None
    if paths.resolve(
        StorageCategory.REPORTS, analysis_relative(analysis_run_id, CONFIRMATION_FILE)
    ).exists():
        raise HoldoutGuardError("a confirmation artifact already exists for this analysis run")
    try:
        write_json_with_checksum(
            paths,
            StorageCategory.REPORTS,
            ledger_relative(discovery_sha),
            {
                "ledger_version": LEDGER_VERSION,
                "discovery_sha256": discovery_sha,
                "analysis_run_id": analysis_run_id,
                "config_sha256": config_sha256,
                "code_commit": code_commit,
                "h_star_s": h_star,
                "opened_utc": utc_now_text(),
            },
        )
    except CreateOnceError:
        raise HoldoutGuardError(
            "the confirmation month was already opened for this discovery; refusing a second "
            "confirmation"
        ) from None

    month = config.period.confirmation_month
    plan, missing = run.month_plan(month, None)
    grid = Grid.for_segments(plan)
    analysis = analyze_month(month, run.iter_days(plan, None), config, keep_arrays=True, grid=grid)
    result = confirmation_result(analysis, h_star, discovery, config, run)
    result.update(
        {
            "artifact_version": CONFIRMATION_VERSION,
            "created_utc": utc_now_text(),
            "discovery_sha256": discovery_sha,
            "code_commit": code_commit,
            "discovery_code_commit": discovery.get("code_commit"),
            "code_sha256": code_sha256(),
            "config_sha256": config_sha256,
            "data_manifest": _data_manifest(run, month),
            "missing_primary_days": missing,
            "calendar": run.day_universe(month),
            "files_opened": sorted(set(run.opened)),
        }
    )
    write_create_once(
        paths,
        StorageCategory.REPORTS,
        analysis_relative(analysis_run_id, "confirmation-day-lag-cap.csv"),
        _cells_csv(analysis),
    )
    written = write_json_with_checksum(
        paths,
        StorageCategory.REPORTS,
        analysis_relative(analysis_run_id, CONFIRMATION_FILE),
        result,
    )
    result["artifact_sha256"] = written.sha256
    return result


def confirmation_result(
    analysis: MonthAnalysis,
    h_star: int | None,
    discovery: dict[str, object],
    config: ScreenConfig,
    run: SecondsRun | None,
) -> dict[str, object]:
    """All confirmation statistics, controls, data gate and the decision (pure given inputs)."""
    cap = config.analysis.primary_cap_s
    inference = config.inference
    included = analysis.included
    reports = {report.trading_date: report for report in analysis.days}
    arrays = [analysis.primary_arrays[d] for d in included]

    primary: float | None = None
    threshold: float | None = None
    p_value: float | None = None
    null_summary: dict[str, object] = {}
    simultaneous: dict[str, object] = {}
    circular: float | None = None
    impossible: float | None = None
    impossible_days = 0
    reverse: float | None = None
    cap_stats: dict[int, float | None] = {}
    day_rows: list[dict[str, object]] = []
    zs: list[float] = []
    positive_share: float | None = None
    if h_star is not None:
        primary = analysis.stats[(h_star, cap)].statistic
        reverse = analysis.stats[(-h_star, cap)].statistic
        cap_stats = {
            c: analysis.stats[(h_star, c)].statistic for c in config.analysis.staleness_caps_s
        }
        for d in included:
            report = reports[d]
            row: dict[str, object] = {"trading_date": d.isoformat()}
            for c in config.analysis.staleness_caps_s:
                cell = report.cells[(h_star, c)]
                row[f"r_cap_{c}s"] = finite_or_none(cell.r)
                row[f"n_cap_{c}s"] = cell.sums.n
            cell = report.cells[(h_star, cap)]
            if cell.z is not None:
                zs.append(cell.z)
            row["fisher_z_primary"] = finite_or_none(cell.z)
            row["sign_primary"] = (
                0 if cell.r is None else (1 if cell.r > 0 else -1 if cell.r < 0 else 0)
            )
            day_rows.append(row)
        defined = [row for row in day_rows if row[f"r_cap_{cap}s"] is not None]
        if defined:
            positive_share = sum(1 for row in defined if row["sign_primary"] == 1) / len(defined)
        if len(arrays) >= 2:
            grid = config.analysis.positive_grid_s
            matrix = z_matrix(arrays, grid, cap, config)
            perms = derangements(len(arrays), inference.replicates, inference.seed)
            null = permutation_null(matrix, perms)
            values = [v for v in null[h_star] if not math.isnan(v)]
            threshold = upper_quantile(values, inference.null_quantile) if values else None
            if primary is not None and values:
                p_value = (1 + sum(1 for v in values if v >= primary)) / (len(values) + 1)
            maxima = [
                max(null[lag][k] for lag in grid if not math.isnan(null[lag][k]))
                for k in range(len(perms))
                if any(not math.isnan(null[lag][k]) for lag in grid)
            ]
            max_threshold = upper_quantile(maxima, inference.null_quantile) if maxima else None
            observed = {lag: analysis.stats[(lag, cap)].statistic for lag in grid}
            simultaneous = {
                "max_statistic_threshold": finite_or_none(max_threshold),
                "observed": {str(k): finite_or_none(v) for k, v in observed.items()},
                "lags_above_threshold": [
                    lag
                    for lag, v in observed.items()
                    if v is not None and max_threshold is not None and v > max_threshold
                ],
            }
            null_summary = {
                "method": inference.null_method,
                "seed": inference.seed,
                "replicates": len(perms),
                "quantile": inference.null_quantile,
                "threshold_at_h_star": finite_or_none(threshold),
                "mean_at_h_star": finite_or_none(math.fsum(values) / len(values))
                if values
                else None,
                "p_value_one_sided": p_value,
                "permutations_sha256": sha256_bytes(repr(perms).encode("ascii")),
            }
            circular = circular_shift_statistic(
                arrays, h_star, inference.circular_shift_s, cap, config
            )
            impossible, impossible_days = impossible_time_statistic(matrix[h_star])
    share = strongest_day_share(zs)
    min_paired = min((reports[d].min_paired_positive_grid for d in included), default=None)
    stale_seen: list[float] = []
    for d in included:
        for venue in ("moex", "comex"):
            value = reports[d].counts[venue].stale_fraction[cap]
            if isinstance(value, float):
                stale_seen.append(value)
    quantum_limit = config.data_gate.max_timestamp_quantum_ns
    timestamp_failures = {"timestamp_quantum_missing", "timestamp_quantum_too_coarse"}
    timestamp_ok = all(timestamp_failures.isdisjoint(report.reasons) for report in analysis.days)
    if run is not None:
        identity = run.manifest.get("comex_identity")
        bar_interval = identity.get("bar_interval_ns") if isinstance(identity, dict) else None
        timestamp_ok = (
            timestamp_ok and bar_interval == NS_PER_SECOND and quantum_limit >= NS_PER_SECOND
        )
    # Published common-session map: every included day has a non-empty common-segment map.
    session_map = bool(included) and all(reports[d].counts["moex"].slots > 0 for d in included)
    reconciliation_ok = all(
        "reconciliation_failed" not in report.reasons for report in analysis.days
    )
    gate = evaluate_data_gate(
        GateInputs(
            included_days=len(included),
            min_paired_each_day=min_paired,
            max_stale_fraction_seen=max(stale_seen) if stale_seen else None,
            timestamp_semantics_ok=timestamp_ok,
            session_map_published=session_map,
            reconciliation_ok=reconciliation_ok,
            strongest_day_share=share,
        ),
        config,
    )
    category, conditions = decide(
        DecisionInputs(
            gate=gate,
            timestamp_semantics_ok=timestamp_ok,
            h_star=h_star,
            primary_statistic=primary,
            null_threshold=threshold,
            positive_day_share=positive_share,
            reverse_statistic=reverse,
            circular_shift_statistic=circular,
            impossible_time_statistic=impossible,
            cap_statistics=cap_stats,
            strongest_day_share=share,
        ),
        config,
    )
    september = {
        str(item["lag_s"]) + "/" + str(item["cap_s"]): item.get("equal_day_r")
        for item in _as_list(discovery.get("month_statistics"))
        if isinstance(item, dict)
    }
    comparison = [
        {
            "lag_s": stat.lag_s,
            "cap_s": stat.cap_s,
            "september_equal_day_r": september.get(f"{stat.lag_s}/{stat.cap_s}"),
            "october_equal_day_r": finite_or_none(stat.statistic),
        }
        for (_lag, _cap), stat in sorted(analysis.stats.items())
    ]
    return {
        "confirmation_month": analysis.month,
        "h_star_s": h_star,
        "primary_cap_s": cap,
        "primary_statistic_equal_day_r": finite_or_none(primary),
        "included_days": [d.isoformat() for d in included],
        "days": _days_json(analysis),
        "null": null_summary,
        "simultaneous_grid": simultaneous,
        "controls": {
            "reverse_direction_lag_s": None if h_star is None else -h_star,
            "reverse_direction_equal_day_r": finite_or_none(reverse),
            "day_mismatch_threshold": finite_or_none(threshold),
            "circular_shift_s": inference.circular_shift_s,
            "circular_shift_equal_day_r": finite_or_none(circular),
            "impossible_time_method": inference.impossible_time_method,
            "impossible_time_equal_day_r": finite_or_none(impossible),
            "impossible_time_day_pairs": impossible_days,
            "caps_equal_day_r": {str(k): finite_or_none(v) for k, v in cap_stats.items()},
        },
        "per_day_sign_table": day_rows,
        "positive_day_share": positive_share,
        "strongest_day_share": finite_or_none(share),
        "strongest_day_share_definition": config.data_gate.strongest_day_share_definition,
        "abs_z_share_diagnostic": finite_or_none(abs_z_share(zs)) if zs else None,
        "equal_day_r_check": finite_or_none(equal_day_statistic(zs)),
        "month_statistics": _month_stats_json(analysis),
        "september_vs_october": comparison,
        "data_gate": gate,
        "decision_conditions": conditions,
        "category": category,
        "limitations": list(LIMITATIONS),
        "time_semantics": TIME_SEMANTICS,
    }


# --- CLI ---------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m moex_arbitrage_lab.research.seconds_screen")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-seconds", help="build causal seconds per venue/day")
    build.add_argument("--inputs", required=True)
    build.add_argument("--run-id", required=True)
    for name in ("discover", "confirm"):
        step = commands.add_parser(name)
        step.add_argument("--seconds-run-id", required=True)
        step.add_argument("--analysis-run-id", required=True)
    for sub in commands.choices.values():
        sub.add_argument("--config", required=True)
        sub.add_argument("--code-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None, paths: ProjectPaths | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = paths if paths is not None else resolve_project_paths()
        config, config_sha = load_screen_config(resolve_config_file(project, args.config))
        if args.command == "build-seconds":
            moex, comex = load_inputs_spec(project, args.inputs)
            manifest = build_seconds_run(
                project, config, config_sha, moex, comex, args.run_id, args.code_commit
            )
            summary: dict[str, object] = {
                "run_id": manifest["run_id"],
                "moex_days": len(moex),
                "comex_bars_total": manifest["comex_bars_total"],
            }
        elif args.command == "discover":
            artifact = run_discovery(
                project,
                config,
                config_sha,
                args.seconds_run_id,
                args.analysis_run_id,
                args.code_commit,
            )
            summary = {
                "h_star_s": artifact["h_star_s"],
                "artifact_sha256": artifact["artifact_sha256"],
            }
        else:
            result = run_confirmation(
                project,
                config,
                config_sha,
                args.seconds_run_id,
                args.analysis_run_id,
                args.code_commit,
            )
            summary = {"category": result["category"], "artifact_sha256": result["artifact_sha256"]}
    except (StoreError, StoragePathError, ValueError, OSError) as error:
        sys.stderr.write(f"refused: {error}\n")
        return 2
    sys.stdout.write(f"{summary}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
