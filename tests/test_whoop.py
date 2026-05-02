from datetime import UTC, datetime

import pytest

from pbinator.whoop import WhoopParseError, WhoopWorkout, parse_workouts

_HEADER = (
    "Cycle start time,Cycle end time,Cycle timezone,"
    "Workout start time,Workout end time,Duration (min),Activity name,"
    "Activity Strain,Energy burned (cal)\n"
)


def _csv(*data_lines: str) -> str:
    return _HEADER + "\n".join(data_lines) + ("\n" if data_lines else "")


def test_parses_utc_z_row() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTCZ,"
        "2024-04-15 07:00:00,2024-04-15 07:30:00,30,Running,15.0,500.0"
    )

    workouts = parse_workouts(text)

    assert len(workouts) == 1
    w = workouts[0]
    assert w.activity_name == "Running"
    assert w.start_utc == datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    assert w.end_utc == datetime(2024, 4, 15, 7, 30, 0, tzinfo=UTC)
    assert w.duration_min == 30


def test_parses_positive_offset_row_to_utc() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTC+01:00,"
        "2024-04-15 08:00:00,2024-04-15 08:30:00,30,Running,15.0,500.0"
    )

    workouts = parse_workouts(text)

    assert len(workouts) == 1
    assert workouts[0].start_utc == datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    assert workouts[0].end_utc == datetime(2024, 4, 15, 7, 30, 0, tzinfo=UTC)


def test_parses_negative_offset_row_to_utc() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTC-05:00,"
        "2024-04-15 02:00:00,2024-04-15 02:30:00,30,Running,15.0,500.0"
    )

    workouts = parse_workouts(text)

    assert workouts[0].start_utc == datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)


def test_each_row_honours_its_own_offset() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTCZ,"
        "2024-04-15 07:00:00,2024-04-15 07:30:00,30,Running,15.0,500.0",
        "2024-10-30 06:00:00,2024-10-31 06:00:00,UTC+01:00,"
        "2024-10-30 09:00:00,2024-10-30 09:30:00,30,Walking,8.0,200.0",
    )

    workouts = parse_workouts(text)

    assert workouts[0].start_utc == datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    assert workouts[1].start_utc == datetime(2024, 10, 30, 8, 0, 0, tzinfo=UTC)


def test_blank_workout_start_row_is_skipped() -> None:
    text = _csv(
        "2024-04-15 06:00:00,,UTCZ,,,,Running,,",
        "2024-04-14 06:00:00,2024-04-15 06:00:00,UTCZ,"
        "2024-04-14 07:00:00,2024-04-14 07:30:00,30,Running,15.0,500.0",
    )

    workouts = parse_workouts(text)

    assert len(workouts) == 1
    assert workouts[0].start_utc == datetime(2024, 4, 14, 7, 0, 0, tzinfo=UTC)


def test_header_only_returns_empty_list() -> None:
    workouts = parse_workouts(_HEADER)
    assert workouts == []


def test_missing_required_column_raises() -> None:
    text = "Cycle timezone,Workout start time\nUTCZ,2024-04-15 07:00:00\n"

    with pytest.raises(WhoopParseError) as excinfo:
        parse_workouts(text)

    assert "Workout end time" in str(excinfo.value) or "missing" in str(excinfo.value).lower()


def test_unparsable_timezone_raises_with_line_number() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,PST,"
        "2024-04-15 07:00:00,2024-04-15 07:30:00,30,Running,15.0,500.0"
    )

    with pytest.raises(WhoopParseError) as excinfo:
        parse_workouts(text)

    assert excinfo.value.line_no == 2
    assert "PST" in excinfo.value.reason


def test_unparsable_workout_start_raises() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTCZ,"
        "not-a-time,2024-04-15 07:30:00,30,Running,15.0,500.0"
    )

    with pytest.raises(WhoopParseError) as excinfo:
        parse_workouts(text)

    assert excinfo.value.line_no == 2


def test_blank_workout_end_when_start_present_raises() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTCZ,2024-04-15 07:00:00,,30,Running,15.0,500.0"
    )

    with pytest.raises(WhoopParseError) as excinfo:
        parse_workouts(text)

    assert excinfo.value.line_no == 2


def test_non_integer_duration_raises() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTCZ,"
        "2024-04-15 07:00:00,2024-04-15 07:30:00,thirty,Running,15.0,500.0"
    )

    with pytest.raises(WhoopParseError) as excinfo:
        parse_workouts(text)

    assert excinfo.value.line_no == 2


def test_blank_activity_name_raises() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTCZ,"
        "2024-04-15 07:00:00,2024-04-15 07:30:00,30,,15.0,500.0"
    )

    with pytest.raises(WhoopParseError) as excinfo:
        parse_workouts(text)

    assert excinfo.value.line_no == 2


def test_line_number_is_file_line_after_skipped_rows() -> None:
    text = _csv(
        "2024-04-15 06:00:00,,UTCZ,,,,Running,,",  # data line 2 — skipped (blank start)
        "2024-04-14 06:00:00,2024-04-15 06:00:00,PST,"
        "2024-04-14 07:00:00,2024-04-14 07:30:00,30,Running,15.0,500.0",  # data line 3
    )

    with pytest.raises(WhoopParseError) as excinfo:
        parse_workouts(text)

    assert excinfo.value.line_no == 3


def test_workout_dataclass_is_frozen() -> None:
    w = WhoopWorkout(
        activity_name="Running",
        start_utc=datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 7, 30, 0, tzinfo=UTC),
        duration_min=30,
    )
    with pytest.raises((AttributeError, TypeError)):
        w.activity_name = "Walking"  # ty: ignore[invalid-assignment]  # frozen dataclass


def test_offset_with_minutes_handled() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTC+05:30,"
        "2024-04-15 12:30:00,2024-04-15 13:00:00,30,Running,15.0,500.0"
    )

    workouts = parse_workouts(text)

    assert workouts[0].start_utc == datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
