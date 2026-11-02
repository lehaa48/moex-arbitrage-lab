"""Zero-cost acquisition of one daily file and its offline QA run (P1).

``acquire`` order (each step fails closed and the network is reached only in step 5):

1. ``recover_raw_publications``; any conflict -> ``RuntimeError("BLOCKED: ...")``;
2. a committed, verifying publication of the same path and URL -> returned as already
   published; any present-but-unverifiable target -> ``RuntimeError("BLOCKED: ...")``;
3. request ledger rate-limit check;
4. create-once cost-ledger record with decision ``FREE_CONFIRMED`` (docs/COST_AND_PURCHASE_
   POLICY.md); the zero-cost evidence URL comes from the config;
5. one GET, payload validation, the optional caller check ``accept`` (e.g. parse + identity
   for the period screen; it raises to refuse), ``publish_raw`` (create-once; raw bytes
   unchanged) and ``verify_published``. A refused payload is never published.

``run_qa`` never touches the network and never writes row-level trades: it writes create-once
``reconciliation.json``, ``qa.json`` and ``run.json`` (last) into a new run directory.
"""

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from moex_arbitrage_lab import __version__
from moex_arbitrage_lab.contracts.acquisition_manifest import AcquisitionManifestV1
from moex_arbitrage_lab.contracts.trade_v1 import TRADE_SCHEMA_VERSION
from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.settings.raw_publication import (
    RawIntegrityError,
    publish_raw,
    raw_relative_path,
    recover_raw_publications,
    verify_published,
)
from moex_arbitrage_lab.settings.storage_paths import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.tbank_history.archive import validate_archive
from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig
from moex_arbitrage_lab.sources.tbank_history.fetch import (
    FetchResult,
    Opener,
    RequestLedger,
    fetch,
    ledger_path,
)
from moex_arbitrage_lab.sources.tbank_history.parser import (
    PARSER_VERSION,
    ParseResult,
    parse_digest,
    parse_trades,
)
from moex_arbitrage_lab.sources.tbank_history.qa import compute_qa

PROVIDER: Final = "tbank"
VENUE: Final = "MOEX"
PUBLISHER_VERSION: Final = f"moex-arbitrage-lab-{__version__}"
OPERATOR: Final = "claude-main-agent"
REPORT_EXPERIMENT_ID: Final = "p1-tbank-gdz6"


@dataclass(frozen=True, slots=True)
class AcquisitionOutcome:
    relative: str
    already_published: bool
    fetch: FetchResult | None
    manifest: AcquisitionManifestV1
    payload_sha256: str
    payload_bytes: int


def contract_slug(config: TbankHistoryConfig) -> str:
    """Lower-case root + month code of the ticker, without the class code
    (``GDZ6_SPBFUT`` -> ``gdz6``): the ``<contract>`` level of the raw layout."""
    return config.ticker_cc.split("_", 1)[0].lower()


def raw_relative(config: TbankHistoryConfig) -> str:
    return raw_relative_path(
        PROVIDER, config.dataset, contract_slug(config), config.archive_date, config.raw_filename
    )


def config_sha256(config: TbankHistoryConfig) -> str:
    """SHA-256 of the canonical JSON of the validated config (fixed field order)."""
    return sha256_hex(config.model_dump_json().encode("utf-8"))


def _publication_targets(paths: ProjectPaths, relative: str) -> tuple[Path, ...]:
    return (
        paths.category_dir(StorageCategory.RAW).joinpath(*relative.split("/")),
        paths.category_dir(StorageCategory.MANIFESTS).joinpath(
            "raw", *f"{relative}.manifest.json".split("/")
        ),
        paths.category_dir(StorageCategory.CHECKSUMS).joinpath(
            "raw", *f"{relative}.sha256".split("/")
        ),
        paths.category_dir(StorageCategory.MANIFESTS).joinpath(
            "raw", *f"{relative}.commit.json".split("/")
        ),
    )


def existing_publication(
    paths: ProjectPaths, relative: str, config: TbankHistoryConfig
) -> AcquisitionManifestV1 | None:
    if not any(os.path.lexists(target) for target in _publication_targets(paths, relative)):
        return None
    try:
        manifest = verify_published(paths, relative)
    except RawIntegrityError as error:
        raise RuntimeError(f"BLOCKED: raw target exists but does not verify: {error}") from None
    if manifest.request_parameters.get("url") != config.url:
        raise RuntimeError("BLOCKED: published payload was requested with a different URL")
    return manifest


def write_create_once(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _request_id(config: TbankHistoryConfig) -> str:
    return (
        f"{PROVIDER}-{contract_slug(config)}-{config.archive_date.isoformat()}-"
        f"{uuid.uuid4().hex[:16]}"
    )


def _cost_ledger_record(request_id: str, config: TbankHistoryConfig) -> dict[str, object]:
    return {
        "request_id": request_id,
        "provider": PROVIDER,
        "dataset": config.dataset,
        "schema": config.source_schema,
        "symbols": [config.ticker_cc],
        "start_utc": None,
        "end_utc": None,
        "estimated_bytes": None,
        "estimated_cost_currency": config.cost_currency,
        "estimated_cost": "0",
        "credit_checked_at": None,
        "decision": "FREE_CONFIRMED",
        "zero_cost_evidence_url": config.zero_cost_evidence_url,
        "raw_manifest_id": None,
        "actual_cost_if_known": "0",
        "operator": OPERATOR,
    }


def acquire(
    paths: ProjectPaths,
    config: TbankHistoryConfig,
    *,
    opener: Opener | None = None,
    clock: Callable[[], int] = time.time_ns,
    accept: Callable[[FetchResult], None] | None = None,
) -> AcquisitionOutcome:
    """Acquire and publish exactly one daily file (see the module docstring for the order)."""
    report = recover_raw_publications(paths)
    if not report.clean:
        raise RuntimeError(
            f"BLOCKED: raw publication recovery has {len(report.conflicts)} conflict(s); "
            "human decision required"
        )
    relative = raw_relative(config)
    existing = existing_publication(paths, relative, config)
    if existing is not None:
        return AcquisitionOutcome(
            relative=relative,
            already_published=True,
            fetch=None,
            manifest=existing,
            payload_sha256=existing.payload_sha256,
            payload_bytes=existing.payload_bytes,
        )

    ledger = RequestLedger(ledger_path(paths))
    ledger.check_allowed(clock(), config)

    request_id = _request_id(config)
    write_create_once(
        paths.resolve(StorageCategory.MANIFESTS, f"tbank/cost-ledger/{request_id}.json"),
        _cost_ledger_record(request_id, config),
    )

    result = fetch("GET", config, ledger=ledger, opener=opener, clock=clock)
    validate_archive(result.body, config)
    if accept is not None:
        accept(result)

    manifest_fields: dict[str, object] = {
        "acquisition_id": request_id,
        "provider": PROVIDER,
        "dataset": config.dataset,
        "source_schema": config.source_schema,
        "source_format_version": config.source_format_version,
        "venue": VENUE,
        "canonical_contract": config.canonical_contract,
        # Archive (file) date of the provider. Venue trading dates are assigned per row from
        # the verified session windows, never from this field.
        "trading_date": config.archive_date,
        "request_parameters": {
            "url": config.url,
            "ticker_cc": config.ticker_cc,
            "archive_date": config.archive_date.isoformat(),
        },
        "request_start_utc_ns": None,
        "request_end_utc_ns": None,
        "retrieval_started_utc_ns": result.started_utc_ns,
        "retrieval_completed_utc_ns": result.completed_utc_ns,
        "cost_ledger_request_id": request_id,
        "estimated_cost": Decimal(0),
        "estimated_cost_currency": config.cost_currency,
        "actual_cost_if_known": Decimal(0),
        "cost_decision": "FREE_CONFIRMED",
        "license_url": config.license_url,
        "license_version": config.license_version,
        "response_status": result.status,
        "response_metadata": dict(result.headers),
        "publisher_version": PUBLISHER_VERSION,
    }
    publish_raw(paths, relative, result.body, manifest_fields)
    manifest = verify_published(paths, relative)
    return AcquisitionOutcome(
        relative=relative,
        already_published=False,
        fetch=result,
        manifest=manifest,
        payload_sha256=manifest.payload_sha256,
        payload_bytes=manifest.payload_bytes,
    )


def run_qa(paths: ProjectPaths, config: TbankHistoryConfig, *, run_id: str) -> Path:
    """Offline: verify the publication, parse twice (determinism), reconcile and write QA."""
    relative = raw_relative(config)
    manifest = verify_published(paths, relative)
    payload = paths.resolve(StorageCategory.RAW, relative).read_bytes()
    if sha256_hex(payload) != manifest.payload_sha256:
        raise RuntimeError("BLOCKED: raw payload changed after verification")
    contents = validate_archive(payload, config)

    def parse() -> ParseResult:
        return parse_trades(
            contents.csv_bytes,
            config=config,
            source_file=manifest.payload_path,
            raw_payload_hash=manifest.payload_sha256,
            local_ingest_ts_utc_ns=manifest.retrieval_completed_utc_ns,
        )

    result = parse()
    digest = parse_digest(result)
    if parse_digest(parse()) != digest:
        raise RuntimeError("BLOCKED: parsing is not deterministic (digests differ)")
    result.reconciliation.check()
    qa = compute_qa(result, config)

    run_dir = paths.resolve(StorageCategory.REPORTS, f"{REPORT_EXPERIMENT_ID}/{run_id}")
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    # A run ID is used once: an existing run directory is never reused or mutated.
    run_dir.mkdir(exist_ok=False)
    write_create_once(
        paths.resolve(
            StorageCategory.REPORTS, f"{REPORT_EXPERIMENT_ID}/{run_id}/reconciliation.json"
        ),
        {
            "run_id": run_id,
            "archive": {
                "payload_format": config.payload_format,
                "payload_bytes": manifest.payload_bytes,
                "member_name": contents.member_name,
                "member_count": contents.member_count,
                "compressed_size": contents.compressed_size,
                "uncompressed_size": contents.uncompressed_size,
            },
            **result.reconciliation.to_json(),
        },
    )
    write_create_once(
        paths.resolve(StorageCategory.REPORTS, f"{REPORT_EXPERIMENT_ID}/{run_id}/qa.json"), qa
    )
    write_create_once(
        paths.resolve(StorageCategory.REPORTS, f"{REPORT_EXPERIMENT_ID}/{run_id}/run.json"),
        {
            "run_id": run_id,
            "config_sha256": config_sha256(config),
            "raw_relative": relative,
            "payload_path": manifest.payload_path,
            "payload_sha256": manifest.payload_sha256,
            "payload_bytes": manifest.payload_bytes,
            "acquisition_id": manifest.acquisition_id,
            "retrieval_completed_utc_ns": manifest.retrieval_completed_utc_ns,
            "parse_digest": digest,
            "parse_determinism_check": "two parses, identical digest",
            "member_name": contents.member_name,
            "parsed_rows": result.reconciliation.parsed_rows,
            "eligible_rows": result.reconciliation.eligible_rows,
            "code_version": __version__,
            "parser_version": PARSER_VERSION,
            "schema_version": TRADE_SCHEMA_VERSION,
            "row_level_trades_written": False,
        },
    )
    return run_dir
