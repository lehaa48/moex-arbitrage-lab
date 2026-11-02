"""T-Bank historical anonymized-trades adapter (P1): one daily file of one MOEX contract.

Zero-cost, unauthenticated, rate-limited acquisition through an injectable opener; fail-closed
payload validation; create-once raw publication; deterministic ``trade_v1`` parsing with full
row reconciliation; measured source QA. ``DIRECTION`` is never mapped to an aggressor side.

H-0001 v0.2 seconds screen: a frozen date-specific session calendar (opening auction separate
from continuous sessions), a deterministic multi-day request plan, sequential fail-closed
period acquisition with a checksum-verified cache, fail-closed instrument identity, per-file
and period QA, and a downstream loader that reads verified publications only.
"""

from moex_arbitrage_lab.sources.tbank_history.acquire import (
    AcquisitionOutcome,
    acquire,
    config_sha256,
    contract_slug,
    raw_relative,
    run_qa,
)
from moex_arbitrage_lab.sources.tbank_history.archive import (
    ARCHIVE_REJECTION_REASONS,
    ArchiveContents,
    ArchiveRejectedError,
    validate_archive,
)
from moex_arbitrage_lab.sources.tbank_history.config import (
    DEFAULT_LAG_BUCKETS_NS,
    SessionWindow,
    TbankHistoryConfig,
    load_config,
)
from moex_arbitrage_lab.sources.tbank_history.fetch import (
    ALLOWED_RESPONSE_HEADERS,
    FETCH_REJECTION_REASONS,
    REQUEST_HEADERS,
    FetchRejectedError,
    FetchResponse,
    FetchResult,
    Opener,
    RequestLedger,
    fetch,
    ledger_path,
)
from moex_arbitrage_lab.sources.tbank_history.identity import (
    IDENTITY_REJECTION_REASONS,
    UID_STATUS,
    FileIdentity,
    IdentityRejectedError,
    check_file_identity,
    check_period_identity,
)
from moex_arbitrage_lab.sources.tbank_history.inspection import FileInspection, inspect_payload
from moex_arbitrage_lab.sources.tbank_history.parser import (
    INELIGIBILITY_REASONS,
    PARSE_FAILURE_REASONS,
    PARSER_VERSION,
    CsvSchemaError,
    ParseResult,
    Reconciliation,
    parse_digest,
    parse_trade_ts,
    parse_trades,
)
from moex_arbitrage_lab.sources.tbank_history.period import (
    LoadedTemplate,
    PeriodTemplate,
    RequestPlan,
    build_day_config,
    load_template,
    plan_document,
    plan_for,
    plan_requests,
)
from moex_arbitrage_lab.sources.tbank_history.period_acquire import (
    DateOutcome,
    PeriodReport,
    acquire_period,
)
from moex_arbitrage_lab.sources.tbank_history.period_qa import (
    DayTrades,
    file_qa,
    load_period_trades,
    qa_period,
)
from moex_arbitrage_lab.sources.tbank_history.qa import LAG_STATUS, QA_VERSION, compute_qa
from moex_arbitrage_lab.sources.tbank_history.session_calendar import (
    CalendarError,
    MoexCalendar,
    load_calendar,
)

__all__ = [
    "ALLOWED_RESPONSE_HEADERS",
    "ARCHIVE_REJECTION_REASONS",
    "DEFAULT_LAG_BUCKETS_NS",
    "FETCH_REJECTION_REASONS",
    "IDENTITY_REJECTION_REASONS",
    "INELIGIBILITY_REASONS",
    "LAG_STATUS",
    "PARSER_VERSION",
    "PARSE_FAILURE_REASONS",
    "QA_VERSION",
    "REQUEST_HEADERS",
    "UID_STATUS",
    "AcquisitionOutcome",
    "ArchiveContents",
    "ArchiveRejectedError",
    "CalendarError",
    "CsvSchemaError",
    "DateOutcome",
    "DayTrades",
    "FetchRejectedError",
    "FetchResponse",
    "FetchResult",
    "FileIdentity",
    "FileInspection",
    "IdentityRejectedError",
    "LoadedTemplate",
    "MoexCalendar",
    "Opener",
    "ParseResult",
    "PeriodReport",
    "PeriodTemplate",
    "Reconciliation",
    "RequestLedger",
    "RequestPlan",
    "SessionWindow",
    "TbankHistoryConfig",
    "acquire",
    "acquire_period",
    "build_day_config",
    "check_file_identity",
    "check_period_identity",
    "compute_qa",
    "config_sha256",
    "contract_slug",
    "fetch",
    "file_qa",
    "inspect_payload",
    "ledger_path",
    "load_calendar",
    "load_config",
    "load_period_trades",
    "load_template",
    "parse_digest",
    "parse_trade_ts",
    "parse_trades",
    "plan_document",
    "plan_for",
    "plan_requests",
    "qa_period",
    "raw_relative",
    "run_qa",
    "validate_archive",
]
