"""Create-once publication of one raw daily payload with manifest and checksum (ADR-0002).

Minimal replacement for the legacy streaming segment/catalog/checkpoint/replay graph (M-011,
deferred): historical daily files need only "write once, record SHA-256 and provenance,
never overwrite, never expose a half-written publication".

Guarantee: **logical (protocol-level) atomicity through a commit marker.** Payload, manifest and
checksum live in different directories, so no single filesystem operation can create all three.
Instead a publication is *valid* only when its commit marker exists, and the marker is created
by one atomic, create-once operation (``os.link`` of a fully written and flushed staging file on
the same volume) after the three artifacts are in place and verified. Readers
(``verify_published``) require the marker and check that its SHA-256 values match all three
files. Artifacts without a marker are *uncommitted*: never valid, never read, and removed only by
the publisher's own rollback or recovery. This is not physical atomicity: after a crash,
uncommitted files can remain visible on disk until ``recover_raw_publications`` runs.

Publication protocol for ``<source>/<dataset>/<contract>/<trading_date>/<file>``:

1. all targets (payload ``data/raw/<rel>``, manifest
   ``artifacts/manifests/raw/<rel>.manifest.json``, checksum
   ``artifacts/checksums/raw/<rel>.sha256``, marker ``artifacts/manifests/raw/<rel>.commit.json``)
   are resolved through the storage guard and must not exist yet;
2. a journal ``tmp/raw-publication/<id>.journal`` is created exclusively, its first byte is
   locked for the lifetime of the publication (the OS releases the lock if the process dies),
   and its header names the publication; it is flushed before anything else is written;
3. the four contents are written to exclusively created staging files
   ``tmp/raw-publication/<id>.<k>.part`` and flushed; a failed write removes its own file;
4. payload, manifest and checksum staging files are hard-linked to their final names
   (``os.link`` never replaces an existing file, so a concurrent writer is never overwritten);
5. the three final files are re-read and verified against the expected SHA-256 values;
6. COMMIT: the marker staging file is hard-linked to the marker name (create-once, atomic);
7. staging files and the journal are removed, final files are marked read-only, and the
   committed publication is verified as a reader would.

Ownership invariant: a final name belongs to this publisher only while its file identity
(``st_dev`` + non-zero file index) equals the identity recorded right after this publisher's
``os.link`` created it (or, in recovery, the identity of the publisher's still-existing staging
file). Path, bytes, hash and ``publication_id`` never prove ownership. A name that another local
writer has replaced is never removed, chmod-ed or overwritten, even with identical bytes.

Any exception before step 7 has completed removes, in reverse order (marker first, so the
publication stops looking committed at once), the final names that are still this call's files,
plus its staging files and journal, then re-raises. Before marking files read-only and after the
final reader verification, every final name must still be this call's file; otherwise the
publication is rolled back the same way. ``publish_raw`` therefore either returns a committed,
verified, fully owned publication or leaves nothing of its own behind (foreign replacements stay).

``recover_raw_publications`` handles a killed publisher; journals whose lock is held (a live
publisher) are skipped. Outcomes per dead publication:

- ``completed``: only after a full ``verify_published`` (payload, manifest, checksum, marker).
  While the staging marker exists, the final marker must be that same file; once all staging
  files are gone (normal crash window after the commit), a schema-valid marker naming this
  publication and payload path plus full verification is accepted;
- ``rolled_back``: names proven to be own files are removed (marker first); provably foreign
  names are left untouched and listed as a ``foreign_path`` conflict;
- ``unverified_commit`` conflict: ownership cannot be proven and the publication does not verify;
  nothing is removed or chmod-ed and the journal is kept, so automatic continuation fails closed
  (re-publishing the same path raises ``RawCollisionError``).

Nothing outside ``tmp/raw-publication`` is removed unless its identity proves it is own. A race in
which another writer replaces a name between the identity check and the removal is not excluded.
"""

import contextlib
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from moex_arbitrage_lab.contracts.acquisition_manifest import (
    MANIFEST_VERSION,
    AcquisitionManifestV1,
    manifest_from_json_bytes,
    manifest_to_json_bytes,
)
from moex_arbitrage_lab.contracts.types import Identifier, RelativePosixPath, Sha256Hex, sha256_hex
from moex_arbitrage_lab.settings.storage_paths import (
    PathEscapeError,
    ProjectPaths,
    StorageCategory,
    StoragePathError,
    validate_relative_path,
)

if sys.platform == "win32":
    import msvcrt

    def _try_lock(descriptor: int) -> bool:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - the project root is a Windows drive; kept for completeness
    import fcntl

    def _try_lock(descriptor: int) -> bool:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


_WORKDIR: Final = "raw-publication"
_MANIFEST_SUFFIX: Final = ".manifest.json"
_CHECKSUM_SUFFIX: Final = ".sha256"
_MARKER_SUFFIX: Final = ".commit.json"
_JOURNAL_SUFFIX: Final = ".journal"
_ARTIFACTS: Final = ("payload", "manifest", "checksum", "marker")
_STAGING_NAME: Final = re.compile(r"^(?P<id>[0-9a-f]{32})\.[0-3]\.part$")
_JOURNAL_NAME: Final = re.compile(r"^(?P<id>[0-9a-f]{32})\.journal$")
_O_BINARY: Final = getattr(os, "O_BINARY", 0)
# Fields computed here from the payload; the caller cannot supply them.
_COMPUTED_FIELDS: Final = frozenset(
    {"manifest_version", "payload_path", "payload_bytes", "payload_sha256"}
)


class RawPublicationError(StoragePathError):
    """A raw publication was refused or could not complete."""


class RawCollisionError(RawPublicationError):
    """A target of the publication already exists; it is never overwritten."""


class RawIntegrityError(RawPublicationError):
    """A publication is missing, incomplete, uncommitted or does not match its marker."""


class RawUncommittedError(RawIntegrityError):
    """Artifacts exist without a commit marker; they are never valid."""


class RawCleanupPendingError(RawPublicationError):
    """The publication IS committed and verified, but staging/journal cleanup failed.

    ``recover_raw_publications`` removes the leftovers; the publication stays valid.
    """


class RawCommitMarkerV1(BaseModel):
    """Commit marker: binds the three artifacts of one publication by SHA-256."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    marker_version: Literal["raw_commit_marker_v1"]
    publication_id: Identifier
    payload_path: RelativePosixPath
    payload_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    checksum_sha256: Sha256Hex


@dataclass(frozen=True, slots=True)
class PublishedRaw:
    payload: Path
    manifest: Path
    checksum: Path
    marker: Path
    manifest_record: AcquisitionManifestV1


ConflictKind = Literal["foreign_path", "unverified_commit"]


@dataclass(frozen=True, slots=True)
class RecoveryConflict:
    """A path recovery could neither complete nor roll back; it was left untouched.

    ``foreign_path``: provably not this publisher's file (its own state was cleaned up).
    ``unverified_commit``: ownership cannot be proven and the publication does not verify;
    nothing was changed and the journal is kept, so automatic continuation stays blocked.
    """

    publication_id: str
    kind: ConflictKind
    paths: tuple[Path, ...]


@dataclass(slots=True)
class RecoveryReport:
    completed: list[str] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)
    skipped_active: list[str] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    conflicts: list[RecoveryConflict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True if nothing needs a human decision."""
        return not self.conflicts


def _checkpoint(step: str) -> None:
    """Between protocol steps; tests replace it to inject failures or abrupt termination."""


def raw_relative_path(
    source: str, dataset: str, contract: str, trading_date: date, filename: str
) -> str:
    """``<source>/<dataset>/<contract>/<trading_date>/<filename>``, validated."""
    relative = f"{source}/{dataset}/{contract}/{trading_date.isoformat()}/{filename}"
    validate_relative_path(relative)
    return relative


# --- paths ------------------------------------------------------------------------------------


def _is_link_or_reparse_point(status: os.stat_result) -> bool:
    attributes: int = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _file_in(paths: ProjectPaths, category: StorageCategory, relative: str) -> Path:
    """Guarded location of a file. The parent directory goes through the full storage guard;
    the file itself must not be a link/reparse point. Unlike ``ProjectPaths.resolve`` it allows
    a second hard link, because during a publication the final name and the staging name are
    the same file."""
    parent, _, name = relative.rpartition("/")
    validate_relative_path(relative)
    directory = paths.resolve(category, parent) if parent else paths.category_dir(category)
    target = directory / name
    status = _lstat(target)
    if status is not None and _is_link_or_reparse_point(status):
        raise PathEscapeError(f"link or reparse point inside the project tree: {target}")
    return target


def _targets(paths: ProjectPaths, relative: str) -> tuple[Path, Path, Path, Path]:
    validate_relative_path(relative)
    return (
        _file_in(paths, StorageCategory.RAW, relative),
        _file_in(paths, StorageCategory.MANIFESTS, f"raw/{relative}{_MANIFEST_SUFFIX}"),
        _file_in(paths, StorageCategory.CHECKSUMS, f"raw/{relative}{_CHECKSUM_SUFFIX}"),
        _file_in(paths, StorageCategory.MANIFESTS, f"raw/{relative}{_MARKER_SUFFIX}"),
    )


def _workdir(paths: ProjectPaths) -> Path:
    return paths.resolve(StorageCategory.TEMP, _WORKDIR)


def _staging_path(paths: ProjectPaths, publication_id: str, index: int) -> Path:
    return _file_in(paths, StorageCategory.TEMP, f"{_WORKDIR}/{publication_id}.{index}.part")


Identity = tuple[int, int]


def _identity(path: Path) -> Identity | None:
    """``(st_dev, st_ino)`` of a plain existing file; ``None`` if missing, a link/reparse point,
    or without a usable file index. This is the only ownership proof: never path, bytes, hash or
    publication_id."""
    status = _lstat(path)
    if status is None or status.st_ino == 0 or _is_link_or_reparse_point(status):
        return None
    return status.st_dev, status.st_ino


def _same_file(left: Path, right: Path) -> bool:
    """True only if both exist and are the same file (device and file index)."""
    identity = _identity(left)
    return identity is not None and identity == _identity(right)


def _still_owned(path: Path, identity: Identity) -> bool:
    return _identity(path) == identity


def _remove(path: Path) -> None:
    """Remove one file of this publisher (read-only attribute cleared first)."""
    path.chmod(0o644)
    path.unlink()


def _remove_if(path: Path, removed: list[Path]) -> None:
    if _lstat(path) is not None:
        _remove(path)
        removed.append(path)


# --- journal ----------------------------------------------------------------------------------


class _Journal:
    """Exclusive, locked journal of one live publication (append-only header line)."""

    def __init__(self, path: Path, descriptor: int) -> None:
        self.path = path
        self._descriptor: int | None = descriptor

    @classmethod
    def create(cls, paths: ProjectPaths, publication_id: str, relative: str) -> "_Journal":
        workdir = _workdir(paths)
        workdir.mkdir(parents=True, exist_ok=True)
        path = _file_in(
            paths, StorageCategory.TEMP, f"{_WORKDIR}/{publication_id}{_JOURNAL_SUFFIX}"
        )
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644)
        journal = cls(path, descriptor)
        try:
            if not _try_lock(descriptor):
                raise RawPublicationError(f"cannot lock a new journal: {path}")
            header = {"journal_version": 1, "publication_id": publication_id, "relative": relative}
            data = (json.dumps(header, sort_keys=True) + "\n").encode("ascii")
            os.lseek(descriptor, 0, os.SEEK_SET)
            _write_all(descriptor, data)
            os.fsync(descriptor)
        except BaseException:
            journal.remove()
            raise
        return journal

    def remove(self) -> None:
        """Release the lock, close and delete the journal (in this order: an open file cannot
        be deleted on Windows)."""
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            # Closing the descriptor releases the lock as well if an explicit unlock fails.
            with contextlib.suppress(OSError):
                _unlock(descriptor)
            os.close(descriptor)
        if _lstat(self.path) is not None:
            self.path.unlink()


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _write_staging(paths: ProjectPaths, publication_id: str, index: int, data: bytes) -> Path:
    """Write and flush one exclusively created staging file; on any failure remove it."""
    staging = _staging_path(paths, publication_id, index)
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o444)
    try:
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        _remove(staging)
        raise
    return staging


# --- publication ------------------------------------------------------------------------------


def publish_raw(
    paths: ProjectPaths,
    relative: str,
    payload: bytes,
    manifest_fields: Mapping[str, object],
) -> PublishedRaw:
    """Publish ``payload`` once under ``data/raw/<relative>`` with manifest, checksum and commit
    marker (logical atomicity; see the module docstring).

    ``manifest_fields`` holds every ``AcquisitionManifestV1`` field except the ones computed here
    (version, payload path, byte count, SHA-256). Raises ``RawCollisionError`` if any target
    exists (committed or not) and a ``ValidationError`` if the manifest is invalid; in both cases
    nothing is written. Returns only after the publication is committed and verified.
    """
    supplied = _COMPUTED_FIELDS.intersection(manifest_fields)
    if supplied:
        raise RawPublicationError(
            f"fields computed by publish_raw were supplied: {sorted(supplied)}"
        )
    targets = _targets(paths, relative)
    _refuse_existing(targets)

    publication_id = uuid.uuid4().hex
    payload_target = targets[0]
    digest = sha256_hex(payload)
    payload_path = paths.relative_to_root(payload_target)
    manifest = AcquisitionManifestV1.model_validate(
        {
            **manifest_fields,
            "manifest_version": MANIFEST_VERSION,
            "payload_path": payload_path,
            "payload_bytes": len(payload),
            "payload_sha256": digest,
        }
    )
    manifest_bytes = manifest_to_json_bytes(manifest)
    checksum_bytes = f"{digest}  {payload_path}\n".encode("ascii")
    marker = RawCommitMarkerV1(
        marker_version="raw_commit_marker_v1",
        publication_id=publication_id,
        payload_path=payload_path,
        payload_sha256=digest,
        manifest_sha256=sha256_hex(manifest_bytes),
        checksum_sha256=sha256_hex(checksum_bytes),
    )
    contents = (payload, manifest_bytes, checksum_bytes, marker.model_dump_json().encode("utf-8"))

    journal = _Journal.create(paths, publication_id, relative)
    staged: list[Path] = []
    # Final names this call created, with the file identity recorded right after each link.
    owned: list[tuple[Path, Identity]] = []
    try:
        _checkpoint("journaled")
        for index, data in enumerate(contents):
            staged.append(_write_staging(paths, publication_id, index, data))
            _checkpoint(f"staged:{_ARTIFACTS[index]}")
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after creating parents: no link may have appeared on the way.
        if _targets(paths, relative) != targets:
            raise RawPublicationError("publication targets changed during publication")
        for index in range(3):
            owned.append((targets[index], _link_owned(staged[index], targets[index])))
            _checkpoint(f"linked:{_ARTIFACTS[index]}")
        _verify_own_artifacts(targets[:3], staged[:3], contents[:3])
        _checkpoint("verified")
        # COMMIT: one atomic create-once operation.
        owned.append((targets[3], _link_owned(staged[3], targets[3])))
        _checkpoint("committed")
        for path in staged:
            _remove(path)
        staged.clear()
        for target, identity in owned:
            _require_owned(target, identity)
            target.chmod(0o444)
        _checkpoint("finalized")
        record = verify_published(paths, relative)
        # Equal bytes are not ownership: every final name must still be this call's file.
        for target, identity in owned:
            _require_owned(target, identity)
        _checkpoint("reverified")
    except BaseException:
        # Marker first, so the publication stops looking committed at once. A name is removed
        # only while it is still this call's file; an independent replacement is never touched.
        for target, identity in reversed(owned):
            if _still_owned(target, identity):
                _remove(target)
        for path in staged:
            if _lstat(path) is not None:
                _remove(path)
        journal.remove()
        raise
    try:
        journal.remove()
    except OSError as error:
        raise RawCleanupPendingError(
            f"publication committed; journal cleanup failed ({error.strerror}); "
            "run recover_raw_publications"
        ) from None
    return PublishedRaw(*targets, record)


def _refuse_existing(targets: tuple[Path, ...]) -> None:
    present = [target for target in targets if os.path.lexists(target)]
    if not present:
        return
    if not os.path.lexists(targets[3]):
        raise RawCollisionError(
            f"uncommitted raw artifacts already exist ({present[0]}); they are never "
            "overwritten - run recover_raw_publications or investigate"
        )
    raise RawCollisionError(f"raw publication already exists (committed): {targets[3]}")


def _link_create_once(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except FileExistsError:
        raise RawCollisionError(f"raw publication target already exists: {target}") from None


def _link_owned(source: Path, target: Path) -> Identity:
    """Link create-once and return the identity this publisher now owns at ``target``."""
    _link_create_once(source, target)
    identity = _identity(source)
    if identity is None or _identity(target) != identity:
        # Replaced between link and check: the name is no longer ours and is not recorded.
        raise RawIntegrityError(f"final name was replaced right after linking: {target}")
    return identity


def _require_owned(target: Path, identity: Identity) -> None:
    if not _still_owned(target, identity):
        raise RawIntegrityError(f"final name was replaced by an independent file: {target}")


def _verify_own_artifacts(
    targets: tuple[Path, ...], staged: list[Path], contents: tuple[bytes, ...]
) -> None:
    for target, source, data in zip(targets, staged, contents, strict=True):
        if not _same_file(target, source) or target.read_bytes() != data:
            raise RawIntegrityError(f"staged artifact does not match before commit: {target}")


def verify_published(paths: ProjectPaths, relative: str) -> AcquisitionManifestV1:
    """Reader check: the commit marker exists and its SHA-256 values match payload, manifest and
    checksum, which agree with each other. Uncommitted artifacts raise ``RawUncommittedError``."""
    payload_target, manifest_target, checksum_target, marker_target = _targets(paths, relative)
    if _lstat(marker_target) is None:
        raise RawUncommittedError(f"no commit marker; publication is not valid: {payload_target}")
    try:
        marker = RawCommitMarkerV1.model_validate_json(marker_target.read_bytes())
        payload = payload_target.read_bytes()
        manifest_bytes = manifest_target.read_bytes()
        checksum_bytes = checksum_target.read_bytes()
    except OSError as error:
        raise RawIntegrityError(f"publication is incomplete: {error.strerror}") from None
    except ValueError:
        raise RawIntegrityError(f"commit marker is invalid: {marker_target}") from None
    try:
        manifest = manifest_from_json_bytes(manifest_bytes)
    except ValueError:
        raise RawIntegrityError(f"manifest is invalid: {manifest_target}") from None
    digest = sha256_hex(payload)
    payload_path = paths.relative_to_root(payload_target)
    if (
        marker.payload_path != payload_path
        or marker.payload_sha256 != digest
        or marker.manifest_sha256 != sha256_hex(manifest_bytes)
        or marker.checksum_sha256 != sha256_hex(checksum_bytes)
        or manifest.payload_sha256 != digest
        or manifest.payload_bytes != len(payload)
        or manifest.payload_path != payload_path
        or checksum_bytes != f"{digest}  {payload_path}\n".encode("ascii")
    ):
        raise RawIntegrityError(
            f"payload does not match its marker/manifest/checksum: {payload_target}"
        )
    return manifest


# --- recovery ---------------------------------------------------------------------------------


def recover_raw_publications(paths: ProjectPaths) -> RecoveryReport:
    """Finish or roll back publications whose publisher died; skip live ones.

    Bounded to ``tmp/raw-publication``: journals and staging files named by this publisher, and
    final files proven to be the same file as one of a journal's staging files.
    """
    report = RecoveryReport()
    workdir = _workdir(paths)
    if not workdir.is_dir():
        return report
    for journal_path in sorted(workdir.iterdir()):
        match = _JOURNAL_NAME.fullmatch(journal_path.name)
        if match is not None:
            _recover_one(paths, match["id"], journal_path, report)
    # Staging files whose journal is gone (the journal is created first and removed last, so
    # these belong to no live publication).
    for path in sorted(workdir.iterdir()):
        match = _STAGING_NAME.fullmatch(path.name)
        if match is not None and _lstat(workdir / f"{match['id']}{_JOURNAL_SUFFIX}") is None:
            _remove_if(path, report.removed)
    return report


def _recover_one(
    paths: ProjectPaths, publication_id: str, journal_path: Path, report: RecoveryReport
) -> None:
    descriptor = os.open(journal_path, os.O_RDWR | _O_BINARY)
    try:
        if not _try_lock(descriptor):
            report.skipped_active.append(publication_id)
            return
        relative = _journal_relative(descriptor, publication_id)
        staged = [_staging_path(paths, publication_id, index) for index in range(4)]
        settled = _Settled(resolved=True, owned=())
        if relative is not None:
            settled = _settle(paths, publication_id, relative, staged, report)
        else:
            # The header is flushed before any staging file exists and before any link, so a
            # missing/torn header means nothing was linked: only staging files can be left.
            report.rolled_back.append(publication_id)
        if settled.resolved:
            for path in staged:
                _remove_if(path, report.removed)
            # Removing a staging hard link cleared the read-only attribute shared with the final
            # name; restore it only on names still proven to be this publisher's files.
            for target, identity in settled.owned:
                if _still_owned(target, identity):
                    target.chmod(0o444)
        _unlock(descriptor)
    finally:
        os.close(descriptor)
    if settled.resolved:
        _remove_if(journal_path, report.removed)


def _journal_relative(descriptor: int, publication_id: str) -> str | None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    raw = os.read(descriptor, 4096).split(b"\n", 1)[0]
    try:
        header = json.loads(raw)
        relative = header["relative"]
        if header.get("publication_id") != publication_id or not isinstance(relative, str):
            return None
        validate_relative_path(relative)
    except (ValueError, KeyError, TypeError, StoragePathError):
        return None
    return relative


@dataclass(frozen=True, slots=True)
class _Settled:
    resolved: bool  # False: leave journal and staging in place (fail closed)
    owned: tuple[tuple[Path, Identity], ...]  # committed names to mark read-only again


def _settle(
    paths: ProjectPaths,
    publication_id: str,
    relative: str,
    staged: list[Path],
    report: RecoveryReport,
) -> _Settled:
    """Decide one dead publication. ``completed`` is reported only after full reader-level
    verification; files are removed or chmod-ed only when their identity proves ownership.

    Staging files are removed in order payload, manifest, checksum, marker and only after the
    commit, so: staging marker present -> identity decides; staging marker absent but other
    staging present -> the marker was never linked by this publisher; no staging at all ->
    normal crash window after the commit (or before staging), where only the marker's content
    plus a full ``verify_published`` can confirm the commit.
    """
    targets = _targets(paths, relative)
    present = [_lstat(path) is not None for path in staged]
    identities = [
        _identity(target) if _same_file(target, source) else None
        for target, source in zip(targets, staged, strict=True)
    ]
    if not any(present):
        return _settle_without_staging(paths, publication_id, relative, targets, report)
    own_marker = identities[3] is not None
    # A target whose staging file is gone can only be unproven while the staging marker exists
    # (post-commit, mid-cleanup); otherwise it is provably not this publisher's file.
    sound = all(identities[i] is not None or not present[i] for i in range(3))
    if own_marker and sound and _verifies(paths, relative):
        report.completed.append(publication_id)
        owned = tuple((t, i) for t, i in zip(targets, identities, strict=True) if i is not None)
        return _Settled(resolved=True, owned=owned)
    # Not (validly) committed by this publisher: roll back own names, marker first.
    for target, identity in reversed(list(zip(targets, identities, strict=True))):
        if identity is not None and _still_owned(target, identity):
            _remove(target)
            report.removed.append(target)
    leftovers = [t for t in targets if _lstat(t) is not None]
    unproven = tuple(
        target
        for target, here in zip(targets, present, strict=True)
        if _lstat(target) is not None and not here and present[3]
    )
    if unproven:
        report.conflicts.append(RecoveryConflict(publication_id, "unverified_commit", unproven))
        return _Settled(resolved=False, owned=())
    if leftovers:
        report.conflicts.append(RecoveryConflict(publication_id, "foreign_path", tuple(leftovers)))
    report.rolled_back.append(publication_id)
    return _Settled(resolved=True, owned=())


def _settle_without_staging(
    paths: ProjectPaths,
    publication_id: str,
    relative: str,
    targets: tuple[Path, Path, Path, Path],
    report: RecoveryReport,
) -> _Settled:
    existing = tuple(t for t in targets if _lstat(t) is not None)
    if not existing:
        report.rolled_back.append(publication_id)  # died before staging: nothing was linked
        return _Settled(resolved=True, owned=())
    payload_path = paths.relative_to_root(targets[0])
    if _marker_claims(targets[3], publication_id, payload_path) and _verifies(paths, relative):
        report.completed.append(publication_id)
        return _Settled(resolved=True, owned=())  # identity unprovable: no chmod
    # Ownership cannot be proven and the publication does not verify: touch nothing.
    report.conflicts.append(RecoveryConflict(publication_id, "unverified_commit", existing))
    return _Settled(resolved=False, owned=())


def _marker_claims(marker: Path, publication_id: str, payload_path: str) -> bool:
    """Schema-valid marker naming this publication and payload path. Necessary, never
    sufficient: callers also require a full ``verify_published``."""
    try:
        record = RawCommitMarkerV1.model_validate_json(marker.read_bytes())
    except (OSError, ValueError):
        return False
    return record.publication_id == publication_id and record.payload_path == payload_path


def _verifies(paths: ProjectPaths, relative: str) -> bool:
    try:
        verify_published(paths, relative)
    except (StoragePathError, OSError):
        return False
    return True


def staging_artifacts(paths: ProjectPaths) -> list[str]:
    """Names in the publisher work directory (diagnostics and tests)."""
    workdir = _workdir(paths)
    return sorted(p.name for p in workdir.iterdir()) if workdir.is_dir() else []
