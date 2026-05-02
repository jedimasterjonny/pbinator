# Whoop ↔ Strava comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Whoop" tab to the Streamlit app that flags time-mismatches and missing-from-Strava activities by comparing a Whoop CSV export against the Strava activity database.

**Architecture:** Two new pure-logic modules (`whoop.py`, `compare.py`), one helper added to `store.py`, one settings field, and a third tab in `app.py`. No schema changes — comparison runs on render. 100% branch coverage stays.

**Tech Stack:** Python 3.14, SQLModel + SQLAlchemy, Streamlit, pytest. Stdlib only for the new modules (`csv`, `dataclasses`, `datetime`, `re`).

**Spec:** `docs/superpowers/specs/2026-05-02-whoop-strava-compare-design.md`.

---

## Conventions

- All new code follows the existing module style: pure logic where possible, `@dataclass(frozen=True)`, no relative imports, no `os.environ` reads.
- Run `just check` (lint + format-check + typecheck + test) after every task before committing.
- Commit messages are conventional commits (`feat(scope): …`, `chore: …`). The pre-commit hook enforces the format.
- Stage hunks deliberately with `git add <paths>` — never `git add -A`.
- Each task is one commit; do not bundle commits.
- Tests use real `Session`/`Engine` fixtures from `tests/conftest.py` where DB is involved; pure-logic modules are tested without DB.

## File map

**Created:**
- `src/pbinator/whoop.py` — Whoop CSV parser; defines `WhoopWorkout`, `WhoopParseError`, `parse_workouts` (Task 3).
- `src/pbinator/compare.py` — pairing/classification; defines `TimeMismatch`, `WhoopOnly`, `WhoopComparison`, `compare`, sport map, thresholds (Task 4).
- `tests/test_whoop.py` — covers `whoop.py` (Task 3).
- `tests/test_compare.py` — covers `compare.py` (Task 4).

**Modified:**
- `src/pbinator/store.py` — add `activities_in_range` (Task 1).
- `tests/test_store.py` — extend with `activities_in_range` tests (Task 1).
- `src/pbinator/settings.py` — add `whoop_csv_path` field (Task 2).
- `tests/test_settings.py` — extend with `whoop_csv_path` tests (Task 2).
- `src/pbinator/app.py` — add `_render_whoop_tab` and a third tab (Task 5; not tested — `app.py` is coverage-excluded).

---

## Task 1: Add `activities_in_range` to `store.py`

**Files:**
- Modify: `src/pbinator/store.py`
- Test: `tests/test_store.py`

**Why this task is first:** Pure addition; no consumers yet. Lands a tested helper that later tasks build on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
from datetime import UTC, datetime


def test_activities_in_range_inclusive_bounds(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T07:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=2, start_date="2024-04-15T08:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=3, start_date="2024-04-15T09:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=42,
        start_utc=datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 9, 0, 0, tzinfo=UTC),
    )

    ids = [a.activity_id for a in rows]
    assert ids == [1, 2, 3]


def test_activities_in_range_excludes_outside_window(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T06:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=2, start_date="2024-04-15T10:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=42,
        start_utc=datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 9, 0, 0, tzinfo=UTC),
    )

    assert rows == []


def test_activities_in_range_scopes_by_athlete(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=1,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T08:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=2,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T08:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=1,
        start_utc=datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 9, 0, 0, tzinfo=UTC),
    )

    assert [a.athlete_id for a in rows] == [1]


def test_activities_in_range_orders_by_start_date(session: Session) -> None:
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=1, start_date="2024-04-15T09:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=2, start_date="2024-04-15T07:00:00Z"),
    )
    store.upsert_activity(
        session,
        athlete_id=42,
        activity=_summary_activity(activity_id=3, start_date="2024-04-15T08:00:00Z"),
    )
    session.commit()

    rows = store.activities_in_range(
        session,
        athlete_id=42,
        start_utc=datetime(2024, 4, 15, 6, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 4, 15, 10, 0, 0, tzinfo=UTC),
    )

    assert [a.activity_id for a in rows] == [2, 3, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k activities_in_range`

Expected: FAIL with `AttributeError: module 'pbinator.store' has no attribute 'activities_in_range'`.

- [ ] **Step 3: Implement `activities_in_range` in `store.py`**

Add at the bottom of `src/pbinator/store.py`:

```python
def activities_in_range(
    session: Session,
    *,
    athlete_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> list[Activity]:
    """Return activities for ``athlete_id`` whose start_date lies in ``[start_utc, end_utc]``.

    Both bounds are inclusive. Results are ordered by ``start_date`` ascending.
    The bounds are formatted as Strava-style ISO-UTC strings (``...Z``) so the
    comparison matches the lexical encoding of the stored ``start_date`` column.

    Returns:
        A list of ``Activity`` rows; empty if nothing falls in the window.
    """
    lo = start_utc.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = end_utc.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = session.execute(
        select(Activity)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= lo,  # ty: ignore[invalid-argument-type]
            Activity.start_date <= hi,  # ty: ignore[invalid-argument-type]
        )
        .order_by(Activity.start_date)
    )
    return list(result.scalars().all())
```

The two `# ty: ignore[invalid-argument-type]` comments mirror the pattern used by `delete_activities_not_in` and `mark_detail_fetched` higher in this file: ty types `Activity.start_date` as `str` even though SQLAlchemy comparisons return column expressions.

- [ ] **Step 4: Run the full store suite**

Run: `uv run pytest tests/test_store.py -v`

Expected: all tests pass, including the four new `test_activities_in_range_*`.

- [ ] **Step 5: Run `just check`**

Run: `just check`

Expected: all green (lint, format-check, typecheck, full pytest with 100% branch coverage).

- [ ] **Step 6: Commit**

```bash
git add src/pbinator/store.py tests/test_store.py
git commit -m "feat(store): add activities_in_range"
```

---

## Task 2: Add `whoop_csv_path` to `Settings`

**Files:**
- Modify: `src/pbinator/settings.py`
- Test: `tests/test_settings.py`

**Why this task is next:** Trivial config change with no downstream consumer yet. Lands the field so Task 5 can read it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py`:

```python
def test_whoop_csv_path_defaults_to_data_workouts_csv(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")

    s = isolated_settings_cls()  # ty: ignore[missing-argument]

    assert s.whoop_csv_path == Path("data/workouts.csv")


def test_whoop_csv_path_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("WHOOP_CSV_PATH", "/tmp/custom-whoop.csv")  # noqa: S108 — test fixture path

    s = isolated_settings_cls()  # ty: ignore[missing-argument]

    assert s.whoop_csv_path == Path("/tmp/custom-whoop.csv")  # noqa: S108 — same as above
```

Then add `monkeypatch.delenv("WHOOP_CSV_PATH", raising=False)` to the `isolated_settings_cls` fixture's deletion list (next to the other `delenv` calls), so the test environment doesn't leak.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_settings.py -v -k whoop`

Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'whoop_csv_path'`.

- [ ] **Step 3: Add the field to `Settings`**

Edit `src/pbinator/settings.py`:

```python
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    strava_client_id: str
    strava_client_secret: SecretStr
    strava_redirect_uri: str = "http://localhost:8501/"
    pbinator_db_path: Path = Path("data/pbinator.db")
    whoop_csv_path: Path = Path("data/workouts.csv")
```

- [ ] **Step 4: Run the settings suite**

Run: `uv run pytest tests/test_settings.py -v`

Expected: all tests pass, including the two new `test_whoop_csv_path_*`.

- [ ] **Step 5: Run `just check`**

Run: `just check`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/pbinator/settings.py tests/test_settings.py
git commit -m "feat(settings): add whoop_csv_path"
```

---

## Task 3: Add `whoop.py` (CSV parser)

**Files:**
- Create: `src/pbinator/whoop.py`
- Create: `tests/test_whoop.py`

**Why this task is next:** Pure logic, stdlib only. Independent of `compare.py`. Tests can use inline CSV strings.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_whoop.py`:

```python
from datetime import UTC, datetime, timedelta, timezone

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
        "2024-04-15 06:00:00,,UTCZ,"
        ",,,Running,,",
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
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTCZ,"
        "2024-04-15 07:00:00,,30,Running,15.0,500.0"
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
        w.activity_name = "Walking"  # ty: ignore[possibly-unbound-attribute]


def test_offset_with_minutes_handled() -> None:
    text = _csv(
        "2024-04-15 06:00:00,2024-04-16 06:00:00,UTC+05:30,"
        "2024-04-15 12:30:00,2024-04-15 13:00:00,30,Running,15.0,500.0"
    )

    workouts = parse_workouts(text)

    assert workouts[0].start_utc == datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_whoop.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'pbinator.whoop'`.

- [ ] **Step 3: Implement `whoop.py`**

Create `src/pbinator/whoop.py`:

```python
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
    match = _TZ_RE.match(tz_str)
    if match is None:
        msg = f"unparsable timezone: {tz_str!r}"
        raise WhoopParseError(line_no, msg)
    if match.group(1) == "Z":
        return UTC
    sign = 1 if match.group(2) == "+" else -1
    hours = int(match.group(3))
    minutes = int(match.group(4))
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
```

- [ ] **Step 4: Run the new test file**

Run: `uv run pytest tests/test_whoop.py -v`

Expected: all 14 tests pass.

- [ ] **Step 5: Run `just check`**

Run: `just check`

Expected: all green (lint, format-check, typecheck, full pytest with 100% branch coverage).

- [ ] **Step 6: Commit**

```bash
git add src/pbinator/whoop.py tests/test_whoop.py
git commit -m "feat(whoop): add CSV parser"
```

---

## Task 4: Add `compare.py` (Whoop ↔ Strava comparator)

**Files:**
- Create: `src/pbinator/compare.py`
- Create: `tests/test_compare.py`

**Why this task is next:** Pure logic, depends only on `whoop.WhoopWorkout` and `models.Activity`. No DB needed in tests — `Activity` instances can be constructed directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compare.py`:

```python
from datetime import UTC, datetime, timedelta

from pbinator.compare import (
    MISMATCH_TOLERANCE_S,
    PAIRING_WINDOW_S,
    TimeMismatch,
    WhoopComparison,
    WhoopOnly,
    compare,
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
    w2 = _whoop(start=whoop_start)                              # clean

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
    import pytest

    w = _whoop()
    only = WhoopOnly(whoop=w, reason="no_strava_match")
    with pytest.raises((AttributeError, TypeError)):
        only.reason = "unmapped_sport"  # ty: ignore[possibly-unbound-attribute]

    comp = WhoopComparison(mismatches=[], whoop_only=[])
    with pytest.raises((AttributeError, TypeError)):
        comp.mismatches = [1]  # ty: ignore[possibly-unbound-attribute]


def test_thresholds_are_documented_values() -> None:
    assert PAIRING_WINDOW_S == 600
    assert MISMATCH_TOLERANCE_S == 120


def test_mismatch_dataclass_fields_present() -> None:
    """Smoke test that all spec fields exist on TimeMismatch."""
    whoop_start = datetime(2024, 4, 15, 7, 0, 0, tzinfo=UTC)
    drift = MISMATCH_TOLERANCE_S + 1
    a = _strava(start=whoop_start + timedelta(seconds=drift))

    result = compare([_whoop(start=whoop_start)], [a])
    m = result.mismatches[0]

    assert isinstance(m, TimeMismatch)
    assert m.whoop is not None
    assert isinstance(m.flagged_start, bool)
    assert isinstance(m.flagged_end, bool)
    assert isinstance(m.delta_start_s, int)
    assert isinstance(m.delta_end_s, int)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compare.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'pbinator.compare'`.

- [ ] **Step 3: Implement `compare.py`**

Create `src/pbinator/compare.py`:

```python
"""Whoop ↔ Strava pairing and classification.

Pure logic: takes parsed Whoop rows and Strava ``Activity`` rows, returns
a ``WhoopComparison``. No I/O, no clock reads, no DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
    delta_start_s: int  # signed: strava − whoop
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

        candidates.sort(key=lambda c: (c[0], c[1]))
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
```

- [ ] **Step 4: Run the new test file**

Run: `uv run pytest tests/test_compare.py -v`

Expected: all tests pass.

- [ ] **Step 5: Run `just check`**

Run: `just check`

Expected: all green (lint, format-check, typecheck, full pytest with 100% branch coverage).

- [ ] **Step 6: Commit**

```bash
git add src/pbinator/compare.py tests/test_compare.py
git commit -m "feat(compare): add Whoop↔Strava comparator"
```

---

## Task 5: Add the Whoop tab in `app.py`

**Files:**
- Modify: `src/pbinator/app.py` (extend `_render_logged_in`, add `_render_whoop_tab`)

**No tests:** `app.py` is excluded from coverage by `pyproject.toml:176`. The behaviour is exercised manually via `just run`. Pure logic that the tab depends on (parsing, comparison, store helper) is fully covered by Tasks 1–4.

**Why this task is last:** It's the integration point. All its dependencies (`whoop`, `compare`, `store.activities_in_range`, `Settings.whoop_csv_path`) exist by now.

- [ ] **Step 1: Read the current `app.py` to confirm the integration point**

Run: `uv run python -c "import pbinator.app"` to confirm imports still resolve before editing.

Expected: clean exit (Streamlit may emit `RuntimeError: no Streamlit runtime` from `main()` since it executes at import — that's pre-existing, ignore it; the import itself succeeds).

Actually, simpler: just open `src/pbinator/app.py` and confirm the structure of `_render_logged_in` (the function this task modifies). Do not run the import command above.

- [ ] **Step 2: Add the import block**

In `src/pbinator/app.py`, locate the existing imports near the top and add `compare` and `whoop` to the `pbinator` import group. Replace:

```python
from pbinator import pbs, store, sync
```

with:

```python
from datetime import timedelta as _timedelta  # already imported in this file as `timedelta`
```

Wait — `timedelta` is already imported on line 7 (`from datetime import UTC, datetime, timedelta`). Don't re-import it.

Replace the line:

```python
from pbinator import pbs, store, sync
```

with:

```python
from pbinator import compare, pbs, store, sync, whoop
```

- [ ] **Step 3: Add the `_render_whoop_tab` function**

Add this function below `_render_pbs_tab` and above `_render_logged_in` in `src/pbinator/app.py`:

```python
def _render_whoop_tab(session: Session, athlete_id: int, settings: Settings) -> None:
    """Render the Whoop comparison tab body."""
    uploaded = st.file_uploader("Replace Whoop CSV for this session", type=["csv"])
    if uploaded is not None:
        text = uploaded.getvalue().decode("utf-8")
    elif settings.whoop_csv_path.exists():
        text = settings.whoop_csv_path.read_text(encoding="utf-8")
    else:
        st.info("Place your Whoop export at data/workouts.csv or upload one above.")
        return

    try:
        workouts = whoop.parse_workouts(text)
    except whoop.WhoopParseError as exc:
        st.error(f"Could not parse Whoop CSV at line {exc.line_no}: {exc.reason}")
        return

    if not workouts:
        st.write("No Whoop workouts in this file.")
        return

    pad = timedelta(seconds=compare.PAIRING_WINDOW_S)
    lo = min(w.start_utc for w in workouts) - pad
    hi = max(w.start_utc for w in workouts) + pad
    activities = store.activities_in_range(
        session, athlete_id=athlete_id, start_utc=lo, end_utc=hi
    )
    result = compare.compare(workouts, activities)

    st.write(
        f"Compared **{len(workouts)}** Whoop workouts against Strava — "
        f"**{len(result.mismatches)}** time-mismatches, "
        f"**{len(result.whoop_only)}** Whoop-only."
    )

    st.subheader("Time mismatches")
    if not result.mismatches:
        st.success("No time mismatches.")
    else:
        rows_m = [
            {
                "Whoop start (UTC)": m.whoop.start_utc.strftime("%Y-%m-%d %H:%M"),
                "Sport": m.whoop.activity_name,
                "Δ start": _format_signed_delta(m.delta_start_s),
                "Δ end": _format_signed_delta(m.delta_end_s),
                "Strava": f"https://www.strava.com/activities/{m.strava_activity_id}",
            }
            for m in sorted(result.mismatches, key=lambda x: x.whoop.start_utc, reverse=True)
        ]
        st.dataframe(
            rows_m,
            width="stretch",
            column_config={"Strava": st.column_config.LinkColumn("Strava", display_text="open")},
        )

    st.subheader("Whoop-only")
    if not result.whoop_only:
        st.success("Every Whoop workout has a Strava match.")
    else:
        reason_label = {"no_strava_match": "No Strava match", "unmapped_sport": "Unmapped sport"}
        rows_o = [
            {
                "Whoop start (UTC)": o.whoop.start_utc.strftime("%Y-%m-%d %H:%M"),
                "Sport": o.whoop.activity_name,
                "Duration (min)": o.whoop.duration_min,
                "Reason": reason_label[o.reason],
            }
            for o in sorted(result.whoop_only, key=lambda x: x.whoop.start_utc, reverse=True)
        ]
        st.dataframe(rows_o, width="stretch")


def _format_signed_delta(seconds: int) -> str:
    """Format a signed second-count as ``±Mm SSs`` or ``±Ss`` for ``|Δ| < 60``."""
    if seconds == 0:
        return "0s"
    sign = "+" if seconds > 0 else "-"
    magnitude = abs(seconds)
    minutes, remainder = divmod(magnitude, 60)
    if minutes == 0:
        return f"{sign}{remainder}s"
    return f"{sign}{minutes}m {remainder:02d}s"
```

- [ ] **Step 4: Wire the third tab into `_render_logged_in`**

Replace the `_render_logged_in` body that currently reads:

```python
    engine = _get_engine(str(settings.pbinator_db_path))
    with Session(engine) as session:
        tab_sync, tab_pbs = st.tabs(["Sync", "PBs"])
        with tab_sync:
            _render_sync_tab(token, settings, session, controller)
        with tab_pbs:
            _render_pbs_tab(session, token.athlete_id)
```

with:

```python
    engine = _get_engine(str(settings.pbinator_db_path))
    with Session(engine) as session:
        tab_sync, tab_pbs, tab_whoop = st.tabs(["Sync", "PBs", "Whoop"])
        with tab_sync:
            _render_sync_tab(token, settings, session, controller)
        with tab_pbs:
            _render_pbs_tab(session, token.athlete_id)
        with tab_whoop:
            _render_whoop_tab(session, token.athlete_id, settings)
```

- [ ] **Step 5: Run `just check`**

Run: `just check`

Expected: all green. `app.py` is excluded from coverage so the new code does not need direct test exercise; lint/typecheck/format must still pass on it.

If ruff complains about a missing docstring on `_format_signed_delta` or `_render_whoop_tab`, the docstrings included above satisfy `D` rules (which are off at module/class scope but on for some function-level rules); both are present.

If ty complains about `compare` or `whoop` imports being unused at type-check time, recheck Step 2 — they must be in the runtime `from pbinator import …` line, not under `if TYPE_CHECKING:`.

- [ ] **Step 6: Manual smoke test**

Run: `just run`

In the browser:

1. Sign in if not already (existing flow).
2. Click the **Whoop** tab.
3. Verify the page loads:
   - With `data/workouts.csv` present (it is, in this repo): the summary line shows non-zero workouts, and at least one of the two sections renders.
   - Click the file uploader and upload the same `data/workouts.csv`: the page re-renders with the same content (uploader override path exercised).
4. Verify a Strava-link cell in the mismatches table opens the corresponding `https://www.strava.com/activities/<id>` URL when clicked.
5. Stop the server with `Ctrl+C`.

Expected: tab renders, summary counts present, no traceback in the terminal.

- [ ] **Step 7: Commit**

```bash
git add src/pbinator/app.py
git commit -m "feat(app): add Whoop tab"
```

---

## Self-review notes

The five tasks together cover every section of the spec:

| Spec section | Task |
|---|---|
| Goal — flag time-mismatches and Whoop-only | T4 (compare), T5 (UI) |
| Decisions — pairing/mismatch thresholds | T4 (`PAIRING_WINDOW_S`, `MISMATCH_TOLERANCE_S`) |
| Decisions — file source (static + uploader) | T2 (path), T5 (uploader override) |
| Architecture — module split | T1–T5 (each task creates/modifies the listed file) |
| Data shapes — `WhoopWorkout` | T3 |
| Data shapes — `TimeMismatch`, `WhoopOnly`, `WhoopComparison` | T4 |
| Sport mapping | T4 (`SPORT_MAP`) |
| CSV parsing | T3 |
| Pairing algorithm | T4 |
| Store helper (`activities_in_range`) | T1 |
| Settings (`whoop_csv_path`) | T2 |
| UI | T5 |
| Error handling | T3 (`WhoopParseError`), T5 (`st.error` / `st.info`) |
| Testing | T1, T2, T3, T4 (all behaviours covered) |
| Out of scope | (deliberately not implemented) |

No placeholders. Type names and function signatures are consistent across tasks (`WhoopWorkout`, `Activity`, `compare()`, `parse_workouts()`, `activities_in_range()`).
