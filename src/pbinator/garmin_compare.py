"""Garmin ↔ Strava per-field comparison.

Pure logic: takes parsed Garmin rows and Strava ``Activity`` rows, returns
a ``GarminComparison``. No I/O, no clock reads, no DB.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from operator import itemgetter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pbinator.garmin import GarminActivity
    from pbinator.models import Activity


SPORT_MAP: dict[str, str] = {
    "Running": "Run",
    "Walking": "Walk",
    "Pool Swim": "Swim",
    "Pilates": "Pilates",
    "Mobility": "Workout",
}

PAIRING_WINDOW_S = 60


def _g_sport(g: GarminActivity) -> str | None:
    return SPORT_MAP.get(g.activity_type)


def _s_sport(a: Activity, _raw: dict[str, Any]) -> str:
    return a.sport_type


def _g_title(g: GarminActivity) -> str:
    return g.title


def _s_name(a: Activity, _raw: dict[str, Any]) -> str:
    return a.name


def _g_start(g: GarminActivity) -> float:
    return g.start_local.timestamp()


def _s_start(a: Activity, _raw: dict[str, Any]) -> float | None:
    if a.start_date_local is None:
        return None
    return datetime.fromisoformat(a.start_date_local).timestamp()


def _g_distance(g: GarminActivity) -> float:
    return g.distance_m


def _s_distance(a: Activity, _raw: dict[str, Any]) -> float:
    return a.distance_m


def _g_moving_time(g: GarminActivity) -> int | None:
    return g.moving_time_s


def _g_moving_time_alt(g: GarminActivity) -> int | None:
    return g.moving_time_alt_s


def _s_moving_time(a: Activity, _raw: dict[str, Any]) -> int:
    return a.moving_time_s


def _g_elapsed(g: GarminActivity) -> int:
    return g.elapsed_time_s


def _s_elapsed(a: Activity, _raw: dict[str, Any]) -> int:
    return a.elapsed_time_s


def _g_calories(g: GarminActivity) -> int | None:
    return g.calories


def _s_calories(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("calories")


def _g_avg_hr(g: GarminActivity) -> int | None:
    return g.avg_hr


def _s_avg_hr(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("average_heartrate")


def _g_max_hr(g: GarminActivity) -> int | None:
    return g.max_hr


def _s_max_hr(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("max_heartrate")


def _g_ascent(g: GarminActivity) -> int | None:
    return g.total_ascent_m


def _s_ascent(a: Activity, _raw: dict[str, Any]) -> float:
    return a.total_elev_gain_m


def _g_min_elev(g: GarminActivity) -> int | None:
    return g.min_elevation_m


def _s_min_elev(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("elev_low")


def _g_max_elev(g: GarminActivity) -> int | None:
    return g.max_elevation_m


def _s_max_elev(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("elev_high")


def _g_avg_cadence(g: GarminActivity) -> int | None:
    return g.avg_run_cadence


def _s_avg_cadence(_a: Activity, raw: dict[str, Any]) -> float | None:
    val = raw.get("average_cadence")
    return None if val is None else val * 2


def _g_max_cadence(g: GarminActivity) -> int | None:
    return g.max_run_cadence


def _s_max_cadence(_a: Activity, raw: dict[str, Any]) -> float | None:
    val = raw.get("max_cadence")
    return None if val is None else val * 2


def _g_avg_power(g: GarminActivity) -> int | None:
    return g.avg_power


def _s_avg_power(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("average_watts")


def _g_max_power(g: GarminActivity) -> int | None:
    return g.max_power


def _s_max_power(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("max_watts")


def _g_np(g: GarminActivity) -> int | None:
    return g.normalized_power


def _s_np(_a: Activity, raw: dict[str, Any]) -> float | None:
    return raw.get("weighted_average_watts")


@dataclass(frozen=True)
class FieldRule:
    """One field comparison rule."""

    name: str
    garmin_get: Callable[[GarminActivity], object]
    strava_get: Callable[[Activity, dict[str, Any]], object]
    numeric: bool
    tolerance: float


FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule("sport_type", _g_sport, _s_sport, numeric=False, tolerance=0),
    FieldRule("title", _g_title, _s_name, numeric=False, tolerance=0),
    FieldRule("start_local", _g_start, _s_start, numeric=True, tolerance=60),
    FieldRule("distance_m", _g_distance, _s_distance, numeric=True, tolerance=10),
    FieldRule("moving_time_s", _g_moving_time, _s_moving_time, numeric=True, tolerance=2),
    FieldRule("moving_time_alt_s", _g_moving_time_alt, _s_moving_time, numeric=True, tolerance=2),
    FieldRule("elapsed_time_s", _g_elapsed, _s_elapsed, numeric=True, tolerance=2),
    FieldRule("calories", _g_calories, _s_calories, numeric=True, tolerance=1),
    FieldRule("avg_hr", _g_avg_hr, _s_avg_hr, numeric=True, tolerance=1),
    FieldRule("max_hr", _g_max_hr, _s_max_hr, numeric=True, tolerance=1),
    FieldRule("total_ascent_m", _g_ascent, _s_ascent, numeric=True, tolerance=1),
    FieldRule("min_elevation_m", _g_min_elev, _s_min_elev, numeric=True, tolerance=1),
    FieldRule("max_elevation_m", _g_max_elev, _s_max_elev, numeric=True, tolerance=1),
    FieldRule("avg_cadence", _g_avg_cadence, _s_avg_cadence, numeric=True, tolerance=1),
    FieldRule("max_cadence", _g_max_cadence, _s_max_cadence, numeric=True, tolerance=1),
    FieldRule("avg_power", _g_avg_power, _s_avg_power, numeric=True, tolerance=1),
    FieldRule("max_power", _g_max_power, _s_max_power, numeric=True, tolerance=1),
    FieldRule("normalized_power", _g_np, _s_np, numeric=True, tolerance=1),
)


@dataclass(frozen=True)
class FieldMismatch:
    """One disagreeing field on one paired (Garmin, Strava) row.

    ``delta`` is signed (garmin - strava) when both values are numeric;
    ``None`` for non-numeric or skipped rules.
    """

    garmin: GarminActivity
    strava_activity_id: int
    field: str
    garmin_value: object
    strava_value: object
    delta: float | None


@dataclass(frozen=True)
class GarminOnly:
    """Garmin row with no Strava counterpart in the pairing window."""

    garmin: GarminActivity


@dataclass(frozen=True)
class StravaOnly:
    """Strava activity inside the Garmin date-range with no Garmin pair."""

    activity: Activity


@dataclass(frozen=True)
class GarminComparison:
    """Result of comparing Garmin rows against Strava activities."""

    mismatches: list[FieldMismatch]
    garmin_only: list[GarminOnly]
    strava_only: list[StravaOnly]


def _eval_rule(
    rule: FieldRule,
    garmin: GarminActivity,
    strava: Activity,
    raw: dict[str, Any],
) -> FieldMismatch | None:
    g_val = rule.garmin_get(garmin)
    s_val = rule.strava_get(strava, raw)
    if g_val is None or s_val is None:
        return None
    if not rule.numeric:
        if g_val == s_val:
            return None
        return FieldMismatch(
            garmin=garmin,
            strava_activity_id=strava.activity_id,
            field=rule.name,
            garmin_value=g_val,
            strava_value=s_val,
            delta=None,
        )
    # `g_val` and `s_val` are typed as `object` but narrowed to numeric by the None-check above.
    delta = float(g_val) - float(s_val)  # ty: ignore[invalid-argument-type]
    if abs(delta) <= rule.tolerance:
        return None
    return FieldMismatch(
        garmin=garmin,
        strava_activity_id=strava.activity_id,
        field=rule.name,
        garmin_value=g_val,
        strava_value=s_val,
        delta=delta,
    )


def compare(
    *,
    garmin: Sequence[GarminActivity],
    strava: Sequence[Activity],
) -> GarminComparison:
    """Pair each Garmin row to its closest Strava activity and emit per-field mismatches.

    Pairing is sport-agnostic on ``|Δ start_date_local| ≤ PAIRING_WINDOW_S``;
    ties on ``|Δ|`` break on lower ``activity_id``. After processing all
    Garmin rows, any unpaired Strava activity whose ``start_date_local``
    falls inside ``[min(garmin.start_local), max(garmin.start_local)]`` is
    emitted as ``StravaOnly``.

    Returns:
        A ``GarminComparison`` with mismatches, garmin_only, strava_only.
    """
    if not garmin:
        return GarminComparison(mismatches=[], garmin_only=[], strava_only=[])

    parsed: list[tuple[Activity, datetime, dict[str, Any]]] = []
    for a in strava:
        if a.start_date_local is None:
            continue
        local = datetime.fromisoformat(a.start_date_local)
        raw = json.loads(a.raw_json)
        parsed.append((a, local, raw))

    mismatches: list[FieldMismatch] = []
    garmin_only: list[GarminOnly] = []
    paired_ids: set[int] = set()

    for g in garmin:
        candidates: list[tuple[float, int, Activity, dict[str, Any]]] = []
        for activity, s_local, raw in parsed:
            delta = abs((s_local - g.start_local).total_seconds())
            if delta <= PAIRING_WINDOW_S:
                candidates.append((delta, activity.activity_id, activity, raw))
        if not candidates:
            garmin_only.append(GarminOnly(garmin=g))
            continue
        candidates.sort(key=itemgetter(0, 1))
        _, _, chosen, raw = candidates[0]
        paired_ids.add(chosen.activity_id)
        for rule in FIELD_RULES:
            mismatch = _eval_rule(rule, g, chosen, raw)
            if mismatch is not None:
                mismatches.append(mismatch)

    lo = min(g.start_local for g in garmin)
    hi = max(g.start_local for g in garmin)
    strava_only: list[StravaOnly] = [
        StravaOnly(activity=a)
        for a, s_local, _raw in parsed
        if a.activity_id not in paired_ids and lo <= s_local <= hi
    ]

    return GarminComparison(mismatches=mismatches, garmin_only=garmin_only, strava_only=strava_only)
