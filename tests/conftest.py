from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from pbinator import store


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
