"""Step B: approval gate, single job creation, verified create-once publication, idempotency."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.settings import ProjectPaths, StorageCategory
from moex_arbitrage_lab.settings.raw_publication import verify_published
from moex_arbitrage_lab.sources.databento_history import (
    DatabentoError,
    MissingApiKeyError,
    StepBOutcome,
    StepBRefusedError,
    ledger_path,
    run_step_a,
    run_step_b,
)
from moex_arbitrage_lab.sources.databento_history.request import REQUEST_SHA256

if TYPE_CHECKING:
    from tests.unit.databento_history.conftest import FakeClock, FakeOpener, FakeProvider, Kit

RAW = "databento/glbx-mdp3/gcz6/2026-09-11--2026-11-01"
DROP = "<drop>"  # removes a field in the synthetic approval helper
Approve = Callable[..., Path]


def _step_a(
    paths: ProjectPaths, opener: "FakeOpener", env: dict[str, str], clock: "FakeClock"
) -> tuple[dict[str, object], Path]:
    outcome = run_step_a(paths, opener=opener, env=env, clock=clock, run_id="synthetic-a1")
    return outcome.report, outcome.report_path


def _ready(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    **overrides: object,
) -> tuple[Path, Path]:
    report, report_path = _step_a(paths, opener, env, clock)
    provider.requests.clear()
    return approve(tmp_path, report_path, report, **overrides), report_path


def _run_b(
    paths: ProjectPaths,
    approval: Path,
    report: Path,
    opener: "FakeOpener",
    env: dict[str, str],
    clock: "FakeClock",
    sleeps: list[float],
    *,
    allow_redownload: bool = False,
) -> StepBOutcome:
    return run_step_b(
        paths,
        approval_path=approval,
        step_a_report_path=report,
        opener=opener,
        env=env,
        clock=clock,
        sleep=sleeps.append,
        poll_delays_s=(1.0, 2.0, 4.0),
        allow_redownload=allow_redownload,
    )


def _ledger(paths: ProjectPaths) -> list[dict[str, object]]:
    return [json.loads(line) for line in ledger_path(paths).read_text().splitlines()]


def _raw_files(paths: ProjectPaths) -> list[str]:
    raw = paths.category_dir(StorageCategory.RAW)
    return sorted(p.name for p in raw.rglob("*") if p.is_file()) if raw.exists() else []


def test_happy_path_one_job_verified_publications(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
    repo: Path,
    text_under: Callable[[Path], str],
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)

    outcome = _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert provider.endpoint_paths() == [
        "metadata.get_cost",
        "batch.submit_job",
        "batch.list_jobs",
        "batch.list_jobs",
        "batch.list_jobs",
        "batch.list_files",
        f"batch/download/synthetic-team/{kit.job_id}/{kit.csv_name}",
        f"batch/download/synthetic-team/{kit.job_id}/{kit.aux_name}",
    ]
    assert sleeps == [1.0, 2.0]
    assert outcome.job_id == kit.job_id
    assert outcome.job_created_now is True
    assert outcome.actual_cost_usd == "0.0"
    assert _raw_files(paths) == [kit.csv_name, kit.aux_name]
    manifest = verify_published(paths, f"{RAW}/{kit.csv_name}")
    assert manifest.cost_decision == "FREE_CONFIRMED"
    assert str(manifest.estimated_cost) == "0.0"
    assert manifest.venue == "COMEX"
    assert manifest.source_schema == "ohlcv-1s"
    assert manifest.request_parameters["symbols"] == "GCZ6"
    assert manifest.request_parameters["job_id"] == kit.job_id
    assert manifest.request_parameters["request_sha256"] == REQUEST_SHA256
    assert manifest.response_metadata["csv_rows"] == "3"
    assert manifest.payload_bytes == len(kit.csv())
    assert verify_published(paths, f"{RAW}/{kit.aux_name}").response_metadata["file_role"] == (
        "aux_json"
    )
    events = [e["event"] for e in _ledger(paths)]
    assert events.count("batch_job_created") == 1
    assert events[-2:] == ["step_b_complete", "cost_gate"]
    assert kit.fake_key not in text_under(repo)


def test_refuses_without_valid_approval(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    tmp_path: Path,
    sleeps: list[float],
) -> None:
    _, report = _step_a(paths, opener, env, clock)
    provider.requests.clear()
    missing = tmp_path / "absent.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"explicit_user_approval": false}', encoding="utf-8")

    for path in (missing, invalid):
        with pytest.raises(StepBRefusedError) as caught:
            _run_b(paths, path, report, opener, env, clock, sleeps)
        assert caught.value.reason == "approval_invalid"
    assert provider.requests == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"request_sha256": "0" * 64}, "request_hash_mismatch"),
        ({"step_a_report_sha256": "f" * 64}, "approval_report_mismatch"),
        ({"approved_estimate_usd": "0.01"}, "estimate_mismatch"),
        ({"approved_estimate_usd": "1"}, "estimate_mismatch"),
        # Exact string equality with the report's "0.0", not numeric equality.
        ({"approved_estimate_usd": "0"}, "estimate_mismatch"),
        ({"approved_estimate_usd": "0.00"}, "estimate_mismatch"),
        ({"approved_billable_size": 1}, "estimate_mismatch"),
        ({"approved_billable_size": DROP}, "approval_invalid"),
        ({"approved_billable_size_bytes": 69_135_752}, "approval_invalid"),
        ({"max_cash_charge_usd": DROP}, "approval_invalid"),
        ({"max_cash_charge_usd": "0.0"}, "approval_invalid"),
        ({"max_cash_charge_usd": "1"}, "approval_invalid"),
        ({"max_cash_charge_usd": 0}, "approval_invalid"),
        ({"user_states_credit_covers_estimate": "yes"}, "approval_invalid"),
        ({"request_sha256": DROP}, "approval_invalid"),
        ({"step_a_report_sha256": DROP}, "approval_invalid"),
        ({"explicit_user_approval": False}, "approval_invalid"),
        ({"approved_at_utc": "yesterday"}, "approval_invalid"),
        ({"unexpected": "field"}, "approval_invalid"),
        ({"approved_estimate_usd": 0}, "approval_invalid"),
    ],
)
def test_refuses_mismatched_approval(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    overrides: dict[str, object],
    reason: str,
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path, **overrides)

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert caught.value.reason == reason
    assert provider.requests == []
    assert _raw_files(paths) == []


@pytest.mark.parametrize("statement", [DROP, False, None])
def test_non_zero_estimate_needs_the_user_credit_statement(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    statement: object,
) -> None:
    provider.cost_text = "0.42"
    approval, report = _ready(
        paths,
        opener,
        provider,
        env,
        clock,
        approve,
        tmp_path,
        user_states_credit_covers_estimate=statement,
    )

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert caught.value.reason == "credit_statement_missing"
    assert provider.requests == []


def test_non_zero_estimate_covered_by_user_stated_credit_proceeds(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
) -> None:
    provider.cost_text = "0.42"
    provider.job_cost_text = "0.42"
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)

    outcome = _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert outcome.actual_cost_usd == "0.42"
    assert provider.endpoint_paths().count("batch.submit_job") == 1
    manifest = verify_published(paths, f"{RAW}/{kit.csv_name}")
    assert str(manifest.estimated_cost) == "0.42"
    assert str(manifest.actual_cost_if_known) == "0.42"
    assert manifest.response_metadata["user_states_credit_covers_estimate"] == "true"
    assert manifest.response_metadata["max_cash_charge_usd"] == "0"
    gate = _ledger(paths)[-1]
    assert gate["event"] == "cost_gate"
    assert gate["estimated_cost"] == "0.42"
    assert gate["user_states_credit_covers_estimate"] is True
    assert gate["max_cash_charge_usd"] == "0"
    assert gate["credit_checked_at"] == "2026-11-02T12:00:00Z"


def test_tampered_report_is_refused(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    copy = tmp_path / "copy.json"
    original = report.read_bytes()
    copy.write_bytes(original.replace(b'"AWAITING_USER_APPROVAL"', b'"AWAITING_USER_APPROVAL" '))
    assert copy.read_bytes() != original

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, copy, opener, env, clock, sleeps)

    assert caught.value.reason == "approval_report_mismatch"


@pytest.mark.parametrize(
    ("approved", "now"), [("0.0", "0.05"), ("0.42", "0.43"), ("0.42", "0.0"), ("0.42", "0.41")]
)
def test_changed_cost_before_creation_stops_without_job(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    approved: str,
    now: str,
) -> None:
    provider.cost_text = approved
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    provider.cost_text = now

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert caught.value.reason == "estimate_changed"
    assert "batch.submit_job" not in provider.endpoint_paths()


def test_missing_key_stops_step_b_before_any_request(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)

    with pytest.raises(MissingApiKeyError):
        _run_b(paths, approval, report, opener, {}, clock, sleeps)

    assert provider.requests == []


def test_single_job_creation_across_poll_failure_and_rerun(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    provider.job_states = ["queued"]

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)
    assert caught.value.reason == "poll_timeout"
    assert sleeps == [1.0, 2.0, 4.0]

    provider.fail_paths["batch.list_jobs"] = 503
    with pytest.raises(DatabentoError, match="http_status"):
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    provider.fail_paths.clear()
    provider.job_states = ["done"]
    outcome = _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert outcome.job_created_now is False
    assert outcome.job_id == kit.job_id
    assert provider.endpoint_paths().count("batch.submit_job") == 1
    assert provider.endpoint_paths().count("metadata.get_cost") == 1
    assert _raw_files(paths) == [kit.csv_name, kit.aux_name]


def test_job_creation_without_recorded_id_is_never_retried(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    provider.raise_on["batch.submit_job"] = TimeoutError("synthetic timeout")

    with pytest.raises(DatabentoError, match="timeout"):
        _run_b(paths, approval, report, opener, env, clock, sleeps)
    provider.raise_on.clear()
    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert caught.value.reason == "job_creation_outcome_unknown"
    assert provider.endpoint_paths().count("batch.submit_job") == 1


@pytest.mark.parametrize(
    ("approved", "where"),
    [("0.0", "created"), ("0.0", "done"), ("0.42", "created"), ("0.42", "done")],
)
def test_job_cost_above_the_approved_estimate_stops_before_download(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    approved: str,
    where: str,
) -> None:
    provider.cost_text = approved
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    provider.job_cost_text = "0.5"
    if where == "done":
        provider.create_response = {**provider.job("received"), "cost_usd": float(approved)}

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert caught.value.reason == "job_cost_exceeds_approval"
    assert not any(p.startswith("batch/download") for p in provider.endpoint_paths())
    assert provider.endpoint_paths().count("batch.submit_job") == 1
    assert _raw_files(paths) == []
    recorded = [e for e in _ledger(paths) if e["event"] == "job_cost_exceeds_approval"]
    assert len(recorded) == 1
    assert recorded[0]["stage"] == where
    assert recorded[0]["job_cost_usd"] == "0.5"
    assert recorded[0]["approved_estimate_usd"] == approved


def test_job_echo_mismatch_stops(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    provider.create_response = {**provider.job("received"), "schema": "trades"}

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert caught.value.reason == "job_parameters_mismatch"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        ("bad_csv", "payload_rejected"),
        ("html", "payload_rejected"),
        ("hash", "hash_mismatch"),
        ("size", "size_mismatch"),
        ("host", "download_url_refused"),
        ("algo", "provider_hash_unrecognized"),
        ("name", "files_listing_invalid"),
    ],
)
def test_download_or_validation_failure_leaves_no_raw(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
    mutate: str,
    reason: str,
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    if mutate == "bad_csv":
        provider.files[kit.csv_name] = kit.csv([f"{kit.start_ns + 10**9},32,1,999,1,1,1,1,1,GCZ6"])
    elif mutate == "html":
        provider.files[kit.csv_name] = b"<html><body>synthetic error page</body></html>"
    elif mutate == "hash":
        provider.listed_hashes[kit.csv_name] = "sha256:" + "0" * 64
    elif mutate == "size":
        provider.listed_sizes[kit.aux_name] = len(kit.aux) + 1
    elif mutate == "host":
        provider.download_host = "evil.example"
    elif mutate == "algo":
        provider.listed_hashes[kit.csv_name] = "md5:abc"
    else:
        provider.files = {"GLBX.CSV": kit.csv()}

    with pytest.raises((StepBRefusedError, DatabentoError)) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    error = caught.value
    assert isinstance(error, StepBRefusedError | DatabentoError)
    assert error.reason == reason
    assert _raw_files(paths) == []
    assert not paths.category_dir(StorageCategory.MANIFESTS).exists()
    assert provider.endpoint_paths().count("batch.submit_job") == 1


def test_failed_download_is_not_repeated_without_explicit_permission(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    good = provider.files[kit.csv_name]
    provider.files[kit.csv_name] = b"<html>synthetic</html>"
    provider.listed_hashes[kit.csv_name] = "sha256:" + hashlib.sha256(good).hexdigest()
    provider.listed_sizes[kit.csv_name] = len(provider.files[kit.csv_name])
    with pytest.raises(StepBRefusedError):
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)
    assert caught.value.reason == "download_already_attempted"

    provider.files[kit.csv_name] = good
    provider.listed_sizes.clear()
    outcome = _run_b(paths, approval, report, opener, env, clock, sleeps, allow_redownload=True)

    assert outcome.job_created_now is False
    assert provider.endpoint_paths().count("batch.submit_job") == 1
    assert _raw_files(paths) == [kit.csv_name, kit.aux_name]


def test_idempotent_rerun_uses_verified_publications_without_network_or_key(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    first = _run_b(paths, approval, report, opener, env, clock, sleeps)
    before = ledger_path(paths).read_bytes()

    again = _run_b(paths, approval, report, kit.refusing_opener(), {}, clock, sleeps)

    assert again.network_used is False
    assert [f.payload_sha256 for f in again.files] == [f.payload_sha256 for f in first.files]
    assert all(f.already_published for f in again.files)
    assert ledger_path(paths).read_bytes() == before


def test_tampered_publication_stops_idempotent_rerun(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    _run_b(paths, approval, report, opener, env, clock, sleeps)
    payload = paths.resolve(StorageCategory.RAW, f"{RAW}/{kit.aux_name}")
    payload.chmod(0o644)
    payload.write_bytes(b'{"tampered": true}')

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, kit.refusing_opener(), {}, clock, sleeps)

    assert caught.value.reason == "completion_unverifiable"


def test_existing_publication_is_never_overwritten(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    approve: Approve,
    tmp_path: Path,
    sleeps: list[float],
    kit: "Kit",
) -> None:
    approval, report = _ready(paths, opener, provider, env, clock, approve, tmp_path)
    foreign = paths.resolve(StorageCategory.RAW, f"{RAW}/{kit.aux_name}")
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b'{"foreign": true}')

    with pytest.raises(StepBRefusedError) as caught:
        _run_b(paths, approval, report, opener, env, clock, sleeps)

    assert caught.value.reason == "publication_unverifiable"
    assert foreign.read_bytes() == b'{"foreign": true}'
    assert not any(p.startswith("batch/download") for p in provider.endpoint_paths())
