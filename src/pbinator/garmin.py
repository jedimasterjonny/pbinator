"""Garmin Connect CSV-export parser.

Pure logic: takes CSV text, returns a list of ``GarminActivity`` dataclasses.
No I/O, no logging.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime

_REQUIRED_COLUMNS: tuple[str, ...] = (
    "Activity Type",
    "Date",
    "Title",
    "Distance",
    "Calories",
    "Time",
    "Avg HR",
    "Max HR",
    "Elapsed Time",
)
_BLANK = "--"
_HMS_BLANK = "--:--:--"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_int_or_none(value: str, field: str, line_no: int) -> int | None:
    raw = value.strip()
    if raw in {_BLANK, ""}:
        return None
    # Garmin formats integers >= 1000 with a thousands separator (e.g. "1,280").
    try:
        return int(raw.replace(",", ""))
    except ValueError as exc:
        msg = f"unparsable {field}: {value!r}"
        raise GarminParseError(line_no, msg) from exc


def _parse_hms_to_s(value: str, field: str, line_no: int) -> int | None:
    raw = value.strip()
    # Garmin uses two blank-sentinel shapes for duration cells: "--" and the
    # literal-position form "--:--:--".
    if raw in {_BLANK, "", _HMS_BLANK}:
        return None
    parts = raw.split(":")
    expected_parts = 3
    if len(parts) != expected_parts:
        msg = f"unparsable {field}: {value!r}"
        raise GarminParseError(line_no, msg)
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        # Seconds may carry a fractional component (e.g. "25.4"); round to the
        # nearest whole second since the rest of the pipeline is integer-seconds.
        seconds = round(float(parts[2]))
    except ValueError as exc:
        msg = f"unparsable {field}: {value!r}"
        raise GarminParseError(line_no, msg) from exc
    return hours * 3600 + minutes * 60 + seconds


def _parse_distance_to_m(value: str, activity_type: str, line_no: int) -> float:
    """Parse Garmin's ``Distance`` cell as metres.

    Garmin's CSV mixes units in the same column: pool swims are reported in
    metres (e.g. ``"600"``), every other activity in kilometres (e.g.
    ``"9.01"``). Branch on ``activity_type`` to apply the right scale.

    Returns:
        Distance in metres rounded to 0.1 m precision.

    Raises:
        GarminParseError: if ``value`` is not a parsable float.
    """
    raw = value.strip()
    multiplier = 1.0 if activity_type == "Pool Swim" else 1000.0
    try:
        return round(float(raw) * multiplier, 1)
    except ValueError as exc:
        msg = f"unparsable Distance: {value!r}"
        raise GarminParseError(line_no, msg) from exc


def _parse_date(value: str, line_no: int) -> datetime:
    try:
        return datetime.strptime(value, _DATE_FMT)  # noqa: DTZ007 — Garmin Date is naive local by design
    except ValueError as exc:
        msg = f"unparsable Date: {value!r}"
        raise GarminParseError(line_no, msg) from exc


class GarminParseError(Exception):
    """Raised when a Garmin CSV row cannot be parsed.

    ``line_no`` is the 1-indexed file line; the header is line 1, the first
    data row is line 2, and so on. ``reason`` is a human-readable message.
    """

    def __init__(self, line_no: int, reason: str) -> None:
        super().__init__(f"line {line_no}: {reason}")
        self.line_no = line_no
        self.reason = reason


@dataclass(frozen=True)
class GarminActivity:
    """One Garmin activity row, parsed.

    ``start_local`` is naive (no ``tzinfo``). All numeric fields that Garmin
    renders as ``--`` parse to ``None``.
    """

    activity_type: str
    start_local: datetime
    title: str
    distance_m: float
    moving_time_s: int | None
    elapsed_time_s: int
    calories: int | None
    avg_hr: int | None
    max_hr: int | None


def _require_field(row: dict[str, str], field: str, line_no: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        msg = f"blank {field}"
        raise GarminParseError(line_no, msg)
    return value


def parse_activities(text: str) -> list[GarminActivity]:
    """Parse a Garmin Connect bulk-export CSV into ``GarminActivity`` rows.

    Returns:
        One ``GarminActivity`` per data row, in input order.

    Raises:
        GarminParseError: when a required column is missing or a row cannot
            be parsed.
    """
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or ()
    missing = [c for c in _REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        msg = f"missing required columns: {missing}"
        raise GarminParseError(1, msg)

    out: list[GarminActivity] = []
    for row in reader:
        line_no = reader.line_num
        activity_type = _require_field(row, "Activity Type", line_no)
        title = _require_field(row, "Title", line_no)
        start_local = _parse_date(_require_field(row, "Date", line_no), line_no)
        distance_m = _parse_distance_to_m(
            _require_field(row, "Distance", line_no), activity_type, line_no
        )
        elapsed_raw = _require_field(row, "Elapsed Time", line_no)
        elapsed_time_s = _parse_hms_to_s(elapsed_raw, "Elapsed Time", line_no)
        if elapsed_time_s is None:
            msg = f"unparsable Elapsed Time: {elapsed_raw!r}"
            raise GarminParseError(line_no, msg)

        out.append(
            GarminActivity(
                activity_type=activity_type,
                start_local=start_local,
                title=title,
                distance_m=distance_m,
                moving_time_s=_parse_hms_to_s(row.get("Time", ""), "Time", line_no),
                elapsed_time_s=elapsed_time_s,
                calories=_parse_int_or_none(row.get("Calories", ""), "Calories", line_no),
                avg_hr=_parse_int_or_none(row.get("Avg HR", ""), "Avg HR", line_no),
                max_hr=_parse_int_or_none(row.get("Max HR", ""), "Max HR", line_no),
            )
        )
    return out
