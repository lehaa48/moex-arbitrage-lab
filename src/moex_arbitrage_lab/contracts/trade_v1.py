"""Canonical trade schema ``trade_v1`` (technical specification v0.1, section 3).

Clock-separation and strict-schema semantics adapted from moex-event-alpha
``contracts/envelope.py`` at commit 068bbd8d86a935686d741ea0ef1949617f9f0cab (migration
row M-008); the old event envelope itself is not ported.

Rules (ADR-0003):

- ``event_ts_utc_ns``, ``receive_ts_utc_ns`` and ``capture_ts_utc_ns`` are nullable and have
  no default: a producer must state ``None`` explicitly when the source does not supply them.
- ``local_ingest_ts_utc_ns`` is required and never substitutes a missing source time: a missing
  event time stays ``None`` and an ``event_ts_type`` naming local/ingest/download time is
  refused. Equal event and ingest values are accepted (coarse clocks can coincide); adapters
  must map the source's event-time semantics explicitly into ``event_ts_type``.
- ``canonical_contract`` is ``<VENUE>:<ROOT>:<YYYY-MM-DD expiry>`` and must agree with
  ``venue`` and ``expiry``: an explicit contract, never a continuous series.
- Prices, sizes and multipliers are exact decimals; binary floats are refused.
- Field names and order are frozen; any change is a new schema version.

Construct only through validation (``model_validate``/``model_validate_json``);
``model_construct`` and ``model_copy(update=...)`` bypass validation and are forbidden.
"""

import re
from datetime import date
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moex_arbitrage_lab.contracts.types import (
    CanonicalContract,
    CurrencyCode,
    ExactDecimal,
    Identifier,
    Int64,
    NonNegativeInt64,
    RelativePosixPath,
    Sha256Hex,
    SourceIdentifier,
    SourceText,
    UtcNanos,
    parse_canonical_contract,
)

TRADE_SCHEMA_VERSION: Final = "trade_v1"
EVENT_TS_TYPE_UNKNOWN: Final = "UNKNOWN"

_INGEST_SEMANTICS: Final = re.compile(r"INGEST|LOCAL|RETRIEV|DOWNLOAD", re.IGNORECASE)


class AggressorSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class TradeAction(StrEnum):
    ADD = "ADD"
    CANCEL = "CANCEL"
    CORRECT = "CORRECT"
    UNKNOWN = "UNKNOWN"


class TradeV1(BaseModel):
    """One normalized trade; immutable once constructed."""

    # Rejected inputs are never echoed in errors: a refused value may be a secret.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    source: Identifier
    venue: Literal["MOEX", "COMEX"]
    dataset: Identifier
    instrument_id: SourceIdentifier
    raw_symbol: SourceText
    canonical_contract: CanonicalContract
    asset: Literal["GOLD"]
    expiry: date
    currency: CurrencyCode
    price_unit: SourceText
    contract_multiplier: ExactDecimal = Field(gt=0)
    event_ts_utc_ns: UtcNanos | None
    event_ts_type: Identifier
    receive_ts_utc_ns: UtcNanos | None
    capture_ts_utc_ns: UtcNanos | None
    local_ingest_ts_utc_ns: UtcNanos
    price: ExactDecimal
    price_ticks: Int64 | None
    size: ExactDecimal
    aggressor_side: AggressorSide | None
    sequence: NonNegativeInt64 | SourceIdentifier | None
    action: TradeAction
    flags: tuple[Identifier, ...] | None
    trade_source: Identifier | None
    session_id: Identifier | None
    trading_date: date
    source_file: RelativePosixPath
    source_record_id: SourceIdentifier
    source_row_no: NonNegativeInt64
    schema_version: Literal["trade_v1"]
    raw_payload_hash: Sha256Hex

    @field_validator("event_ts_type")
    @classmethod
    def _event_ts_type_is_not_local_time(cls, value: str) -> str:
        if _INGEST_SEMANTICS.search(value):
            raise ValueError(
                "event_ts_type describes source event time only; local/ingest time never "
                "substitutes it (ADR-0003)"
            )
        return value

    @model_validator(mode="after")
    def _contract_agrees_with_venue_and_expiry(self) -> Self:
        identity = parse_canonical_contract(self.canonical_contract)
        if identity.venue != self.venue or identity.expiry != self.expiry:
            raise ValueError("canonical_contract must agree with venue and expiry")
        return self


TRADE_V1_FIELDS: Final = tuple(TradeV1.model_fields)


def trade_to_json_bytes(trade: TradeV1) -> bytes:
    """Serialize deterministically: fixed field order, UTF-8, decimals as strings."""
    return trade.model_dump_json().encode("utf-8")


def trade_from_json_bytes(data: bytes) -> TradeV1:
    """Parse and fully validate a trade produced by ``trade_to_json_bytes``."""
    return TradeV1.model_validate_json(data)
