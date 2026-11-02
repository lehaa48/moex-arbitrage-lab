"""The frozen MOEX session calendar (real config file, offline) and synthetic calendar rules.

Covers both date-specific regimes including the 2026-09-14 switch, the opening auction as a
separate non-continuous window, trading date = calendar date, weekend/coverage refusals, the
calendar validators, per-date exceptions, and parsing: auction trades are parsed (never
``outside_verified_session``) yet flagged non-continuous.
"""

import tomllib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from moex_arbitrage_lab.sources.tbank_history import (
    CalendarError,
    MoexCalendar,
    load_calendar,
    parse_trades,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import Builders, ConfigFactory

REPO = Path(__file__).resolve().parents[3]
CALENDAR = (
    REPO
    / "config"
    / "sources"
    / "tbank_history"
    / "screen_v0_2"
    / ("moex_calendar_2026_09_10.toml")
)


def _ns(day: date, hour: int, minute: int = 0) -> int:
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return int(moment.timestamp()) * 1_000_000_000


@pytest.fixture(scope="module")
def calendar() -> MoexCalendar:
    return load_calendar(CALENDAR)


def _summary(calendar: MoexCalendar, day: date) -> list[tuple[str, int, int, bool]]:
    return [
        (w.name, w.start_utc_ns, w.end_utc_ns, w.continuous) for w in calendar.sessions_for(day)
    ]


def test_regime_before_the_switch(calendar: MoexCalendar) -> None:
    day = date(2026, 9, 11)  # Friday, last weekday of the n101980 regime
    # MSK = UTC+3: 06:50/07:00/10:00/19:00/23:50 MSK.
    assert _summary(calendar, day) == [
        ("opening_auction", _ns(day, 3, 50), _ns(day, 4), False),
        ("morning", _ns(day, 4), _ns(day, 7), True),
        ("main", _ns(day, 7), _ns(day, 16), True),
        ("evening", _ns(day, 16), _ns(day, 20, 50), True),
    ]
    assert calendar.regime_for(day).source_url == "https://www.moex.com/n101980"


def test_regime_after_the_switch(calendar: MoexCalendar) -> None:
    day = date(2026, 9, 14)  # Monday, first day of the n103379 regime
    assert _summary(calendar, day) == [
        ("opening_auction", _ns(day, 3, 50), _ns(day, 4), False),
        ("morning", _ns(day, 4), _ns(day, 6), True),
        ("main", _ns(day, 6), _ns(day, 16), True),
        ("evening", _ns(day, 16), _ns(day, 20, 50), True),
    ]
    assert calendar.regime_for(day).source_url == "https://www.moex.com/n103379"


@pytest.mark.parametrize("day", [date(2026, 9, 1), date(2026, 10, 15), date(2026, 10, 30)])
def test_trading_date_is_the_calendar_date(calendar: MoexCalendar, day: date) -> None:
    windows = calendar.sessions_for(day)

    assert {w.trading_date for w in windows} == {day}
    # The evening session belongs to the current day and ends before UTC midnight.
    assert windows[-1].name == "evening"
    assert windows[-1].end_utc_ns == _ns(day, 20, 50)


def test_auction_is_separate_and_never_continuous(calendar: MoexCalendar) -> None:
    day = date(2026, 10, 6)
    windows = calendar.sessions_for(day)
    continuous = calendar.continuous_sessions_for(day)

    assert [w.name for w in windows if not w.continuous] == ["opening_auction"]
    assert [w.name for w in continuous] == ["morning", "main", "evening"]
    assert windows[0].end_utc_ns == continuous[0].start_utc_ns  # adjacent, not merged


@pytest.mark.parametrize("day", [date(2026, 9, 5), date(2026, 10, 24), date(2026, 10, 25)])
def test_weekends_are_not_regular(calendar: MoexCalendar, day: date) -> None:
    assert calendar.day_status(day) == "weekend"
    with pytest.raises(CalendarError):
        calendar.sessions_for(day)


def test_weekend_facts_are_reported(calendar: MoexCalendar) -> None:
    fact = calendar.weekend_fact(date(2026, 10, 24))
    assert fact is not None
    assert fact.status == "no_weekend_session"
    assert calendar.weekend_fact(date(2026, 10, 31)) is None


@pytest.mark.parametrize("day", [date(2026, 8, 31), date(2026, 11, 1), date(2027, 1, 4)])
def test_dates_outside_coverage_are_refused(calendar: MoexCalendar, day: date) -> None:
    with pytest.raises(CalendarError) as caught:
        calendar.sessions_for(day)
    assert caught.value.reason == "outside_calendar_coverage"
    with pytest.raises(CalendarError):
        calendar.day_status(day)


def test_no_weekday_exceptions_in_september_october(calendar: MoexCalendar) -> None:
    assert calendar.exceptions == ()


# --- synthetic calendars --------------------------------------------------------------------------


def _raw() -> dict[str, Any]:
    with CALENDAR.open("rb") as handle:
        return tomllib.load(handle)


def _validate(data: dict[str, Any]) -> MoexCalendar:
    def tuples(value: object) -> object:
        if isinstance(value, list):
            return tuple(tuples(v) for v in value)
        if isinstance(value, dict):
            return {k: tuples(v) for k, v in value.items()}
        return value

    return MoexCalendar.model_validate(tuples(data))


def test_raw_table_round_trips() -> None:
    assert _validate(_raw()) == load_calendar(CALENDAR)


def _gap(data: dict[str, Any]) -> None:
    data["regimes"][1]["effective_from"] = date(2026, 9, 15)


def _overlap(data: dict[str, Any]) -> None:
    data["regimes"][0]["windows"][1]["start_local"] = data["regimes"][0]["windows"][0][
        "start_local"
    ]


def _short_coverage(data: dict[str, Any]) -> None:
    data["coverage_end_exclusive"] = date(2026, 11, 2)


def _no_continuous(data: dict[str, Any]) -> None:
    for window in data["regimes"][0]["windows"]:
        window["continuous"] = False


def _weekend_exception(data: dict[str, Any]) -> None:
    data["exceptions"] = [
        {
            "day": date(2026, 9, 5),
            "kind": "closed",
            "source_url": "https://x.invalid/a",
            "note": "x",
        }
    ]


def _weekday_fact(data: dict[str, Any]) -> None:
    data["weekend_facts"][0]["day"] = date(2026, 10, 23)


def _unknown_key(data: dict[str, Any]) -> None:
    data["synthetic_extra"] = 1


@pytest.mark.parametrize(
    "mutate",
    [
        _gap,
        _overlap,
        _short_coverage,
        _no_continuous,
        _weekend_exception,
        _weekday_fact,
        _unknown_key,
    ],
)
def test_invalid_calendars_are_refused(mutate: Any) -> None:
    data = _raw()
    mutate(data)
    with pytest.raises(ValidationError):
        _validate(data)


@pytest.mark.parametrize("kind", ["closed", "modified"])
def test_exceptions_remove_a_weekday(kind: str) -> None:
    data = _raw()
    day = date(2026, 10, 7)
    data["exceptions"] = [
        {"day": day, "kind": kind, "source_url": "https://synthetic.invalid/n", "note": "synthetic"}
    ]
    calendar = _validate(data)

    assert calendar.day_status(day) == kind
    with pytest.raises(CalendarError):
        calendar.sessions_for(day)
    assert calendar.day_status(date(2026, 10, 8)) == "regular"


# --- parsing against date-specific windows --------------------------------------------------------


def test_auction_trades_are_parsed_not_outside_session(
    calendar: MoexCalendar, make_config: "ConfigFactory", builders: "Builders"
) -> None:
    day = date(2026, 9, 14)
    config = make_config("gzip", archive_date=day, sessions=calendar.sessions_for(day))
    csv = builders.csv(
        [
            builders.row(ts="2026-09-14T03:49:59.999999Z"),  # before the auction
            builders.row(ts="2026-09-14T03:50:00.000000Z"),  # auction start (inclusive)
            builders.row(ts="2026-09-14T03:59:59.999999Z"),  # auction
            builders.row(ts="2026-09-14T04:00:00.000000Z"),  # morning start
            builders.row(ts="2026-09-14T06:00:00.000001Z"),  # main (new regime)
            builders.row(ts="2026-09-14T20:49:59.123456789Z"),  # evening, 9 digits kept
            builders.row(ts="2026-09-14T20:50:00.000000Z"),  # evening end (exclusive)
        ]
    )
    result = parse_trades(
        csv,
        config=config,
        source_file="data/raw/synthetic.csv.gz",
        raw_payload_hash="0" * 64,
        local_ingest_ts_utc_ns=1,
    )

    assert result.reconciliation.parse_failures["outside_verified_session"] == 2
    assert [t.session_id for t in result.trades] == [
        "opening_auction",
        "opening_auction",
        "morning",
        "main",
        "evening",
    ]
    assert {t.trading_date for t in result.trades} == {day}
    assert result.trades[-1].event_ts_utc_ns == _ns(day, 20, 49) + 59_123_456_789
