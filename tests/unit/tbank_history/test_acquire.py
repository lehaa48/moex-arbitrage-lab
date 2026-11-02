"""Create-once acquisition and QA runs against a synthetic project root (both payload formats).

The repository is a synthetic ``ProjectPaths`` under ``tmp_path`` (parent conftest ``paths``);
the HTTP opener is a fake serving synthetic bytes; the socket guard stays active. Covers:
publication with manifest/checksum/marker that ``verify_published`` accepts, a second acquire
that never calls the opener or overwrites, recovery conflicts blocking before any network,
fetch/archive rejections leaving no raw artifacts, retries that honour the rate limit, and
deterministic create-once QA reports. CSV-format payloads are removed at teardown.
"""

import email.message
import io
import json
import re
import stat
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.settings import ProjectPaths, StorageCategory
from moex_arbitrage_lab.settings.raw_publication import verify_published
from moex_arbitrage_lab.sources.tbank_history import (
    ArchiveRejectedError,
    FetchRejectedError,
    TbankHistoryConfig,
    acquire,
    raw_relative,
    run_qa,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import (
        Builders,
        ConfigFactory,
        FakeClock,
        FakeResponse,
        OpenerKit,
    )

SECOND = 1_000_000_000
FORMATS = ["zip", "csv"]


@pytest.fixture(params=FORMATS)
def fmt(request: pytest.FixtureRequest, csv_cleanup: None) -> str:
    value = request.param
    assert isinstance(value, str)
    return value


def _payload(fmt: str, builders: "Builders") -> bytes:
    return builders.synthetic_zip if fmt == "zip" else builders.synthetic_csv


def _content_type(fmt: str) -> str:
    return "application/zip" if fmt == "zip" else "text/csv"


def _files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _raw_artifacts(paths: ProjectPaths) -> dict[str, bytes]:
    return {
        **_files(paths.category_dir(StorageCategory.RAW)),
        **_files(paths.category_dir(StorageCategory.MANIFESTS) / "raw"),
        **_files(paths.category_dir(StorageCategory.CHECKSUMS)),
    }


def _cost_ledger_files(paths: ProjectPaths) -> list[Path]:
    directory = paths.category_dir(StorageCategory.MANIFESTS) / "tbank" / "cost-ledger"
    return sorted(directory.glob("*.json")) if directory.exists() else []


def _requests(paths: ProjectPaths) -> list[dict[str, Any]]:
    path = paths.category_dir(StorageCategory.LOGS) / "tbank" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


# --- layout ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("payload_format", FORMATS)
def test_raw_relative_follows_the_daily_layout(
    make_config: "ConfigFactory", payload_format: str
) -> None:
    config = make_config(payload_format)

    assert raw_relative(config) == (
        f"tbank/{config.dataset}/gdz6/{config.archive_date.isoformat()}/{config.raw_filename}"
    )


# --- happy path and no-overwrite ------------------------------------------------------------------


def test_acquire_publishes_raw_manifest_checksum_and_marker(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
    scalars: Callable[[Any], list[Any]],
) -> None:
    config = make_config(fmt)
    payload = _payload(fmt, builders)
    opener = http.opener(http.ok(payload, _content_type(fmt)))

    outcome = acquire(paths, config, opener=opener, clock=clock)

    relative = raw_relative(config)
    assert outcome.relative == relative
    assert outcome.already_published is False
    assert outcome.payload_sha256 == sha256_hex(payload)
    assert outcome.payload_bytes == len(payload)
    assert opener.methods.count("GET") == 1

    manifest = verify_published(paths, relative)
    assert manifest == outcome.manifest
    assert manifest.payload_sha256 == sha256_hex(payload)
    assert manifest.payload_bytes == len(payload)
    assert manifest.cost_decision == "FREE_CONFIRMED"
    assert manifest.venue == "MOEX"
    assert manifest.canonical_contract == config.canonical_contract
    assert manifest.trading_date == config.archive_date
    assert manifest.license_url == config.license_url
    assert manifest.license_version == config.license_version
    assert manifest.response_status == 200
    assert manifest.estimated_cost == 0
    assert manifest.retrieval_started_utc_ns <= manifest.retrieval_completed_utc_ns

    raw = paths.category_dir(StorageCategory.RAW) / relative
    assert raw.read_bytes() == payload  # raw bytes kept unchanged
    manifests = paths.category_dir(StorageCategory.MANIFESTS) / "raw"
    assert (manifests / f"{relative}.manifest.json").is_file()
    assert (manifests / f"{relative}.commit.json").is_file()
    checksums = paths.category_dir(StorageCategory.CHECKSUMS) / "raw"
    assert (checksums / f"{relative}.sha256").read_text("ascii").startswith(sha256_hex(payload))

    ledgers = _cost_ledger_files(paths)
    assert len(ledgers) == 1
    assert manifest.cost_ledger_request_id == ledgers[0].stem
    assert "FREE_CONFIRMED" in scalars(json.loads(ledgers[0].read_text("utf-8")))

    requests = _requests(paths)
    assert [r["method"] for r in requests].count("GET") == 1
    assert all(r["url"] == config.url for r in requests)
    manifest_text = (manifests / f"{relative}.manifest.json").read_text("utf-8")
    assert "synthetic-session" not in manifest_text  # Set-Cookie never persisted


def test_second_acquire_never_calls_the_opener_or_overwrites(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    config = make_config(fmt)
    acquire(
        paths,
        config,
        opener=http.opener(http.ok(_payload(fmt, builders), _content_type(fmt))),
        clock=clock,
    )
    before = _raw_artifacts(paths)
    requests_before = _requests(paths)
    ledgers_before = _cost_ledger_files(paths)

    # Same clock instant: a re-download would also be rate limited, so the short-circuit must
    # come before the network and before the ledger check.
    refusing = http.opener(http.refusing)
    again = acquire(paths, config, opener=refusing, clock=clock)

    assert again.already_published is True
    assert refusing.requests == []
    assert again.payload_sha256 == sha256_hex(_payload(fmt, builders))
    assert _raw_artifacts(paths) == before
    assert _requests(paths) == requests_before
    assert _cost_ledger_files(paths) == ledgers_before


def _make_writable(path: Path) -> None:
    path.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_tampered_publication_fails_closed_and_is_not_overwritten(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    config = make_config(fmt)
    payload = _payload(fmt, builders)
    acquire(paths, config, opener=http.opener(http.ok(payload, _content_type(fmt))), clock=clock)
    raw = paths.category_dir(StorageCategory.RAW) / raw_relative(config)
    _make_writable(raw)
    raw.write_bytes(payload + b"synthetic tamper")
    clock.advance(600 * SECOND)

    with pytest.raises(RuntimeError):
        acquire(
            paths, config, opener=http.opener(http.ok(payload, _content_type(fmt))), clock=clock
        )

    assert raw.read_bytes() == payload + b"synthetic tamper"


def test_tampered_publication_is_detected_before_any_download(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    config = make_config(fmt)
    payload = _payload(fmt, builders)
    acquire(paths, config, opener=http.opener(http.ok(payload, _content_type(fmt))), clock=clock)
    raw = paths.category_dir(StorageCategory.RAW) / raw_relative(config)
    _make_writable(raw)
    raw.write_bytes(payload + b"synthetic tamper")
    clock.advance(600 * SECOND)
    refusing = http.opener(http.refusing)

    with pytest.raises(RuntimeError):
        acquire(paths, config, opener=refusing, clock=clock)

    assert refusing.requests == []


# --- blocked and rejected acquisitions ------------------------------------------------------------


def _plant_dead_publication_with_foreign_final(paths: ProjectPaths) -> None:
    """A dead publisher's journal and staging file whose final name holds a foreign file:
    ``recover_raw_publications`` reports a ``foreign_path`` conflict (not clean)."""
    relative = "synthetic/synthetic-other/x/2026-10-15/day.bin"
    publication_id = "e" * 32
    workdir = paths.category_dir(StorageCategory.TEMP) / "raw-publication"
    workdir.mkdir(parents=True)
    header = {"journal_version": 1, "publication_id": publication_id, "relative": relative}
    (workdir / f"{publication_id}.journal").write_text(json.dumps(header) + "\n", "ascii")
    (workdir / f"{publication_id}.0.part").write_bytes(b"synthetic staged payload")
    final = paths.category_dir(StorageCategory.RAW) / relative
    final.parent.mkdir(parents=True)
    final.write_bytes(b"synthetic foreign payload")


def test_unclean_recovery_blocks_before_any_network(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    _plant_dead_publication_with_foreign_final(paths)
    opener = http.opener(http.refusing)

    with pytest.raises(RuntimeError, match="BLOCKED"):
        acquire(paths, make_config(fmt), opener=opener, clock=clock)

    assert opener.requests == []
    assert _requests(paths) == []
    assert _cost_ledger_files(paths) == []
    assert not (paths.category_dir(StorageCategory.RAW) / raw_relative(make_config(fmt))).exists()


def _http_error_responder(code: int) -> Callable[[urllib.request.Request], "FakeResponse"]:
    def respond(request: urllib.request.Request) -> "FakeResponse":
        raise urllib.error.HTTPError(
            request.full_url, code, "synthetic", email.message.Message(), io.BytesIO(b"")
        )

    return respond


@pytest.mark.parametrize(("code", "reason"), [(401, "auth_required"), (404, "http_status")])
def test_http_rejection_leaves_no_raw_artifacts(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
    code: int,
    reason: str,
) -> None:
    with pytest.raises(FetchRejectedError) as caught:
        acquire(
            paths, make_config(fmt), opener=http.opener(_http_error_responder(code)), clock=clock
        )

    assert caught.value.reason == reason
    assert _raw_artifacts(paths) == {}


def test_html_response_leaves_no_raw_artifacts(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    body = b"<!DOCTYPE html><html>synthetic login page</html>" * 4
    opener = http.opener(http.ok(body, "text/html; charset=utf-8"))

    with pytest.raises(FetchRejectedError) as caught:
        acquire(paths, make_config(fmt), opener=opener, clock=clock)

    assert caught.value.reason == "html_payload"
    assert _raw_artifacts(paths) == {}


def test_html_body_with_an_allowed_content_type_is_rejected_without_raw_artifacts(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    body = b"<!DOCTYPE html><html>synthetic error page</html>" * 4
    opener = http.opener(http.ok(body, _content_type(fmt)))

    with pytest.raises((ArchiveRejectedError, FetchRejectedError)) as caught:
        acquire(paths, make_config(fmt), opener=opener, clock=clock)

    assert getattr(caught.value, "reason", None) == "html_payload"
    assert _raw_artifacts(paths) == {}


@pytest.mark.parametrize("payload_format", FORMATS)
def test_invalid_archive_leaves_no_raw_artifacts(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    csv_cleanup: None,
    payload_format: str,
) -> None:
    config = make_config(payload_format)
    if payload_format == "zip":
        # Two members (well-formed zip, wrong shape) served as application/zip.
        body = builders.zip(
            [
                (builders.member_name, builders.synthetic_csv),
                ("synthetic-gdz6-20261016.csv", builders.synthetic_csv),
            ]
        )
        expected = "member_count"
        content_type = "application/zip"
    else:
        body = builders.synthetic_zip  # a zip where a plain CSV is expected
        expected = "unexpected_zip"
        content_type = "application/octet-stream"

    with pytest.raises(ArchiveRejectedError) as caught:
        acquire(paths, config, opener=http.opener(http.ok(body, content_type)), clock=clock)

    assert caught.value.reason == expected
    assert _raw_artifacts(paths) == {}


def test_truncated_zip_leaves_no_raw_artifacts(
    paths: ProjectPaths,
    config: TbankHistoryConfig,
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
) -> None:
    partial = builders.synthetic_zip[: len(builders.synthetic_zip) // 2]
    assert len(partial) >= 64  # passes the min_bytes check; only the archive check refuses it

    with pytest.raises(ArchiveRejectedError) as caught:
        acquire(paths, config, opener=http.opener(http.ok(partial)), clock=clock)

    assert caught.value.reason in {"corrupt_zip", "not_zip"}
    assert _raw_artifacts(paths) == {}


def test_retry_after_a_failure_honours_the_rate_limit_and_publishes_once(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    config = make_config(fmt)
    payload = _payload(fmt, builders)
    with pytest.raises(FetchRejectedError):
        acquire(paths, config, opener=http.opener(_http_error_responder(503)), clock=clock)

    # Immediate retry: refused by the ledger, the opener is never called.
    early = http.opener(http.refusing)
    with pytest.raises(FetchRejectedError) as caught:
        acquire(paths, config, opener=early, clock=clock)
    assert caught.value.reason == "rate_limited"
    assert early.requests == []
    assert _raw_artifacts(paths) == {}

    clock.advance(int(config.min_request_interval_s * SECOND) + SECOND)
    outcome = acquire(
        paths, config, opener=http.opener(http.ok(payload, _content_type(fmt))), clock=clock
    )

    assert outcome.already_published is False
    verify_published(paths, outcome.relative)
    assert [r["method"] for r in _requests(paths)].count("GET") == 2


# --- QA runs --------------------------------------------------------------------------------------


def _report_dir(paths: ProjectPaths, run_id: str) -> Path:
    return paths.category_dir(StorageCategory.REPORTS) / "p1-tbank-gdz6" / run_id


def _without_run_id(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text("utf-8"))
    assert isinstance(data, dict)
    data.pop("run_id", None)
    return data


def test_run_qa_writes_create_once_deterministic_reports(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    config = make_config(fmt)
    payload = _payload(fmt, builders)
    acquire(paths, config, opener=http.opener(http.ok(payload, _content_type(fmt))), clock=clock)

    first = Path(run_qa(paths, config, run_id="run-0001"))
    second = Path(run_qa(paths, config, run_id="run-0002"))

    assert first == _report_dir(paths, "run-0001")
    assert second == _report_dir(paths, "run-0002")
    for name in ("reconciliation.json", "qa.json", "run.json"):
        assert (first / name).is_file()
        assert (second / name).is_file()
    assert (first / "qa.json").read_bytes() == (second / "qa.json").read_bytes()
    # reconciliation.json may name its run; every count must be identical.
    assert _without_run_id(first / "reconciliation.json") == _without_run_id(
        second / "reconciliation.json"
    )
    run_digests = {
        json.loads((d / "run.json").read_text("utf-8"))["parse_digest"] for d in (first, second)
    }
    assert len(run_digests) == 1  # the parse of the same raw bytes is reproducible

    reconciliation = json.loads((first / "reconciliation.json").read_text("utf-8"))
    assert reconciliation["csv_data_rows"] == 4
    assert reconciliation["parsed_rows"] == 4
    assert reconciliation["eligible_rows"] == 3
    assert reconciliation["ineligible"] == {"trade_source_not_exchange": 1}
    run = json.loads((first / "run.json").read_text("utf-8"))
    assert re.fullmatch(r"[0-9a-f]{64}", run["parse_digest"])
    assert "trades" not in run

    # No row-level data in any report: no record ids, no synthetic prices.
    for name in ("reconciliation.json", "qa.json", "run.json"):
        text = (first / name).read_text("utf-8")
        assert "row-2" not in text
        assert "4012.3" not in text
    assert not list(first.rglob("*.csv"))


def test_run_qa_never_overwrites_an_existing_run(
    paths: ProjectPaths,
    make_config: "ConfigFactory",
    builders: "Builders",
    http: "OpenerKit",
    clock: "FakeClock",
    fmt: str,
) -> None:
    config = make_config(fmt)
    acquire(
        paths,
        config,
        opener=http.opener(http.ok(_payload(fmt, builders), _content_type(fmt))),
        clock=clock,
    )
    report = Path(run_qa(paths, config, run_id="run-0001"))
    before = _files(report)

    with pytest.raises((OSError, RuntimeError, ValueError)):
        run_qa(paths, config, run_id="run-0001")

    assert _files(report) == before


def test_run_qa_without_a_verified_publication_fails_closed(
    paths: ProjectPaths, config: TbankHistoryConfig
) -> None:
    with pytest.raises((OSError, RuntimeError)):
        run_qa(paths, config, run_id="run-0001")

    assert (
        not _report_dir(paths, "run-0001").exists() or _files(_report_dir(paths, "run-0001")) == {}
    )


# --- CLI (in-process, offline) --------------------------------------------------------------------


def _cli_main() -> Callable[[list[str]], int | None]:
    import importlib

    try:
        module = importlib.import_module("moex_arbitrage_lab.sources.tbank_history.cli")
    except ModuleNotFoundError:
        pytest.skip("no in-process CLI module (moex_arbitrage_lab.sources.tbank_history.cli)")
    main = getattr(module, "main", None)
    if main is None:
        pytest.skip("CLI module exposes no main(argv)")
    assert callable(main)
    return cast(Callable[[list[str]], int | None], main)


def _exit_code(main: Callable[[list[str]], int | None], argv: list[str]) -> int | str | None:
    try:
        return main(argv)
    except SystemExit as stop:
        return stop.code


@pytest.fixture
def no_urllib(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the CLI must not open a URL without --confirm-network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)


@pytest.mark.parametrize("command", ["probe", "acquire"])
def test_cli_network_commands_require_confirmation(
    tmp_path: Path, builders: "Builders", no_urllib: None, command: str
) -> None:
    main = _cli_main()
    config_path = tmp_path / "synthetic-tbank.toml"
    config_path.write_text(builders.toml(builders.base_config_values()), encoding="utf-8")

    assert _exit_code(main, [command, "--config", str(config_path)]) == 2


def test_cli_plan_is_offline(tmp_path: Path, builders: "Builders", no_urllib: None) -> None:
    main = _cli_main()
    config_path = tmp_path / "synthetic-tbank.toml"
    config_path.write_text(builders.toml(builders.base_config_values()), encoding="utf-8")

    assert _exit_code(main, ["plan", "--config", str(config_path)]) in {0, None}
