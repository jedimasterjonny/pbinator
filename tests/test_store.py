import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlmodel import select

from pbinator import store
from pbinator.best_efforts import BestEffortRow
from pbinator.models import Activity, BestEffort


def test_make_engine_bootstraps_schema(engine: Engine) -> None:
    tables = set(sa.inspect(engine).get_table_names())
    assert tables == {"activity", "sync_cursor", "best_effort"}


def test_make_engine_is_idempotent(db_path: Path) -> None:
    eng = store.make_engine(db_path)
    eng.dispose()
    # Second call must succeed even though the schema already exists.
    eng = store.make_engine(db_path)
    try:
        with eng.connect() as conn:
            result = conn.execute(sa.text("SELECT 1")).scalar_one()
    finally:
        eng.dispose()
    assert result == 1


def test_make_engine_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "subdir" / "pbinator.db"
    eng = store.make_engine(nested)
    try:
        assert nested.exists()
    finally:
        eng.dispose()


def _summary_activity(  # noqa: PLR0913 — test helper builder
    *,
    activity_id: int = 100,
    name: str = "Morning Run",
    sport_type: str = "Run",
    start_date: str = "2024-04-15T07:00:00Z",
    start_date_local: str = "2024-04-15T08:00:00",
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
        "start_date_local": start_date_local,
        "distance": distance,
        "moving_time": moving_time,
        "elapsed_time": elapsed_time,
        "total_elevation_gain": total_elevation_gain,
        "extra_field": "ignored-but-kept-in-raw-json",
    }


def test_upsert_inserts_new_row(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity())
    session.commit()
    rows = session.execute(select(Activity)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.sport_type == "Run"
    assert abs(row.distance_m - 5023.4) < 1e-9  # float equality
    assert row.moving_time_s == 1500
    assert "extra_field" in row.raw_json


def test_upsert_overwrites_mutable_fields(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(name="Old"))
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(name="Renamed", distance=6000.0),
    )
    session.commit()
    rows = session.execute(select(Activity)).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "Renamed"
    assert abs(rows[0].distance_m - 6000.0) < 1e-9  # float equality


def test_upsert_scopes_by_athlete(session: Session) -> None:
    store.upsert_activity(session, athlete_id=1, activity=_summary_activity(activity_id=5))
    store.upsert_activity(session, athlete_id=2, activity=_summary_activity(activity_id=5))
    session.commit()
    rows = session.execute(select(Activity)).scalars().all()
    assert len(rows) == 2  # same activity_id under different athletes is fine


def test_get_cursor_returns_none_when_absent(session: Session) -> None:
    cursor = store.get_cursor(session, athlete_id=42)
    assert cursor is None


def test_update_cursor_inserts_then_updates(session: Session) -> None:
    store.update_cursor(
        session,
        athlete_id=42,
        last_activity_start="2024-04-15T07:00:00Z",
        last_synced_at="2024-04-15T08:00:00Z",
    )
    session.commit()
    first = store.get_cursor(session, athlete_id=42)
    assert first is not None
    # Snapshot fields before the second write — the identity-mapped instance
    # would otherwise mutate in place when the row is updated.
    first_last_activity_start = first.last_activity_start

    store.update_cursor(
        session,
        athlete_id=42,
        last_activity_start="2024-05-01T07:00:00Z",
        last_synced_at="2024-05-01T08:00:00Z",
    )
    session.commit()
    session.expire_all()
    second = store.get_cursor(session, athlete_id=42)

    assert first_last_activity_start == "2024-04-15T07:00:00Z"
    assert second is not None
    assert second.last_activity_start == "2024-05-01T07:00:00Z"
    assert second.last_synced_at == "2024-05-01T08:00:00Z"


def test_update_cursor_accepts_none_last_activity_start(session: Session) -> None:
    store.update_cursor(
        session,
        athlete_id=42,
        last_activity_start=None,
        last_synced_at="2024-05-01T08:00:00Z",
    )
    session.commit()
    cursor = store.get_cursor(session, athlete_id=42)
    assert cursor is not None
    assert cursor.last_activity_start is None
    assert cursor.last_synced_at == "2024-05-01T08:00:00Z"


def test_get_cursor_scopes_by_athlete(session: Session) -> None:
    store.update_cursor(
        session,
        athlete_id=1,
        last_activity_start="2024-01-01T00:00:00Z",
        last_synced_at="2024-01-01T00:00:00Z",
    )
    session.commit()
    cursor = store.get_cursor(session, athlete_id=2)
    assert cursor is None


def test_count_activities_returns_zero_when_empty(session: Session) -> None:
    assert store.count_activities(session, athlete_id=42) == 0


def test_count_activities_scopes_by_athlete(session: Session) -> None:
    store.upsert_activity(session, athlete_id=1, activity=_summary_activity(activity_id=10))
    store.upsert_activity(session, athlete_id=1, activity=_summary_activity(activity_id=11))
    store.upsert_activity(session, athlete_id=2, activity=_summary_activity(activity_id=20))
    session.commit()
    assert store.count_activities(session, athlete_id=1) == 2
    assert store.count_activities(session, athlete_id=2) == 1
    assert store.count_activities(session, athlete_id=3) == 0


def test_delete_activities_not_in_removes_unseen_for_athlete(session: Session) -> None:
    for activity_id in (1, 2, 3, 4):
        store.upsert_activity(
            session, athlete_id=42, activity=_summary_activity(activity_id=activity_id)
        )
    # Athlete 99's row must NOT be touched.
    store.upsert_activity(session, athlete_id=99, activity=_summary_activity(activity_id=1))
    session.commit()

    deleted = store.delete_activities_not_in(session, athlete_id=42, kept_ids={1, 3})
    session.commit()

    remaining = {
        row.activity_id
        for row in session.execute(select(Activity).where(Activity.athlete_id == 42))
        .scalars()
        .all()
    }
    other_athlete = store.count_activities(session, athlete_id=99)

    assert deleted == 2
    assert remaining == {1, 3}
    assert other_athlete == 1


def test_delete_activities_not_in_with_empty_kept_ids_clears_athlete(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_activity(session, athlete_id=99, activity=_summary_activity(activity_id=1))
    session.commit()

    deleted = store.delete_activities_not_in(session, athlete_id=42, kept_ids=set())
    session.commit()
    assert deleted == 1
    assert store.count_activities(session, athlete_id=42) == 0
    assert store.count_activities(session, athlete_id=99) == 1


def test_make_engine_enables_foreign_keys(engine: Engine) -> None:
    with engine.connect() as conn:
        result = conn.execute(sa.text("PRAGMA foreign_keys")).scalar_one()
    assert result == 1


def test_make_engine_enables_wal_journal_mode(engine: Engine) -> None:
    with engine.connect() as conn:
        result = conn.execute(sa.text("PRAGMA journal_mode")).scalar_one()
    assert result == "wal"


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("activity; DROP TABLE x", "ok"),
        ("activity", "x; DROP TABLE y"),
        ("", "ok"),
        ("ok", ""),
        ("1bad", "ok"),
        ("ok", "1bad"),
    ],
)
def test_add_column_if_missing_rejects_unsafe_identifiers(
    engine: Engine, table: str, column: str
) -> None:
    with engine.connect() as conn, pytest.raises(ValueError, match="unsafe SQL identifier"):
        store._add_column_if_missing(conn, table, column, "TEXT")


def test_upsert_writes_start_date_local(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=42,
        activity={
            "id": 1,
            "name": "Run",
            "sport_type": "Run",
            "start_date": "2024-04-15T07:00:00Z",
            "start_date_local": "2024-04-15T08:00:00",
            "distance": 5000.0,
            "moving_time": 1500,
            "elapsed_time": 1530,
            "total_elevation_gain": 0.0,
        },
    )
    session.commit()
    row = session.execute(select(Activity)).scalar_one()
    assert row.start_date_local == "2024-04-15T08:00:00"


def test_make_engine_backfills_start_date_local_from_raw_json(db_path: Path) -> None:
    """A pre-upgrade row (no start_date_local column at insert time) gets backfilled."""
    legacy = sqlite3.connect(str(db_path))
    try:
        legacy.executescript(
            """
            CREATE TABLE activity (
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
            """
        )
        legacy.execute(
            "INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                42,
                1,
                "Run",
                "2024-04-15T07:00:00Z",
                5000.0,
                1500,
                1530,
                0.0,
                "Run",
                '{"start_date_local":"2024-04-15T08:00:00"}',
                "2024-04-15T08:00:30+00:00",
            ),
        )
        legacy.commit()
    finally:
        legacy.close()

    eng = store.make_engine(db_path)
    try:
        with Session(eng) as sess:
            row = sess.execute(select(Activity)).scalar_one()
            assert row.start_date_local == "2024-04-15T08:00:00"
    finally:
        eng.dispose()


def test_make_engine_does_not_overwrite_existing_start_date_local(db_path: Path) -> None:
    """Backfill only fills NULLs — already-set values are left alone."""
    eng = store.make_engine(db_path)
    try:
        with Session(eng) as sess:
            store.upsert_activity(
                sess,
                athlete_id=42,
                activity={
                    "id": 1,
                    "name": "Run",
                    "sport_type": "Run",
                    "start_date": "2024-04-15T07:00:00Z",
                    "start_date_local": "2024-04-15T08:00:00",
                    "distance": 5000.0,
                    "moving_time": 1500,
                    "elapsed_time": 1530,
                    "total_elevation_gain": 0.0,
                },
            )
            sess.commit()
            # Tamper raw_json so a re-backfill would change the stored value.
            sess.execute(
                sa.text("UPDATE activity SET raw_json = :rj WHERE activity_id = 1"),
                {"rj": '{"start_date_local":"1999-01-01T00:00:00"}'},
            )
            sess.commit()
    finally:
        eng.dispose()

    # Reopen — make_engine runs the backfill again. Existing value must survive.
    eng = store.make_engine(db_path)
    try:
        with Session(eng) as sess:
            row = sess.execute(select(Activity)).scalar_one()
            assert row.start_date_local == "2024-04-15T08:00:00"
    finally:
        eng.dispose()


def test_make_engine_creates_best_effort_table(engine: Engine) -> None:
    tables = set(sa.inspect(engine).get_table_names())
    assert "best_effort" in tables


def test_make_engine_creates_best_effort_index(engine: Engine) -> None:
    indexes = {idx["name"] for idx in sa.inspect(engine).get_indexes("best_effort")}
    assert "idx_best_effort_athlete_label_time" in indexes


def test_make_engine_adds_legacy_columns(tmp_path: Path) -> None:
    """An old DB (no start_date_local / best_efforts_fetched_at) must upgrade in place."""
    legacy_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(legacy_path)
    try:
        legacy.executescript(
            """
            CREATE TABLE activity (
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
            """
        )
        legacy.execute(
            "INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                42,
                1,
                "Run",
                "2024-04-15T07:00:00Z",
                5000.0,
                1500,
                1530,
                0.0,
                "Run",
                '{"start_date_local":"2024-04-15T08:00:00"}',
                "2024-04-15T08:00:30+00:00",
            ),
        )
        legacy.commit()
    finally:
        legacy.close()

    eng = store.make_engine(legacy_path)
    try:
        cols = {c["name"] for c in sa.inspect(eng).get_columns("activity")}
        assert "start_date_local" in cols
        assert "best_efforts_fetched_at" in cols
        with Session(eng) as sess:
            row = sess.execute(select(Activity)).scalar_one()
            assert row.start_date_local == "2024-04-15T08:00:00"
            assert row.best_efforts_fetched_at is None
    finally:
        eng.dispose()


def _best_effort_row(label: str = "5k", time_s: int = 1100) -> BestEffortRow:
    return BestEffortRow(
        distance_label=label,
        distance_m=5000.0,
        moving_time_s=time_s,
        elapsed_time_s=time_s + 1,
        start_date="2024-04-15T07:30:00Z",
    )


def test_upsert_best_efforts_inserts_rows(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_best_efforts(
        session,
        athlete_id=42,
        activity_id=1,
        efforts=[_best_effort_row("1k", 200), _best_effort_row("5k", 1100)],
    )
    session.commit()
    rows = session.execute(select(BestEffort).order_by(BestEffort.distance_label)).scalars().all()
    assert [(r.distance_label, r.moving_time_s) for r in rows] == [
        ("1k", 200),
        ("5k", 1100),
    ]


def test_upsert_best_efforts_replaces_on_conflict(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_best_efforts(
        session, athlete_id=42, activity_id=1, efforts=[_best_effort_row("5k", 1100)]
    )
    session.commit()
    store.upsert_best_efforts(
        session, athlete_id=42, activity_id=1, efforts=[_best_effort_row("5k", 1080)]
    )
    session.commit()
    rows = session.execute(select(BestEffort)).scalars().all()
    assert len(rows) == 1
    assert rows[0].moving_time_s == 1080


def test_upsert_best_efforts_with_empty_list_is_noop(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_best_efforts(session, athlete_id=42, activity_id=1, efforts=[])
    session.commit()
    rows = session.execute(select(BestEffort)).scalars().all()
    assert len(rows) == 0


def test_mark_detail_fetched_sets_timestamp(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.mark_detail_fetched(
        session,
        athlete_id=42,
        activity_id=1,
        fetched_at="2024-05-01T08:00:00+00:00",
    )
    session.commit()
    row = session.execute(select(Activity)).scalar_one()
    assert row.best_efforts_fetched_at == "2024-05-01T08:00:00+00:00"


def test_count_runs_awaiting_detail_zero_initially(session: Session) -> None:
    assert store.count_runs_awaiting_detail(session, athlete_id=42) == 0


def test_count_runs_awaiting_detail_counts_only_unfetched_runs(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=2))
    store.upsert_activity(
        session, athlete_id=42, activity=_summary_activity(activity_id=3, sport_type="Ride")
    )
    store.mark_detail_fetched(
        session, athlete_id=42, activity_id=1, fetched_at="2024-05-01T08:00:00+00:00"
    )
    session.commit()
    assert store.count_runs_awaiting_detail(session, athlete_id=42) == 1


def test_count_runs_awaiting_detail_scopes_by_athlete(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_activity(session, athlete_id=99, activity=_summary_activity(activity_id=1))
    session.commit()
    assert store.count_runs_awaiting_detail(session, athlete_id=42) == 1
    assert store.count_runs_awaiting_detail(session, athlete_id=99) == 1


def test_delete_activities_not_in_cascades_to_best_effort(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=2))
    store.upsert_best_efforts(
        session, athlete_id=42, activity_id=1, efforts=[_best_effort_row("5k", 1100)]
    )
    store.upsert_best_efforts(
        session, athlete_id=42, activity_id=2, efforts=[_best_effort_row("5k", 1080)]
    )
    session.commit()
    store.delete_activities_not_in(session, athlete_id=42, kept_ids={1})
    session.commit()
    remaining = session.execute(select(BestEffort)).scalars().all()
    assert {r.activity_id for r in remaining} == {1}


def test_already_fetched_run_ids_returns_empty_for_empty_input(session: Session) -> None:
    assert store.already_fetched_run_ids(session, athlete_id=42, run_ids=[]) == set()


def test_already_fetched_run_ids_returns_only_fetched(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=1))
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=2))
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity(activity_id=3))
    store.mark_detail_fetched(
        session, athlete_id=42, activity_id=1, fetched_at="2024-05-01T08:00:00+00:00"
    )
    store.mark_detail_fetched(
        session, athlete_id=42, activity_id=3, fetched_at="2024-05-01T08:00:00+00:00"
    )
    session.commit()
    fetched = store.already_fetched_run_ids(session, athlete_id=42, run_ids=[1, 2, 3, 4])
    assert fetched == {1, 3}


def test_write_transaction_commits_on_clean_exit(session: Session) -> None:
    with store.write_transaction(session):
        store.upsert_activity(session, athlete_id=42, activity=_summary_activity())
    session.expire_all()
    rows = session.execute(select(Activity)).scalars().all()
    assert len(rows) == 1


class BoomError(RuntimeError):
    """Test-only sentinel exception for rollback paths."""


def _upsert_then_raise(session: Session) -> None:
    store.upsert_activity(session, athlete_id=42, activity=_summary_activity())
    raise BoomError


def test_write_transaction_rolls_back_on_exception(session: Session) -> None:
    with pytest.raises(BoomError), store.write_transaction(session):
        _upsert_then_raise(session)
    session.expire_all()
    rows = session.execute(select(Activity)).scalars().all()
    assert len(rows) == 0


def test_session_returns_typed_models(session: Session) -> None:
    activity = {
        "id": 1,
        "sport_type": "Run",
        "start_date": "2024-04-15T07:00:00Z",
        "start_date_local": "2024-04-15T08:00:00",
        "distance": 5023.4,
        "moving_time": 1500,
        "elapsed_time": 1530,
        "total_elevation_gain": 47.0,
        "name": "Morning Run",
    }
    store.upsert_activity(session, athlete_id=1, activity=activity)
    session.commit()
    rows = session.execute(select(Activity)).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "Morning Run"
    assert abs(rows[0].distance_m - 5023.4) < 1e-9  # float equality


def test_activities_in_range_inclusive_bounds(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T07:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=2, start_date="2024-04-15T08:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=3, start_date="2024-04-15T09:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=42,
        start_utc=datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 9, 0, 0, tzinfo=UTC),
    )

    ids = [a.activity_id for a in rows]
    assert ids == [1, 2, 3]


def test_activities_in_range_excludes_outside_window(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T06:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=2, start_date="2024-04-15T10:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=42,
        start_utc=datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 9, 0, 0, tzinfo=UTC),
    )

    assert rows == []


def test_activities_in_range_scopes_by_athlete(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=1,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T08:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=2,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T08:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=1,
        start_utc=datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 9, 0, 0, tzinfo=UTC),
    )

    assert [a.athlete_id for a in rows] == [1]


def test_activities_in_range_orders_by_start_date(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T09:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=2, start_date="2024-04-15T07:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=3, start_date="2024-04-15T08:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=42,
        start_utc=datetime(2024, 4, 15, 6, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 10, 0, 0, tzinfo=UTC),
    )

    assert [a.activity_id for a in rows] == [2, 3, 1]
