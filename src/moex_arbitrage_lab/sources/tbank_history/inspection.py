"""Offline, fail-closed inspection of one daily payload: container -> CSV -> ``trade_v1`` ->
reconciliation -> identity. Used before publication (period acquisition), for the per-file QA
and by the downstream loader, so all three apply exactly the same rules.

Raises ``ArchiveRejectedError`` (container), ``ValueError`` (header/schema mismatch or failed
reconciliation) or ``IdentityRejectedError`` (identity). Never touches the network or disk.
"""

from dataclasses import dataclass

from moex_arbitrage_lab.sources.tbank_history.archive import ArchiveContents, validate_archive
from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig
from moex_arbitrage_lab.sources.tbank_history.identity import FileIdentity, check_file_identity
from moex_arbitrage_lab.sources.tbank_history.parser import ParseResult, parse_trades


@dataclass(frozen=True, slots=True)
class FileInspection:
    contents: ArchiveContents
    result: ParseResult
    identity: FileIdentity


def inspect_payload(
    payload: bytes,
    config: TbankHistoryConfig,
    *,
    payload_sha256: str,
    payload_path: str,
    local_ingest_ts_utc_ns: int,
) -> FileInspection:
    contents = validate_archive(payload, config)
    result = parse_trades(
        contents.csv_bytes,
        config=config,
        source_file=payload_path,
        raw_payload_hash=payload_sha256,
        local_ingest_ts_utc_ns=local_ingest_ts_utc_ns,
    )
    result.reconciliation.check()
    identity = check_file_identity(result, config)
    return FileInspection(contents=contents, result=result, identity=identity)
