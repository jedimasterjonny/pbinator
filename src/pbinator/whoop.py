"""Whoop CSV-export parser.

Pure logic: takes CSV text, returns a list of ``WhoopWorkout`` dataclasses.
No I/O, no logging.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

_REQUIRED_COLUMNS = (
    "Cycle timezone",
    "Workout start time",
    "Workout end time",
    "Duration (min)",
    "Activity name",
)

_TZ_RE = re.compile(r"^UTC(Z|([+-])(\d{2}):(\d{2}))$")
_TS_FMT = "%Y-%m-%d %H:%M:%S"


class WhoopParseError(Exception):
    """Raised when a Whoop CSV row cannot be parsed.

    ``line_no`` is the 1-indexed file line; the header is line 1, the first
    data row is line 2, and so on. ``reason`` is a human-readable message.
    """

    def __init__(self, line_no: int, reason: str) -> None:
        super().__init__(f"line {line_no}: {reason}")
        self.line_no = line_no
        self.reason = reason


@dataclass(frozen=True)
class WhoopWorkout:
    """One Whoop workout row, parsed.

    ``start_utc`` and ``end_utc`` are tz-aware UTC datetimes, derived by
    applying the row's ``Cycle timezone`` offset to the local timestamps.
    """

    activity_name: str
    start_utc: datetime
    end_utc: datetime
    duration_min: int


def _parse_offset(tz_str: str, line_no: int) -> timezone:
    match = _TZ_RE.fullmatch(tz_str)
    if match is None:
        msg = f"unparsable timezone: {tz_str!r}"
        raise WhoopParseError(line_no, msg)
    if match.group(1) == "Z":
        return UTC
    hours = int(match.group(3))
    minutes = int(match.group(4))
    if hours >= 24 or minutes >= 60:  # noqa: PLR2004 — inline calendar bounds
        msg = f"unparsable timezone: {tz_str!r}"
        raise WhoopParseError(line_no, msg)
    sign = 1 if match.group(2) == "+" else -1
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse_local_dt(value: str, tz: timezone, field: str, line_no: int) -> datetime:
    try:
        naive = datetime.strptime(value, _TS_FMT)  # noqa: DTZ007 — offset attached on next line
    except ValueError as exc:
        msg = f"unparsable {field}: {value!r}"
        raise WhoopParseError(line_no, msg) from exc
    return naive.replace(tzinfo=tz).astimezone(UTC)


def _require_field(row: dict[str, str], field: str, line_no: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        msg = f"blank {field}"
        raise WhoopParseError(line_no, msg)
    return value


def parse_workouts(text: str) -> list[WhoopWorkout]:
    """Parse a Whoop bulk-export CSV into ``WhoopWorkout`` rows.

    Returns:
        One ``WhoopWorkout`` per non-blank-start data row. Rows whose
        ``Workout start time`` is blank are silently skipped (Whoop emits
        these for cycles without a workout). Other blank required fields
        raise ``WhoopParseError``.

    Raises:
        WhoopParseError: when a required column is missing, or any row
            cannot be parsed.
    """
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or ()
    missing = [c for c in _REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        msg = f"missing required columns: {missing}"
        raise WhoopParseError(1, msg)

    workouts: list[WhoopWorkout] = []
    for row in reader:
        line_no = reader.line_num  # 2 for first data row
        if not (row.get("Workout start time") or "").strip():
            continue
        tz = _parse_offset(_require_field(row, "Cycle timezone", line_no), line_no)
        start_utc = _parse_local_dt(
            _require_field(row, "Workout start time", line_no),
            tz,
            "Workout start time",
            line_no,
        )
        end_utc = _parse_local_dt(
            _require_field(row, "Workout end time", line_no),
            tz,
            "Workout end time",
            line_no,
        )
        duration_str = _require_field(row, "Duration (min)", line_no)
        try:
            duration_min = int(duration_str)
        except ValueError as exc:
            msg = f"unparsable Duration (min): {duration_str!r}"
            raise WhoopParseError(line_no, msg) from exc
        activity_name = _require_field(row, "Activity name", line_no)
        workouts.append(
            WhoopWorkout(
                activity_name=activity_name,
                start_utc=start_utc,
                end_utc=end_utc,
                duration_min=duration_min,
            )
        )
    return workouts
