"""Behavioural tests for the Whoop ↔ Strava comparator."""

from datetime import UTC, datetime, timedelta

import pytest

from pbinator.compare import (
    MISMATCH_TOLERANCE_S,
    PAIRING_WINDOW_S,
    WhoopComparison,
    WhoopOnly,
    compare,
    format_signed_delta,
)
from pbinator.models import Activity
from pbinator.whoop import WhoopWorkout


def _whoop(
    *,
    activity_name: str = "Running",
    start: datetime = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
    duration_min: int = 30,
) -> WhoopWorkout:
    return WhoopWorkout(
        activity_name=activity_name,
        start_utc=start,
        end_utc=start + timedelta(minutes=duration_min),
        duration_min=duration_min,
    )


def _strava(
    *,
    activity_id: int = 1,
    sport_type: str = "Run",
    start: datetime = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
    elapsed_s: int = 1800,
) -> Activity:
    return Activity(
        athlete_id=42,
        activity_id=activity_id,
        sport_type=sport_type,
        start_date=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        start_date_local=None,
        distance_m=5000.0,
        moving_time_s=elapsed_s,
        elapsed_time_s=elapsed_s,
        total_elev_gain_m=0.0,
        name="Run",
        raw_json="{}",
        fetched_at="2024-04-15T08:00:00Z",
    )


def test_paired_clean_emits_nothing() -> None:
    w = _whoop()
    a = _strava()  # exact match

    result = compare([w], [a])

    assert result.mismatches == []
    assert result.whoop_only == []


def test_unmapped_sport_emits_whoop_only() -> None:
    w = _whoop(activity_name="Activity")

    result = compare([w], [])

    assert result.mismatches == []
    assert len(result.whoop_only) == 1
    assert result.whoop_only[0].reason == "unmapped_sport"
    assert result.whoop_only[0].whoop is w


def test_no_candidates_emits_no_strava_match() -> None:
    w = _whoop()

    result = compare([w], [])

    assert len(result.whoop_only) == 1
    assert result.whoop_only[0].reason == "no_strava_match"


def test_wrong_sport_in_window_emits_no_strava_match() -> None:
    w = _whoop(activity_name="Running")
    a = _strava(sport_type="Walk")

    result = compare([w], [a])

    assert len(result.whoop_only) == 1
    assert result.whoop_only[0].reason == "no_strava_match"
    assert result.mismatches == []


def test_correct_sport_outside_window_emits_no_strava_match() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    a = _strava(start=whoop_start + timedelta(seconds=PAIRING_WINDOW_S + 1))

    result = compare([_whoop(start=whoop_start)], [a])

    assert len(result.whoop_only) == 1
    assert result.whoop_only[0].reason == "no_strava_match"


def test_paired_within_window_but_outside_tolerance_flags_start() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    drift_s = MISMATCH_TOLERANCE_S + 1
    a = _strava(start=whoop_start + timedelta(seconds=drift_s))

    result = compare([_whoop(start=whoop_start)], [a])

    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.flagged_start is True
    assert m.delta_start_s == drift_s
    # End delta = strava_end - whoop_end = (strava_start + elapsed) - (whoop_start + duration)
    # Both elapsed and duration cover 30 min, so end delta == start delta.
    assert m.flagged_end is True


def test_end_mismatch_only_flags_end_not_start() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    # Strava starts at the same moment but runs longer than Whoop's 30 min duration.
    a = _strava(start=whoop_start, elapsed_s=30 * 60 + MISMATCH_TOLERANCE_S + 1)

    result = compare([_whoop(start=whoop_start, duration_min=30)], [a])

    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.flagged_start is False
    assert m.flagged_end is True
    assert m.delta_end_s == MISMATCH_TOLERANCE_S + 1


def test_two_candidates_inside_window_picks_closer() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    near = _strava(activity_id=1, start=whoop_start + timedelta(seconds=10))
    far = _strava(activity_id=2, start=whoop_start + timedelta(seconds=300))

    result = compare([_whoop(start=whoop_start)], [near, far])

    # |Δstart| 10s is within tolerance — clean pair, no mismatch entry.
    assert result.mismatches == []
    assert result.whoop_only == []


def test_two_candidates_inside_window_far_one_flagged_when_chosen() -> None:
    """Sanity check: the comparator pairs to the closer one, not the further one."""
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    near = _strava(activity_id=1, start=whoop_start + timedelta(seconds=300))
    far = _strava(activity_id=2, start=whoop_start + timedelta(seconds=400))

    result = compare([_whoop(start=whoop_start)], [near, far])

    # Both are outside tolerance, but `near` (300s) is chosen, not `far` (400s).
    assert len(result.mismatches) == 1
    assert result.mismatches[0].strava_activity_id == 1
    assert result.mismatches[0].delta_start_s == 300


def test_tied_distance_breaks_on_lower_activity_id() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    a_high = _strava(activity_id=99, start=whoop_start + timedelta(seconds=10))
    a_low = _strava(activity_id=5, start=whoop_start + timedelta(seconds=10))

    result = compare([_whoop(start=whoop_start)], [a_high, a_low])

    # Both within tolerance — clean pair, but to assert which was chosen we
    # widen the gap so the chosen one's id is observable via mismatch output.
    # Re-test below with widened gap.
    assert result.mismatches == []
    assert result.whoop_only == []

    # Widen so they appear as mismatches; verify lower id wins the tie.
    drift_s = MISMATCH_TOLERANCE_S + 1
    a_high = _strava(activity_id=99, start=whoop_start + timedelta(seconds=drift_s))
    a_low = _strava(activity_id=5, start=whoop_start + timedelta(seconds=drift_s))

    result2 = compare([_whoop(start=whoop_start)], [a_high, a_low])

    assert len(result2.mismatches) == 1
    assert result2.mismatches[0].strava_activity_id == 5


def test_signed_delta_strava_later_is_positive() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    drift = MISMATCH_TOLERANCE_S + 30
    a = _strava(start=whoop_start + timedelta(seconds=drift))

    result = compare([_whoop(start=whoop_start)], [a])

    assert result.mismatches[0].delta_start_s == drift  # positive


def test_signed_delta_strava_earlier_is_negative() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    drift = MISMATCH_TOLERANCE_S + 30
    a = _strava(start=whoop_start - timedelta(seconds=drift))

    result = compare([_whoop(start=whoop_start)], [a])

    assert result.mismatches[0].delta_start_s == -drift


def test_one_strava_paired_by_two_whoop_rows_emits_independently() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    a = _strava(start=whoop_start)
    drift = MISMATCH_TOLERANCE_S + 1
    w1 = _whoop(start=whoop_start - timedelta(seconds=drift))  # mismatch
    w2 = _whoop(start=whoop_start)  # clean

    result = compare([w1, w2], [a])

    assert len(result.mismatches) == 1
    assert result.mismatches[0].whoop is w1
    assert result.mismatches[0].strava_activity_id == 1
    assert result.whoop_only == []


def test_sport_map_covers_all_documented_sports() -> None:
    cases = [
        ("Running", "Run"),
        ("Walking", "Walk"),
        ("Cycling", "Ride"),
        ("Mountain Biking", "MountainBikeRide"),
        ("Swimming", "Swim"),
        ("Pilates", "Pilates"),
    ]
    for whoop_name, strava_name in cases:
        whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
        w = _whoop(activity_name=whoop_name, start=whoop_start)
        a = _strava(sport_type=strava_name, start=whoop_start)

        result = compare([w], [a])

        assert result.mismatches == [], f"{whoop_name} -> {strava_name} should pair clean"
        assert result.whoop_only == []


def test_mismatch_carries_strava_metadata() -> None:
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    drift = MISMATCH_TOLERANCE_S + 1
    a = _strava(activity_id=12345, start=whoop_start + timedelta(seconds=drift), elapsed_s=1800)

    result = compare([_whoop(start=whoop_start, duration_min=30)], [a])

    m = result.mismatches[0]
    assert m.strava_activity_id == 12345
    assert m.strava_sport_type == "Run"
    assert m.strava_start_utc == whoop_start + timedelta(seconds=drift)
    assert m.strava_end_utc == whoop_start + timedelta(seconds=drift + 1800)


def test_dataclasses_are_frozen() -> None:
    w = _whoop()
    only = WhoopOnly(whoop=w, reason="no_strava_match")
    with pytest.raises((AttributeError, TypeError)):
        only.reason = "unmapped_sport"  # ty: ignore[invalid-assignment]  # frozen dataclass

    comp = WhoopComparison(mismatches=[], whoop_only=[])
    with pytest.raises((AttributeError, TypeError)):
        comp.mismatches = [1]  # ty: ignore[invalid-assignment]  # frozen dataclass


def test_thresholds_are_documented_values() -> None:
    assert PAIRING_WINDOW_S == 600
    assert MISMATCH_TOLERANCE_S == 120


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (1, "+1s"),
        (-1, "-1s"),
        (59, "+59s"),
        (-59, "-59s"),
        (60, "+1m 00s"),
        (-60, "-1m 00s"),
        (65, "+1m 05s"),
        (-65, "-1m 05s"),
        (3600, "+60m 00s"),
        (3661, "+61m 01s"),
        (-3661, "-61m 01s"),
    ],
)
def test_format_signed_delta(seconds: int, expected: str) -> None:
    assert format_signed_delta(seconds) == expected
