"""SQLite persistence layer for pbinator.

Pure-logic module: takes paths and connections in, returns Python data.
No Streamlit, no env reads, no global state.
"""

import sqlite3
from pathlib import Path

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
