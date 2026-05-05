import json
from datetime import datetime
from typing import Any

import pytest

from pbinator import garmin_compare
from pbinator.garmin import GarminActivity
from pbinator.models import Activity


def _g(  # noqa: PLR0913 — test helper builder
    *,
    activity_type: str = "Running",
    start_local: datetime | None = None,
    title: str = "Easy Run",
    distance_m: float = 9010.0,
    elapsed_time_s: int = 3208,
    moving_time_s: int | None = 3191,
    calories: int | None = 631,
    avg_hr: int | None = 156,
    max_hr: int | None = 168,
) -> GarminActivity:
    return GarminActivity(
        activity_type=activity_type,
        start_local=start_local or datetime(2026, 5, 2, 13, 18, 6),  # noqa: DTZ001 — naive by design
        title=title,
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=elapsed_time_s,
        calories=calories,
        avg_hr=avg_hr,
        max_hr=max_hr,
    )


def test_compare_empty_garmin_returns_empty_result() -> None:
    result = garmin_compare.compare(garmin=[], strava=[])
    assert result.mismatches == []
    assert result.garmin_only == []
    assert result.strava_only == []


def test_compare_empty_garmin_with_strava_present_emits_no_strava_only() -> None:
    """Empty Garmin → no min/max on empty range, no Strava-only emissions."""
    s = _strava()
    result = garmin_compare.compare(garmin=[], strava=[s])
    assert result.strava_only == []


def test_sport_map_contains_known_garmin_types() -> None:
    assert garmin_compare.SPORT_MAP["Running"] == "Run"
    assert garmin_compare.SPORT_MAP["Walking"] == "Walk"
    assert garmin_compare.SPORT_MAP["Pool Swim"] == "Swim"
    assert garmin_compare.SPORT_MAP["Pilates"] == "Pilates"
    assert garmin_compare.SPORT_MAP["Mobility"] == "Workout"


def test_pairing_window_constant() -> None:
    assert garmin_compare.PAIRING_WINDOW_S == 60


def _strava_raw(
    *,
    average_heartrate: float | None = 156,
    max_heartrate: float | None = 168,
    calories: float | None = 631,
) -> dict[str, Any]:
    candidates: dict[str, float | None] = {
        "average_heartrate": average_heartrate,
        "max_heartrate": max_heartrate,
        "calories": calories,
    }
    return {k: v for k, v in candidates.items() if v is not None}


def test_field_rules_include_all_9_entries() -> None:
    names = [r.name for r in garmin_compare.FIELD_RULES]
    assert names == [
        "sport_type",
        "title",
        "start_local",
        "distance_m",
        "moving_time_s",
        "elapsed_time_s",
        "calories",
        "avg_hr",
        "max_hr",
    ]


def test_field_rules_strava_getter_returns_none_when_absent() -> None:
    rule = next(r for r in garmin_compare.FIELD_RULES if r.name == "avg_hr")
    fake_activity = _strava()
    assert rule.strava_get(fake_activity, {}) is None


def test_field_rules_start_local_returns_none_when_strava_local_missing() -> None:
    """``_s_start`` returns None directly when ``start_date_local`` is null on the row."""
    rule = next(r for r in garmin_compare.FIELD_RULES if r.name == "start_local")
    s = _strava(start_date_local=None)
    assert rule.strava_get(s, {}) is None


def _strava(  # noqa: PLR0913 — test helper builder
    *,
    activity_id: int = 100,
    sport_type: str = "Run",
    name: str = "Easy Run",
    start_date: str = "2026-05-02T12:18:06Z",
    start_date_local: str | None = "2026-05-02T13:18:06",
    distance_m: float = 9010.0,
    moving_time_s: int = 3191,
    elapsed_time_s: int = 3208,
    total_elev_gain_m: float = 55,
    raw: dict[str, Any] | None = None,
) -> Activity:
    return Activity(
        athlete_id=42,
        activity_id=activity_id,
        sport_type=sport_type,
        start_date=start_date,
        start_date_local=start_date_local,
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=elapsed_time_s,
        total_elev_gain_m=total_elev_gain_m,
        name=name,
        raw_json=json.dumps(raw if raw is not None else _strava_raw()),
        fetched_at="2026-05-02T13:30:00+00:00",
    )


def test_compare_clean_pair_emits_nothing() -> None:
    g = _g()
    s = _strava()
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert result.mismatches == []
    assert result.garmin_only == []
    assert result.strava_only == []


def test_compare_skips_default_named_pair_entirely() -> None:
    """When BOTH sides carry an auto-generated name, skip every field rule."""
    # Distance disagreement that would normally flag — but the pair is auto-named
    # on both sides, so no field mismatches should be emitted.
    g = _g(title="Bournemouth Running", distance_m=9011.0)
    s = _strava(name="Morning Run", distance_m=9000.0)
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert result.mismatches == []
    # The pair still consumed paired_ids, so the Strava activity isn't Strava-only.
    assert result.strava_only == []
    assert result.garmin_only == []


def test_compare_default_named_garmin_with_custom_strava_still_flags() -> None:
    g = _g(title="Bournemouth Running", distance_m=9011.0)
    s = _strava(name="Easy Run - 9km", distance_m=9000.0)
    result = garmin_compare.compare(garmin=[g], strava=[s])
    fields = {m.field for m in result.mismatches}
    assert "distance_m" in fields
    assert "title" in fields  # different titles still flag


def test_compare_default_named_strava_with_custom_garmin_still_flags() -> None:
    g = _g(title="Easy Run - 9km", distance_m=9011.0)
    s = _strava(name="Morning Run", distance_m=9000.0)
    result = garmin_compare.compare(garmin=[g], strava=[s])
    fields = {m.field for m in result.mismatches}
    assert "distance_m" in fields
    assert "title" in fields


def test_compare_strava_afternoon_run_is_treated_as_default() -> None:
    """Afternoon is one of Strava's five auto-name time-of-day prefixes."""
    g = _g(title="Bournemouth Running")
    s = _strava(name="Afternoon Run")
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert result.mismatches == []
    assert result.strava_only == []  # pair still consumed paired_id


def test_compare_strava_random_prefix_is_not_treated_as_default() -> None:
    """A non-default Strava prefix (e.g. user-typed) still flags normally."""
    g = _g(title="Bournemouth Running")
    s = _strava(name="Random Run")
    result = garmin_compare.compare(garmin=[g], strava=[s])
    title_mismatches = [m for m in result.mismatches if m.field == "title"]
    assert len(title_mismatches) == 1


def test_compare_pool_swim_skips_moving_time_rule() -> None:
    """Garmin's Pool Swim "Time" includes wall rest; Strava's moving_time_s does not."""
    g = _g(activity_type="Pool Swim", title="Stroke Refinement - 600m", moving_time_s=1423)
    s = _strava(sport_type="Swim", name="Stroke Refinement - 600m", moving_time_s=820)
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert all(m.field != "moving_time_s" for m in result.mismatches)


def test_compare_running_still_compares_moving_time() -> None:
    """Sanity: the Pool-Swim skip doesn't suppress moving_time on other sports."""
    g = _g(activity_type="Running", moving_time_s=3000)
    s = _strava(sport_type="Run", moving_time_s=2950)  # |Δ| = 50 > tolerance 10
    result = garmin_compare.compare(garmin=[g], strava=[s])
    moving = [m for m in result.mismatches if m.field == "moving_time_s"]
    assert len(moving) == 1


def test_compare_handles_strava_local_with_trailing_z() -> None:
    """Strava serialises start_date_local with a misleading trailing Z.

    The string represents naive local time; the Z is a Strava API artifact.
    The comparator must strip it before parsing so subtraction against the
    Garmin naive datetime doesn't raise on aware/naive mixing.
    """
    g = _g()
    s = _strava(start_date_local="2026-05-02T13:18:06Z")
    result = garmin_compare.compare(garmin=[g], strava=[s])
    # Same instant once Z is stripped, so the pair is clean.
    assert result.mismatches == []
    assert result.garmin_only == []
    assert result.strava_only == []


def test_compare_title_strict_mismatch_emits_one() -> None:
    g = _g(title="Easy Run")
    s = _strava(name="Easy Run ")  # trailing space
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.field == "title"
    assert m.garmin_value == "Easy Run"
    assert m.strava_value == "Easy Run "
    assert m.delta is None


def test_compare_distance_within_tolerance_no_flag() -> None:
    g = _g(distance_m=9010.0)
    s = _strava(distance_m=9000.0)  # Δ = 10, tolerance = 10
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert all(m.field != "distance_m" for m in result.mismatches)


def test_compare_distance_beyond_tolerance_flags() -> None:
    g = _g(distance_m=9011.0)
    s = _strava(distance_m=9000.0)  # Δ = 11, > tolerance = 10
    result = garmin_compare.compare(garmin=[g], strava=[s])
    distance_mismatches = [m for m in result.mismatches if m.field == "distance_m"]
    assert len(distance_mismatches) == 1
    assert distance_mismatches[0].delta == pytest.approx(11.0)


def test_compare_start_local_within_tolerance_no_flag() -> None:
    # Δ = 2s, within tolerance.
    g = _g(start_local=datetime(2026, 5, 2, 13, 18, 6))  # noqa: DTZ001 — naive by design
    s = _strava(start_date_local="2026-05-02T13:18:08")
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert all(m.field != "start_local" for m in result.mismatches)


def test_compare_start_local_beyond_tolerance_flags() -> None:
    # Δ = 5s: pairs (within ±60s window) but flags (beyond ±2s field tolerance).
    g = _g(start_local=datetime(2026, 5, 2, 13, 18, 6))  # noqa: DTZ001 — naive by design
    s = _strava(start_date_local="2026-05-02T13:18:11")
    result = garmin_compare.compare(garmin=[g], strava=[s])
    start_mismatches = [m for m in result.mismatches if m.field == "start_local"]
    assert len(start_mismatches) == 1
    assert start_mismatches[0].delta == pytest.approx(-5.0)


def test_compare_skips_field_when_garmin_none() -> None:
    g = _g(calories=None)
    s = _strava(raw=_strava_raw(calories=999))
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert all(m.field != "calories" for m in result.mismatches)


def test_compare_skips_field_when_strava_raw_field_absent() -> None:
    g = _g(avg_hr=156)
    s = _strava(raw=_strava_raw(average_heartrate=None))
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert all(m.field != "avg_hr" for m in result.mismatches)


def test_compare_sport_mapping_running_run_clean() -> None:
    g = _g(activity_type="Running")
    s = _strava(sport_type="Run")
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert all(m.field != "sport_type" for m in result.mismatches)


def test_compare_sport_mapping_mobility_walk_flags() -> None:
    g = _g(activity_type="Mobility")
    s = _strava(sport_type="Walk")  # Mobility maps to Workout
    result = garmin_compare.compare(garmin=[g], strava=[s])
    sport = [m for m in result.mismatches if m.field == "sport_type"]
    assert len(sport) == 1
    assert sport[0].garmin_value == "Workout"  # mapped value
    assert sport[0].strava_value == "Walk"


def test_compare_no_strava_pair_emits_garmin_only() -> None:
    g = _g(start_local=datetime(2026, 5, 2, 13, 0, 0))  # noqa: DTZ001 — naive by design
    s = _strava(start_date_local="2026-05-02T15:00:00")  # 2 hours later
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert len(result.garmin_only) == 1
    assert result.mismatches == []


def test_compare_picks_closer_of_two_candidates() -> None:
    # Garmin window spans 13:00:00 → 13:01:00 so both Strava candidates fall inside
    # the date-range guard for Strava-only emission.
    g_first = _g(start_local=datetime(2026, 5, 2, 13, 0, 0))  # noqa: DTZ001 — naive by design
    g_last = _g(start_local=datetime(2026, 5, 2, 13, 1, 0))  # noqa: DTZ001 — naive by design
    s_close = _strava(
        activity_id=1, start_date_local="2026-05-02T13:00:10", name="A"
    )  # +10s from g_first
    s_far = _strava(
        activity_id=2, start_date_local="2026-05-02T13:00:30", name="B"
    )  # +30s from g_first, +30s before g_last
    result = garmin_compare.compare(garmin=[g_first, g_last], strava=[s_close, s_far])
    # g_first picks s_close (Δ=10 vs 30); g_last picks s_far (Δ=30 vs 50).
    # Both Strava get paired, so neither appears in strava_only.
    assert result.strava_only == []
    # The mismatch references the chosen Strava for g_first's title.
    title_mismatches = [m for m in result.mismatches if m.field == "title"]
    pair_for_first = next(m for m in title_mismatches if m.garmin is g_first)
    assert pair_for_first.strava_activity_id == 1


def test_compare_tie_break_lower_activity_id_wins() -> None:
    g = _g(start_local=datetime(2026, 5, 2, 13, 0, 0), title="G")  # noqa: DTZ001 — naive by design
    s_lo = _strava(activity_id=1, start_date_local="2026-05-02T13:00:10", name="A")
    s_hi = _strava(activity_id=2, start_date_local="2026-05-02T13:00:10", name="B")  # exact tie
    result = garmin_compare.compare(garmin=[g], strava=[s_lo, s_hi])
    # Lower activity_id wins on tie, so s_lo (id=1) is chosen.
    title_mismatches = [m for m in result.mismatches if m.field == "title"]
    assert len(title_mismatches) == 1
    assert title_mismatches[0].strava_activity_id == 1


def test_compare_strava_in_range_unpaired_emits_strava_only() -> None:
    g_first = _g(start_local=datetime(2026, 5, 1, 12, 0, 0))  # noqa: DTZ001 — naive by design
    g_last = _g(start_local=datetime(2026, 5, 2, 13, 18, 6))  # noqa: DTZ001 — naive by design
    s_paired = _strava(activity_id=1, start_date_local="2026-05-02T13:18:06")
    s_unpaired = _strava(activity_id=2, start_date_local="2026-05-01T18:00:00")
    result = garmin_compare.compare(garmin=[g_first, g_last], strava=[s_paired, s_unpaired])
    only_ids = {o.activity.activity_id for o in result.strava_only}
    assert only_ids == {2}


def test_compare_strava_outside_range_not_emitted() -> None:
    g = _g(start_local=datetime(2026, 5, 2, 13, 18, 6))  # noqa: DTZ001 — naive by design
    s_in = _strava(activity_id=1, start_date_local="2026-05-02T13:18:06")
    s_out = _strava(activity_id=2, start_date_local="2025-01-01T00:00:00")
    result = garmin_compare.compare(garmin=[g], strava=[s_in, s_out])
    assert result.strava_only == []


def test_compare_paired_strava_not_also_strava_only() -> None:
    g = _g()
    s = _strava()
    result = garmin_compare.compare(garmin=[g], strava=[s])
    assert result.strava_only == []


def test_compare_skips_strava_with_null_start_date_local() -> None:
    """Strava activities with null start_date_local must be skipped silently."""
    g = _g()
    s = _strava(start_date_local=None)
    result = garmin_compare.compare(garmin=[g], strava=[s])
    # No pair available → Garmin-only, no Strava-only emission.
    assert len(result.garmin_only) == 1
    assert result.strava_only == []
