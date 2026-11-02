"""Canonical ``trade_v1`` contract (technical specification v0.1, section 3; ADR-0003).

Strict-schema scenarios adapted from moex-event-alpha ``tests/contract/test_envelope_v1.py``
at commit 068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-019) for the new schema.
"""

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from moex_arbitrage_lab.contracts import (
    EVENT_TS_TYPE_UNKNOWN,
    TRADE_V1_FIELDS,
    TradeV1,
    trade_from_json_bytes,
    trade_to_json_bytes,
)

Factory = Callable[..., dict[str, object]]

# Exactly the table in docs/TECHNICAL_SPECIFICATION_V0_1_RU.md section 3, in order.
SPEC_FIELDS = (
    "source",
    "venue",
    "dataset",
    "instrument_id",
    "raw_symbol",
    "canonical_contract",
    "asset",
    "expiry",
    "currency",
    "price_unit",
    "contract_multiplier",
    "event_ts_utc_ns",
    "event_ts_type",
    "receive_ts_utc_ns",
    "capture_ts_utc_ns",
    "local_ingest_ts_utc_ns",
    "price",
    "price_ticks",
    "size",
    "aggressor_side",
    "sequence",
    "action",
    "flags",
    "trade_source",
    "session_id",
    "trading_date",
    "source_file",
    "source_record_id",
    "source_row_no",
    "schema_version",
    "raw_payload_hash",
)
NULLABLE = {
    "event_ts_utc_ns",
    "receive_ts_utc_ns",
    "capture_ts_utc_ns",
    "price_ticks",
    "aggressor_side",
    "sequence",
    "flags",
    "trade_source",
    "session_id",
}


def test_field_names_and_order_match_the_specification_exactly() -> None:
    # Changing this tuple means trade_v2, never an in-place edit of trade_v1.
    assert TRADE_V1_FIELDS == SPEC_FIELDS
    assert tuple(TradeV1.model_fields) == SPEC_FIELDS


def test_every_field_is_required_and_only_specified_fields_are_nullable() -> None:
    for name, info in TradeV1.model_fields.items():
        assert info.is_required(), name
        accepts_none = "None" in repr(info.annotation)
        assert accepts_none == (name in NULLABLE), name


def test_valid_trade_round_trips_byte_stably(make_trade_fields: Factory) -> None:
    trade = TradeV1.model_validate(make_trade_fields())

    first = trade_to_json_bytes(trade)
    restored = trade_from_json_bytes(first)

    assert restored == trade
    assert trade_to_json_bytes(restored) == first


# --- timestamps (ADR-0003) -----------------------------------------------------------------


def test_missing_source_event_time_is_accepted_only_as_null(make_trade_fields: Factory) -> None:
    trade = TradeV1.model_validate(
        make_trade_fields(
            event_ts_utc_ns=None,
            event_ts_type=EVENT_TS_TYPE_UNKNOWN,
            receive_ts_utc_ns=None,
            capture_ts_utc_ns=None,
        )
    )

    restored = trade_from_json_bytes(trade_to_json_bytes(trade))
    assert restored.event_ts_utc_ns is None
    assert restored.receive_ts_utc_ns is None
    assert restored.capture_ts_utc_ns is None
    assert restored.local_ingest_ts_utc_ns == trade.local_ingest_ts_utc_ns


@pytest.mark.parametrize("name", ["event_ts_utc_ns", "receive_ts_utc_ns", "capture_ts_utc_ns"])
def test_source_timestamps_cannot_be_omitted_silently(
    make_trade_fields: Factory, name: str
) -> None:
    fields = make_trade_fields()
    del fields[name]

    with pytest.raises(ValidationError, match="Field required"):
        TradeV1.model_validate(fields)


def test_local_ingest_time_is_required(make_trade_fields: Factory) -> None:
    with pytest.raises(ValidationError):
        TradeV1.model_validate(make_trade_fields(local_ingest_ts_utc_ns=None))


@pytest.mark.parametrize("event_ts_type", ["EXCHANGE_MATCH", "VENDOR_EXCHANGE_TIME_MS"])
def test_equal_event_and_ingest_values_are_accepted_for_a_source_event_type(
    make_trade_fields: Factory, event_ts_type: str
) -> None:
    # Equal values alone prove no substitution (coarse source clocks can coincide); the
    # adapter states the source semantics explicitly.
    same = 1_796_000_000_000_000_000
    trade = TradeV1.model_validate(
        make_trade_fields(
            event_ts_utc_ns=same, local_ingest_ts_utc_ns=same, event_ts_type=event_ts_type
        )
    )

    restored = trade_from_json_bytes(trade_to_json_bytes(trade))
    assert restored.event_ts_utc_ns == restored.local_ingest_ts_utc_ns == same
    assert restored.event_ts_type == event_ts_type


@pytest.mark.parametrize(
    "event_ts_type", ["LOCAL_INGEST", "ingest", "LOCAL_RECEIVE", "RETRIEVAL_TIME", "download"]
)
def test_event_ts_type_cannot_claim_local_time(
    make_trade_fields: Factory, event_ts_type: str
) -> None:
    with pytest.raises(ValidationError, match="ADR-0003"):
        TradeV1.model_validate(make_trade_fields(event_ts_type=event_ts_type))


def test_full_int64_nanoseconds_survive_json_exactly(make_trade_fields: Factory) -> None:
    max_int64 = 2**63 - 1
    trade = TradeV1.model_validate(
        make_trade_fields(event_ts_utc_ns=max_int64 - 1, local_ingest_ts_utc_ns=max_int64)
    )

    restored = trade_from_json_bytes(trade_to_json_bytes(trade))

    assert restored.event_ts_utc_ns == max_int64 - 1


@pytest.mark.parametrize("value", [-1, 2**63, True])
def test_out_of_range_or_non_integer_timestamps_are_refused(
    make_trade_fields: Factory, value: object
) -> None:
    with pytest.raises(ValidationError):
        TradeV1.model_validate(make_trade_fields(event_ts_utc_ns=value))


# --- contract identity, precision and strictness -------------------------------------------


@pytest.mark.parametrize(
    "contract",
    [
        "GC.c.0",
        "COMEX:GC:CONT",
        "COMEX:GC",
        "COMEX:GC:2026-12-30",
        "MOEX:GD:2026-12-29",
        "comex:gc:2026-12-29",
        "COMEX:GC:2026-12-29:x",
        "COMEX:GC:2026-02-30",
        "COMEX:GC:Z6",
    ],
)
def test_contract_must_be_explicit_and_agree_with_venue_and_expiry(
    make_trade_fields: Factory, contract: str
) -> None:
    with pytest.raises(ValidationError, match="canonical_contract"):
        TradeV1.model_validate(make_trade_fields(canonical_contract=contract))


def test_moex_contract_is_accepted(make_trade_fields: Factory) -> None:
    trade = TradeV1.model_validate(
        make_trade_fields(
            venue="MOEX",
            canonical_contract="MOEX:GD:2026-12-17",
            expiry=date(2026, 12, 17),
            currency="USD",
        )
    )
    assert trade.venue == "MOEX"


def test_decimal_precision_is_preserved(make_trade_fields: Factory) -> None:
    trade = TradeV1.model_validate(
        make_trade_fields(price=Decimal("2345.123456789"), size=Decimal("0.001"))
    )

    raw = json.loads(trade_to_json_bytes(trade))

    assert raw["price"] == "2345.123456789"
    assert raw["size"] == "0.001"
    assert trade_from_json_bytes(trade_to_json_bytes(trade)).price == Decimal("2345.123456789")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", 2345.1),
        ("size", 1.0),
        ("price", Decimal("NaN")),
        ("price", Decimal("Infinity")),
        ("contract_multiplier", Decimal(0)),
        ("venue", "CME"),
        ("asset", "SILVER"),
        ("schema_version", "trade_v2"),
        ("raw_payload_hash", "ABC"),
        ("currency", "usd"),
        ("source_file", "/abs/path.bin"),
        ("source_file", "data/../escape.bin"),
        ("source_file", "C:/data/raw/x.bin"),
        ("aggressor_side", "BUY"),
        ("action", "TRADE"),
        ("source_row_no", -1),
        ("raw_symbol", ""),
        ("instrument_id", "has space"),
    ],
)
def test_invalid_values_are_refused(make_trade_fields: Factory, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TradeV1.model_validate(make_trade_fields(**{field: value}))


@pytest.mark.parametrize("value", ["2345.1", "1e3"])
def test_decimal_strings_from_json_are_accepted(make_trade_fields: Factory, value: str) -> None:
    raw = json.loads(trade_to_json_bytes(TradeV1.model_validate(make_trade_fields())))
    raw["price"] = value

    assert trade_from_json_bytes(json.dumps(raw).encode()).price == Decimal(value)


@pytest.mark.parametrize("value", [2345.1, "not-a-number"])
def test_json_float_or_garbage_price_is_refused(make_trade_fields: Factory, value: object) -> None:
    raw = json.loads(trade_to_json_bytes(TradeV1.model_validate(make_trade_fields())))
    raw["price"] = value

    with pytest.raises(ValidationError):
        trade_from_json_bytes(json.dumps(raw).encode())


def test_unknown_field_is_refused(make_trade_fields: Factory) -> None:
    with pytest.raises(ValidationError):
        TradeV1.model_validate(make_trade_fields(available_at_utc_ns=1))


def test_trade_is_immutable(make_trade_fields: Factory) -> None:
    trade = TradeV1.model_validate(make_trade_fields())

    with pytest.raises(ValidationError):
        trade.event_ts_utc_ns = None  # type: ignore[misc]


def test_optional_source_fields_round_trip(make_trade_fields: Factory) -> None:
    trade = TradeV1.model_validate(
        make_trade_fields(
            sequence="seq-abc",
            flags=("F_LAST", "F_SYNTHETIC"),
            trade_source="EXCHANGE",
            session_id="2026-12-01:main",
            aggressor_side=None,
            price_ticks=None,
        )
    )

    assert trade_from_json_bytes(trade_to_json_bytes(trade)) == trade
