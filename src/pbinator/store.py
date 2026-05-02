"""SQLModel-backed persistence layer for pbinator.

Pure-logic module: takes paths or Sessions in, returns Python data.
No Streamlit, no env reads, no global state.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import event, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import SQLModel, create_engine, select

from pbinator import models  # noqa: F401 — registers tables on SQLModel.metadata
from pbinator.models import Activity, BestEffort, SyncCursor

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator, Sequence
    from pathlib import Path
    from typing import Any

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from pbinator.best_efforts import BestEffortRow


@contextmanager
def write_transaction(session: Session) -> Iterator[None]:
    """Commit on clean exit, rollback on exception.

    Drop-in replacement for ``with conn:`` from the sqlite3 era. Works
    whether or not the session is in an autobegun read transaction —
    ``commit()`` flushes any pending state; ``rollback()`` unwinds it.

    SQLAlchemy 2.0's ``Session.begin()`` raises ``InvalidRequestError`` if
    the session has already autobegun a transaction (e.g. from a prior
    read), so it is unsafe to use as a generic per-write boundary. This
    helper sidesteps that problem.
    """
    try:
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


def _add_column_if_missing(conn: sa.Connection, table: str, column: str, ddl: str) -> None:
    """Idempotent ALTER TABLE … ADD COLUMN guarded by PRAGMA table_info.

    Survives until Alembic lands. ``table`` and ``column`` come from
    internal hardcoded calls; no user-controlled SQL.
    """
    rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    existing = {row[1] for row in rows}  # row[1] = column name
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def make_engine(path: Path) -> Engine:
    """Open the pbinator SQLite engine, bootstrapping the schema if needed.

    Returns:
        A SQLAlchemy ``Engine`` with WAL journaling and foreign-key
        enforcement enabled. Schema is created on first use; post-launch
        columns are added if absent.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _set_pragmas(
        dbapi_connection: sqlite3.Connection,
        _connection_record: Any,  # noqa: ANN401 — opaque pool record from SQLAlchemy
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        _add_column_if_missing(conn, "activity", "start_date_local", "TEXT")
        _add_column_if_missing(conn, "activity", "best_efforts_fetched_at", "TEXT")
        conn.execute(
            text(
                "UPDATE activity "
                "SET start_date_local = json_extract(raw_json, '$.start_date_local') "
                "WHERE start_date_local IS NULL"
            )
        )
    return engine


def _upsert_activity_stmt(values: dict[str, Any]) -> sa.Executable:
    stmt = sqlite_insert(Activity).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=["athlete_id", "activity_id"],
        set_={
            "sport_type": stmt.excluded.sport_type,
            "start_date": stmt.excluded.start_date,
            "start_date_local": stmt.excluded.start_date_local,
            "distance_m": stmt.excluded.distance_m,
            "moving_time_s": stmt.excluded.moving_time_s,
            "elapsed_time_s": stmt.excluded.elapsed_time_s,
            "total_elev_gain_m": stmt.excluded.total_elev_gain_m,
            "name": stmt.excluded.name,
            "raw_json": stmt.excluded.raw_json,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )


def upsert_activity(
    session: Session,
    *,
    athlete_id: int,
    activity: dict[str, Any],
) -> None:
    """Insert or update one SummaryActivity for an athlete."""
    values = {
        "athlete_id": athlete_id,
        "activity_id": int(activity["id"]),
        "sport_type": str(activity["sport_type"]),
        "start_date": str(activity["start_date"]),
        "start_date_local": str(activity["start_date_local"]),
        "distance_m": float(activity["distance"]),
        "moving_time_s": int(activity["moving_time"]),
        "elapsed_time_s": int(activity["elapsed_time"]),
        "total_elev_gain_m": float(activity["total_elevation_gain"]),
        "name": str(activity["name"]),
        "raw_json": json.dumps(activity, separators=(",", ":")),
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    session.execute(_upsert_activity_stmt(values))


def _upsert_best_effort_stmt() -> sa.Executable:
    """Build a single ``INSERT … ON CONFLICT DO UPDATE`` for best_effort rows.

    Used with executemany-style ``session.execute(stmt, [{...}, ...])``.

    Returns:
        A pre-built upsert statement to be executed with row-dict bindings.
    """
    stmt = sqlite_insert(BestEffort)
    return stmt.on_conflict_do_update(
        index_elements=["athlete_id", "activity_id", "distance_label"],
        set_={
            "distance_m": stmt.excluded.distance_m,
            "moving_time_s": stmt.excluded.moving_time_s,
            "elapsed_time_s": stmt.excluded.elapsed_time_s,
            "start_date": stmt.excluded.start_date,
        },
    )


def upsert_best_efforts(
    session: Session,
    *,
    athlete_id: int,
    activity_id: int,
    efforts: Sequence[BestEffortRow],
) -> None:
    """Insert or update one activity's set of best_effort rows."""
    if not efforts:
        return
    rows = [
        {
            "athlete_id": athlete_id,
            "activity_id": activity_id,
            "distance_label": e.distance_label,
            "distance_m": e.distance_m,
            "moving_time_s": e.moving_time_s,
            "elapsed_time_s": e.elapsed_time_s,
            "start_date": e.start_date,
        }
        for e in efforts
    ]
    session.execute(_upsert_best_effort_stmt(), rows)


def get_cursor(session: Session, athlete_id: int) -> SyncCursor | None:
    """Return the sync cursor for ``athlete_id``, or None if no sync has run yet."""
    return session.get(SyncCursor, athlete_id)


def update_cursor(
    session: Session,
    *,
    athlete_id: int,
    last_activity_start: str | None,
    last_synced_at: str,
) -> None:
    """Insert or update the sync cursor for ``athlete_id``."""
    stmt = sqlite_insert(SyncCursor).values(
        athlete_id=athlete_id,
        last_activity_start=last_activity_start,
        last_synced_at=last_synced_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["athlete_id"],
        set_={
            "last_activity_start": stmt.excluded.last_activity_start,
            "last_synced_at": stmt.excluded.last_synced_at,
        },
    )
    session.execute(stmt)


def count_activities(session: Session, athlete_id: int) -> int:
    """Return the number of stored activities for ``athlete_id``."""
    result = session.execute(
        select(sa.func.count()).select_from(Activity).where(Activity.athlete_id == athlete_id)
    )
    return int(result.scalar_one())


def delete_activities_not_in(session: Session, *, athlete_id: int, kept_ids: set[int]) -> int:
    """Delete this athlete's activities whose id is NOT in ``kept_ids``.

    Returns:
        The number of rows deleted (matches today's ``cursor.rowcount``).
    """
    # ty does not model SQLModel column descriptors — Activity.athlete_id is
    # typed `int` even though at runtime it's a SQLAlchemy column expression.
    stmt = sa.delete(Activity).where(Activity.athlete_id == athlete_id)  # ty: ignore[invalid-argument-type]
    if kept_ids:
        stmt = stmt.where(Activity.activity_id.not_in(kept_ids))  # ty: ignore[unresolved-attribute]
    result = session.execute(stmt)
    # Result.rowcount exists on CursorResult (returned for DML); ty stubs only expose Result[Any].
    return int(result.rowcount)  # ty: ignore[unresolved-attribute]


def mark_detail_fetched(
    session: Session,
    *,
    athlete_id: int,
    activity_id: int,
    fetched_at: str,
) -> None:
    """Stamp ``best_efforts_fetched_at`` on one activity row."""
    session.execute(
        sa.update(Activity)
        .where(
            Activity.athlete_id == athlete_id,  # ty: ignore[invalid-argument-type]
            Activity.activity_id == activity_id,  # ty: ignore[invalid-argument-type]
        )
        .values(best_efforts_fetched_at=fetched_at)
    )


def count_runs_awaiting_detail(session: Session, athlete_id: int) -> int:
    """Return the count of Run activities for ``athlete_id`` lacking detail."""
    result = session.execute(
        select(sa.func.count())
        .select_from(Activity)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.sport_type == "Run",
            Activity.best_efforts_fetched_at.is_(None),  # ty: ignore[unresolved-attribute]
        )
    )
    return int(result.scalar_one())


def already_fetched_run_ids(
    session: Session,
    *,
    athlete_id: int,
    run_ids: list[int],
) -> set[int]:
    """Return the subset of ``run_ids`` for ``athlete_id`` already detail-fetched.

    Replaces the ad-hoc ``IN (?,?,?)`` query that previously lived in
    ``sync._process_detail_fetches_for_page``.
    """
    if not run_ids:
        return set()
    result = session.execute(
        select(Activity.activity_id).where(
            Activity.athlete_id == athlete_id,
            Activity.activity_id.in_(run_ids),  # ty: ignore[unresolved-attribute]
            Activity.best_efforts_fetched_at.is_not(None),  # ty: ignore[unresolved-attribute]
        )
    )
    return {int(row[0]) for row in result}
