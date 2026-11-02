"""Fail-closed identity of synthetic per-instrument files and periods.

Every row must carry the configured ``TICKER_CC``; the target rows must carry exactly one
canonical-UUID ``INSTRUMENT_UID``; across a period the UID must not change. The UID is only
observed from the archive (status ``OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED``).
"""

from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.sources.tbank_history import (
    UID_STATUS,
    IdentityRejectedError,
    ParseResult,
    TbankHistoryConfig,
    check_file_identity,
    check_period_identity,
    parse_trades,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import Builders, ConfigFactory


def _parse(config: TbankHistoryConfig, csv: bytes) -> ParseResult:
    return parse_trades(
        csv,
        config=config,
        source_file="data/raw/synthetic.csv.gz",
        raw_payload_hash="0" * 64,
        local_ingest_ts_utc_ns=1,
    )


@pytest.fixture
def observed(make_config: "ConfigFactory") -> TbankHistoryConfig:
    """Config without a configured UID: the UID is observed from the file."""
    return make_config("gzip", instrument_uid=None)


def _reject(config: TbankHistoryConfig, csv: bytes) -> IdentityRejectedError:
    with pytest.raises(IdentityRejectedError) as caught:
        check_file_identity(_parse(config, csv), config)
    return caught.value


def test_clean_file_reports_the_observed_uid(
    observed: TbankHistoryConfig, builders: "Builders"
) -> None:
    identity = check_file_identity(_parse(observed, builders.synthetic_csv), observed)

    assert identity.instrument_uid == builders.uid
    assert identity.uid_status == UID_STATUS == "OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED"
    assert identity.target_rows == 4
    assert identity.other_ticker_rows == 0
    assert identity.to_json()["uid_status"] == UID_STATUS


def test_one_other_ticker_row_rejects_the_whole_file(
    observed: TbankHistoryConfig, builders: "Builders"
) -> None:
    csv = builders.csv([builders.row(), builders.row(ticker=builders.other_ticker), builders.row()])
    error = _reject(observed, csv)

    assert error.reason == "other_ticker_rows"
    assert error.counts == {"target_rows": 2, "other_ticker_rows": 1}


def test_no_target_rows(observed: TbankHistoryConfig, builders: "Builders") -> None:
    assert _reject(observed, builders.csv([])).reason == "no_target_rows"


def test_empty_uid(observed: TbankHistoryConfig, builders: "Builders") -> None:
    error = _reject(observed, builders.csv([builders.row(), builders.row(uid="")]))

    assert error.reason == "uid_missing"
    assert error.counts["empty_uid_rows"] == 1


def test_two_uids(observed: TbankHistoryConfig, builders: "Builders") -> None:
    csv = builders.csv([builders.row(), builders.row(uid=builders.other_uid)])

    assert _reject(observed, csv).reason == "uid_multiple"


@pytest.mark.parametrize(
    "uid",
    ["synthetic-not-a-uuid", "00000000000040008000000000000d0d6", "{" + "0" * 8 + "}"],
)
def test_non_uuid(observed: TbankHistoryConfig, builders: "Builders", uid: str) -> None:
    assert _reject(observed, builders.csv([builders.row(uid=uid)])).reason == "uid_not_uuid"


def test_configured_uid_must_match(make_config: "ConfigFactory", builders: "Builders") -> None:
    config = make_config("gzip", instrument_uid=builders.other_uid)
    error = _reject(config, builders.csv([builders.row()]))

    assert error.reason == "uid_config_mismatch"


def test_period_identity() -> None:
    uid = "00000000-0000-4000-8000-00000000d0d6"  # synthetic
    other = "00000000-0000-4000-8000-00000000beef"  # synthetic

    assert check_period_identity([]) is None
    assert check_period_identity([uid, uid, uid]) == uid
    with pytest.raises(IdentityRejectedError) as caught:
        check_period_identity([uid, other])
    assert caught.value.reason == "uid_changed_across_period"


def test_unknown_reason_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown identity rejection reason"):
        IdentityRejectedError("synthetic_reason")
