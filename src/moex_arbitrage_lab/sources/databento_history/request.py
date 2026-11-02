"""The one frozen Databento request of the H-0001 v0.2 seconds screen and its endpoint policy.

P2C fixes exactly one metered candidate for the missing remainder after the verified P2B
publication: ``GLBX.MDP3`` / ``ohlcv-1s`` / ``raw_symbol`` / ``GCZ6`` /
``[2026-09-11T00:00:00Z, 2026-11-01T00:00:00Z)`` as one batch job. Every field of
``FrozenBatchRequest`` is a ``Literal``
of that single value, so no other dataset, schema, symbol, interval or delivery option can be
constructed; every request parameter sent to the provider is derived from ``FROZEN_REQUEST`` by
the zero-argument builders below (callers cannot pass parameters).

Endpoint policy: Step A may only reach free metadata/symbology endpoints
(``STEP_A_ENDPOINTS``); Step B adds exactly one metered call (batch job creation) plus job
status, file listing and file download of that job (``STEP_B_ENDPOINTS``).

All endpoint paths, HTTP methods and parameter names/values below were written from the public
Databento Historical HTTP API v0 conventions WITHOUT network access. Each one carries the
marker ``VERIFY`` and must be checked against
https://databento.com/docs/api-reference-historical before Step A.
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

# VERIFY against https://databento.com/docs/api-reference-historical before Step A.
API_VERSION: Final = "v0"
# VERIFY against https://databento.com/docs/api-reference-historical before Step A.
API_HOST: Final = "hist.databento.com"
# Batch file URLs returned by ``batch.list_files`` use Databento's download host, distinct
# from the Historical RPC host. Verified from the provider response for the approved job.
DOWNLOAD_HOST: Final = "api.databento.com"
# VERIFY against https://databento.com/docs/api-reference-historical before Step A.
BASE_URL: Final = f"https://{API_HOST}/{API_VERSION}/"
# VERIFY against https://databento.com/docs/api-reference-historical before Step A:
# batch file URLs returned by batch.list_files are expected below this path prefix.
DOWNLOAD_PATH_PREFIX: Final = f"/{API_VERSION}/batch/download/"
# Environment variable holding the key; the value is read at call time only, never stored.
API_KEY_ENV_NAME: Final = "DATABENTO_API_KEY"

PROVIDER: Final = "databento"
VENUE: Final = "COMEX"


class Endpoint(StrEnum):
    """Historical API v0 endpoints (path relative to ``BASE_URL``).

    VERIFY every path against https://databento.com/docs/api-reference-historical before Step A.
    """

    LIST_SCHEMAS = "metadata.list_schemas"
    GET_DATASET_RANGE = "metadata.get_dataset_range"
    RESOLVE_SYMBOLOGY = "symbology.resolve"
    GET_RECORD_COUNT = "metadata.get_record_count"
    GET_BILLABLE_SIZE = "metadata.get_billable_size"
    GET_COST = "metadata.get_cost"
    # The provider's path for creating a batch job (the only metered call). Only this string
    # value carries the provider's name; no identifier in this package uses that word.
    CREATE_BATCH_JOB = "batch.submit_job"
    LIST_BATCH_JOBS = "batch.list_jobs"
    LIST_BATCH_FILES = "batch.list_files"
    # Pseudo endpoint: the HTTPS URL comes from batch.list_files and is checked against
    # API_HOST + DOWNLOAD_PATH_PREFIX + job id + file name.
    DOWNLOAD_BATCH_FILE = "batch.download"


# VERIFY each method against https://databento.com/docs/api-reference-historical before Step A.
# POST endpoints receive an application/x-www-form-urlencoded body; GET endpoints a query string.
ENDPOINT_METHOD: Final = MappingProxyType(
    {
        Endpoint.LIST_SCHEMAS: "GET",
        Endpoint.GET_DATASET_RANGE: "GET",
        Endpoint.RESOLVE_SYMBOLOGY: "POST",
        Endpoint.GET_RECORD_COUNT: "POST",
        Endpoint.GET_BILLABLE_SIZE: "POST",
        Endpoint.GET_COST: "POST",
        Endpoint.CREATE_BATCH_JOB: "POST",
        Endpoint.LIST_BATCH_JOBS: "GET",
        Endpoint.LIST_BATCH_FILES: "GET",
        Endpoint.DOWNLOAD_BATCH_FILE: "GET",
    }
)

Step = Literal["A", "B"]

# Step A: free metadata/symbology calls only. No batch/timeseries endpoint can be reached.
STEP_A_ENDPOINTS: Final = frozenset(
    {
        Endpoint.LIST_SCHEMAS,
        Endpoint.GET_DATASET_RANGE,
        Endpoint.RESOLVE_SYMBOLOGY,
        Endpoint.GET_RECORD_COUNT,
        Endpoint.GET_BILLABLE_SIZE,
        Endpoint.GET_COST,
    }
)
# Step B: a free cost re-check, one job creation, status polling, file listing and download.
STEP_B_ENDPOINTS: Final = frozenset(
    {
        Endpoint.GET_COST,
        Endpoint.CREATE_BATCH_JOB,
        Endpoint.LIST_BATCH_JOBS,
        Endpoint.LIST_BATCH_FILES,
        Endpoint.DOWNLOAD_BATCH_FILE,
    }
)
STEP_ENDPOINTS: Final = MappingProxyType({"A": STEP_A_ENDPOINTS, "B": STEP_B_ENDPOINTS})
# Metered: may be sent at most once per frozen request, ever (ledger-enforced).
METERED_ENDPOINTS: Final = frozenset({Endpoint.CREATE_BATCH_JOB})


class FrozenBatchRequest(BaseModel):
    """The only constructible request. Field names are local; ``batch_job_params`` maps them
    to provider parameter names (VERIFY names/values against
    https://databento.com/docs/api-reference-historical before Step A)."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    dataset: Literal["GLBX.MDP3"]
    record_schema: Literal["ohlcv-1s"]
    stype_in: Literal["raw_symbol"]
    stype_out: Literal["instrument_id"]
    symbols: Literal["GCZ6"]
    # Inclusive start, exclusive end (provider convention for ``start``/``end``).
    start: Literal["2026-09-11T00:00:00Z"]
    end: Literal["2026-11-01T00:00:00Z"]
    encoding: Literal["csv"]
    compression: Literal["none"]
    pretty_px: Literal[True]
    pretty_ts: Literal[False]
    map_symbols: Literal[True]
    split_symbols: Literal[False]
    split_duration: Literal["none"]
    delivery: Literal["download"]


FROZEN_REQUEST: Final = FrozenBatchRequest(
    dataset="GLBX.MDP3",
    record_schema="ohlcv-1s",
    stype_in="raw_symbol",
    stype_out="instrument_id",
    symbols="GCZ6",
    start="2026-09-11T00:00:00Z",
    end="2026-11-01T00:00:00Z",
    encoding="csv",
    compression="none",
    pretty_px=True,
    pretty_ts=False,
    map_symbols=True,
    split_symbols=False,
    split_duration="none",
    delivery="download",
)

START_DATE: Final = date(2026, 9, 11)
END_DATE_EXCLUSIVE: Final = date(2026, 11, 1)
START_UTC_NS: Final = int(datetime(2026, 9, 11, tzinfo=UTC).timestamp()) * 1_000_000_000
END_UTC_NS: Final = int(datetime(2026, 11, 1, tzinfo=UTC).timestamp()) * 1_000_000_000

# Raw layout: data/raw/databento/glbx-mdp3/gcz6/<period>/<provider file name>.
PERIOD_SEGMENT: Final = "2026-09-11--2026-11-01"
RAW_PREFIX: Final = f"databento/glbx-mdp3/gcz6/{PERIOD_SEGMENT}"
# The manifest has one ``trading_date``; this multi-day payload records the period start there and
# the exact interval in ``request_start_utc_ns``/``request_end_utc_ns``.
MANIFEST_TRADING_DATE: Final = START_DATE

# Canonical contract ``<VENUE>:<ROOT>:<expiry>``. VERIFY: GC last trade date is the third-last
# business day of the contract month (CME Rulebook ch. 113) -> 2026-12-29 for December 2026;
# confirm against the official CME calendar / instrument definition before Step B.
CANONICAL_CONTRACT: Final = "COMEX:GC:2026-12-29"
# VERIFY license terms before Step B (storage policy: unknown retention rights -> BLOCKED).
LICENSE_URL: Final = "https://databento.com/terms"
LICENSE_VERSION: Final = "databento-terms-unverified-2026-11-02"

# Batch job identifiers as returned by the provider (e.g. ``GLBX-20261102-ABCDEFGHIJ``).
JOB_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _form_bool(value: bool) -> str:
    # VERIFY: the API expects lowercase ``true``/``false`` form values.
    return "true" if value else "false"


def frozen_request_document() -> dict[str, object]:
    """Canonical description of the frozen request (provider parameter names)."""
    request = FROZEN_REQUEST
    return {
        "provider": PROVIDER,
        "api_version": API_VERSION,
        "dataset": request.dataset,
        "schema": request.record_schema,
        "stype_in": request.stype_in,
        "stype_out": request.stype_out,
        "symbols": request.symbols,
        "start": request.start,
        "end": request.end,
        "encoding": request.encoding,
        "compression": request.compression,
        "pretty_px": request.pretty_px,
        "pretty_ts": request.pretty_ts,
        "map_symbols": request.map_symbols,
        "split_symbols": request.split_symbols,
        "split_duration": request.split_duration,
        "delivery": request.delivery,
        "batch_jobs": 1,
    }


def _request_sha256() -> str:
    canonical = json.dumps(frozen_request_document(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Binds ledger entries, the Step A report and the user approval to this exact request.
REQUEST_SHA256: Final = _request_sha256()


# --- parameter builders (no arguments: nothing outside FROZEN_REQUEST can be sent) -----------


def dataset_params() -> dict[str, str]:
    """metadata.list_schemas / metadata.get_dataset_range (VERIFY parameter name)."""
    return {"dataset": FROZEN_REQUEST.dataset}


def symbology_params() -> dict[str, str]:
    """symbology.resolve: raw symbol -> instrument_id over ``[start_date, end_date)``.

    VERIFY parameter names and that ``end_date`` is exclusive."""
    request = FROZEN_REQUEST
    return {
        "dataset": request.dataset,
        "symbols": request.symbols,
        "stype_in": request.stype_in,
        "stype_out": request.stype_out,
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE_EXCLUSIVE.isoformat(),
    }


def estimate_params() -> dict[str, str]:
    """metadata.get_record_count / get_billable_size / get_cost for the exact request.

    VERIFY: parameter names; whether get_cost still accepts/needs ``mode`` (omitted here, so
    the provider default applies) and whether the estimate depends on encoding/compression."""
    request = FROZEN_REQUEST
    return {
        "dataset": request.dataset,
        "symbols": request.symbols,
        "schema": request.record_schema,
        "start": request.start,
        "end": request.end,
        "stype_in": request.stype_in,
    }


def batch_job_params() -> dict[str, str]:
    """The single batch job creation (VERIFY every name/value before Step A)."""
    request = FROZEN_REQUEST
    return {
        **estimate_params(),
        "stype_out": request.stype_out,
        "encoding": request.encoding,
        "compression": request.compression,
        "pretty_px": _form_bool(request.pretty_px),
        "pretty_ts": _form_bool(request.pretty_ts),
        "map_symbols": _form_bool(request.map_symbols),
        "split_symbols": _form_bool(request.split_symbols),
        "split_duration": request.split_duration,
        "delivery": request.delivery,
    }


def list_jobs_params() -> dict[str, str]:
    """batch.list_jobs (VERIFY the ``states`` filter name/values)."""
    # Databento rejected the former ``received`` value on 2026-11-02 and returned the
    # authoritative current set in its HTTP 400 response. Keep only accepted provider states.
    return {"states": "queued,processing,done,expired"}


def list_files_params(job_id: str) -> dict[str, str]:
    """batch.list_files for the one job created from the frozen request."""
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("job id has an unexpected form")
    return {"job_id": job_id}


def endpoint_params(endpoint: Endpoint, job_id: str | None = None) -> dict[str, str]:
    """Parameters of ``endpoint``; the only variable input is the provider-issued job id."""
    if endpoint in (Endpoint.LIST_SCHEMAS, Endpoint.GET_DATASET_RANGE):
        return dataset_params()
    if endpoint is Endpoint.RESOLVE_SYMBOLOGY:
        return symbology_params()
    if endpoint in (Endpoint.GET_RECORD_COUNT, Endpoint.GET_BILLABLE_SIZE, Endpoint.GET_COST):
        return estimate_params()
    if endpoint is Endpoint.CREATE_BATCH_JOB:
        return batch_job_params()
    if endpoint is Endpoint.LIST_BATCH_JOBS:
        return list_jobs_params()
    if endpoint is Endpoint.LIST_BATCH_FILES:
        if job_id is None:
            raise ValueError("batch.list_files needs the job id")
        return list_files_params(job_id)
    raise ValueError(f"{endpoint.value} has no parameter builder")


def manifest_request_parameters(job_id: str, provider_filename: str) -> dict[str, object]:
    """Request parameters recorded in each raw manifest (no secrets)."""
    request = FROZEN_REQUEST
    return {
        "dataset": request.dataset,
        "schema": request.record_schema,
        "stype_in": request.stype_in,
        "stype_out": request.stype_out,
        "symbols": request.symbols,
        "start": request.start,
        "end": request.end,
        "encoding": request.encoding,
        "compression": request.compression,
        "pretty_px": request.pretty_px,
        "pretty_ts": request.pretty_ts,
        "map_symbols": request.map_symbols,
        "split_symbols": request.split_symbols,
        "split_duration": request.split_duration,
        "delivery": request.delivery,
        "request_sha256": REQUEST_SHA256,
        "job_id": job_id,
        "provider_filename": provider_filename,
    }
