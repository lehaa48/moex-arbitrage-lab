"""Contract-test guards and synthetic records.

Offline guard: every test runs with Python-level socket connections and name resolution
refused. All records are synthetic; no value comes from a real market-data source.
"""

import socket
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from moex_arbitrage_lab.contracts import AggressorSide, TradeAction, sha256_hex

SYNTHETIC_PAYLOAD = b"synthetic raw payload, not market data\n"
BASE_UTC_NS = 1_796_000_000_123_456_789


class NetworkRefusedError(RuntimeError):
    """A test tried to use the network; the P0 suite is offline."""


def _refuse(*args: object, **kwargs: object) -> None:
    raise NetworkRefusedError("tests must not use the network")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


def trade_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "source": "synthetic",
        "venue": "COMEX",
        "dataset": "synthetic.trades",
        "instrument_id": "1000001",
        "raw_symbol": "SYNTH Z6",
        "canonical_contract": "COMEX:GC:2026-12-29",
        "asset": "GOLD",
        "expiry": date(2026, 12, 29),
        "currency": "USD",
        "price_unit": "USD/troy ounce",
        "contract_multiplier": Decimal(100),
        "event_ts_utc_ns": BASE_UTC_NS,
        "event_ts_type": "EXCHANGE_MATCH",
        "receive_ts_utc_ns": BASE_UTC_NS + 1_000,
        "capture_ts_utc_ns": None,
        "local_ingest_ts_utc_ns": BASE_UTC_NS + 86_400_000_000_000,
        "price": Decimal("2345.1"),
        "price_ticks": 23451,
        "size": Decimal(1),
        "aggressor_side": AggressorSide.BUY,
        "sequence": 42,
        "action": TradeAction.ADD,
        "flags": None,
        "trade_source": None,
        "session_id": None,
        "trading_date": date(2026, 12, 1),
        "source_file": "data/raw/synthetic/synthetic.trades/gcz6/2026-12-01/day.bin",
        "source_record_id": "row-1",
        "source_row_no": 1,
        "schema_version": "trade_v1",
        "raw_payload_hash": sha256_hex(SYNTHETIC_PAYLOAD),
    }
    fields.update(overrides)
    return fields


def manifest_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "manifest_version": "acquisition_manifest_v1",
        "acquisition_id": "acq-synthetic-0001",
        "provider": "synthetic",
        "dataset": "synthetic.trades",
        "source_schema": "trades",
        "source_format_version": None,
        "venue": "COMEX",
        "canonical_contract": "COMEX:GC:2026-12-29",
        "trading_date": date(2026, 12, 1),
        "request_parameters": {"symbols": "GCZ6", "limit": 10, "compressed": False},
        "request_start_utc_ns": BASE_UTC_NS,
        "request_end_utc_ns": BASE_UTC_NS + 1,
        "retrieval_started_utc_ns": BASE_UTC_NS + 10,
        "retrieval_completed_utc_ns": BASE_UTC_NS + 20,
        "cost_ledger_request_id": "req-synthetic-0001",
        "estimated_cost": Decimal(0),
        "estimated_cost_currency": "USD",
        "actual_cost_if_known": None,
        "cost_decision": "FREE_CONFIRMED",
        "license_url": "https://example.invalid/license",
        "license_version": "synthetic-1",
        "response_status": 200,
        "response_metadata": {"content-type": "application/octet-stream"},
        "payload_path": "data/raw/synthetic/synthetic.trades/gcz6/2026-12-01/day.bin",
        "payload_bytes": len(SYNTHETIC_PAYLOAD),
        "payload_sha256": sha256_hex(SYNTHETIC_PAYLOAD),
        "publisher_version": "moex-arbitrage-lab-0.0.1",
    }
    fields.update(overrides)
    return fields


Factory = Callable[..., dict[str, object]]


@pytest.fixture
def make_trade_fields() -> Factory:
    return trade_fields


@pytest.fixture
def make_manifest_fields() -> Factory:
    return manifest_fields


@pytest.fixture
def synthetic_payload() -> bytes:
    return SYNTHETIC_PAYLOAD
