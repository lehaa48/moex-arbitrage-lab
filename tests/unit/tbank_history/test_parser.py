"""Deterministic parsing of synthetic T-Bank anonymized-trade CSV into ``trade_v1``.

Covers provenance fields, null source timestamps (never filled by local ingest time), every
parse-failure and ineligibility reason counted exactly once, first-failing-reason precedence,
other instruments never becoming ``TradeV1``, ``DIRECTION`` never mapping to aggressor side,
exact decimals and ``price_ticks``, session assignment, header refusal and reconciliation
invariants (property-style over seeded random synthetic files). All rows are synthetic.
"""

import hashlib
import json
import random
from collections import Counter
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from moex_arbitrage_lab.contracts import trade_v1
from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.sources.tbank_history import (
    INELIGIBILITY_REASONS,
    PARSE_FAILURE_REASONS,
    ParseResult,
    Reconciliation,
    TbankHistoryConfig,
    parse_digest,
    parse_trades,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import Builders, ConfigFactory

SOURCE_FILE = "data/raw/tbank/synthetic-anon-trades/gdz6/2026-10-15/synthetic-gdz6-20261015.zip"
RAW_HASH = sha256_hex(b"synthetic raw payload hash input")
INGEST_NS = 1_760_569_200_123_456_789  # synthetic local ingest time, after every event

EXPECTED_FAILURES = (
    "malformed_row",
    "other_instrument",
    "instrument_uid_mismatch",
    "null_timestamp",
    "unparseable_timestamp",
    "unparseable_price",
    "unparseable_quantity",
    "outside_verified_session",
)


def _parse(data: bytes, config: TbankHistoryConfig) -> ParseResult:
    return parse_trades(
        data,
        config=config,
        source_file=SOURCE_FILE,
        raw_payload_hash=RAW_HASH,
        local_ingest_ts_utc_ns=INGEST_NS,
    )


def _ts(nanos: int, digits: int) -> str:
    """Synthetic ``YYYY-MM-DDTHH:MM:SS[.f]Z`` text for 2026-10-15 UTC nanoseconds."""
    seconds, fraction = divmod(nanos, 1_000_000_000)
    day_seconds = seconds % 86_400
    hh, rest = divmod(day_seconds, 3600)
    mm, ss = divmod(rest, 60)
    text = f"2026-10-15T{hh:02d}:{mm:02d}:{ss:02d}"
    if digits:
        text += "." + f"{fraction:09d}"[:digits]
    return text + "Z"


def _reconciliation_json(reconciliation: Reconciliation) -> dict[str, Any]:
    exported: object = reconciliation.to_json()
    if isinstance(exported, dict):
        return exported
    assert isinstance(exported, str | bytes)
    loaded = json.loads(exported)
    assert isinstance(loaded, dict)
    return loaded


def _assert_invariants(result: ParseResult) -> None:
    rec = result.reconciliation
    assert set(rec.parse_failures) == set(PARSE_FAILURE_REASONS)
    assert set(rec.ineligible) == set(INELIGIBILITY_REASONS)
    assert rec.csv_data_rows == rec.parsed_rows + sum(rec.parse_failures.values())
    assert rec.parsed_rows == rec.eligible_rows + sum(rec.ineligible.values())
    assert rec.parsed_rows == len(result.trades)
    assert rec.eligible_rows == len(result.eligible)
    rec.check()


# --- reason vocabulary ----------------------------------------------------------------------------


def test_reason_vocabularies_are_the_documented_ones_in_precedence_sequence() -> None:
    assert tuple(PARSE_FAILURE_REASONS) == EXPECTED_FAILURES
    assert tuple(INELIGIBILITY_REASONS) == ("trade_source_not_exchange",)


# --- happy path and provenance --------------------------------------------------------------------


def test_synthetic_file_parses_to_trade_v1_with_full_provenance(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    result = _parse(builders.synthetic_csv, config)

    _assert_invariants(result)
    rec = result.reconciliation
    assert tuple(rec.header) == builders.columns
    assert rec.csv_data_rows == 4
    assert rec.parsed_rows == 4
    assert rec.eligible_rows == 3
    assert rec.ineligible["trade_source_not_exchange"] == 1
    assert all(count == 0 for count in rec.parse_failures.values())
    assert rec.target_rows == 4  # every synthetic row is the configured instrument

    first = result.trades[0]
    assert isinstance(first, trade_v1.TradeV1)
    assert first.schema_version == "trade_v1"
    assert first.venue == "MOEX"
    assert first.asset == "GOLD"
    assert first.dataset == config.dataset
    assert first.raw_symbol == builders.ticker
    assert first.instrument_id in {builders.uid, builders.ticker}
    assert first.canonical_contract == config.canonical_contract
    assert first.expiry == config.expiry
    assert first.currency == "USD"
    assert first.price_unit == config.price_unit
    assert first.contract_multiplier == config.contract_multiplier
    assert first.event_ts_type == config.event_ts_type
    assert first.event_ts_utc_ns == builders.utc_ns(7, 2, 29, 315_229_000)
    assert first.receive_ts_utc_ns is None
    assert first.capture_ts_utc_ns is None
    assert first.local_ingest_ts_utc_ns == INGEST_NS
    assert first.price == Decimal("4012.3")
    assert first.size == Decimal("1")
    assert first.aggressor_side is None
    assert first.action == config.trade_action
    assert first.trade_source == "EXCHANGE"
    assert first.session_id == "main"
    assert first.trading_date == date(2026, 10, 15)
    assert first.source_file == SOURCE_FILE
    assert first.raw_payload_hash == RAW_HASH

    assert [t.source_row_no for t in result.trades] == [2, 3, 4, 5]
    assert [t.source_record_id for t in result.trades] == ["row-2", "row-3", "row-4", "row-5"]
    assert [t.source_row_no for t in result.eligible] == [2, 3, 5]
    assert all(t.trade_source == "EXCHANGE" for t in result.eligible)
    assert result.trades[2].trade_source == "DEALER"
    assert all(t.local_ingest_ts_utc_ns == INGEST_NS for t in result.trades)


def test_every_trade_round_trips_through_the_canonical_serialization(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    for trade in _parse(builders.synthetic_csv, config).trades:
        data = trade_v1.trade_to_json_bytes(trade)
        assert trade_v1.trade_from_json_bytes(data) == trade


def test_parsing_is_deterministic_and_digest_is_canonical(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    first = _parse(builders.synthetic_csv, config)
    second = _parse(builders.synthetic_csv, config)

    assert list(first.trades) == list(second.trades)
    assert parse_digest(first) == parse_digest(second)
    over_trades = hashlib.sha256(
        b"".join(trade_v1.trade_to_json_bytes(t) + b"\n" for t in first.trades)
    ).hexdigest()
    over_eligible = hashlib.sha256(
        b"".join(trade_v1.trade_to_json_bytes(t) + b"\n" for t in first.eligible)
    ).hexdigest()
    assert parse_digest(first) in {over_trades, over_eligible}


def test_digest_changes_with_any_provenance_input(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    base = parse_digest(_parse(builders.synthetic_csv, config))
    other = parse_trades(
        builders.synthetic_csv,
        config=config,
        source_file=SOURCE_FILE,
        raw_payload_hash=sha256_hex(b"another synthetic payload"),
        local_ingest_ts_utc_ns=INGEST_NS,
    )
    assert parse_digest(other) != base


def test_utf8_bom_and_crlf_do_not_change_the_result(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    plain = _parse(builders.synthetic_csv, config)
    with_bom = _parse(b"\xef\xbb\xbf" + builders.synthetic_csv, config)
    crlf = _parse(builders.synthetic_csv.replace(b"\n", b"\r\n"), config)

    assert list(with_bom.trades) == list(plain.trades)
    assert list(crlf.trades) == list(plain.trades)
    assert with_bom.reconciliation.csv_data_rows == 4


def test_fractional_digit_histogram_and_composition_are_counted(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    result = _parse(builders.synthetic_csv, config)

    assert dict(result.fractional_digits) == {6: 4}
    assert dict(result.composition["TRADE_SOURCE"]) == {"EXCHANGE": 3, "DEALER": 1}
    assert dict(result.composition["DIRECTION"]) == {"BUY": 2, "SELL": 2}


# --- header ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(
            (
                "TICKER_CC",
                "TRADE_TS",
                "DIRECTION",
                "PRICE",
                "QUANTITY",
                "TRADE_SOURCE",
                "INSTRUMENT_UID",
            ),
            id="columns-swapped",
        ),
        pytest.param(
            ("TRADE_TS", "TICKER_CC", "DIRECTION", "PRICE", "QUANTITY", "TRADE_SOURCE"),
            id="missing-column",
        ),
        pytest.param(
            (
                "TRADE_TS",
                "TICKER_CC",
                "DIRECTION",
                "PRICE",
                "QUANTITY",
                "TRADE_SOURCE",
                "INSTRUMENT_UID",
                "EXTRA",
            ),
            id="extra-column",
        ),
        pytest.param(
            (
                "trade_ts",
                "ticker_cc",
                "direction",
                "price",
                "quantity",
                "trade_source",
                "instrument_uid",
            ),
            id="lowercase",
        ),
    ],
)
def test_header_mismatch_rejects_the_whole_file(
    config: TbankHistoryConfig, builders: "Builders", header: tuple[str, ...]
) -> None:
    data = builders.csv([builders.row()], header=header)

    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _parse(data, config)


def test_semicolon_file_does_not_match_a_comma_header(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    data = builders.csv([builders.row()], delimiter=";")

    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _parse(data, config)


def test_empty_file_has_no_header_and_is_refused(config: TbankHistoryConfig) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _parse(b"", config)


def test_header_only_file_reconciles_to_zero(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    result = _parse(builders.csv([]), config)

    _assert_invariants(result)
    assert result.reconciliation.csv_data_rows == 0
    assert list(result.trades) == []


# --- every exclusion reason, counted once ---------------------------------------------------------

ROWS_BY_REASON: dict[str, Callable[["Builders"], list[str]]] = {
    "malformed_row:short": lambda b: b.row()[:-1],
    "malformed_row:long": lambda b: [*b.row(), "synthetic-extra"],
    "other_instrument": lambda b: b.row(ticker=b.other_ticker, uid=b.other_uid),
    "other_instrument:same-uid": lambda b: b.row(ticker=b.other_ticker),
    "instrument_uid_mismatch": lambda b: b.row(uid=b.other_uid),
    "null_timestamp": lambda b: b.row(ts=""),
    "unparseable_timestamp:offset": lambda b: b.row(ts="2026-10-15T10:02:29.315229+03:00"),
    "unparseable_timestamp:dotted": lambda b: b.row(ts="15.10.2026 07:02:29"),
    "unparseable_timestamp:ten-digits": lambda b: b.row(ts="2026-10-15T07:02:29.1234567890Z"),
    "unparseable_price:decimal-comma": lambda b: b.row(price="4012,3"),
    "unparseable_price:text": lambda b: b.row(price="synthetic"),
    "unparseable_price:empty": lambda b: b.row(price=""),
    "unparseable_price:nan": lambda b: b.row(price="NaN"),
    "unparseable_price:infinity": lambda b: b.row(price="Infinity"),
    "unparseable_quantity:decimal-comma": lambda b: b.row(quantity="1,5"),
    "unparseable_quantity:text": lambda b: b.row(quantity="one"),
    "unparseable_quantity:empty": lambda b: b.row(quantity=""),
    "unparseable_quantity:nan": lambda b: b.row(quantity="NaN"),
    "outside_verified_session:before": lambda b: b.row(ts="2026-10-15T03:59:59.999999Z"),
    "outside_verified_session:end-is-open": lambda b: b.row(ts="2026-10-15T20:50:00.000000Z"),
    "outside_verified_session:night": lambda b: b.row(ts="2026-10-15T23:00:00.000000Z"),
    "outside_verified_session:other-day": lambda b: b.row(ts="2026-10-14T08:00:00.000000Z"),
}


@pytest.mark.parametrize("case", sorted(ROWS_BY_REASON))
def test_each_parse_failure_reason_is_counted_once_and_never_becomes_a_trade(
    config: TbankHistoryConfig, builders: "Builders", case: str
) -> None:
    reason = case.split(":")[0]
    good = builders.row()
    data = builders.csv([good, ROWS_BY_REASON[case](builders), good])

    result = _parse(data, config)

    _assert_invariants(result)
    expected = dict.fromkeys(PARSE_FAILURE_REASONS, 0)
    expected[reason] = 1
    assert dict(result.reconciliation.parse_failures) == expected
    assert [t.source_row_no for t in result.trades] == [2, 4]
    assert result.reconciliation.csv_data_rows == 3


@pytest.mark.parametrize(
    ("row_kwargs", "reason"),
    [
        ({"ticker": "SVZ6_SPBFUT", "ts": ""}, "other_instrument"),
        ({"ticker": "SVZ6_SPBFUT", "price": "x"}, "other_instrument"),
        ({"uid": "00000000-0000-4000-8000-00000000beef", "ts": ""}, "instrument_uid_mismatch"),
        ({"ts": "", "price": "x", "quantity": "y"}, "null_timestamp"),
        ({"ts": "2026/10/15 07:00:00", "price": "x"}, "unparseable_timestamp"),
        ({"price": "x", "quantity": "y"}, "unparseable_price"),
        ({"quantity": "y", "ts": "2026-10-15T23:00:00Z"}, "unparseable_quantity"),
        ({"ts": "2026-10-15T23:00:00Z", "source": "DEALER"}, "outside_verified_session"),
    ],
)
def test_first_failing_reason_wins(
    config: TbankHistoryConfig,
    builders: "Builders",
    row_kwargs: dict[str, str],
    reason: str,
) -> None:
    result = _parse(builders.csv([builders.row(**row_kwargs)]), config)

    _assert_invariants(result)
    assert result.reconciliation.parse_failures[reason] == 1
    assert sum(result.reconciliation.parse_failures.values()) == 1
    assert sum(result.reconciliation.ineligible.values()) == 0


def test_other_instruments_never_become_trades_even_without_a_configured_uid(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    config = make_config(instrument_uid=None)
    rows = [
        builders.row(ticker=builders.other_ticker, uid=builders.other_uid),
        builders.row(ticker="GDH7_SPBFUT"),
        builders.row(uid=builders.other_uid),  # uid unchecked without a configured uid
    ]

    result = _parse(builders.csv(rows), config)

    _assert_invariants(result)
    assert result.reconciliation.parse_failures["other_instrument"] == 2
    assert result.reconciliation.parse_failures["instrument_uid_mismatch"] == 0
    assert [t.source_row_no for t in result.trades] == [4]
    assert all(t.raw_symbol == builders.ticker for t in result.trades)


@pytest.mark.parametrize("source", ["DEALER", "exchange", "EXCHANGE_SYNTHETIC", "OTC"])
def test_non_exchange_trade_source_is_parsed_but_ineligible(
    config: TbankHistoryConfig, builders: "Builders", source: str
) -> None:
    result = _parse(builders.csv([builders.row(source=source), builders.row()]), config)

    _assert_invariants(result)
    assert result.reconciliation.ineligible["trade_source_not_exchange"] == 1
    assert [t.trade_source for t in result.trades] == [source, "EXCHANGE"]
    assert [t.source_row_no for t in result.eligible] == [3]


# --- null event time, DIRECTION, decimals, sessions -----------------------------------------------


def test_null_trade_ts_is_never_filled_with_local_ingest_time(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    result = _parse(builders.csv([builders.row(ts=""), builders.row(ts="")]), config)

    assert list(result.trades) == []
    assert result.reconciliation.parse_failures["null_timestamp"] == 2
    assert all(t.event_ts_utc_ns != INGEST_NS for t in result.trades)


@pytest.mark.parametrize("direction", ["BUY", "SELL", "B", "S", "UNKNOWN", "SYNTHETIC"])
def test_direction_never_maps_to_aggressor_side(
    config: TbankHistoryConfig, builders: "Builders", direction: str
) -> None:
    result = _parse(builders.csv([builders.row(direction=direction)]), config)

    assert len(result.trades) == 1
    assert result.trades[0].aggressor_side is None
    assert dict(result.composition["DIRECTION"]) == {direction: 1}


@pytest.mark.parametrize(
    ("price", "quantity"),
    [("4012.30", "1"), ("4012.3", "10"), ("4012.123456789", "0.5"), ("4012", "100")],
)
def test_price_and_size_are_exact_decimals(
    config: TbankHistoryConfig, builders: "Builders", price: str, quantity: str
) -> None:
    trade = _parse(builders.csv([builders.row(price=price, quantity=quantity)]), config).trades[0]

    assert isinstance(trade.price, Decimal)
    assert str(trade.price) == price
    assert str(trade.size) == quantity


@pytest.mark.parametrize(
    ("tick", "price", "ticks"),
    [
        (Decimal("0.1"), "4012.3", 40123),
        (Decimal("0.1"), "4012.30", 40123),
        (Decimal("0.1"), "4012.35", None),
        (Decimal("0.1"), "4012", 40120),
        (Decimal("0.25"), "4012.75", 16051),
        (Decimal("0.25"), "4012.7", None),
        (None, "4012.3", None),
    ],
)
def test_price_ticks_only_for_exact_multiples_of_a_configured_tick(
    make_config: "ConfigFactory",
    builders: "Builders",
    tick: Decimal | None,
    price: str,
    ticks: int | None,
) -> None:
    config = make_config(tick_size=tick)
    trade = _parse(builders.csv([builders.row(price=price)]), config).trades[0]

    assert trade.price_ticks == ticks
    assert trade.price == Decimal(price)


@pytest.mark.parametrize(
    ("ts", "session_id", "trading_date"),
    [
        ("2026-10-15T04:00:00.000000Z", "morning", date(2026, 10, 15)),
        ("2026-10-15T06:59:59.999999Z", "morning", date(2026, 10, 15)),
        ("2026-10-15T07:00:00.000000Z", "main", date(2026, 10, 15)),
        ("2026-10-15T15:59:59.999999999Z", "main", date(2026, 10, 15)),
        ("2026-10-15T16:00:00.000000Z", "evening", date(2026, 10, 16)),
        ("2026-10-15T20:49:59.999999Z", "evening", date(2026, 10, 16)),
    ],
)
def test_session_and_trading_date_come_from_the_containing_window(
    config: TbankHistoryConfig,
    builders: "Builders",
    ts: str,
    session_id: str,
    trading_date: date,
) -> None:
    trade = _parse(builders.csv([builders.row(ts=ts)]), config).trades[0]

    assert trade.session_id == session_id
    assert trade.trading_date == trading_date


# --- reconciliation -------------------------------------------------------------------------------


def _rebuild(rec: Reconciliation, **changes: Any) -> None:
    values: dict[str, Any] = {
        "header": rec.header,
        "csv_data_rows": rec.csv_data_rows,
        "parse_failures": dict(rec.parse_failures),
        "parsed_rows": rec.parsed_rows,
        "ineligible": dict(rec.ineligible),
        "eligible_rows": rec.eligible_rows,
        "target_rows": rec.target_rows,
    }
    values.update(changes)
    Reconciliation(**values).check()


def test_reconciliation_check_refuses_inconsistent_counts(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    rows = [builders.row(), builders.row(ts=""), builders.row(source="DEALER")]
    rec = _parse(builders.csv(rows), config).reconciliation
    _rebuild(rec)  # the real counts pass

    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _rebuild(rec, csv_data_rows=rec.csv_data_rows + 1)
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _rebuild(rec, parsed_rows=rec.parsed_rows - 1)
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _rebuild(rec, eligible_rows=rec.eligible_rows + 1)
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _rebuild(rec, parse_failures={**rec.parse_failures, "null_timestamp": 0})
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        _rebuild(rec, ineligible={"trade_source_not_exchange": 0})


def test_reconciliation_json_carries_every_count_and_zero_filled_reason(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    rows = [builders.row(), builders.row(ts=""), builders.row(source="DEALER")]
    exported = _reconciliation_json(_parse(builders.csv(rows), config).reconciliation)

    assert exported["csv_data_rows"] == 3
    assert exported["parsed_rows"] == 2
    assert exported["eligible_rows"] == 1
    assert exported["parse_failures"] == {
        **dict.fromkeys(PARSE_FAILURE_REASONS, 0),
        "null_timestamp": 1,
    }
    assert exported["ineligible"] == {"trade_source_not_exchange": 1}
    assert "target_rows" in exported
    assert "header" in exported


# --- property-style: seeded random synthetic files ------------------------------------------------

_MAIN_START_NS = 7 * 3600 * 1_000_000_000
_MAIN_SPAN_NS = 9 * 3600 * 1_000_000_000


def _random_row(rng: random.Random, b: "Builders") -> tuple[list[str], str]:
    """A synthetic row and its expected outcome: a failure reason, ``eligible`` or
    ``trade_source_not_exchange``. Defects are combined so precedence is exercised."""
    digits = rng.choice([0, 3, 6, 9])
    nanos = b.utc_ns(0) + _MAIN_START_NS + rng.randrange(_MAIN_SPAN_NS)
    values = b.row(
        ts=_ts(nanos, digits),
        direction=rng.choice(["BUY", "SELL"]),
        price=f"{rng.randrange(38_000, 45_000) / 10:.1f}",
        quantity=str(rng.randrange(1, 50)),
        source=rng.choice(["EXCHANGE", "EXCHANGE", "EXCHANGE", "DEALER"]),
    )
    defects = {
        "outside_verified_session": rng.random() < 0.1,
        "unparseable_quantity": rng.random() < 0.1,
        "unparseable_price": rng.random() < 0.1,
        "unparseable_timestamp": rng.random() < 0.1,
        "null_timestamp": rng.random() < 0.1,
        "instrument_uid_mismatch": rng.random() < 0.1,
        "other_instrument": rng.random() < 0.1,
        "malformed_row": rng.random() < 0.05,
    }
    if defects["outside_verified_session"]:
        values[0] = _ts(b.utc_ns(22) + rng.randrange(3600 * 1_000_000_000), digits)
    if defects["unparseable_quantity"]:
        values[4] = rng.choice(["1,5", "x", ""])
    if defects["unparseable_price"]:
        values[3] = rng.choice(["4012,3", "p", ""])
    if defects["unparseable_timestamp"]:
        values[0] = rng.choice(["2026-10-15T10:00:00+03:00", "15.10.2026 07:00:00"])
    if defects["null_timestamp"]:
        values[0] = ""
    if defects["instrument_uid_mismatch"]:
        values[6] = b.other_uid
    if defects["other_instrument"]:
        values[1] = b.other_ticker
    if defects["malformed_row"]:
        values = values[: rng.randrange(1, 7)] if rng.random() < 0.5 else [*values, "extra"]
    for reason in PARSE_FAILURE_REASONS:
        if defects.get(reason):
            return values, reason
    return values, "eligible" if values[5] == "EXCHANGE" else "trade_source_not_exchange"


@pytest.mark.parametrize("seed", range(30))
def test_random_synthetic_files_always_reconcile(
    config: TbankHistoryConfig, builders: "Builders", seed: int
) -> None:
    rng = random.Random(seed)  # noqa: S311 - seeded synthetic test data
    generated = [_random_row(rng, builders) for _ in range(rng.randrange(0, 80))]
    data = builders.csv([values for values, _ in generated])

    result = _parse(data, config)

    _assert_invariants(result)
    outcomes = Counter(outcome for _, outcome in generated)
    rec = result.reconciliation
    assert rec.csv_data_rows == len(generated)
    for reason in PARSE_FAILURE_REASONS:
        assert rec.parse_failures[reason] == outcomes[reason], reason
    assert rec.ineligible["trade_source_not_exchange"] == outcomes["trade_source_not_exchange"]
    assert rec.eligible_rows == outcomes["eligible"]
    expected_rows = [
        n for n, (_, outcome) in enumerate(generated, start=2) if outcome == "eligible"
    ]
    assert [t.source_row_no for t in result.eligible] == expected_rows
    assert all(t.raw_symbol == builders.ticker for t in result.trades)
    assert all(t.aggressor_side is None for t in result.trades)
    assert all(t.event_ts_utc_ns is not None for t in result.trades)
    assert all(t.source_record_id == f"row-{t.source_row_no}" for t in result.trades)
    assert parse_digest(result) == parse_digest(_parse(data, config))
