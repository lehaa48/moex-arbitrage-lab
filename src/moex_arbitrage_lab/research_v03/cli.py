"""Offline workflows and CLI of the H-0001 v0.3 multi-scale confirmation.

    python -m moex_arbitrage_lab.research_v03.cli calibrate \\
        --config experiments/h0001_multiscale_v0_3.toml --analysis-run-id <id> \\
        --code-commit <40-hex sha>
    python -m moex_arbitrage_lab.research_v03.cli confirm \\
        --config experiments/h0001_multiscale_v0_3.toml --analysis-run-id <id> \\
        --code-commit <40-hex sha> --v0-2-analysis-run-id <v0.2 analysis run id>

``--config`` is relative to ``config/`` (a leading ``config/`` is also accepted). No step
contacts any host. Inputs are the frozen v0.2 one-second series only, loaded through
``research.seconds_store.SecondsRun`` (SHA-verified); the v0.2 config bytes, the seconds
manifest SHA-256 and the v0.2 build code identity must equal the registered values.

- ``calibrate``: calibration month only (``MonthGuard``); refused once ANY v0.2 opened marker
  exists (``h0001-seconds-v0-2/ledger``: "v0.2 confirmation already opened") or any v0.3 opened
  marker exists under the same config SHA-256. Writes ``calibration.json`` (+ ``.sha256``) and
  ``calibration-cells.csv`` create-once.
- ``confirm``: requires the checksum-verified calibration artifact of the same analysis run with
  identical config SHA-256, code commit, research_v03 code SHA-256, v0.2 research code SHA-256
  and seconds manifest SHA-256, and the checksum-verified v0.2 ``confirmation.json``. Before
  the marker it refuses: an existing ``confirmation.json``/``.sha256``/``confirmation-cells.csv``
  of the run; ANY existing v0.3 opened marker ("October already opened for v0.3"); any v0.2
  marker whose ``opened_utc`` is not strictly after the calibration ``created_utc``
  ("calibration does not predate the v0.2 opening"). It then writes the create-once opened
  marker ``h0001-multiscale-v0-3/ledger/<calibration_sha256>.opened.json`` BEFORE any
  confirmation-month day is loaded (only manifest metadata is read before it, and every
  date/interval of the plan is pre-checked against the guard on that metadata); then loads the
  confirmation month under ``ConfirmationGuard`` (MOEX dates of the confirmation month only;
  COMEX intervals inside [calibration month start, confirmation month end), because the first
  October CME trade date opens on the last September evening) and writes ``confirmation.json``
  (+ ``.sha256``, incl. ``v0_2_category`` and ``combined_decision``) and
  ``confirmation-cells.csv`` create-once.
"""

import argparse
import csv
import hashlib
import io
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

from moex_arbitrage_lab.research.seconds_analysis import CATEGORIES
from moex_arbitrage_lab.research.seconds_config import month_of, month_window_ns
from moex_arbitrage_lab.research.seconds_series import CommonSegment
from moex_arbitrage_lab.research.seconds_store import (
    LEDGER_DIR,
    SCREEN_DIR,
    CreateOnceError,
    HoldoutGuardError,
    IntegrityError,
    MonthGuard,
    SecondsRun,
    StoreError,
    check_code_commit,
    check_run_id,
    code_sha256,
    read_json_with_checksum,
    resolve_config_file,
    sha256_bytes,
    utc_now_text,
    write_create_once,
    write_json_with_checksum,
)
from moex_arbitrage_lab.research_v03.analysis import (
    MultiscaleResult,
    analyze,
    combined_decision,
)
from moex_arbitrage_lab.research_v03.config import MultiscaleConfig, load_multiscale_config
from moex_arbitrage_lab.settings.storage_paths import (
    ProjectPaths,
    StorageCategory,
    StoragePathError,
    resolve_project_paths,
)

REPORT_DIR: Final = "h0001-multiscale-v0-3"
CALIBRATION_FILE: Final = "calibration.json"
CONFIRMATION_FILE: Final = "confirmation.json"
CALIBRATION_CELLS: Final = "calibration-cells.csv"
CONFIRMATION_CELLS: Final = "confirmation-cells.csv"
CALIBRATION_VERSION: Final = "h0001_multiscale_calibration_v1"
CONFIRMATION_VERSION: Final = "h0001_multiscale_confirmation_v1"
LEDGER_VERSION: Final = "h0001_multiscale_confirmation_opened_v1"
V02_CONFIRMATION_FILE: Final = "confirmation.json"
V02_CONFIRMATION_VERSION: Final = "h0001_seconds_confirmation_v1"
PROTOCOL_DOC: Final = "docs/experiments/H-0001_MULTISCALE_V0_3.md"
DEFINITIONS: Final = {
    "states": "frozen v0.2 one-second states: last observation with time < t+1s inside the same "
    "common segment, age measured at t+1s",
    "grid": "lo = min common-segment start offset of the month (s from trading-date 00:00 UTC), "
    "rounded down to 60 s; bar u covers [o+uD, o+(u+1)D), o = date 00:00 UTC + lo",
    "endpoints": "e0 = o+uD-1, e1 = o+(u+1)D-1; seg[u] = common segment containing both, else -1",
    "bar_return": "ln S(e1) - ln S(e0); valid iff seg[u] >= 0, both states exist, both ages <= "
    "max(staleness_floor_s, D); zero returns kept",
    "pairing": "COMEX bar u with MOEX bar u+k, both valid, seg[u] == seg[u+k] >= 0; k=+1 COMEX "
    "leads (forward), k=-1 reverse control",
    "cell": "Pearson r defined iff N >= min_pairs_per_cell and both variances > 0; z = "
    "atanh(clamp(r))",
    "eligibility": "reconciled, common segments present, MOEX quantum <= max, r defined at every "
    "width for both lags (data quality only)",
    "statistic": "tanh(mean z) over eligible days",
    "null": "R whole-day derangements (seed), shared by all widths and lags; m_b = mean_j "
    "z(COMEX pi_b(j), MOEX j), undefined cells skipped",
    "standardisation": "T = (mean_z - mu_null)/sigma_null, population moments of defined m_b; "
    "undefined if sigma_null = 0",
    "p_fw": "(1 + #{b: max_D T_b(D,+1) >= T(D,+1)})/(R+1); undefined replicate max counts as "
    "exceedance",
    "p_rev": "(1 + #{b: max_D |T_b(D,-1)| >= |T(D,-1)|})/(R+1)",
    "strongest_day_share": "max_d(s*z_d)/|sum z_d|, s = sign(sum); +inf if sum = 0",
}
LIMITATIONS: Final = (
    "COMEX ohlcv-1s bars are timestamped by Databento receive time (ts_recv), not exchange "
    "event time; lags are provider-observation lags.",
    "MOEX T-Bank TRADE_TS clock accuracy and capture point are undocumented.",
    "Trades/bars only: a positive screen is predictive evidence at most and makes no claim "
    "about quotes, costs, tradability or profit.",
    "The bar-width grid was chosen from September exploration; October is the only confirmation.",
    "Prices are trade-based (last-trade closes per second), not quotes; a bar return is the "
    "change between two last-trade states.",
    "Non-synchronous trading: MOEX last-trade staleness (a MOEX bar whose last trade lags the "
    "common price move) can produce an apparent COMEX -> MOEX next-bar lead that is not a "
    "quote lag.",
    "Not evidence of executable trading: no quotes, spreads, fees, latency or fills are modelled.",
)


def v03_code_sha256() -> str:
    """Content identity of the research_v03 package source (sorted ``*.py``, name + bytes)."""
    package = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(package.glob("*.py")):
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def run_relative(run_id: str, name: str) -> str:
    return f"{REPORT_DIR}/{check_run_id(run_id)}/{name}"


def ledger_relative(calibration_sha256: str) -> str:
    return f"{REPORT_DIR}/{LEDGER_DIR}/{calibration_sha256}.opened.json"


def config_relative(argument: str) -> str:
    """Root-relative config path for ``--config`` given relative to ``config/``."""
    return argument if argument.startswith("config/") else f"config/{argument}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


@dataclass(frozen=True, slots=True)
class ConfirmationGuard(MonthGuard):
    """Confirmation barrier: only MOEX trading dates of ``month``; COMEX intervals must lie
    inside ``[earliest_month start, month end)``. The CME trade date of the first confirmation
    day opens on the previous evening (e.g. 2026-09-30 22:00 UTC for 2026-10-01), i.e. inside
    the calibration month, which is not holdout data; nothing after ``month`` can be opened."""

    earliest_month: str = ""

    def check_interval(self, start_ns: int, end_ns: int) -> None:
        lo, _ = month_window_ns(self.earliest_month or self.month)
        _, hi = month_window_ns(self.month)
        if start_ns < lo or end_ns > hi:
            raise HoldoutGuardError(
                f"COMEX interval outside [{self.earliest_month or self.month}, {self.month}]"
            )


def _markers_in(paths: ProjectPaths, relative: str) -> list[dict[str, object]]:
    """Checksum-verified ``*.opened.json`` markers of one ledger directory (REPORTS)."""
    directory = paths.resolve(StorageCategory.REPORTS, relative)
    if not directory.exists():
        return []
    return [
        read_json_with_checksum(paths, StorageCategory.REPORTS, f"{relative}/{path.name}")[0]
        for path in sorted(directory.glob("*.opened.json"))
    ]


def _ledger_markers(paths: ProjectPaths) -> list[dict[str, object]]:
    """v0.3 opened markers (``h0001-multiscale-v0-3/ledger``)."""
    return _markers_in(paths, f"{REPORT_DIR}/{LEDGER_DIR}")


def _v02_ledger_markers(paths: ProjectPaths) -> list[dict[str, object]]:
    """v0.2 opened markers (``h0001-seconds-v0-2/ledger``), as in ``seconds_screen``."""
    return _markers_in(paths, f"{SCREEN_DIR}/{LEDGER_DIR}")


def _utc_instant(value: object) -> datetime | None:
    """Aware UTC instant of an artifact timestamp text (None if missing or malformed)."""
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    return instant if instant.utcoffset() is not None else None


def _open_inputs(paths: ProjectPaths, config: MultiscaleConfig) -> SecondsRun:
    """Verify the v0.2 config bytes, the seconds manifest and the v0.2 build code identity."""
    inputs = config.inputs
    v02_config = resolve_config_file(paths, config_relative(inputs.v0_2_config_path))
    _require(
        sha256_bytes(v02_config.read_bytes()) == inputs.v0_2_config_sha256,
        "v0.2 config bytes differ from the registered SHA-256",
    )
    run = SecondsRun.open(paths, inputs.seconds_run_id, inputs.v0_2_config_sha256)
    _require(
        run.manifest_sha256 == inputs.seconds_manifest_sha256,
        "seconds manifest differs from the registered SHA-256",
    )
    _require(
        run.manifest.get("code_sha256") == code_sha256(),
        "v0.2 research code differs from the code that built the seconds run",
    )
    return run


def _month_dates(month: str) -> list[date]:
    year, number = (int(part) for part in month.split("-"))
    day = date(year, number, 1)
    days: list[date] = []
    while month_of(day) == month:
        days.append(day)
        day += timedelta(days=1)
    return days


def _month_plan(
    run: SecondsRun, config: MultiscaleConfig, month: str, guard: MonthGuard
) -> tuple[list[tuple[date, tuple[CommonSegment, ...]]], list[dict[str, object]]]:
    """Manifest-only plan; the primary candidates must be Mon-Fri minus the excluded dates, and
    every MOEX date and COMEX interval the plan will open must already pass ``guard`` (checked
    on manifest metadata, so a guard refusal can never happen after the opened marker)."""
    candidates = run.primary_candidates(month)
    expected = {d for d in _month_dates(month) if d.weekday() < 5} - config.excluded_in(month)
    _require(
        len(candidates) == len(set(candidates)) and set(candidates) == expected,
        f"primary candidates of {month} are not Mon-Fri minus the registered excluded dates",
    )
    plan, missing = run.month_plan(month, guard)
    for day, common in plan:
        guard.check_date(day)
        for name in sorted({segment.comex_session for segment in common}):
            interval = run.comex_interval(name)
            guard.check_interval(
                int(str(interval["start_utc_ns"])), int(str(interval["end_utc_ns"]))
            )
    return plan, missing


def _data_manifest(run: SecondsRun, month: str) -> dict[str, object]:
    raw_days = run.manifest.get("moex_days")
    days = [
        item
        for item in (raw_days if isinstance(raw_days, list) else [])
        if isinstance(item, dict) and str(item.get("trading_date", "")).startswith(month)
    ]
    return {
        "seconds_run_id": run.run_id,
        "seconds_manifest_sha256": run.manifest_sha256,
        "seconds_build_code_commit": run.manifest.get("code_commit"),
        "seconds_build_code_sha256": run.manifest.get("code_sha256"),
        "moex_raw_sha256": {str(d["trading_date"]): d.get("raw_sha256") for d in days},
        "comex_inputs": run.manifest.get("comex_inputs"),
    }


def cells_csv(result: MultiscaleResult) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        (
            "trading_date",
            "eligible",
            "width_s",
            "lag_bars",
            "paired_n",
            "r",
            "fisher_z",
            "comex_valid_bars",
            "moex_valid_bars",
        )
    )
    for day in result.days:
        for (width, lag), cell in sorted(day.cells.items()):
            writer.writerow(
                (
                    day.trading_date.isoformat(),
                    "true" if day.eligible else "false",
                    width,
                    lag,
                    cell.sums.n,
                    "" if cell.r is None else repr(cell.r),
                    "" if cell.z is None else repr(cell.z),
                    day.counts[width]["comex"]["valid"],
                    day.counts[width]["moex"]["valid"],
                )
            )
    return buffer.getvalue().encode("utf-8")


def _artifact(
    version: str,
    run: SecondsRun,
    config: MultiscaleConfig,
    config_sha256: str,
    analysis_run_id: str,
    code_commit: str,
    month: str,
    missing: list[dict[str, object]],
    result: MultiscaleResult,
) -> dict[str, object]:
    return {
        "artifact_version": version,
        "created_utc": utc_now_text(),
        "hypothesis": config.hypothesis,
        "protocol_version": config.protocol_version,
        "protocol_document": PROTOCOL_DOC,
        "analysis_run_id": analysis_run_id,
        "month": month,
        "code_commit": code_commit,
        "code_sha256": v03_code_sha256(),
        "v0_2_research_code_sha256": code_sha256(),
        "config_sha256": config_sha256,
        "seconds_run_id": run.run_id,
        "seconds_manifest_sha256": run.manifest_sha256,
        "v0_2_config_sha256": config.inputs.v0_2_config_sha256,
        "data_manifest": _data_manifest(run, month),
        "missing_primary_days": missing,
        "calendar": run.day_universe(month),
        "files_opened": sorted(set(run.opened)),
        "definitions": DEFINITIONS,
        "limitations": list(LIMITATIONS),
        "category": result.category,
        "results": result.as_json(),
    }


# --- calibrate ---------------------------------------------------------------------------------


def run_calibration(
    paths: ProjectPaths,
    config: MultiscaleConfig,
    config_sha256: str,
    analysis_run_id: str,
    code_commit: str,
) -> dict[str, object]:
    check_code_commit(code_commit)
    target = run_relative(analysis_run_id, CALIBRATION_FILE)
    if _v02_ledger_markers(paths):
        raise HoldoutGuardError(
            "v0.2 confirmation already opened (a v0.2 ledger marker exists); calibration must "
            "predate any opening of the confirmation month"
        )
    for marker in _ledger_markers(paths):
        if marker.get("config_sha256") == config_sha256:
            raise HoldoutGuardError(
                "the confirmation month was already opened under this config; re-calibration "
                "would tune on the confirmation month"
            )
    if paths.resolve(StorageCategory.REPORTS, target).exists():
        raise CreateOnceError(f"calibration already exists for analysis run {analysis_run_id}")
    month = config.period.calibration_month
    guard = MonthGuard(month)
    run = _open_inputs(paths, config)
    plan, missing = _month_plan(run, config, month, guard)
    result = analyze(run.iter_days(plan, guard), config, month=month, plan=plan)
    artifact = _artifact(
        CALIBRATION_VERSION,
        run,
        config,
        config_sha256,
        analysis_run_id,
        code_commit,
        month,
        missing,
        result,
    )
    write_create_once(
        paths,
        StorageCategory.REPORTS,
        run_relative(analysis_run_id, CALIBRATION_CELLS),
        cells_csv(result),
    )
    written = write_json_with_checksum(paths, StorageCategory.REPORTS, target, artifact)
    artifact["artifact_sha256"] = written.sha256
    return artifact


# --- confirm -----------------------------------------------------------------------------------


def _v02_category(
    paths: ProjectPaths, config: MultiscaleConfig, v0_2_analysis_run_id: str
) -> tuple[str, str]:
    relative = f"{SCREEN_DIR}/{check_run_id(v0_2_analysis_run_id)}/{V02_CONFIRMATION_FILE}"
    value, digest = read_json_with_checksum(paths, StorageCategory.REPORTS, relative)
    _require(value.get("artifact_version") == V02_CONFIRMATION_VERSION, "not a v0.2 confirmation")
    _require(
        value.get("confirmation_month") == config.period.confirmation_month,
        "v0.2 confirmation is for another month",
    )
    data_manifest = value.get("data_manifest")
    _require(
        isinstance(data_manifest, dict)
        and data_manifest.get("seconds_manifest_sha256") == config.inputs.seconds_manifest_sha256,
        "v0.2 confirmation used another seconds manifest",
    )
    category = value.get("category")
    if not isinstance(category, str) or category not in CATEGORIES:
        raise IntegrityError("v0.2 confirmation has no valid category")
    return category, digest


def run_confirmation(
    paths: ProjectPaths,
    config: MultiscaleConfig,
    config_sha256: str,
    analysis_run_id: str,
    code_commit: str,
    v0_2_analysis_run_id: str,
) -> dict[str, object]:
    check_code_commit(code_commit)
    calibration, calibration_sha = read_json_with_checksum(
        paths, StorageCategory.REPORTS, run_relative(analysis_run_id, CALIBRATION_FILE)
    )
    _require(
        calibration.get("artifact_version") == CALIBRATION_VERSION, "not a calibration artifact"
    )
    _require(calibration.get("config_sha256") == config_sha256, "config changed since calibration")
    _require(calibration.get("code_commit") == code_commit, "code commit changed since calibration")
    _require(
        calibration.get("code_sha256") == v03_code_sha256(),
        "research_v03 code changed since calibration",
    )
    _require(
        calibration.get("v0_2_research_code_sha256") == code_sha256(),
        "v0.2 research code changed since calibration",
    )
    _require(
        calibration.get("seconds_manifest_sha256") == config.inputs.seconds_manifest_sha256,
        "seconds manifest differs from the one used at calibration",
    )
    _require(
        calibration.get("month") == config.period.calibration_month,
        "calibration artifact is not for the calibration month",
    )
    v02_category, v02_sha = _v02_category(paths, config, v0_2_analysis_run_id)
    for name in (CONFIRMATION_FILE, f"{CONFIRMATION_FILE}.sha256", CONFIRMATION_CELLS):
        if paths.resolve(StorageCategory.REPORTS, run_relative(analysis_run_id, name)).exists():
            raise HoldoutGuardError(
                "a confirmation artifact already exists for this analysis run; October already "
                "opened for v0.3"
            )
    if _ledger_markers(paths):
        raise HoldoutGuardError(
            "October already opened for v0.3 (a v0.3 ledger marker exists); refusing any "
            "further confirmation"
        )
    calibrated = _utc_instant(calibration.get("created_utc"))
    for marker in _v02_ledger_markers(paths):
        opened = _utc_instant(marker.get("opened_utc"))
        if calibrated is None or opened is None or not opened > calibrated:
            raise HoldoutGuardError(
                "calibration does not predate the v0.2 opening of the confirmation month"
            )
    month = config.period.confirmation_month
    guard = ConfirmationGuard(month, earliest_month=config.period.calibration_month)
    # Manifest metadata only (identities, day universe, common segments): no outcome data.
    run = _open_inputs(paths, config)
    _require(
        calibration.get("seconds_manifest_sha256") == run.manifest_sha256,
        "seconds manifest differs from the one used at calibration",
    )
    plan, missing = _month_plan(run, config, month, guard)
    _require(not run.opened, "no data file may be opened before the opened marker")
    try:
        write_json_with_checksum(
            paths,
            StorageCategory.REPORTS,
            ledger_relative(calibration_sha),
            {
                "ledger_version": LEDGER_VERSION,
                "calibration_sha256": calibration_sha,
                "analysis_run_id": analysis_run_id,
                "config_sha256": config_sha256,
                "code_commit": code_commit,
                "code_sha256": v03_code_sha256(),
                "seconds_manifest_sha256": run.manifest_sha256,
                "confirmation_month": month,
                "v0_2_analysis_run_id": v0_2_analysis_run_id,
                "v0_2_confirmation_sha256": v02_sha,
                "opened_utc": utc_now_text(),
            },
        )
    except CreateOnceError:
        raise HoldoutGuardError(
            "the confirmation month was already opened for this calibration; refusing a second "
            "confirmation"
        ) from None

    result = analyze(run.iter_days(plan, guard), config, month=month, plan=plan)
    decision = combined_decision(v02_category, result)
    artifact = _artifact(
        CONFIRMATION_VERSION,
        run,
        config,
        config_sha256,
        analysis_run_id,
        code_commit,
        month,
        missing,
        result,
    )
    artifact.update(
        {
            "calibration_sha256": calibration_sha,
            "v0_2_analysis_run_id": v0_2_analysis_run_id,
            "v0_2_confirmation_sha256": v02_sha,
            "v0_2_category": v02_category,
            "v0_3_category": result.category,
            "combined_decision": decision,
        }
    )
    write_create_once(
        paths,
        StorageCategory.REPORTS,
        run_relative(analysis_run_id, CONFIRMATION_CELLS),
        cells_csv(result),
    )
    written = write_json_with_checksum(
        paths,
        StorageCategory.REPORTS,
        run_relative(analysis_run_id, CONFIRMATION_FILE),
        artifact,
    )
    artifact["artifact_sha256"] = written.sha256
    return artifact


# --- CLI ---------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m moex_arbitrage_lab.research_v03.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("calibrate", help="calibration month (September) only")
    confirm = commands.add_parser("confirm", help="confirmation month (October), once")
    confirm.add_argument("--v0-2-analysis-run-id", required=True)
    for sub in commands.choices.values():
        sub.add_argument("--config", required=True)
        sub.add_argument("--analysis-run-id", required=True)
        sub.add_argument("--code-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None, paths: ProjectPaths | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = paths if paths is not None else resolve_project_paths()
        config, config_sha = load_multiscale_config(
            resolve_config_file(project, config_relative(args.config))
        )
        check_run_id(args.analysis_run_id)
        check_code_commit(args.code_commit)
        if args.command == "calibrate":
            artifact = run_calibration(
                project, config, config_sha, args.analysis_run_id, args.code_commit
            )
            summary: dict[str, object] = {
                "command": "calibrate",
                "category": artifact["category"],
                "artifact_sha256": artifact["artifact_sha256"],
            }
        else:
            artifact = run_confirmation(
                project,
                config,
                config_sha,
                args.analysis_run_id,
                args.code_commit,
                args.v0_2_analysis_run_id,
            )
            summary = {
                "command": "confirm",
                "v0_2_category": artifact["v0_2_category"],
                "v0_3_category": artifact["v0_3_category"],
                "combined_decision": artifact["combined_decision"],
                "artifact_sha256": artifact["artifact_sha256"],
            }
    except (StoreError, StoragePathError, ValueError, OSError) as error:
        sys.stderr.write(f"refused: {error}\n")
        return 2
    sys.stdout.write(f"{summary}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
