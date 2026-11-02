"""``parse_trade_ts``: documented UTC forms only, exact integer nanoseconds (synthetic values)."""

from datetime import UTC, datetime

import pytest

from moex_arbitrage_lab.sources.tbank_history import parse_trade_ts


def _second_ns(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    whole = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    return int(whole.timestamp()) * 1_000_000_000


BASE = _second_ns(2026, 10, 6, 7, 2, 29)


@pytest.mark.parametrize(
    ("text", "nanos", "digits"),
    [
        ("2026-10-06T07:02:29.315229Z", 315_229_000, 6),
        ("2026-10-06 07:02:29.315229", 315_229_000, 6),
        ("2026-10-06T07:02:29.315229+00:00", 315_229_000, 6),
        ("2026-10-06T07:02:29Z", 0, 0),
        ("2026-10-06 07:02:29", 0, 0),
        ("2026-10-06T07:02:29.5Z", 500_000_000, 1),
        ("2026-10-06T07:02:29.050Z", 50_000_000, 3),
        ("2026-10-06T07:02:29.000000Z", 0, 6),
        ("2026-10-06T07:02:29.123456789Z", 123_456_789, 9),
        ("2026-10-06T07:02:29.999999999Z", 999_999_999, 9),
        ("2026-10-06T07:02:29.000000001Z", 1, 9),
    ],
)
def test_documented_forms_parse_exactly(text: str, nanos: int, digits: int) -> None:
    assert parse_trade_ts(text) == (BASE + nanos, digits)


def test_nine_digit_fraction_is_exact_beyond_float_precision() -> None:
    # A binary float of epoch seconds cannot hold 1 ns at this magnitude; integers must be used.
    value, digits = parse_trade_ts("2026-10-06T07:02:29.123456789Z")
    neighbour, _ = parse_trade_ts("2026-10-06T07:02:29.123456788Z")

    assert value - neighbour == 1
    assert value % 1_000_000_000 == 123_456_789
    assert digits == 9
    assert isinstance(value, int)


def test_trailing_zeros_count_as_represented_digits() -> None:
    assert parse_trade_ts("2026-10-06T07:02:29.100Z")[1] == 3
    assert parse_trade_ts("2026-10-06T07:02:29.1Z")[1] == 1


def test_epoch_and_leap_day_boundaries() -> None:
    assert parse_trade_ts("1970-01-01T00:00:00Z") == (0, 0)
    leap = _second_ns(2028, 2, 29, 23, 59, 59) + 999_999_999
    assert parse_trade_ts("2028-02-29T23:59:59.999999999Z") == (leap, 9)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "2026-10-06",
        "07:02:29",
        "2026-10-06T07:02:29.315229+03:00",  # a non-UTC offset is never silently converted
        "2026-10-06T07:02:29.315229-00:30",
        "2026-10-06T07:02:29.1234567890Z",  # ten digits would invent/discard precision
        "2026-10-06T07:02:29.Z",
        "2026-10-06T07:02:29.",
        "2026-10-06T07:02Z",
        "2026-10-06T7:02:29Z",
        "2026-10-6T07:02:29Z",
        "06.10.2026 07:02:29",
        "2026/10/06 07:02:29",
        "2026-13-06T07:02:29Z",
        "2026-02-30T07:02:29Z",
        "2026-10-06T24:00:00Z",
        "2026-10-06T07:60:00Z",
        "2026-10-06T07:02:60Z",  # leap seconds are not representable
        "2026-10-06T07:02:29.315229ZZ",
        "2026-10-06T07:02:29.315229 UTC",
        "2026-10-06T07:02:29,315229Z",
        "1760000000.315229",
        "NULL",
        "٢٠٢٦-10-06T07:02:29Z",  # non-ASCII (Arabic-Indic) digits
        "2026-10-06T07:02:29.31522٩Z",
    ],
)
def test_other_forms_are_refused(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the interface fixes the type, not the message
        parse_trade_ts(text)
