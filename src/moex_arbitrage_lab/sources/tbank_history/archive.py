"""Fail-closed validation of one downloaded daily payload: ZIP with one CSV, plain CSV, or a
gzip stream of one CSV (``payload_format``).

The provider served ``.csv.gz`` per the ``Content-Disposition`` observed in the HEAD probe of
2026-11-02 (``attachment; filename=GDZ6_SPBFUT_2026-10-06.csv.gz``). A gzip payload must be
exactly one gzip member: it is decompressed with a bounded streaming loop (never more than
``max_uncompressed_bytes + 1`` bytes); a truncated stream, a bad CRC/length trailer or any bytes
after the member are ``corrupt_gzip``.

Nothing here touches the network or the disk; the payload is inspected in memory. A rejected
payload raises ``ArchiveRejectedError`` with a stable, non-secret reason code; payload bytes are
never echoed.
"""

import io
import re
import zipfile
import zlib
from dataclasses import dataclass
from typing import Final

from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig

ARCHIVE_REJECTION_REASONS: Final = (
    "empty",
    "html_payload",
    "not_zip",
    "unexpected_zip",
    "corrupt_zip",
    "encrypted_member",
    "member_count",
    "member_name",
    "uncompressed_too_large",
    "not_gzip",
    "corrupt_gzip",
)

ZIP_LOCAL_HEADER: Final = b"PK\x03\x04"
GZIP_MAGIC: Final = b"\x1f\x8b"
_GZIP_WBITS: Final = 16 + zlib.MAX_WBITS
_UTF8_BOM: Final = b"\xef\xbb\xbf"
_SNIFF_BYTES: Final = 1024
_READ_CHUNK: Final = 1 << 20
_ZIP_ERRORS: Final = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    OSError,
    NotImplementedError,
    ValueError,
    RuntimeError,
)


class ArchiveRejectedError(RuntimeError):
    """The payload is not an acceptable daily file; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in ARCHIVE_REJECTION_REASONS:
            raise ValueError(f"unknown archive rejection reason: {reason!r}")
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True, slots=True)
class ArchiveContents:
    member_name: str
    csv_bytes: bytes
    member_count: int
    compressed_size: int
    uncompressed_size: int


def looks_like_html(payload: bytes) -> bool:
    """An HTML/text error page: ``<`` first after whitespace (and an optional UTF-8 BOM), or
    ``<html`` anywhere in the first KiB."""
    head = payload[:_SNIFF_BYTES]
    stripped = head.removeprefix(_UTF8_BOM).lstrip()
    return stripped.startswith(b"<") or b"<html" in head.lower()


def validate_archive(payload: bytes, config: TbankHistoryConfig) -> ArchiveContents:
    """Return the single CSV of ``payload`` or raise ``ArchiveRejectedError``."""
    if not payload:
        raise ArchiveRejectedError("empty", "payload has zero bytes")
    if looks_like_html(payload):
        raise ArchiveRejectedError("html_payload", "payload looks like an HTML/text page")
    if config.payload_format == "csv":
        return _validate_plain_csv(payload, config)
    if config.payload_format == "gzip":
        return _validate_gzip(payload, config)
    return _validate_zip(payload, config)


def _validate_gzip(payload: bytes, config: TbankHistoryConfig) -> ArchiveContents:
    if not payload.startswith(GZIP_MAGIC):
        raise ArchiveRejectedError("not_gzip", "payload does not start with the gzip magic")
    data = _gunzip_bounded(payload, config.max_uncompressed_bytes)
    if data.startswith(ZIP_LOCAL_HEADER):
        raise ArchiveRejectedError("unexpected_zip", "gzip payload contains a ZIP archive")
    return ArchiveContents(
        member_name=config.raw_filename.removesuffix(".gz"),
        csv_bytes=data,
        member_count=1,
        compressed_size=len(payload),
        uncompressed_size=len(data),
    )


def _gunzip_bounded(payload: bytes, limit: int) -> bytes:
    """Decompress exactly one gzip member; output is capped at ``limit + 1`` bytes in total."""
    decompressor = zlib.decompressobj(_GZIP_WBITS)
    chunks: list[bytes] = []
    total = 0
    pending = payload
    try:
        while not decompressor.eof:
            piece = decompressor.decompress(pending, min(_READ_CHUNK, limit + 1 - total))
            chunks.append(piece)
            total += len(piece)
            if total > limit:
                raise ArchiveRejectedError(
                    "uncompressed_too_large", f"decompressed size > {limit} bytes"
                )
            pending = decompressor.unconsumed_tail
            if not pending and not piece:
                break  # input exhausted before the end of the stream
    except ArchiveRejectedError:
        raise
    except (zlib.error, EOFError, OSError, ValueError):
        # zlib verifies the gzip header and the CRC32/ISIZE trailer.
        raise ArchiveRejectedError("corrupt_gzip", "gzip stream cannot be decompressed") from None
    if not decompressor.eof:
        raise ArchiveRejectedError("corrupt_gzip", "gzip stream is truncated")
    if decompressor.unused_data:
        raise ArchiveRejectedError("corrupt_gzip", "bytes follow the gzip member")
    return b"".join(chunks)


def _validate_plain_csv(payload: bytes, config: TbankHistoryConfig) -> ArchiveContents:
    if payload.startswith(ZIP_LOCAL_HEADER):
        raise ArchiveRejectedError("unexpected_zip", "payload_format is csv but payload is ZIP")
    if len(payload) > config.max_uncompressed_bytes:
        raise ArchiveRejectedError(
            "uncompressed_too_large", f"{len(payload)} > {config.max_uncompressed_bytes} bytes"
        )
    return ArchiveContents(
        member_name=config.raw_filename,
        csv_bytes=payload,
        member_count=1,
        compressed_size=len(payload),
        uncompressed_size=len(payload),
    )


def _validate_zip(payload: bytes, config: TbankHistoryConfig) -> ArchiveContents:
    if not payload.startswith(ZIP_LOCAL_HEADER):
        raise ArchiveRejectedError("not_zip", "payload does not start with a ZIP local header")
    limit = config.max_uncompressed_bytes
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except _ZIP_ERRORS:
        raise ArchiveRejectedError("corrupt_zip", "cannot open ZIP directory") from None
    with archive:
        infos = archive.infolist()
        if any(info.flag_bits & 0x1 for info in infos):
            raise ArchiveRejectedError("encrypted_member", "encrypted members are refused")
        if not infos or len(infos) > config.max_members:
            raise ArchiveRejectedError(
                "member_count", f"{len(infos)} members; allowed 1..{config.max_members}"
            )
        pattern = re.compile(config.csv_member_pattern)
        matching = [info for info in infos if pattern.fullmatch(info.filename)]
        if len(matching) != 1:
            raise ArchiveRejectedError(
                "member_name", f"{len(matching)} members match csv_member_pattern; need 1"
            )
        # Declared sizes are checked before anything is decompressed (bomb guard) ...
        if sum(info.file_size for info in infos) > limit:
            raise ArchiveRejectedError("uncompressed_too_large", f"declared size > {limit} bytes")
        try:
            # CRC of every member (reads are bounded by the declared sizes checked above).
            bad = archive.testzip()
        except _ZIP_ERRORS:
            raise ArchiveRejectedError("corrupt_zip", "ZIP integrity check failed") from None
        if bad is not None:
            raise ArchiveRejectedError("corrupt_zip", "CRC mismatch in a member")
        member = matching[0]
        # ... and the actual decompressed size is enforced with a bounded loop.
        data = _read_bounded(archive, member, limit)
    if len(data) != member.file_size:
        raise ArchiveRejectedError("corrupt_zip", "member size differs from its header")
    return ArchiveContents(
        member_name=member.filename,
        csv_bytes=data,
        member_count=len(infos),
        compressed_size=member.compress_size,
        uncompressed_size=len(data),
    )


def _read_bounded(archive: zipfile.ZipFile, member: zipfile.ZipInfo, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(member) as handle:
            while True:
                chunk = handle.read(min(_READ_CHUNK, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise ArchiveRejectedError(
                        "uncompressed_too_large", f"decompressed size > {limit} bytes"
                    )
    except ArchiveRejectedError:
        raise
    except _ZIP_ERRORS:
        raise ArchiveRejectedError("corrupt_zip", "member cannot be decompressed") from None
    return b"".join(chunks)
