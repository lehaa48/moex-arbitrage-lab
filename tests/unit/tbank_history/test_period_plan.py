"""The frozen two-month request plan (real template + calendar, offline and deterministic).

44 Mon-Fri dates of 2026-09-01..2026-10-31 are requested, the canary 2026-09-02 first and the
rest chronologically; the 17 weekend dates are listed separately (``weekend_not_requested``).
Per-date configs carry the date-specific URL, raw filename and sessions; the base values are
the frozen screen values.
"""

import json
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from moex_arbitrage_lab.sources.tbank_history import (
    CalendarError,
    LoadedTemplate,
    build_day_config,
    load_template,
    plan_document,
    plan_for,
    plan_requests,
    raw_relative,
)

if TYPE_CHECKING:
    from collections.abc import Callable

REPO = Path(__file__).resolve().parents[3]
SCREEN = REPO / "config" / "sources" / "tbank_history" / "screen_v0_2"
TEMPLATE = SCREEN / "gdz6_base.toml"


@pytest.fixture(scope="module")
def loaded() -> LoadedTemplate:
    return load_template(TEMPLATE)


def _all_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days)]


def test_plan_has_44_weekdays_canary_first(loaded: LoadedTemplate) -> None:
    plan = plan_for(loaded)
    weekdays = [d for d in _all_days(date(2026, 9, 1), date(2026, 11, 1)) if d.weekday() < 5]

    assert len(plan.requested) == 44
    assert plan.canary == date(2026, 9, 2)
    assert plan.requested[0] == date(2026, 9, 2)
    assert list(plan.requested[1:]) == [d for d in weekdays if d != date(2026, 9, 2)]
    assert sorted(plan.requested) == weekdays
    assert all(d.weekday() < 5 for d in plan.requested)


def test_weekends_are_listed_not_requested(loaded: LoadedTemplate) -> None:
    plan = plan_for(loaded)
    weekends = [d for d in _all_days(date(2026, 9, 1), date(2026, 11, 1)) if d.weekday() >= 5]

    assert [e.day for e in plan.not_requested] == weekends
    assert len(weekends) == 17
    assert {e.reason for e in plan.not_requested} == {"weekend_not_requested"}
    notes = {e.day: e.note for e in plan.not_requested if e.note}
    assert set(notes) == {date(2026, 10, 24), date(2026, 10, 25)}
    assert all("no_weekend_session" in note for note in notes.values())
    assert set(plan.requested).isdisjoint(weekends)


def test_plan_is_deterministic(loaded: LoadedTemplate) -> None:
    first = json.dumps(plan_document(loaded, plan_for(loaded)), sort_keys=True)
    again = load_template(TEMPLATE)
    second = json.dumps(plan_document(again, plan_for(again)), sort_keys=True)

    assert first == second
    document = json.loads(first)
    assert document["requested_count"] == 44
    assert document["not_requested_by_reason"] == {"weekend_not_requested": 17}
    assert document["requested"][0]["role"] == "canary"
    assert {r["role"] for r in document["requested"][1:]} == {"period"}


def test_day_config_is_date_specific(loaded: LoadedTemplate) -> None:
    before = build_day_config(loaded, date(2026, 9, 11))
    after = build_day_config(loaded, date(2026, 9, 14))

    assert after.url == (
        "https://invest-public-api.tbank.ru/history-trades/2026-09-14?instrumentId=GDZ6_SPBFUT"
    )
    assert after.raw_filename == "gdz6_spbfut_2026-09-14.csv.gz"
    assert raw_relative(after) == (
        "tbank/history-trades/gdz6/2026-09-14/gdz6_spbfut_2026-09-14.csv.gz"
    )
    assert after.archive_date == date(2026, 9, 14)
    morning_before = next(w for w in before.sessions if w.name == "morning")
    morning_after = next(w for w in after.sessions if w.name == "morning")
    hour = 3_600_000_000_000
    assert morning_before.end_utc_ns - morning_before.start_utc_ns == 3 * hour
    assert morning_after.end_utc_ns - morning_after.start_utc_ns == 2 * hour
    assert [w.continuous for w in after.sessions] == [False, True, True, True]


def test_base_values_are_the_frozen_screen_values(loaded: LoadedTemplate) -> None:
    config = build_day_config(loaded, date(2026, 10, 6))

    assert config.dataset == "history-trades"
    assert config.payload_format == "gzip"
    assert config.expected_content_types == ("application/octet-stream",)
    assert (config.min_bytes, config.max_bytes) == (20, 268_435_456)
    assert config.max_uncompressed_bytes == 2_147_483_648
    assert (config.requests_per_minute, config.min_request_interval_s) == (20, 3.0)
    assert config.event_ts_type == "TBANK_TRADE_TS_UTC"
    assert config.clock_accuracy_documented is False
    assert config.trade_action == "UNKNOWN"
    assert config.ticker_cc == "GDZ6_SPBFUT"
    assert config.instrument_uid is None  # observed from the archive, never configured
    assert config.canonical_contract == "MOEX:GD:2026-12-18"
    assert config.expiry == date(2026, 12, 18)
    assert (config.currency, config.price_unit) == ("USD", "USD per troy ounce")
    assert (config.contract_multiplier, config.tick_size) == (Decimal(1), Decimal("0.1"))
    assert config.license_url == "https://www.tbank.ru/invest/disclaimers/basic-information/"
    assert config.license_version == "retrieved-2026-11-02"
    assert config.zero_cost_evidence_url == (
        "https://developer.tbank.ru/invest/services/history-md/head-history-md"
    )
    assert config.cost_currency == "RUB"
    assert config.expected_columns == (
        "TRADE_TS",
        "TICKER_CC",
        "DIRECTION",
        "PRICE",
        "QUANTITY",
        "TRADE_SOURCE",
        "INSTRUMENT_UID",
    )


def test_weekend_has_no_day_config(loaded: LoadedTemplate) -> None:
    with pytest.raises(CalendarError):
        build_day_config(loaded, date(2026, 9, 5))


def test_canary_must_be_a_regular_day(loaded: LoadedTemplate) -> None:
    with pytest.raises(ValueError, match="canary"):
        plan_requests(date(2026, 9, 1), date(2026, 11, 1), loaded.calendar, canary=date(2026, 9, 5))


def test_plan_outside_the_calendar_is_refused(loaded: LoadedTemplate) -> None:
    with pytest.raises(CalendarError):
        plan_requests(date(2026, 8, 31), date(2026, 9, 3), loaded.calendar, canary=date(2026, 9, 2))
    with pytest.raises(CalendarError):
        plan_requests(
            date(2026, 10, 30), date(2026, 11, 3), loaded.calendar, canary=date(2026, 10, 30)
        )


def _copy_screen(tmp_path: Path, edit: "Callable[[str], str]") -> Path:
    shutil.copy(
        SCREEN / "moex_calendar_2026_09_10.toml", tmp_path / "moex_calendar_2026_09_10.toml"
    )
    target = tmp_path / "gdz6_base.toml"
    target.write_text(edit(TEMPLATE.read_text(encoding="utf-8")), encoding="utf-8")
    return target


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('dataset = "history-trades"', 'dataset = "history-trades"\nurl = "https://x.invalid/a"'),
        ("canary_date = 2026-09-02", "canary_date = 2026-11-02"),
        ("instrumentId=GDZ6_SPBFUT", "instrumentId=GDZ6_SPBFUT&d={date}"),
        ('payload_format = "gzip"', 'payload_format = "gz"'),
        ("requests_per_minute = 20", "requests_per_minute = 31"),
    ],
)
def test_invalid_templates_are_refused(tmp_path: Path, old: str, new: str) -> None:
    path = _copy_screen(tmp_path, lambda text: text.replace(old, new, 1))
    with pytest.raises((ValidationError, ValueError)):
        load_template(path)


def test_exception_dates_are_not_requested(tmp_path: Path) -> None:
    path = _copy_screen(tmp_path, lambda text: text)
    calendar_path = tmp_path / "moex_calendar_2026_09_10.toml"
    calendar_text = calendar_path.read_text(encoding="utf-8").replace(
        "exceptions = []",
        'exceptions = [{ day = 2026-10-07, kind = "closed", '
        'source_url = "https://synthetic.invalid/n", note = "synthetic closure" }]',
    )
    calendar_path.write_text(calendar_text, encoding="utf-8")
    plan = plan_for(load_template(path))

    assert date(2026, 10, 7) not in plan.requested
    assert len(plan.requested) == 43
    skipped = {e.day: e for e in plan.not_requested}
    assert skipped[date(2026, 10, 7)].reason == "exchange_closed_not_requested"
    assert skipped[date(2026, 10, 7)].note is not None
