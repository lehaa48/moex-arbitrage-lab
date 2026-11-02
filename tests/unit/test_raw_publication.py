"""Create-once raw publication (ADR-0002): logical atomicity through a commit marker.

Covers: committed publications verify; staging write/fsync/close failures remove their own
files; a failure between every protocol step leaves nothing; abrupt termination (``os._exit`` in
a subprocess) before the commit leaves an uncommitted publication that is never valid and that
recovery removes without touching foreign files; termination after the commit is completed by
recovery; post-commit verification failure leaves no committed state; collisions never
overwrite.
"""

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from moex_arbitrage_lab.contracts import manifest_from_json_bytes, sha256_hex
from moex_arbitrage_lab.settings import (
    PathEscapeError,
    ProjectPaths,
    StorageCategory,
    UnsafeRelativePathError,
)
from moex_arbitrage_lab.settings import raw_publication as rp
from moex_arbitrage_lab.settings.raw_publication import (
    RawCollisionError,
    RawCommitMarkerV1,
    RawIntegrityError,
    RawPublicationError,
    RawUncommittedError,
    publish_raw,
    raw_relative_path,
    recover_raw_publications,
    verify_published,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows locking/links")

REPO_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = b"synthetic daily payload, not market data\n"
RELATIVE = "synthetic/synthetic.trades/gcz6/2026-12-01/day.bin"
PRE_COMMIT_STEPS = [
    "journaled",
    "staged:payload",
    "staged:manifest",
    "staged:checksum",
    "staged:marker",
    "linked:payload",
    "linked:manifest",
    "linked:checksum",
    "verified",
]
ALL_STEPS = [*PRE_COMMIT_STEPS, "committed", "finalized", "reverified"]


def fields(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "acquisition_id": "acq-synthetic-0001",
        "provider": "synthetic",
        "dataset": "synthetic.trades",
        "source_schema": "trades",
        "source_format_version": None,
        "venue": "COMEX",
        "canonical_contract": "COMEX:GC:2026-12-29",
        "trading_date": date(2026, 12, 1),
        "request_parameters": {"symbols": "GCZ6"},
        "request_start_utc_ns": None,
        "request_end_utc_ns": None,
        "retrieval_started_utc_ns": 1,
        "retrieval_completed_utc_ns": 2,
        "cost_ledger_request_id": "req-synthetic-0001",
        "estimated_cost": Decimal(0),
        "estimated_cost_currency": "USD",
        "actual_cost_if_known": Decimal(0),
        "cost_decision": "FREE_CONFIRMED",
        "license_url": "https://example.invalid/license",
        "license_version": "synthetic-1",
        "response_status": None,
        "response_metadata": {},
        "publisher_version": "moex-arbitrage-lab-0.0.1",
    }
    base.update(overrides)
    return base


# Child process: loads this file for ``fields``/``PAYLOAD``, then dies with os._exit (no
# cleanup, no finally blocks, open handles and locks released by the OS) at the given step.
_KILLER = """
import importlib.util, os, sys
from pathlib import Path
from moex_arbitrage_lab.settings import raw_publication as rp
from moex_arbitrage_lab.settings.storage_paths import _resolve_project_paths
spec = importlib.util.spec_from_file_location("raw_publication_tests", sys.argv[4])
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
repo, stop, relative = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
paths = _resolve_project_paths(repo, repo)
def hook(step):
    if step == stop:
        os._exit(77)
rp._checkpoint = hook
rp.publish_raw(paths, relative, tests.PAYLOAD, tests.fields())
os._exit(0)
"""


def _state(paths: ProjectPaths) -> dict[Path, bytes]:
    roots = [paths.root / "data", paths.root / "artifacts", paths.root / "tmp"]
    return {
        file: file.read_bytes()
        for root in roots
        if root.exists()
        for file in sorted(root.rglob("*"))
        if file.is_file()
    }


def _kill_at(paths: ProjectPaths, step: str, relative: str = RELATIVE) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _KILLER, str(paths.root), step, relative, __file__],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 77, result.stderr


def _workdir(paths: ProjectPaths) -> Path:
    return paths.root / "tmp" / "raw-publication"


# --- committed publication ---------------------------------------------------------------------


def test_committed_publication_is_visible_and_verifies(paths: ProjectPaths) -> None:
    published = publish_raw(paths, RELATIVE, PAYLOAD, fields())

    assert published.payload == paths.root / "data" / "raw" / Path(RELATIVE)
    assert published.payload.read_bytes() == PAYLOAD
    manifest = manifest_from_json_bytes(published.manifest.read_bytes())
    assert manifest == published.manifest_record
    assert manifest.payload_sha256 == sha256_hex(PAYLOAD)
    assert manifest.payload_path == f"data/raw/{RELATIVE}"
    assert published.checksum.read_text(encoding="ascii") == (
        f"{sha256_hex(PAYLOAD)}  data/raw/{RELATIVE}\n"
    )
    marker = RawCommitMarkerV1.model_validate_json(published.marker.read_bytes())
    assert published.marker == paths.root / "artifacts" / "manifests" / "raw" / Path(
        RELATIVE + ".commit.json"
    )
    assert marker.payload_sha256 == sha256_hex(PAYLOAD)
    assert marker.manifest_sha256 == sha256_hex(published.manifest.read_bytes())
    assert marker.checksum_sha256 == sha256_hex(published.checksum.read_bytes())
    assert verify_published(paths, RELATIVE) == manifest
    assert rp.staging_artifacts(paths) == []
    for path in (published.payload, published.manifest, published.checksum, published.marker):
        status = path.lstat()
        assert status.st_nlink == 1
        assert not status.st_mode & stat.S_IWRITE
    assert recover_raw_publications(paths) == rp.RecoveryReport()


def test_republication_to_the_same_path_fails_closed(paths: ProjectPaths) -> None:
    publish_raw(paths, RELATIVE, PAYLOAD, fields())
    before = _state(paths)

    with pytest.raises(RawCollisionError, match="already exists"):
        publish_raw(paths, RELATIVE, b"different bytes", fields(acquisition_id="acq-2"))

    assert _state(paths) == before


@pytest.mark.parametrize("existing", ["payload", "manifest", "checksum", "marker"])
def test_any_pre_existing_target_blocks_the_publication_untouched(
    paths: ProjectPaths, existing: str
) -> None:
    category, suffix = {
        "payload": (StorageCategory.RAW, ""),
        "manifest": (StorageCategory.MANIFESTS, ".manifest.json"),
        "checksum": (StorageCategory.CHECKSUMS, ".sha256"),
        "marker": (StorageCategory.MANIFESTS, ".commit.json"),
    }[existing]
    prefix = "" if existing == "payload" else "raw/"
    stray = paths.resolve(category, f"{prefix}{RELATIVE}{suffix}")
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"pre-existing")
    before = _state(paths)

    with pytest.raises(RawCollisionError):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    assert _state(paths) == before


@pytest.mark.parametrize("racing", ["payload", "manifest", "checksum", "marker"])
def test_concurrent_creation_between_check_and_link_is_never_overwritten(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch, racing: str
) -> None:
    real_link = os.link
    suffix = {"payload": "", "manifest": ".manifest.json", "checksum": ".sha256"}
    suffix["marker"] = ".commit.json"

    def racing_link(source: Path, target: Path) -> None:
        if Path(target).name.endswith(Path(RELATIVE).name + suffix[racing]):
            Path(target).write_bytes(b"other writer")
        real_link(source, target)

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(RawCollisionError):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    remaining = _state(paths)
    assert list(remaining.values()) == [b"other writer"]


# --- in-process failures -----------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["write", "fsync", "close"])
@pytest.mark.parametrize("artifact", range(4))
def test_staging_failure_removes_its_own_partial_file(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch, operation: str, artifact: int
) -> None:
    # Call 1 of write/fsync is the journal header; staging file k is call k + 2. The journal
    # descriptor is closed only at the end, so the k-th staging close is call k + 1.
    fail_at = artifact + (1 if operation == "close" else 2)
    real = getattr(os, operation)
    calls = {"n": 0}

    def failing(descriptor: int, *args: object) -> object:
        calls["n"] += 1
        if calls["n"] == fail_at:
            if operation == "close":
                real(descriptor)
            raise OSError(28, "No space left on device")
        return real(descriptor, *args)

    monkeypatch.setattr(os, operation, failing)

    with pytest.raises(OSError, match="No space"):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    monkeypatch.undo()
    assert _state(paths) == {}


@pytest.mark.parametrize("step", ALL_STEPS)
def test_failure_between_any_two_steps_leaves_nothing(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    def hook(current: str) -> None:
        if current == step:
            raise RuntimeError(f"injected failure at {current}")

    monkeypatch.setattr(rp, "_checkpoint", hook)

    with pytest.raises(RuntimeError, match="injected failure"):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    assert _state(paths) == {}
    with pytest.raises(RawUncommittedError):
        verify_published(paths, RELATIVE)


def test_pre_commit_verification_failure_never_commits(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> None:
        raise RawIntegrityError("staged artifact does not match before commit")

    monkeypatch.setattr(rp, "_verify_own_artifacts", broken)

    with pytest.raises(RawIntegrityError):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    assert _state(paths) == {}


def test_post_commit_verification_failure_leaves_no_committed_state(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []

    def failing_verify(paths_: ProjectPaths, relative: str) -> None:
        marker = paths_.root / "artifacts" / "manifests" / "raw" / Path(relative + ".commit.json")
        seen.append(marker.exists())
        raise RawIntegrityError("synthetic verification failure")

    monkeypatch.setattr(rp, "verify_published", failing_verify)

    with pytest.raises(RawIntegrityError, match="synthetic verification failure"):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    assert seen == [True]  # the failure happened while the marker existed ...
    assert _state(paths) == {}  # ... and the marker and every artifact are gone again
    monkeypatch.undo()
    with pytest.raises(RawUncommittedError):
        verify_published(paths, RELATIVE)


def test_journal_cleanup_failure_after_commit_keeps_a_valid_publication(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_remove(self: rp._Journal) -> None:
        descriptor, self._descriptor = self._descriptor, None
        assert descriptor is not None
        rp._unlock(descriptor)
        os.close(descriptor)
        raise OSError(13, "Access is denied")

    monkeypatch.setattr(rp._Journal, "remove", failing_remove)

    with pytest.raises(rp.RawCleanupPendingError, match="publication committed"):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    monkeypatch.undo()
    manifest = verify_published(paths, RELATIVE)
    report = recover_raw_publications(paths)
    assert len(report.completed) == 1
    assert rp.staging_artifacts(paths) == []
    assert verify_published(paths, RELATIVE) == manifest


# --- abrupt termination and recovery -----------------------------------------------------------


def _plant_foreign_files(paths: ProjectPaths) -> dict[Path, bytes]:
    foreign = {
        paths.root
        / "data"
        / "raw"
        / "synthetic"
        / "synthetic.trades"
        / "gcz6"
        / "2026-12-01"
        / "other.bin": b"foreign raw file",
        _workdir(paths) / "notes.txt": b"foreign note",
        _workdir(paths) / ("f" * 32 + ".journal.bak"): b"not a journal name",
    }
    for path, data in foreign.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return foreign


@pytest.mark.parametrize("step", PRE_COMMIT_STEPS)
def test_abrupt_termination_before_commit_is_never_valid_and_recovery_removes_only_own(
    paths: ProjectPaths, step: str
) -> None:
    _kill_at(paths, step)

    with pytest.raises(RawUncommittedError):
        verify_published(paths, RELATIVE)
    left_behind = _state(paths)
    assert left_behind  # at least the journal of the dead publisher remains
    foreign = _plant_foreign_files(paths)

    report = recover_raw_publications(paths)

    assert len(report.rolled_back) == 1
    assert report.completed == report.skipped_active == []
    assert _state(paths) == foreign
    # The path is free again and a new publication commits normally.
    publish_raw(paths, RELATIVE, PAYLOAD, fields())
    verify_published(paths, RELATIVE)


@pytest.mark.parametrize("step", ["committed", "finalized"])
def test_abrupt_termination_after_commit_is_completed_by_recovery(
    paths: ProjectPaths, step: str
) -> None:
    _kill_at(paths, step)

    manifest = verify_published(paths, RELATIVE)  # committed: already valid for readers
    report = recover_raw_publications(paths)

    assert len(report.completed) == 1
    assert report.rolled_back == []
    assert rp.staging_artifacts(paths) == []
    assert verify_published(paths, RELATIVE) == manifest
    payload = paths.root / "data" / "raw" / Path(RELATIVE)
    assert payload.lstat().st_nlink == 1
    assert not payload.lstat().st_mode & stat.S_IWRITE


def test_recovery_skips_a_live_publication(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports: list[rp.RecoveryReport] = []

    def hook(step: str) -> None:
        if step == "linked:checksum":
            reports.append(recover_raw_publications(paths))

    monkeypatch.setattr(rp, "_checkpoint", hook)

    publish_raw(paths, RELATIVE, PAYLOAD, fields())

    assert len(reports[0].skipped_active) == 1
    assert reports[0].removed == []
    verify_published(paths, RELATIVE)


def test_recovery_never_removes_a_target_that_is_not_its_own_file(paths: ProjectPaths) -> None:
    # A dead journal and its staging payload, but the final name holds an independent file
    # with identical bytes (another writer): identity, not content, proves ownership.
    publication_id = "a" * 32
    workdir = _workdir(paths)
    workdir.mkdir(parents=True)
    header = {"journal_version": 1, "publication_id": publication_id, "relative": RELATIVE}
    (workdir / f"{publication_id}.journal").write_text(json.dumps(header) + "\n", "ascii")
    (workdir / f"{publication_id}.0.part").write_bytes(PAYLOAD)
    final = paths.root / "data" / "raw" / Path(RELATIVE)
    final.parent.mkdir(parents=True)
    final.write_bytes(PAYLOAD)

    report = recover_raw_publications(paths)

    assert report.rolled_back == [publication_id]
    assert final.read_bytes() == PAYLOAD
    assert rp.staging_artifacts(paths) == []


@pytest.mark.parametrize("header", [b"", b"{torn", b'{"publication_id": "other"}\n'])
def test_recovery_of_an_unreadable_journal_removes_only_its_staging(
    paths: ProjectPaths, header: bytes
) -> None:
    publication_id = "b" * 32
    workdir = _workdir(paths)
    workdir.mkdir(parents=True)
    (workdir / f"{publication_id}.journal").write_bytes(header)
    (workdir / f"{publication_id}.1.part").write_bytes(b"staged manifest")
    foreign = _plant_foreign_files(paths)

    report = recover_raw_publications(paths)

    assert report.rolled_back == [publication_id]
    assert _state(paths) == foreign


def test_recovery_removes_orphaned_staging_without_journal_only(paths: ProjectPaths) -> None:
    workdir = _workdir(paths)
    workdir.mkdir(parents=True)
    orphan = workdir / ("c" * 32 + ".2.part")
    orphan.write_bytes(b"orphan")
    foreign = _plant_foreign_files(paths)

    report = recover_raw_publications(paths)

    assert report.removed == [orphan]
    assert _state(paths) == foreign


def test_recovery_treats_a_foreign_or_invalid_marker_as_not_committed(paths: ProjectPaths) -> None:
    # Dead journal + own staged payload hard-linked to the final name, but the marker name is
    # held by a foreign, unreadable file: own artifacts are rolled back, the marker is untouched.
    publication_id = "d" * 32
    workdir = _workdir(paths)
    workdir.mkdir(parents=True)
    header = {"journal_version": 1, "publication_id": publication_id, "relative": RELATIVE}
    (workdir / f"{publication_id}.journal").write_text(json.dumps(header) + "\n", "ascii")
    staged = workdir / f"{publication_id}.0.part"
    staged.write_bytes(PAYLOAD)
    final = paths.root / "data" / "raw" / Path(RELATIVE)
    final.parent.mkdir(parents=True)
    os.link(staged, final)
    marker = paths.root / "artifacts" / "manifests" / "raw" / Path(RELATIVE + ".commit.json")
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"{foreign")

    report = recover_raw_publications(paths)

    assert report.rolled_back == [publication_id]
    assert not final.exists()
    assert marker.read_bytes() == b"{foreign"


def test_pre_commit_check_requires_the_same_file_not_just_equal_bytes(tmp_path: Path) -> None:
    staged, target = tmp_path / "staged", tmp_path / "target"
    staged.write_bytes(PAYLOAD)
    target.write_bytes(PAYLOAD)

    with pytest.raises(RawIntegrityError, match="before commit"):
        rp._verify_own_artifacts((target,), [staged], (PAYLOAD,))


def test_reparse_point_at_a_final_name_is_refused(
    paths: ProjectPaths, tmp_path: Path, junction: Callable[[Path, Path], None]
) -> None:
    final = paths.root / "data" / "raw" / Path(RELATIVE)
    final.parent.mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    junction(final, tmp_path / "outside")

    with pytest.raises(PathEscapeError, match="reparse point"):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())
    assert list((tmp_path / "outside").iterdir()) == []


def test_recovery_without_workdir_is_a_no_op(paths: ProjectPaths) -> None:
    assert recover_raw_publications(paths) == rp.RecoveryReport()


# --- ownership-safe rollback and recovery (second review) --------------------------------------
# Ownership is proven only by file identity (device + file index) recorded when this publisher
# created the name, never by path, bytes, hash or publication_id.

_LINK_STEP = {
    "payload": "linked:payload",
    "manifest": "linked:manifest",
    "checksum": "linked:checksum",
    "marker": "committed",
}


def _final(paths: ProjectPaths, artifact: str) -> Path:
    if artifact == "payload":
        return paths.root / "data" / "raw" / Path(RELATIVE)
    category, suffix = {
        "manifest": ("manifests", ".manifest.json"),
        "checksum": ("checksums", ".sha256"),
        "marker": ("manifests", ".commit.json"),
    }[artifact]
    return paths.root / "artifacts" / category / "raw" / Path(RELATIVE + suffix)


def _identity(path: Path) -> tuple[int, int]:
    status = path.lstat()
    return status.st_dev, status.st_ino


def _replace_with_foreign(path: Path, same_bytes: bool) -> tuple[bytes, tuple[int, int]]:
    """Another local writer removes this publisher's name and creates an independent file."""
    data = path.read_bytes() if same_bytes else b"independent foreign file"
    path.chmod(0o644)
    path.unlink()
    path.write_bytes(data)
    return data, _identity(path)


def _assert_foreign_untouched(path: Path, data: bytes, identity: tuple[int, int]) -> None:
    assert path.read_bytes() == data
    assert _identity(path) == identity
    assert path.lstat().st_mode & stat.S_IWRITE  # never chmod-ed to read-only


def _dead_publication_id(paths: ProjectPaths) -> str:
    journals = [name for name in rp.staging_artifacts(paths) if name.endswith(".journal")]
    assert len(journals) == 1
    return journals[0].removesuffix(".journal")


@pytest.mark.parametrize("same_bytes", [False, True], ids=["other-bytes", "same-bytes"])
@pytest.mark.parametrize("artifact", ["payload", "manifest", "checksum", "marker"])
def test_rollback_keeps_a_foreign_file_that_replaced_an_own_link(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch, artifact: str, same_bytes: bool
) -> None:
    victim = _final(paths, artifact)
    foreign: dict[str, object] = {}

    def hook(step: str) -> None:
        if step == _LINK_STEP[artifact]:
            foreign["data"], foreign["identity"] = _replace_with_foreign(victim, same_bytes)
            raise RuntimeError("injected failure after replacement")

    monkeypatch.setattr(rp, "_checkpoint", hook)

    with pytest.raises(RuntimeError, match="injected failure"):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    data, identity = foreign["data"], foreign["identity"]
    assert isinstance(data, bytes)
    assert isinstance(identity, tuple)
    _assert_foreign_untouched(victim, data, identity)
    assert _state(paths) == {victim: data}  # every own artifact, staging file, journal gone


@pytest.mark.parametrize("same_bytes", [False, True], ids=["other-bytes", "same-bytes"])
@pytest.mark.parametrize("artifact", ["payload", "manifest", "checksum"])
def test_post_commit_check_with_a_replaced_target_uncommits_first_and_keeps_foreign(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch, artifact: str, same_bytes: bool
) -> None:
    victim = _final(paths, artifact)
    foreign: dict[str, object] = {}
    removed: list[Path] = []
    real_remove = rp._remove

    def recording_remove(path: Path) -> None:
        removed.append(Path(path))
        real_remove(path)

    def hook(step: str) -> None:
        if step == "finalized":
            foreign["data"], foreign["identity"] = _replace_with_foreign(victim, same_bytes)

    monkeypatch.setattr(rp, "_remove", recording_remove)
    monkeypatch.setattr(rp, "_checkpoint", hook)

    with pytest.raises(RawIntegrityError):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())

    removed_finals = [p for p in removed if not p.is_relative_to(_workdir(paths))]
    assert removed_finals[0] == _final(paths, "marker")  # own marker removed first
    assert victim not in removed_finals
    data, identity = foreign["data"], foreign["identity"]
    assert isinstance(data, bytes)
    assert isinstance(identity, tuple)
    _assert_foreign_untouched(victim, data, identity)
    assert _state(paths) == {victim: data}
    with pytest.raises(RawUncommittedError):
        verify_published(paths, RELATIVE)


def test_recovery_rejects_a_foreign_marker_with_the_same_id_while_staging_marker_exists(
    paths: ProjectPaths,
) -> None:
    _kill_at(paths, "verified")  # three finals linked, four staging files, no marker
    publication_id = _dead_publication_id(paths)
    finals = [_final(paths, a) for a in ("payload", "manifest", "checksum")]
    # Schema-valid, same publication_id and even correct hashes: identity still decides.
    forged = RawCommitMarkerV1(
        marker_version="raw_commit_marker_v1",
        publication_id=publication_id,
        payload_path=f"data/raw/{RELATIVE}",
        payload_sha256=sha256_hex(finals[0].read_bytes()),
        manifest_sha256=sha256_hex(finals[1].read_bytes()),
        checksum_sha256=sha256_hex(finals[2].read_bytes()),
    )
    marker = _final(paths, "marker")
    marker.write_bytes(forged.model_dump_json().encode("utf-8"))
    data, identity = marker.read_bytes(), _identity(marker)

    report = recover_raw_publications(paths)

    assert report.completed == []
    assert report.rolled_back == [publication_id]
    assert [(c.publication_id, c.paths) for c in report.conflicts] == [(publication_id, (marker,))]
    _assert_foreign_untouched(marker, data, identity)
    assert not any(f.exists() for f in finals)  # own, identity-proven links rolled back
    assert _state(paths) == {marker: data}


def test_recovery_completes_a_verified_commit_after_staging_removal(paths: ProjectPaths) -> None:
    _kill_at(paths, "finalized")  # committed; staging removed; journal left behind
    publication_id = _dead_publication_id(paths)
    assert rp.staging_artifacts(paths) == [f"{publication_id}.journal"]

    report = recover_raw_publications(paths)

    assert report.completed == [publication_id]
    assert report.rolled_back == []
    assert report.conflicts == []
    assert rp.staging_artifacts(paths) == []
    verify_published(paths, RELATIVE)


def _forge_marker_with_wrong_hashes(paths: ProjectPaths, publication_id: str) -> None:
    marker = _final(paths, "marker")
    marker.chmod(0o644)
    marker.unlink()
    wrong = sha256_hex(b"something else")
    forged = RawCommitMarkerV1(
        marker_version="raw_commit_marker_v1",
        publication_id=publication_id,
        payload_path=f"data/raw/{RELATIVE}",
        payload_sha256=wrong,
        manifest_sha256=wrong,
        checksum_sha256=wrong,
    )
    marker.write_bytes(forged.model_dump_json().encode("utf-8"))


@pytest.mark.parametrize(
    "defect",
    [
        "missing-manifest",
        "missing-checksum",
        "corrupt-checksum",
        "corrupt-manifest",
        "forged-marker",
    ],
)
def test_recovery_never_completes_an_unverifiable_commit_and_touches_nothing(
    paths: ProjectPaths, defect: str
) -> None:
    _kill_at(paths, "finalized")  # staging marker already gone
    publication_id = _dead_publication_id(paths)
    if defect == "forged-marker":
        _forge_marker_with_wrong_hashes(paths, publication_id)
    else:
        target = _final(paths, defect.split("-")[1])
        target.chmod(0o644)
        if defect.startswith("missing"):
            target.unlink()
        else:
            target.write_bytes(target.read_bytes() + b"tampered")
    before = _state(paths)
    modes = {path: path.lstat().st_mode for path in before}

    report = recover_raw_publications(paths)  # must not raise (e.g. FileNotFoundError)

    assert report.completed == report.rolled_back == []
    assert [c.publication_id for c in report.conflicts] == [publication_id]
    assert report.conflicts[0].kind == "unverified_commit"
    assert _state(paths) == before  # nothing removed, journal kept: fails closed
    assert {path: path.lstat().st_mode for path in before} == modes
    with pytest.raises(RawCollisionError):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())


# --- readers, validation and paths -------------------------------------------------------------


@pytest.mark.parametrize("tamper", ["payload", "manifest", "checksum", "marker"])
def test_verification_detects_tampering(paths: ProjectPaths, tamper: str) -> None:
    published = publish_raw(paths, RELATIVE, PAYLOAD, fields())
    target = {
        "payload": published.payload,
        "manifest": published.manifest,
        "checksum": published.checksum,
        "marker": published.marker,
    }[tamper]
    target.chmod(0o644)
    if tamper == "manifest":
        record = json.loads(target.read_bytes())
        record["payload_bytes"] += 1
        target.write_bytes(json.dumps(record).encode())
    elif tamper == "marker":
        target.write_bytes(b"{not json")
    else:
        target.write_bytes(target.read_bytes() + b"x")

    with pytest.raises(RawIntegrityError):
        verify_published(paths, RELATIVE)


@pytest.mark.parametrize("missing", ["payload", "manifest", "checksum", "marker"])
def test_missing_artifact_is_never_valid(paths: ProjectPaths, missing: str) -> None:
    published = publish_raw(paths, RELATIVE, PAYLOAD, fields())
    target = getattr(published, missing)
    target.chmod(0o644)
    target.unlink()

    expected = RawUncommittedError if missing == "marker" else RawIntegrityError
    with pytest.raises(expected):
        verify_published(paths, RELATIVE)


def test_invalid_manifest_json_is_reported_as_integrity_error(paths: ProjectPaths) -> None:
    published = publish_raw(paths, RELATIVE, PAYLOAD, fields())
    published.manifest.chmod(0o644)
    published.manifest.write_bytes(b"{}")

    with pytest.raises(RawIntegrityError, match="manifest is invalid"):
        verify_published(paths, RELATIVE)


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_parameters": {"api_key": "synthetic"}},
        {"response_metadata": {"Authorization": "synthetic"}},
        {"response_metadata": {"x-note": "Bearer synthetic-value"}},
        {"cost_decision": "STOP_USER_DECISION"},
        {"estimated_cost": 0.0},
        {"retrieval_started_utc_ns": 5, "retrieval_completed_utc_ns": 4},
        {"canonical_contract": "GC.c.0"},
    ],
)
def test_invalid_or_secret_bearing_manifest_writes_nothing(
    paths: ProjectPaths, overrides: dict[str, object]
) -> None:
    with pytest.raises(ValidationError) as caught:
        publish_raw(paths, RELATIVE, PAYLOAD, fields(**overrides))

    assert "synthetic-value" not in str(caught.value)
    assert _state(paths) == {}


@pytest.mark.parametrize("computed", ["payload_sha256", "payload_bytes", "payload_path"])
def test_caller_cannot_supply_computed_provenance(paths: ProjectPaths, computed: str) -> None:
    with pytest.raises(RawPublicationError, match="computed"):
        publish_raw(paths, RELATIVE, PAYLOAD, fields(**{computed: "0" * 64}))


@pytest.mark.parametrize("relative", ["../escape.bin", "C:/x.bin", "a/../../b", "Upper/x.bin"])
def test_unsafe_relative_path_is_refused(paths: ProjectPaths, relative: str) -> None:
    with pytest.raises(UnsafeRelativePathError):
        publish_raw(paths, relative, PAYLOAD, fields())
    assert _state(paths) == {}


def test_junction_in_the_raw_tree_is_refused(
    paths: ProjectPaths, tmp_path: Path, junction: Callable[[Path, Path], None]
) -> None:
    raw = paths.category_dir(StorageCategory.RAW)
    raw.mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    junction(raw / "synthetic", tmp_path / "outside")

    with pytest.raises(PathEscapeError):
        publish_raw(paths, RELATIVE, PAYLOAD, fields())
    assert list((tmp_path / "outside").iterdir()) == []


def test_raw_relative_path_follows_the_daily_layout() -> None:
    assert raw_relative_path("comex", "glbx.mdp3", "gcz6", date(2026, 12, 1), "trades.dbn.zst") == (
        "comex/glbx.mdp3/gcz6/2026-12-01/trades.dbn.zst"
    )
    with pytest.raises(UnsafeRelativePathError):
        raw_relative_path("COMEX", "x", "gcz6", date(2026, 12, 1), "f")
