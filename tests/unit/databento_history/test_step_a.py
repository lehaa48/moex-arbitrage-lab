"""Step A: free calls only, exact-zero decision, approval always required, no key anywhere."""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.settings import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.databento_history import (
    DatabentoError,
    MissingApiKeyError,
    StepAError,
    ledger_path,
    run_step_a,
)
from moex_arbitrage_lab.sources.databento_history.step_a import (
    analyze_dataset_range,
    analyze_symbology,
    parse_iso_utc_ns,
)

if TYPE_CHECKING:
    from tests.unit.databento_history.conftest import FakeClock, FakeOpener, FakeProvider, Kit

STEP_A_PATHS = [
    "metadata.list_schemas",
    "metadata.get_dataset_range",
    "symbology.resolve",
    "metadata.get_record_count",
    "metadata.get_billable_size",
    "metadata.get_cost",
]


def _run(
    paths: ProjectPaths,
    opener: "FakeOpener",
    env: dict[str, str],
    clock: "FakeClock",
    run_id: str = "synthetic-a1",
) -> tuple[dict[str, object], Path, str]:
    outcome = run_step_a(paths, opener=opener, env=env, clock=clock, run_id=run_id)
    return outcome.report, outcome.report_path, outcome.report_sha256


def test_zero_estimate_awaits_explicit_user_approval(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
) -> None:
    report, report_path, digest = _run(paths, opener, env, clock)

    assert provider.endpoint_paths() == STEP_A_PATHS
    assert report["decision"] == "AWAITING_USER_APPROVAL"
    assert report["stop_reasons"] == []
    assert report["estimate_usd"] == "0.0"
    assert report["max_cash_charge_usd"] == "0"
    template = report["approval_template"]
    assert isinstance(template, dict)
    assert template["approved_estimate_usd"] == "0.0"
    assert template["approved_billable_size"] == 69_135_752
    assert template["max_cash_charge_usd"] == "0"
    assert "user_states_credit_covers_estimate" not in template
    assert report["explicit_user_approval_required"] is True
    assert report["estimate"] == {
        "currency": "USD",
        "cost_usd": "0.0",
        "is_exactly_zero": True,
        "ledger_request_id": report["estimate"]["ledger_request_id"],  # type: ignore[index]
    }
    assert report["record_count"] == 1_234_567
    assert report["billable_size_bytes"] == 69_135_752
    assert report["credit"] == {
        "status": "unknown",
        "statement": "credit: unknown — user must state it",
        "facts": None,
    }
    symbology = report["symbology"]
    assert isinstance(symbology, dict)
    assert symbology["instrument_ids"] == [4242]
    assert symbology["resolved_uniquely"] is True
    assert report_path.is_relative_to(paths.category_dir(StorageCategory.REPORTS))
    assert json.loads(report_path.read_bytes()) == report
    import hashlib

    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("cost", ["0.01", "0.000000001", "125.0", "1", "0.4200"])
def test_non_zero_estimate_is_recorded_exactly_and_awaits_approval(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    cost: str,
) -> None:
    provider.cost_text = cost

    report, _, _ = _run(paths, opener, env, clock)

    assert report["decision"] == "AWAITING_USER_APPROVAL"
    assert report["stop_reasons"] == []
    assert report["explicit_user_approval_required"] is True
    assert report["estimate_usd"] == str(Decimal(cost))
    assert report["credit"] == {
        "status": "unknown",
        "statement": "credit: unknown — user must state it",
        "facts": None,
    }
    template = report["approval_template"]
    assert isinstance(template, dict)
    assert template["approved_estimate_usd"] == str(Decimal(cost))
    assert template["user_states_credit_covers_estimate"] is True
    assert template["max_cash_charge_usd"] == "0"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: setattr(p, "schemas", ["trades", "mbo"]), "schema_unavailable"),
        (
            lambda p: setattr(
                p,
                "dataset_range",
                {"start": "2010-06-06T00:00:00Z", "end": "2026-10-15T00:00:00Z"},
            ),
            "dataset_range_does_not_cover_period",
        ),
        (
            lambda p: setattr(
                p,
                "symbology",
                {
                    "result": {
                        "GCZ6": [
                            {"d0": "2026-09-11", "d1": "2026-10-01", "s": "1"},
                            {"d0": "2026-10-01", "d1": "2026-11-01", "s": "2"},
                        ]
                    },
                    "partial": [],
                    "not_found": [],
                },
            ),
            "symbology_not_unique_or_incomplete",
        ),
        (
            lambda p: setattr(p, "symbology", {"result": {}, "partial": [], "not_found": ["GCZ6"]}),
            "symbology_not_unique_or_incomplete",
        ),
        (lambda p: setattr(p, "record_count", 0), "no_records_expected"),
    ],
)
def test_coverage_problems_stop_even_at_zero_cost(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    mutate: Callable[["FakeProvider"], None],
    reason: str,
) -> None:
    mutate(provider)

    report, _, _ = _run(paths, opener, env, clock)

    assert report["decision"] == "STOP_USER_DECISION"
    assert reason in report["stop_reasons"]  # type: ignore[operator]


def test_cached_payload_or_prior_job_stops(
    paths: ProjectPaths,
    opener: "FakeOpener",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
) -> None:
    raw_dir = paths.resolve(StorageCategory.RAW, "databento/glbx-mdp3/gcz6/2026-09-11--2026-11-01")
    raw_dir.mkdir(parents=True)
    (raw_dir / "metadata.json").write_bytes(kit.aux)
    ledger = ledger_path(paths)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    from moex_arbitrage_lab.sources.databento_history.request import REQUEST_SHA256

    ledger.write_text(
        json.dumps(
            {
                "event": "request",
                "endpoint": "batch.submit_job",
                "request_sha256": REQUEST_SHA256,
                "request_id": "r1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report, _, _ = _run(paths, opener, env, clock)

    assert report["decision"] == "STOP_USER_DECISION"
    reasons = report["stop_reasons"]
    assert isinstance(reasons, list)
    assert set(reasons) == {
        "identical_payload_already_cached",
        "batch_job_already_in_ledger",
    }
    cache = report["cache_check"]
    assert isinstance(cache, dict)
    assert cache["raw_files"] == ["metadata.json"]


def test_empty_cache_is_reported(
    paths: ProjectPaths, opener: "FakeOpener", env: dict[str, str], clock: "FakeClock"
) -> None:
    report, _, _ = _run(paths, opener, env, clock)

    assert report["cache_check"] == {
        "raw_dir": "data/raw/databento/glbx-mdp3/gcz6/2026-09-11--2026-11-01",
        "raw_dir_exists": False,
        "raw_files": [],
        "manifest_dir": "artifacts/manifests/raw/databento/glbx-mdp3/gcz6/2026-09-11--2026-11-01",
        "manifest_files": [],
        "ledger_path": "logs/databento/requests.jsonl",
        "ledger_job_creation_attempts": 0,
        "ledger_step_b_complete": False,
        "identical_payload_present": False,
    }


def test_key_never_in_report_or_ledger(
    paths: ProjectPaths,
    opener: "FakeOpener",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
    repo: Path,
    text_under: Callable[[Path], str],
) -> None:
    _run(paths, opener, env, clock)

    everything = text_under(repo)
    assert kit.fake_key not in everything
    assert "Basic " not in everything
    entries = [json.loads(line) for line in ledger_path(paths).read_text().splitlines()]
    assert [e["event"] for e in entries].count("request") == 6
    gate = entries[-1]
    assert gate["event"] == "cost_gate"
    assert gate["decision"] == "AWAITING_USER_APPROVAL"
    for name in (
        "request_id",
        "provider",
        "dataset",
        "schema",
        "symbols",
        "start_utc",
        "end_utc",
        "estimated_bytes",
        "estimated_cost_currency",
        "estimated_cost",
        "credit_checked_at",
        "decision",
        "raw_manifest_id",
        "actual_cost_if_known",
        "operator",
    ):
        assert name in gate


def test_missing_key_stops_before_anything(
    paths: ProjectPaths, opener: "FakeOpener", provider: "FakeProvider", clock: "FakeClock"
) -> None:
    with pytest.raises(MissingApiKeyError, match="DATABENTO_API_KEY not set"):
        run_step_a(paths, opener=opener, env={}, clock=clock, run_id="synthetic-a1")

    assert provider.requests == []
    assert not ledger_path(paths).exists()
    assert not paths.category_dir(StorageCategory.REPORTS).exists()


def test_provider_error_stops_without_report(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
) -> None:
    provider.fail_paths["metadata.get_cost"] = 500

    with pytest.raises(DatabentoError, match="http_status"):
        _run(paths, opener, env, clock)

    assert not paths.category_dir(StorageCategory.REPORTS).exists()


@pytest.mark.parametrize("body", ['"0"', "-1.0", "true", '{"cost": 0}'])
def test_malformed_cost_stops(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    body: str,
) -> None:
    provider.cost_text = body

    with pytest.raises(StepAError, match="get_cost"):
        _run(paths, opener, env, clock)


def test_report_is_create_once(
    paths: ProjectPaths, opener: "FakeOpener", env: dict[str, str], clock: "FakeClock"
) -> None:
    _run(paths, opener, env, clock, run_id="same")
    with pytest.raises(StepAError, match="create-once"):
        _run(paths, opener, env, clock, run_id="same")


def test_parsers() -> None:
    assert parse_iso_utc_ns("2026-09-01") == 1_788_220_800 * 10**9
    assert parse_iso_utc_ns("2026-09-01T00:00:00.000000001Z") == 1_788_220_800 * 10**9 + 1
    assert parse_iso_utc_ns("2026-09-01T00:00:00+00:00") == 1_788_220_800 * 10**9
    for bad in ("2026-09-01T00:00:00+03:00", "yesterday", 5, None, "2026-02-30"):
        assert parse_iso_utc_ns(bad) is None
    old_style = analyze_dataset_range({"start_date": "2017-05-21", "end_date": "2026-11-02"})
    assert old_style["covers_period"] is True
    with pytest.raises(StepAError):
        analyze_symbology({"result": {"GCZ6": [{"d0": 1}]}})
