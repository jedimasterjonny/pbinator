import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Final, NoReturn

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from pbinator import store

_REAL_SOCKET: Final[type[socket.socket]] = socket.socket


class NetworkBlockedError(RuntimeError):
    """Raised when test code attempts to open a network socket."""


def _blocked_socket(*_args: object, **_kwargs: object) -> NoReturn:
    msg = (
        "Network access is blocked in tests. Mock the call (e.g. with respx) "
        "or request the `allow_socket` fixture for this test."
    )
    raise NetworkBlockedError(msg)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block ``socket.socket`` for every test; tripwire for unmocked network calls."""
    monkeypatch.setattr(socket, "socket", _blocked_socket)


@pytest.fixture
def allow_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-out of the network block for a single test that genuinely needs sockets."""
    monkeypatch.setattr(socket, "socket", _REAL_SOCKET)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A throwaway SQLite DB path under the test's tmp dir.

    Returns:
        Path to a fresh, non-existent ``pbinator.db`` under ``tmp_path``.
    """
    return tmp_path / "pbinator.db"


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    """A real SQLite engine, schema bootstrapped, scoped to one test.

    Yields:
        A live ``Engine``; disposed on teardown.
    """
    eng = store.make_engine(db_path)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A fresh Session against the test engine.

    Does NOT auto-commit on teardown — production code must call
    ``session.commit()`` (or use ``with store.write_transaction(session):``)
    where it would have committed today. This catches missing-commit bugs
    instead of masking them.

    Yields:
        A live ``Session`` bound to the test engine.
    """
    with Session(engine) as sess:
        yield sess
