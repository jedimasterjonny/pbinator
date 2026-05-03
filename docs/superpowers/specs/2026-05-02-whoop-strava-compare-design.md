# Whoop ↔ Strava comparison — design

**Date:** 2026-05-02
**Status:** Approved (brainstorming complete; awaiting implementation plan)

## Goal

A new tab in the app that compares my Whoop workouts against my Strava activities, treating Strava as the source of truth. Surface two things:

1. **Time mismatches** — paired Whoop/Strava activities whose start time or end time differ by more than a small tolerance.
2. **Whoop-only activities** — Whoop workouts with no plausible Strava match, or whose Whoop sport doesn't map to any Strava sport.

Distance comparison is out of scope: the Whoop CSV export does not include distance.

## Decisions

| Decision | Choice |
|---|---|
| Whoop file format | `data/workouts.csv` (Whoop bulk export) |
| Required CSV columns | `Cycle timezone`, `Workout start time`, `Workout end time`, `Duration (min)`, `Activity name` |
| File source | Static path `settings.whoop_csv_path` (default `data/workouts.csv`); a per-session uploader in the Whoop tab can override it for the current Streamlit run |
| Pairing window | ±10 min (sport-aware): Whoop and Strava must share a mapped `sport_type` AND start within 10 min of each other |
| Mismatch tolerance | ±2 min on start time and on end time |
| Sport scope | All Whoop rows. Sport-aware matching: unmapped Whoop sports are always emitted as Whoop-only |
| Comparison direction | Whoop → Strava only. Strava-only activities are not flagged. |
| Storage | None. Comparison runs on render; no Whoop data persisted to SQLite |
| UI placement | New tab `Whoop`, after `Sync` and `PBs` |
| Strava end-time | `start_date_utc + elapsed_time_s` (wall-clock duration matches Whoop's wall-clock end) |
| Pairing tie-break | Closest `|Δstart|`; if exactly tied, lower `activity_id` for determinism |
| Re-pairing of Strava activities | A Strava activity can be the chosen pair for multiple Whoop rows. Pairing is independent per Whoop row (we want signal, not bijection) |

## Architecture

Two new pure-logic modules, one new helper in `store.py`, a settings field, and a tab in `app.py`. No schema changes.

```
app.py
 ├─ tab "Sync"  → existing _render_sync_tab
 ├─ tab "PBs"   → existing _render_pbs_tab
 └─ tab "Whoop" → _render_whoop_tab(session, athlete_id, settings)
                   ├─ optional st.file_uploader; else read settings.whoop_csv_path
                   ├─ whoop.parse_workouts(text) → list[WhoopWorkout]
                   ├─ store.activities_in_range(session, athlete_id, lo, hi) → list[Activity]
                   ├─ compare.compare(workouts, activities) → WhoopComparison
                   └─ render summary + two st.dataframes

whoop.py  (new)
 ├─ class WhoopParseError(Exception)
 ├─ @dataclass WhoopWorkout
 └─ parse_workouts(text: str) → list[WhoopWorkout]

compare.py  (new)
 ├─ SPORT_MAP, PAIRING_WINDOW_S = 600, MISMATCH_TOLERANCE_S = 120
 ├─ @dataclass TimeMismatch
 ├─ @dataclass WhoopOnly
 ├─ @dataclass WhoopComparison
 └─ compare(workouts: Sequence[WhoopWorkout],
            activities: Sequence[Activity]) → WhoopComparison

store.py
 └─ NEW activities_in_range(session, *, athlete_id, start_utc, end_utc) → list[Activity]

settings.py
 └─ NEW whoop_csv_path: Path = Path("data/workouts.csv")
```

The comparator is pure: no clock reads, no DB reads, no filesystem. All I/O lives in `app.py`.

## Data shapes

```python
# whoop.py
@dataclass(frozen=True)
class WhoopWorkout:
    activity_name: str            # raw Whoop "Activity name"
    start_utc: datetime           # tz-aware UTC
    end_utc: datetime             # tz-aware UTC
    duration_min: int             # Whoop "Duration (min)"


# compare.py
@dataclass(frozen=True)
class TimeMismatch:
    whoop: WhoopWorkout
    strava_activity_id: int
    strava_sport_type: str
    strava_start_utc: datetime
    strava_end_utc: datetime       # = start_date + elapsed_time_s
    delta_start_s: int             # signed: strava − whoop, in seconds
    delta_end_s: int               # signed
    flagged_start: bool            # |delta_start_s| > MISMATCH_TOLERANCE_S
    flagged_end: bool              # |delta_end_s|   > MISMATCH_TOLERANCE_S


@dataclass(frozen=True)
class WhoopOnly:
    whoop: WhoopWorkout
    reason: str                    # "no_strava_match" | "unmapped_sport"


@dataclass(frozen=True)
class WhoopComparison:
    mismatches: list[TimeMismatch]   # paired rows where flagged_start or flagged_end
    whoop_only: list[WhoopOnly]
```

## Sport mapping

Defined as a module-level dict in `compare.py`. Storage uses Strava's `sport_type` strings verbatim.

| Whoop `Activity name` | Strava `sport_type` |
|---|---|
| `Running` | `Run` |
| `Walking` | `Walk` |
| `Cycling` | `Ride` |
| `Mountain Biking` | `MountainBikeRide` |
| `Swimming` | `Swim` |
| `Pilates` | `Pilates` |
| *anything else* | unmapped → `WhoopOnly(reason="unmapped_sport")` |

Unmapped rows are surfaced (not silently dropped) so a mistyped Whoop sport name shows up rather than disappearing.

## CSV parsing

`whoop.parse_workouts(text: str) -> list[WhoopWorkout]` uses `csv.DictReader` and:

- Reads only the columns named under "Required CSV columns" above. Unknown extra columns are ignored.
- Parses `Cycle timezone`:
  - `"UTCZ"` → offset 0
  - `"UTC±HH:MM"` → signed offset
  - anything else → `WhoopParseError(line_no, reason)`
- Parses `Workout start time` and `Workout end time` as naive `datetime` (`%Y-%m-%d %H:%M:%S`), then attaches the row's parsed offset and converts to UTC (`astimezone(UTC)`).
- Skips rows where `Workout start time` is blank (some Whoop exports include in-progress / cycle-only rows).
- Raises `WhoopParseError(line_no, reason)` on:
  - missing required column,
  - unparsable timestamp,
  - blank `Workout end time`, `Duration (min)`, or `Activity name` (when `Workout start time` is present),
  - unparsable timezone string,
  - unparsable `Duration (min)`.

Line numbers in errors are 1-indexed and refer to the data row (header is line 1).

## Pairing algorithm

`compare.compare(workouts, activities) -> WhoopComparison`. For each `WhoopWorkout`, in input order:

1. **Map sport.** Look up `whoop.activity_name` in `SPORT_MAP`. If absent → emit `WhoopOnly(reason="unmapped_sport")`, continue.
2. **Find candidates.** All `activities` whose `sport_type` equals the mapped Strava sport AND `|start_utc − whoop.start_utc| ≤ PAIRING_WINDOW_S`.
3. **No candidates** → emit `WhoopOnly(reason="no_strava_match")`, continue.
4. **One or more candidates** → choose the one with the smallest `|Δstart|`; if exactly tied, the lower `activity_id`.
5. **Compute deltas** against the chosen Strava activity:
   - `strava_start_utc = parse(activity.start_date)`
   - `strava_end_utc = strava_start_utc + timedelta(seconds=activity.elapsed_time_s)`
   - `delta_start_s = (strava_start_utc - whoop.start_utc).total_seconds()` (signed int)
   - `delta_end_s = (strava_end_utc - whoop.end_utc).total_seconds()` (signed int)
6. **Flag.** Compute `flagged_start = abs(delta_start_s) > MISMATCH_TOLERANCE_S` and `flagged_end = abs(delta_end_s) > MISMATCH_TOLERANCE_S`. If either is true, append a `TimeMismatch`. Otherwise the pair is clean and emits nothing.

The function is `O(W × A)` where `W` is the Whoop row count and `A` is the candidate Strava activity count. The caller pre-filters `A` via `store.activities_in_range`, so in practice this is a few hundred × a few hundred — negligible.

## Store helper

```python
def activities_in_range(
    session: Session,
    *,
    athlete_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> list[Activity]:
    """Return activities for athlete with start_date in [start_utc, end_utc] (inclusive)."""
```

Implemented via `select(Activity).where(...)` ordered by `start_date`. Used by `_render_whoop_tab` to bound the comparator's input.

## Settings

Add to `pbinator/settings.py`:

```python
whoop_csv_path: Path = Path("data/workouts.csv")
```

Loaded by pydantic-settings same as existing fields. Override via `WHOOP_CSV_PATH` env or `.env`.

## UI

New `_render_whoop_tab(session, athlete_id, settings)` in `app.py`. Body, in order:

1. **Source picker.**

   ```python
   uploaded = st.file_uploader("Replace Whoop CSV for this session", type=["csv"])
   if uploaded is not None:
       text = uploaded.getvalue().decode("utf-8")
   elif settings.whoop_csv_path.exists():
       text = settings.whoop_csv_path.read_text(encoding="utf-8")
   else:
       st.info("Place your Whoop export at data/workouts.csv or upload one above.")
       return
   ```

2. **Parse + compare.** Wrap the parse + comparison in `try/except WhoopParseError` and on error call `st.error(f"Could not parse Whoop CSV at line {e.line_no}: {e.reason}")` and return.

   ```python
   workouts = whoop.parse_workouts(text)
   if not workouts:
       st.write("No Whoop workouts in this file.")
       return
   lo = min(w.start_utc for w in workouts) - timedelta(seconds=compare.PAIRING_WINDOW_S)
   hi = max(w.start_utc for w in workouts) + timedelta(seconds=compare.PAIRING_WINDOW_S)
   activities = store.activities_in_range(
       session, athlete_id=athlete_id, start_utc=lo, end_utc=hi,
   )
   result = compare.compare(workouts, activities)
   ```

3. **Summary line.**

   ```python
   st.write(
       f"Compared **{len(workouts)}** Whoop workouts against Strava — "
       f"**{len(result.mismatches)}** time-mismatches, "
       f"**{len(result.whoop_only)}** Whoop-only."
   )
   ```

4. **Time mismatches section.** Rendered with `st.subheader("Time mismatches")`. Empty state: `st.success("No time mismatches.")`. Otherwise an `st.dataframe` with columns:
   - **Whoop start (UTC)** — formatted `YYYY-MM-DD HH:MM`
   - **Sport** — Whoop `activity_name`
   - **Δ start** — signed, formatted via `_format_signed_delta`; bold/red when `flagged_start`
   - **Δ end** — same; bold/red when `flagged_end`
   - **Strava** — `LinkColumn` to `https://www.strava.com/activities/{strava_activity_id}`

   Sorted newest-first by Whoop start.

5. **Whoop-only section.** `st.subheader("Whoop-only")`. Empty state: `st.success("Every Whoop workout has a Strava match.")`. Otherwise a `st.dataframe` with columns: **Whoop start (UTC)**, **Sport**, **Duration (min)**, **Reason** (`"No Strava match"` / `"Unmapped sport"`).

   Sorted newest-first.

`_format_signed_delta(seconds: int) -> str`:

- `0` → `"0s"`
- positive → `f"+{m}m {s:02d}s"` (or `f"+{s}s"` if `m == 0`)
- negative → mirror with `-`
- magnitudes ≥ 1 hour are still rendered in minutes (no hours bucket; deltas this large are degenerate but readable)

The PB tab uses `format_time` from `pbs.py` for `m:ss` durations; the comparison delta format is distinct (signed, with units) and lives in `compare.py` rather than `pbs.py`.

## Error handling

| Condition | Behaviour |
|---|---|
| Static path missing AND no upload | `st.info("Place your Whoop export at data/workouts.csv or upload one above.")` and return |
| Malformed CSV row | `WhoopParseError(line_no, reason)` → caught in app → `st.error` with line + reason → return |
| Empty Whoop file (header only) | `st.write("No Whoop workouts in this file.")` and return |
| Strava DB has no activities for this athlete | Every mapped Whoop row → `WhoopOnly(reason="no_strava_match")`. UI renders normally. |
| Whoop row's `Workout start time` is blank | Silently skipped during parse |
| Whoop row's other required field is blank when start is present | `WhoopParseError` |
| Logged out | Tab is inside `_render_logged_in`; not reachable |

## Testing

100% branch coverage is preserved. `pytest-socket` blocks network. The Whoop tab rendering itself (in `app.py`) is excluded from coverage as today; the comparator and parser carry the test weight.

**`tests/test_whoop.py`** (new):

- Parse a small inline CSV exercising:
  - `UTCZ` and `UTC+01:00` rows;
  - DST boundary (offset changes mid-file → each row's offset is honoured);
  - blank `Workout start time` row → skipped silently;
  - missing required column → `WhoopParseError`;
  - unparsable `Cycle timezone` (e.g. `"PST"`) → `WhoopParseError`;
  - unparsable `Workout start time` → `WhoopParseError`;
  - blank `Workout end time` when start is present → `WhoopParseError`;
  - non-integer `Duration (min)` → `WhoopParseError`;
  - line numbers in `WhoopParseError` are 1-indexed and accurate.
- `WhoopWorkout` `start_utc`/`end_utc` are tz-aware UTC.

**`tests/test_compare.py`** (new):

- Build `WhoopWorkout`s and `Activity` objects directly (no DB):
  - paired clean (within ±2 min) → no `TimeMismatch`, no `WhoopOnly`;
  - paired Δstart > 2 min → `TimeMismatch` with `flagged_start=True`, `flagged_end` reflects end delta;
  - paired Δend > 2 min only → `flagged_end=True`, `flagged_start=False`;
  - two Strava candidates inside ±10 min → closer one chosen;
  - two candidates exactly tied on `|Δstart|` → lower `activity_id` wins;
  - candidate of wrong sport in window + correct-sport candidate outside window → `no_strava_match` (sport must match);
  - unmapped Whoop sport (`"Activity"`) → `WhoopOnly(reason="unmapped_sport")`;
  - no candidates → `WhoopOnly(reason="no_strava_match")`;
  - signed delta sign correctness (Strava later than Whoop → positive Δstart);
  - one Strava activity paired by two Whoop rows → both pairings emitted independently.

**`tests/test_store.py`** (extend):

- `activities_in_range`: inclusive bounds; out-of-range returns empty; scoped by `athlete_id`; ordered by `start_date`.

**No tests for `app.py`** — coverage exclusion is unchanged.

## Dependencies

None. `csv`, `dataclasses`, `datetime` are stdlib.

## Out of scope (deliberately)

- Persisting Whoop data in SQLite.
- Strava-only flagging (Strava activities with no Whoop counterpart).
- Distance comparison (Whoop CSV lacks distance).
- Bulk-edit / write actions from the UI.
- Caching/memoisation (`st.cache_data`); add only if perf bites.
- Configurable tolerances exposed in the UI; constants in code.

## Commit plan

Each commit serves a single purpose and leaves the tree green (`just check`).

1. `feat(store): add activities_in_range`
2. `feat(settings): add whoop_csv_path`
3. `feat(whoop): add CSV parser`
4. `feat(compare): add Whoop↔Strava comparator`
5. `feat(app): add Whoop tab`

Each step lands with its own tests; PRs may bundle related steps if the bundle still has one purpose.
