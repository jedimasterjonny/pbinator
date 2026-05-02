"""Whoop ↔ Strava pairing and classification.

Pure logic: takes parsed Whoop rows and Strava ``Activity`` rows, returns
a ``WhoopComparison``. No I/O, no clock reads, no DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from operator import itemgetter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pbinator.models import Activity
    from pbinator.whoop import WhoopWorkout


SPORT_MAP: dict[str, str] = {
    "Running": "Run",
    "Walking": "Walk",
    "Cycling": "Ride",
    "Mountain Biking": "MountainBikeRide",
    "Swimming": "Swim",
    "Pilates": "Pilates",
}

PAIRING_WINDOW_S = 600
MISMATCH_TOLERANCE_S = 120


@dataclass(frozen=True)
class TimeMismatch:
    """A Whoop/Strava pair whose start or end time differs by more than the tolerance."""

    whoop: WhoopWorkout
    strava_activity_id: int
    strava_sport_type: str
    strava_start_utc: datetime
    strava_end_utc: datetime
    delta_start_s: int  # signed: strava - whoop
    delta_end_s: int  # signed
    flagged_start: bool
    flagged_end: bool


@dataclass(frozen=True)
class WhoopOnly:
    """A Whoop row with no plausible Strava counterpart, or an unmapped sport."""

    whoop: WhoopWorkout
    reason: str  # "no_strava_match" | "unmapped_sport"


@dataclass(frozen=True)
class WhoopComparison:
    """Result of comparing a list of Whoop workouts to a list of Strava activities."""

    mismatches: list[TimeMismatch]
    whoop_only: list[WhoopOnly]


def compare(
    workouts: Sequence[WhoopWorkout],
    activities: Sequence[Activity],
) -> WhoopComparison:
    """Pair each Whoop row to its closest plausible Strava activity and classify.

    For each ``WhoopWorkout`` (in input order):

    * If its sport is not in ``SPORT_MAP``, emit ``WhoopOnly("unmapped_sport")``.
    * Otherwise, candidates are activities of the mapped sport whose start
      time is within ``PAIRING_WINDOW_S`` of the Whoop start.
    * If no candidates, emit ``WhoopOnly("no_strava_match")``.
    * Otherwise pick the one with the smallest absolute start-delta; ties
      break on lower ``activity_id`` for determinism.
    * If the chosen pair has start- or end-delta exceeding
      ``MISMATCH_TOLERANCE_S``, append a ``TimeMismatch``.

    A Strava activity may be the chosen pair for multiple Whoop rows;
    pairing is independent per row.

    Returns:
        A ``WhoopComparison`` with two lists.
    """
    # Pre-parse Strava start times once; we touch each O(W) times.
    parsed: list[tuple[Activity, datetime]] = [
        (a, datetime.fromisoformat(a.start_date)) for a in activities
    ]

    mismatches: list[TimeMismatch] = []
    whoop_only: list[WhoopOnly] = []

    for w in workouts:
        strava_sport = SPORT_MAP.get(w.activity_name)
        if strava_sport is None:
            whoop_only.append(WhoopOnly(whoop=w, reason="unmapped_sport"))
            continue

        candidates: list[tuple[float, int, Activity, datetime]] = []
        for activity, start_utc in parsed:
            if activity.sport_type != strava_sport:
                continue
            delta = (start_utc - w.start_utc).total_seconds()
            if abs(delta) <= PAIRING_WINDOW_S:
                candidates.append((abs(delta), activity.activity_id, activity, start_utc))

        if not candidates:
            whoop_only.append(WhoopOnly(whoop=w, reason="no_strava_match"))
            continue

        candidates.sort(key=itemgetter(0, 1))
        _, _, chosen, strava_start = candidates[0]
        strava_end = strava_start + timedelta(seconds=chosen.elapsed_time_s)
        delta_start_s = int((strava_start - w.start_utc).total_seconds())
        delta_end_s = int((strava_end - w.end_utc).total_seconds())
        flagged_start = abs(delta_start_s) > MISMATCH_TOLERANCE_S
        flagged_end = abs(delta_end_s) > MISMATCH_TOLERANCE_S

        if flagged_start or flagged_end:
            mismatches.append(
                TimeMismatch(
                    whoop=w,
                    strava_activity_id=chosen.activity_id,
                    strava_sport_type=chosen.sport_type,
                    strava_start_utc=strava_start,
                    strava_end_utc=strava_end,
                    delta_start_s=delta_start_s,
                    delta_end_s=delta_end_s,
                    flagged_start=flagged_start,
                    flagged_end=flagged_end,
                )
            )

    return WhoopComparison(mismatches=mismatches, whoop_only=whoop_only)


def format_signed_delta(seconds: int) -> str:
    """Format a signed second-count as ``±Mm SSs`` or ``±Ss`` for ``|Δ| < 60``.

    Returns:
        ``"0s"`` when ``seconds == 0``; otherwise a signed string with
        minutes (when ``|seconds| >= 60``) and zero-padded remainder seconds.
    """
    if seconds == 0:
        return "0s"
    sign = "+" if seconds > 0 else "-"
    magnitude = abs(seconds)
    minutes, remainder = divmod(magnitude, 60)
    if minutes == 0:
        return f"{sign}{remainder}s"
    return f"{sign}{minutes}m {remainder:02d}s"
