"""Append-only Databento request ledger ``logs/databento/requests.jsonl`` (no secrets).

Every provider request is recorded *before* it is sent (event ``request``) and its outcome
afterwards (event ``response``); Step A/B also append ``cost_gate``, ``publication`` and
``step_b_complete`` events. Lines are only ever appended and flushed to disk; nothing is
rewritten. Keys that look like credentials are refused and every line is checked against the
caller's forbidden values (the API key) before it is written.

The ledger is also the memory that prevents a repeated metered request: a ``request`` event for
``batch.submit_job`` with the frozen request hash blocks any further job creation, whatever the
recorded outcome.
"""

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

from moex_arbitrage_lab.settings.secrets import is_secret_name
from moex_arbitrage_lab.settings.storage_paths import ProjectPaths, StorageCategory
from moex_arbitrage_lab.sources.databento_history.request import Endpoint

LEDGER_VERSION: Final = 1
LEDGER_RELATIVE: Final = "databento/requests.jsonl"


class LedgerError(RuntimeError):
    """The ledger is unreadable, corrupt, or an entry was refused."""


def ledger_path(paths: ProjectPaths) -> Path:
    return paths.resolve(StorageCategory.LOGS, LEDGER_RELATIVE)


def _check_keys(value: object, where: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or is_secret_name(key):
                raise LedgerError(f"ledger entry key refused at {where}: looks like a credential")
            _check_keys(item, f"{where}.{key}")
    elif isinstance(value, list | tuple):
        for item in value:
            _check_keys(item, where)


class RequestLedger:
    """JSON-lines ledger; ``forbidden`` values (the API key) can never be written."""

    def __init__(self, path: Path, forbidden: Iterable[str] = ()) -> None:
        self.path = path
        self._forbidden = tuple(value for value in forbidden if value)

    def __repr__(self) -> str:
        return f"RequestLedger(path={str(self.path)!r})"

    def forbid(self, values: Iterable[str]) -> None:
        """Add values that must never appear in a ledger line."""
        self._forbidden = (*self._forbidden, *(v for v in values if v))

    def entries(self) -> list[dict[str, object]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            raise LedgerError("request ledger is unreadable") from None
        entries: list[dict[str, object]] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                raise LedgerError(f"request ledger line {number} is corrupt") from None
            if not isinstance(entry, dict) or not isinstance(entry.get("event"), str):
                raise LedgerError(f"request ledger line {number} is corrupt")
            entries.append(entry)
        return entries

    def append(self, entry: Mapping[str, object]) -> dict[str, object]:
        """Append one line (flushed and fsync-ed) and return what was written."""
        record = {"ledger_version": LEDGER_VERSION, **entry}
        _check_keys(record, "entry")
        line = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        if any(value in line for value in self._forbidden):
            # The value is never echoed.
            raise LedgerError("ledger entry refused: it contains a forbidden secret value")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def next_request_id(self, utc_ns: int) -> str:
        return f"databento-{utc_ns}-{len(self.entries()) + 1}"

    # --- queries ------------------------------------------------------------------------------

    def job_creation_attempts(self, request_sha256: str) -> list[dict[str, object]]:
        """``request`` events of the metered job creation for ``request_sha256``."""
        return [
            entry
            for entry in self.entries()
            if entry.get("event") == "request"
            and entry.get("endpoint") == Endpoint.CREATE_BATCH_JOB.value
            and entry.get("request_sha256") == request_sha256
        ]

    def response_for(self, request_id: object) -> dict[str, object] | None:
        for entry in self.entries():
            if entry.get("event") == "response" and entry.get("request_id") == request_id:
                return entry
        return None

    def download_attempts(self, job_id: str, filename: str) -> list[dict[str, object]]:
        return [
            entry
            for entry in self.entries()
            if entry.get("event") == "request"
            and entry.get("endpoint") == Endpoint.DOWNLOAD_BATCH_FILE.value
            and entry.get("job_id") == job_id
            and entry.get("provider_filename") == filename
        ]

    def completion(self, request_sha256: str) -> dict[str, object] | None:
        """The last ``step_b_complete`` event of ``request_sha256``, if any."""
        found = None
        for entry in self.entries():
            if entry.get("event") == "step_b_complete" and (
                entry.get("request_sha256") == request_sha256
            ):
                found = entry
        return found
