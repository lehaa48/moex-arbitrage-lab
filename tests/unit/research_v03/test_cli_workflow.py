"""v0.3 offline workflow on a synthetic v0.2 seconds run in a tmp repository, in the legitimate
order: v0.2 build + discover -> v0.3 calibrate (September only) -> v0.2 confirm -> v0.3 confirm
(October once, opened marker before any October read). Identity checks, holdout barriers and
the confirmation guard bounds, all through the CLI entry point."""

import hashlib
import json
import stat
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from moex_arbitrage_lab.research import seconds_store
from moex_arbitrage_lab.research.seconds_config import load_screen_config
from moex_arbitrage_lab.research.seconds_screen import (
    ComexFileInput,
    MoexDayInput,
    build_seconds_run,
    run_confirmation,
    run_discovery,
)
from moex_arbitrage_lab.research.seconds_store import (
    HoldoutGuardError,
    SecondsRun,
    read_json_with_checksum,
    write_json_with_checksum,
)
from moex_arbitrage_lab.research_v03 import cli
from moex_arbitrage_lab.research_v03.cli import ConfirmationGuard, main
from moex_arbitrage_lab.settings import ProjectPaths, StorageCategory

COMMIT = "0123abc" + "0" * 33  # synthetic full commit id
OTHER_COMMIT = "f" * 40
RAW_SHA = "a" * 64  # synthetic raw payload hash
RUN_ID = "synthetic-run"
V02_ANALYSIS = "v02-analysis"
# 2026-10-01: its CME trade-date interval opens on 2026-09-30 22:00 UTC.
DAYS = [date(2026, 9, 2), date(2026, 9, 3), date(2026, 10, 1), date(2026, 10, 6)]
V03_TOML = "config/experiments/h0001_multiscale_v0_3.toml"
V02_TOML = "config/experiments/h0001_seconds_screen_v0_2.toml"
CONFIG_ARG = ["--config", "experiments/h0001_multiscale_v0_3.toml"]
REPORT = "h0001-multiscale-v0-3"
V02_LEDGER = "h0001-seconds-v0-2/ledger"
REAL_MANIFEST_SHA = "dc43c416d54e3170feb489384d3c3beb160976579669876a40b9dd06d4b35222"
NS = 1_000_000_000


def _calibrate(paths: ProjectPaths, run_id: str, commit: str = COMMIT) -> int:
    return main(
        ["calibrate", *CONFIG_ARG, "--analysis-run-id", run_id, "--code-commit", commit], paths
    )


def _confirm(
    paths: ProjectPaths,
    run_id: str,
    *,
    commit: str = COMMIT,
    v02: str = V02_ANALYSIS,
    config: list[str] = CONFIG_ARG,
) -> int:
    argv = [
        "confirm",
        *config,
        "--analysis-run-id",
        run_id,
        "--code-commit",
        commit,
        "--v0-2-analysis-run-id",
        v02,
    ]
    return main(argv, paths)


def _report(paths: ProjectPaths, relative: str) -> Path:
    return paths.resolve(StorageCategory.REPORTS, f"{REPORT}/{relative}")


def _ledger(paths: ProjectPaths) -> Path:
    return _report(paths, "ledger")


def _markers(paths: ProjectPaths) -> list[Path]:
    directory = _ledger(paths)
    return sorted(directory.glob("*.opened.json")) if directory.exists() else []


@pytest.fixture
def built(synthetic_paths: ProjectPaths, market_factory: Callable[..., Any]) -> dict[str, Any]:
    """Synthetic v0.2 seconds run and v0.2 discovery (NOT yet confirmed), and the v0.3 config
    re-pointed at the synthetic run (seconds run id and manifest SHA-256 only)."""
    paths = synthetic_paths
    v02_config, v02_sha = load_screen_config(paths.root / V02_TOML)
    market = market_factory(DAYS, lead_s=5, seed=3)
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
    comex = [ComexFileInput("data/raw/synthetic/comex.csv", market.comex_csv)]
    build_seconds_run(paths, v02_config, v02_sha, moex, comex, RUN_ID, COMMIT)
    manifest_sha = SecondsRun.open(paths, RUN_ID, v02_sha).manifest_sha256
    run_discovery(paths, v02_config, v02_sha, RUN_ID, V02_ANALYSIS, COMMIT)

    target = paths.root / V03_TOML
    text = target.read_bytes().decode("utf-8")
    assert text.count('"p3-20261102-v1"') == 1
    assert text.count(REAL_MANIFEST_SHA) == 1
    text = text.replace('"p3-20261102-v1"', f'"{RUN_ID}"').replace(REAL_MANIFEST_SHA, manifest_sha)
    target.write_bytes(text.encode("utf-8"))

    def v02_confirm() -> str:
        result = run_confirmation(paths, v02_config, v02_sha, RUN_ID, V02_ANALYSIS, COMMIT)
        return str(result["category"])

    return {
        "paths": paths,
        "manifest_sha": manifest_sha,
        "config_sha": hashlib.sha256(target.read_bytes()).hexdigest(),
        "v02_confirm": v02_confirm,
    }


def _refused(capsys: pytest.CaptureFixture[str], fragment: str) -> None:
    err = capsys.readouterr().err
    assert err.startswith("refused:"), err
    assert fragment in err, err


def _read_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    checksum = path.with_name(path.name + ".sha256").read_text(encoding="utf-8")
    assert checksum.split()[0] == hashlib.sha256(data).hexdigest()
    value: dict[str, Any] = json.loads(data)
    return value


def test_calibrate_writes_a_september_only_create_once_artifact(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]

    assert _calibrate(paths, "cal-1") == 0
    artifact = _read_json(_report(paths, "cal-1/calibration.json"))

    assert "calibrate" in capsys.readouterr().out
    assert artifact["month"] == "2026-09"
    assert artifact["config_sha256"] == built["config_sha"]
    assert artifact["seconds_manifest_sha256"] == built["manifest_sha"]
    assert artifact["code_commit"] == COMMIT
    assert artifact["category"] == "DATA_INSUFFICIENT"  # two synthetic September days
    assert artifact["files_opened"]
    for path in artifact["files_opened"]:
        assert "/2026-10-" not in path, path
        assert "/2026-09-30" not in path, path  # COMEX interval of trade date 2026-10-01
    results = artifact["results"]
    assert results["eligible_days"] == ["2026-09-02", "2026-09-03"]
    assert results["grid_origin_lo_s"] == 7 * 3600
    assert not _ledger(paths).exists()
    # Create-once: the same analysis run cannot be calibrated twice.
    assert _calibrate(paths, "cal-1") == 2
    _refused(capsys, "already exists")


def test_calibrate_is_refused_once_the_v02_confirmation_was_opened(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    built["v02_confirm"]()
    assert list(paths.resolve(StorageCategory.REPORTS, V02_LEDGER).glob("*.opened.json"))

    assert _calibrate(paths, "cal-1") == 2
    _refused(capsys, "v0.2 confirmation already opened")
    assert not _report(paths, "cal-1/calibration.json").exists()


def test_confirm_refuses_without_a_v02_confirmation_artifact(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    assert _calibrate(paths, "cal-1") == 0
    capsys.readouterr()

    assert _confirm(paths, "cal-1") == 2  # v0.2 not confirmed yet
    _refused(capsys, "h0001-seconds-v0-2/v02-analysis/confirmation.json")
    built["v02_confirm"]()
    assert _confirm(paths, "cal-1", v02="v02-missing") == 2
    _refused(capsys, "h0001-seconds-v0-2/v02-missing/confirmation.json")
    assert _confirm(paths, "cal-none") == 2  # no calibration artifact either
    _refused(capsys, "cal-none/calibration.json")
    assert not _ledger(paths).exists()
    assert not _report(paths, "cal-1/confirmation.json").exists()


def test_confirm_opens_october_once_with_the_marker_written_first(
    built: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    assert _calibrate(paths, "cal-1") == 0
    v02_category = built["v02_confirm"]()
    calibration_sha = hashlib.sha256(
        _report(paths, "cal-1/calibration.json").read_bytes()
    ).hexdigest()
    marker = _report(paths, f"ledger/{calibration_sha}.opened.json")
    reads: list[tuple[str, bool]] = []
    original = SecondsRun._read_output

    def recording(self: SecondsRun, entry: Any) -> bytes:
        output = entry.get("output")
        reads.append((str(output["path"]), marker.exists()))
        return original(self, entry)

    monkeypatch.setattr(seconds_store.SecondsRun, "_read_output", recording)
    capsys.readouterr()

    assert _confirm(paths, "cal-1") == 0

    paths_read = [path for path, _opened in reads]
    assert any("/2026-10-01" in path for path in paths_read), paths_read
    assert any("/2026-10-06" in path for path in paths_read), paths_read
    # Every file of the confirmation run is read only after the opened marker exists.
    assert all(opened for _path, opened in reads), reads
    assert all("/2026-09-0" not in path for path in paths_read)
    ledger = _read_json(marker)
    assert ledger["config_sha256"] == built["config_sha"]
    assert ledger["calibration_sha256"] == calibration_sha
    result = _read_json(_report(paths, "cal-1/confirmation.json"))
    assert result["month"] == "2026-10"
    assert result["v0_2_category"] == v02_category
    assert result["v0_3_category"] == "DATA_INSUFFICIENT"  # two synthetic October days
    assert result["combined_decision"] == "CLOSED_NOT_CONFIRMED"
    assert result["calibration_sha256"] == calibration_sha
    assert result["results"]["eligible_days"] == ["2026-10-01", "2026-10-06"]
    assert "combined_decision" in capsys.readouterr().out

    # A second confirmation, and any re-calibration, are refused.
    assert _confirm(paths, "cal-1") == 2
    _refused(capsys, "already")
    assert _calibrate(paths, "cal-2") == 2
    _refused(capsys, "already opened")
    assert not _report(paths, "cal-2/calibration.json").exists()
    assert _markers(paths) == [marker]


def test_confirm_is_refused_once_any_v03_marker_exists(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    assert _calibrate(paths, "cal-1") == 0
    assert _calibrate(paths, "cal-2") == 0
    built["v02_confirm"]()
    assert _confirm(paths, "cal-1") == 0
    markers = _markers(paths)
    assert len(markers) == 1
    capsys.readouterr()

    assert _confirm(paths, "cal-2") == 2
    _refused(capsys, "October already opened for v0.3")
    assert _markers(paths) == markers
    assert not _report(paths, "cal-2/confirmation.json").exists()


def test_confirm_is_refused_before_the_marker_if_the_cells_file_exists(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    assert _calibrate(paths, "cal-1") == 0
    built["v02_confirm"]()
    cells = _report(paths, "cal-1/confirmation-cells.csv")
    cells.write_bytes(b"synthetic placeholder\n")
    capsys.readouterr()

    assert _confirm(paths, "cal-1") == 2
    _refused(capsys, "a confirmation artifact already exists")
    assert not _ledger(paths).exists()
    assert not _report(paths, "cal-1/confirmation.json").exists()


def _rewrite_v02_marker(paths: ProjectPaths, opened_utc: str) -> None:
    """Replace the single v0.2 opened marker by one with another ``opened_utc`` and a correct
    ``.sha256`` (written with the project's create-once helper)."""
    directory = paths.resolve(StorageCategory.REPORTS, V02_LEDGER)
    (marker,) = sorted(directory.glob("*.opened.json"))
    relative = f"{V02_LEDGER}/{marker.name}"
    value, _ = read_json_with_checksum(paths, StorageCategory.REPORTS, relative)
    for path in (marker, marker.with_name(marker.name + ".sha256")):
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
        path.unlink()
    value["opened_utc"] = opened_utc
    write_json_with_checksum(paths, StorageCategory.REPORTS, relative, value)
    assert read_json_with_checksum(paths, StorageCategory.REPORTS, relative)[0] == value


def test_confirm_requires_the_calibration_to_predate_the_v02_opening(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    assert _calibrate(paths, "cal-1") == 0
    built["v02_confirm"]()
    calibration = _read_json(_report(paths, "cal-1/calibration.json"))
    created = str(calibration["created_utc"])
    _rewrite_v02_marker(paths, "2026-01-01T00:00:00.000000Z")
    assert created > "2026-01-01T00:00:00.000000Z"
    capsys.readouterr()

    assert _confirm(paths, "cal-1") == 2
    _refused(capsys, "calibration does not predate the v0.2 opening")
    _rewrite_v02_marker(paths, created)  # equal is not earlier
    assert _confirm(paths, "cal-1") == 2
    _refused(capsys, "calibration does not predate the v0.2 opening")
    assert not _ledger(paths).exists()


def test_confirm_refuses_changed_config_or_code_identity(
    built: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    assert _calibrate(paths, "cal-1") == 0
    built["v02_confirm"]()
    capsys.readouterr()
    original = paths.root / V03_TOML
    changed = original.with_name("h0001_multiscale_v0_3_changed.toml")
    changed.write_bytes(original.read_bytes() + b"# one more byte\n")
    changed_args = ["--config", "experiments/h0001_multiscale_v0_3_changed.toml"]

    assert _confirm(paths, "cal-1", commit=OTHER_COMMIT) == 2
    _refused(capsys, "code commit changed since calibration")
    assert _confirm(paths, "cal-1", config=changed_args) == 2
    _refused(capsys, "config changed since calibration")
    with monkeypatch.context() as patch:
        patch.setattr(cli, "v03_code_sha256", lambda: "e" * 64)
        assert _confirm(paths, "cal-1") == 2
    _refused(capsys, "research_v03 code changed since calibration")
    with monkeypatch.context() as patch:
        patch.setattr(cli, "code_sha256", lambda: "d" * 64)
        assert _confirm(paths, "cal-1") == 2
    _refused(capsys, "v0.2 research code changed since calibration")
    assert not _ledger(paths).exists()
    assert not _report(paths, "cal-1/confirmation.json").exists()


def test_calibrate_refuses_inputs_other_than_the_registered_ones(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    paths: ProjectPaths = built["paths"]
    original = paths.root / V03_TOML
    text = original.read_bytes().decode("utf-8")
    wrong = original.with_name("h0001_multiscale_v0_3_wrong.toml")
    wrong.write_bytes(text.replace(built["manifest_sha"], "0" * 64).encode("utf-8"))
    wrong_args = ["--config", "experiments/h0001_multiscale_v0_3_wrong.toml"]

    argv = ["calibrate", *wrong_args, "--analysis-run-id", "cal-x", "--code-commit", COMMIT]
    assert main(argv, paths) == 2
    _refused(capsys, "seconds manifest differs from the registered SHA-256")
    assert _calibrate(paths, "cal-y", commit="NOT-HEX") == 2
    _refused(capsys, "40-character")
    assert not _report(paths, "cal-x/calibration.json").exists()


# --- confirmation guard bounds -----------------------------------------------------------------


def _ns(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp()) * NS


def test_confirmation_guard_bounds() -> None:
    guard = ConfirmationGuard("2026-10", earliest_month="2026-09")

    for day in (date(2026, 10, 1), date(2026, 10, 30), date(2026, 10, 31)):
        guard.check_date(day)
    for day in (date(2026, 9, 30), date(2026, 11, 2), date(2026, 9, 1)):
        with pytest.raises(HoldoutGuardError):
            guard.check_date(day)
    # Interval of CME trade date 2026-10-01 opens 2026-09-30 22:00 UTC.
    guard.check_interval(_ns(2026, 9, 30, 22), _ns(2026, 10, 1, 21))
    guard.check_interval(_ns(2026, 10, 30, 22), _ns(2026, 11, 1))
    with pytest.raises(HoldoutGuardError):
        guard.check_interval(_ns(2026, 10, 30, 22), _ns(2026, 11, 1) + 1)
    with pytest.raises(HoldoutGuardError):
        guard.check_interval(_ns(2026, 8, 31, 22), _ns(2026, 9, 1, 21))
    with pytest.raises(HoldoutGuardError):
        guard.check_interval(_ns(2026, 11, 1), _ns(2026, 11, 2))
