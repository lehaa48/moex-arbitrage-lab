"""Create-once outputs, integrity checks and month-guarded loading for the seconds screen.

Layout (all through the storage guard ``ProjectPaths``):

- seconds run: ``data/aligned/h0001-seconds-v0-2/<run_id>/{moex,comex}/<date>.csv`` and
  ``seconds-manifest.json`` (+ ``.sha256``);
- analysis run: ``reports/generated/h0001-seconds-v0-2/<run_id>/discovery.json``,
  ``confirmation.json`` (+ ``.sha256``) and CSV tables;
- confirmation ledger: ``reports/generated/h0001-seconds-v0-2/ledger/
  <discovery_sha256>.opened.json`` - written create-once BEFORE any October outcome is
  computed, so a discovery can be confirmed at most once even across analysis run ids.

Every file is written exclusively to a staging name, hard-linked to its final name (``os.link``
never replaces an existing file), re-read and verified, then marked read-only.
"""

import hashlib
import json
import math
import os
import re
import stat
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Final

from moex_arbitrage_lab.research.seconds_analysis import AnalysisDay, VenueDayStates
from moex_arbitrage_lab.research.seconds_config import (
    NS_PER_SECOND,
    month_of,
    month_window_ns,
)
from moex_arbitrage_lab.research.seconds_series import (
    CommonSegment,
    seconds_from_csv_light,
)
from moex_arbitrage_lab.settings.storage_paths import (
    STORAGE_LAYOUT,
    ProjectPaths,
    StorageCategory,
    StoragePathError,
)

SCREEN_DIR: Final = "h0001-seconds-v0-2"
LEDGER_DIR: Final = "ledger"
SECONDS_MANIFEST: Final = "seconds-manifest.json"
SECONDS_MANIFEST_VERSION: Final = "h0001_seconds_manifest_v1"
RUN_ID: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CODE_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_O_BINARY: Final = getattr(os, "O_BINARY", 0)
_CONFIG_SEGMENT: Final = re.compile(r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9]+)?$")


class StoreError(RuntimeError):
    """An output or input violates the storage/integrity rules."""


class CreateOnceError(StoreError):
    """The target already exists; outputs are never overwritten."""


class IntegrityError(StoreError):
    """A file does not match its recorded SHA-256 or identity."""


class HoldoutGuardError(StoreError):
    """A step tried to read or reopen data it is not allowed to see."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    """Deterministic JSON (sorted keys, no NaN/Infinity)."""
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def finite_json(value: object) -> object:
    """Recursively replace non-finite floats (NaN, +/-inf) by None (an undefined statistic),
    so an artifact can always be written once its outcome has been computed."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: finite_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [finite_json(item) for item in value]
    return value


def check_run_id(run_id: str) -> str:
    if not RUN_ID.fullmatch(run_id):
        raise StoreError(f"run id must match {RUN_ID.pattern}")
    if run_id == LEDGER_DIR:
        raise StoreError(f"run id {LEDGER_DIR!r} is reserved for the confirmation ledger")
    return run_id


def check_code_commit(commit: str) -> str:
    if not CODE_COMMIT.fullmatch(commit):
        raise StoreError("code commit must be a full 40-character lowercase Git SHA")
    return commit


def code_sha256() -> str:
    """Content identity of the research package source (sorted ``*.py``, name + bytes)."""
    package = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(package.glob("*.py")):
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def utc_now_text() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- create-once writing -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WrittenFile:
    path: str
    sha256: str
    bytes: int

    def as_json(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes}


def write_create_once(
    paths: ProjectPaths, category: StorageCategory, relative: str, data: bytes
) -> WrittenFile:
    target = paths.resolve(category, relative)
    if target.exists() or target.is_symlink():
        raise CreateOnceError(f"refusing to overwrite {paths.relative_to_root(target)}")
    staging = paths.resolve(category, f"{relative}.{uuid.uuid4().hex[:16]}.part")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(staging, target)
        except FileExistsError:
            raise CreateOnceError(
                f"refusing to overwrite {paths.relative_to_root(target)}"
            ) from None
    finally:
        staging.unlink(missing_ok=True)
    expected = sha256_bytes(data)
    if sha256_bytes(target.read_bytes()) != expected:
        raise IntegrityError(f"written file does not verify: {target}")
    target.chmod(stat.S_IREAD)
    return WrittenFile(paths.relative_to_root(target), expected, len(data))


def write_json_with_checksum(
    paths: ProjectPaths, category: StorageCategory, relative: str, value: object
) -> WrittenFile:
    data = json_bytes(finite_json(value))
    written = write_create_once(paths, category, relative, data)
    name = PurePosixPath(relative).name
    write_create_once(
        paths, category, f"{relative}.sha256", f"{written.sha256}  {name}\n".encode("ascii")
    )
    return written


def read_json_with_checksum(
    paths: ProjectPaths, category: StorageCategory, relative: str
) -> tuple[dict[str, object], str]:
    """Read a JSON artifact, verify it against its ``.sha256`` file, return (value, sha)."""
    target = paths.resolve(category, relative)
    checksum = paths.resolve(category, f"{relative}.sha256")
    if not target.is_file() or not checksum.is_file():
        raise IntegrityError(f"missing artifact or checksum: {relative}")
    data = target.read_bytes()
    recorded = checksum.read_text(encoding="ascii").split()[0]
    digest = sha256_bytes(data)
    if digest != recorded:
        raise IntegrityError(f"{relative}: SHA-256 does not match its checksum file")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise IntegrityError(f"{relative}: not a JSON object")
    return value, digest


def read_verified(paths: ProjectPaths, relative_to_root: str, sha256: str) -> bytes:
    path = resolve_project_file(paths, relative_to_root)
    data = path.read_bytes()
    if sha256_bytes(data) != sha256:
        raise IntegrityError(f"{relative_to_root}: SHA-256 mismatch")
    return data


def resolve_project_file(paths: ProjectPaths, relative_to_root: str) -> Path:
    """Map a root-relative POSIX path onto its storage category and resolve it through the
    storage guard (inputs must live in a project storage location)."""
    for category, prefix in STORAGE_LAYOUT.items():
        head = f"{prefix.as_posix()}/"
        if relative_to_root.startswith(head):
            return paths.resolve(category, relative_to_root[len(head) :])
    raise StoragePathError(f"not inside a project storage location: {relative_to_root!r}")


def resolve_config_file(paths: ProjectPaths, relative_to_root: str) -> Path:
    """Config files live under ``config/``; no links, no escape."""
    segments = relative_to_root.split("/")
    if len(segments) < 2 or segments[0] != "config":
        raise StoragePathError("config path must be config/<...>")
    if not all(_CONFIG_SEGMENT.fullmatch(segment) for segment in segments):
        raise StoragePathError(f"unsafe config path: {relative_to_root!r}")
    target = paths.root.joinpath(*segments)
    current = paths.root
    for part in segments:
        current = current / part
        status = current.lstat()
        attributes: int = getattr(status, "st_file_attributes", 0)
        if stat.S_ISLNK(status.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise StoragePathError(f"link inside config path: {current}")
    if not target.resolve(strict=True).is_relative_to(paths.root / "config"):
        raise StoragePathError("config path escapes config/")
    return target


def seconds_relative(run_id: str, name: str) -> str:
    return f"{SCREEN_DIR}/{check_run_id(run_id)}/{name}"


def analysis_relative(run_id: str, name: str) -> str:
    return f"{SCREEN_DIR}/{check_run_id(run_id)}/{name}"


def ledger_relative(discovery_sha256: str) -> str:
    return f"{SCREEN_DIR}/{LEDGER_DIR}/{discovery_sha256}.opened.json"


# --- loading analysis days ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonthGuard:
    """Discovery barrier: only MOEX dates of ``month`` and COMEX intervals entirely inside the
    month's UTC window may be opened."""

    month: str

    def check_date(self, day: date) -> None:
        if month_of(day) != self.month:
            raise HoldoutGuardError(f"{day} is outside the allowed month {self.month}")

    def check_interval(self, start_ns: int, end_ns: int) -> None:
        lo, hi = month_window_ns(self.month)
        if start_ns < lo or end_ns > hi:
            raise HoldoutGuardError(f"COMEX interval outside the allowed month {self.month}")


@dataclass(slots=True)
class SecondsRun:
    paths: ProjectPaths
    run_id: str
    manifest: dict[str, object]
    manifest_sha256: str
    opened: list[str]

    @staticmethod
    def open(paths: ProjectPaths, run_id: str, config_sha256: str) -> "SecondsRun":
        manifest, digest = read_json_with_checksum(
            paths, StorageCategory.ALIGNED, seconds_relative(run_id, SECONDS_MANIFEST)
        )
        if manifest.get("manifest_version") != SECONDS_MANIFEST_VERSION:
            raise IntegrityError("unknown seconds manifest version")
        if manifest.get("config_sha256") != config_sha256:
            raise IntegrityError("seconds run was built with a different config")
        if manifest.get("run_id") != run_id:
            raise IntegrityError("seconds manifest run id mismatch")
        return SecondsRun(paths, run_id, manifest, digest, [])

    def _section(self, key: str) -> list[dict[str, object]]:
        value = self.manifest.get(key)
        if not isinstance(value, list):
            raise IntegrityError(f"manifest section {key} missing")
        return [item for item in value if isinstance(item, dict)]

    def moex_day(self, day: date) -> dict[str, object] | None:
        for item in self._section("moex_days"):
            if item.get("trading_date") == day.isoformat():
                return item
        return None

    def comex_interval(self, name: str) -> dict[str, object]:
        for item in self._section("comex_intervals"):
            if item.get("session_id") == name:
                return item
        raise IntegrityError(f"COMEX interval {name} missing from the manifest")

    def primary_candidates(self, month: str) -> list[date]:
        return [
            date.fromisoformat(str(item["date"]))
            for item in self._section("day_universe")
            if item.get("primary_candidate") is True and str(item["date"]).startswith(month)
        ]

    def day_universe(self, month: str) -> list[dict[str, object]]:
        return [
            item for item in self._section("day_universe") if str(item["date"]).startswith(month)
        ]

    def _read_output(self, entry: Mapping[str, object]) -> bytes:
        output = entry.get("output")
        if not isinstance(output, dict):
            raise IntegrityError("manifest entry without output")
        path, digest = str(output["path"]), str(output["sha256"])
        data = read_verified(self.paths, path, digest)
        self.opened.append(path)
        return data

    def month_plan(
        self, month: str, guard: MonthGuard | None
    ) -> tuple[list[tuple[date, tuple[CommonSegment, ...]]], list[dict[str, object]]]:
        """Primary candidates of ``month`` with MOEX input and their common segments (from the
        manifest only, no outcome data); the second list reports candidates without input
        (coverage evidence; never substituted)."""
        plan: list[tuple[date, tuple[CommonSegment, ...]]] = []
        missing: list[dict[str, object]] = []
        for day in self.primary_candidates(month):
            if guard is not None:
                guard.check_date(day)
            entry = self.moex_day(day)
            if entry is None:
                missing.append({"date": day.isoformat(), "reason": "moex_input_missing"})
                continue
            plan.append((day, _common_from_manifest(entry)))
        return plan, missing

    def iter_days(
        self, plan: Sequence[tuple[date, tuple[CommonSegment, ...]]], guard: MonthGuard | None
    ) -> Iterator[AnalysisDay]:
        """Load one analysis day at a time (SHA-verified files; bounded COMEX cache)."""
        comex_cache: dict[str, list[tuple[int, str, str, str, bool]]] = {}
        log_cache: dict[str, float] = {}
        for day, common in plan:
            if guard is not None:
                guard.check_date(day)
            entry = self.moex_day(day)
            if entry is None:
                raise IntegrityError(f"MOEX day {day} vanished from the manifest")
            moex_rows = seconds_from_csv_light(self._read_output(entry))
            comex_rows: list[tuple[int, str, str, str, bool]] = []
            reconciled = _reconciled(entry)
            names = sorted({segment.comex_session for segment in common})
            for name in names:
                interval = self.comex_interval(name)
                if guard is not None:
                    guard.check_interval(
                        int(str(interval["start_utc_ns"])), int(str(interval["end_utc_ns"]))
                    )
                reconciled = reconciled and _reconciled(interval)
                if name not in comex_cache:
                    comex_cache[name] = seconds_from_csv_light(self._read_output(interval))
                comex_rows.extend(comex_cache[name])
            # Only intervals of the current day can be shared with the next one.
            comex_cache = {name: comex_cache[name] for name in names}
            timestamps = entry.get("timestamps")
            quantum = timestamps.get("quantum_ns") if isinstance(timestamps, dict) else None
            yield AnalysisDay(
                trading_date=day,
                common=common,
                moex=_venue_states(moex_rows, common, "moex", log_cache),
                comex=_venue_states(comex_rows, common, "comex", log_cache),
                moex_quantum_ns=quantum if isinstance(quantum, int) else None,
                reconciled=reconciled and bool(self.manifest.get("comex_reconciled")),
            )


def _reconciled(entry: Mapping[str, object]) -> bool:
    reconciliation = entry.get("reconciliation")
    return isinstance(reconciliation, dict) and reconciliation.get("ok") is True


def _common_from_manifest(entry: Mapping[str, object]) -> tuple[CommonSegment, ...]:
    raw = entry.get("common_segments")
    if not isinstance(raw, list):
        raise IntegrityError("manifest day without common_segments")
    segments: list[CommonSegment] = []
    for item in raw:
        if not isinstance(item, dict):
            raise IntegrityError("malformed common segment")
        start = int(str(item["start_utc_ns"]))
        end = int(str(item["end_utc_ns"]))
        if start % NS_PER_SECOND or end % NS_PER_SECOND:
            raise IntegrityError("common segment not on whole seconds")
        segments.append(
            CommonSegment(
                name=str(item["name"]),
                start_s=start // NS_PER_SECOND,
                end_s=end // NS_PER_SECOND,
                trading_date=date.fromisoformat(str(item["trading_date"])),
                moex_session=str(item["moex_session"]),
                comex_session=str(item["comex_session"]),
            )
        )
    return tuple(segments)


def _venue_states(
    rows: Sequence[tuple[int, str, str, str, bool]],
    common: Sequence[CommonSegment],
    venue: str,
    log_cache: dict[str, float],
) -> VenueDayStates:
    """States inside the common segments; the session of each row must be the segment's."""
    bounds = [
        (
            segment.start_s,
            segment.end_s,
            segment.moex_session if venue == "moex" else segment.comex_session,
        )
        for segment in common
    ]
    states: dict[int, tuple[float, int]] = {}
    refreshed: set[int] = set()
    for second, close_text, age_text, session, is_refreshed in rows:
        for start, end, name in bounds:
            if start <= second < end:
                if session != name:
                    raise IntegrityError(f"{venue} second {second} has session {session}")
                if is_refreshed:
                    refreshed.add(second)
                if close_text:
                    log_value = log_cache.get(close_text)
                    if log_value is None:
                        log_value = math.log(float(close_text))
                        log_cache[close_text] = log_value
                    whole, _, fraction = age_text.partition(".")
                    states[second] = (log_value, int(whole) * 1_000_000 + int(fraction))
                break
    return VenueDayStates(states, frozenset(refreshed))
