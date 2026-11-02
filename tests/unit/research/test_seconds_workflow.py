"""End-to-end offline workflow on synthetic data: build-seconds -> discover (September only)
-> confirm (October once), create-once outputs, holdout barriers and the CLI."""

import json
import stat
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from moex_arbitrage_lab.research import seconds_screen
from moex_arbitrage_lab.research.seconds_analysis import Grid, analyze_month, derangements
from moex_arbitrage_lab.research.seconds_config import ScreenConfig
from moex_arbitrage_lab.research.seconds_screen import (
    ComexFileInput,
    MoexDayInput,
    build_seconds_run,
    confirmation_result,
    main,
    run_confirmation,
    run_discovery,
)
from moex_arbitrage_lab.research.seconds_store import (
    CreateOnceError,
    HoldoutGuardError,
    IntegrityError,
    MonthGuard,
    SecondsRun,
    StoreError,
    finite_json,
    read_verified,
    sha256_bytes,
    write_create_once,
)
from moex_arbitrage_lab.settings import ProjectPaths, StorageCategory

# Assembled from parts so that this file does not contain the banned words themselves.
BANNED_WORDS = ("pro" + "fit", "executable " + "arbitrage", "evidence_of_" + "lead_lag")
COMMIT = "0123abc" + "0" * 33  # synthetic full commit id
RAW_SHA = "a" * 64  # synthetic raw payload hash
Factory = Callable[[str], ProjectPaths]


def _inputs(market: Any) -> tuple[list[MoexDayInput], list[ComexFileInput]]:
    moex = [
        MoexDayInput(
            trading_date=day.trading_date,
            trades=day.trades,
            segments=day.segments,
            input_files=({"path": "synthetic-in-memory", "sha256": RAW_SHA, "bytes": 0},),
            raw_sha256=(RAW_SHA,),
        )
        for day in market.days
    ]
    return moex, [ComexFileInput("data/raw/synthetic/comex.csv", market.comex_csv)]


@pytest.fixture(scope="module")
def positive(
    session_repo_factory: Factory,
    frozen: tuple[ScreenConfig, str],
    market: Callable[..., Any],
    screen_days: tuple[list[date], list[date]],
) -> dict[str, Any]:
    config, sha = frozen
    september, october = screen_days
    paths = session_repo_factory("positive")
    # Fixed synthetic seed. Under the null each placebo control exceeds the 95% threshold with
    # probability ~5% (the frozen conservative rule), so the seed is part of the fixture.
    moex, comex = _inputs(market(september + october, lead_s=5, seed=12))
    manifest = build_seconds_run(paths, config, sha, moex, comex, "run-1", COMMIT)
    discovery = run_discovery(paths, config, sha, "run-1", "analysis-1", COMMIT)
    confirmation = run_confirmation(paths, config, sha, "run-1", "analysis-1", COMMIT)
    return {
        "paths": paths,
        "manifest": manifest,
        "discovery": discovery,
        "confirmation": confirmation,
    }


def test_discovery_freezes_h_star_from_september_only(positive: dict[str, Any]) -> None:
    discovery = positive["discovery"]

    assert discovery["h_star_s"] == 5
    assert discovery["included_days"] == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    assert discovery["code_commit"] == COMMIT
    assert len(discovery["config_sha256"]) == 64
    assert discovery["data_manifest"]["moex_raw_sha256"]["2026-09-01"] == [RAW_SHA]
    for path in discovery["files_opened"]:
        assert "/2026-09-" in path, path
    statistics = discovery["positive_grid_statistics"]
    assert statistics["5"] == max(statistics.values())


def test_confirmation_recovers_the_lead_and_all_controls_behave(positive: dict[str, Any]) -> None:
    result = positive["confirmation"]
    controls = result["controls"]
    threshold = result["null"]["threshold_at_h_star"]

    assert result["category"] == "SCREEN_POSITIVE"
    assert all(result["decision_conditions"].values())
    assert result["data_gate"]["passed"] is True
    assert result["h_star_s"] == 5
    assert len(result["included_days"]) == 16
    assert result["primary_statistic_equal_day_r"] > 0.8 > threshold
    assert result["null"]["replicates"] == 2000
    assert result["null"]["seed"] == 20260901
    assert result["null"]["p_value_one_sided"] == pytest.approx(1 / 2001)
    assert abs(controls["reverse_direction_equal_day_r"]) < 0.05
    assert controls["reverse_direction_lag_s"] == -5
    assert controls["circular_shift_equal_day_r"] < threshold
    assert controls["impossible_time_equal_day_r"] < threshold
    assert controls["impossible_time_day_pairs"] == 15
    assert all(v > 0.5 for v in controls["caps_equal_day_r"].values())
    assert 5 in result["simultaneous_grid"]["lags_above_threshold"]
    assert result["positive_day_share"] == 1.0
    assert result["strongest_day_share"] < 0.35
    assert len(result["per_day_sign_table"]) == 16
    comparison = {(c["lag_s"], c["cap_s"]): c for c in result["september_vs_october"]}
    assert comparison[(5, 5)]["september_equal_day_r"] > 0.8
    for path in result["files_opened"]:
        assert "/2026-10-" in path or "/2026-09-30" in path, path
    text = json.dumps(result).lower()
    for word in BANNED_WORDS:
        assert word not in text


def test_seconds_manifest_reconciles_and_outputs_verify(positive: dict[str, Any]) -> None:
    paths: ProjectPaths = positive["paths"]
    manifest = positive["manifest"]

    assert manifest["comex_reconciled"] is True
    assert manifest["comex_bars_total"] == manifest["comex_bars_assigned"] + sum(
        manifest["comex_excluded"].values()
    )
    assert manifest["comex_identity"]["instrument_id"] == 4242
    assert len(manifest["moex_days"]) == 20
    for entry in [*manifest["moex_days"], *manifest["comex_intervals"]]:
        assert entry["reconciliation"]["ok"] is True
        output = entry["output"]
        data = read_verified(paths, output["path"], output["sha256"])
        assert data.count(b"\n") == output["rows"] + 1
        target = paths.root / output["path"]
        assert not target.stat().st_mode & stat.S_IWRITE  # read-only
    universe = {item["date"]: item for item in manifest["day_universe"]}
    assert len(universe) == 61
    assert universe["2026-09-05"]["calendar_exclusion_reason"] == "weekend_not_primary"
    assert (
        universe["2026-09-07"]["calendar_exclusion_reason"] == "us_labor_day_cme_holiday_schedule"
    )
    assert universe["2026-10-12"]["primary_candidate"] is False
    assert universe["2026-10-06"]["moex_input"] is True
    assert universe["2026-10-26"]["moex_input"] is False
    assert (
        sorted(o["output"]["sha256"] for o in manifest["moex_days"] + manifest["comex_intervals"])
        == manifest["outputs_sha256"]
    )


def test_second_confirmation_and_rediscovery_are_refused(
    positive: dict[str, Any], frozen: tuple[ScreenConfig, str]
) -> None:
    config, sha = frozen
    paths: ProjectPaths = positive["paths"]
    discovery_bytes = (
        paths.root / "reports/generated/h0001-seconds-v0-2/analysis-1/discovery.json"
    ).read_bytes()

    with pytest.raises(HoldoutGuardError):
        run_confirmation(paths, config, sha, "run-1", "analysis-1", COMMIT)
    # The same frozen discovery copied under another analysis id is refused by the ledger.
    write_create_once(
        paths,
        StorageCategory.REPORTS,
        "h0001-seconds-v0-2/analysis-2/discovery.json",
        discovery_bytes,
    )
    write_create_once(
        paths,
        StorageCategory.REPORTS,
        "h0001-seconds-v0-2/analysis-2/discovery.json.sha256",
        f"{sha256_bytes(discovery_bytes)}  discovery.json\n".encode(),
    )
    with pytest.raises(HoldoutGuardError):
        run_confirmation(paths, config, sha, "run-1", "analysis-2", COMMIT)
    with pytest.raises(HoldoutGuardError):
        run_discovery(paths, config, sha, "run-1", "analysis-3", COMMIT)


# --- smaller function-scoped scenarios -------------------------------------------------------

SMALL_DAYS = [date(2026, 9, 2), date(2026, 9, 3), date(2026, 10, 6), date(2026, 10, 7)]


@pytest.fixture
def small(
    synthetic_paths: ProjectPaths,
    frozen: tuple[ScreenConfig, str],
    market: Callable[..., Any],
) -> dict[str, Any]:
    config, sha = frozen
    moex, comex = _inputs(market(SMALL_DAYS, lead_s=5, seed=3, seconds=3900))
    manifest = build_seconds_run(synthetic_paths, config, sha, moex, comex, "run-s", COMMIT)
    return {"paths": synthetic_paths, "manifest": manifest, "moex": moex, "comex": comex}


def test_discover_never_reads_october_and_confirm_detects_corruption(
    small: dict[str, Any], frozen: tuple[ScreenConfig, str]
) -> None:
    config, sha = frozen
    paths: ProjectPaths = small["paths"]
    for entry in small["manifest"]["moex_days"] + small["manifest"]["comex_intervals"]:
        if "/2026-10-" in entry["output"]["path"] or "/2026-09-30" in entry["output"]["path"]:
            target = paths.root / entry["output"]["path"]
            target.chmod(stat.S_IWRITE | stat.S_IREAD)
            target.write_bytes(b"corrupted october outcome data\n")

    discovery: dict[str, Any] = run_discovery(paths, config, sha, "run-s", "analysis-s", COMMIT)

    assert discovery["h_star_s"] == 5
    assert all("/2026-09-0" in path for path in discovery["files_opened"])
    with pytest.raises(IntegrityError, match="SHA-256"):
        run_confirmation(paths, config, sha, "run-s", "analysis-s", COMMIT)


def test_month_guard_blocks_other_months() -> None:
    guard = MonthGuard("2026-09")

    guard.check_date(date(2026, 9, 30))
    with pytest.raises(HoldoutGuardError):
        guard.check_date(date(2026, 10, 1))
    with pytest.raises(HoldoutGuardError):
        # Globex interval of CME trade date 2026-10-01 starts 2026-09-30 22:00 UTC.
        guard.check_interval(1790805600 * 10**9, 1790888400 * 10**9)


def test_confirm_requires_the_frozen_discovery_artifact(
    small: dict[str, Any], frozen: tuple[ScreenConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, sha = frozen
    paths: ProjectPaths = small["paths"]

    with pytest.raises(IntegrityError):
        run_confirmation(paths, config, sha, "run-s", "analysis-none", COMMIT)
    with pytest.raises(IntegrityError, match="code commit differs from the seconds build"):
        run_discovery(paths, config, sha, "run-s", "analysis-other", "f" * 40)
    run_discovery(paths, config, sha, "run-s", "analysis-s", COMMIT)
    with pytest.raises(IntegrityError):
        run_confirmation(paths, config, "f" * 64, "run-s", "analysis-s", COMMIT)
    with pytest.raises(IntegrityError, match="code commit changed since discovery"):
        run_confirmation(paths, config, sha, "run-s", "analysis-s", "f" * 40)
    monkeypatch.setattr(seconds_screen, "code_sha256", lambda: "e" * 64)
    with pytest.raises(IntegrityError, match="research code changed"):
        run_confirmation(paths, config, sha, "run-s", "analysis-s", COMMIT)
    monkeypatch.undo()
    artifact = paths.root / "reports/generated/h0001-seconds-v0-2/analysis-s/discovery.json"
    artifact.chmod(stat.S_IWRITE | stat.S_IREAD)
    artifact.write_bytes(artifact.read_bytes().replace(b'"h_star_s": 5', b'"h_star_s": 2'))
    with pytest.raises(IntegrityError, match="checksum"):
        run_confirmation(paths, config, sha, "run-s", "analysis-s", COMMIT)
    ledger = paths.root / "reports/generated/h0001-seconds-v0-2/ledger"
    assert not ledger.exists()  # nothing was opened by the refused attempts


def test_too_few_october_days_is_data_insufficient(
    small: dict[str, Any], frozen: tuple[ScreenConfig, str]
) -> None:
    config, sha = frozen
    paths: ProjectPaths = small["paths"]
    run_discovery(paths, config, sha, "run-s", "analysis-s", COMMIT)

    result: dict[str, Any] = run_confirmation(paths, config, sha, "run-s", "analysis-s", COMMIT)

    assert result["category"] == "DATA_INSUFFICIENT"
    assert result["data_gate"]["min_confirmation_days"] is False
    assert result["h_star_s"] == 5
    missing = {item["date"] for item in result["missing_primary_days"]}
    assert "2026-10-08" in missing
    assert "2026-10-12" not in missing


def test_outputs_are_create_once(small: dict[str, Any], frozen: tuple[ScreenConfig, str]) -> None:
    config, sha = frozen
    paths: ProjectPaths = small["paths"]

    with pytest.raises(CreateOnceError):
        build_seconds_run(paths, config, sha, small["moex"], small["comex"], "run-s", COMMIT)
    with pytest.raises(CreateOnceError):
        write_create_once(
            paths, StorageCategory.ALIGNED, "h0001-seconds-v0-2/run-s/seconds-manifest.json", b"{}"
        )
    run_discovery(paths, config, sha, "run-s", "analysis-s", COMMIT)
    with pytest.raises(CreateOnceError):
        run_discovery(paths, config, sha, "run-s", "analysis-s", COMMIT)


def test_cli_build_discover_confirm_and_header_rejection(
    synthetic_paths: ProjectPaths,
    market: Callable[..., Any],
    make_comex_csv: Callable[..., bytes],
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = synthetic_paths
    generated = market(SMALL_DAYS, lead_s=5, seed=4, seconds=3700)
    folder = paths.root / "data/normalized/synthetic"
    folder.mkdir(parents=True)
    days = []
    for day in generated.days:
        name = f"moex-{day.trading_date.isoformat()}.csv"
        rows = "".join(f"{ts},{p},{s}\n" for ts, p, s in day.trades)
        (folder / name).write_bytes(f"event_ts_utc_ns,price,size\n{rows}".encode())
        days.append(
            {
                "trading_date": day.trading_date.isoformat(),
                "trades_csv": f"data/normalized/synthetic/{name}",
                "raw_sha256": [RAW_SHA],
                "segments": [
                    {
                        "name": seg.name,
                        "start_utc_ns": seg.start_ns,
                        "end_utc_ns": seg.end_ns,
                        "trading_date": seg.trading_date.isoformat(),
                    }
                    for seg in day.segments
                ],
            }
        )
    (paths.root / "data/raw/synthetic").mkdir(parents=True)
    (paths.root / "data/raw/synthetic/comex.csv").write_bytes(generated.comex_csv)
    (paths.root / "data/raw/synthetic/bad.csv").write_bytes(
        generated.comex_csv.replace(b",symbol\n", b",sym\n", 1)
    )
    for name, comex in (("inputs.json", "comex.csv"), ("bad.json", "bad.csv")):
        spec = {
            "inputs_version": "h0001_seconds_inputs_v1",
            "moex_days": days,
            "comex_ohlcv_csv": [f"data/raw/synthetic/{comex}"],
        }
        (folder / name).write_text(json.dumps(spec), encoding="utf-8")
    config = ["--config", "config/experiments/h0001_seconds_screen_v0_2.toml"]
    common = [*config, "--code-commit", COMMIT]

    assert (
        main(
            [
                "build-seconds",
                *common,
                "--inputs",
                "data/normalized/synthetic/bad.json",
                "--run-id",
                "cli-bad",
            ],
            paths,
        )
        == 2
    )
    assert "header" in capsys.readouterr().err
    assert (
        main(
            [
                "build-seconds",
                *common,
                "--inputs",
                "data/normalized/synthetic/inputs.json",
                "--run-id",
                "cli",
            ],
            paths,
        )
        == 0
    )
    ids = ["--seconds-run-id", "cli", "--analysis-run-id", "cli-a"]
    assert main(["discover", *common, *ids], paths) == 0
    assert "'h_star_s': 5" in capsys.readouterr().out
    assert main(["confirm", *common, *ids], paths) == 0
    assert "DATA_INSUFFICIENT" in capsys.readouterr().out
    assert main(["confirm", *common, *ids], paths) == 2
    assert main(["discover", *config, "--code-commit", "NOT-HEX", *ids], paths) == 2
    assert (
        main(["discover", "--config", "config/missing.toml", "--code-commit", COMMIT, *ids], paths)
        == 2
    )
    assert (
        main(["discover", "--config", "../outside.toml", "--code-commit", COMMIT, *ids], paths) == 2
    )


def test_confirmation_is_deterministic_and_reproducible(
    positive: dict[str, Any], frozen: tuple[ScreenConfig, str]
) -> None:
    config, sha = frozen
    paths: ProjectPaths = positive["paths"]
    stored: dict[str, Any] = positive["confirmation"]
    run = SecondsRun.open(paths, "run-1", sha)
    plan, _ = run.month_plan("2026-10", None)
    analysis = analyze_month(
        "2026-10", run.iter_days(plan, None), config, keep_arrays=True, grid=Grid.for_segments(plan)
    )

    again = confirmation_result(analysis, 5, positive["discovery"], config, run)

    for key in ("null", "simultaneous_grid", "controls", "per_day_sign_table", "category"):
        assert json.loads(json.dumps(finite_json(again[key]))) == stored[key], key
    expected = derangements(16, config.inference.replicates, config.inference.seed)
    assert stored["null"]["permutations_sha256"] == sha256_bytes(repr(expected).encode("ascii"))


def test_run_ids_are_validated_and_the_ledger_name_is_reserved(
    synthetic_paths: ProjectPaths, frozen: tuple[ScreenConfig, str]
) -> None:
    config, sha = frozen
    for bad in ("ledger", "Run", "../x", "a_b", ""):
        with pytest.raises(StoreError):
            build_seconds_run(synthetic_paths, config, sha, [], [], bad, COMMIT)
