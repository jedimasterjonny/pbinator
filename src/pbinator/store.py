"""SQLite persistence layer for pbinator.

Pure-logic module: takes paths and connections in, returns Python data.
No Streamlit, no env reads, no global state.
"""

import json
import sqlite3
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
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open the pbinator SQLite database, bootstrapping the schema if needed.

    Returns:
        A connection with ``Row`` factory and WAL journaling enabled.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA_SQL)
    return conn


_UPSERT_ACTIVITY_SQL = """
INSERT INTO activity (
    athlete_id, activity_id, sport_type, start_date,
    distance_m, moving_time_s, elapsed_time_s, total_elev_gain_m,
    name, raw_json, fetched_at
) VALUES (
    :athlete_id, :activity_id, :sport_type, :start_date,
    :distance_m, :moving_time_s, :elapsed_time_s, :total_elev_gain_m,
    :name, :raw_json, :fetched_at
)
ON CONFLICT(athlete_id, activity_id) DO UPDATE SET
    sport_type        = excluded.sport_type,
    start_date        = excluded.start_date,
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
            "distance_m": float(activity["distance"]),
            "moving_time_s": int(activity["moving_time"]),
            "elapsed_time_s": int(activity["elapsed_time"]),
            "total_elev_gain_m": float(activity["total_elevation_gain"]),
            "name": str(activity["name"]),
            "raw_json": json.dumps(activity, separators=(",", ":")),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
