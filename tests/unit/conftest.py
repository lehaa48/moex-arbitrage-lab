"""Unit-test guards and synthetic project roots.

Offline guard: every test runs with Python-level socket connections and name resolution
refused, so no test can reach any host. Synthetic repositories live under ``tmp_path``, which
pytest places in the project ``tmp/pytest`` (``--basetemp`` in pyproject.toml).
"""

import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from moex_arbitrage_lab.settings import PROJECT_NAME, ProjectPaths
from moex_arbitrage_lab.settings.storage_paths import _resolve_project_paths


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


def make_repo(root: Path, name: str = PROJECT_NAME) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n', encoding="utf-8")
    return root


def make_junction(link: Path, target: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


@pytest.fixture
def junction() -> Iterator[Callable[[Path, Path], None]]:
    """``junction(link, target)`` creates an NTFS junction (no elevation needed).

    Every junction is removed at teardown (``rmdir`` removes the link, never the target), so
    no link is left in the project tmp for the storage report to flag.
    """
    created: list[Path] = []

    def create(link: Path, target: Path) -> None:
        make_junction(link, target)
        created.append(link)

    yield create
    for link in reversed(created):
        if link.is_junction():
            link.rmdir()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo")


@pytest.fixture
def paths(repo: Path) -> ProjectPaths:
    return _resolve_project_paths(repo, repo)
