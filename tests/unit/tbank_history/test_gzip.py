"""Fail-closed validation of synthetic ``payload_format = "gzip"`` payloads (in memory only).

Covers the accepted single-member stream, empty/HTML/not-gzip payloads, truncation, CRC and
ISIZE trailer corruption, trailing garbage and a second member, a corrupt deflate body, an
in-memory gzip bomb (bounded streaming decompression; memory stays far below the inflated size),
the exact uncompressed limit and a ZIP hidden inside the gzip stream. The zip/csv modes are
covered by ``test_archive.py`` and keep working.
"""

import struct
import tracemalloc
from typing import TYPE_CHECKING

import pytest

from moex_arbitrage_lab.sources.tbank_history import (
    ArchiveRejectedError,
    TbankHistoryConfig,
    validate_archive,
)

if TYPE_CHECKING:
    from tests.unit.tbank_history.conftest import Builders, ConfigFactory


@pytest.fixture
def gz_config(make_config: "ConfigFactory") -> TbankHistoryConfig:
    return make_config("gzip")


def _reason(payload: bytes, config: TbankHistoryConfig) -> str:
    with pytest.raises(ArchiveRejectedError) as caught:
        validate_archive(payload, config)
    return caught.value.reason


def test_single_member_gzip_is_accepted(
    gz_config: TbankHistoryConfig, builders: "Builders"
) -> None:
    payload = builders.synthetic_gzip
    contents = validate_archive(payload, gz_config)

    assert contents.csv_bytes == builders.synthetic_csv
    assert contents.member_name == gz_config.raw_filename.removesuffix(".gz")
    assert contents.member_name.endswith(".csv")
    assert contents.member_count == 1
    assert contents.compressed_size == len(payload)
    assert contents.uncompressed_size == len(builders.synthetic_csv)


def test_gzip_with_header_name_field_is_accepted(
    gz_config: TbankHistoryConfig, builders: "Builders"
) -> None:
    # FNAME flag set (as a server-side ``gzip file.csv`` would write); member name still
    # comes from the configured raw filename, never from the untrusted header.
    import gzip
    import io

    buffer = io.BytesIO()
    with gzip.GzipFile(filename="evil/../name.csv", mode="wb", fileobj=buffer, mtime=0) as gz:
        gz.write(builders.synthetic_csv)
    contents = validate_archive(buffer.getvalue(), gz_config)

    assert contents.csv_bytes == builders.synthetic_csv
    assert contents.member_name == gz_config.raw_filename.removesuffix(".gz")


def test_empty(gz_config: TbankHistoryConfig) -> None:
    assert _reason(b"", gz_config) == "empty"


@pytest.mark.parametrize(
    "payload",
    [b"<!DOCTYPE html><html>synthetic</html>", b"\xef\xbb\xbf  <html>synthetic</html>"],
)
def test_html(gz_config: TbankHistoryConfig, payload: bytes) -> None:
    assert _reason(payload, gz_config) == "html_payload"


def test_plain_csv_is_not_gzip(gz_config: TbankHistoryConfig, builders: "Builders") -> None:
    assert _reason(builders.synthetic_csv, gz_config) == "not_gzip"


def test_zip_container_is_not_gzip(gz_config: TbankHistoryConfig, builders: "Builders") -> None:
    assert _reason(builders.synthetic_zip, gz_config) == "not_gzip"


@pytest.mark.parametrize("cut", [2, 10, 0.5, -9, -8, -4, -1])
def test_truncated_gzip_is_corrupt(
    gz_config: TbankHistoryConfig, builders: "Builders", cut: float
) -> None:
    payload = builders.synthetic_gzip
    end = int(len(payload) * cut) if isinstance(cut, float) else cut
    truncated = payload[:end]

    assert _reason(truncated, gz_config) == "corrupt_gzip"


def test_bad_crc_trailer_is_corrupt(gz_config: TbankHistoryConfig, builders: "Builders") -> None:
    data = bytearray(builders.synthetic_gzip)
    (crc,) = struct.unpack_from("<I", data, len(data) - 8)
    struct.pack_into("<I", data, len(data) - 8, crc ^ 0x1)

    assert _reason(bytes(data), gz_config) == "corrupt_gzip"


def test_bad_isize_trailer_is_corrupt(gz_config: TbankHistoryConfig, builders: "Builders") -> None:
    data = bytearray(builders.synthetic_gzip)
    (size,) = struct.unpack_from("<I", data, len(data) - 4)
    struct.pack_into("<I", data, len(data) - 4, size + 1)

    assert _reason(bytes(data), gz_config) == "corrupt_gzip"


@pytest.mark.parametrize("tail", [b"\x00", b"synthetic trailing garbage", b"\x1f\x8b"])
def test_trailing_bytes_are_corrupt(
    gz_config: TbankHistoryConfig, builders: "Builders", tail: bytes
) -> None:
    assert _reason(builders.synthetic_gzip + tail, gz_config) == "corrupt_gzip"


def test_second_member_is_corrupt(gz_config: TbankHistoryConfig, builders: "Builders") -> None:
    two = builders.synthetic_gzip + builders.gzip(builders.synthetic_csv)

    assert _reason(two, gz_config) == "corrupt_gzip"


def test_corrupt_deflate_body_is_corrupt(
    gz_config: TbankHistoryConfig, builders: "Builders"
) -> None:
    data = bytearray(builders.synthetic_gzip)
    for index in range(10, 26):  # right after the 10-byte header
        data[index] = 0xFF

    assert _reason(bytes(data), gz_config) == "corrupt_gzip"


def test_gzip_bomb_is_cut_with_bounded_memory(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    config = make_config("gzip", max_uncompressed_bytes=1_000_000)
    inflated = 64 * 1024 * 1024
    bomb = builders.gzip_bomb(inflated)
    assert len(bomb) < inflated // 500  # tiny on the wire, huge when inflated

    tracemalloc.start()
    try:
        reason = _reason(bomb, config)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert reason == "uncompressed_too_large"
    assert peak < 16 * 1024 * 1024  # never inflates anywhere near 64 MiB


def test_uncompressed_limit_is_inclusive(
    make_config: "ConfigFactory", builders: "Builders"
) -> None:
    size = len(builders.synthetic_csv)
    exact = make_config("gzip", max_uncompressed_bytes=size)
    smaller = make_config("gzip", max_uncompressed_bytes=size - 1)

    assert validate_archive(builders.synthetic_gzip, exact).csv_bytes == builders.synthetic_csv
    assert _reason(builders.synthetic_gzip, smaller) == "uncompressed_too_large"


def test_zip_inside_gzip_is_unexpected(gz_config: TbankHistoryConfig, builders: "Builders") -> None:
    assert _reason(builders.gzip(builders.synthetic_zip), gz_config) == "unexpected_zip"


def test_other_modes_keep_working(make_config: "ConfigFactory", builders: "Builders") -> None:
    assert validate_archive(builders.synthetic_zip, make_config("zip")).member_count == 1
    assert (
        validate_archive(builders.synthetic_csv, make_config("csv")).csv_bytes
        == builders.synthetic_csv
    )
    assert _reason(builders.synthetic_gzip, make_config("zip")) == "not_zip"
