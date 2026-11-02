"""Storage-path guard (ADR-0002): every project path stays inside D:/project/moex-arbitrage-lab.

Failure scenarios adapted from moex-event-alpha ``tests/unit/test_storage_paths.py`` at commit
068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-019), with the new root, layout,
C: refusal and unresolved-variable checks. Filesystem tests build synthetic repositories under
``tmp_path`` and pass them as the required root through ``_resolve_project_paths``; only
``test_this_repository_resolves_to_the_required_root`` checks the real checkout.
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from moex_arbitrage_lab.settings import (
    PROJECT_NAME,
    REQUIRED_DRIVE,
    REQUIRED_ROOT,
    STORAGE_LAYOUT,
    STORAGE_ROOT_ENV,
    TOOL_LOCATION_ENV,
    PathEscapeError,
    ProjectPaths,
    ProjectRootError,
    StorageCategory,
    StoragePathError,
    UnsafeRelativePathError,
    environment_violations,
    resolve_project_paths,
    tool_location_violations,
    validate_relative_path,
)
from moex_arbitrage_lab.settings.storage_paths import _resolve_project_paths, default_project_root

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows drive semantics")
Junction = Callable[[Path, Path], None]

# --- relative-path grammar ----------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "a",
        "0",
        "comex/glbx.mdp3/gcz6/2026-12-01/trades.dbn.zst",
        "moex/tbank-archive/gdz6/2026-12-01/day.zip",
        "ab/cd/" + "0" * 64,
        "console",
        "nul_x",
        "x" * 160,
    ],
)
def test_valid_relative_paths_are_accepted(relative: str) -> None:
    assert validate_relative_path(relative) == tuple(relative.split("/"))


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "x" * 161,
        "/abs",
        "//server/share/x",
        "a//b",
        "a/",
        ".",
        "./a",
        "..",
        "../x",
        "a/../b",
        "a\\b",
        "..\\x",
        "C:/x",
        "C:x",
        "c:x",
        "file.bin:stream",
        "a.",
        "a b",
        "A",
        "Data/raw",
        "progra~1",
        "~",
        ".hidden",
        "-a",
        "%TEMP%",
        "a/%userprofile%/b",
        "$HOME",
        "${TMP}/x",
        "a\x00b",
        "a\nb",
        chr(0x0430),  # Cyrillic homoglyph of "a"
        "con",
        "nul.json",
        "com1",
        "a/prn/b",
        "a*",
        "a?",
        "a|b",
    ],
)
def test_traversal_and_unsafe_relative_paths_are_refused(relative: str) -> None:
    with pytest.raises(UnsafeRelativePathError):
        validate_relative_path(relative)


# --- layout -------------------------------------------------------------------------------


def test_layout_matches_the_storage_policy() -> None:
    assert {category: location.as_posix() for category, location in STORAGE_LAYOUT.items()} == {
        StorageCategory.RAW: "data/raw",
        StorageCategory.NORMALIZED: "data/normalized",
        StorageCategory.ALIGNED: "data/aligned",
        StorageCategory.MANIFESTS: "artifacts/manifests",
        StorageCategory.CHECKSUMS: "artifacts/checksums",
        StorageCategory.REPORTS: "reports/generated",
        StorageCategory.CACHE: "cache",
        StorageCategory.TEMP: "tmp",
        StorageCategory.LOGS: "logs",
        StorageCategory.VIRTUALENV: ".venv",
    }


def test_categories_are_relative_and_not_nested() -> None:
    locations = list(STORAGE_LAYOUT.values())
    assert len(set(locations)) == len(locations)
    for outer in locations:
        assert not outer.is_absolute()
        assert ".." not in outer.parts
        for inner in locations:
            assert outer == inner or not inner.is_relative_to(outer)


def test_tool_locations_are_inside_cache_temp_or_virtualenv() -> None:
    allowed = {StorageCategory.CACHE, StorageCategory.TEMP, StorageCategory.VIRTUALENV}
    for category, relative in TOOL_LOCATION_ENV.values():
        assert category in allowed
        assert relative is None or validate_relative_path(relative)


def test_required_root_is_fixed_and_not_on_c() -> None:
    # Changing the required root is an ADR-0002 decision.
    assert PureWindowsPath("D:/project/moex-arbitrage-lab") == REQUIRED_ROOT
    assert REQUIRED_DRIVE == "D:"
    assert "users" not in {part.lower() for part in REQUIRED_ROOT.parts}


# --- project root ---------------------------------------------------------------------------


def test_valid_repository_root_is_accepted(repo: Path) -> None:
    assert _resolve_project_paths(repo, Path(str(repo).upper())).root == repo


def test_project_paths_cannot_be_built_around_the_guard(repo: Path) -> None:
    with pytest.raises(ProjectRootError):
        ProjectPaths(repo, object())


def test_relative_root_is_refused() -> None:
    with pytest.raises(ProjectRootError, match="absolute"):
        _resolve_project_paths(Path("repo"), REQUIRED_ROOT)


@pytest.mark.parametrize(
    ("candidate", "required"),
    [
        ("C:/project/moex-arbitrage-lab", "C:/project/moex-arbitrage-lab"),
        ("c:/Users/someone/moex-arbitrage-lab", "c:/Users/someone/moex-arbitrage-lab"),
        ("D:/project/moex-arbitrage-lab", "C:/project/moex-arbitrage-lab"),
    ],
)
def test_project_storage_on_c_is_refused_before_touching_disk(
    candidate: str, required: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_touch(self: Path) -> os.stat_result:
        raise AssertionError("the disk must not be touched for a C: root")

    monkeypatch.setattr(Path, "lstat", must_not_touch)

    with pytest.raises(ProjectRootError, match="on C: is refused"):
        _resolve_project_paths(Path(candidate), PureWindowsPath(required))


def test_missing_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProjectRootError):
        _resolve_project_paths(tmp_path / "missing", tmp_path / "missing")


def test_file_as_root_is_refused(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")

    with pytest.raises(ProjectRootError):
        _resolve_project_paths(file, file)


@pytest.mark.parametrize("where", ["sibling", "required_root"])
def test_root_other_than_the_required_root_is_refused(repo: Path, where: str) -> None:
    required = repo.parent / "moex-arbitrage-lab" if where == "sibling" else REQUIRED_ROOT

    with pytest.raises(ProjectRootError, match="project root must be"):
        _resolve_project_paths(repo, required)


def test_root_reached_through_a_junction_is_refused(
    repo: Path, tmp_path: Path, junction: Junction
) -> None:
    link = tmp_path / "alias"
    junction(link, repo)

    with pytest.raises(ProjectRootError):
        _resolve_project_paths(link, link)


def test_root_below_a_junction_is_refused(repo: Path, tmp_path: Path, junction: Junction) -> None:
    link = tmp_path / "alias"
    junction(link, tmp_path)

    with pytest.raises(ProjectRootError, match="link or alias"):
        _resolve_project_paths(link / "repo", link / "repo")


@pytest.mark.parametrize("git", ["missing", "worktree_file"])
def test_root_without_main_git_checkout_is_refused(repo: Path, git: str) -> None:
    (repo / ".git").rmdir()
    if git == "worktree_file":
        (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    with pytest.raises(ProjectRootError, match="main Git checkout"):
        _resolve_project_paths(repo, repo)


@pytest.mark.parametrize(
    "pyproject",
    [None, '[project]\nname = "moex-event-alpha"\n', "[tool.x]\n", "not = toml = ["],
)
def test_root_of_another_project_is_refused(repo: Path, pyproject: str | None) -> None:
    target = repo / "pyproject.toml"
    if pyproject is None:
        target.unlink()
    else:
        target.write_text(pyproject, encoding="utf-8")

    with pytest.raises(ProjectRootError, match="repository root"):
        _resolve_project_paths(repo, repo)


def test_default_root_is_this_checkout() -> None:
    assert (default_project_root() / "pyproject.toml").is_file()
    assert (default_project_root() / "src" / "moex_arbitrage_lab").is_dir()


def test_this_repository_resolves_to_the_required_root() -> None:
    project = resolve_project_paths()

    assert str(project.root).lower() == str(REQUIRED_ROOT).lower()
    assert PROJECT_NAME == "moex-arbitrage-lab"
    for category in StorageCategory:
        location = project.category_dir(category)
        assert location.drive.upper() == REQUIRED_DRIVE
        assert location.is_relative_to(project.root)


# --- paths below the root -------------------------------------------------------------------


def test_category_dirs_are_checked_without_being_created(paths: ProjectPaths) -> None:
    for category, relative in STORAGE_LAYOUT.items():
        location = paths.category_dir(category)
        assert location == paths.root.joinpath(*relative.parts)
        assert not location.exists()


def test_valid_path_resolves_inside_its_category(paths: ProjectPaths) -> None:
    target = paths.resolve(StorageCategory.RAW, "comex/ds/gcz6/2026-12-01/day.bin")

    assert target == paths.root / "data" / "raw" / "comex" / "ds" / "gcz6" / "2026-12-01" / (
        "day.bin"
    )
    assert paths.relative_to_root(target) == "data/raw/comex/ds/gcz6/2026-12-01/day.bin"


def test_unsafe_relative_path_is_refused_before_touching_disk(paths: ProjectPaths) -> None:
    with pytest.raises(UnsafeRelativePathError):
        paths.resolve(StorageCategory.RAW, "../../outside")


@pytest.mark.parametrize("inside", [False, True])
def test_junction_below_a_category_is_refused(
    paths: ProjectPaths, tmp_path: Path, junction: Junction, inside: bool
) -> None:
    raw = paths.category_dir(StorageCategory.RAW)
    raw.mkdir(parents=True)
    target = paths.root / "logs" if inside else tmp_path / "outside"
    target.mkdir()
    junction(raw / "escape", target)

    with pytest.raises(PathEscapeError, match="reparse point"):
        paths.resolve(StorageCategory.RAW, "escape/day.bin")


def test_category_dir_that_is_a_junction_is_refused(
    paths: ProjectPaths, tmp_path: Path, junction: Junction
) -> None:
    (paths.root / "data").mkdir()
    (tmp_path / "outside").mkdir()
    junction(paths.root / "data" / "raw", tmp_path / "outside")

    with pytest.raises(PathEscapeError):
        paths.category_dir(StorageCategory.RAW)


def test_intermediate_junction_is_refused(
    paths: ProjectPaths, tmp_path: Path, junction: Junction
) -> None:
    (tmp_path / "outside" / "raw").mkdir(parents=True)
    junction(paths.root / "data", tmp_path / "outside")

    with pytest.raises(PathEscapeError):
        paths.resolve(StorageCategory.RAW, "day.bin")


def test_symlink_is_refused(paths: ProjectPaths, tmp_path: Path) -> None:
    logs = paths.category_dir(StorageCategory.LOGS)
    logs.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("x", encoding="utf-8")
    try:
        (logs / "link.log").symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or elevation on this machine")

    with pytest.raises(PathEscapeError, match="reparse point"):
        paths.resolve(StorageCategory.LOGS, "link.log")


def test_hard_linked_file_is_refused(paths: ProjectPaths, tmp_path: Path) -> None:
    raw = paths.category_dir(StorageCategory.RAW)
    raw.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"synthetic")
    os.link(outside, raw / "day.bin")

    with pytest.raises(PathEscapeError, match="hard link"):
        paths.resolve(StorageCategory.RAW, "day.bin")


def test_target_that_resolves_elsewhere_is_refused(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulates an alias the reparse check cannot see (8.3 short name, subst drive).
    elsewhere = paths.root.parent / "elsewhere"
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: elsewhere)

    with pytest.raises(PathEscapeError, match="resolve to itself"):
        paths.category_dir(StorageCategory.LOGS)


def test_uninspectable_component_fails_closed(
    paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(self: Path) -> os.stat_result:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "lstat", denied)

    with pytest.raises(StoragePathError, match="cannot inspect"):
        paths.category_dir(StorageCategory.LOGS)


# --- tool cache/temp/virtualenv redirection and root override --------------------------------


def _expected_env(paths: ProjectPaths) -> dict[str, str]:
    env: dict[str, str] = {}
    for name, (category, relative) in TOOL_LOCATION_ENV.items():
        location = paths.category_dir(category)
        env[name] = str(location if relative is None else location / relative)
    return env


def test_correct_tool_locations_have_no_violations(paths: ProjectPaths) -> None:
    env = _expected_env(paths)
    env["TMP"] = env["TMP"].upper() + "\\"
    env[STORAGE_ROOT_ENV] = str(paths.root)

    assert environment_violations({**env, "UNRELATED": "C:\\x"}, paths) == []


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "relative-cache",
        "C:\\Users\\someone\\AppData\\Local\\Temp",
        "D:\\uv-cache",
        "%LOCALAPPDATA%\\uv",
        "$HOME/uv",
        "~/uv",
    ],
)
def test_missing_misdirected_or_unresolved_tool_location_is_reported(
    paths: ProjectPaths, value: str | None
) -> None:
    env = _expected_env(paths)
    if value is None:
        del env["TEMP"]
    else:
        env["TEMP"] = value

    violations = tool_location_violations(env, paths)

    assert len(violations) == 1
    assert violations[0].startswith("TEMP ")
    if value:
        assert value not in violations[0]


@pytest.mark.parametrize("value", ["C:\\data", "D:\\other", "%USERPROFILE%\\lab", "relative"])
def test_storage_root_override_must_name_the_root(paths: ProjectPaths, value: str) -> None:
    env = {**_expected_env(paths), STORAGE_ROOT_ENV: value}

    violations = environment_violations(env, paths)

    assert len(violations) == 1
    assert violations[0].startswith(STORAGE_ROOT_ENV)
    assert value not in violations[0]


def test_layout_paths_are_posix() -> None:
    for location in STORAGE_LAYOUT.values():
        assert location == PurePosixPath(location.as_posix())
