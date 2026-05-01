"""Personal-best derivation and tabular rendering helpers.

Pure-logic module: takes a sqlite3 connection in, returns dataclasses and
pandas DataFrames out. No Streamlit, no global state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from pbinator.best_efforts import KNOWN_LABELS

if TYPE_CHECKING:
    import sqlite3


DISTANCE_LABELS: tuple[str, ...] = KNOWN_LABELS

DISPLAY_LABELS: dict[str, str] = {
    "400m": "400m",
    "1/2 mile": "½mi",
    "1K": "1km",
    "1 mile": "1mi",
    "2 mile": "2mi",
    "5K": "5km",
    "10K": "10km",
    "15K": "15km",
    "10 mile": "10mi",
    "Half-Marathon": "Half",
    "Marathon": "Marathon",
}


@dataclass(frozen=True)
class PbCell:
    """One cell of the PB table.

    ``moving_time_s`` is the running best at this distance as of the row's date.
    ``is_pb_break`` is ``True`` only on the row where this distance was bettered.
    """

    moving_time_s: int
    is_pb_break: bool


@dataclass(frozen=True)
class PbRow:
    """One row of the PB table — keyed by local date.

    ``cells`` is mutable (dict reassignment within the dict is allowed even on a
    frozen dataclass). ``frozen=True`` only prevents reassigning ``date`` or
    ``cells`` itself.
    """

    date: str
    cells: dict[str, PbCell | None]


def format_time(seconds: int) -> str:
    """Format a duration in seconds as ``m:ss`` (<1h) or ``h:mm:ss`` (≥1h).

    Returns:
        A string like ``"18:30"`` or ``"3:00:00"``.
    """
    hour_seconds = 3600
    minute_seconds = 60
    if seconds >= hour_seconds:
        h, rem = divmod(seconds, hour_seconds)
        m, s = divmod(rem, minute_seconds)
        return f"{h}:{m:02d}:{s:02d}"
    m, s = divmod(seconds, minute_seconds)
    return f"{m}:{s:02d}"


_BREAK_QUERY = """
WITH ordered AS (
    SELECT
        be.distance_label,
        be.moving_time_s,
        SUBSTR(a.start_date_local, 1, 10) AS local_date,
        a.start_date_local                AS sort_key
    FROM best_effort AS be
    JOIN activity   AS a USING (athlete_id, activity_id)
    WHERE be.athlete_id = :athlete_id
), with_running_min AS (
    SELECT
        distance_label,
        local_date,
        moving_time_s,
        MIN(moving_time_s) OVER (
            PARTITION BY distance_label
            ORDER BY sort_key
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS prev_best
    FROM ordered
)
SELECT distance_label, local_date, moving_time_s
FROM with_running_min
WHERE prev_best IS NULL OR moving_time_s < prev_best
ORDER BY local_date DESC, distance_label
"""


_RUNNING_BEST_FOR_DATE_QUERY = """
SELECT be.distance_label, MIN(be.moving_time_s) AS best_so_far
FROM best_effort AS be
JOIN activity    AS a USING (athlete_id, activity_id)
WHERE be.athlete_id = :athlete_id
  AND SUBSTR(a.start_date_local, 1, 10) <= :date
GROUP BY be.distance_label
"""


def compute_rows(conn: sqlite3.Connection, *, athlete_id: int) -> list[PbRow]:
    """Compute the PB table rows for ``athlete_id``.

    Returns:
        Rows ordered newest-first. Each row's cells dict has all ten labels;
        a value is ``None`` if no PB had been set at that distance by the
        row's date.
    """
    breaks = conn.execute(_BREAK_QUERY, {"athlete_id": athlete_id}).fetchall()
    if not breaks:
        return []

    breaks_by_date: dict[str, set[str]] = {}
    for row in breaks:
        breaks_by_date.setdefault(row["local_date"], set()).add(row["distance_label"])

    rows: list[PbRow] = []
    for date in sorted(breaks_by_date.keys(), reverse=True):
        running = conn.execute(
            _RUNNING_BEST_FOR_DATE_QUERY,
            {"athlete_id": athlete_id, "date": date},
        ).fetchall()
        running_by_label = {r["distance_label"]: int(r["best_so_far"]) for r in running}
        cells: dict[str, PbCell | None] = {}
        for label in DISTANCE_LABELS:
            if label in running_by_label:
                cells[label] = PbCell(
                    moving_time_s=running_by_label[label],
                    is_pb_break=label in breaks_by_date[date],
                )
            else:
                cells[label] = None
        rows.append(PbRow(date=date, cells=cells))
    return rows


def to_dataframe(rows: list[PbRow]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Project ``compute_rows`` output into (values, mask) DataFrames.

    Returns:
        ``values_df`` with formatted strings (``"—"`` where ``None``) and
        ``mask_df`` with booleans (``True`` only on PB-break cells). Columns
        are display labels in canonical order; index is the row dates.
    """
    display_columns = [DISPLAY_LABELS[label] for label in DISTANCE_LABELS]

    if not rows:
        return (
            pd.DataFrame(columns=display_columns),
            pd.DataFrame(columns=display_columns),
        )

    dates = [row.date for row in rows]
    values: list[list[str]] = []
    masks: list[list[bool]] = []
    for row in rows:
        values_row = []
        mask_row = []
        for label in DISTANCE_LABELS:
            cell = row.cells.get(label)
            if cell is None:
                values_row.append("—")
                mask_row.append(False)
            else:
                values_row.append(format_time(cell.moving_time_s))
                mask_row.append(cell.is_pb_break)
        values.append(values_row)
        masks.append(mask_row)

    index = pd.Index(dates, name="Date")
    return (
        pd.DataFrame(values, index=index, columns=display_columns),
        pd.DataFrame(masks, index=index, columns=display_columns),
    )
