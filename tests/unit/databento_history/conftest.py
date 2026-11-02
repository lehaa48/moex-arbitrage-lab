"""SYNTHETIC fixtures for the Databento adapter tests (offline).

Everything here is invented: the key (an obvious fake), instrument id, job id, account path,
prices and timestamps are not market data and not credentials. The fake provider answers by URL
path; the parent conftest refuses every socket anyway. Published synthetic ``*.csv`` files are
removed at teardown so the repository hygiene scan of ``tmp`` never sees a market-data suffix.
"""

import email.message
import hashlib
import io
import json
import stat
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from moex_arbitrage_lab.settings import ProjectPaths

FAKE_KEY = "db-synthetic-fake-key-0123456789abcdef"  # synthetic test value, never a real key
SYNTHETIC_INSTRUMENT_ID = 4242  # synthetic
SYNTHETIC_JOB_ID = "GLBX-20261102-SYNTHETIC1"  # synthetic
CSV_NAME = "glbx-mdp3-20260911-20261031.ohlcv-1s.csv"
AUX_NAME = "metadata.json"
START_NS = 1_789_084_800 * 1_000_000_000  # 2026-09-11T00:00:00Z
HEADER = "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol"


def synthetic_csv(rows: list[str] | None = None, header: str = HEADER) -> bytes:
    if rows is None:
        rows = [
            f"{START_NS + 3_600 * 10**9},32,1,{SYNTHETIC_INSTRUMENT_ID},"
            "2650.100000000,2650.400000000,2650.000000000,2650.300000000,3,GCZ6",
            f"{START_NS + 3_601 * 10**9},32,1,{SYNTHETIC_INSTRUMENT_ID},"
            "2650.300000000,2650.300000000,2650.200000000,2650.200000000,1,GCZ6",
            f"{START_NS + 3_605 * 10**9},32,1,{SYNTHETIC_INSTRUMENT_ID},"
            "2650.200000000,2650.600000000,2650.200000000,2650.500000000,7,GCZ6",
        ]
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


SYNTHETIC_AUX = json.dumps({"query": {"dataset": "GLBX.MDP3"}, "synthetic": True}).encode()


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"", content_type: str = "") -> None:
        self.status = status
        self.headers = email.message.Message()
        if content_type:
            self.headers["Content-Type"] = content_type
        self._body = io.BytesIO(body)

    def read(self, amount: int = -1) -> bytes:
        return self._body.read() if amount is None or amount < 0 else self._body.read(amount)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _json(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


@dataclass
class FakeProvider:
    """Synthetic Databento: per-path answers plus a log of every request."""

    cost_text: str = "0.0"
    step_b_cost_text: str | None = None
    schemas: list[str] = field(default_factory=lambda: ["mbo", "trades", "ohlcv-1s", "ohlcv-1m"])
    dataset_range: dict[str, object] = field(
        default_factory=lambda: {
            "start": "2010-06-06T00:00:00.000000000Z",
            "end": "2026-11-02T00:00:00.000000000Z",
            "schema": {
                "ohlcv-1s": {
                    "start": "2017-05-21T00:00:00.000000000Z",
                    "end": "2026-11-02T00:00:00.000000000Z",
                }
            },
        }
    )
    symbology: dict[str, object] = field(
        default_factory=lambda: {
            "result": {
                "GCZ6": [
                    {"d0": "2026-09-11", "d1": "2026-11-01", "s": str(SYNTHETIC_INSTRUMENT_ID)}
                ]
            },
            "symbols": ["GCZ6"],
            "stype_in": "raw_symbol",
            "stype_out": "instrument_id",
            "partial": [],
            "not_found": [],
        }
    )
    record_count: int = 1_234_567
    billable_size: int = 69_135_752
    job_states: list[str] = field(default_factory=lambda: ["queued", "processing", "done"])
    job_cost_text: str = "0.0"
    files: dict[str, bytes] = field(
        default_factory=lambda: {CSV_NAME: synthetic_csv(), AUX_NAME: SYNTHETIC_AUX}
    )
    listed_hashes: dict[str, str] = field(default_factory=dict)
    listed_sizes: dict[str, int] = field(default_factory=dict)
    download_host: str = "api.databento.com"
    fail_paths: dict[str, int] = field(default_factory=dict)
    raise_on: dict[str, BaseException] = field(default_factory=dict)
    create_response: dict[str, object] | None = None
    requests: list[urllib.request.Request] = field(default_factory=list)
    _poll: int = 0

    @property
    def paths(self) -> list[str]:
        return [urllib.parse.urlsplit(r.full_url).path for r in self.requests]

    def endpoint_paths(self) -> list[str]:
        return [p.removeprefix("/v0/") for p in self.paths]

    def listing(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for name, data in self.files.items():
            entries.append(
                {
                    "filename": name,
                    "size": self.listed_sizes.get(name, len(data)),
                    "hash": self.listed_hashes.get(
                        name, "sha256:" + hashlib.sha256(data).hexdigest()
                    ),
                    "urls": {
                        "https": f"https://{self.download_host}/v0/batch/download/"
                        f"synthetic-team/{SYNTHETIC_JOB_ID}/{name}",
                        "ftp": f"ftp://ftp.synthetic.invalid/{SYNTHETIC_JOB_ID}/{name}",
                    },
                }
            )
        return entries

    def job(self, state: str) -> dict[str, object]:
        return {
            "id": SYNTHETIC_JOB_ID,
            "state": state,
            "dataset": "GLBX.MDP3",
            "schema": "ohlcv-1s",
            "symbols": "GCZ6",
            "stype_in": "raw_symbol",
            "start": "2026-09-11T00:00:00.000000000Z",
            "end": "2026-11-01T00:00:00.000000000Z",
            "encoding": "csv",
            "compression": "none",
            "split_duration": "none",
            "cost_usd": json.loads(self.job_cost_text),
        }

    def __call__(self, request: urllib.request.Request) -> FakeResponse:
        self.requests.append(request)
        path = urllib.parse.urlsplit(request.full_url).path
        name = path.removeprefix("/v0/")
        if name in self.raise_on:
            raise self.raise_on[name]
        if name in self.fail_paths:
            return FakeResponse(self.fail_paths[name], b'{"detail":"synthetic failure"}')
        if name == "metadata.list_schemas":
            return FakeResponse(200, _json(self.schemas))
        if name == "metadata.get_dataset_range":
            return FakeResponse(200, _json(self.dataset_range))
        if name == "symbology.resolve":
            return FakeResponse(200, _json(self.symbology))
        if name == "metadata.get_record_count":
            return FakeResponse(200, _json(self.record_count))
        if name == "metadata.get_billable_size":
            return FakeResponse(200, _json(self.billable_size))
        if name == "metadata.get_cost":
            text = self.cost_text
            if self.step_b_cost_text is not None and any(
                p.endswith("metadata.get_cost") for p in self.paths[:-1]
            ):
                text = self.step_b_cost_text
            return FakeResponse(200, text.encode())
        if name == "batch.submit_job":
            document = self.create_response or self.job("received")
            return FakeResponse(200, _json(document))
        if name == "batch.list_jobs":
            state = self.job_states[min(self._poll, len(self.job_states) - 1)]
            self._poll += 1
            return FakeResponse(200, _json([self.job(state)]))
        if name == "batch.list_files":
            return FakeResponse(200, _json(self.listing()))
        if name.startswith("batch/download/"):
            filename = name.rsplit("/", 1)[1]
            return FakeResponse(200, self.files[filename], "application/octet-stream")
        raise AssertionError(f"unexpected synthetic path {path}")


@dataclass
class FakeOpener:
    responder: Callable[[urllib.request.Request], FakeResponse]
    timeouts: list[float] = field(default_factory=list)

    def open(self, request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        self.timeouts.append(timeout)
        return self.responder(request)


@dataclass
class FakeClock:
    now: int = START_NS + 52 * 86_400 * 10**9
    step_ns: int = 1_000_000

    def __call__(self) -> int:
        self.now += self.step_ns
        return self.now


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def opener(provider: FakeProvider) -> FakeOpener:
    return FakeOpener(provider)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def env() -> dict[str, str]:
    return {"DATABENTO_API_KEY": FAKE_KEY}


@pytest.fixture
def sleeps() -> list[float]:
    return []


def refusing_opener() -> FakeOpener:
    def refuse(request: urllib.request.Request) -> FakeResponse:
        raise AssertionError(f"the network must not be used ({request.full_url})")

    return FakeOpener(refuse)


@dataclass(frozen=True)
class Kit:
    fake_key: str = FAKE_KEY
    instrument_id: int = SYNTHETIC_INSTRUMENT_ID
    job_id: str = SYNTHETIC_JOB_ID
    csv_name: str = CSV_NAME
    aux_name: str = AUX_NAME
    start_ns: int = START_NS
    header: str = HEADER
    csv: Callable[..., bytes] = synthetic_csv
    aux: bytes = SYNTHETIC_AUX
    refusing_opener: Callable[[], FakeOpener] = refusing_opener
    provider: type[FakeProvider] = FakeProvider
    opener: type[FakeOpener] = FakeOpener
    response: type[FakeResponse] = FakeResponse


@pytest.fixture
def kit() -> Kit:
    return Kit()


def write_approval(
    directory: Path, report_path: Path, report: dict[str, object], **overrides: object
) -> Path:
    """The user's approval file (written here by the TEST, standing in for the user).

    ``overrides`` replace fields; the value ``DROP`` (``"<drop>"``) removes the field."""
    estimate = report["estimate_usd"]
    assert isinstance(estimate, str)
    document: dict[str, object] = {
        "approval_version": "databento_step_b_approval_v1",
        "request_sha256": report["request_sha256"],
        "step_a_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "approved_estimate_usd": estimate,
        "approved_billable_size": report["billable_size_bytes"],
        "max_cash_charge_usd": "0",
        "approved_by": "synthetic-user",
        "approved_at_utc": "2026-11-02T12:00:00Z",
        "explicit_user_approval": True,
    }
    if Decimal(estimate) > 0:
        document["user_states_credit_covers_estimate"] = True
    document.update(overrides)
    document = {k: v for k, v in document.items() if v != DROP}
    path = directory / "approval.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


DROP = "<drop>"


@pytest.fixture
def approve() -> Callable[..., Path]:
    return write_approval


def all_text_under(root: Path) -> str:
    """Every readable file below ``root`` concatenated (to search for leaked secrets)."""
    parts: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            parts.append(path.read_bytes().decode("utf-8", "replace"))
    return "\n".join(parts)


@pytest.fixture
def text_under() -> Callable[[Path], str]:
    return all_text_under


@pytest.fixture(autouse=True)
def _remove_synthetic_csv(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*.csv"):
        if path.is_file() and not path.is_symlink():
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
            path.unlink()


@pytest.fixture
def repo_paths(paths: ProjectPaths) -> ProjectPaths:
    return paths
