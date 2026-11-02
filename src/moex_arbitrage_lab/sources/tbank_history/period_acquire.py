"""Sequential, rate-limited, fail-closed acquisition of a planned period (H-0001 v0.2).

``acquire_period`` walks ``plan.requested`` in order (canary first) and never runs requests in
parallel:

1. preflight: ``recover_raw_publications`` must be clean, otherwise nothing is attempted
   (status ``blocked``);
2. per date, a committed publication that verifies (manifest/checksum/marker SHA-256) is
   reused **without network**; it is still re-inspected offline (gzip, header, parse,
   reconciliation, identity) and any failure stops the run;
3. otherwise the run waits (injected ``sleep``) until the request ledger allows a request
   (minimum interval and requests/minute of the config), then performs exactly one GET through
   ``acquire``. The payload is inspected *before* publication (``accept`` hook): only a payload
   that passes container, header/schema, reconciliation and file + period identity checks is
   published (create-once; raw bytes unchanged);
4. HTTP 404 ("file not found" per the archive documentation) records ``missing_file`` as
   coverage evidence and continues, without retry — except for the canary, where it stops;
5. any other rejection (auth, 429, redirect, HTML, content type, size, corrupt gzip,
   header/schema mismatch, identity failure, collision, unverifiable target, own rate-limit)
   stops immediately: no retry, no overwrite, no further request. Remaining dates are
   ``not_attempted``.

The canary is the first date; because every date passes the same checks and the run stops at
the first failure, no further request is made unless the canary fully passed.

The returned ``PeriodReport`` is machine-readable (per date: outcome, reason, bytes, SHA-256,
raw relative path, manifest id, observed UID). Wall-clock values appear only through the raw
manifests, so a fully cached rerun reproduces the same report except ``requests_sent``.
"""

import math
import time
import urllib.request
from collections.abc import Callable
from datetime import date
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from moex_arbitrage_lab.contracts.acquisition_manifest import AcquisitionManifestV1
from moex_arbitrage_lab.contracts.types import sha256_hex
from moex_arbitrage_lab.settings.raw_publication import (
    RawPublicationError,
    recover_raw_publications,
)
from moex_arbitrage_lab.settings.storage_paths import (
    ProjectPaths,
    StorageCategory,
    StoragePathError,
)
from moex_arbitrage_lab.sources.tbank_history.acquire import (
    acquire,
    existing_publication,
    raw_relative,
)
from moex_arbitrage_lab.sources.tbank_history.archive import ArchiveRejectedError
from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig
from moex_arbitrage_lab.sources.tbank_history.fetch import (
    FetchRejectedError,
    FetchResponse,
    FetchResult,
    Opener,
    RequestLedger,
    default_opener,
    ledger_path,
)
from moex_arbitrage_lab.sources.tbank_history.identity import (
    UID_STATUS,
    IdentityRejectedError,
    check_period_identity,
)
from moex_arbitrage_lab.sources.tbank_history.inspection import FileInspection, inspect_payload
from moex_arbitrage_lab.sources.tbank_history.parser import CsvSchemaError
from moex_arbitrage_lab.sources.tbank_history.period import (
    LoadedTemplate,
    RequestPlan,
    build_day_config,
)

REPORT_VERSION: Final = "tbank_period_acquisition_v1"
OUTCOMES: Final = ("published", "reused_verified", "missing_file", "rejected", "not_attempted")
HTTP_NOT_FOUND: Final = 404
# Waiting must make progress; a clock that does not advance while sleeping fails closed.
_MAX_WAITS: Final = 8

Outcome = Literal["published", "reused_verified", "missing_file", "rejected", "not_attempted"]


class DateOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    position: int
    trading_date: date
    role: Literal["canary", "period"]
    outcome: Outcome
    reason: str | None = None
    http_status: int | None = None
    network_request: bool = False
    raw_relative: str | None = None
    payload_bytes: int | None = None
    payload_sha256: str | None = None
    manifest_id: str | None = None
    cost_ledger_request_id: str | None = None
    instrument_uid: str | None = None
    csv_data_rows: int | None = None
    eligible_rows: int | None = None


class NotRequestedDate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    trading_date: date
    reason: str
    note: str | None = None


class PeriodReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    report_version: str = REPORT_VERSION
    template_id: str
    template_sha256: str
    calendar_id: str
    calendar_sha256: str
    period_start: date
    period_end_exclusive: date
    canary: date
    status: Literal["complete", "stopped", "blocked"]
    stop_reason: str | None = None
    stop_date: date | None = None
    requests_sent: int
    instrument_uid: str | None = None
    uid_status: str = UID_STATUS
    outcome_counts: dict[str, int]
    outcomes: tuple[DateOutcome, ...]
    not_requested: tuple[NotRequestedDate, ...]


class _CountingOpener:
    """Counts every ``open`` call of the wrapped opener (evidence of network use)."""

    def __init__(self, inner: Opener) -> None:
        self._inner = inner
        self.calls = 0

    def open(self, request: urllib.request.Request, timeout: float) -> FetchResponse:
        self.calls += 1
        return self._inner.open(request, timeout)


class _PrePublicationCheck:
    """``acquire(accept=...)`` hook: inspect a downloaded payload before it is published and
    remember its size/hash for the report, even when it is refused."""

    def __init__(
        self, config: TbankHistoryConfig, payload_path: str, accepted_uids: tuple[str, ...]
    ) -> None:
        self._config = config
        self._payload_path = payload_path
        self._accepted_uids = accepted_uids
        self.payload_bytes: int | None = None
        self.payload_sha256: str | None = None
        self.inspection: FileInspection | None = None

    def __call__(self, result: FetchResult) -> None:
        digest = sha256_hex(result.body)
        self.payload_bytes = len(result.body)
        self.payload_sha256 = digest
        inspection = inspect_payload(
            result.body,
            self._config,
            payload_sha256=digest,
            payload_path=self._payload_path,
            local_ingest_ts_utc_ns=result.completed_utc_ns,
        )
        check_period_identity([*self._accepted_uids, inspection.identity.instrument_uid])
        self.inspection = inspection


def _wait_for_slot(
    ledger: RequestLedger,
    config: TbankHistoryConfig,
    clock: Callable[[], int],
    sleep: Callable[[float], None],
) -> None:
    for _ in range(_MAX_WAITS):
        wait = ledger.wait_ns(clock(), config)
        if wait == 0:
            return
        # Round up to whole milliseconds so the wait is never short.
        sleep(math.ceil(wait / 1_000_000) / 1_000)
    raise FetchRejectedError("rate_limited", "clock did not advance while waiting for a slot")


def _payload_path(paths: ProjectPaths, relative: str) -> str:
    return paths.relative_to_root(paths.resolve(StorageCategory.RAW, relative))


def _reason(error: BaseException) -> str:
    if isinstance(error, FetchRejectedError):
        return f"fetch:{error.reason}"
    if isinstance(error, ArchiveRejectedError):
        return f"archive:{error.reason}"
    if isinstance(error, IdentityRejectedError):
        return f"identity:{error.reason}"
    if isinstance(error, CsvSchemaError):
        return "schema:header_mismatch"
    if isinstance(error, RawPublicationError):
        return f"publication:{type(error).__name__}"
    if isinstance(error, StoragePathError):
        return f"storage:{type(error).__name__}"
    if isinstance(error, RuntimeError):
        return "blocked:" + str(error).removeprefix("BLOCKED: ")[:200]
    return f"rejected:{type(error).__name__}"


def _is_stop_error(error: BaseException) -> bool:
    if isinstance(
        error,
        FetchRejectedError
        | ArchiveRejectedError
        | IdentityRejectedError
        | RawPublicationError
        | StoragePathError
        | ValueError,
    ):
        return True
    return isinstance(error, RuntimeError) and str(error).startswith("BLOCKED")


def acquire_period(
    paths: ProjectPaths,
    loaded: LoadedTemplate,
    plan: RequestPlan,
    *,
    opener: Opener | None = None,
    clock: Callable[[], int] = time.time_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> PeriodReport:
    """Acquire every planned date sequentially (see the module docstring)."""
    counting = _CountingOpener(opener if opener is not None else default_opener())
    ledger = RequestLedger(ledger_path(paths))
    outcomes: list[DateOutcome] = []
    uids: list[str] = []
    status: Literal["complete", "stopped", "blocked"] = "complete"
    stop_reason: str | None = None
    stop_date: date | None = None

    recovery = recover_raw_publications(paths)
    if not recovery.clean:
        status = "blocked"
        stop_reason = f"recovery_not_clean:{len(recovery.conflicts)}_conflict(s)"

    for position, day in enumerate(plan.requested):
        role: Literal["canary", "period"] = "canary" if day == plan.canary else "period"
        if status != "complete":
            outcomes.append(
                DateOutcome(position=position, trading_date=day, role=role, outcome="not_attempted")
            )
            continue
        calls_before = counting.calls
        check: _PrePublicationCheck | None = None
        relative: str | None = None
        try:
            config = build_day_config(loaded, day)
            relative = raw_relative(config)
            payload_path = _payload_path(paths, relative)
            existing = existing_publication(paths, relative, config)
            if existing is not None:
                payload = paths.resolve(StorageCategory.RAW, relative).read_bytes()
                if sha256_hex(payload) != existing.payload_sha256:
                    raise RuntimeError("BLOCKED: raw payload changed after verification")
                inspection = inspect_payload(
                    payload,
                    config,
                    payload_sha256=existing.payload_sha256,
                    payload_path=existing.payload_path,
                    local_ingest_ts_utc_ns=existing.retrieval_completed_utc_ns,
                )
                check_period_identity([*uids, inspection.identity.instrument_uid])
                uids.append(inspection.identity.instrument_uid)
                outcomes.append(
                    _done(position, day, role, "reused_verified", relative, existing, inspection)
                )
                continue

            check = _PrePublicationCheck(config, payload_path, tuple(uids))
            _wait_for_slot(ledger, config, clock, sleep)
            acquired = acquire(paths, config, opener=counting, clock=clock, accept=check)
            checked = check.inspection
            if (
                checked is None
                or acquired.already_published
                or acquired.payload_sha256 != check.payload_sha256
                or acquired.manifest.payload_path != payload_path
            ):
                raise RuntimeError("BLOCKED: published payload differs from the inspected one")
            uids.append(checked.identity.instrument_uid)
            outcomes.append(
                _done(position, day, role, "published", relative, acquired.manifest, checked, True)
            )
        except Exception as error:
            if not _is_stop_error(error):
                raise
            network = counting.calls > calls_before
            if (
                isinstance(error, FetchRejectedError)
                and error.http_status == HTTP_NOT_FOUND
                and role == "period"
            ):
                outcomes.append(
                    DateOutcome(
                        position=position,
                        trading_date=day,
                        role=role,
                        outcome="missing_file",
                        reason="http_404_file_not_found",
                        http_status=HTTP_NOT_FOUND,
                        network_request=network,
                        raw_relative=relative,
                    )
                )
                continue
            reason = _reason(error)
            if isinstance(error, FetchRejectedError) and error.http_status == HTTP_NOT_FOUND:
                reason = "canary_missing_file"
            outcomes.append(
                DateOutcome(
                    position=position,
                    trading_date=day,
                    role=role,
                    outcome="rejected",
                    reason=reason,
                    http_status=getattr(error, "http_status", None),
                    network_request=network,
                    raw_relative=relative,
                    payload_bytes=None if check is None else check.payload_bytes,
                    payload_sha256=None if check is None else check.payload_sha256,
                )
            )
            status = "stopped"
            stop_reason = reason
            stop_date = day

    counts = dict.fromkeys(OUTCOMES, 0)
    for entry in outcomes:
        counts[entry.outcome] += 1
    template = loaded.template
    return PeriodReport(
        template_id=template.template_id,
        template_sha256=loaded.template_sha256,
        calendar_id=loaded.calendar.calendar_id,
        calendar_sha256=loaded.calendar_sha256,
        period_start=plan.period_start,
        period_end_exclusive=plan.period_end_exclusive,
        canary=plan.canary,
        status=status,
        stop_reason=stop_reason,
        stop_date=stop_date,
        requests_sent=counting.calls,
        instrument_uid=check_period_identity(uids),
        outcome_counts=counts,
        outcomes=tuple(outcomes),
        not_requested=tuple(
            NotRequestedDate(trading_date=entry.day, reason=entry.reason, note=entry.note)
            for entry in plan.not_requested
        ),
    )


def _done(
    position: int,
    day: date,
    role: Literal["canary", "period"],
    outcome: Outcome,
    relative: str,
    manifest: AcquisitionManifestV1,
    inspection: FileInspection,
    network: bool = False,
) -> DateOutcome:
    return DateOutcome(
        position=position,
        trading_date=day,
        role=role,
        outcome=outcome,
        http_status=manifest.response_status,
        network_request=network,
        raw_relative=relative,
        payload_bytes=manifest.payload_bytes,
        payload_sha256=manifest.payload_sha256,
        manifest_id=manifest.acquisition_id,
        cost_ledger_request_id=manifest.cost_ledger_request_id,
        instrument_uid=inspection.identity.instrument_uid,
        csv_data_rows=inspection.result.reconciliation.csv_data_rows,
        eligible_rows=inspection.result.reconciliation.eligible_rows,
    )
