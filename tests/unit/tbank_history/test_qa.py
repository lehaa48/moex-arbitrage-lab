"""Source QA over synthetic parsed trades: timestamp quantum (represented vs observed), ties,
in-file monotonicity, gaps and machine-readable lag-bucket support statuses.

Every timestamp, price and trade below is synthetic.
"""

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.sources.tbank_history import (
    ParseResult,
    TbankHistoryConfig,
    compute_qa,
    parse_trades,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import Builders, ConfigFactory

SECOND = 1_000_000_000
STATUSES = {
    "UNSUPPORTED_BY_DATA",
    "TIMESTAMP_SEMANTICS_INSUFFICIENT",
    "QUANTUM_SUPPORTED_DENSITY_UNASSESSED",
}


def _ts(offset_ns: int, digits: int) -> str:
    """``2026-10-15T07:MM:SS[.f]Z`` for a synthetic offset from 07:00:00 UTC (main session)."""
    seconds, fraction = divmod(offset_ns, SECOND)
    minutes, seconds = divmod(seconds, 60)
    text = f"2026-10-15T07:{minutes:02d}:{seconds:02d}"
    if digits:
        assert fraction % 10 ** (9 - digits) == 0, "offset not representable"
        text += "." + f"{fraction:09d}"[:digits]
    return text + "Z"


def _result(
    builders: "Builders",
    config: TbankHistoryConfig,
    offsets: list[int],
    digits: int,
    sources: list[str] | None = None,
) -> ParseResult:
    sources = sources or ["EXCHANGE"] * len(offsets)
    rows = [
        builders.row(ts=_ts(offset, digits), source=source, price=f"40{index % 10}0.1")
        for index, (offset, source) in enumerate(zip(offsets, sources, strict=True))
    ]
    return parse_trades(
        builders.csv(rows),
        config=config,
        source_file="data/raw/tbank/synthetic-anon-trades/gdz6/2026-10-15/synthetic.zip",
        raw_payload_hash=sha256_hex(b"synthetic qa payload"),
        local_ingest_ts_utc_ns=1_760_569_200 * SECOND,
    )


def _expected_status(representation: int, horizon: int, documented: bool) -> str:
    if representation >= horizon:
        return "UNSUPPORTED_BY_DATA"
    if not documented:
        return "TIMESTAMP_SEMANTICS_INSUFFICIENT"
    return "QUANTUM_SUPPORTED_DENSITY_UNASSESSED"


def _qa(result: ParseResult, config: TbankHistoryConfig) -> dict[str, Any]:
    """``compute_qa`` through a strict JSON round trip (it must be JSON-serializable)."""
    loaded = json.loads(json.dumps(compute_qa(result, config), allow_nan=False))
    assert isinstance(loaded, dict)
    return loaded


def _timestamp(qa: dict[str, Any], key: str) -> Any:
    """A timestamp-quantum key, top level or grouped under ``timestamp`` (the interface names
    the keys, not their nesting)."""
    if key in qa:
        return qa[key]
    group = qa.get("timestamp")
    assert isinstance(group, dict), f"{key} missing from QA"
    assert key in group, f"{key} missing from QA"
    return group[key]


GAP_OFFSETS = [0, 500_000_000, 2_500_000_000, 6_500_000_000]  # gaps 0.5 s, 2 s, 4 s


def test_qa_is_json_serializable_and_deterministic(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    result = _result(builders, config, GAP_OFFSETS, 6)

    first = _qa(result, config)
    second = _qa(result, config)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert isinstance(_timestamp(first, "representation_quantum_ns"), int)
    assert isinstance(_timestamp(first, "observed_quantum_ns"), int)
    for key in (
        "ties",
        "non_monotonic_in_file",
        "potential_duplicates",
        "composition",
        "lag_support",
    ):
        assert key in first, key


@pytest.mark.parametrize(("digits", "quantum"), [(0, SECOND), (3, 1_000_000), (6, 1_000), (9, 1)])
def test_representation_quantum_follows_the_maximum_fractional_digits(
    config: TbankHistoryConfig, builders: "Builders", digits: int, quantum: int
) -> None:
    result = _result(builders, config, [0, SECOND, 3 * SECOND], digits)

    assert _timestamp(_qa(result, config), "representation_quantum_ns") == quantum


def test_observed_quantum_is_the_gcd_of_non_zero_eligible_gaps(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    # Eligible gaps 3000, 6000, 0 (tie), 6000, 9000 ns -> gcd 3000. The DEALER row at +1000 ns
    # would pull the gcd down to 1000 if ineligible rows were wrongly included.
    offsets = [0, 3_000, 9_000, 9_000, 15_000, 24_000, 1_000]
    sources = ["EXCHANGE"] * 6 + ["DEALER"]
    result = _result(builders, config, offsets, 6, sources)

    qa = _qa(result, config)

    assert _timestamp(qa, "observed_quantum_ns") == 3_000
    assert _timestamp(qa, "representation_quantum_ns") == 1_000


def test_in_file_monotonicity_is_measured_not_assumed(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    monotone = _result(builders, config, [0, SECOND, 2 * SECOND, 3 * SECOND], 6)
    inverted = _result(builders, config, [0, 2 * SECOND, SECOND, 3 * SECOND], 6)

    assert _qa(monotone, config)["non_monotonic_in_file"] == 0
    assert _qa(inverted, config)["non_monotonic_in_file"] == 1


def test_ties_are_reported(config: TbankHistoryConfig, builders: "Builders") -> None:
    tied = _qa(_result(builders, config, [0, SECOND, SECOND, SECOND, 2 * SECOND], 6), config)
    untied = _qa(_result(builders, config, [0, SECOND, 2 * SECOND], 6), config)

    assert tied["ties"] != untied["ties"]


@pytest.mark.parametrize("documented", [False, True])
@pytest.mark.parametrize("digits", [0, 3, 6, 9])
def test_lag_support_status_per_bucket(
    make_config: "ConfigFactory", builders: "Builders", digits: int, documented: bool
) -> None:
    config = make_config(clock_accuracy_documented=documented)
    result = _result(builders, config, [0, SECOND, 3 * SECOND, 7 * SECOND], digits)

    qa = _qa(result, config)

    representation = 10 ** (9 - digits)
    support = qa["lag_support"]
    assert [entry["horizon_ns"] for entry in support] == list(builders.lag_buckets_ns)
    for entry in support:
        horizon = entry["horizon_ns"]
        assert entry["status"] in STATUSES
        assert entry["status"] == _expected_status(representation, horizon, documented), horizon
        assert entry["representation_quantum_ns"] == representation
        assert entry["observed_quantum_ns"] == _timestamp(qa, "observed_quantum_ns")
        assert entry["quantum_finer_than_horizon"] is (representation < horizon)
        assert entry["clock_accuracy_documented"] is documented
        assert entry["eligible_events"] == 4


def test_one_second_timestamps_cannot_support_sub_second_or_one_second_lags(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    qa = _qa(_result(builders, config, [0, SECOND, 3 * SECOND], 0), config)
    by_horizon = {entry["horizon_ns"]: entry["status"] for entry in qa["lag_support"]}

    assert by_horizon[1_000_000] == "UNSUPPORTED_BY_DATA"
    assert by_horizon[500_000_000] == "UNSUPPORTED_BY_DATA"
    assert by_horizon[SECOND] == "UNSUPPORTED_BY_DATA"  # quantum >= horizon
    assert by_horizon[2 * SECOND] == "TIMESTAMP_SEMANTICS_INSUFFICIENT"


def test_microsecond_representation_alone_never_claims_full_support(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    # A six-digit representation is not proof of microsecond clock accuracy.
    qa = _qa(_result(builders, config, GAP_OFFSETS, 6), config)

    assert config.clock_accuracy_documented is False
    assert {entry["status"] for entry in qa["lag_support"]} == {"TIMESTAMP_SEMANTICS_INSUFFICIENT"}


@pytest.mark.parametrize(
    ("horizon", "fraction"),
    [
        (1_000_000, 0.0),
        (500_000_000, 1 / 3),
        (SECOND, 1 / 3),
        (2 * SECOND, 2 / 3),
        (5 * SECOND, 1.0),
        (300 * SECOND, 1.0),
    ],
)
def test_fraction_of_gaps_within_each_horizon(
    config: TbankHistoryConfig, builders: "Builders", horizon: int, fraction: float
) -> None:
    qa = _qa(_result(builders, config, GAP_OFFSETS, 6), config)
    entry = next(e for e in qa["lag_support"] if e["horizon_ns"] == horizon)

    assert float(Decimal(str(entry["fraction_of_gaps_le_horizon"]))) == pytest.approx(fraction)


def test_lag_buckets_come_from_the_config(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    config = make_config(lag_buckets_ns=(1_000, 1_000_000, 2 * SECOND))
    qa = _qa(_result(builders, config, GAP_OFFSETS, 3), config)

    statuses = [(e["horizon_ns"], e["status"]) for e in qa["lag_support"]]
    assert statuses == [
        (1_000, "UNSUPPORTED_BY_DATA"),
        (1_000_000, "UNSUPPORTED_BY_DATA"),
        (2 * SECOND, "TIMESTAMP_SEMANTICS_INSUFFICIENT"),
    ]


def test_ineligible_rows_are_not_eligible_events(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    sources = ["EXCHANGE", "DEALER", "EXCHANGE", "DEALER"]
    qa = _qa(_result(builders, config, GAP_OFFSETS, 6, sources), config)

    assert {entry["eligible_events"] for entry in qa["lag_support"]} == {2}
    assert qa["composition"]["TRADE_SOURCE"] == {"EXCHANGE": 2, "DEALER": 2}
