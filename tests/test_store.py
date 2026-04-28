import sqlite3  # noqa: F401 — used in later test additions
from pathlib import Path

import pytest

from pbinator import store


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "pbinator.db"


def test_connect_bootstraps_schema(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert tables == {"activity", "sync_cursor"}


def test_connect_is_idempotent(db_path: Path) -> None:
    store.connect(db_path).close()
    # Second call must succeed even though the schema already exists.
    conn = store.connect(db_path)
    try:
        result = conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()

    assert result[0] == 1


def test_connect_uses_row_factory(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        row = conn.execute("SELECT 1 AS one").fetchone()
    finally:
        conn.close()

    assert row["one"] == 1


def test_connect_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "subdir" / "pbinator.db"

    conn = store.connect(nested)
    try:
        assert nested.exists()
    finally:
        conn.close()
