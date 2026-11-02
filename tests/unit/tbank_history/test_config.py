"""Synthetic configuration validation for the T-Bank history adapter (P1, workstream D).

All values are synthetic (``.invalid`` hosts, invented UID); nothing is fetched.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from moex_arbitrage_lab.sources.tbank_history import TbankHistoryConfig, load_config

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import Builders, ConfigFactory


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "synthetic-tbank.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("payload_format", ["zip", "csv"])
def test_load_config_round_trips_synthetic_toml(
    tmp_path: Path, builders: "Builders", make_config: "ConfigFactory", payload_format: str
) -> None:
    values = builders.base_config_values(payload_format)
    loaded = load_config(_write(tmp_path, builders.toml(values)))

    assert loaded == make_config(payload_format)
    assert loaded.payload_format == payload_format
    assert loaded.contract_multiplier == Decimal("1")
    assert loaded.tick_size == Decimal("0.1")
    assert loaded.archive_date == date(2026, 10, 15)
    assert loaded.source_format_version is None
    assert tuple(loaded.expected_columns) == builders.columns
    assert [w.name for w in loaded.sessions] == ["morning", "main", "evening"]


def test_load_config_refuses_unknown_keys(tmp_path: Path, builders: "Builders") -> None:
    text = builders.toml(builders.base_config_values(), extra="synthetic_unknown_key = 1")

    with pytest.raises(ValueError, match=r"(?i)synthetic_unknown_key|extra"):
        load_config(_write(tmp_path, text))


def test_default_lag_buckets_are_the_h0001_family(
    builders: "Builders", make_config: "ConfigFactory"
) -> None:
    assert tuple(make_config().lag_buckets_ns) == builders.lag_buckets_ns


def test_config_is_frozen_and_refuses_extra_fields(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    with pytest.raises(ValidationError):
        config.ticker_cc = "SYNTHETIC"  # type: ignore[misc]
    values = {**builders.base_config_values(), "synthetic_extra": 1}
    with pytest.raises(ValidationError):
        TbankHistoryConfig.model_validate(values)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"dataset": "Synthetic-Anon"}, id="dataset-not-lowercase"),
        pytest.param({"dataset": "synthetic/anon"}, id="dataset-not-a-slug"),
        pytest.param({"url": "http://synthetic.invalid/a.zip"}, id="url-not-https"),
        pytest.param({"raw_filename": "Synthetic.zip"}, id="raw-filename-uppercase"),
        pytest.param({"raw_filename": "../x.zip"}, id="raw-filename-traversal"),
        pytest.param({"canonical_contract": "MOEX:GD:2026-12-17"}, id="contract-expiry-mismatch"),
        pytest.param({"canonical_contract": "MOEX:GD:CONT"}, id="contract-continuous"),
        pytest.param({"contract_multiplier": Decimal(0)}, id="multiplier-zero"),
        pytest.param({"tick_size": Decimal(0)}, id="tick-zero"),
        pytest.param({"tick_size": Decimal("-0.1")}, id="tick-negative"),
        pytest.param({"event_ts_type": "LOCAL_INGEST_TS"}, id="event-ts-local"),
        pytest.param({"event_ts_type": "DOWNLOAD_TIME"}, id="event-ts-download"),
        pytest.param({"event_ts_type": "RETRIEVAL_TS"}, id="event-ts-retrieval"),
        pytest.param({"trade_action": "CANCEL"}, id="trade-action"),
        pytest.param({"csv_delimiter": ",,"}, id="delimiter-two-chars"),
        pytest.param({"csv_delimiter": ""}, id="delimiter-empty"),
        pytest.param({"requests_per_minute": 31}, id="rpm-above-documented-30"),
        pytest.param({"requests_per_minute": 0}, id="rpm-zero"),
        pytest.param({"min_request_interval_s": 1}, id="interval-below-60-over-rpm"),
        pytest.param({"zero_cost_evidence_url": "http://synthetic.invalid/z"}, id="evidence-http"),
        pytest.param({"payload_format": "gz"}, id="payload-format"),
    ],
)
def test_invalid_synthetic_config_is_refused(
    make_config: "ConfigFactory", overrides: dict[str, object]
) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        make_config(**overrides)


def test_minimum_interval_may_equal_sixty_over_rate(make_config: "ConfigFactory") -> None:
    config = make_config(requests_per_minute=12, min_request_interval_s=5)

    assert config.requests_per_minute == 12


def test_sessions_must_be_sorted_and_non_overlapping(
    builders: "Builders", make_config: "ConfigFactory"
) -> None:
    morning, main, evening = builders.windows
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        make_config(sessions=(builders.session(*main), builders.session(*morning)))
    _, start, end, day = main
    overlapping = (builders.session("overlap", start, end + 1, day), builders.session(*evening))
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        make_config(sessions=overlapping)
    # Adjacent half-open windows are not overlapping.
    adjacent = make_config(sessions=(builders.session(*main), builders.session(*evening)))
    assert len(adjacent.sessions) == 2


def test_session_window_must_have_positive_length(
    builders: "Builders", make_config: "ConfigFactory"
) -> None:
    # The refusal may come from SessionWindow itself or from the config; both fail closed.
    start = builders.utc_ns(7)
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        make_config(sessions=(builders.session("empty", start, start, builders.archive_date),))
