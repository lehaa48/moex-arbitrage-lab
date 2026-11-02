"""Fail-closed validation of synthetic daily payloads (``payload_format`` zip and csv).

Archives are built in memory with ``zipfile``; nothing is written to disk. Covers empty, HTML,
not-a-zip, truncated and CRC-corrupt archives, member count and name, encrypted members, zip
bombs (honest and lying declared sizes) and the csv-format equivalents.
"""

import zipfile
from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.sources.tbank_history import (
    ArchiveRejectedError,
    TbankHistoryConfig,
    validate_archive,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import Builders, ConfigFactory

HTML_PAYLOADS = [
    b"<!DOCTYPE html><html><body>synthetic error page</body></html>",
    b"<html><head><title>synthetic 404</title></head></html>",
    b"  \r\n<HTML>synthetic</HTML>",
    b"\xef\xbb\xbf<!doctype html><html>synthetic</html>",
]


def _reason(payload: bytes, config: TbankHistoryConfig) -> str:
    with pytest.raises(ArchiveRejectedError) as caught:
        validate_archive(payload, config)
    return caught.value.reason


# --- zip format -----------------------------------------------------------------------------------


def test_valid_single_member_zip_is_accepted(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    payload = builders.synthetic_zip
    contents = validate_archive(payload, config)

    assert contents.member_name == builders.member_name
    assert contents.csv_bytes == builders.synthetic_csv
    assert contents.member_count == 1
    assert contents.uncompressed_size == len(builders.synthetic_csv)
    assert 0 < contents.compressed_size <= len(payload)


def test_stored_member_is_accepted(config: TbankHistoryConfig, builders: "Builders") -> None:
    payload = builders.zip([(builders.member_name, builders.synthetic_csv)], zipfile.ZIP_STORED)

    assert validate_archive(payload, config).csv_bytes == builders.synthetic_csv


def test_empty_payload(config: TbankHistoryConfig) -> None:
    assert _reason(b"", config) == "empty"


@pytest.mark.parametrize("payload", HTML_PAYLOADS)
def test_html_payload(config: TbankHistoryConfig, payload: bytes) -> None:
    assert _reason(payload, config) == "html_payload"


@pytest.mark.parametrize(
    "payload",
    [b"synthetic plain text, not an archive", b"\x1f\x8b\x08synthetic-gzip", b"PK"],
)
def test_not_a_zip(config: TbankHistoryConfig, payload: bytes) -> None:
    assert _reason(payload, config) in {"not_zip", "corrupt_zip"}


def test_plain_csv_in_zip_mode_is_not_a_zip(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    assert _reason(builders.synthetic_csv, config) == "not_zip"


@pytest.mark.parametrize("fraction", [0.25, 0.5, 0.9])
def test_truncated_zip_fails_closed(
    config: TbankHistoryConfig, builders: "Builders", fraction: float
) -> None:
    payload = builders.synthetic_zip
    truncated = payload[: int(len(payload) * fraction)]

    assert _reason(truncated, config) in {"corrupt_zip", "not_zip"}


def test_crc_corrupt_member_is_corrupt_zip(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    payload = bytearray(
        builders.zip([(builders.member_name, builders.synthetic_csv)], zipfile.ZIP_STORED)
    )
    offset = payload.index(b"TRADE_TS")
    payload[offset] ^= 0x01  # flips one data byte; sizes stay valid, CRC-32 no longer matches

    assert _reason(bytes(payload), config) == "corrupt_zip"


def test_garbage_deflate_stream_is_corrupt_zip(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    payload = bytearray(builders.synthetic_zip)
    start = payload.index(b"PK\x03\x04") + 30 + len(builders.member_name)
    for index in range(start, start + 16):
        payload[index] = 0xFF

    assert _reason(bytes(payload), config) == "corrupt_zip"


def test_two_members_are_refused(config: TbankHistoryConfig, builders: "Builders") -> None:
    payload = builders.zip(
        [
            (builders.member_name, builders.synthetic_csv),
            ("synthetic-gdz6-20261016.csv", builders.synthetic_csv),
        ]
    )

    assert _reason(payload, config) == "member_count"


def test_zero_members_are_refused(config: TbankHistoryConfig, builders: "Builders") -> None:
    assert _reason(builders.zip([]), config) in {"member_count", "empty", "not_zip"}


@pytest.mark.parametrize(
    "name",
    [
        "other-20261015.csv",
        "synthetic-gdz6-20261015.csv.exe",
        "SYNTHETIC-GDZ6-20261015.CSV",
        "../synthetic-gdz6-20261015.csv",
        "nested/synthetic-gdz6-20261015.csv",
        "synthetic-gdz6-2026101.csv",
    ],
)
def test_wrong_member_name_is_refused(
    config: TbankHistoryConfig, builders: "Builders", name: str
) -> None:
    payload = builders.zip([(name, builders.synthetic_csv)])

    assert _reason(payload, config) == "member_name"


def test_encrypted_member_is_refused(config: TbankHistoryConfig, builders: "Builders") -> None:
    payload = builders.patch_zip_flags(builders.synthetic_zip, 0x1)

    assert _reason(payload, config) == "encrypted_member"


def test_zip_bomb_with_honest_declared_size(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    config = make_config(max_uncompressed_bytes=100_000)
    bomb = builders.zip([(builders.member_name, b"0" * 2_000_000)])
    assert len(bomb) < 10_000  # tiny on the wire, huge when inflated

    assert _reason(bomb, config) == "uncompressed_too_large"


def test_zip_bomb_lying_about_its_size_is_never_accepted(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    config = make_config(max_uncompressed_bytes=100_000)
    bomb = builders.zip([(builders.member_name, b"0" * 2_000_000)])
    lying = builders.patch_zip_declared_size(bomb, 100)

    # Either the inflated stream is cut at the limit or the size/CRC lie is detected.
    assert _reason(lying, config) in {"uncompressed_too_large", "corrupt_zip"}


def test_member_exactly_at_the_limit_is_accepted(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    config = make_config(max_uncompressed_bytes=len(builders.synthetic_csv))

    assert validate_archive(builders.synthetic_zip, config).csv_bytes == builders.synthetic_csv
    smaller = make_config(max_uncompressed_bytes=len(builders.synthetic_csv) - 1)
    assert _reason(builders.synthetic_zip, smaller) == "uncompressed_too_large"


def test_rejection_reason_is_from_the_documented_vocabulary(
    config: TbankHistoryConfig, builders: "Builders"
) -> None:
    documented = {
        "empty",
        "html_payload",
        "not_zip",
        "corrupt_zip",
        "encrypted_member",
        "member_count",
        "member_name",
        "uncompressed_too_large",
        "unexpected_zip",
    }
    payloads = [b"", HTML_PAYLOADS[0], b"synthetic", builders.synthetic_zip[:40]]
    assert {_reason(p, config) for p in payloads} <= documented


# --- csv format -----------------------------------------------------------------------------------


@pytest.fixture
def csv_config(make_config: "ConfigFactory") -> TbankHistoryConfig:
    return make_config("csv")


def test_csv_payload_is_returned_unchanged(
    csv_config: TbankHistoryConfig, builders: "Builders"
) -> None:
    contents = validate_archive(builders.synthetic_csv, csv_config)

    assert contents.member_name == csv_config.raw_filename
    assert contents.csv_bytes == builders.synthetic_csv
    assert contents.member_count == 1
    assert contents.compressed_size == len(builders.synthetic_csv)
    assert contents.uncompressed_size == len(builders.synthetic_csv)


def test_csv_empty_payload(csv_config: TbankHistoryConfig) -> None:
    assert _reason(b"", csv_config) == "empty"


@pytest.mark.parametrize("payload", HTML_PAYLOADS)
def test_csv_html_payload(csv_config: TbankHistoryConfig, payload: bytes) -> None:
    assert _reason(payload, csv_config) == "html_payload"


def test_csv_mode_refuses_a_zip_payload(
    csv_config: TbankHistoryConfig, builders: "Builders"
) -> None:
    assert _reason(builders.synthetic_zip, csv_config) == "unexpected_zip"


def test_csv_payload_over_the_uncompressed_limit(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    exact = make_config("csv", max_uncompressed_bytes=len(builders.synthetic_csv))
    smaller = make_config("csv", max_uncompressed_bytes=len(builders.synthetic_csv) - 1)

    assert validate_archive(builders.synthetic_csv, exact).csv_bytes == builders.synthetic_csv
    assert _reason(builders.synthetic_csv, smaller) == "uncompressed_too_large"
