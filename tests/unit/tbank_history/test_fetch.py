"""Bounded HTTP fetch through an injected fake opener, and the request ledger (synthetic).

No socket is ever opened: the opener is a fake and the parent conftest refuses every socket.
Covers status/redirect/auth/timeouts/network errors, content-type and size limits, header
allowlisting, no credentials in requests, rate limiting (interval and per-minute) and that a
request is recorded in the ledger before it is sent.
"""

import email.message
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest

from moex_arbitrage_lab.settings import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.tbank_history import (
    FetchRejectedError,
    RequestLedger,
    TbankHistoryConfig,
    fetch,
    ledger_path,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import (
        Builders,
        ConfigFactory,
        FakeClock,
        FakeResponse,
        OpenerKit,
        Responder,
    )

SECOND = 1_000_000_000
ALLOWED_HEADERS = {
    "content-type",
    "content-length",
    "etag",
    "last-modified",
    "date",
    "cache-control",
    "content-disposition",
    "accept-ranges",
    "server",
}
CREDENTIAL_HEADERS = {"authorization", "cookie", "proxy-authorization"}
SYNTHETIC_TOKEN_VALUE = "synthetic-not-a-real-token-value-0000"  # noqa: S105 - synthetic, not real


@pytest.fixture
def ledger(tmp_path: Path) -> RequestLedger:
    return RequestLedger(tmp_path / "requests.jsonl")


def _lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _http_error(url: str, code: int, headers: dict[str, str] | None = None) -> Exception:
    message = email.message.Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError(url, code, "synthetic", message, io.BytesIO(b""))


def _raising(error: BaseException) -> "Responder":
    def respond(request: urllib.request.Request) -> "FakeResponse":
        raise error

    return respond


def _status(http: "OpenerKit", status: int, headers: dict[str, str], body: bytes) -> "Responder":
    def respond(request: urllib.request.Request) -> "FakeResponse":
        return http.response(status, headers, body)

    return respond


def _reject(
    method: Literal["HEAD", "GET"],
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    responder: "Responder",
    http: "OpenerKit",
    clock: "FakeClock",
) -> str:
    opener = http.opener(responder)
    with pytest.raises(FetchRejectedError) as caught:
        fetch(method, config, ledger=ledger, opener=opener, clock=clock)
    assert len(opener.requests) == 1
    return caught.value.reason


# --- success --------------------------------------------------------------------------------------


def test_get_returns_body_status_and_allowlisted_headers(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    builders: "Builders",
) -> None:
    opener = http.opener(http.ok())

    result = fetch("GET", config, ledger=ledger, opener=opener, clock=clock)

    assert result.status == 200
    assert result.method == "GET"
    assert result.url == config.url
    assert result.body == builders.synthetic_zip
    assert result.started_utc_ns <= result.completed_utc_ns
    headers = dict(result.headers)
    assert set(headers) <= ALLOWED_HEADERS
    assert all(name == name.lower() for name in headers)
    assert headers["content-type"] == "application/zip"
    assert headers["etag"] == '"synthetic-etag"'
    assert "set-cookie" not in headers
    assert "x-synthetic-debug" not in headers
    request = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == config.url
    assert opener.timeouts == [pytest.approx(float(config.request_timeout_s))]


def test_head_returns_empty_body(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    opener = http.opener(http.ok())

    result = fetch("HEAD", config, ledger=ledger, opener=opener, clock=clock)

    assert result.body == b""
    assert result.method == "HEAD"
    assert opener.methods == ["HEAD"]


def test_no_credentials_are_ever_sent_even_with_a_token_in_the_environment(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TBANK_INVEST_TOKEN", SYNTHETIC_TOKEN_VALUE)  # synthetic
    opener = http.opener(http.ok())

    fetch("HEAD", config, ledger=ledger, opener=opener, clock=clock)
    clock.advance(10 * SECOND)
    fetch("GET", config, ledger=ledger, opener=opener, clock=clock)

    for request in opener.requests:
        names = {name.lower() for name, _ in request.header_items()}
        assert not names & CREDENTIAL_HEADERS
        assert SYNTHETIC_TOKEN_VALUE not in request.full_url
        assert all(SYNTHETIC_TOKEN_VALUE not in value for _, value in request.header_items())
    assert SYNTHETIC_TOKEN_VALUE not in (tmp_path / "requests.jsonl").read_text("utf-8")


# --- rejections -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (401, "auth_required"),
        (403, "auth_required"),
        (404, "http_status"),
        (410, "http_status"),
        (429, "rate_limited"),
        (500, "http_status"),
        (503, "http_status"),
    ],
)
def test_http_errors_map_to_reasons(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    code: int,
    reason: str,
) -> None:
    responder = _raising(_http_error(config.url, code))

    assert _reject("GET", config, ledger, responder, http, clock) == reason


@pytest.mark.parametrize("code", [401, 403])
def test_auth_required_on_head_too(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    code: int,
) -> None:
    responder = _raising(_http_error(config.url, code))

    assert _reject("HEAD", config, ledger, responder, http, clock) == "auth_required"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_response_is_refused(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    code: int,
) -> None:
    location = {"Location": "https://synthetic.invalid/elsewhere.zip"}
    responder = _status(http, code, location, b"")

    assert _reject("GET", config, ledger, responder, http, clock) == "redirect_refused"


@pytest.mark.parametrize("code", [301, 302, 307])
def test_redirect_raised_by_a_non_following_opener_is_refused(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    code: int,
) -> None:
    error = _http_error(config.url, code, {"Location": "https://synthetic.invalid/x.zip"})

    assert _reject("GET", config, ledger, _raising(error), http, clock) == "redirect_refused"


@pytest.mark.parametrize("status", [201, 204, 206])
def test_non_200_success_status_is_refused(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    builders: "Builders",
    status: int,
) -> None:
    headers = {"Content-Type": "application/zip"}
    responder = _status(http, status, headers, builders.synthetic_zip)

    assert _reject("GET", config, ledger, responder, http, clock) == "http_status"


@pytest.mark.parametrize(
    "error",
    [TimeoutError("synthetic timeout"), TimeoutError()],
)
def test_timeout(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    error: BaseException,
) -> None:
    assert _reject("GET", config, ledger, _raising(error), http, clock) == "timeout"


def test_timeout_while_reading_the_body(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
) -> None:
    def respond(request: urllib.request.Request) -> "FakeResponse":
        headers = {"Content-Type": "application/zip"}
        return http.response(200, headers, b"", read_error=TimeoutError("synthetic"))

    assert _reject("GET", config, ledger, respond, http, clock) == "timeout"


def test_url_error_is_a_network_error(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    error = urllib.error.URLError("synthetic name resolution failure")

    assert _reject("GET", config, ledger, _raising(error), http, clock) == "network_error"


def test_url_error_wrapping_a_timeout_fails_closed(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    error = urllib.error.URLError(TimeoutError("synthetic"))

    assert _reject("GET", config, ledger, _raising(error), http, clock) in {
        "timeout",
        "network_error",
    }


@pytest.mark.parametrize("content_type", ["text/html", "text/html; charset=utf-8", "TEXT/HTML"])
def test_html_content_type(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    content_type: str,
) -> None:
    body = b"<html>synthetic error page</html>" * 4
    responder = http.ok(body, content_type)

    assert _reject("GET", config, ledger, responder, http, clock) == "html_payload"


@pytest.mark.parametrize("content_type", ["application/json", "text/plain", "image/png"])
def test_unexpected_content_type(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    builders: "Builders",
    content_type: str,
) -> None:
    responder = http.ok(builders.synthetic_zip, content_type)

    assert _reject("GET", config, ledger, responder, http, clock) == "content_type"


def test_oversized_content_length_is_refused(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    headers = {"Content-Type": "application/zip", "Content-Length": str(config.max_bytes + 1)}
    responder = _status(http, 200, headers, b"\0" * 128)

    assert _reject("GET", config, ledger, responder, http, clock) == "too_large"


def test_oversized_body_without_content_length_is_refused(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    body = b"\0" * (config.max_bytes + 1)
    responder = _status(http, 200, {"Content-Type": "application/zip"}, body)

    assert _reject("GET", config, ledger, responder, http, clock) == "too_large"


def test_body_longer_than_its_content_length_is_refused_when_over_the_limit(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    headers = {"Content-Type": "application/zip", "Content-Length": "128"}
    responder = _status(http, 200, headers, b"\0" * (config.max_bytes + 1))

    assert _reject("GET", config, ledger, responder, http, clock) == "too_large"


def test_endless_body_is_cut_at_the_limit(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    responses: list[FakeResponse] = []

    def respond(request: urllib.request.Request) -> "FakeResponse":
        response = http.response(200, {"Content-Type": "application/zip"}, endless=True)
        responses.append(response)
        return response

    assert _reject("GET", config, ledger, respond, http, clock) == "too_large"
    assert responses[0].bytes_read <= 4 * 1024 * 1024 + config.max_bytes


def test_undersized_body_is_refused(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    responder = http.ok(b"PK\x05\x06synthetic", "application/zip")

    assert _reject("GET", config, ledger, responder, http, clock) == "too_small"


# --- ledger and rate limits -----------------------------------------------------------------------


def test_ledger_path_is_under_project_logs(paths: ProjectPaths) -> None:
    expected = paths.category_dir(StorageCategory.LOGS) / "tbank" / "requests.jsonl"

    assert Path(ledger_path(paths)) == expected


def test_record_appends_one_json_line_per_request(
    config: TbankHistoryConfig, ledger: RequestLedger, tmp_path: Path
) -> None:
    ledger.record(utc_ns=1 * SECOND, method="HEAD", url=config.url, outcome="started")
    ledger.record(utc_ns=5 * SECOND, method="GET", url=config.url, outcome="started")

    lines = _lines(tmp_path / "requests.jsonl")
    assert [line["utc_ns"] for line in lines] == [1 * SECOND, 5 * SECOND]
    assert [line["method"] for line in lines] == ["HEAD", "GET"]
    assert all(line["url"] == config.url for line in lines)
    assert all(line["outcome"] == "started" for line in lines)


def test_request_is_recorded_before_it_is_sent(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
    tmp_path: Path,
) -> None:
    seen: list[int] = []
    opener = http.opener(http.ok())
    opener.on_open.append(lambda request: seen.append(len(_lines(tmp_path / "requests.jsonl"))))

    fetch("GET", config, ledger=ledger, opener=opener, clock=clock)

    assert seen == [1]
    assert _lines(tmp_path / "requests.jsonl")[0]["method"] == "GET"


def test_failed_request_still_counts_against_the_rate_limit(
    config: TbankHistoryConfig,
    ledger: RequestLedger,
    http: "OpenerKit",
    clock: "FakeClock",
) -> None:
    failing = http.opener(_raising(_http_error(config.url, 500)))
    with pytest.raises(FetchRejectedError):
        fetch("GET", config, ledger=ledger, opener=failing, clock=clock)

    retry = http.opener(http.ok())
    with pytest.raises(FetchRejectedError) as caught:
        fetch("GET", config, ledger=ledger, opener=retry, clock=clock)
    assert caught.value.reason == "rate_limited"
    assert retry.requests == []

    clock.advance(int(config.min_request_interval_s * SECOND) + SECOND)
    assert fetch("GET", config, ledger=ledger, opener=retry, clock=clock).status == 200


def test_min_interval_is_enforced(config: TbankHistoryConfig, ledger: RequestLedger) -> None:
    start = 1_000 * SECOND
    interval = int(config.min_request_interval_s * SECOND)
    ledger.record(utc_ns=start, method="GET", url=config.url, outcome="started")

    with pytest.raises(FetchRejectedError) as caught:
        ledger.check_allowed(start + interval - 1, config)
    assert caught.value.reason == "rate_limited"
    ledger.check_allowed(start + interval, config)


def test_requests_per_minute_is_enforced_independently_of_the_interval(
    make_config: "ConfigFactory", ledger: RequestLedger
) -> None:
    config = make_config(requests_per_minute=2, min_request_interval_s=30)
    start = 1_000 * SECOND
    # Two earlier requests one second apart (e.g. recorded under an older, looser config).
    ledger.record(utc_ns=start, method="HEAD", url=config.url, outcome="started")
    ledger.record(utc_ns=start + SECOND, method="GET", url=config.url, outcome="started")

    # 40 s after the last request the interval is satisfied but both are in the last minute.
    with pytest.raises(FetchRejectedError) as caught:
        ledger.check_allowed(start + 41 * SECOND, config)
    assert caught.value.reason == "rate_limited"
    # Once the first request leaves the 60 s window, one request in the window is below 2.
    ledger.check_allowed(start + 62 * SECOND, config)


def test_empty_ledger_allows_a_request(config: TbankHistoryConfig, tmp_path: Path) -> None:
    RequestLedger(tmp_path / "absent" / "requests.jsonl").check_allowed(SECOND, config)


def test_corrupt_ledger_fails_closed(config: TbankHistoryConfig, tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    path.write_text('{"utc_ns": 1, "method": "GET"\n', encoding="utf-8")

    with pytest.raises((FetchRejectedError, ValueError)):
        RequestLedger(path).check_allowed(10_000 * SECOND, config)


def test_rate_limited_fetch_never_calls_the_opener(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    ledger.record(utc_ns=clock.now, method="GET", url=config.url, outcome="started")
    opener = http.opener(http.refusing)

    with pytest.raises(FetchRejectedError) as caught:
        fetch("GET", config, ledger=ledger, opener=opener, clock=clock)

    assert caught.value.reason == "rate_limited"
    assert opener.requests == []


# --- wait_ns (used by the period acquisition to wait instead of failing) -----------------------


def test_wait_ns_is_zero_on_an_empty_ledger(
    config: TbankHistoryConfig, ledger: RequestLedger
) -> None:
    assert ledger.wait_ns(SECOND, config) == 0


def test_wait_ns_honours_the_minimum_interval(
    make_config: "ConfigFactory", ledger: RequestLedger
) -> None:
    config = make_config(requests_per_minute=20, min_request_interval_s=3)
    start = 1_000 * SECOND
    ledger.record(utc_ns=start, method="GET", url=config.url, outcome="sent")

    assert ledger.wait_ns(start + SECOND, config) == 2 * SECOND
    assert ledger.wait_ns(start + 3 * SECOND, config) == 0
    ledger.check_allowed(start + 3 * SECOND, config)


def test_wait_ns_honours_requests_per_minute(
    make_config: "ConfigFactory", ledger: RequestLedger
) -> None:
    config = make_config(requests_per_minute=20, min_request_interval_s=3)
    start = 1_000 * SECOND
    # 20 requests 1 s apart (e.g. from a looser earlier config): the window is full.
    for index in range(20):
        ledger.record(utc_ns=start + index * SECOND, method="GET", url=config.url, outcome="sent")
    now = start + 22 * SECOND  # interval satisfied, but 20 requests in the last 60 s

    wait = ledger.wait_ns(now, config)
    assert wait == start + 60 * SECOND - now  # until the oldest leaves the window
    with pytest.raises(FetchRejectedError):
        ledger.check_allowed(now + wait - 1, config)
    ledger.check_allowed(now + wait, config)


def test_wait_ns_refuses_a_future_entry(config: TbankHistoryConfig, ledger: RequestLedger) -> None:
    ledger.record(utc_ns=10 * SECOND, method="GET", url=config.url, outcome="sent")

    with pytest.raises(FetchRejectedError) as caught:
        ledger.wait_ns(SECOND, config)
    assert caught.value.reason == "rate_limited"


def test_status_rejections_carry_the_http_status(
    config: TbankHistoryConfig, ledger: RequestLedger, http: "OpenerKit", clock: "FakeClock"
) -> None:
    opener = http.opener(_raising(_http_error(config.url, 404)))
    with pytest.raises(FetchRejectedError) as caught:
        fetch("GET", config, ledger=ledger, opener=opener, clock=clock)

    assert (caught.value.reason, caught.value.http_status) == ("http_status", 404)
