"""Batch file validation before publication (synthetic CSV/JSON)."""

from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.sources.databento_history import PayloadRejectedError, validate_file
from moex_arbitrage_lab.sources.databento_history.validate import check_provider_filename

if TYPE_CHECKING:
    from tests.unit.databento_history.conftest import Kit


def _row(kit: "Kit", ts: int | str, **overrides: str) -> str:
    values = {
        "ts_event": str(ts),
        "rtype": "32",
        "publisher_id": "1",
        "instrument_id": str(kit.instrument_id),
        "open": "2650.1",
        "high": "2650.4",
        "low": "2650.0",
        "close": "2650.3",
        "volume": "3",
        "symbol": "GCZ6",
    }
    values.update(overrides)
    return ",".join(values.values())


def test_valid_csv_summary(kit: "Kit") -> None:
    result = validate_file(kit.csv_name, kit.csv(), frozenset({kit.instrument_id}))

    assert result.kind == "csv"
    assert result.csv is not None
    assert result.csv.rows == 3
    assert result.csv.instrument_ids == (kit.instrument_id,)
    assert result.csv.symbols == ("GCZ6",)
    assert result.csv.ts_event_min == kit.start_ns + 3_600 * 10**9
    assert result.csv.not_second_aligned == 0
    assert result.metadata["csv_rows"] == "3"
    assert all(0 < len(v) <= 256 for v in result.metadata.values())


def test_descriptive_qa_is_measured_not_rejected(kit: "Kit") -> None:
    base = kit.start_ns + 10**9
    rows = [
        _row(kit, base + 5),  # not second-aligned
        _row(kit, base + 5),  # tie
        _row(kit, base),  # decreasing step
        _row(kit, base + 2 * 10**9, high="2649.0"),  # OHLC inconsistent
        _row(kit, base + 3 * 10**9, volume="0"),
    ]
    result = validate_file(kit.csv_name, kit.csv(rows), frozenset({kit.instrument_id}))

    assert result.csv is not None
    assert (result.csv.not_second_aligned, result.csv.ts_event_ties) == (2, 1)
    assert result.csv.ts_event_decreasing_steps == 1
    assert result.csv.ohlc_inconsistent == 1
    assert result.csv.zero_volume == 1


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("empty", "empty file"),
        ("html", "HTML"),
        ("bom", "byte-order mark"),
        ("not_utf8", "not UTF-8"),
        ("header_only", "no data rows"),
        ("missing_column", "unexpected CSV header"),
        ("ragged", "ragged row"),
        ("pretty_ts", "non-integer ts_event"),
        ("before_start", "outside interval"),
        ("at_end", "outside interval"),
        ("other_instrument", "unexpected instrument_id"),
        ("other_symbol", "unexpected symbol"),
        ("bad_price", "non-decimal"),
        ("fraction_volume", "invalid volume"),
    ],
)
def test_invalid_csv_is_rejected(kit: "Kit", case: str, reason: str) -> None:
    ok_ts = kit.start_ns + 10**9
    end_ns = kit.start_ns + 51 * 86_400 * 10**9
    payloads = {
        "empty": b"",
        "html": b"<!DOCTYPE html><html><body>login</body></html>",
        "bom": b"\xef\xbb\xbf" + kit.csv(),
        "not_utf8": kit.csv([_row(kit, ok_ts, symbol="GCZ6")]).replace(b"GCZ6", b"\xff\xfe", 1),
        "header_only": kit.csv([]),
        "missing_column": kit.csv(header=kit.header.replace(",symbol", "")),
        "ragged": kit.csv([_row(kit, ok_ts) + ",extra"]),
        "pretty_ts": kit.csv([_row(kit, "2026-09-01T00:00:01.000000000Z")]),
        "before_start": kit.csv([_row(kit, kit.start_ns - 10**9)]),
        "at_end": kit.csv([_row(kit, end_ns)]),
        "other_instrument": kit.csv([_row(kit, ok_ts, instrument_id="999")]),
        "other_symbol": kit.csv([_row(kit, ok_ts, symbol="GCG7")]),
        "bad_price": kit.csv([_row(kit, ok_ts, close="NaN")]),
        "fraction_volume": kit.csv([_row(kit, ok_ts, volume="1.5")]),
    }
    with pytest.raises(PayloadRejectedError, match=reason):
        validate_file(kit.csv_name, payloads[case], frozenset({kit.instrument_id}))


def test_aux_json_validation(kit: "Kit") -> None:
    assert validate_file("metadata.json", kit.aux, frozenset()).kind == "aux_json"
    for bad in (b"", b"<html></html>", b"{", b"42"):
        with pytest.raises(PayloadRejectedError):
            validate_file("metadata.json", bad, frozenset())


@pytest.mark.parametrize(
    "name",
    [
        "GLBX.csv",
        "../x.csv",
        "a/b.csv",
        "x.csv.zst",
        "x.dbn",
        "x.dbn.zst",
        "x.zip",
        "",
        "con.csv",
        "x" * 120 + ".csv",
    ],
)
def test_unexpected_provider_file_names_are_refused(name: str) -> None:
    with pytest.raises(PayloadRejectedError):
        check_provider_filename(name)
