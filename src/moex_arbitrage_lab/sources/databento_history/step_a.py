"""Step A: free metadata/symbology calls and the cost estimate of the frozen request only.

Calls (all in ``STEP_A_ENDPOINTS``; the client refuses anything else, so no batch/timeseries
endpoint can be reached): ``metadata.list_schemas``, ``metadata.get_dataset_range``,
``symbology.resolve``, ``metadata.get_record_count``, ``metadata.get_billable_size``,
``metadata.get_cost``. No free account/credit endpoint is known, so credit is reported as
unknown and must be stated by the user.

Decision: ``AWAITING_USER_APPROVAL`` if the schema is available, the dataset range covers the
period, the raw symbol resolves to exactly one instrument over the whole period, records are
expected, no job for this request is in the ledger and no payload of this request is cached;
otherwise ``STOP_USER_DECISION`` with reasons. The estimate itself is never a stop reason: it is
recorded exactly as returned (decimal string, USD) with the billable bytes, and the user must
approve that exact estimate - stating that free credit covers it when it is above zero, with a
maximum cash charge of 0 (pivot prompt "cost gate"; docs/COST_AND_PURCHASE_POLICY.md "zero or
safely covered credit"). ``explicit_user_approval_required`` is always ``true``
(ADR-0001/0005). Any provider error stops Step A without a report (the ledger keeps the trail).

The report is written create-once to
``reports/generated/databento/step-a/<run_id>/step-a-report.json`` and a ``cost_gate`` event
with the policy ledger fields is appended to the request ledger.
"""

import itertools
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.settings.storage_paths import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.databento_history.client import (
    HistoricalClient,
    Opener,
    read_api_key,
)
from moex_arbitrage_lab.sources.databento_history.ledger import RequestLedger, ledger_path
from moex_arbitrage_lab.sources.databento_history.request import (
    API_KEY_ENV_NAME,
    END_DATE_EXCLUSIVE,
    END_UTC_NS,
    FROZEN_REQUEST,
    PROVIDER,
    RAW_PREFIX,
    REQUEST_SHA256,
    START_DATE,
    START_UTC_NS,
    STEP_A_ENDPOINTS,
    Endpoint,
    frozen_request_document,
)
from moex_arbitrage_lab.sources.databento_history.validate import OHLCV_1S_SEMANTICS

REPORT_VERSION: Final = "databento_step_a_report_v1"
APPROVAL_VERSION: Final = "databento_step_b_approval_v1"
REPORT_FILENAME: Final = "step-a-report.json"
CREDIT_UNKNOWN: Final = "credit: unknown \u2014 user must state it"
DEFAULT_OPERATOR: Final = "claude-code"
RUN_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ISO: Final = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?:[T ](?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<frac>\d{1,9}))?(?P<tz>Z|\+00:00|\+0000)?)?$"
)


class StepAError(RuntimeError):
    """Step A could not produce a report (malformed provider response or local refusal)."""


def parse_iso_utc_ns(value: object) -> int | None:
    """``YYYY-MM-DD`` or ISO-8601 UTC with up to 9 fractional digits -> exact ns, else None."""
    if not isinstance(value, str):
        return None
    match = _ISO.fullmatch(value.strip())
    if match is None:
        return None
    try:
        day = date.fromisoformat(match["date"])
    except ValueError:
        return None
    seconds = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    if match["h"] is not None:
        hour, minute, second = int(match["h"]), int(match["m"]), int(match["s"])
        if hour > 23 or minute > 59 or second > 59:
            return None
        seconds += hour * 3600 + minute * 60 + second
    fraction = (match["frac"] or "").ljust(9, "0")
    return seconds * 1_000_000_000 + int(fraction or "0")


def _decimal_of(value: object, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise StepAError(f"{what}: provider response is not a number")
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise StepAError(f"{what}: provider response is not a finite non-negative number")
    return number


def _int_of(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StepAError(f"{what}: provider response is not a non-negative integer")
    return value


def analyze_schemas(document: object) -> bool:
    if not isinstance(document, list) or not all(isinstance(s, str) for s in document):
        raise StepAError("metadata.list_schemas: unexpected response shape")
    return FROZEN_REQUEST.record_schema in document


def analyze_dataset_range(document: object) -> dict[str, object]:
    """Overall and (if present) per-schema range; ``covers_period`` uses the most specific."""
    if not isinstance(document, dict):
        raise StepAError("metadata.get_dataset_range: unexpected response shape")
    start = document.get("start", document.get("start_date"))
    end = document.get("end", document.get("end_date"))
    schema_start = schema_end = None
    per_schema = document.get("schema")
    if isinstance(per_schema, dict):
        entry = per_schema.get(FROZEN_REQUEST.record_schema)
        if isinstance(entry, dict):
            schema_start, schema_end = entry.get("start"), entry.get("end")
    use_start = schema_start if schema_start is not None else start
    use_end = schema_end if schema_end is not None else end
    start_ns, end_ns = parse_iso_utc_ns(use_start), parse_iso_utc_ns(use_end)
    covers = (
        start_ns is not None
        and end_ns is not None
        and start_ns <= START_UTC_NS
        and end_ns >= END_UTC_NS
    )
    return {
        "start": start if isinstance(start, str) else None,
        "end": end if isinstance(end, str) else None,
        "schema_start": schema_start if isinstance(schema_start, str) else None,
        "schema_end": schema_end if isinstance(schema_end, str) else None,
        "range_source": "schema" if schema_start is not None else "dataset",
        "covers_period": covers,
    }


def analyze_symbology(document: object) -> dict[str, object]:
    """Mappings of the raw symbol; unique only if one instrument id covers the whole period
    contiguously and nothing is partial/not found."""
    if not isinstance(document, dict) or not isinstance(document.get("result"), dict):
        raise StepAError("symbology.resolve: unexpected response shape")
    result = document["result"]
    raw = result.get(FROZEN_REQUEST.symbols, [])
    if not isinstance(raw, list):
        raise StepAError("symbology.resolve: unexpected mapping shape")
    mappings: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(k), str) for k in ("d0", "d1", "s")
        ):
            raise StepAError("symbology.resolve: unexpected mapping entry")
        mappings.append({"d0": item["d0"], "d1": item["d1"], "s": item["s"]})
    mappings.sort(key=lambda m: m["d0"])
    partial = document.get("partial", [])
    not_found = document.get("not_found", [])
    ids = sorted({m["s"] for m in mappings})
    contiguous = all(a["d1"] == b["d0"] for a, b in itertools.pairwise(mappings))
    covers = (
        bool(mappings)
        and mappings[0]["d0"] <= START_DATE.isoformat()
        and mappings[-1]["d1"] >= END_DATE_EXCLUSIVE.isoformat()
    )
    unique = (
        len(ids) == 1
        and ids[0].isdigit()
        and contiguous
        and covers
        and not partial
        and not not_found
    )
    return {
        "symbol": FROZEN_REQUEST.symbols,
        "stype_in": FROZEN_REQUEST.stype_in,
        "stype_out": FROZEN_REQUEST.stype_out,
        "mappings": mappings,
        "partial": partial if isinstance(partial, list) else [str(partial)],
        "not_found": not_found if isinstance(not_found, list) else [str(not_found)],
        "instrument_ids": [int(i) for i in ids if i.isdigit()],
        "covers_period": covers,
        "contiguous": contiguous,
        "resolved_uniquely": unique,
        "definition_expiry_verified": False,
    }


def local_cache_check(paths: ProjectPaths, ledger: RequestLedger) -> dict[str, object]:
    """Offline evidence that no identical payload/job already exists (or what exists)."""
    raw_dir = paths.resolve(StorageCategory.RAW, RAW_PREFIX)
    manifest_dir = paths.resolve(StorageCategory.MANIFESTS, f"raw/{RAW_PREFIX}")
    raw_files = sorted(p.name for p in raw_dir.iterdir()) if raw_dir.is_dir() else []
    manifest_files = sorted(p.name for p in manifest_dir.iterdir()) if manifest_dir.is_dir() else []
    attempts = ledger.job_creation_attempts(REQUEST_SHA256)
    completion = ledger.completion(REQUEST_SHA256)
    return {
        "raw_dir": paths.relative_to_root(raw_dir),
        "raw_dir_exists": raw_dir.is_dir(),
        "raw_files": raw_files,
        "manifest_dir": paths.relative_to_root(manifest_dir),
        "manifest_files": manifest_files,
        "ledger_path": paths.relative_to_root(ledger.path),
        "ledger_job_creation_attempts": len(attempts),
        "ledger_step_b_complete": completion is not None,
        "identical_payload_present": bool(raw_files) or completion is not None,
    }


def _decision(facts: Mapping[str, object]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not facts["schema_available"]:
        reasons.append("schema_unavailable")
    if not facts["range_covers"]:
        reasons.append("dataset_range_does_not_cover_period")
    if not facts["symbology_unique"]:
        reasons.append("symbology_not_unique_or_incomplete")
    if facts["record_count"] == 0:
        reasons.append("no_records_expected")
    if facts["prior_job"]:
        reasons.append("batch_job_already_in_ledger")
    if facts["cached"]:
        reasons.append("identical_payload_already_cached")
    return ("AWAITING_USER_APPROVAL" if not reasons else "STOP_USER_DECISION"), reasons


def approval_template(report: Mapping[str, object]) -> dict[str, object]:
    """What the USER writes to approve Step B (agents never write it)."""
    estimate_usd = report["estimate_usd"]
    if not isinstance(estimate_usd, str):
        raise StepAError("report has no estimate")
    template: dict[str, object] = {
        "approval_version": APPROVAL_VERSION,
        "request_sha256": REQUEST_SHA256,
        "step_a_report_sha256": "<sha256 of the step-a-report.json file, printed by step-a>",
        "approved_estimate_usd": estimate_usd,
        "approved_billable_size": report["billable_size_bytes"],
        "max_cash_charge_usd": "0",
        "approved_by": "<user identifier>",
        "approved_at_utc": "<YYYY-MM-DDTHH:MM:SSZ>",
        "explicit_user_approval": True,
    }
    if Decimal(estimate_usd) > 0:
        # Required above zero: only the user can state that free credit covers the estimate.
        template["user_states_credit_covers_estimate"] = True
    return template


@dataclass(frozen=True, slots=True)
class StepAOutcome:
    report: dict[str, object]
    report_path: Path
    report_sha256: str


def _write_create_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.chmod(0o444)


def report_relative(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise StepAError("run id must match [a-z0-9][a-z0-9-]{0,63}")
    return f"databento/step-a/{run_id}/{REPORT_FILENAME}"


def run_step_a(
    paths: ProjectPaths,
    *,
    opener: Opener | None = None,
    env: Mapping[str, str] | None = None,
    clock: Callable[[], int] = time.time_ns,
    run_id: str | None = None,
    operator: str = DEFAULT_OPERATOR,
) -> StepAOutcome:
    environment: Mapping[str, str] = os.environ if env is None else env
    read_api_key(environment)  # presence only, before anything is written or sent
    started = clock()
    run = run_id if run_id is not None else f"run-{started}"
    report_path = paths.resolve(StorageCategory.REPORTS, report_relative(run))
    if report_path.exists():
        raise StepAError(f"report already exists (create-once): {report_path.name}")
    ledger = RequestLedger(ledger_path(paths))
    cache = local_cache_check(paths, ledger)
    client = HistoricalClient(step="A", ledger=ledger, opener=opener, env=environment, clock=clock)

    schema_available = analyze_schemas(client.call_json(Endpoint.LIST_SCHEMAS))
    dataset_range = analyze_dataset_range(client.call_json(Endpoint.GET_DATASET_RANGE))
    symbology = analyze_symbology(client.call_json(Endpoint.RESOLVE_SYMBOLOGY))
    record_count = _int_of(client.call_json(Endpoint.GET_RECORD_COUNT), "get_record_count")
    billable = _int_of(client.call_json(Endpoint.GET_BILLABLE_SIZE), "get_billable_size")
    cost = _decimal_of(client.call_json(Endpoint.GET_COST), "get_cost")
    cost_request_id = client.last_request_id
    completed = clock()

    decision, reasons = _decision(
        {
            "estimate_cost": cost,
            "schema_available": schema_available,
            "range_covers": dataset_range["covers_period"],
            "symbology_unique": symbology["resolved_uniquely"],
            "record_count": record_count,
            "prior_job": cache["ledger_job_creation_attempts"] != 0,
            "cached": cache["identical_payload_present"],
        }
    )
    report: dict[str, object] = {
        "report_version": REPORT_VERSION,
        "run_id": run,
        "step": "A",
        "network": True,
        "started_utc_ns": started,
        "completed_utc_ns": completed,
        "provider": PROVIDER,
        "request": frozen_request_document(),
        "request_sha256": REQUEST_SHA256,
        "endpoints_allowed": sorted(e.value for e in STEP_A_ENDPOINTS),
        "endpoints_called": [e.value for e in client.sent],
        "credential_source": {"env_name": API_KEY_ENV_NAME, "value": "<redacted>"},
        "schema_available": schema_available,
        "dataset_range": dataset_range,
        "symbology": symbology,
        "record_count": record_count,
        "billable_size_bytes": billable,
        "estimate": {
            "currency": "USD",
            "cost_usd": str(cost),
            "is_exactly_zero": cost == 0,
            "ledger_request_id": cost_request_id,
        },
        # Exactly as returned by metadata.get_cost (decimal string; no binary float).
        "estimate_usd": str(cost),
        "credit": {"status": "unknown", "statement": CREDIT_UNKNOWN, "facts": None},
        "zero_cash_charge_basis": (
            "estimate exactly 0 USD from metadata.get_cost"
            if cost == 0
            else "pending: the user must state that free credit covers the exact estimate "
            "(user_states_credit_covers_estimate=true) with max_cash_charge_usd=0"
        ),
        "max_cash_charge_usd": "0",
        "cache_check": cache,
        "decision": decision,
        "stop_reasons": reasons,
        "explicit_user_approval_required": True,
        "limitations": [OHLCV_1S_SEMANTICS],
        "operator": operator,
    }
    report["approval_template"] = approval_template(report)
    data = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
    _write_create_once(report_path, data)
    digest = sha256_hex(data)
    ledger.append(
        {
            "event": "cost_gate",
            "step": "A",
            "request_id": cost_request_id,
            "request_sha256": REQUEST_SHA256,
            "provider": PROVIDER,
            "dataset": FROZEN_REQUEST.dataset,
            "schema": FROZEN_REQUEST.record_schema,
            "symbols": FROZEN_REQUEST.symbols,
            "start_utc": FROZEN_REQUEST.start,
            "end_utc": FROZEN_REQUEST.end,
            "estimated_bytes": billable,
            "estimated_cost_currency": "USD",
            "estimated_cost": str(cost),
            "credit_checked_at": None,
            "decision": decision,
            "raw_manifest_id": None,
            "actual_cost_if_known": None,
            "operator": operator,
            "step_a_report": paths.relative_to_root(report_path),
            "step_a_report_sha256": digest,
            "utc_ns": completed,
        }
    )
    return StepAOutcome(report=report, report_path=report_path, report_sha256=digest)
