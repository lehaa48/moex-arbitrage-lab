"""Sequential period acquisition, QA and loading against a synthetic project root (offline).

The template is synthetic (``.invalid`` host, synthetic raw filenames) but uses the frozen
calendar file; payloads are synthetic gzip CSVs served by a fake opener; clock and sleep are
fakes, so rate limiting is measured without waiting. Covers: canary first, all dates published
with raw bytes unchanged; <= 20 requests/min and >= 3 s apart; cache reuse without network;
HTTP 404 -> ``missing_file`` and continue (no retry); a canary 404 and every other rejection
stop at once with no overwrite and no further request; period identity; collisions; recovery
preflight; deterministic reports; per-file/period QA; the downstream loader.
"""

import itertools
import json
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.settings import ProjectPaths, StorageCategory
from moex_arbitrage_lab.settings.raw_publication import RecoveryConflict, RecoveryReport
from moex_arbitrage_lab.sources.tbank_history import (
    LoadedTemplate,
    PeriodReport,
    RequestPlan,
    acquire_period,
    build_day_config,
    load_period_trades,
    load_template,
    plan_for,
    plan_requests,
    qa_period,
    raw_relative,
)
from moex_arbitrage_lab.sources.tbank_history import period_acquire as period_acquire_module

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import (
        Builders,
        FakeClock,
        FakeOpener,
        FakeResponse,
        OpenerKit,
    )

REPO = Path(__file__).resolve().parents[3]
CALENDAR = (
    REPO
    / "config"
    / "sources"
    / "tbank_history"
    / "screen_v0_2"
    / ("moex_calendar_2026_09_10.toml")
)
UID = "00000000-0000-4000-8000-00000000d0d6"  # synthetic UUID, not a real instrument
OTHER_UID = "00000000-0000-4000-8000-00000000beef"  # synthetic UUID
SECOND = 1_000_000_000
WINDOW = 60 * SECOND

TEMPLATE = """# synthetic period template (tests only)
template_id = "synthetic-period"
period_start = {start}
period_end_exclusive = {end}
canary_date = {canary}
calendar_file = "moex_calendar_2026_09_10.toml"
url_template = "https://synthetic.invalid/history-trades/{{date}}?instrumentId=GDZ6_SPBFUT"
raw_filename_template = "synthetic-gdz6-{{date}}.csv.gz"

[base]
dataset = "history-trades"
source_schema = "anonymized-trades-csv"
payload_format = "gzip"
ticker_cc = "GDZ6_SPBFUT"
canonical_contract = "MOEX:GD:2026-12-18"
expiry = 2026-12-18
currency = "USD"
price_unit = "USD per troy ounce"
contract_multiplier = "1"
tick_size = "0.1"
event_ts_type = "TBANK_TRADE_TS_UTC"
clock_accuracy_documented = false
trade_action = "UNKNOWN"
csv_delimiter = ","
expected_columns = [
    "TRADE_TS", "TICKER_CC", "DIRECTION", "PRICE", "QUANTITY", "TRADE_SOURCE", "INSTRUMENT_UID",
]
csv_member_pattern = "[^/]+\\\\.csv"
max_members = 1
max_uncompressed_bytes = 1000000
expected_content_types = ["application/octet-stream"]
min_bytes = 20
max_bytes = 1000000
request_timeout_s = 60.0
requests_per_minute = 20
min_request_interval_s = 3.0
license_url = "https://synthetic.invalid/license"
license_version = "synthetic-1"
zero_cost_evidence_url = "https://synthetic.invalid/zero-cost"
cost_currency = "RUB"
"""


def make_template(directory: Path, start: date, end: date, canary: date) -> LoadedTemplate:
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(CALENDAR, directory / CALENDAR.name)
    path = directory / "synthetic-period.toml"
    path.write_text(TEMPLATE.format(start=start, end=end, canary=canary), encoding="utf-8")
    return load_template(path)


@pytest.fixture
def loaded(tmp_path: Path, csv_cleanup: None) -> LoadedTemplate:
    """2026-09-01..2026-09-07: regular 01,02,03,04,07 (canary 02), weekend 05,06."""
    return make_template(tmp_path / "cfg", date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 2))


@pytest.fixture
def plan(loaded: LoadedTemplate) -> RequestPlan:
    return plan_for(loaded)


def day_rows(day: date, uid: str = UID, ticker: str = "GDZ6_SPBFUT") -> list[list[str]]:
    d = day.isoformat()
    return [
        [f"{d}T03:55:00.100000Z", ticker, "B", "4012.3", "1", "EXCHANGE", uid],  # auction
        [f"{d}T04:00:00.000001Z", ticker, "S", "4012.4", "2", "EXCHANGE", uid],  # morning
        [f"{d}T08:00:00.123456789Z", ticker, "B", "4012.5", "1", "EXCHANGE", uid],  # main
        [f"{d}T08:00:00.123456789Z", ticker, "S", "4012.5", "1", "EXCHANGE", uid],  # tie
        [f"{d}T09:00:00.000000Z", ticker, "B", "4012.6", "3", "DEALER", uid],  # ineligible
        [f"{d}T17:00:00.500000Z", ticker, "S", "4012.7", "1", "EXCHANGE", uid],  # evening
    ]


def day_payload(builders: "Builders", day: date, **kwargs: str) -> bytes:
    return builders.gzip(builders.csv(day_rows(day, **kwargs)))


def _day_of(request: urllib.request.Request) -> date:
    return date.fromisoformat(request.full_url.split("/history-trades/", 1)[1][:10])


Special = Callable[[urllib.request.Request], "FakeResponse"]


@dataclass
class Server:
    """Synthetic archive: a normal gzip CSV per date unless ``special`` overrides a date."""

    builders: "Builders"
    http: "OpenerKit"
    special: dict[date, Special] = field(default_factory=dict)

    def ok(self, payload: bytes, content_type: str = "application/octet-stream") -> Special:
        def respond(request: urllib.request.Request) -> "FakeResponse":
            headers = {
                "Content-Type": content_type,
                "Content-Disposition": "attachment; filename=synthetic.csv.gz",
            }
            return self.http.response(200, headers, payload)

        return respond

    def status(self, code: int, headers: dict[str, str] | None = None) -> Special:
        def respond(request: urllib.request.Request) -> "FakeResponse":
            return self.http.response(code, headers or {"Content-Type": "text/plain"}, b"")

        return respond

    def __call__(self, request: urllib.request.Request) -> "FakeResponse":
        assert request.get_method() == "GET"
        assert "Authorization" not in request.headers
        day = _day_of(request)
        handler = self.special.get(day) or self.ok(day_payload(self.builders, day))
        return handler(request)


@dataclass
class FakeSleep:
    clock: "FakeClock"
    calls: list[float] = field(default_factory=list)

    def __call__(self, seconds: float) -> None:
        assert seconds > 0
        self.calls.append(seconds)
        self.clock.advance(round(seconds * SECOND))


@pytest.fixture
def server(builders: "Builders", http: "OpenerKit") -> Server:
    return Server(builders, http)


@pytest.fixture
def sleep(clock: "FakeClock") -> FakeSleep:
    return FakeSleep(clock)


def _run(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    opener: "FakeOpener",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> PeriodReport:
    return acquire_period(paths, loaded, plan, opener=opener, clock=clock, sleep=sleep)


def _ledger(paths: ProjectPaths) -> list[dict[str, Any]]:
    path = paths.category_dir(StorageCategory.LOGS) / "tbank" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _raw_state(paths: ProjectPaths) -> dict[str, bytes]:
    return {
        **_files(paths.category_dir(StorageCategory.RAW)),
        **_files(paths.category_dir(StorageCategory.MANIFESTS) / "raw"),
        **_files(paths.category_dir(StorageCategory.CHECKSUMS)),
    }


def _outcomes(report: PeriodReport) -> list[tuple[str, str]]:
    return [(o.trading_date.isoformat(), o.outcome) for o in report.outcomes]


# --- happy path, cache and determinism ------------------------------------------------------------


def test_period_is_acquired_canary_first_and_published_unchanged(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
    builders: "Builders",
) -> None:
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "complete"
    assert report.stop_reason is None
    assert [_day_of(r) for r in opener.requests] == list(plan.requested)
    assert opener.methods == ["GET"] * 5
    assert report.requests_sent == 5
    assert report.outcomes[0].role == "canary"
    assert report.outcomes[0].trading_date == date(2026, 9, 2)
    assert report.outcome_counts["published"] == 5
    assert report.instrument_uid == UID
    assert report.uid_status == "OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED"
    assert [(n.trading_date.isoformat(), n.reason) for n in report.not_requested] == [
        ("2026-09-05", "weekend_not_requested"),
        ("2026-09-06", "weekend_not_requested"),
    ]
    for outcome in report.outcomes:
        payload = day_payload(builders, outcome.trading_date)
        assert outcome.outcome == "published"
        assert outcome.network_request is True
        assert outcome.payload_sha256 == sha256_hex(payload)
        assert outcome.payload_bytes == len(payload)
        assert outcome.manifest_id is not None
        assert outcome.instrument_uid == UID
        assert outcome.eligible_rows == 5
        raw = paths.category_dir(StorageCategory.RAW) / str(outcome.raw_relative)
        assert raw.read_bytes() == payload  # raw bytes unchanged


def test_rate_limit_is_at_most_20_per_minute_and_3_s_apart(
    paths: ProjectPaths,
    tmp_path: Path,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
    csv_cleanup: None,
) -> None:
    loaded = make_template(
        tmp_path / "two-months", date(2026, 9, 1), date(2026, 11, 1), date(2026, 9, 2)
    )
    plan = plan_for(loaded)
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "complete"
    assert report.outcome_counts["published"] == 44
    times = [entry["utc_ns"] for entry in _ledger(paths)]
    assert len(times) == 44
    assert all(b - a >= 3 * SECOND for a, b in itertools.pairwise(times))
    assert max(sum(1 for t in times if start <= t < start + WINDOW) for start in times) <= 20
    assert sleep.calls  # the run waited instead of failing or bursting


def test_rerun_reuses_verified_cache_without_network(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> None:
    first = _run(paths, loaded, plan, http.opener(server), clock, sleep)
    raw_before = _raw_state(paths)
    ledger_before = _ledger(paths)

    refusing = http.opener(http.refusing)
    second = _run(paths, loaded, plan, refusing, clock, sleep)
    third = _run(paths, loaded, plan, refusing, clock, sleep)

    assert refusing.requests == []
    assert second.requests_sent == 0
    assert second.status == "complete"
    assert second.outcome_counts["reused_verified"] == 5
    assert [o.payload_sha256 for o in second.outcomes] == [o.payload_sha256 for o in first.outcomes]
    assert [o.manifest_id for o in second.outcomes] == [o.manifest_id for o in first.outcomes]
    assert _raw_state(paths) == raw_before
    assert _ledger(paths) == ledger_before
    # Determinism: two cached runs give byte-identical reports.
    assert second.model_dump_json() == third.model_dump_json()


def test_report_round_trips_as_json(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> None:
    report = _run(paths, loaded, plan, http.opener(server), clock, sleep)

    assert PeriodReport.model_validate_json(report.model_dump_json()) == report


# --- 404 and stops --------------------------------------------------------------------------------


def test_404_records_missing_file_and_continues_without_retry(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> None:
    missing = date(2026, 9, 3)
    server.special[missing] = server.status(404)
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "complete"
    assert [_day_of(r) for r in opener.requests].count(missing) == 1
    assert len(opener.requests) == 5
    entry = next(o for o in report.outcomes if o.trading_date == missing)
    assert (entry.outcome, entry.reason, entry.http_status) == (
        "missing_file",
        "http_404_file_not_found",
        404,
    )
    assert entry.payload_sha256 is None
    assert report.outcome_counts == {
        "published": 4,
        "reused_verified": 0,
        "missing_file": 1,
        "rejected": 0,
        "not_attempted": 0,
    }
    config = build_day_config(loaded, missing)
    assert not (paths.category_dir(StorageCategory.RAW) / raw_relative(config)).exists()


def test_canary_404_stops_everything(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> None:
    server.special[plan.canary] = server.status(404)
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "stopped"
    assert report.stop_reason == "canary_missing_file"
    assert len(opener.requests) == 1
    assert report.outcome_counts["not_attempted"] == 4
    assert _files(paths.category_dir(StorageCategory.RAW)) == {}


def _rejections(server: Server, builders: "Builders", day: date) -> dict[str, Special]:
    good = builders.csv(day_rows(day))
    return {
        "auth-401": server.status(401),
        "auth-403": server.status(403),
        "rate-429": server.status(429),
        "server-500": server.status(500),
        "redirect-302": server.status(302, {"Location": "https://synthetic.invalid/elsewhere"}),
        "html-content-type": server.ok(b"<html>synthetic</html>" * 4, "text/html"),
        "html-body": server.ok(b"<!DOCTYPE html><html>synthetic error page</html>"),
        "content-type": server.ok(builders.gzip(good), "application/json"),
        "not-gzip": server.ok(good),
        "corrupt-gzip": server.ok(builders.gzip(good)[:-6]),
        "trailing-garbage": server.ok(builders.gzip(good) + b"synthetic"),
        "header-mismatch": server.ok(
            builders.gzip(builders.csv(day_rows(day), header=("A", "B", "C", "D", "E", "F", "G")))
        ),
        "other-ticker": server.ok(
            builders.gzip(builders.csv(day_rows(day) + day_rows(day, ticker="SVZ6_SPBFUT")[:1]))
        ),
        "two-uids": server.ok(
            builders.gzip(builders.csv(day_rows(day) + day_rows(day, uid=OTHER_UID)[:1]))
        ),
        "bad-uid": server.ok(day_payload(builders, day, uid="synthetic-not-a-uuid")),
        "uid-changed": server.ok(day_payload(builders, day, uid=OTHER_UID)),
    }


EXPECTED_REASON = {
    "auth-401": "fetch:auth_required",
    "auth-403": "fetch:auth_required",
    "rate-429": "fetch:rate_limited",
    "server-500": "fetch:http_status",
    "redirect-302": "fetch:redirect_refused",
    "html-content-type": "fetch:html_payload",
    "html-body": "fetch:html_payload",
    "content-type": "fetch:content_type",
    "not-gzip": "archive:not_gzip",
    "corrupt-gzip": "archive:corrupt_gzip",
    "trailing-garbage": "archive:corrupt_gzip",
    "header-mismatch": "schema:header_mismatch",
    "other-ticker": "identity:other_ticker_rows",
    "two-uids": "identity:uid_multiple",
    "bad-uid": "identity:uid_not_uuid",
    "uid-changed": "identity:uid_changed_across_period",
}


# Position 0 = the canary, 2 = a later date. A UID change needs an earlier accepted file.
REJECTION_CASES = [
    (kind, position)
    for kind in sorted(EXPECTED_REASON)
    for position in (0, 2)
    if not (kind == "uid-changed" and position == 0)
]


@pytest.mark.parametrize(("kind", "position"), REJECTION_CASES)
def test_every_other_rejection_stops_without_retry_or_overwrite(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
    builders: "Builders",
    kind: str,
    position: int,
) -> None:
    day = plan.requested[position]
    server.special[day] = _rejections(server, builders, day)[kind]
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "stopped"
    assert report.stop_date == day
    assert report.stop_reason == EXPECTED_REASON[kind]
    # No retry and no further request after the failing one.
    assert [_day_of(r) for r in opener.requests] == list(plan.requested[: position + 1])
    assert [o.outcome for o in report.outcomes] == (
        ["published"] * position + ["rejected"] + ["not_attempted"] * (4 - position)
    )
    rejected = report.outcomes[position]
    assert rejected.network_request is True
    # The refused payload is never published.
    config = build_day_config(loaded, day)
    raw = paths.category_dir(StorageCategory.RAW) / raw_relative(config)
    assert not raw.exists()
    published = {o.raw_relative for o in report.outcomes if o.outcome == "published"}
    assert set(_files(paths.category_dir(StorageCategory.RAW))) == published


def test_failed_canary_identity_prevents_any_further_request(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
    builders: "Builders",
) -> None:
    server.special[plan.canary] = server.ok(day_payload(builders, plan.canary, uid=""))
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.stop_reason == "identity:uid_missing"
    assert len(opener.requests) == 1
    assert report.outcomes[0].payload_sha256 == sha256_hex(
        day_payload(builders, plan.canary, uid="")
    )


def test_rejection_then_rerun_does_not_overwrite_earlier_publications(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
    builders: "Builders",
) -> None:
    bad_day = plan.requested[3]
    server.special[bad_day] = server.status(429)
    _run(paths, loaded, plan, http.opener(server), clock, sleep)
    before = _raw_state(paths)

    del server.special[bad_day]
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "complete"
    assert [_day_of(r) for r in opener.requests] == list(plan.requested[3:])
    after = _raw_state(paths)
    assert {k: v for k, v in after.items() if k in before} == before


def test_foreign_file_at_a_raw_target_blocks_before_its_request(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> None:
    day = plan.requested[1]
    target = paths.category_dir(StorageCategory.RAW) / raw_relative(build_day_config(loaded, day))
    target.parent.mkdir(parents=True)
    target.write_bytes(b"synthetic foreign bytes")
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "stopped"
    assert report.stop_date == day
    assert str(report.stop_reason).startswith("blocked:")
    assert report.outcomes[1].network_request is False
    assert len(opener.requests) == 1  # only the canary
    assert target.read_bytes() == b"synthetic foreign bytes"


def test_collision_during_the_request_stops_without_overwrite(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> None:
    day = plan.requested[2]
    target = paths.category_dir(StorageCategory.RAW) / raw_relative(build_day_config(loaded, day))

    def plant(request: urllib.request.Request) -> None:
        if _day_of(request) == day:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"synthetic racing writer")

    opener = http.opener(server)
    opener.on_open.append(plant)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "stopped"
    assert report.stop_reason == "publication:RawCollisionError"
    assert len(opener.requests) == 3
    assert target.read_bytes() == b"synthetic racing writer"


def test_unclean_recovery_blocks_before_any_request(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unclean(_: ProjectPaths) -> RecoveryReport:
        conflict = RecoveryConflict(publication_id="0" * 32, kind="foreign_path", paths=())
        return RecoveryReport(conflicts=[conflict])

    monkeypatch.setattr(period_acquire_module, "recover_raw_publications", unclean)
    opener = http.opener(server)
    report = _run(paths, loaded, plan, opener, clock, sleep)

    assert report.status == "blocked"
    assert str(report.stop_reason).startswith("recovery_not_clean")
    assert opener.requests == []
    assert report.outcome_counts["not_attempted"] == 5


def test_own_rate_limit_fails_closed_if_the_clock_never_advances(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
) -> None:
    opener = http.opener(server)
    report = acquire_period(
        paths, loaded, plan, opener=opener, clock=clock, sleep=lambda seconds: None
    )

    assert report.status == "stopped"
    assert report.stop_reason == "fetch:rate_limited"
    assert len(opener.requests) == 1


# --- QA and loader --------------------------------------------------------------------------------


@pytest.fixture
def acquired(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    server: Server,
    http: "OpenerKit",
    clock: "FakeClock",
    sleep: FakeSleep,
) -> PeriodReport:
    server.special[date(2026, 9, 3)] = server.status(404)
    return _run(paths, loaded, plan, http.opener(server), clock, sleep)


def test_period_qa_is_deterministic_and_complete(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    acquired: PeriodReport,
    builders: "Builders",
) -> None:
    first = json.dumps(qa_period(paths, loaded, plan, acquired), sort_keys=True)
    second = json.dumps(qa_period(paths, loaded, plan, acquired), sort_keys=True)
    assert first == second
    qa = json.loads(first)

    assert qa["instrument_uid"] == UID
    assert qa["uid_status"] == "OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED"
    assert qa["dates"]["requested"] == 5
    assert qa["dates"]["verified"] == 4
    assert qa["dates"]["missing_file"] == ["2026-09-03"]
    assert qa["dates"]["not_published"] == []
    assert [e["reason"] for e in qa["dates"]["not_requested"]] == ["weekend_not_requested"] * 2
    assert qa["totals"]["csv_data_rows"] == 24
    assert qa["totals"]["eligible_rows"] == 20
    assert qa["exclusions_by_reason"]["ineligible:trade_source_not_exchange"] == 4
    assert qa["composition"]["TRADE_SOURCE"] == {"DEALER": 4, "EXCHANGE": 20}
    assert qa["composition"]["DIRECTION"] == {"B": 12, "S": 12}
    assert qa["timestamp"]["fractional_digits"] == {"6": 16, "9": 8}
    assert qa["timestamp"]["representation_quantum_ns"] == 1
    assert qa["timestamp"]["tied_rows"] == 4

    files = {f["trading_date"]: f for f in qa["files"]}
    canary = files["2026-09-02"]
    assert canary["status"] == "verified"
    assert canary["payload_sha256"] == sha256_hex(day_payload(builders, date(2026, 9, 2)))
    assert canary["identity"]["instrument_uid"] == UID
    assert canary["counts"] == {
        "raw_csv_lines_including_header": 7,
        "csv_data_rows": 6,
        "target_rows": 6,
        "parsed_rows": 6,
        "eligible_rows": 5,
    }
    assert canary["request_manifest"]["cost_decision"] == "FREE_CONFIRMED"
    assert canary["archive"]["member_name"] == "synthetic-gdz6-2026-09-02.csv"
    sessions = {s["name"]: s for s in canary["qa"]["sessions"]}
    assert sessions["opening_auction"]["continuous"] is False
    assert sessions["opening_auction"]["n_events"] == 1
    assert sessions["morning"]["first_event_utc"] == "2026-09-02T04:00:00.000001000Z"
    assert sessions["main"]["n_events"] == 2
    assert sessions["evening"]["last_event_utc"] == "2026-09-02T17:00:00.500000000Z"
    assert canary["qa"]["ties"]["tied_rows"] == 1
    assert files["2026-09-03"] == {
        "trading_date": "2026-09-03",
        "status": "missing_file",
        "raw_relative": raw_relative(build_day_config(loaded, date(2026, 9, 3))),
    }


def test_loader_returns_verified_eligible_trades_with_full_precision(
    paths: ProjectPaths, loaded: LoadedTemplate, acquired: PeriodReport
) -> None:
    days = load_period_trades(paths, acquired, loaded)

    assert [d.trading_date for d in days] == [
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 4),
        date(2026, 9, 7),
    ]
    first = days[0]
    assert len(first.eligible) == 5
    assert {t.trade_source for t in first.eligible} == {"EXCHANGE"}
    assert all(t.aggressor_side is None for t in first.eligible)
    assert [w.name for w in first.continuous_windows] == ["morning", "main", "evening"]
    assert [t.session_id for t in first.continuous_eligible()] == [
        "morning",
        "main",
        "main",
        "evening",
    ]
    main_ts = datetime(2026, 9, 1, 8, tzinfo=UTC)
    assert first.eligible[2].event_ts_utc_ns == int(main_ts.timestamp()) * SECOND + 123_456_789
    assert {d.instrument_uid for d in days} == {UID}


def test_loader_refuses_a_report_that_differs_from_disk(
    paths: ProjectPaths, loaded: LoadedTemplate, acquired: PeriodReport
) -> None:
    tampered = acquired.model_copy(
        update={
            "outcomes": tuple(
                o.model_copy(update={"payload_sha256": "0" * 64}) if o.position == 0 else o
                for o in acquired.outcomes
            )
        }
    )
    with pytest.raises(RuntimeError, match="BLOCKED"):
        load_period_trades(paths, tampered, loaded)

    other = acquired.model_copy(update={"template_sha256": "f" * 64})
    with pytest.raises(RuntimeError, match="BLOCKED"):
        load_period_trades(paths, other, loaded)


def test_loader_never_touches_the_network(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    acquired: PeriodReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the loader must not open a URL")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    assert len(load_period_trades(paths, acquired, loaded)) == 4


def test_plan_requests_matches_plan_for(loaded: LoadedTemplate) -> None:
    direct = plan_requests(
        date(2026, 9, 1), date(2026, 9, 8), loaded.calendar, canary=date(2026, 9, 2)
    )
    assert direct == plan_for(loaded)
    assert direct.requested == (
        date(2026, 9, 2),
        date(2026, 9, 1),
        date(2026, 9, 3),
        date(2026, 9, 4),
        date(2026, 9, 7),
    )


# --- CLI (offline refusals only; the real project root is never written) -------------------------


def _cli(argv: list[str]) -> int | str | None:
    from moex_arbitrage_lab.sources.tbank_history.cli import main

    try:
        return main(argv)
    except SystemExit as stop:
        return stop.code


@pytest.fixture
def no_urllib(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the CLI must not open a URL")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)


REAL_TEMPLATE = str(
    REPO / "config" / "sources" / "tbank_history" / "screen_v0_2" / "gdz6_base.toml"
)


def test_cli_plan_period_is_offline(no_urllib: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli(["plan-period", "--template", REAL_TEMPLATE]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["network"] is False
    assert document["requested_count"] == 44
    assert document["requested"][0]["date"] == "2026-09-02"


@pytest.mark.parametrize(
    "argv",
    [
        ["acquire-period", "--template", REAL_TEMPLATE, "--run-id", "synthetic"],
        ["acquire-period", "--template", REAL_TEMPLATE, "--confirm-network"],
        ["qa-period", "--template", REAL_TEMPLATE],
        ["plan-period"],
        ["plan-period", "--config", REAL_TEMPLATE],
    ],
)
def test_cli_period_refusals(no_urllib: None, argv: list[str]) -> None:
    assert _cli(argv) == 2
