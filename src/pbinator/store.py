"""SQLite persistence layer for pbinator.

Pure-logic module: takes paths and connections in, returns Python data.
No Streamlit, no env reads, no global state.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS activity (
    athlete_id        INTEGER NOT NULL,
    activity_id       INTEGER NOT NULL,
    sport_type        TEXT    NOT NULL,
    start_date        TEXT    NOT NULL,
    distance_m        REAL    NOT NULL,
    moving_time_s     INTEGER NOT NULL,
    elapsed_time_s    INTEGER NOT NULL,
    total_elev_gain_m REAL    NOT NULL,
    name              TEXT    NOT NULL,
    raw_json          TEXT    NOT NULL,
    fetched_at        TEXT    NOT NULL,
    PRIMARY KEY (athlete_id, activity_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_athlete_date
    ON activity (athlete_id, start_date DESC);

CREATE INDEX IF NOT EXISTS idx_activity_athlete_sport
    ON activity (athlete_id, sport_type, start_date DESC);

CREATE TABLE IF NOT EXISTS sync_cursor (
    athlete_id           INTEGER PRIMARY KEY,
    last_activity_start  TEXT,
    last_synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS best_effort (
    athlete_id     INTEGER NOT NULL,
    activity_id    INTEGER NOT NULL,
    distance_label TEXT    NOT NULL,
    distance_m     REAL    NOT NULL,
    moving_time_s  INTEGER NOT NULL,
    elapsed_time_s INTEGER NOT NULL,
    start_date     TEXT    NOT NULL,
    PRIMARY KEY (athlete_id, activity_id, distance_label),
    FOREIGN KEY (athlete_id, activity_id)
        REFERENCES activity(athlete_id, activity_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_best_effort_athlete_label_time
    ON best_effort (athlete_id, distance_label, moving_time_s);
"""


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Idempotent ALTER TABLE … ADD COLUMN guarded by PRAGMA table_info.

    SQLite raises if the column already exists; this skips that path so
    ``connect`` can re-run safely on an already-upgraded database.
    """
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        # Both `table` and `column` come from internal hardcoded calls; no
        # user-controlled SQL.
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def connect(path: Path) -> sqlite3.Connection:
    """Open the pbinator SQLite database, bootstrapping the schema if needed.

    Returns:
        A connection with ``Row`` factory, WAL journaling, and foreign-key
        enforcement enabled.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    _add_column_if_missing(conn, "activity", "start_date_local", "TEXT")
    _add_column_if_missing(conn, "activity", "best_efforts_fetched_at", "TEXT")
    with conn:
        conn.execute(
            "UPDATE activity "
            "SET start_date_local = json_extract(raw_json, '$.start_date_local') "
            "WHERE start_date_local IS NULL"
        )
    return conn


_UPSERT_ACTIVITY_SQL = """
INSERT INTO activity (
    athlete_id, activity_id, sport_type, start_date, start_date_local,
    distance_m, moving_time_s, elapsed_time_s, total_elev_gain_m,
    name, raw_json, fetched_at
) VALUES (
    :athlete_id, :activity_id, :sport_type, :start_date, :start_date_local,
    :distance_m, :moving_time_s, :elapsed_time_s, :total_elev_gain_m,
    :name, :raw_json, :fetched_at
)
ON CONFLICT(athlete_id, activity_id) DO UPDATE SET
    sport_type        = excluded.sport_type,
    start_date        = excluded.start_date,
    start_date_local  = excluded.start_date_local,
    distance_m        = excluded.distance_m,
    moving_time_s     = excluded.moving_time_s,
    elapsed_time_s    = excluded.elapsed_time_s,
    total_elev_gain_m = excluded.total_elev_gain_m,
    name              = excluded.name,
    raw_json          = excluded.raw_json,
    fetched_at        = excluded.fetched_at
"""


def upsert_activity(
    conn: sqlite3.Connection,
    *,
    athlete_id: int,
    activity: dict[str, Any],
) -> None:
    """Insert or update one SummaryActivity for an athlete."""
    conn.execute(
        _UPSERT_ACTIVITY_SQL,
        {
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
        },
    )


@dataclass(frozen=True)
class SyncCursor:
    """Per-athlete sync progress.

    ``last_activity_start`` is the ISO-UTC ``start_date`` of the newest
    activity we have stored. ``last_synced_at`` is the ISO-UTC timestamp
    when the most recent sync (successful, partial, or errored) finished.
    """

    last_activity_start: str | None
    last_synced_at: str | None


_GET_CURSOR_SQL = """
SELECT last_activity_start, last_synced_at
FROM sync_cursor
WHERE athlete_id = ?
"""

_UPSERT_CURSOR_SQL = """
INSERT INTO sync_cursor (athlete_id, last_activity_start, last_synced_at)
VALUES (:athlete_id, :last_activity_start, :last_synced_at)
ON CONFLICT(athlete_id) DO UPDATE SET
    last_activity_start = excluded.last_activity_start,
    last_synced_at      = excluded.last_synced_at
"""


def get_cursor(conn: sqlite3.Connection, athlete_id: int) -> SyncCursor | None:
    """Return the sync cursor for ``athlete_id``, or None if no sync has run yet.

    Returns:
        A ``SyncCursor`` with the stored timestamps, or ``None``.
    """
    row = conn.execute(_GET_CURSOR_SQL, (athlete_id,)).fetchone()
    if row is None:
        return None
    return SyncCursor(
        last_activity_start=row["last_activity_start"],
        last_synced_at=row["last_synced_at"],
    )


def update_cursor(
    conn: sqlite3.Connection,
    *,
    athlete_id: int,
    last_activity_start: str | None,
    last_synced_at: str,
) -> None:
    """Insert or update the sync cursor for ``athlete_id``."""
    conn.execute(
        _UPSERT_CURSOR_SQL,
        {
            "athlete_id": athlete_id,
            "last_activity_start": last_activity_start,
            "last_synced_at": last_synced_at,
        },
    )


def count_activities(conn: sqlite3.Connection, athlete_id: int) -> int:
    """Return the number of stored activities for ``athlete_id``.

    Returns:
        The row count, scoped to that athlete.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM activity WHERE athlete_id = ?",
        (athlete_id,),
    ).fetchone()
    return int(row["n"])


def delete_activities_not_in(
    conn: sqlite3.Connection,
    *,
    athlete_id: int,
    kept_ids: set[int],
) -> int:
    """Delete this athlete's activities whose id is NOT in ``kept_ids``.

    Returns:
        The number of rows deleted.

    Notes:
        Building a parameterised IN-clause SQL string from a set of ints is safe
        because every value is coerced to int before substitution, and SQLite's
        IN-clause has no parameter-list shortcut for arbitrary-length lists.
    """
    if not kept_ids:
        cursor = conn.execute(
            "DELETE FROM activity WHERE athlete_id = ?",
            (athlete_id,),
        )
        return cursor.rowcount

    placeholders = ",".join("?" * len(kept_ids))
    # placeholders is ?,… only; values pass via parameters
    sql = f"DELETE FROM activity WHERE athlete_id = ? AND activity_id NOT IN ({placeholders})"  # noqa: S608
    cursor = conn.execute(sql, (athlete_id, *(int(i) for i in kept_ids)))
    return cursor.rowcount
