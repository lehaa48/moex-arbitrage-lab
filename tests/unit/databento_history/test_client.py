"""HTTP client: key handling/redaction, allowlist per step, one metered call, URL checks."""

import base64
import email.message
import io
import json
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.settings import ProjectPaths
from moex_arbitrage_lab.sources.databento_history import (
    DatabentoError,
    Endpoint,
    EndpointNotAllowedError,
    HistoricalClient,
    MissingApiKeyError,
    RequestLedger,
    check_download_url,
    ledger_path,
    read_api_key,
)
from moex_arbitrage_lab.sources.databento_history.client import parse_json_exact, scrub

if TYPE_CHECKING:
    from tests.unit.databento_history.conftest import (
        FakeClock,
        FakeOpener,
        FakeProvider,
        Kit,
    )


def _client(
    paths: ProjectPaths,
    step: str,
    opener: "FakeOpener",
    env: dict[str, str],
    clock: "FakeClock",
) -> HistoricalClient:
    return HistoricalClient(
        step=step,  # type: ignore[arg-type]
        ledger=RequestLedger(ledger_path(paths)),
        opener=opener,
        env=env,
        clock=clock,
    )


@pytest.mark.parametrize("env", [{}, {"DATABENTO_API_KEY": ""}, {"DATABENTO_API_KEY": "  "}])
def test_missing_key_is_refused_with_a_clear_message(env: dict[str, str]) -> None:
    with pytest.raises(MissingApiKeyError, match="DATABENTO_API_KEY not set"):
        read_api_key(env)


def test_missing_key_sends_and_records_nothing(
    paths: ProjectPaths, opener: "FakeOpener", provider: "FakeProvider", clock: "FakeClock"
) -> None:
    client = _client(paths, "A", opener, {}, clock)

    with pytest.raises(MissingApiKeyError):
        client.call_json(Endpoint.GET_COST)

    assert provider.requests == []
    assert not ledger_path(paths).exists()


def test_basic_auth_key_as_user_with_empty_password_never_redirected(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
) -> None:
    client = _client(paths, "A", opener, env, clock)

    assert client.call_json(Endpoint.LIST_SCHEMAS) == ["mbo", "trades", "ohlcv-1s", "ohlcv-1m"]

    request = provider.requests[0]
    expected = "Basic " + base64.b64encode(f"{kit.fake_key}:".encode()).decode()
    assert request.unredirected_hdrs == {"Authorization": expected}
    assert "Authorization" not in request.headers
    assert request.get_method() == "GET"
    assert (
        request.full_url == "https://hist.databento.com/v0/metadata.list_schemas?dataset=GLBX.MDP3"
    )
    assert kit.fake_key not in repr(client)


def test_post_endpoints_send_frozen_form_body(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
) -> None:
    client = _client(paths, "A", opener, env, clock)

    client.call_json(Endpoint.GET_COST)

    request = provider.requests[0]
    assert request.get_method() == "POST"
    assert request.data is not None
    assert isinstance(request.data, bytes)
    assert dict(urllib.parse.parse_qsl(request.data.decode())) == {
        "dataset": "GLBX.MDP3",
        "symbols": "GCZ6",
        "schema": "ohlcv-1s",
        "start": "2026-09-11T00:00:00Z",
        "end": "2026-11-01T00:00:00Z",
        "stype_in": "raw_symbol",
    }
    assert request.headers["Content-type"] == "application/x-www-form-urlencoded"


@pytest.mark.parametrize(
    "endpoint",
    [
        Endpoint.CREATE_BATCH_JOB,
        Endpoint.LIST_BATCH_JOBS,
        Endpoint.LIST_BATCH_FILES,
        Endpoint.DOWNLOAD_BATCH_FILE,
    ],
)
def test_step_a_refuses_every_batch_endpoint_before_ledger_or_network(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    endpoint: Endpoint,
    kit: "Kit",
) -> None:
    client = _client(paths, "A", opener, env, clock)

    def attempt() -> object:
        if endpoint is Endpoint.DOWNLOAD_BATCH_FILE:
            return client.download(
                f"https://hist.databento.com/v0/batch/download/t/{kit.job_id}/{kit.csv_name}",
                job_id=kit.job_id,
                filename=kit.csv_name,
                expected_size=10,
            )
        return client.call_json(endpoint, job_id=kit.job_id)

    with pytest.raises(EndpointNotAllowedError):
        attempt()

    assert provider.requests == []
    assert not ledger_path(paths).exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        Endpoint.LIST_SCHEMAS,
        Endpoint.GET_DATASET_RANGE,
        Endpoint.RESOLVE_SYMBOLOGY,
        Endpoint.GET_RECORD_COUNT,
        Endpoint.GET_BILLABLE_SIZE,
    ],
)
def test_step_b_refuses_metadata_it_does_not_need(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    endpoint: Endpoint,
) -> None:
    client = _client(paths, "B", opener, env, clock)

    with pytest.raises(EndpointNotAllowedError):
        client.call_json(endpoint)

    assert provider.requests == []


def test_metered_job_creation_is_sent_at_most_once_ever(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
) -> None:
    first = _client(paths, "B", opener, env, clock)
    first.call_json(Endpoint.CREATE_BATCH_JOB)

    with pytest.raises(DatabentoError, match="metered_repeat_refused"):
        first.call_json(Endpoint.CREATE_BATCH_JOB)
    # A new client (e.g. a re-run) reads the ledger and refuses as well.
    with pytest.raises(DatabentoError, match="metered_repeat_refused"):
        _client(paths, "B", opener, env, clock).call_json(Endpoint.CREATE_BATCH_JOB)

    assert provider.endpoint_paths().count("batch.submit_job") == 1


def test_failed_job_creation_still_blocks_a_second_attempt(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
) -> None:
    provider.fail_paths["batch.submit_job"] = 500
    with pytest.raises(DatabentoError, match="http_status"):
        _client(paths, "B", opener, env, clock).call_json(Endpoint.CREATE_BATCH_JOB)
    provider.fail_paths.clear()

    with pytest.raises(DatabentoError, match="metered_repeat_refused"):
        _client(paths, "B", opener, env, clock).call_json(Endpoint.CREATE_BATCH_JOB)

    assert provider.endpoint_paths().count("batch.submit_job") == 1


def test_request_is_recorded_before_it_is_sent(
    paths: ProjectPaths,
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
) -> None:
    seen: list[list[str]] = []

    def responder(request: urllib.request.Request) -> object:
        lines = ledger_path(paths).read_text(encoding="utf-8").splitlines()
        seen.append(lines)
        return provider(request)

    opener = kit.opener(responder)  # type: ignore[arg-type]
    _client(paths, "A", opener, env, clock).call_json(Endpoint.GET_COST)

    assert len(seen) == 1
    assert json.loads(seen[0][-1])["event"] == "request"
    entries = [json.loads(line) for line in ledger_path(paths).read_text().splitlines()]
    assert [e["event"] for e in entries] == ["request", "response"]
    assert entries[0]["endpoint"] == "metadata.get_cost"
    assert entries[0]["params"]["symbols"] == "GCZ6"
    assert entries[1]["outcome"] == "ok"


@pytest.mark.parametrize(
    "failure",
    [
        "http_error_body",
        "os_error",
        "url_error",
    ],
)
def test_key_never_appears_in_errors_ledger_or_tracebacks(
    paths: ProjectPaths,
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
    failure: str,
) -> None:
    encoded = base64.b64encode(f"{kit.fake_key}:".encode()).decode()

    def responder(request: urllib.request.Request) -> object:
        leak = f"echo {kit.fake_key} {encoded}"
        if failure == "http_error_body":
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                email.message.Message(),
                io.BytesIO(leak.encode()),
            )
        if failure == "os_error":
            raise OSError(leak)
        raise urllib.error.URLError(leak)

    opener = kit.opener(responder)  # type: ignore[arg-type]
    with pytest.raises(DatabentoError) as caught:
        _client(paths, "A", opener, env, clock).call_json(Endpoint.GET_COST)

    rendered = "".join(traceback.format_exception(caught.value, chain=True))
    ledger_text = ledger_path(paths).read_text(encoding="utf-8")
    for text in (str(caught.value), repr(caught.value), rendered, ledger_text):
        assert kit.fake_key not in text
        assert encoded not in text
    assert caught.value.__cause__ is None


def test_auth_failure_maps_to_stable_reason(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
) -> None:
    provider.fail_paths["metadata.get_cost"] = 401
    with pytest.raises(DatabentoError) as caught:
        _client(paths, "A", opener, env, clock).call_json(Endpoint.GET_COST)
    assert caught.value.reason == "auth_required"


def test_html_and_invalid_json_are_rejected() -> None:
    with pytest.raises(DatabentoError, match="html_payload"):
        parse_json_exact(b"<!DOCTYPE html><html></html>")
    with pytest.raises(DatabentoError, match="bad_response"):
        parse_json_exact(b"{not json")


def test_numbers_are_parsed_without_binary_floats() -> None:
    from decimal import Decimal

    assert parse_json_exact(b"0.0") == Decimal("0.0")
    assert parse_json_exact(b"12.345678901234567890") == Decimal("12.345678901234567890")
    assert parse_json_exact(b"17") == 17


def test_scrub_removes_every_secret_form() -> None:
    secret = "synthetic-secret-0123456789"  # noqa: S105 - synthetic test value
    assert scrub(f"a {secret} b", (secret,)) == "a <redacted> b"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.databento.com/v0/batch/download/t/{job}/{name}",
        "https://evil.example/v0/batch/download/t/{job}/{name}",
        "https://api.databento.com.evil.example/v0/batch/download/t/{job}/{name}",
        "https://user@api.databento.com/v0/batch/download/t/{job}/{name}",
        "https://api.databento.com:8443/v0/batch/download/t/{job}/{name}",
        "https://hist.databento.com/v0/metadata.get_cost",
        "https://hist.databento.com/v0/batch/download/t/{job}/{name}",
        "https://api.databento.com/v0/batch/download/t/OTHER-JOB/{name}",
        "https://api.databento.com/v0/batch/download/t/{job}/other.csv",
        "https://api.databento.com/v0/batch/download/t/{job}/{name}?x=1",
        "https://api.databento.com/v0/batch/download/../{job}/{name}",
    ],
)
def test_download_url_is_restricted(url: str, kit: "Kit") -> None:
    with pytest.raises(DatabentoError, match="download_url_refused"):
        check_download_url(url.format(job=kit.job_id, name=kit.csv_name), kit.job_id, kit.csv_name)


def test_download_checks_size_and_logs_no_url(
    paths: ProjectPaths,
    opener: "FakeOpener",
    provider: "FakeProvider",
    env: dict[str, str],
    clock: "FakeClock",
    kit: "Kit",
) -> None:
    client = _client(paths, "B", opener, env, clock)
    url = f"https://api.databento.com/v0/batch/download/synthetic-team/{kit.job_id}/{kit.aux_name}"

    with pytest.raises(DatabentoError, match="size_mismatch"):
        client.download(
            url, job_id=kit.job_id, filename=kit.aux_name, expected_size=len(kit.aux) + 5
        )
    with pytest.raises(DatabentoError, match="too_large"):
        client.download(url, job_id=kit.job_id, filename=kit.aux_name, expected_size=3)
    assert "synthetic-team" not in ledger_path(paths).read_text(encoding="utf-8")
