"""Versioned, strict contracts: canonical ``trade_v1``, acquisition manifest, clock semantics."""

from moex_arbitrage_lab.contracts.acquisition_manifest import (
    MANIFEST_VERSION,
    AcquisitionManifestV1,
    manifest_from_json_bytes,
    manifest_to_json_bytes,
)
from moex_arbitrage_lab.contracts.clock import (
    ClockDomain,
    ClockDomainError,
    ClockReading,
    ClockSample,
    SystemMonotonicClock,
    SystemWallClock,
    elapsed_ns,
    offset_ms,
    reference_time_ns,
)
from moex_arbitrage_lab.contracts.trade_v1 import (
    EVENT_TS_TYPE_UNKNOWN,
    TRADE_SCHEMA_VERSION,
    TRADE_V1_FIELDS,
    AggressorSide,
    TradeAction,
    TradeV1,
    trade_from_json_bytes,
    trade_to_json_bytes,
)
from moex_arbitrage_lab.contracts.types import sha256_hex

__all__ = [
    "EVENT_TS_TYPE_UNKNOWN",
    "MANIFEST_VERSION",
    "TRADE_SCHEMA_VERSION",
    "TRADE_V1_FIELDS",
    "AcquisitionManifestV1",
    "AggressorSide",
    "ClockDomain",
    "ClockDomainError",
    "ClockReading",
    "ClockSample",
    "SystemMonotonicClock",
    "SystemWallClock",
    "TradeAction",
    "TradeV1",
    "elapsed_ns",
    "manifest_from_json_bytes",
    "manifest_to_json_bytes",
    "offset_ms",
    "reference_time_ns",
    "sha256_hex",
    "trade_from_json_bytes",
    "trade_to_json_bytes",
]
