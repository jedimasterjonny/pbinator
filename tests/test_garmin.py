from datetime import datetime

import pytest

from pbinator import garmin

_MINIMAL_HEADER = (
    "Activity Type,Date,Title,Distance,Calories,Time,Avg HR,Max HR,"
    "Avg Run Cadence,Max Run Cadence,Total Ascent,Avg Power,Max Power,"
    "Normalized Power® (NP®),Moving Time,Elapsed Time,Min Elevation,Max Elevation\n"
)
_MINIMAL_HEADER_NO_TITLE = (
    "Activity Type,Date,Distance,Calories,Time,Avg HR,Max HR,"
    "Avg Run Cadence,Max Run Cadence,Total Ascent,Avg Power,Max Power,"
    "Normalized Power® (NP®),Moving Time,Elapsed Time,Min Elevation,Max Elevation\n"
)


def test_parse_empty_file_returns_empty_list() -> None:
    assert garmin.parse_activities(_MINIMAL_HEADER) == []


def test_parse_missing_required_column_raises() -> None:
    # No "Title" column
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(_MINIMAL_HEADER_NO_TITLE)
    assert exc.value.line_no == 1
    assert "Title" in exc.value.reason


def test_parse_int_or_none_handles_blank_sentinel() -> None:
    assert garmin._parse_int_or_none("--", "Avg HR", 5) is None


def test_parse_int_or_none_returns_int() -> None:
    assert garmin._parse_int_or_none("156", "Avg HR", 5) == 156


def test_parse_int_or_none_strips_thousands_separator() -> None:
    assert garmin._parse_int_or_none("1,280", "Calories", 5) == 1280
    assert garmin._parse_int_or_none("8,382", "Steps", 5) == 8382


def test_parse_int_or_none_rejects_garbage() -> None:
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin._parse_int_or_none("not-a-number", "Avg HR", 7)
    assert exc.value.line_no == 7
    assert "Avg HR" in exc.value.reason


def test_parse_hms_to_s_full_hours() -> None:
    assert garmin._parse_hms_to_s("01:09:42", "Time", 4) == 4182


def test_parse_hms_to_s_under_one_hour() -> None:
    assert garmin._parse_hms_to_s("00:53:11", "Time", 4) == 3191


def test_parse_hms_to_s_blank_returns_none() -> None:
    assert garmin._parse_hms_to_s("--", "Time", 4) is None


def test_parse_hms_to_s_dashed_positions_return_none() -> None:
    assert garmin._parse_hms_to_s("--:--:--", "Moving Time", 4) is None


def test_parse_hms_to_s_rounds_fractional_seconds() -> None:
    assert garmin._parse_hms_to_s("00:09:25.4", "Moving Time", 4) == 565
    assert garmin._parse_hms_to_s("00:09:25.6", "Moving Time", 4) == 566


def test_parse_hms_to_s_rejects_malformed() -> None:
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin._parse_hms_to_s("12:99", "Time", 4)
    assert exc.value.line_no == 4


def test_parse_hms_to_s_rejects_non_integer_parts() -> None:
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin._parse_hms_to_s("aa:bb:cc", "Time", 9)
    assert exc.value.line_no == 9
    assert "Time" in exc.value.reason


def test_parse_distance_km_to_m_converts_with_precision() -> None:
    assert garmin._parse_distance_km_to_m("9.01", 4) == pytest.approx(9010.0)


def test_parse_distance_km_to_m_rejects_garbage() -> None:
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin._parse_distance_km_to_m("not-a-number", 4)
    assert "Distance" in exc.value.reason


def test_parse_date_returns_naive_datetime() -> None:
    parsed = garmin._parse_date("2026-05-02 13:18:06", 4)
    assert parsed == datetime(2026, 5, 2, 13, 18, 6)  # noqa: DTZ001 — naive by design
    assert parsed.tzinfo is None


def test_parse_date_rejects_garbage() -> None:
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin._parse_date("not-a-date", 4)
    assert exc.value.line_no == 4
    assert "Date" in exc.value.reason


_HEADER = (
    "Activity Type,Date,Favorite,Title,Distance,Calories,Time,Avg HR,Max HR,"
    "Aerobic TE,Avg Run Cadence,Max Run Cadence,Avg Pace,Best Pace,Total Ascent,"
    "Total Descent,Avg Stride Length,Avg Vertical Ratio,Avg Vertical Oscillation,"
    "Avg Ground Contact Time,Avg GAP,Normalized Power® (NP®),Training Stress Score®,"
    "Avg Power,Max Power,Total Strokes,Avg. Swolf,Avg Stroke Rate,Steps,"
    "Body Battery Drain,Decompression,Best Lap Time,Number of Laps,Avg Stress,"
    "Max Stress,Moving Time,Elapsed Time,Min Elevation,Max Elevation"
)


def _row(  # noqa: PLR0913 — test helper builder
    *,
    activity_type: str = "Running",
    date: str = "2026-05-02 13:18:06",
    title: str = "Easy Run - 9km",
    distance: str = "9.01",
    calories: str = "631",
    time: str = "00:53:11",
    avg_hr: str = "156",
    max_hr: str = "168",
    avg_run_cadence: str = "158",
    max_run_cadence: str = "163",
    total_ascent: str = "55",
    elapsed_time: str = "00:53:28",
    min_elev: str = "72",
    max_elev: str = "92",
) -> str:
    cols = [
        activity_type,
        date,
        "false",
        title,
        distance,
        calories,
        time,
        avg_hr,
        max_hr,
        "3.4",
        avg_run_cadence,
        max_run_cadence,
        "5:54",
        "5:15",
        total_ascent,
        "53",
        "1.07",
        "9.0",
        "9.7",
        "293",
        "5:54",
        "296",
        "0.0",
        "298",
        "388",
        "--",
        "--",
        "--",
        "8,382",
        "-12",
        "No",
        "00:00:02.5",
        "2",
        "--",
        "--",
        "--",
        elapsed_time,
        min_elev,
        max_elev,
    ]
    return ",".join(f'"{c}"' if "," in c else c for c in cols)


def test_parse_one_row_populates_dataclass() -> None:
    text = _HEADER + "\n" + _row() + "\n"
    rows = garmin.parse_activities(text)
    assert len(rows) == 1
    g = rows[0]
    assert g.activity_type == "Running"
    assert g.start_local == datetime(2026, 5, 2, 13, 18, 6)  # noqa: DTZ001 — naive by design
    assert g.start_local.tzinfo is None
    assert g.title == "Easy Run - 9km"
    assert g.distance_m == pytest.approx(9010.0)
    assert g.moving_time_s == 3191  # from "Time"
    assert g.elapsed_time_s == 3208
    assert g.calories == 631
    assert g.avg_hr == 156
    assert g.max_hr == 168
    assert g.total_ascent_m == 55
    assert g.min_elevation_m == 72
    assert g.max_elevation_m == 92
    assert g.avg_run_cadence == 158
    assert g.max_run_cadence == 163


def test_parse_blanks_become_none() -> None:
    text = (
        _HEADER
        + "\n"
        + _row(
            calories="--",
            avg_hr="--",
            max_hr="--",
            avg_run_cadence="--",
            max_run_cadence="--",
            total_ascent="--",
            min_elev="--",
            max_elev="--",
            time="--",
        )
        + "\n"
    )
    g = garmin.parse_activities(text)[0]
    assert g.calories is None
    assert g.avg_hr is None
    assert g.max_hr is None
    assert g.total_ascent_m is None
    assert g.min_elevation_m is None
    assert g.max_elevation_m is None
    assert g.avg_run_cadence is None
    assert g.max_run_cadence is None
    assert g.moving_time_s is None


def test_parse_rejects_blank_activity_type() -> None:
    text = _HEADER + "\n" + _row(activity_type="") + "\n"
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(text)
    assert "Activity Type" in exc.value.reason


def test_parse_rejects_blank_title() -> None:
    text = _HEADER + "\n" + _row(title="") + "\n"
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(text)
    assert "Title" in exc.value.reason


def test_parse_rejects_unparsable_date() -> None:
    text = _HEADER + "\n" + _row(date="not-a-date") + "\n"
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(text)
    assert "Date" in exc.value.reason
    assert exc.value.line_no == 2


def test_parse_rejects_unparsable_distance() -> None:
    text = _HEADER + "\n" + _row(distance="not-a-number") + "\n"
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(text)
    assert "Distance" in exc.value.reason


def test_parse_rejects_unparsable_elapsed_time() -> None:
    text = _HEADER + "\n" + _row(elapsed_time="not-a-time") + "\n"
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(text)
    assert "Elapsed Time" in exc.value.reason


def test_parse_rejects_blank_sentinel_elapsed_time() -> None:
    # Elapsed Time is mandatory; "--" parses to None and must be rejected.
    text = _HEADER + "\n" + _row(elapsed_time="--") + "\n"
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(text)
    assert "Elapsed Time" in exc.value.reason
    assert exc.value.line_no == 2


def test_parse_line_numbers_are_1_indexed_to_the_data_row() -> None:
    text = _HEADER + "\n" + _row() + "\n" + _row() + "\n" + _row(date="oops") + "\n"
    with pytest.raises(garmin.GarminParseError) as exc:
        garmin.parse_activities(text)
    assert exc.value.line_no == 4  # header=1, rows 2/3 ok, row at line 4 is bad


def test_parse_ignores_unknown_extra_columns() -> None:
    header_with_extra = _HEADER + ",My Custom Column"
    body = _row() + ",ignored-value"
    rows = garmin.parse_activities(header_with_extra + "\n" + body + "\n")
    assert len(rows) == 1


def test_parse_returns_rows_in_input_order() -> None:
    text = (
        _HEADER
        + "\n"
        + _row(date="2026-05-02 10:00:00")
        + "\n"
        + _row(date="2026-05-01 10:00:00")
        + "\n"
    )
    rows = garmin.parse_activities(text)
    assert [r.start_local.day for r in rows] == [2, 1]
