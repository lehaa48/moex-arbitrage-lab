"""Strict configuration of the T-Bank historical anonymized-trades adapter (P1).

One config describes exactly one daily file of one instrument. Verified provider facts
(developer.tbank.ru, history-md), recorded here only as context for the fields:

- URL pattern ``https://invest-public-api.tbank.ru/history-trades/{YYYY-MM-DD}?instrumentId=
  {TICKER}_{CLASS_CODE}``; 404 means "file not found", 429 means rate limited (30 files/min/IP);
- header ``TRADE_TS,TICKER_CC,DIRECTION,PRICE,QUANTITY,TRADE_SOURCE,INSTRUMENT_UID``;
  ``TRADE_TS`` is documented as the trade date/time in UTC (example
  ``2018-01-23T07:02:29.315229Z``); ``TRADE_SOURCE`` is ``EXCHANGE`` or ``DEALER``;
  ``QUANTITY`` is in lots; ``DIRECTION`` semantics are undefined ("направление"), so it is
  never mapped to an aggressor side;
- the documentation is contradictory on whether the response is a ZIP with one CSV or a plain
  CSV, hence the explicit ``payload_format``. The single HEAD probe of 2026-11-02 observed
  ``200``, ``Content-Type: application/octet-stream``, ``Content-Disposition: attachment;
  filename=GDZ6_SPBFUT_2026-10-06.csv.gz`` and no ``Content-Length``, so ``"gzip"`` (a gzip
  stream of one CSV) is supported as well.

Everything else (sessions, contract economics, license, zero-cost evidence) is supplied by the
main agent from official sources; nothing here is a default guessed from data, except the
preregistered H-0001 lag family.
"""

import itertools
import re
import tomllib
from datetime import date
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from moex_arbitrage_lab.contracts.types import (
    CanonicalContract,
    CurrencyCode,
    ExactDecimal,
    Identifier,
    SourceText,
    parse_canonical_contract,
)

_MS: Final = 1_000_000
_S: Final = 1_000_000_000
# H-0001 lag/horizon family: mandatory 1..500 ms and 1..60 s, secondary 2 and 5 minutes.
DEFAULT_LAG_BUCKETS_NS: Final[tuple[int, ...]] = (
    1 * _MS,
    5 * _MS,
    10 * _MS,
    25 * _MS,
    50 * _MS,
    100 * _MS,
    250 * _MS,
    500 * _MS,
    1 * _S,
    2 * _S,
    5 * _S,
    10 * _S,
    30 * _S,
    60 * _S,
    120 * _S,
    300 * _S,
)

REQUIRED_COLUMNS: Final = ("TRADE_TS", "TICKER_CC", "PRICE", "QUANTITY", "TRADE_SOURCE")
OPTIONAL_COLUMNS: Final = ("DIRECTION", "INSTRUMENT_UID")

_SLUG_PATTERN: Final = r"^[a-z0-9][a-z0-9._=-]*$"
_SLUG: Final = re.compile(_SLUG_PATTERN)
_INGEST_SEMANTICS: Final = re.compile(r"INGEST|LOCAL|RETRIEV|DOWNLOAD", re.IGNORECASE)

Slug = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=_SLUG_PATTERN)]
HttpsUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https://[^\s]+$")]
# The request URL is stored as a manifest request parameter (an ``Identifier``: <=256 chars,
# no whitespace/control characters), so it is held to that grammar here, before any request.
RequestUrl = Annotated[
    str,
    StringConstraints(min_length=9, max_length=256, pattern=r"^https://[^\s\x00-\x1f\x7f-\x9f]+$"),
]
ContentType = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
]


class SessionWindow(BaseModel):
    """One verified venue session as a half-open UTC interval ``[start, end)``.

    ``continuous`` is ``False`` for a call-auction window (e.g. the MOEX opening auction):
    its trades are parsed and counted (never ``outside_verified_session``), but a downstream
    consumer must never treat the window as continuous trading. Omitted means ``True`` (the
    single-day configs list continuous sessions only)."""

    # Inline (not a shared variable) so the pydantic mypy plugin sees frozen=True.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    name: Identifier
    start_utc_ns: int = Field(ge=0)
    end_utc_ns: int = Field(ge=0)
    trading_date: date
    continuous: bool = True

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end_utc_ns <= self.start_utc_ns:
            raise ValueError("session window must be half-open [start, end) with end > start")
        return self


class TbankHistoryConfig(BaseModel):
    """Exactly one daily file of one instrument; immutable once constructed."""

    # Inline (not a shared variable) so the pydantic mypy plugin sees frozen=True.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    dataset: Slug
    source_schema: Identifier
    # TOML has no null: an omitted nullable key means "not supplied" (None).
    source_format_version: Identifier | None = None
    archive_date: date
    url: RequestUrl
    raw_filename: Slug
    payload_format: Literal["zip", "csv", "gzip"]

    ticker_cc: Identifier
    instrument_uid: Identifier | None = None

    canonical_contract: CanonicalContract
    expiry: date
    currency: CurrencyCode
    price_unit: SourceText
    contract_multiplier: ExactDecimal = Field(gt=0)
    tick_size: Annotated[ExactDecimal, Field(gt=0)] | None = None

    event_ts_type: Identifier
    clock_accuracy_documented: bool
    trade_action: Literal["ADD", "UNKNOWN"]

    csv_delimiter: str
    expected_columns: tuple[str, ...]
    csv_member_pattern: str
    max_members: int = Field(ge=1)
    max_uncompressed_bytes: int = Field(gt=0)

    expected_content_types: tuple[ContentType, ...] = Field(min_length=1)
    min_bytes: int = Field(ge=1)
    max_bytes: int
    request_timeout_s: float = Field(gt=0)
    requests_per_minute: int = Field(ge=1, le=30)
    min_request_interval_s: float

    sessions: tuple[SessionWindow, ...] = Field(min_length=1)

    license_url: HttpsUrl
    zero_cost_evidence_url: HttpsUrl
    license_version: Identifier
    cost_currency: CurrencyCode

    lag_buckets_ns: tuple[int, ...] = DEFAULT_LAG_BUCKETS_NS

    @field_validator("event_ts_type")
    @classmethod
    def _event_ts_type_is_not_local_time(cls, value: str) -> str:
        if _INGEST_SEMANTICS.search(value):
            raise ValueError("event_ts_type describes source event time only (ADR-0003)")
        return value

    @field_validator("ticker_cc")
    @classmethod
    def _ticker_gives_a_path_segment(cls, value: str) -> str:
        # Same derivation as acquire.contract_slug: ``GDZ6_SPBFUT`` -> ``gdz6``.
        slug = value.split("_", 1)[0].lower()
        if not _SLUG.fullmatch(slug) or slug.endswith("."):
            raise ValueError("ticker_cc root+month code must give a safe raw path segment")
        return value

    @field_validator("csv_delimiter")
    @classmethod
    def _single_character_delimiter(cls, value: str) -> str:
        if len(value) != 1 or value in {'"', "\r", "\n"}:
            raise ValueError("csv_delimiter must be exactly one character (not quote/newline)")
        return value

    @field_validator("expected_columns")
    @classmethod
    def _columns_complete_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not column for column in value):
            raise ValueError("expected_columns must be non-empty and unique")
        missing = [column for column in REQUIRED_COLUMNS if column not in value]
        if missing:
            raise ValueError(f"expected_columns lacks required columns: {missing}")
        return value

    @field_validator("csv_member_pattern")
    @classmethod
    def _member_pattern_compiles(cls, value: str) -> str:
        if not value:
            raise ValueError("csv_member_pattern must not be empty")
        try:
            re.compile(value)
        except re.error:
            raise ValueError("csv_member_pattern is not a valid regular expression") from None
        return value

    @field_validator("lag_buckets_ns")
    @classmethod
    def _lag_buckets_increasing(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or value[0] <= 0 or any(b <= a for a, b in itertools.pairwise(value)):
            raise ValueError("lag_buckets_ns must be positive and strictly increasing")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        identity = parse_canonical_contract(self.canonical_contract)
        if identity.venue != "MOEX":
            raise ValueError("canonical_contract must start with MOEX:")
        if identity.expiry != self.expiry:
            raise ValueError("canonical_contract must agree with expiry")
        if self.max_bytes <= self.min_bytes:
            raise ValueError("max_bytes must be greater than min_bytes")
        if self.min_request_interval_s < 60 / self.requests_per_minute:
            raise ValueError("min_request_interval_s must be >= 60 / requests_per_minute")
        for left, right in zip(self.sessions, self.sessions[1:], strict=False):
            if right.start_utc_ns < left.end_utc_ns:
                raise ValueError("sessions must be sorted by start and must not overlap")
        return self


def _tuples(value: object) -> object:
    """TOML arrays arrive as lists; strict models take tuples."""
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuples(item) for key, item in value.items()}
    return value


def load_config(path: Path) -> TbankHistoryConfig:
    """Load a TOML config (dates as TOML dates, decimals as strings); unknown keys are refused."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return TbankHistoryConfig.model_validate(_tuples(data))
