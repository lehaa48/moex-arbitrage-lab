"""Bounded, rate-limited, unauthenticated HTTP access to exactly one configured daily file.

- Only ``config.url`` is ever requested, with ``HEAD`` or ``GET``; the only request headers are
  ``REQUEST_HEADERS`` (never Authorization, Cookie or any credential; no secret is read).
- Every request is checked against and recorded in a JSON-lines ledger *before* it is sent,
  so failed requests still count toward the provider limit of 30 files/min/IP.
- Redirects are refused; environment proxies are not used (no proxy credentials can be sent).
- Responses fail closed with a stable ``FetchRejectedError.reason``; only allowlisted response
  headers are kept.

The network is reached only through the injectable ``opener``; tests use fakes.
"""

import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Final, Literal, Protocol

from moex_arbitrage_lab.settings.storage_paths import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.tbank_history.archive import looks_like_html
from moex_arbitrage_lab.sources.tbank_history.config import TbankHistoryConfig

FETCH_REJECTION_REASONS: Final = (
    "rate_limited",
    "redirect_refused",
    "auth_required",
    "http_status",
    "content_type",
    "too_large",
    "too_small",
    "html_payload",
    "timeout",
    "network_error",
)
USER_AGENT: Final = "moex-arbitrage-lab/0.0.1 (research; zero-cost)"
REQUEST_HEADERS: Final = {
    "User-Agent": USER_AGENT,
    # Same as the provider's official example scripts (plain curl / Invoke-WebRequest): the
    # container (ZIP vs CSV) is contradictory in the docs, so content negotiation must not
    # cause a 406. The response Content-Type is still checked against the config allowlist.
    "Accept": "*/*",
}
ALLOWED_RESPONSE_HEADERS: Final = (
    "content-type",
    "content-length",
    "etag",
    "last-modified",
    "date",
    "cache-control",
    "content-disposition",
    "accept-ranges",
    "server",
)
_WINDOW_NS: Final = 60 * 1_000_000_000
_READ_CHUNK: Final = 1 << 20
_MAX_HEADER_VALUE: Final = 256


class FetchRejectedError(RuntimeError):
    """The request was refused or its response rejected; ``reason`` is a stable code.

    ``http_status`` is the provider's HTTP status when the rejection is a status code
    (e.g. ``404`` = "file not found" per the archive documentation), else ``None``."""

    def __init__(self, reason: str, detail: str = "", *, http_status: int | None = None) -> None:
        if reason not in FETCH_REJECTION_REASONS:
            raise ValueError(f"unknown fetch rejection reason: {reason!r}")
        self.reason = reason
        self.http_status = http_status
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    method: str
    status: int
    headers: dict[str, str]
    body: bytes
    started_utc_ns: int
    completed_utc_ns: int


class FetchResponse(Protocol):
    """The part of an HTTP response this module uses (``http.client.HTTPResponse`` fits)."""

    @property
    def status(self) -> int: ...

    # Mapping-like with ``.get(name)`` and ``.items()`` (``email.message.Message`` fits).
    @property
    def headers(self) -> Any: ...

    def read(self, n: int, /) -> bytes: ...

    def __enter__(self) -> "FetchResponse": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        /,
    ) -> bool | None: ...


class Opener(Protocol):
    def open(self, request: urllib.request.Request, timeout: float) -> FetchResponse: ...


# --- request ledger ---------------------------------------------------------------------------


def _min_interval_ns(config: TbankHistoryConfig) -> int:
    seconds = Decimal(repr(config.min_request_interval_s))
    return int((seconds * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))


class RequestLedger:
    """Append-only JSON-lines record of every request attempt (no headers, no secrets)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _times(self) -> list[int]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            raise FetchRejectedError("rate_limited", "request ledger is unreadable") from None
        times: list[int] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)["utc_ns"]
            except (ValueError, KeyError, TypeError):
                raise FetchRejectedError("rate_limited", "request ledger is corrupt") from None
            if not isinstance(value, int) or isinstance(value, bool):
                raise FetchRejectedError("rate_limited", "request ledger is corrupt")
            times.append(value)
        return times

    def check_allowed(self, now_utc_ns: int, config: TbankHistoryConfig) -> None:
        """Raise ``FetchRejectedError("rate_limited")`` unless a new request is allowed now."""
        times = self._times()
        if not times:
            return
        if max(times) > now_utc_ns:
            raise FetchRejectedError("rate_limited", "ledger has a request in the future")
        if now_utc_ns - max(times) < _min_interval_ns(config):
            raise FetchRejectedError("rate_limited", "minimum request interval not elapsed")
        recent = sum(1 for t in times if now_utc_ns - t < _WINDOW_NS)
        if recent >= config.requests_per_minute:
            raise FetchRejectedError("rate_limited", "requests_per_minute reached")

    def wait_ns(self, now_utc_ns: int, config: TbankHistoryConfig) -> int:
        """Nanoseconds until ``check_allowed`` passes (``0`` = allowed now).

        Both limits are honoured: the minimum interval after the latest request and at most
        ``requests_per_minute`` requests in any half-open 60 s window. A corrupt ledger or a
        ledger entry in the future raises ``FetchRejectedError("rate_limited")``."""
        times = self._times()
        if not times:
            return 0
        latest = max(times)
        if latest > now_utc_ns:
            raise FetchRejectedError("rate_limited", "ledger has a request in the future")
        wait = max(0, latest + _min_interval_ns(config) - now_utc_ns)
        recent = sorted(t for t in times if now_utc_ns - t < _WINDOW_NS)
        excess = len(recent) - config.requests_per_minute
        if excess >= 0:
            # The oldest ``excess + 1`` recent requests must leave the window.
            wait = max(wait, recent[excess] + _WINDOW_NS - now_utc_ns)
        return wait

    def record(self, *, utc_ns: int, method: str, url: str, outcome: str) -> None:
        """Append one line and flush it to disk."""
        line = json.dumps(
            {"method": method, "outcome": outcome, "url": url, "utc_ns": utc_ns}, sort_keys=True
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def ledger_path(paths: ProjectPaths) -> Path:
    return paths.resolve(StorageCategory.LOGS, "tbank/requests.jsonl")


# --- default opener ---------------------------------------------------------------------------


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        fp.close()
        raise FetchRejectedError("redirect_refused", f"HTTP {code} redirect refused")


class _DefaultOpener:
    def __init__(self) -> None:
        self._director = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RefuseRedirects(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def open(self, request: urllib.request.Request, timeout: float) -> FetchResponse:
        response: FetchResponse = self._director.open(request, timeout=timeout)
        return response


def default_opener() -> Opener:
    return _DefaultOpener()


# --- fetch ------------------------------------------------------------------------------------


def _allowlisted(headers: Any) -> dict[str, str]:
    kept: dict[str, list[str]] = {}
    for name, value in headers.items():
        key = str(name).lower()
        if key in ALLOWED_RESPONSE_HEADERS:
            kept.setdefault(key, []).append(str(value))
    result: dict[str, str] = {}
    for key in sorted(kept):
        value = ", ".join(kept[key])
        printable = value and all(c.isprintable() for c in value)
        result[key] = (
            value if printable and len(value) <= _MAX_HEADER_VALUE else "<omitted:unrepresentable>"
        )
    return result


def _status_error(status: int) -> FetchRejectedError:
    if status in (401, 403):
        return FetchRejectedError("auth_required", f"HTTP {status}", http_status=status)
    if status == 429:
        return FetchRejectedError("rate_limited", "HTTP 429 from provider", http_status=status)
    if 300 <= status < 400:
        return FetchRejectedError("redirect_refused", f"HTTP {status}", http_status=status)
    return FetchRejectedError("http_status", f"HTTP {status}", http_status=status)


def _check_headers(headers: dict[str, str], config: TbankHistoryConfig) -> int | None:
    media = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media == "text/html":
        raise FetchRejectedError("html_payload", "Content-Type is text/html")
    if media not in config.expected_content_types:
        raise FetchRejectedError("content_type", f"unexpected Content-Type {media!r}")
    length_text = headers.get("content-length")
    if length_text is None:
        return None
    if not length_text.isascii() or not length_text.isdigit():
        raise FetchRejectedError("network_error", "invalid Content-Length")
    length = int(length_text)
    if length > config.max_bytes:
        raise FetchRejectedError("too_large", f"Content-Length {length} > {config.max_bytes}")
    return length


def _read_body(response: FetchResponse, config: TbankHistoryConfig) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= config.max_bytes:
        chunk = response.read(min(_READ_CHUNK, config.max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > config.max_bytes:
        raise FetchRejectedError("too_large", f"body exceeds {config.max_bytes} bytes")
    return b"".join(chunks)


def fetch(
    method: Literal["HEAD", "GET"],
    config: TbankHistoryConfig,
    *,
    ledger: RequestLedger,
    opener: Opener | None = None,
    clock: Callable[[], int] = time.time_ns,
) -> FetchResult:
    """One bounded request to ``config.url``; raises ``FetchRejectedError`` on any refusal."""
    if method not in ("HEAD", "GET"):
        raise ValueError("method must be HEAD or GET")
    active = opener if opener is not None else default_opener()
    now = clock()
    ledger.check_allowed(now, config)
    ledger.record(utc_ns=now, method=method, url=config.url, outcome="sent")
    request = urllib.request.Request(  # noqa: S310 - config.url is validated https only
        config.url, headers=dict(REQUEST_HEADERS), method=method
    )
    try:
        with active.open(request, timeout=config.request_timeout_s) as response:
            status = int(response.status)
            if status != 200:
                raise _status_error(status)
            headers = _allowlisted(response.headers)
            declared = _check_headers(headers, config)
            body = b"" if method == "HEAD" else _read_body(response, config)
    except FetchRejectedError:
        raise
    except urllib.error.HTTPError as error:
        error.close()
        raise _status_error(error.code) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise FetchRejectedError("timeout", "request timed out") from None
        raise FetchRejectedError("network_error", type(error.reason).__name__) from None
    except TimeoutError:
        raise FetchRejectedError("timeout", "request timed out") from None
    except (OSError, http.client.HTTPException) as error:
        raise FetchRejectedError("network_error", type(error).__name__) from None
    completed = clock()
    if method == "GET":
        if declared is not None and len(body) != declared:
            raise FetchRejectedError("network_error", "body length differs from Content-Length")
        if len(body) < config.min_bytes:
            raise FetchRejectedError("too_small", f"{len(body)} < {config.min_bytes} bytes")
        if looks_like_html(body):
            raise FetchRejectedError("html_payload", "body looks like an HTML/text page")
    return FetchResult(
        url=config.url,
        method=method,
        status=status,
        headers=headers,
        body=body,
        started_utc_ns=now,
        completed_utc_ns=completed,
    )
