"""Minimal Databento Historical HTTP client (stdlib ``urllib``) behind a per-step allowlist.

- The API key is read from the environment (``DATABENTO_API_KEY``) at each call, used only to
  build an HTTP Basic ``Authorization`` header (key as user name, empty password) that is never
  forwarded on a redirect, and never stored on the client, written to the ledger or included in
  an error/repr. Errors are raised ``from None`` and their text is scrubbed of the key and of
  its Basic encoding.
- ``call_json`` accepts an ``Endpoint`` only; parameters come from the frozen builders in
  ``request.py``. An endpoint outside ``STEP_ENDPOINTS[step]`` is refused before the ledger or
  the network is touched.
- The metered job creation can be sent once per frozen request, ever: a previous ledger
  ``request`` event for it refuses any further attempt, whatever its outcome.
- Every request is appended to the ledger before it is sent, its outcome after.
- Redirects and environment proxies are refused; bodies are size-bounded; non-200 fails closed.

The network is reached only through the injectable ``opener``; tests use fakes.
"""

import base64
import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from decimal import Decimal
from types import TracebackType
from typing import IO, Any, Final, Protocol

from moex_arbitrage_lab.sources.databento_history.ledger import RequestLedger
from moex_arbitrage_lab.sources.databento_history.request import (
    API_KEY_ENV_NAME,
    BASE_URL,
    DOWNLOAD_HOST,
    DOWNLOAD_PATH_PREFIX,
    ENDPOINT_METHOD,
    JOB_ID_PATTERN,
    METERED_ENDPOINTS,
    REQUEST_SHA256,
    STEP_ENDPOINTS,
    Endpoint,
    Step,
    endpoint_params,
)

USER_AGENT: Final = "moex-arbitrage-lab/0.0.1 (research; cost-gated)"
METADATA_TIMEOUT_S: Final = 60.0
DOWNLOAD_TIMEOUT_S: Final = 900.0
MAX_JSON_BYTES: Final = 4 * 1024 * 1024
_READ_CHUNK: Final = 1 << 20
_MAX_DETAIL: Final = 200
REDACTED: Final = "<redacted>"

CLIENT_ERROR_REASONS: Final = (
    "missing_api_key",
    "endpoint_not_allowed",
    "metered_repeat_refused",
    "auth_required",
    "rate_limited",
    "redirect_refused",
    "http_status",
    "timeout",
    "network_error",
    "too_large",
    "size_mismatch",
    "html_payload",
    "bad_response",
    "download_url_refused",
)


class DatabentoError(RuntimeError):
    """A provider request was refused or failed; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


class MissingApiKeyError(DatabentoError):
    def __init__(self) -> None:
        super().__init__("missing_api_key", f"{API_KEY_ENV_NAME} not set")


class EndpointNotAllowedError(DatabentoError):
    pass


def read_api_key(env: Mapping[str, str]) -> str:
    """The key from ``env`` (normally ``os.environ``) or ``MissingApiKeyError``; never logged."""
    value = env.get(API_KEY_ENV_NAME, "")
    if not value or not value.strip():
        raise MissingApiKeyError
    return value


def _basic_credentials(key: str) -> str:
    """``Basic base64(key + ":")``: key as user name, empty password."""
    return "Basic " + base64.b64encode(f"{key}:".encode()).decode("ascii")


def _secret_forms(key: str) -> tuple[str, ...]:
    encoded = base64.b64encode(f"{key}:".encode()).decode("ascii")
    return (key, encoded, _basic_credentials(key))


def scrub(text: str, secrets: tuple[str, ...]) -> str:
    """Remove every secret form from ``text`` (longest first)."""
    for value in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text


class ProviderResponse(Protocol):
    """The part of an HTTP response used here (``http.client.HTTPResponse`` fits)."""

    @property
    def status(self) -> int: ...

    @property
    def headers(self) -> Any: ...

    def read(self, n: int, /) -> bytes: ...

    def __enter__(self) -> "ProviderResponse": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        /,
    ) -> bool | None: ...


class Opener(Protocol):
    def open(self, request: urllib.request.Request, timeout: float) -> ProviderResponse: ...


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
        raise DatabentoError("redirect_refused", f"HTTP {code} redirect refused")


class _DefaultOpener:
    def __init__(self) -> None:
        self._director = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RefuseRedirects(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def open(self, request: urllib.request.Request, timeout: float) -> ProviderResponse:
        response: ProviderResponse = self._director.open(request, timeout=timeout)
        return response


def default_opener() -> Opener:
    return _DefaultOpener()


def _status_error(status: int, detail: str = "") -> DatabentoError:
    suffix = f" {detail}" if detail else ""
    if status in (401, 403):
        return DatabentoError("auth_required", f"HTTP {status}{suffix}")
    if status == 429:
        return DatabentoError("rate_limited", f"HTTP 429{suffix}")
    if 300 <= status < 400:
        return DatabentoError("redirect_refused", f"HTTP {status}")
    return DatabentoError("http_status", f"HTTP {status}{suffix}")


def _printable(text: str) -> str:
    cleaned = "".join(c if c.isprintable() else " " for c in text)
    return cleaned[:_MAX_DETAIL]


def _read_bounded(response: ProviderResponse, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = response.read(min(_READ_CHUNK, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > limit:
        raise DatabentoError("too_large", f"body exceeds {limit} bytes")
    return b"".join(chunks)


def looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<!doctype", b"<html", b"<head", b"<body", b"<?xml", b"<"))


def parse_json_exact(data: bytes) -> object:
    """JSON with every non-integer number as ``Decimal`` (no binary floats)."""
    if looks_like_html(data):
        raise DatabentoError("html_payload", "response body is HTML")
    try:
        return json.loads(data.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, ValueError):
        raise DatabentoError("bad_response", "response is not valid JSON") from None


def check_download_url(url: str, job_id: str, filename: str) -> str:
    """Refuse URLs outside ``https://DOWNLOAD_HOST/v0/batch/download/.../<job>/<file>``."""
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise DatabentoError("download_url_refused", "unexpected job id form")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise DatabentoError("download_url_refused", "unparsable URL") from None
    if (
        parts.scheme != "https"
        or parts.hostname != DOWNLOAD_HOST
        or parts.netloc != DOWNLOAD_HOST
        or port is not None
        or parts.query
        or parts.fragment
        or not parts.path.startswith(DOWNLOAD_PATH_PREFIX)
        or f"/{job_id}/" not in parts.path
        or not parts.path.endswith(f"/{filename}")
        or ".." in parts.path.split("/")
    ):
        raise DatabentoError("download_url_refused", "URL is outside the allowed download path")
    return url


class HistoricalClient:
    """One client per step; it never holds the key (read from ``env`` per request)."""

    def __init__(
        self,
        *,
        step: Step,
        ledger: RequestLedger,
        opener: Opener | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        if step not in STEP_ENDPOINTS:
            raise ValueError("step must be 'A' or 'B'")
        self.step: Step = step
        self._ledger = ledger
        self._opener = opener
        self._env: Mapping[str, str] = os.environ if env is None else env
        self._clock = clock
        self.sent: list[Endpoint] = []
        self.last_request_id: str | None = None

    def __repr__(self) -> str:
        return f"HistoricalClient(step={self.step!r}, sent={len(self.sent)})"

    def allowed(self) -> frozenset[Endpoint]:
        return STEP_ENDPOINTS[self.step]

    def _require_allowed(self, endpoint: Endpoint) -> None:
        if endpoint not in self.allowed():
            raise EndpointNotAllowedError(
                "endpoint_not_allowed", f"{endpoint.value} is not allowed in step {self.step}"
            )
        if endpoint in METERED_ENDPOINTS:
            if any(e is endpoint for e in self.sent):
                raise DatabentoError("metered_repeat_refused", f"{endpoint.value} already sent")
            if self._ledger.job_creation_attempts(REQUEST_SHA256):
                raise DatabentoError(
                    "metered_repeat_refused",
                    f"{endpoint.value} for this request is already in the ledger",
                )

    def call_json(self, endpoint: Endpoint, *, job_id: str | None = None) -> object:
        """Send one allowlisted metadata/batch call and return its JSON (exact decimals)."""
        if endpoint is Endpoint.DOWNLOAD_BATCH_FILE:
            raise EndpointNotAllowedError("endpoint_not_allowed", "use download() for files")
        self._require_allowed(endpoint)
        params = endpoint_params(endpoint, job_id)
        method = ENDPOINT_METHOD[endpoint]
        encoded = urllib.parse.urlencode(params)
        url = BASE_URL + endpoint.value
        body: bytes | None = None
        if method == "GET":
            url = f"{url}?{encoded}"
        else:
            body = encoded.encode("ascii")
        ledger_fields: dict[str, object] = {"params": params}
        if job_id is not None:
            ledger_fields["job_id"] = job_id
        data = self._send(
            endpoint,
            method,
            url,
            body,
            limit=MAX_JSON_BYTES,
            timeout=METADATA_TIMEOUT_S,
            accept="application/json",
            ledger_fields=ledger_fields,
        )
        return parse_json_exact(data)

    def download(self, url: str, *, job_id: str, filename: str, expected_size: int) -> bytes:
        """GET one batch file of ``job_id``; its size must equal ``expected_size`` exactly."""
        endpoint = Endpoint.DOWNLOAD_BATCH_FILE
        self._require_allowed(endpoint)
        checked = check_download_url(url, job_id, filename)
        data = self._send(
            endpoint,
            "GET",
            checked,
            None,
            limit=expected_size,
            timeout=DOWNLOAD_TIMEOUT_S,
            accept="*/*",
            # The URL may carry an account path segment: only job id and file name are logged.
            ledger_fields={
                "job_id": job_id,
                "provider_filename": filename,
                "expected_size": expected_size,
            },
        )
        if len(data) != expected_size:
            raise DatabentoError(
                "size_mismatch", f"{filename}: {len(data)} bytes, provider listed {expected_size}"
            )
        return data

    # --- transport ----------------------------------------------------------------------------

    def _send(
        self,
        endpoint: Endpoint,
        method: str,
        url: str,
        body: bytes | None,
        *,
        limit: int,
        timeout: float,
        accept: str,
        ledger_fields: Mapping[str, object],
    ) -> bytes:
        key = read_api_key(self._env)
        secrets = _secret_forms(key)
        self._ledger.forbid(secrets)
        now = self._clock()
        request_id = self._ledger.next_request_id(now)
        logged_url = url if endpoint is not Endpoint.DOWNLOAD_BATCH_FILE else None
        self._ledger.append(
            {
                "event": "request",
                "request_id": request_id,
                "step": self.step,
                "endpoint": endpoint.value,
                "method": method,
                "url": logged_url,
                "metered": endpoint in METERED_ENDPOINTS,
                "request_sha256": REQUEST_SHA256,
                "utc_ns": now,
                **ledger_fields,
            }
        )
        self.sent.append(endpoint)
        self.last_request_id = request_id
        request = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - https
        request.add_unredirected_header("Authorization", _basic_credentials(key))
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", accept)
        if body is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        del key
        opener = self._opener if self._opener is not None else default_opener()
        status: int | None = None
        try:
            try:
                with opener.open(request, timeout=timeout) as response:
                    status = int(response.status)
                    if status != 200:
                        raise _status_error(status)
                    data = _read_bounded(response, limit)
            except DatabentoError:
                raise
            except urllib.error.HTTPError as error:
                status = error.code
                detail = ""
                try:
                    detail = _printable(error.read(4096).decode("utf-8", "replace"))
                except (OSError, http.client.HTTPException):
                    detail = ""
                finally:
                    error.close()
                raise _status_error(error.code, detail) from None
            except urllib.error.URLError as error:
                if isinstance(error.reason, TimeoutError):
                    raise DatabentoError("timeout", "request timed out") from None
                raise DatabentoError("network_error", type(error.reason).__name__) from None
            except TimeoutError:
                raise DatabentoError("timeout", "request timed out") from None
            except (OSError, http.client.HTTPException) as error:
                raise DatabentoError("network_error", type(error).__name__) from None
        except DatabentoError as error:
            message = scrub(str(error), secrets)
            self._ledger.append(
                {
                    "event": "response",
                    "request_id": request_id,
                    "endpoint": endpoint.value,
                    "status": status,
                    "outcome": "error",
                    "reason": error.reason,
                    "detail": message,
                    "utc_ns": self._clock(),
                }
            )
            raise DatabentoError(error.reason, scrub(_detail_of(error), secrets)) from None
        self._ledger.append(
            {
                "event": "response",
                "request_id": request_id,
                "endpoint": endpoint.value,
                "status": status,
                "outcome": "ok",
                "bytes": len(data),
                "utc_ns": self._clock(),
            }
        )
        return data


def _detail_of(error: DatabentoError) -> str:
    text = str(error)
    prefix = f"{error.reason}: "
    return text[len(prefix) :] if text.startswith(prefix) else ""
