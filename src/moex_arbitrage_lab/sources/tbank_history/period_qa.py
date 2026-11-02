"""Offline per-file and period QA, and the downstream loader, from VERIFIED publications only.

Nothing here touches the network. Every file is read only after ``verify_published`` (manifest,
checksum and commit marker agree), its bytes are re-hashed, then inspected with the same rules
as acquisition (``inspection.inspect_payload``): container, header, ``trade_v1`` parsing with
full raw timestamp precision, reconciliation and file identity; the period identity requires one
UID across all files. Primary (eligible) rows are ``TRADE_SOURCE == EXCHANGE`` only; raw bytes
are never modified.

``qa_period`` is deterministic for the same publications (two parses per file, digest compared).
``load_period_trades`` returns, per published date, the eligible ``TradeV1`` rows and the
continuous session windows (the opening auction is never continuous).
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Final

from moex_arbitrage_lab import __version__
from moex_arbitrage_lab.contracts.acquisition_manifest import AcquisitionManifestV1
from moex_arbitrage_lab.contracts.trade_v1 import TradeV1
from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.settings.raw_publication import verify_published
from moex_arbitrage_lab.settings.storage_paths import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.tbank_history.acquire import (
    config_sha256,
    existing_publication,
    raw_relative,
)
from moex_arbitrage_lab.sources.tbank_history.config import SessionWindow, TbankHistoryConfig
from moex_arbitrage_lab.sources.tbank_history.identity import UID_STATUS, check_period_identity
from moex_arbitrage_lab.sources.tbank_history.inspection import FileInspection, inspect_payload
from moex_arbitrage_lab.sources.tbank_history.parser import (
    ELIGIBLE_TRADE_SOURCE,
    PARSER_VERSION,
    parse_digest,
)
from moex_arbitrage_lab.sources.tbank_history.period import (
    LoadedTemplate,
    RequestPlan,
    build_day_config,
)
from moex_arbitrage_lab.sources.tbank_history.period_acquire import PeriodReport
from moex_arbitrage_lab.sources.tbank_history.qa import QA_VERSION, compute_qa

PERIOD_QA_VERSION: Final = "tbank_period_qa_v1"
_LOADABLE: Final = frozenset({"published", "reused_verified"})


@dataclass(frozen=True, slots=True)
class VerifiedFile:
    config: TbankHistoryConfig
    relative: str
    manifest: AcquisitionManifestV1
    payload: bytes
    inspection: FileInspection


def read_verified(paths: ProjectPaths, config: TbankHistoryConfig) -> VerifiedFile | None:
    """Inspect the committed, verifying publication of ``config`` (``None`` if nothing exists;
    ``RuntimeError("BLOCKED...")`` if something exists but does not verify)."""
    relative = raw_relative(config)
    manifest = existing_publication(paths, relative, config)
    if manifest is None:
        return None
    payload = paths.resolve(StorageCategory.RAW, relative).read_bytes()
    if sha256_hex(payload) != manifest.payload_sha256:
        raise RuntimeError("BLOCKED: raw payload changed after verification")
    inspection = inspect_payload(
        payload,
        config,
        payload_sha256=manifest.payload_sha256,
        payload_path=manifest.payload_path,
        local_ingest_ts_utc_ns=manifest.retrieval_completed_utc_ns,
    )
    return VerifiedFile(config, relative, manifest, payload, inspection)


def file_qa(verified: VerifiedFile) -> dict[str, object]:
    """Per-file QA document (JSON-serializable, deterministic)."""
    config = verified.config
    manifest = verified.manifest
    inspection = verified.inspection
    reconciliation = inspection.result.reconciliation
    digest = parse_digest(inspection.result)
    again = parse_digest(
        inspect_payload(
            verified.payload,
            config,
            payload_sha256=manifest.payload_sha256,
            payload_path=manifest.payload_path,
            local_ingest_ts_utc_ns=manifest.retrieval_completed_utc_ns,
        ).result
    )
    if again != digest:
        raise RuntimeError("BLOCKED: parsing is not deterministic (digests differ)")
    exclusions = {
        **{f"parse:{k}": v for k, v in reconciliation.parse_failures.items()},
        **{f"ineligible:{k}": v for k, v in reconciliation.ineligible.items()},
    }
    return {
        "trading_date": config.archive_date.isoformat(),
        "raw_relative": verified.relative,
        "payload_path": manifest.payload_path,
        "payload_bytes": manifest.payload_bytes,
        "payload_sha256": manifest.payload_sha256,
        "request_manifest": {
            "acquisition_id": manifest.acquisition_id,
            "cost_ledger_request_id": manifest.cost_ledger_request_id,
            "url": manifest.request_parameters.get("url"),
            "response_status": manifest.response_status,
            "response_metadata": dict(manifest.response_metadata),
            "retrieval_started_utc_ns": manifest.retrieval_started_utc_ns,
            "retrieval_completed_utc_ns": manifest.retrieval_completed_utc_ns,
            "cost_decision": manifest.cost_decision,
            "actual_cost_if_known": str(manifest.actual_cost_if_known),
        },
        "config_sha256": config_sha256(config),
        "archive": {
            "payload_format": config.payload_format,
            "member_name": inspection.contents.member_name,
            "compressed_size": inspection.contents.compressed_size,
            "uncompressed_size": inspection.contents.uncompressed_size,
        },
        "identity": inspection.identity.to_json(),
        "counts": {
            "raw_csv_lines_including_header": reconciliation.csv_data_rows + 1,
            "csv_data_rows": reconciliation.csv_data_rows,
            "target_rows": reconciliation.target_rows,
            "parsed_rows": reconciliation.parsed_rows,
            "eligible_rows": reconciliation.eligible_rows,
        },
        "exclusions_by_reason": exclusions,
        "reconciliation": reconciliation.to_json(),
        "parse_digest": digest,
        "parse_determinism_check": "two parses, identical digest",
        "qa": compute_qa(inspection.result, config),
    }


def qa_period(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    acquisition: PeriodReport | None = None,
) -> dict[str, object]:
    """Offline QA over every planned date (chronological), from verified publications only."""
    known = {o.trading_date: o for o in acquisition.outcomes} if acquisition is not None else {}
    files: list[dict[str, object]] = []
    uids: list[str] = []
    totals: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    trade_sources: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    digits: Counter[str] = Counter()
    ties: Counter[str] = Counter()
    sessions_without_events: list[str] = []
    for day in sorted(plan.requested):
        config = build_day_config(loaded, day)
        verified = read_verified(paths, config)
        if verified is None:
            previous = known.get(day)
            files.append(
                {
                    "trading_date": day.isoformat(),
                    "status": "missing_file"
                    if previous is not None and previous.outcome == "missing_file"
                    else "not_published",
                    "raw_relative": raw_relative(config),
                }
            )
            continue
        document = {"status": "verified", **file_qa(verified)}
        files.append(document)
        uids.append(verified.inspection.identity.instrument_uid)
        check_period_identity(uids)
        reconciliation = verified.inspection.result.reconciliation
        totals.update(
            {
                "csv_data_rows": reconciliation.csv_data_rows,
                "target_rows": reconciliation.target_rows,
                "parsed_rows": reconciliation.parsed_rows,
                "eligible_rows": reconciliation.eligible_rows,
                "payload_bytes": verified.manifest.payload_bytes,
            }
        )
        exclusions.update({f"parse:{k}": v for k, v in reconciliation.parse_failures.items()})
        exclusions.update({f"ineligible:{k}": v for k, v in reconciliation.ineligible.items()})
        composition = verified.inspection.result.composition
        trade_sources.update(composition["TRADE_SOURCE"])
        directions.update(composition["DIRECTION"])
        digits.update({str(k): v for k, v in verified.inspection.result.fractional_digits.items()})
        qa = _mapping(document["qa"])
        file_ties = _mapping(qa["ties"])
        ties.update(
            {
                "tied_rows": _int(file_ties["tied_rows"]),
                "tie_groups": _int(file_ties["tie_groups"]),
                "potential_duplicates": _int(qa["potential_duplicates"]),
                "non_monotonic_in_file": _int(qa["non_monotonic_in_file"]),
            }
        )
        sessions = qa["sessions"]
        if not isinstance(sessions, list):  # pragma: no cover - compute_qa returns a list
            raise TypeError("QA sessions is not a JSON array")
        for entry in sessions:
            window = _mapping(entry)
            if window["n_events"] == 0:
                sessions_without_events.append(f"{day.isoformat()}:{window['name']}")
    verified_dates = [f["trading_date"] for f in files if f["status"] == "verified"]
    max_digits = max((int(k) for k in digits), default=None)
    return {
        "period_qa_version": PERIOD_QA_VERSION,
        "qa_version": QA_VERSION,
        "parser_version": PARSER_VERSION,
        "code_version": __version__,
        "template_id": loaded.template.template_id,
        "template_sha256": loaded.template_sha256,
        "calendar_id": loaded.calendar.calendar_id,
        "calendar_sha256": loaded.calendar_sha256,
        "period_start": plan.period_start.isoformat(),
        "period_end_exclusive": plan.period_end_exclusive.isoformat(),
        "eligibility_rule": f"TRADE_SOURCE == {ELIGIBLE_TRADE_SOURCE!r}",
        "instrument_uid": check_period_identity(uids),
        "uid_status": UID_STATUS,
        "dates": {
            "requested": len(plan.requested),
            "verified": len(verified_dates),
            "missing_file": [f["trading_date"] for f in files if f["status"] == "missing_file"],
            "not_published": [f["trading_date"] for f in files if f["status"] == "not_published"],
            "not_requested": [
                {"date": e.day.isoformat(), "reason": e.reason, "note": e.note}
                for e in plan.not_requested
            ],
        },
        "totals": dict(sorted(totals.items())),
        "exclusions_by_reason": dict(sorted(exclusions.items())),
        "composition": {
            "TRADE_SOURCE": dict(sorted(trade_sources.items())),
            "DIRECTION": dict(sorted(directions.items())),
            "scope": "target-instrument rows, raw values; DIRECTION semantics unknown",
        },
        "timestamp": {
            "fractional_digits": dict(sorted(digits.items(), key=lambda kv: int(kv[0]))),
            "max_fractional_digits": max_digits,
            "representation_quantum_ns": None if max_digits is None else 10 ** (9 - max_digits),
            **dict(sorted(ties.items())),
        },
        "sessions_without_eligible_events": sessions_without_events,
        "files": files,
    }


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):  # pragma: no cover - compute_qa always returns dicts
        raise TypeError("QA section is not a JSON object")
    return value


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):  # pragma: no cover - typed QA
        raise TypeError("QA count is not an integer")
    return value


@dataclass(frozen=True, slots=True)
class DayTrades:
    """Eligible trades of one verified daily file and its session windows."""

    trading_date: date
    eligible: tuple[TradeV1, ...]
    windows: tuple[SessionWindow, ...]
    continuous_windows: tuple[SessionWindow, ...]
    payload_sha256: str
    raw_relative: str
    instrument_uid: str

    def continuous_eligible(self) -> tuple[TradeV1, ...]:
        """Eligible trades inside a continuous window (auction trades excluded)."""
        names = {window.name for window in self.continuous_windows}
        return tuple(trade for trade in self.eligible if trade.session_id in names)


def load_period_trades(
    paths: ProjectPaths, report: PeriodReport, loaded: LoadedTemplate
) -> tuple[DayTrades, ...]:
    """Per acquired date (chronological): eligible trades + continuous windows, read only from
    verified publications whose SHA-256 equals the report's; never touches the network."""
    if (report.template_sha256, report.calendar_sha256) != (
        loaded.template_sha256,
        loaded.calendar_sha256,
    ):
        raise RuntimeError("BLOCKED: report was produced with a different template/calendar")
    days: list[DayTrades] = []
    uids: list[str] = []
    for outcome in sorted(report.outcomes, key=lambda o: o.trading_date):
        if outcome.outcome not in _LOADABLE:
            continue
        config = build_day_config(loaded, outcome.trading_date)
        verify_published(paths, raw_relative(config))
        verified = read_verified(paths, config)
        if verified is None or verified.manifest.payload_sha256 != outcome.payload_sha256:
            raise RuntimeError("BLOCKED: publication differs from the period report")
        uid = verified.inspection.identity.instrument_uid
        uids.append(uid)
        check_period_identity(uids)
        days.append(
            DayTrades(
                trading_date=outcome.trading_date,
                eligible=verified.inspection.result.eligible,
                windows=config.sessions,
                continuous_windows=tuple(w for w in config.sessions if w.continuous),
                payload_sha256=verified.manifest.payload_sha256,
                raw_relative=verified.relative,
                instrument_uid=uid,
            )
        )
    return tuple(days)
