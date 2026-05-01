from pathlib import Path
from typing import Any

import pytest

from pbinator import pbs, store
from pbinator.best_efforts import BestEffortRow


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "pbinator.db"


_DEFAULT_SUMMARY: dict[str, Any] = {
    "name": "Run",
    "sport_type": "Run",
    "distance": 5000.0,
    "moving_time": 1500,
    "elapsed_time": 1530,
    "total_elevation_gain": 0.0,
}


def _summary(activity_id: int, start_local: str) -> dict[str, Any]:
    return {
        **_DEFAULT_SUMMARY,
        "id": activity_id,
        "start_date": f"{start_local}Z",
        "start_date_local": start_local,
    }


_DISTANCES_M: dict[str, float] = {
    "400m": 400.0,
    "1/2 mile": 804.672,
    "1k": 1000.0,
    "1 mile": 1609.34,
    "2 mile": 3218.69,
    "5k": 5000.0,
    "10k": 10000.0,
    "15k": 15000.0,
    "Half-Marathon": 21097.5,
    "Marathon": 42195.0,
}


def _effort(label: str, time_s: int) -> BestEffortRow:
    return BestEffortRow(
        distance_label=label,
        distance_m=_DISTANCES_M[label],
        moving_time_s=time_s,
        elapsed_time_s=time_s + 1,
        start_date="2024-04-15T07:30:00Z",
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0:00"),
        (5, "0:05"),
        (59, "0:59"),
        (60, "1:00"),
        (599, "9:59"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (86399, "23:59:59"),
    ],
)
def test_format_time_boundaries(seconds: int, expected: str) -> None:
    assert pbs.format_time(seconds) == expected


def test_compute_rows_empty_db_returns_empty(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        rows = pbs.compute_rows(conn, athlete_id=42)
    finally:
        conn.close()

    assert rows == []


def test_compute_rows_one_break_one_row(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=42, activity=_summary(1, "2024-04-15T08:00:00"))
        store.upsert_best_efforts(conn, athlete_id=42, activity_id=1, efforts=[_effort("5k", 1100)])
        rows = pbs.compute_rows(conn, athlete_id=42)
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0].date == "2024-04-15"
    five_k = rows[0].cells["5k"]
    assert five_k is not None
    assert five_k.is_pb_break is True
    assert five_k.moving_time_s == 1100
    assert rows[0].cells["1k"] is None


def test_compute_rows_equalled_does_not_break(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=42, activity=_summary(1, "2024-04-15T08:00:00"))
        store.upsert_activity(conn, athlete_id=42, activity=_summary(2, "2024-05-01T08:00:00"))
        store.upsert_best_efforts(conn, athlete_id=42, activity_id=1, efforts=[_effort("5k", 1100)])
        store.upsert_best_efforts(
            conn,
            athlete_id=42,
            activity_id=2,
            efforts=[_effort("5k", 1100)],
        )
        rows = pbs.compute_rows(conn, athlete_id=42)
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0].date == "2024-04-15"


def test_compute_rows_three_distances_one_race_one_row(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=42, activity=_summary(1, "2024-04-15T08:00:00"))
        store.upsert_best_efforts(
            conn,
            athlete_id=42,
            activity_id=1,
            efforts=[_effort("5k", 1100), _effort("10k", 2280), _effort("Half-Marathon", 5090)],
        )
        rows = pbs.compute_rows(conn, athlete_id=42)
    finally:
        conn.close()

    assert len(rows) == 1
    breaks = {label for label, cell in rows[0].cells.items() if cell and cell.is_pb_break}
    assert breaks == {"5k", "10k", "Half-Marathon"}


def test_compute_rows_multiple_dates_newest_first(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        for activity_id, date_local, time_s in [
            (1, "2023-04-15T08:00:00", 1200),
            (2, "2023-09-01T08:00:00", 1150),
            (3, "2024-04-15T08:00:00", 1100),
        ]:
            store.upsert_activity(conn, athlete_id=42, activity=_summary(activity_id, date_local))
            store.upsert_best_efforts(
                conn,
                athlete_id=42,
                activity_id=activity_id,
                efforts=[_effort("5k", time_s)],
            )
        rows = pbs.compute_rows(conn, athlete_id=42)
    finally:
        conn.close()

    assert [r.date for r in rows] == ["2024-04-15", "2023-09-01", "2023-04-15"]
    expected_times = [1100, 1150, 1200]
    for row, expected in zip(rows, expected_times, strict=True):
        cell = row.cells["5k"]
        assert cell is not None
        assert cell.moving_time_s == expected


def test_compute_rows_running_best_fills_other_columns(db_path: Path) -> None:
    """A row's non-broken cells show the running best at that distance."""
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=42, activity=_summary(1, "2023-01-01T08:00:00"))
        store.upsert_best_efforts(conn, athlete_id=42, activity_id=1, efforts=[_effort("5k", 1200)])
        store.upsert_activity(conn, athlete_id=42, activity=_summary(2, "2024-04-15T08:00:00"))
        store.upsert_best_efforts(
            conn,
            athlete_id=42,
            activity_id=2,
            efforts=[_effort("Marathon", 10760)],
        )
        rows = pbs.compute_rows(conn, athlete_id=42)
    finally:
        conn.close()

    by_date = {r.date: r for r in rows}
    newest = by_date["2024-04-15"]
    five_k_cell = newest.cells["5k"]
    assert five_k_cell is not None
    assert five_k_cell.moving_time_s == 1200
    assert five_k_cell.is_pb_break is False
    marathon_cell = newest.cells["Marathon"]
    assert marathon_cell is not None
    assert marathon_cell.is_pb_break is True


def test_compute_rows_scopes_by_athlete(db_path: Path) -> None:
    conn = store.connect(db_path)
    try:
        store.upsert_activity(conn, athlete_id=1, activity=_summary(1, "2024-04-15T08:00:00"))
        store.upsert_best_efforts(conn, athlete_id=1, activity_id=1, efforts=[_effort("5k", 1100)])
        rows_other = pbs.compute_rows(conn, athlete_id=2)
        rows_self = pbs.compute_rows(conn, athlete_id=1)
    finally:
        conn.close()

    assert rows_other == []
    assert len(rows_self) == 1


def test_to_dataframe_empty_rows_returns_canonical_columns() -> None:
    values_df, mask_df = pbs.to_dataframe([])
    assert list(values_df.columns) == [
        "400m",
        "½mi",
        "1km",
        "1mi",
        "2mi",
        "5km",
        "10km",
        "15km",
        "Half",
        "Marathon",
    ]
    assert list(mask_df.columns) == list(values_df.columns)
    assert len(values_df) == 0
    assert len(mask_df) == 0


def test_to_dataframe_round_trips_values_and_mask() -> None:
    rows = [
        pbs.PbRow(
            date="2024-04-15",
            cells={
                label: (
                    pbs.PbCell(moving_time_s=1100, is_pb_break=(label == "5k"))
                    if label in {"1k", "5k"}
                    else None
                )
                for label in pbs.DISTANCE_LABELS
            },
        ),
    ]
    rows[0].cells["1k"] = pbs.PbCell(moving_time_s=200, is_pb_break=False)

    values_df, mask_df = pbs.to_dataframe(rows)

    assert values_df.index.name == "Date"
    assert mask_df.index.name == "Date"
    assert values_df.loc["2024-04-15", "5km"] == "18:20"
    assert values_df.loc["2024-04-15", "1km"] == "3:20"
    assert values_df.loc["2024-04-15", "Marathon"] == "—"
    assert bool(mask_df.loc["2024-04-15", "5km"]) is True
    assert bool(mask_df.loc["2024-04-15", "1km"]) is False
    assert bool(mask_df.loc["2024-04-15", "Marathon"]) is False


def test_to_dataframe_columns_match_canonical_display_order() -> None:
    rows = [
        pbs.PbRow(
            date="2024-04-15",
            cells=dict.fromkeys(pbs.DISTANCE_LABELS),
        ),
    ]
    values_df, _ = pbs.to_dataframe(rows)
    assert list(values_df.columns) == [
        "400m",
        "½mi",
        "1km",
        "1mi",
        "2mi",
        "5km",
        "10km",
        "15km",
        "Half",
        "Marathon",
    ]
