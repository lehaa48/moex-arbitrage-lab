"""Synthetic fixtures for the P1 T-Bank history adapter tests (workstream D).

Everything here is SYNTHETIC and built in memory: the ticker suffix, instrument UID, URLs
(``.invalid`` host, never resolved; the parent conftest refuses every socket anyway), prices,
timestamps and archives are invented and are not market data. No file with a market-data
suffix is left on disk: CSV payloads live in memory or are removed at teardown.

Shared helpers are exposed as fixtures (pytest runs with ``--import-mode=importlib``; test
modules never import each other). Test modules import the helper *types* below only under
``TYPE_CHECKING`` for annotations.
"""

import email.message
import gzip
import io
import stat
import struct
import types
import typing
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from moex_arbitrage_lab.sources.tbank_history import SessionWindow, TbankHistoryConfig

# --- synthetic identity and layout ----------------------------------------------------------------

SYNTHETIC_TICKER = "GDZ6_SPBFUT"  # synthetic test value shaped like the documented TICKER_CC
SYNTHETIC_OTHER_TICKER = "SVZ6_SPBFUT"  # synthetic "other instrument"
SYNTHETIC_UID = "00000000-0000-4000-8000-00000000d0d6"  # synthetic UUID, not a real instrument
SYNTHETIC_OTHER_UID = "00000000-0000-4000-8000-00000000beef"  # synthetic UUID
COLUMNS = (
    "TRADE_TS",
    "TICKER_CC",
    "DIRECTION",
    "PRICE",
    "QUANTITY",
    "TRADE_SOURCE",
    "INSTRUMENT_UID",
)
ARCHIVE_DATE = date(2026, 10, 15)
MEMBER_NAME = "synthetic-gdz6-20261015.csv"  # in-memory zip member only
MEMBER_PATTERN = r"synthetic-gdz6-\d{8}\.csv"
ZIP_RAW_FILENAME = "synthetic-gdz6-20261015.zip"
CSV_RAW_FILENAME = "synthetic-gdz6-20261015.csv"  # removed at teardown (see ``csv_cleanup``)
# Underscores are refused by the storage path grammar, hence hyphens (removed at teardown).
GZIP_RAW_FILENAME = "synthetic-gdz6-20261015.csv.gz"
RAW_FILENAMES = {"zip": ZIP_RAW_FILENAME, "csv": CSV_RAW_FILENAME, "gzip": GZIP_RAW_FILENAME}
CONTENT_TYPES = {
    "zip": ("application/zip", "application/octet-stream"),
    "csv": ("text/csv", "application/octet-stream"),
    "gzip": ("application/octet-stream", "application/gzip", "application/x-gzip"),
}
SYNTHETIC_URL = "https://synthetic.invalid/history-md/synthetic-gdz6-20261015.zip"


def utc_ns(hour: int, minute: int = 0, second: int = 0, nanos: int = 0) -> int:
    """Exact UTC nanoseconds on the synthetic archive date (integer arithmetic only)."""
    midnight = datetime(ARCHIVE_DATE.year, ARCHIVE_DATE.month, ARCHIVE_DATE.day, tzinfo=UTC)
    seconds = int(midnight.timestamp()) + hour * 3600 + minute * 60 + second
    return seconds * 1_000_000_000 + nanos


# Synthetic MOEX-like windows (UTC): morning 04:00-07:00, main 07:00-16:00 and an evening
# 16:00-20:50 that is assigned to the next trading date, so tests can tell that the trading
# date comes from the window, not from the calendar date of the timestamp.
MORNING = ("morning", utc_ns(4), utc_ns(7), ARCHIVE_DATE)
MAIN = ("main", utc_ns(7), utc_ns(16), ARCHIVE_DATE)
EVENING = ("evening", utc_ns(16), utc_ns(20, 50), date(2026, 10, 16))

DEFAULT_LAG_BUCKETS_NS = tuple(
    [ms * 1_000_000 for ms in (1, 5, 10, 25, 50, 100, 250, 500)]
    + [s * 1_000_000_000 for s in (1, 2, 5, 10, 30, 60, 120, 300)]
)


def session(name: str, start: int, end: int, trading_date: date) -> SessionWindow:
    return SessionWindow(name=name, start_utc_ns=start, end_utc_ns=end, trading_date=trading_date)


def _base_types(annotation: object) -> set[object]:
    """The concrete types behind ``X | None`` / ``Annotated[X, ...]`` annotations."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _base_types(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        found: set[object] = set()
        for argument in typing.get_args(annotation):
            found |= _base_types(argument)
        return found
    return {annotation}


def _coerce(name: str, value: object) -> object:
    """Convert a numeric fixture value to the numeric type the config field declares.

    The interface fixes the meaning of numeric fields but not whether e.g. a timeout is an
    ``int``, ``float`` or ``Decimal``; strict models refuse cross-type input, so the fixture
    adapts instead of guessing.
    """
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return value
    info = TbankHistoryConfig.model_fields.get(name)
    if info is None:
        return value
    types = _base_types(info.annotation)
    if Decimal in types:
        return Decimal(str(value))
    if float in types and int not in types:
        return float(value)
    return value


def base_config_values(payload_format: str = "zip") -> dict[str, object]:
    """Synthetic configuration values (python types; Decimals/dates/tuples)."""
    return {
        "dataset": "synthetic-anon-trades",
        "source_schema": "anonymized_trades",
        "source_format_version": None,
        "payload_format": payload_format,
        "archive_date": ARCHIVE_DATE,
        "url": SYNTHETIC_URL,
        # An unknown format (invalid-config tests) keeps zip values so the model refuses it.
        "raw_filename": RAW_FILENAMES.get(payload_format, ZIP_RAW_FILENAME),
        "ticker_cc": SYNTHETIC_TICKER,
        "instrument_uid": SYNTHETIC_UID,
        "canonical_contract": "MOEX:GD:2026-12-18",
        "expiry": date(2026, 12, 18),
        "currency": "USD",
        "price_unit": "USD per troy ounce",
        "contract_multiplier": Decimal("1"),
        "tick_size": Decimal("0.1"),
        "event_ts_type": "SOURCE_TRADE_TS_UTC",
        "clock_accuracy_documented": False,
        "trade_action": "ADD",
        "csv_delimiter": ",",
        "expected_columns": COLUMNS,
        "csv_member_pattern": MEMBER_PATTERN,
        "max_members": 1,
        "max_uncompressed_bytes": 1_000_000,
        "expected_content_types": CONTENT_TYPES.get(payload_format, CONTENT_TYPES["zip"]),
        "min_bytes": 64,
        "max_bytes": 65_536,
        "request_timeout_s": 30,
        "requests_per_minute": 30,
        "min_request_interval_s": 2,
        "sessions": (session(*MORNING), session(*MAIN), session(*EVENING)),
        "license_url": "https://synthetic.invalid/license",
        "zero_cost_evidence_url": "https://synthetic.invalid/zero-cost",
        "license_version": "synthetic-1",
        "cost_currency": "RUB",
    }


def build_config(payload_format: str = "zip", **overrides: object) -> TbankHistoryConfig:
    values = {**base_config_values(payload_format), **overrides}
    values = {name: _coerce(name, value) for name, value in values.items()}
    return TbankHistoryConfig.model_validate(values)


def render_toml(values: dict[str, object], extra: str = "") -> str:
    """Render synthetic config values as TOML (decimals as strings, dates as TOML dates,
    sessions as ``[[sessions]]`` tables)."""
    lines: list[str] = []
    for key, value in values.items():
        if key == "sessions" or value is None:
            continue
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, date):
            lines.append(f"{key} = {value.isoformat()}")
        elif isinstance(value, Decimal):
            lines.append(f'{key} = "{value}"')
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, tuple):
            items = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in value)
            lines.append(f"{key} = [{items}]")
        else:
            lines.append(f"{key} = '{value}'")
    lines.append(extra)
    sessions = values.get("sessions", ())
    assert isinstance(sessions, tuple)
    for window in sessions:
        lines += [
            "",
            "[[sessions]]",
            f"name = '{window.name}'",
            f"start_utc_ns = {window.start_utc_ns}",
            f"end_utc_ns = {window.end_utc_ns}",
            f"trading_date = {window.trading_date.isoformat()}",
        ]
    return "\n".join(lines) + "\n"


ConfigFactory = Callable[..., TbankHistoryConfig]


@pytest.fixture
def make_config() -> ConfigFactory:
    """``make_config(payload_format="zip", **overrides)`` -> validated synthetic config."""
    return build_config


@pytest.fixture
def config() -> TbankHistoryConfig:
    return build_config()


# --- CSV and archive builders ---------------------------------------------------------------------


def row(
    ts: str = "2026-10-15T07:02:29.315229Z",
    ticker: str = SYNTHETIC_TICKER,
    direction: str = "BUY",
    price: str = "4012.3",
    quantity: str = "1",
    source: str = "EXCHANGE",
    uid: str = SYNTHETIC_UID,
) -> list[str]:
    """One synthetic CSV row in documented column sequence."""
    return [ts, ticker, direction, price, quantity, source, uid]


def build_csv(
    rows: list[list[str]], header: tuple[str, ...] = COLUMNS, delimiter: str = ","
) -> bytes:
    """Synthetic CSV text; a field containing the delimiter is quoted."""

    def cell(value: str) -> str:
        return f'"{value}"' if delimiter in value else value

    lines = [delimiter.join(header), *(delimiter.join(cell(v) for v in r) for r in rows)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_zip(members: list[tuple[str, bytes]], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 15, 0, 0, 0))
            info.compress_type = compression
            archive.writestr(info, data)
    return buffer.getvalue()


def patch_zip_flags(payload: bytes, flag: int) -> bytes:
    """Set general-purpose flag bits in the first local and central header (e.g. 0x1 =
    encrypted) without encrypting anything."""
    data = bytearray(payload)
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    for offset in (local + 6, central + 8):
        (current,) = struct.unpack_from("<H", data, offset)
        struct.pack_into("<H", data, offset, current | flag)
    return bytes(data)


def patch_zip_declared_size(payload: bytes, declared: int) -> bytes:
    """Make the headers lie about the uncompressed size of the first member."""
    data = bytearray(payload)
    struct.pack_into("<I", data, data.index(b"PK\x03\x04") + 22, declared)
    struct.pack_into("<I", data, data.index(b"PK\x01\x02") + 24, declared)
    return bytes(data)


SYNTHETIC_CSV = build_csv(
    [
        row(ts="2026-10-15T07:02:29.315229Z", direction="BUY", price="4012.3", quantity="1"),
        row(ts="2026-10-15T07:02:30.000001Z", direction="SELL", price="4012.4", quantity="2"),
        row(ts="2026-10-15T07:02:31.500000Z", direction="BUY", price="4012.5", source="DEALER"),
        row(ts="2026-10-15T07:02:33.250000Z", direction="SELL", price="4012.2", quantity="3"),
    ]
)
SYNTHETIC_ZIP = build_zip([(MEMBER_NAME, SYNTHETIC_CSV)])


def build_gzip(data: bytes) -> bytes:
    """A single-member gzip stream with a fixed mtime (deterministic bytes)."""
    return gzip.compress(data, compresslevel=9, mtime=0)


def build_gzip_bomb(total_bytes: int, chunk: int = 1 << 20) -> bytes:
    """A gzip stream of ``total_bytes`` zero bytes, built in chunks so that the test itself
    never holds the inflated content in memory (synthetic zip-bomb equivalent for gzip)."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)  # wbits 31 = gzip container
    block = b"\x00" * chunk
    parts: list[bytes] = []
    crc = 0
    remaining = total_bytes
    while remaining > 0:
        piece = block if remaining >= chunk else block[:remaining]
        parts.append(compressor.compress(piece))
        crc = zlib.crc32(piece, crc)
        remaining -= len(piece)
    parts.append(compressor.flush())
    return b"".join(parts)


SYNTHETIC_GZIP = build_gzip(SYNTHETIC_CSV)


@dataclass(frozen=True)
class Builders:
    """Synthetic data builders handed to tests through the ``builders`` fixture."""

    row: Callable[..., list[str]] = row
    csv: Callable[..., bytes] = build_csv
    zip: Callable[..., bytes] = build_zip
    patch_zip_flags: Callable[[bytes, int], bytes] = patch_zip_flags
    patch_zip_declared_size: Callable[[bytes, int], bytes] = patch_zip_declared_size
    utc_ns: Callable[..., int] = utc_ns
    session: Callable[[str, int, int, date], SessionWindow] = session
    base_config_values: Callable[..., dict[str, object]] = base_config_values
    toml: Callable[..., str] = render_toml
    synthetic_csv: bytes = SYNTHETIC_CSV
    synthetic_zip: bytes = SYNTHETIC_ZIP
    synthetic_gzip: bytes = SYNTHETIC_GZIP
    gzip: Callable[[bytes], bytes] = build_gzip
    gzip_bomb: Callable[..., bytes] = build_gzip_bomb
    columns: tuple[str, ...] = COLUMNS
    ticker: str = SYNTHETIC_TICKER
    other_ticker: str = SYNTHETIC_OTHER_TICKER
    uid: str = SYNTHETIC_UID
    other_uid: str = SYNTHETIC_OTHER_UID
    member_name: str = MEMBER_NAME
    url: str = SYNTHETIC_URL
    archive_date: date = ARCHIVE_DATE
    lag_buckets_ns: tuple[int, ...] = DEFAULT_LAG_BUCKETS_NS
    windows: tuple[tuple[str, int, int, date], ...] = (MORNING, MAIN, EVENING)


@pytest.fixture
def builders() -> Builders:
    return Builders()


# --- fake HTTP opener and clock -------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for an ``http.client.HTTPResponse`` (synthetic)."""

    def __init__(
        self,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        endless: bool = False,
        read_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.headers = email.message.Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        self._body = io.BytesIO(body)
        self._endless = endless
        self._read_error = read_error
        self.bytes_read = 0
        self.closed = False

    def read(self, amount: int | None = -1) -> bytes:
        if self._read_error is not None:
            raise self._read_error
        if self._endless:
            # A server that never stops sending: honours ``amount`` exactly; an unbounded
            # read gets a large finite chunk so a broken reader fails instead of hanging.
            size = amount if amount is not None and amount > 0 else 4 * 1024 * 1024
            self.bytes_read += size
            return b"\0" * size
        data = self._body.read() if amount is None or amount < 0 else self._body.read(amount)
        self.bytes_read += len(data)
        return data

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


Responder = Callable[[urllib.request.Request], FakeResponse]


@dataclass
class FakeOpener:
    """Records every request; ``responder`` builds the response or raises (synthetic)."""

    responder: Responder
    requests: list[urllib.request.Request] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    on_open: list[Callable[[urllib.request.Request], None]] = field(default_factory=list)

    def open(self, request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        for hook in self.on_open:
            hook(request)
        return self.responder(request)

    @property
    def methods(self) -> list[str]:
        return [request.get_method() for request in self.requests]


def zip_ok_response(
    payload: bytes = SYNTHETIC_ZIP, content_type: str = "application/zip"
) -> Responder:
    def respond(request: urllib.request.Request) -> FakeResponse:
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
            "ETag": '"synthetic-etag"',
            "Set-Cookie": "synthetic-session=not-a-real-cookie",
            "X-Synthetic-Debug": "dropped",
        }
        body = b"" if request.get_method() == "HEAD" else payload
        return FakeResponse(200, headers, body)

    return respond


def refusing_responder(request: urllib.request.Request) -> FakeResponse:
    raise AssertionError(f"the opener must not be called ({request.get_method()})")


@dataclass
class OpenerKit:
    opener: Callable[[Responder], FakeOpener] = FakeOpener
    response: type[FakeResponse] = FakeResponse
    ok: Callable[..., Responder] = zip_ok_response
    refusing: Responder = refusing_responder


@pytest.fixture
def http() -> OpenerKit:
    return OpenerKit()


@dataclass
class FakeClock:
    """Deterministic ``time.time_ns`` replacement: advances ``step_ns`` per call."""

    now: int = utc_ns(9)
    step_ns: int = 1_000_000
    calls: int = 0

    def __call__(self) -> int:
        self.calls += 1
        current = self.now
        self.now += self.step_ns
        return current

    def advance(self, nanos: int) -> None:
        self.now += nanos


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# --- teardown: never leave a market-data-suffixed file in the project tmp -------------------------


def _remove_tree_files(root: Path, suffix: str) -> None:
    for path in root.rglob(f"*{suffix}"):
        if path.is_file() and not path.is_symlink():
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
            path.unlink()


@pytest.fixture
def csv_cleanup(tmp_path: Path) -> Iterator[None]:
    """Remove synthetic ``*.csv`` and ``*.gz`` payloads published into the synthetic repo at
    teardown, so the repository hygiene scan of ``tmp`` never sees a market-data suffix and no
    compressed synthetic CSV is left behind."""
    yield
    for suffix in (".csv", ".gz"):
        _remove_tree_files(tmp_path, suffix)


def json_values(data: Any) -> list[Any]:
    """All scalar values of a JSON document (recursive), for loose key-agnostic assertions."""
    if isinstance(data, dict):
        return [v for value in data.values() for v in json_values(value)]
    if isinstance(data, list):
        return [v for value in data for v in json_values(value)]
    return [data]


@pytest.fixture
def scalars() -> Callable[[Any], list[Any]]:
    return json_values
