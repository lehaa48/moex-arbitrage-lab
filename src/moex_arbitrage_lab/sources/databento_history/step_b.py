"""Step B: one approved batch job of the frozen request, verified create-once raw publication.

Order (every step fails closed; any error stops the run, nothing is retried automatically):

1. the user approval JSON and the Step A report are loaded offline; the approval must name the
   frozen request hash and the SHA-256 of that exact report file, the report must be
   ``AWAITING_USER_APPROVAL``, ``approved_estimate_usd`` must equal the report's estimate
   string exactly and ``approved_billable_size`` its billable bytes, ``max_cash_charge_usd``
   must be ``"0"`` and, when the estimate is above zero, ``user_states_credit_covers_estimate``
   must be ``true`` (pivot prompt cost gate; docs/COST_AND_PURCHASE_POLICY.md "zero or safely
   covered credit"; a non-zero cash charge is never approved);
2. ``recover_raw_publications``; any conflict stops;
3. idempotent re-run: a ``step_b_complete`` ledger event whose publications all verify is
   returned without any network access (no key needed);
4. the key must be present;
5. job: if the ledger already holds a job creation attempt for this request, its recorded job id
   is reused (an attempt without a recorded job id stops: outcome unknown, the user checks the
   provider portal). Otherwise the free ``metadata.get_cost`` is re-checked (must equal the
   approved estimate exactly) and exactly one ``batch.submit_job`` is sent; a provider-reported
   job ``cost_usd`` above the approved estimate (at creation or when ``done``) is recorded in
   the ledger and stops before any download;
6. ``batch.list_jobs`` is polled with a bounded schedule (injectable sleep) until ``done``;
   ``expired``/timeout stop (a re-run resumes polling of the same job, never a new job);
7. ``batch.list_files``; every entry is checked (file name grammar, suffix, size caps, hash form,
   download URL host/path) before anything is downloaded;
8. each not-yet-published file is downloaded once (a previous ledger download attempt stops
   unless ``allow_redownload`` is set; Databento documents repeat downloads of a batch job
   without a new data charge, but that remains an explicit decision), its size and provider
   SHA-256 are checked and its content validated; only when every file passed are they
   published create-once with ``publish_raw`` and re-verified with ``verify_published``;
9. ``step_b_complete`` and ``cost_gate`` ledger events are appended.
"""

import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from moex_arbitrage_lab import __version__
from moex_arbitrage_lab.contracts.types import (
    Identifier,
    NonNegativeInt64,
    Sha256Hex,
    sha256_hex,
)
from moex_arbitrage_lab.settings.raw_publication import (
    RawIntegrityError,
    publish_raw,
    recover_raw_publications,
    verify_published,
)
from moex_arbitrage_lab.settings.storage_paths import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.databento_history.client import (
    HistoricalClient,
    Opener,
    check_download_url,
    read_api_key,
)
from moex_arbitrage_lab.sources.databento_history.ledger import RequestLedger, ledger_path
from moex_arbitrage_lab.sources.databento_history.request import (
    CANONICAL_CONTRACT,
    END_UTC_NS,
    FROZEN_REQUEST,
    JOB_ID_PATTERN,
    LICENSE_URL,
    LICENSE_VERSION,
    MANIFEST_TRADING_DATE,
    PROVIDER,
    RAW_PREFIX,
    REQUEST_SHA256,
    START_UTC_NS,
    VENUE,
    Endpoint,
    frozen_request_document,
    manifest_request_parameters,
)
from moex_arbitrage_lab.sources.databento_history.step_a import (
    REPORT_VERSION,
    parse_iso_utc_ns,
)
from moex_arbitrage_lab.sources.databento_history.validate import (
    CSV_SUFFIX,
    FileValidation,
    PayloadRejectedError,
    check_provider_filename,
    validate_file,
)

PUBLISHER_VERSION: Final = f"moex-arbitrage-lab-{__version__}-databento-history-1"
MAX_FILE_BYTES: Final = 1 << 30
MAX_TOTAL_BYTES: Final = 2 << 30
MAX_APPROVAL_BYTES: Final = 64 * 1024
MAX_REPORT_BYTES: Final = 1 << 20
DEFAULT_POLL_DELAYS_S: Final = (10.0, 20.0, 40.0, 80.0) + (120.0,) * 60
TERMINAL_OK: Final = "done"
TERMINAL_FAILED: Final = frozenset({"expired"})
_HASH: Final = re.compile(r"^(?:sha256:)?(?P<hex>[0-9a-f]{64})$")

STEP_B_STOP_REASONS: Final = (
    "approval_invalid",
    "approval_report_mismatch",
    "request_hash_mismatch",
    "step_a_not_awaiting_approval",
    "credit_statement_missing",
    "estimate_mismatch",
    "estimate_changed",
    "recovery_not_clean",
    "job_creation_outcome_unknown",
    "job_cost_exceeds_approval",
    "job_parameters_mismatch",
    "job_expired",
    "poll_timeout",
    "job_listing_invalid",
    "files_listing_invalid",
    "provider_hash_unrecognized",
    "download_already_attempted",
    "hash_mismatch",
    "payload_rejected",
    "publication_unverifiable",
    "completion_unverifiable",
)


class StepBRefusedError(RuntimeError):
    """Step B stopped; ``reason`` is one of ``STEP_B_STOP_REASONS``."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in STEP_B_STOP_REASONS:
            raise ValueError(f"unknown step B stop reason: {reason!r}")
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


# Non-negative decimal as printed by ``str(Decimal)`` (exponent form included).
DecimalText = Annotated[
    str, StringConstraints(pattern=r"^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$", max_length=40)
]
UtcSecondText = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]


class StepBApprovalV1(BaseModel):
    """Written by the user (never by an agent) after reading the Step A report."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    approval_version: Literal["databento_step_b_approval_v1"]
    request_sha256: Sha256Hex
    step_a_report_sha256: Sha256Hex
    # Must equal the Step A report's ``estimate_usd`` string exactly.
    approved_estimate_usd: DecimalText
    approved_billable_size: NonNegativeInt64
    # Required (true) when the estimate is above zero; only the user can state it.
    user_states_credit_covers_estimate: bool | None = None
    max_cash_charge_usd: Literal["0"]
    approved_by: Identifier
    approved_at_utc: UtcSecondText
    explicit_user_approval: Literal[True]


@dataclass(frozen=True, slots=True)
class ApprovedPlan:
    approval: StepBApprovalV1
    approval_sha256: str
    report: dict[str, object]
    report_sha256: str
    estimate: Decimal
    instrument_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class PublishedFile:
    relative: str
    payload_path: str
    payload_bytes: int
    payload_sha256: str
    already_published: bool


@dataclass(frozen=True, slots=True)
class StepBOutcome:
    job_id: str
    job_created_now: bool
    network_used: bool
    files: tuple[PublishedFile, ...]
    actual_cost_usd: str | None
    endpoints_called: tuple[str, ...] = field(default=())


# --- approval -----------------------------------------------------------------------------------


def _read_bounded_file(path: Path, limit: int, what: str) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        raise StepBRefusedError("approval_invalid", f"cannot read the {what} file") from None
    if len(data) > limit:
        raise StepBRefusedError("approval_invalid", f"{what} file is too large")
    return data


def _exact_decimal(text: object) -> Decimal | None:
    if not isinstance(text, str):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def load_approved_plan(approval_path: Path, report_path: Path) -> ApprovedPlan:
    """Offline approval check; raises ``StepBRefusedError`` on any mismatch."""
    approval_bytes = _read_bounded_file(approval_path, MAX_APPROVAL_BYTES, "approval")
    try:
        approval = StepBApprovalV1.model_validate_json(approval_bytes)
    except ValidationError as error:
        raise StepBRefusedError(
            "approval_invalid", f"{error.error_count()} validation error(s)"
        ) from None
    report_bytes = _read_bounded_file(report_path, MAX_REPORT_BYTES, "Step A report")
    report_sha256 = sha256_hex(report_bytes)
    if approval.step_a_report_sha256 != report_sha256:
        raise StepBRefusedError("approval_report_mismatch", "approval names another report")
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise StepBRefusedError("approval_report_mismatch", "report is not JSON") from None
    if not isinstance(report, dict) or report.get("report_version") != REPORT_VERSION:
        raise StepBRefusedError("approval_report_mismatch", "not a Step A report")
    if (
        approval.request_sha256 != REQUEST_SHA256
        or report.get("request_sha256") != REQUEST_SHA256
        or report.get("request") != frozen_request_document()
    ):
        raise StepBRefusedError("request_hash_mismatch", "approval/report is for another request")
    if report.get("decision") != "AWAITING_USER_APPROVAL":
        raise StepBRefusedError("step_a_not_awaiting_approval", str(report.get("decision")))
    report_estimate = report.get("estimate_usd")
    estimate = _exact_decimal(report_estimate)
    if (
        estimate is None
        or estimate < 0
        or not isinstance(report_estimate, str)
        or approval.approved_estimate_usd != report_estimate
    ):
        raise StepBRefusedError("estimate_mismatch", "approved estimate differs from Step A")
    billable = report.get("billable_size_bytes")
    if isinstance(billable, bool) or billable != approval.approved_billable_size:
        raise StepBRefusedError("estimate_mismatch", "approved billable size differs")
    if estimate > 0 and approval.user_states_credit_covers_estimate is not True:
        raise StepBRefusedError(
            "credit_statement_missing",
            "estimate is above zero: the user must state user_states_credit_covers_estimate=true",
        )
    symbology = report.get("symbology")
    ids = symbology.get("instrument_ids") if isinstance(symbology, dict) else None
    if (
        not isinstance(symbology, dict)
        or symbology.get("resolved_uniquely") is not True
        or not isinstance(ids, list)
        or len(ids) != 1
        or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
    ):
        raise StepBRefusedError("step_a_not_awaiting_approval", "symbology is not unique")
    return ApprovedPlan(
        approval=approval,
        approval_sha256=sha256_hex(approval_bytes),
        report=report,
        report_sha256=report_sha256,
        estimate=estimate,
        instrument_ids=frozenset(ids),
    )


# --- local publication state ---------------------------------------------------------------------


def raw_relative_for(filename: str) -> str:
    return f"{RAW_PREFIX}/{check_provider_filename(filename)}"


def _publication_state(paths: ProjectPaths, relative: str) -> Literal["absent", "published"]:
    payload = paths.resolve(StorageCategory.RAW, relative)
    marker = paths.resolve(StorageCategory.MANIFESTS, f"raw/{relative}.commit.json")
    manifest = paths.resolve(StorageCategory.MANIFESTS, f"raw/{relative}.manifest.json")
    checksum = paths.resolve(StorageCategory.CHECKSUMS, f"raw/{relative}.sha256")
    present = [os.path.lexists(p) for p in (payload, manifest, checksum, marker)]
    if not any(present):
        return "absent"
    try:
        verify_published(paths, relative)
    except RawIntegrityError:
        raise StepBRefusedError("publication_unverifiable", relative) from None
    return "published"


def _verify_completion(paths: ProjectPaths, completion: Mapping[str, object]) -> StepBOutcome:
    files = completion.get("files")
    job_id = completion.get("job_id")
    if not isinstance(files, list) or not files or not isinstance(job_id, str):
        raise StepBRefusedError("completion_unverifiable", "malformed completion event")
    published: list[PublishedFile] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("relative"), str):
            raise StepBRefusedError("completion_unverifiable", "malformed completion entry")
        relative = item["relative"]
        try:
            manifest = verify_published(paths, relative)
        except RawIntegrityError:
            raise StepBRefusedError("completion_unverifiable", relative) from None
        if manifest.payload_sha256 != item.get("sha256"):
            raise StepBRefusedError("completion_unverifiable", f"{relative}: checksum differs")
        published.append(
            PublishedFile(
                relative=relative,
                payload_path=manifest.payload_path,
                payload_bytes=manifest.payload_bytes,
                payload_sha256=manifest.payload_sha256,
                already_published=True,
            )
        )
    cost = completion.get("actual_cost_usd")
    return StepBOutcome(
        job_id=job_id,
        job_created_now=False,
        network_used=False,
        files=tuple(published),
        actual_cost_usd=cost if isinstance(cost, str) else None,
    )


# --- provider documents ---------------------------------------------------------------------------


def _check_job_cost(
    job: Mapping[str, object],
    approved: Decimal,
    *,
    ledger: RequestLedger,
    job_id: str,
    stage: str,
    clock: Callable[[], int],
) -> str | None:
    """Provider-reported job cost (if any); above the approved estimate (or not a number) it is
    recorded in the ledger and stops before any download."""
    if "cost_usd" not in job or job["cost_usd"] is None:
        return None
    value = job["cost_usd"]
    if (
        not isinstance(value, bool)
        and isinstance(value, int | Decimal)
        and Decimal(value) <= approved
    ):
        return str(value)
    ledger.append(
        {
            "event": "job_cost_exceeds_approval",
            "request_sha256": REQUEST_SHA256,
            "job_id": job_id,
            "stage": stage,
            "job_cost_usd": str(value),
            "approved_estimate_usd": str(approved),
            "utc_ns": clock(),
        }
    )
    raise StepBRefusedError(
        "job_cost_exceeds_approval",
        f"provider reports job cost {value} USD, approved {approved} USD",
    )


def _check_job_echo(job: Mapping[str, object]) -> None:
    """Fields the provider echoes for the job must equal the frozen request (if present)."""
    expected: dict[str, object] = {
        "dataset": FROZEN_REQUEST.dataset,
        "schema": FROZEN_REQUEST.record_schema,
        "stype_in": FROZEN_REQUEST.stype_in,
        "encoding": FROZEN_REQUEST.encoding,
        "compression": FROZEN_REQUEST.compression,
        "split_duration": FROZEN_REQUEST.split_duration,
    }
    for key, value in expected.items():
        if key in job and job[key] is not None and job[key] != value:
            raise StepBRefusedError("job_parameters_mismatch", f"{key} differs")
    symbols = job.get("symbols")
    if symbols is not None and symbols not in (FROZEN_REQUEST.symbols, [FROZEN_REQUEST.symbols]):
        raise StepBRefusedError("job_parameters_mismatch", "symbols differ")
    for key, bound in (("start", START_UTC_NS), ("end", END_UTC_NS)):
        if job.get(key) is not None and parse_iso_utc_ns(job[key]) != bound:
            raise StepBRefusedError("job_parameters_mismatch", f"{key} differs")


@dataclass(frozen=True, slots=True)
class ListedFile:
    filename: str
    size: int
    sha256: str | None
    url: str


def parse_file_listing(document: object, job_id: str) -> list[ListedFile]:
    """Every entry is checked (name, suffix, size, hash form, download URL) before any download."""
    if not isinstance(document, list) or not document:
        raise StepBRefusedError("files_listing_invalid", "empty or not a list")
    listed: list[ListedFile] = []
    for item in document:
        if not isinstance(item, dict):
            raise StepBRefusedError("files_listing_invalid", "entry is not an object")
        filename, size, urls = item.get("filename"), item.get("size"), item.get("urls")
        if not isinstance(filename, str) or isinstance(size, bool) or not isinstance(size, int):
            raise StepBRefusedError("files_listing_invalid", "filename/size missing")
        try:
            check_provider_filename(filename)
        except PayloadRejectedError as error:
            raise StepBRefusedError("files_listing_invalid", error.reason) from None
        if not 0 < size <= MAX_FILE_BYTES:
            raise StepBRefusedError("files_listing_invalid", f"{filename}: size out of bounds")
        url = urls.get("https") if isinstance(urls, dict) else None
        if not isinstance(url, str):
            raise StepBRefusedError("files_listing_invalid", f"{filename}: no https URL")
        check_download_url(url, job_id, filename)
        raw_hash = item.get("hash")
        digest: str | None = None
        if raw_hash is not None:
            match = _HASH.fullmatch(raw_hash) if isinstance(raw_hash, str) else None
            if match is None:
                raise StepBRefusedError("provider_hash_unrecognized", filename)
            digest = match["hex"]
        listed.append(ListedFile(filename, size, digest, url))
    names = [f.filename for f in listed]
    if len(set(names)) != len(names):
        raise StepBRefusedError("files_listing_invalid", "duplicate file names")
    if not any(name.endswith(CSV_SUFFIX) for name in names):
        raise StepBRefusedError("files_listing_invalid", "no CSV data file")
    if sum(f.size for f in listed) > MAX_TOTAL_BYTES:
        raise StepBRefusedError("files_listing_invalid", "total size exceeds the cap")
    return sorted(listed, key=lambda f: f.filename)


# --- job lifecycle -----------------------------------------------------------------------------


def _recorded_job(ledger: RequestLedger) -> tuple[str, str, str | None] | None:
    """(job_id, creation request_id, cost) of an earlier creation, or None if never attempted."""
    attempts = ledger.job_creation_attempts(REQUEST_SHA256)
    if not attempts:
        return None
    if len(attempts) != 1:
        raise StepBRefusedError("job_creation_outcome_unknown", "more than one attempt recorded")
    request_id = attempts[0].get("request_id")
    for entry in ledger.entries():
        if (
            entry.get("event") == "batch_job_created"
            and entry.get("request_id") == request_id
            and isinstance(entry.get("job_id"), str)
        ):
            cost = entry.get("cost_usd")
            return str(entry["job_id"]), str(request_id), cost if isinstance(cost, str) else None
    raise StepBRefusedError(
        "job_creation_outcome_unknown",
        "a job creation was sent earlier without a recorded job id; check the provider portal",
    )


def _create_job(
    client: HistoricalClient, ledger: RequestLedger, plan: ApprovedPlan, clock: Callable[[], int]
) -> tuple[str, str, str | None]:
    current = client.call_json(Endpoint.GET_COST)
    if (
        isinstance(current, bool)
        or not isinstance(current, int | Decimal)
        or Decimal(current) != plan.estimate
    ):
        raise StepBRefusedError("estimate_changed", "metadata.get_cost differs from approval")
    document = client.call_json(Endpoint.CREATE_BATCH_JOB)
    request_id = client.last_request_id
    if request_id is None:  # pragma: no cover - set by every sent request
        raise StepBRefusedError("job_creation_outcome_unknown", "no request id")
    if not isinstance(document, dict) or not isinstance(document.get("id"), str):
        raise StepBRefusedError("job_creation_outcome_unknown", "response has no job id")
    job_id = str(document["id"])
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise StepBRefusedError("job_creation_outcome_unknown", "job id has an unexpected form")
    state = document.get("state")
    raw_cost = document.get("cost_usd")
    ledger.append(
        {
            "event": "batch_job_created",
            "request_id": request_id,
            "request_sha256": REQUEST_SHA256,
            "job_id": job_id,
            "state": state if isinstance(state, str) else None,
            "cost_usd": str(raw_cost) if raw_cost is not None else None,
            "utc_ns": clock(),
        }
    )
    cost = _check_job_cost(
        document, plan.estimate, ledger=ledger, job_id=job_id, stage="created", clock=clock
    )
    _check_job_echo(document)
    return job_id, request_id, cost


def _poll_until_done(
    client: HistoricalClient,
    job_id: str,
    delays: Sequence[float],
    sleep: Callable[[float], None],
) -> dict[str, object]:
    for attempt in range(len(delays) + 1):
        document = client.call_json(Endpoint.LIST_BATCH_JOBS)
        if not isinstance(document, list):
            raise StepBRefusedError("job_listing_invalid", "batch.list_jobs is not a list")
        job = next((j for j in document if isinstance(j, dict) and j.get("id") == job_id), None)
        if job is not None:
            state = job.get("state")
            if state == TERMINAL_OK:
                return job
            if state in TERMINAL_FAILED:
                raise StepBRefusedError("job_expired", f"job state {state}")
        if attempt < len(delays):
            sleep(delays[attempt])
    raise StepBRefusedError("poll_timeout", "job not done within the bounded schedule")


# --- main ---------------------------------------------------------------------------------------


def run_step_b(
    paths: ProjectPaths,
    *,
    approval_path: Path,
    step_a_report_path: Path,
    opener: Opener | None = None,
    env: Mapping[str, str] | None = None,
    clock: Callable[[], int] = time.time_ns,
    sleep: Callable[[float], None] = time.sleep,
    poll_delays_s: Sequence[float] = DEFAULT_POLL_DELAYS_S,
    allow_redownload: bool = False,
    operator: str = "claude-code",
) -> StepBOutcome:
    plan = load_approved_plan(approval_path, step_a_report_path)
    recovery = recover_raw_publications(paths)
    if not recovery.clean:
        raise StepBRefusedError("recovery_not_clean", f"{len(recovery.conflicts)} conflict(s)")
    ledger = RequestLedger(ledger_path(paths))
    completion = ledger.completion(REQUEST_SHA256)
    if completion is not None:
        return _verify_completion(paths, completion)

    environment: Mapping[str, str] = os.environ if env is None else env
    read_api_key(environment)
    client = HistoricalClient(step="B", ledger=ledger, opener=opener, env=environment, clock=clock)
    recorded = _recorded_job(ledger)
    created_now = recorded is None
    if recorded is None:
        job_id, creation_request_id, cost = _create_job(client, ledger, plan, clock)
    else:
        job_id, creation_request_id, cost = recorded

    job = _poll_until_done(client, job_id, poll_delays_s, sleep)
    _check_job_echo(job)
    cost = (
        _check_job_cost(job, plan.estimate, ledger=ledger, job_id=job_id, stage="done", clock=clock)
        or cost
    )
    listing = parse_file_listing(client.call_json(Endpoint.LIST_BATCH_FILES, job_id=job_id), job_id)

    states = {f.filename: _publication_state(paths, raw_relative_for(f.filename)) for f in listing}
    pending = [f for f in listing if states[f.filename] == "absent"]
    for listed in pending:
        if ledger.download_attempts(job_id, listed.filename) and not allow_redownload:
            raise StepBRefusedError("download_already_attempted", listed.filename)

    validated: list[tuple[ListedFile, bytes, FileValidation, int, int]] = []
    for listed in pending:
        started = clock()
        data = client.download(
            listed.url, job_id=job_id, filename=listed.filename, expected_size=listed.size
        )
        completed = clock()
        digest = sha256_hex(data)
        if listed.sha256 is not None and digest != listed.sha256:
            raise StepBRefusedError("hash_mismatch", listed.filename)
        try:
            validation = validate_file(listed.filename, data, plan.instrument_ids)
        except PayloadRejectedError as error:
            raise StepBRefusedError("payload_rejected", str(error)) from None
        ledger.append(
            {
                "event": "download_validated",
                "job_id": job_id,
                "provider_filename": listed.filename,
                "bytes": len(data),
                "sha256": digest,
                "provider_sha256": listed.sha256,
                "utc_ns": completed,
            }
        )
        validated.append((listed, data, validation, started, completed))

    published: list[PublishedFile] = []
    for listed, data, validation, started, completed in validated:
        relative = raw_relative_for(listed.filename)
        metadata = {
            **validation.metadata,
            "file_role": validation.kind,
            "job_id": job_id,
            "provider_size": str(listed.size),
            "provider_sha256": listed.sha256 or "not_provided",
            "approval_sha256": plan.approval_sha256,
            "step_a_report_sha256": plan.report_sha256,
            "approved_by": plan.approval.approved_by,
            "approved_estimate_usd": plan.approval.approved_estimate_usd,
            "user_states_credit_covers_estimate": str(
                plan.approval.user_states_credit_covers_estimate
            ).lower(),
            "max_cash_charge_usd": plan.approval.max_cash_charge_usd,
        }
        result = publish_raw(
            paths,
            relative,
            data,
            {
                "acquisition_id": f"databento-{job_id}-{listed.filename}",
                "provider": PROVIDER,
                "dataset": FROZEN_REQUEST.dataset,
                "source_schema": FROZEN_REQUEST.record_schema,
                "source_format_version": None,
                "venue": VENUE,
                "canonical_contract": CANONICAL_CONTRACT,
                "trading_date": MANIFEST_TRADING_DATE,
                "request_parameters": manifest_request_parameters(job_id, listed.filename),
                "request_start_utc_ns": START_UTC_NS,
                "request_end_utc_ns": END_UTC_NS,
                "retrieval_started_utc_ns": started,
                "retrieval_completed_utc_ns": completed,
                "cost_ledger_request_id": creation_request_id,
                "estimated_cost": plan.estimate,
                "estimated_cost_currency": "USD",
                "actual_cost_if_known": Decimal(cost) if cost is not None else None,
                # Approved exact estimate, zero or user-stated as covered by free credit, with a
                # maximum cash charge of 0 (the only decision the manifest contract admits).
                "cost_decision": "FREE_CONFIRMED",
                "license_url": LICENSE_URL,
                "license_version": LICENSE_VERSION,
                "response_status": 200,
                "response_metadata": metadata,
                "publisher_version": PUBLISHER_VERSION,
            },
        )
        manifest = verify_published(paths, relative)
        ledger.append(
            {
                "event": "publication",
                "job_id": job_id,
                "relative": relative,
                "payload_path": manifest.payload_path,
                "sha256": manifest.payload_sha256,
                "bytes": manifest.payload_bytes,
                "manifest": paths.relative_to_root(result.manifest),
                "utc_ns": clock(),
            }
        )
        published.append(
            PublishedFile(
                relative,
                manifest.payload_path,
                manifest.payload_bytes,
                manifest.payload_sha256,
                False,
            )
        )
    for listed in listing:
        if states[listed.filename] == "published":
            relative = raw_relative_for(listed.filename)
            manifest = verify_published(paths, relative)
            published.append(
                PublishedFile(
                    relative,
                    manifest.payload_path,
                    manifest.payload_bytes,
                    manifest.payload_sha256,
                    True,
                )
            )
    published.sort(key=lambda p: p.relative)
    now = clock()
    ledger.append(
        {
            "event": "step_b_complete",
            "request_sha256": REQUEST_SHA256,
            "job_id": job_id,
            "files": [
                {"relative": p.relative, "sha256": p.payload_sha256, "bytes": p.payload_bytes}
                for p in published
            ],
            "actual_cost_usd": cost,
            "utc_ns": now,
        }
    )
    ledger.append(
        {
            "event": "cost_gate",
            "step": "B",
            "request_id": creation_request_id,
            "request_sha256": REQUEST_SHA256,
            "provider": PROVIDER,
            "dataset": FROZEN_REQUEST.dataset,
            "schema": FROZEN_REQUEST.record_schema,
            "symbols": FROZEN_REQUEST.symbols,
            "start_utc": FROZEN_REQUEST.start,
            "end_utc": FROZEN_REQUEST.end,
            "estimated_bytes": plan.approval.approved_billable_size,
            "estimated_cost_currency": "USD",
            "estimated_cost": str(plan.estimate),
            # Credit is a user statement in the approval, never read from the account.
            "credit_checked_at": (
                plan.approval.approved_at_utc
                if plan.approval.user_states_credit_covers_estimate
                else None
            ),
            "user_states_credit_covers_estimate": plan.approval.user_states_credit_covers_estimate,
            "max_cash_charge_usd": plan.approval.max_cash_charge_usd,
            "approved_estimate_usd": plan.approval.approved_estimate_usd,
            "decision": "FREE_CONFIRMED",
            "raw_manifest_id": [
                f"databento-{job_id}-{p.relative.rsplit('/', 1)[1]}" for p in published
            ],
            "actual_cost_if_known": cost,
            "operator": operator,
            "utc_ns": now,
        }
    )
    return StepBOutcome(
        job_id=job_id,
        job_created_now=created_now,
        network_used=True,
        files=tuple(published),
        actual_cost_usd=cost,
        endpoints_called=tuple(e.value for e in client.sent),
    )
