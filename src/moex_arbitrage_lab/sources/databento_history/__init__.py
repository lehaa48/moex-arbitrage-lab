"""Databento Historical adapter for the H-0001 v0.2 seconds screen (ADR-0005), offline-built.

Exactly one frozen request (``GLBX.MDP3`` / ``ohlcv-1s`` / ``raw_symbol`` / ``GCZ6`` /
``[2026-09-11, 2026-11-01)`` / one batch job, CSV, uncompressed). Step A reaches only free
metadata/symbology/cost endpoints and returns ``AWAITING_USER_APPROVAL`` (checks pass; the
exact estimate is recorded) or ``STOP_USER_DECISION``; explicit user approval is always
required. Step B needs a user approval file bound to the request, the exact Step A report and
the exact estimate (credit statement above zero, maximum cash charge 0), re-checks the estimate,
creates at most one batch job ever and publishes every verified file create-once. Stdlib
``urllib`` only, injectable opener; the key is read from ``DATABENTO_API_KEY`` per request and
never printed, logged or persisted.
"""

from moex_arbitrage_lab.sources.databento_history.client import (
    CLIENT_ERROR_REASONS,
    DatabentoError,
    EndpointNotAllowedError,
    HistoricalClient,
    MissingApiKeyError,
    Opener,
    check_download_url,
    read_api_key,
)
from moex_arbitrage_lab.sources.databento_history.ledger import (
    LedgerError,
    RequestLedger,
    ledger_path,
)
from moex_arbitrage_lab.sources.databento_history.request import (
    FROZEN_REQUEST,
    METERED_ENDPOINTS,
    REQUEST_SHA256,
    STEP_A_ENDPOINTS,
    STEP_B_ENDPOINTS,
    Endpoint,
    FrozenBatchRequest,
    frozen_request_document,
)
from moex_arbitrage_lab.sources.databento_history.step_a import (
    StepAError,
    StepAOutcome,
    run_step_a,
)
from moex_arbitrage_lab.sources.databento_history.step_b import (
    STEP_B_STOP_REASONS,
    StepBApprovalV1,
    StepBOutcome,
    StepBRefusedError,
    run_step_b,
)
from moex_arbitrage_lab.sources.databento_history.validate import (
    REQUIRED_CSV_COLUMNS,
    PayloadRejectedError,
    validate_file,
)

__all__ = [
    "CLIENT_ERROR_REASONS",
    "FROZEN_REQUEST",
    "METERED_ENDPOINTS",
    "REQUEST_SHA256",
    "REQUIRED_CSV_COLUMNS",
    "STEP_A_ENDPOINTS",
    "STEP_B_ENDPOINTS",
    "STEP_B_STOP_REASONS",
    "DatabentoError",
    "Endpoint",
    "EndpointNotAllowedError",
    "FrozenBatchRequest",
    "HistoricalClient",
    "LedgerError",
    "MissingApiKeyError",
    "Opener",
    "PayloadRejectedError",
    "RequestLedger",
    "StepAError",
    "StepAOutcome",
    "StepBApprovalV1",
    "StepBOutcome",
    "StepBRefusedError",
    "check_download_url",
    "frozen_request_document",
    "ledger_path",
    "read_api_key",
    "run_step_a",
    "run_step_b",
    "validate_file",
]
