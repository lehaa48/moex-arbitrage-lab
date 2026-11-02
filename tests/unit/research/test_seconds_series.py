"""Causal one-second construction: availability, backward state, boundaries, ties, coarse
clocks, COMEX CSV strictness, Globex intervals and CSV round trip (all synthetic)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from moex_arbitrage_lab.research.seconds_config import ScreenConfig
from moex_arbitrage_lab.research.seconds_series import (
    ComexCsvError,
    CsvFormatError,
    Segment,
    SeriesError,
    build_comex_series,
    build_moex_day,
    combine_comex_bars,
    comex_open_intervals,
    common_segments,
    read_comex_ohlcv_csv,
    read_moex_trades_csv,
    seconds_from_csv,
    seconds_from_csv_light,
    seconds_to_csv,
    state_as_of,
)

NS = 1_000_000_000
DAY = date(2026, 10, 6)
D = Decimal


def _segment(at: Callable[..., int], name: str, h0: int, m0: int, h1: int, m1: int) -> Segment:
    return Segment(name, at(DAY, h0, m0) * NS, at(DAY, h1, m1) * NS, DAY)


def test_bar_close_is_not_available_before_the_end_of_its_second(at: Callable[..., int]) -> None:
    seg = _segment(at, "main", 7, 0, 7, 1)
    t = seg.start_s + 10
    trades = [(t * NS + 999_999_999, D("100.5"), D(1)), ((t - 3) * NS, D("100.0"), D(1))]
    result = build_moex_day(DAY, sorted(trades), [seg])
    records = result.records

    just_before = state_as_of(records, (t + 1) * NS - 1)
    at_end = state_as_of(records, (t + 1) * NS)

    assert just_before is not None
    assert just_before.second_s == t - 1
    assert just_before.state_close == D("100.0")
    assert at_end is not None
    assert at_end.second_s == t
    assert at_end.state_close == D("100.5")
    assert state_as_of(records, seg.start_ns) is None


def test_half_open_second_assignment_and_exact_age(at: Callable[..., int]) -> None:
    seg = _segment(at, "main", 7, 0, 7, 1)
    t = seg.start_s + 5
    trades = [(t * NS, D("10"), D(1)), ((t + 1) * NS - 1, D("11"), D(2))]
    rec = {r.second_s: r for r in build_moex_day(DAY, trades, [seg]).records}

    assert rec[t].open == D("10")
    assert rec[t].close == D("11")
    assert rec[t].trade_count == 2
    assert rec[t].volume == D(3)
    assert rec[t].age_ns == 1
    assert rec[t].first_obs_ts_ns == t * NS
    assert rec[t].state_obs_ts_ns == (t + 1) * NS - 1
    assert rec[t + 1].refreshed is False
    assert rec[t + 1].close is None
    assert rec[t + 1].state_close == D("11")
    assert rec[t + 1].age_ns == NS + 1
    assert rec[t + 1].volume == 0


def test_no_backfill_from_the_future_and_strict_backward_state(at: Callable[..., int]) -> None:
    seg = _segment(at, "main", 7, 0, 7, 2)
    t = seg.start_s + 30
    base = [(t * NS + 5, D("50"), D(1)), ((t + 20) * NS + 7, D("51"), D(1))]
    early = build_moex_day(DAY, base, [seg]).records
    later = build_moex_day(DAY, [*base, ((t + 40) * NS, D("99"), D(1))], [seg]).records

    for record in early[:30]:
        assert record.state_close is None
        assert record.age_ns is None
    # Adding a future trade never changes any earlier second.
    assert early[: 30 + 40] == later[: 30 + 40]


def test_state_is_never_carried_across_segments_or_breaks(at: Callable[..., int]) -> None:
    morning = _segment(at, "morning", 4, 0, 6, 0)
    main = _segment(at, "main", 6, 0, 6, 1)
    evening = _segment(at, "evening", 16, 0, 16, 1)
    trades = [(main.start_ns - 1, D("10"), D(1)), (main.end_ns - NS, D("11"), D(1))]
    result = build_moex_day(DAY, trades, [evening, main, morning])
    by_second = {r.second_s: r for r in result.records}

    assert by_second[main.start_s - 1].state_close == D("10")
    assert by_second[main.start_s].state_close is None  # contiguous next segment: no carry
    assert by_second[evening.start_s].state_close is None  # after a break: no carry
    assert by_second[main.start_s].session_id == "main"


def test_opening_auction_trades_are_excluded_and_counted(at: Callable[..., int]) -> None:
    auction_start = at(DAY, 3, 50) * NS
    main = _segment(at, "main", 4, 0, 4, 1)
    trades = [(auction_start + 5, D("1"), D(4)), (main.start_ns + 3, D("2"), D(1))]
    result = build_moex_day(DAY, trades, [main])

    assert result.excluded == {"outside_continuous_segment": 1}
    assert result.assigned_observations == 1
    assert result.reconciliation["ok"] is True
    assert result.reconciliation["volume"] == "1"


def test_timestamp_ties_keep_input_sequence(at: Callable[..., int]) -> None:
    seg = _segment(at, "main", 7, 0, 7, 1)
    ts = seg.start_ns + 123
    trades = [(ts, D("5"), D(1)), (ts, D("7"), D(1)), (ts, D("6"), D(1)), (ts, D("6"), D(1))]
    result = build_moex_day(DAY, trades, [seg])
    first = result.records[0]

    assert (first.open, first.high, first.low, first.close) == (D("5"), D("7"), D("5"), D("6"))
    assert result.timestamps.tie_observations == 3
    assert result.timestamps.exact_duplicates == 1


def test_coarse_and_fine_clock_quantum(at: Callable[..., int]) -> None:
    seg = _segment(at, "main", 7, 0, 7, 1)
    coarse = [(seg.start_ns + k * NS, D("5"), D(1)) for k in (0, 1, 3, 7)]
    fine = [(seg.start_ns + k * 1_000_000, D("5"), D(1)) for k in (2, 5, 9)]

    coarse_result = build_moex_day(DAY, coarse, [seg])
    fine_result = build_moex_day(DAY, fine, [seg])

    assert coarse_result.timestamps.quantum_ns == NS
    assert coarse_result.timestamps.subsecond_quantum_ns == NS
    # A whole-second trade belongs to the second it starts.
    assert [r.trade_count for r in coarse_result.records[:4]] == [1, 1, 0, 1]
    assert fine_result.timestamps.quantum_ns == 1_000_000
    assert fine_result.timestamps.max_gap_ns == 4_000_000
    assert fine_result.timestamps.n_gaps == 2
    assert fine_result.timestamps.inter_observation_gap_ns["1"] == 4_000_000


@pytest.mark.parametrize(
    "bad",
    [
        (1, D("0"), D(1)),
        (1, D("-1"), D(1)),
        (1, D("1"), D("0")),
        (-1, D("1"), D(1)),
        (1, 1.5, D(1)),
        (1, D("NaN"), D(1)),
    ],
)
def test_invalid_trades_are_refused(
    at: Callable[..., int], bad: tuple[int, object, object]
) -> None:
    seg = _segment(at, "main", 7, 0, 7, 1)

    with pytest.raises(SeriesError):
        build_moex_day(DAY, [bad], [seg])  # type: ignore[list-item]


def test_segments_must_be_whole_seconds_and_disjoint(at: Callable[..., int]) -> None:
    with pytest.raises(SeriesError):
        Segment("main", at(DAY, 7) * NS + 1, at(DAY, 8) * NS, DAY)
    a = _segment(at, "a", 7, 0, 8, 0)
    b = _segment(at, "b", 7, 30, 9, 0)
    with pytest.raises(SeriesError):
        build_moex_day(DAY, [], [a, b])


def test_moex_trades_csv_reader_is_strict() -> None:
    good = b"event_ts_utc_ns,price,size\n1796000000123456789,2401.5,2\n"

    assert read_moex_trades_csv(good) == [(1796000000123456789, D("2401.5"), D(2))]
    for bad in (
        b"event_ts_utc_ns,price\n1,2\n",
        b"event_ts_utc_ns,price,size\n1.5,2,1\n",
        b"event_ts_utc_ns,price,size\n1,2e3,1\n",
        b"event_ts_utc_ns,price,size\r\n1,2,1\r\n",
    ):
        with pytest.raises(CsvFormatError):
            read_moex_trades_csv(bad)


def test_seconds_csv_round_trip(at: Callable[..., int]) -> None:
    seg = _segment(at, "main", 7, 0, 7, 1)
    trades = [
        (seg.start_ns + 1_500_000_000, D("2401.10"), D(2)),
        (seg.start_ns + 9, D("2400"), D(1)),
    ]
    records = build_moex_day(DAY, trades, [seg]).records
    data = seconds_to_csv(records)

    assert seconds_from_csv(data) == list(records)
    light = seconds_from_csv_light(data)
    assert [row[0] for row in light] == [r.second_s for r in records]
    assert light[1][1] == "2401.10"
    assert light[1][4] is True
    assert data.startswith(
        b"second_utc,open,high,low,close,volume,trade_count,age_ms,refreshed,session_id,"
        b"trading_date,"
    )
    assert b"2026-10-06T07:00:01Z,2401.10,2401.10,2401.10,2401.10,2,1,500.000000,true,main" in data


# --- COMEX ----------------------------------------------------------------------------------------


def test_comex_csv_is_parsed_and_state_ages_are_upper_bounds(
    frozen: tuple[ScreenConfig, str], make_comex_csv: Callable[..., bytes], at: Callable[..., int]
) -> None:
    config = frozen[0]
    t = at(DAY, 7)
    data = make_comex_csv([(t, D("2400.100000000")), (t + 3, D("2400.200000000"))])
    bars = read_comex_ohlcv_csv(data, config.comex)
    intervals = comex_open_intervals(config.comex, config.period)
    only = frozenset({"globex-2026-10-06"})
    results, excluded, per_interval = build_comex_series(bars, intervals, config.period, only)
    (series,) = results
    by_second = {r.second_s: r for r in series.records}

    assert excluded == {"outside_period": 0, "outside_comex_open_interval": 0}
    assert per_interval["globex-2026-10-06"] == 2
    assert by_second[t].refreshed
    assert by_second[t].age_ns == NS
    assert by_second[t].trade_count is None
    assert by_second[t].close == D("2400.100000000")
    assert by_second[t + 2].state_close == D("2400.100000000")
    assert by_second[t + 2].age_ns == 3 * NS
    assert by_second[t + 2].close is None
    assert by_second[t - 1].state_close is None


HEADER = "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol"
ROW = "1791270000000000000,32,1,4242,2400.1,2400.1,2400.1,2400.1,1,GCZ6"


@pytest.mark.parametrize(
    "payload",
    [
        f"{HEADER.replace(',symbol', '')}\n{ROW.rsplit(',', 1)[0]}\n",
        f"{HEADER.replace('open,high', 'high,open')}\n{ROW}\n",
        f"{HEADER},extra\n{ROW},x\n",
        f"﻿{HEADER}\n{ROW}\n",
        f"{HEADER}\r\n{ROW}\r\n",
        f"{HEADER.upper()}\n{ROW}\n",
        f" {HEADER}\n{ROW}\n",
        f"{HEADER.replace('ts_event', 'ts_recv')}\n{ROW}\n",
        "",
    ],
)
def test_comex_header_deviation_is_rejected(frozen: tuple[ScreenConfig, str], payload: str) -> None:
    with pytest.raises(ComexCsvError):
        read_comex_ohlcv_csv(payload.encode("utf-8"), frozen[0].comex)


@pytest.mark.parametrize(
    "row",
    [
        ROW.replace("GCZ6", "GCG7"),
        ROW.replace("1791270000000000000", "1791270000000000001"),
        ROW.replace("1791270000000000000", "2026-10-06T07:00:00Z"),
        ROW.replace(",32,", ",33,"),
        ROW.replace("2400.1,2400.1,2400.1,2400.1", "nan,2400.1,2400.1,2400.1"),
        ROW.replace("2400.1,2400.1,2400.1,2400.1", "2.4e3,2400.1,2400.1,2400.1"),
        ROW.replace("2400.1,2400.1,2400.1,2400.1", "2400.1,2400.0,2400.1,2400.1"),
        ROW.replace(",1,GCZ6", ",0,GCZ6"),
        '"' + ROW + '"',
        ROW + ",",
    ],
)
def test_comex_bad_rows_are_rejected(frozen: tuple[ScreenConfig, str], row: str) -> None:
    with pytest.raises(ComexCsvError):
        read_comex_ohlcv_csv(f"{HEADER}\n{row}\n".encode(), frozen[0].comex)


def test_comex_files_must_share_one_instrument_and_have_unique_seconds(
    frozen: tuple[ScreenConfig, str],
) -> None:
    comex = frozen[0].comex
    first = read_comex_ohlcv_csv(f"{HEADER}\n{ROW}\n".encode(), comex, 0)
    other = read_comex_ohlcv_csv(
        f"{HEADER}\n{ROW.replace('4242', '4243').replace('70000', '70001')}\n".encode(), comex, 1
    )
    duplicate = read_comex_ohlcv_csv(f"{HEADER}\n{ROW}\n".encode(), comex, 2)
    non_increasing = f"{HEADER}\n{ROW}\n{ROW}\n".encode()

    with pytest.raises(ComexCsvError):
        combine_comex_bars([first, other])
    with pytest.raises(ComexCsvError):
        combine_comex_bars([first, duplicate])
    with pytest.raises(ComexCsvError):
        read_comex_ohlcv_csv(non_increasing, comex)
    assert combine_comex_bars([first])[1:] == (4242, 1)


def test_globex_intervals_daily_break_weekend_and_period_clip(
    frozen: tuple[ScreenConfig, str], at: Callable[..., int]
) -> None:
    config = frozen[0]
    intervals = {s.name: s for s in comex_open_intervals(config.comex, config.period)}
    monday = intervals["globex-2026-10-05"]

    assert monday.start_s == at(date(2026, 10, 4), 22)  # Sunday 17:00 CT = 22:00 UTC
    assert monday.end_s == at(date(2026, 10, 5), 21)  # Monday 16:00 CT = 21:00 UTC
    assert "globex-2026-10-03" not in intervals
    assert "globex-2026-10-04" not in intervals
    first = intervals["globex-2026-09-01"]
    assert first.start_ns == config.period.start_ns  # clipped at 2026-09-01T00:00Z
    assert "globex-2026-11-02" not in intervals
    for interval in intervals.values():
        start_tod = interval.start_s % 86_400
        assert start_tod in (0, 22 * 3600)
        assert interval.end_s % 86_400 == 21 * 3600 or interval.end_ns == config.period.end_ns


def test_bars_in_the_daily_break_or_outside_the_period_are_excluded_and_not_carried(
    frozen: tuple[ScreenConfig, str], make_comex_csv: Callable[..., bytes], at: Callable[..., int]
) -> None:
    config = frozen[0]
    before_break = at(DAY, 20, 59, 59)
    in_break = at(DAY, 21, 30)
    outside = at(date(2026, 11, 2), 1)
    bars = read_comex_ohlcv_csv(
        make_comex_csv([(before_break, D("1")), (in_break, D("2")), (outside, D("3"))]),
        config.comex,
    )
    intervals = comex_open_intervals(config.comex, config.period)
    only = frozenset({"globex-2026-10-07"})
    results, excluded, per_interval = build_comex_series(bars, intervals, config.period, only)
    (next_day,) = results

    assert excluded == {"outside_period": 1, "outside_comex_open_interval": 1}
    assert per_interval["globex-2026-10-06"] == 1  # counted although not built
    assert next_day.records[0].second_s == at(DAY, 22)
    assert next_day.records[0].state_close is None


def test_common_segments_intersect_sessions_and_period(
    frozen: tuple[ScreenConfig, str], at: Callable[..., int]
) -> None:
    config = frozen[0]
    intervals = comex_open_intervals(config.comex, config.period)
    evening = Segment("evening", at(DAY, 16) * NS, at(DAY, 22, 30) * NS, DAY)
    common = common_segments([evening], intervals, config.period)

    assert [(c.start_s, c.end_s) for c in common] == [
        (at(DAY, 16), at(DAY, 21)),
        (at(DAY, 22), at(DAY, 22, 30)),
    ]
    assert common[1].comex_session == "globex-2026-10-07"
    early = Segment("x", at(date(2026, 8, 31), 23) * NS, at(date(2026, 9, 1), 1) * NS, DAY)
    clipped = common_segments([early], intervals, config.period)
    assert clipped[0].start_s * NS == config.period.start_ns
