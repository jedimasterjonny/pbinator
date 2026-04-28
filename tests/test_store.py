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


def _summary_activity(  # noqa: PLR0913 — test helper builder
    *,
    activity_id: int = 100,
    name: str = "Morning Run",
    sport_type: str = "Run",
    start_date: str = "2024-04-15T07:00:00Z",
    distance: float = 5023.4,
    moving_time: int = 1500,
    elapsed_time: int = 1530,
    total_elevation_gain: float = 47.0,
) -> dict[str, object]:
    """Minimal SummaryActivity-shaped dict for tests.

    Returns:
        A dict matching Strava SummaryActivity schema.
    """
    return {
        "id": activity_id,
        "name": name,
        "sport_type": sport_type,
        "start_date": start_date,
        "distance": distance,
        "moving_time": moving_time,
        "elapsed_time": elapsed_time,
        "total_elevation_gain": total_elevation_gain,
        "extra_field": "ignored-but-kept-in-raw-json",
    }


def test_upsert_inserts_new_row(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=42, activity=_summary_activity())
        row = conn.execute(
            "SELECT * FROM activity WHERE athlete_id = ? AND activity_id = ?",
            (42, 100),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["sport_type"] == "Run"
    assert abs(row["distance_m"] - 5023.4) < 1e-9  # float equality
    assert row["moving_time_s"] == 1500
    assert "extra_field" in row["raw_json"]


def test_upsert_overwrites_mutable_fields(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=42, activity=_summary_activity(name="Old"))
        store.upsert_activity(
            conn,
            athlete_id=42,
            activity=_summary_activity(name="Renamed", distance=6000.0),
        )
        rows = conn.execute(
            "SELECT name, distance_m FROM activity WHERE athlete_id = ?",
            (42,),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["name"] == "Renamed"
    assert abs(rows[0]["distance_m"] - 6000.0) < 1e-9  # float equality


def test_upsert_scopes_by_athlete(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=1, activity=_summary_activity(activity_id=5))
        store.upsert_activity(conn, athlete_id=2, activity=_summary_activity(activity_id=5))
        count = conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
    finally:
        conn.close()

    assert count == 2  # same activity_id under different athletes is fine


def test_get_cursor_returns_none_when_absent(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert cursor is None


def test_update_cursor_inserts_then_updates(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.update_cursor(
            conn,
            athlete_id=42,
            last_activity_start="2024-04-15T07:00:00Z",
            last_synced_at="2024-04-15T08:00:00Z",
        )
        first = store.get_cursor(conn, athlete_id=42)

        store.update_cursor(
            conn,
            athlete_id=42,
            last_activity_start="2024-05-01T07:00:00Z",
            last_synced_at="2024-05-01T08:00:00Z",
        )
        second = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert first is not None
    assert first.last_activity_start == "2024-04-15T07:00:00Z"
    assert second is not None
    assert second.last_activity_start == "2024-05-01T07:00:00Z"
    assert second.last_synced_at == "2024-05-01T08:00:00Z"


def test_update_cursor_accepts_none_last_activity_start(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.update_cursor(
            conn,
            athlete_id=42,
            last_activity_start=None,
            last_synced_at="2024-05-01T08:00:00Z",
        )
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert cursor is not None
    assert cursor.last_activity_start is None
    assert cursor.last_synced_at == "2024-05-01T08:00:00Z"


def test_get_cursor_scopes_by_athlete(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.update_cursor(
            conn,
            athlete_id=1,
            last_activity_start="2024-01-01T00:00:00Z",
            last_synced_at="2024-01-01T00:00:00Z",
        )
        cursor = store.get_cursor(conn, athlete_id=2)
    finally:
        conn.close()

    assert cursor is None


def test_count_activities_returns_zero_when_empty(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        n = store.count_activities(conn, athlete_id=42)
    finally:
        conn.close()

    assert n == 0


def test_count_activities_scopes_by_athlete(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=1, activity=_summary_activity(activity_id=10))
        store.upsert_activity(conn, athlete_id=1, activity=_summary_activity(activity_id=11))
        store.upsert_activity(conn, athlete_id=2, activity=_summary_activity(activity_id=20))
        a1 = store.count_activities(conn, athlete_id=1)
        a2 = store.count_activities(conn, athlete_id=2)
        a3 = store.count_activities(conn, athlete_id=3)
    finally:
        conn.close()

    assert a1 == 2
    assert a2 == 1
    assert a3 == 0


def test_delete_activities_not_in_removes_unseen_for_athlete(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        for activity_id in (1, 2, 3, 4):
            store.upsert_activity(
                conn, athlete_id=42, activity=_summary_activity(activity_id=activity_id)
            )
        # Athlete 99's row must NOT be touched.
        store.upsert_activity(conn, athlete_id=99, activity=_summary_activity(activity_id=1))

        deleted = store.delete_activities_not_in(conn, athlete_id=42, kept_ids={1, 3})
        remaining = {
            row["activity_id"]
            for row in conn.execute(
                "SELECT activity_id FROM activity WHERE athlete_id = ?",
                (42,),
            ).fetchall()
        }
        other_athlete = store.count_activities(conn, athlete_id=99)
    finally:
        conn.close()

    assert deleted == 2
    assert remaining == {1, 3}
    assert other_athlete == 1


def test_delete_activities_not_in_with_empty_kept_ids_clears_athlete(
    db_path: Path,
) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=42, activity=_summary_activity(activity_id=1))
        store.upsert_activity(conn, athlete_id=99, activity=_summary_activity(activity_id=1))

        deleted = store.delete_activities_not_in(conn, athlete_id=42, kept_ids=set())
        a1 = store.count_activities(conn, athlete_id=42)
        a2 = store.count_activities(conn, athlete_id=99)
    finally:
        conn.close()

    assert deleted == 1
    assert a1 == 0
    assert a2 == 1
