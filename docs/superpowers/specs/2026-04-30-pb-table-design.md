# PB table — design

**Date:** 2026-04-30
**Status:** Approved (brainstorming complete; awaiting implementation plan)

## Goal

A new tab in the app showing the user's running personal-best times. Each row is a date on which a PB was broken. Each column is a Strava `best_effort` distance. The cell whose distance was broken on that date is highlighted; other cells in the row show the running best at that distance as of the row's date. Rows are ordered newest first.

## Decisions

| Decision | Choice |
|---|---|
| Source of PBs | Strava's `best_efforts` array on the detailed activity payload |
| Distances | All ten Strava labels: `400m`, `1/2 mile`, `1k`, `1 mile`, `2 mile`, `5k`, `10k`, `15k`, `Half-Marathon`, `Marathon` |
| Sport types | `Run` only |
| Detail-fetch flow | Integrated into the existing `Sync activities` button (and `Full rescan`); rate-limit / resume machinery applies to detail fetches the same way as page fetches |
| Foreign keys | `PRAGMA foreign_keys = ON` at connect, so deleting an activity cascades to its best efforts |
| UI placement | `st.tabs(["Sync", "PBs"])` |
| Highlight style | Soft blue background `rgba(78, 161, 255, 0.18)` + bold text `#4ea1ff` |
| Cell content | Running best as of that row's date; `—` until the first PB at that distance |
| Tie behaviour | Equalling a PB does **not** break it (strict `<`) |
| Time format | `m:ss` if under one hour, `h:mm:ss` otherwise |
| Date format | `YYYY-MM-DD`, using the activity's `start_date_local` (date portion only) |
| Rendering | `pandas` Styler via `st.dataframe(styler, hide_index=True, use_container_width=True)`; new runtime dep |

## Architecture

Three new pure-logic modules, plus extensions to `store.py` and `sync.py`. The Streamlit glue in `app.py` gains a tab split but no new business logic. Coverage stays at 100%.

```
app.py
 ├─ tab "Sync"  → existing render path (extracted into _render_sync_tab)
 └─ tab "PBs"   → _render_pbs_tab
                    └─ pbs.compute_rows(conn, athlete_id)
                    └─ pbs.to_dataframe(rows) → (values_df, mask_df)
                    └─ st.dataframe(styler, hide_index=True, …)

sync.py
 └─ run / full_rescan
       ├─ existing list-page + upsert_activity loop
       └─ NEW: for each Run lacking best_efforts_fetched_at,
                best_efforts.fetch_detail → parse_best_efforts → store.upsert_best_efforts
              under the same rate-limit budget (would_exceed_next_call applies)

best_efforts.py  (new)
 ├─ fetch_detail(token, settings, activity_id) → dict
 └─ parse_best_efforts(detail_json) → list[BestEffortRow]

pbs.py  (new)
 ├─ DISTANCE_LABELS, DISPLAY_LABELS
 ├─ format_time(seconds: int) → str
 ├─ compute_rows(conn, athlete_id) → list[PbRow]
 └─ to_dataframe(rows) → tuple[pandas.DataFrame, pandas.DataFrame]

store.py
 ├─ schema: NEW best_effort table; NEW start_date_local + best_efforts_fetched_at on activity
 ├─ connect: PRAGMA foreign_keys = ON; one-time backfill of start_date_local from raw_json
 ├─ upsert_activity: also writes start_date_local
 ├─ NEW upsert_best_efforts(conn, *, athlete_id, activity_id, efforts)
 ├─ NEW count_runs_awaiting_detail(conn, athlete_id) → int
 └─ NEW mark_detail_fetched(conn, *, athlete_id, activity_id, fetched_at)
```

## Data model

New table:

```sql
CREATE TABLE IF NOT EXISTS best_effort (
    athlete_id     INTEGER NOT NULL,
    activity_id    INTEGER NOT NULL,
    distance_label TEXT    NOT NULL,    -- "5k", "Half-Marathon", as Strava names them
    distance_m     REAL    NOT NULL,    -- as reported by Strava (e.g. 804.672 for 1/2 mile)
    moving_time_s  INTEGER NOT NULL,    -- the "PB time"
    elapsed_time_s INTEGER NOT NULL,
    start_date     TEXT    NOT NULL,    -- ISO UTC of the segment within the run
    PRIMARY KEY (athlete_id, activity_id, distance_label),
    FOREIGN KEY (athlete_id, activity_id)
        REFERENCES activity(athlete_id, activity_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_best_effort_athlete_label_time
    ON best_effort (athlete_id, distance_label, moving_time_s);
```

Two new columns on the existing `activity` table:

```sql
ALTER TABLE activity ADD COLUMN start_date_local        TEXT;
ALTER TABLE activity ADD COLUMN best_efforts_fetched_at TEXT;
```

`start_date_local` is the activity's local start time (Strava reports it as a naive ISO string in the athlete's local timezone, alongside the UTC `start_date`). The PB table groups rows by *local* date, so this is what we sort and display by.

`best_efforts_fetched_at` is `NULL` until a detail fetch completes for that activity. It tells the sync flow which Runs still need detail, and feeds the `count_runs_awaiting_detail` hint shown in sync result messages.

**Backfill on schema upgrade.** Existing rows have `start_date_local IS NULL`. Since the value lives in `raw_json` already, the schema bootstrap also runs:

```sql
UPDATE activity
SET start_date_local = json_extract(raw_json, '$.start_date_local')
WHERE start_date_local IS NULL;
```

After bootstrap, `upsert_activity` writes `start_date_local` from the summary payload directly, so freshly synced rows don't depend on the json_extract path.

`store.connect` issues `PRAGMA foreign_keys = ON` immediately after opening the connection. This makes the cascade enforce automatically when `delete_activities_not_in` removes an activity during `Full rescan`.

The schema bootstrap runs idempotently: `CREATE TABLE IF NOT EXISTS` for the new table; for the new columns, check `PRAGMA table_info('activity')` before adding (avoids the second-run failure SQLite raises on duplicate columns).

## Sync flow change

Existing `sync.run` and `sync.full_rescan` continue to:

1. Page through `/athlete/activities` until exhausted, rate-limited, or up-to-date.
2. Upsert each summary into `activity`.

After step 2 for each activity on a page, **if** `sport_type == "Run"` **and** `best_efforts_fetched_at IS NULL`, the sync also:

3. Calls `would_exceed_next_call` with the current `RateLimitUsage`. If it would exceed, returns rate-limited as today, leaving the activity row in place with `best_efforts_fetched_at` still NULL — the next sync click resumes here.
4. Calls `best_efforts.fetch_detail(token, settings, activity_id)`.
5. On 401: returns `auth_failed`, same path as today.
6. On other HTTP errors: returns `http_error`, same path as today.
7. On 200: `parse_best_efforts(detail_json)` → `store.upsert_best_efforts(...)` → `store.mark_detail_fetched(...)`.

`fetch_detail` shares the headers/timeout pattern of the existing `activities_api.fetch_page`. It updates the same `RateLimitUsage` object so the budget tracking covers both endpoints.

`Full rescan` retains its current "list everything, then `delete_activities_not_in`" semantics. Cascading foreign keys remove orphaned `best_effort` rows automatically. Detail-fetching for surviving Runs proceeds the same as in regular sync.

## PB derivation

`pbs.compute_rows(conn, athlete_id)` is the single read path used by the UI.

**Step 1 — find PB-breaking events** with one query:

```sql
WITH ordered AS (
    SELECT
        be.distance_label,
        be.moving_time_s,
        SUBSTR(a.start_date_local, 1, 10)       AS local_date,
        a.start_date_local                      AS sort_key
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
ORDER BY local_date DESC, distance_label;
```

`start_date_local` is a naive ISO timestamp ordered the same way as wall-clock local time, so it sorts correctly within an athlete (athletes don't time-travel between timezones often enough to matter; ties on the same local timestamp are deterministic via `distance_label`).

**Step 2 — for each PB-breaking date `D`, fill non-broken cells.** A second query, parameterised by the set of dates:

```sql
SELECT distance_label, MIN(moving_time_s) AS best_so_far
FROM best_effort AS be
JOIN activity    AS a USING (athlete_id, activity_id)
WHERE be.athlete_id = :athlete_id
  AND SUBSTR(a.start_date_local, 1, 10) <= :date
GROUP BY distance_label;
```

Run once per unique PB-breaking date, results merged into `PbRow.cells`. (Acceptable: distinct PB dates are bounded by the number of breaks, typically dozens; per-call cost is microseconds at this size.)

**Step 3 — assemble.**

```python
@dataclass(frozen=True)
class PbCell:
    moving_time_s: int
    is_pb_break: bool

@dataclass(frozen=True)
class PbRow:
    date: str  # YYYY-MM-DD
    cells: dict[str, PbCell | None]  # key = Strava distance_label; None if no PB yet
```

`compute_rows` returns a `list[PbRow]` ordered newest first.

**`format_time(seconds: int) -> str`:**

- `seconds < 3600`: `f"{m}:{s:02d}"`
- `seconds >= 3600`: `f"{h}:{m:02d}:{s:02d}"`

**`to_dataframe(rows)`** returns:

- `values_df`: rows × ten Strava-labelled columns of formatted strings; em-dash `"—"` where the cell is `None`.
- `mask_df`: same shape, boolean — `True` where `cells[label].is_pb_break is True`.
- The index is the `date` strings; `hide_index=True` is used at render time, but the index is still useful for tests.
- Display labels (`5km`, `1mi`, `Half`, …) replace the storage labels in `values_df.columns`. The mask uses identical column names, so the Styler can index it positionally.

## UI

`app._render_logged_in` extracts the existing logged-in body into `_render_sync_tab(token, settings, conn, controller)` and adds:

```python
tab_sync, tab_pbs = st.tabs(["Sync", "PBs"])
with tab_sync:
    _render_sync_tab(token, settings, conn, controller)
with tab_pbs:
    _render_pbs_tab(conn, token.athlete_id)
```

`_render_pbs_tab`:

1. `rows = pbs.compute_rows(conn, athlete_id=athlete_id)`.
2. If `not rows`: `st.info("No PBs yet — click Sync activities, then come back.")` and return.
3. `values_df, mask_df = pbs.to_dataframe(rows)`.
4. Build a Styler:

   ```python
   PB_STYLE = "background-color: rgba(78, 161, 255, 0.18); color: #4ea1ff; font-weight: 700"
   styler = values_df.style.apply(
       lambda col: [PB_STYLE if mask_df.at[idx, col.name] else "" for idx in col.index],
       axis=0,
   )
   st.dataframe(styler, hide_index=True, use_container_width=True)
   ```

5. Show a small caption with backfill progress:

   ```python
   awaiting = store.count_runs_awaiting_detail(conn, athlete_id=athlete_id)
   if awaiting > 0:
       st.caption(f"{awaiting} Runs still awaiting detail — keep clicking Sync.")
   ```

`_render_sync_result` in `app.py` is updated so non-error results suffix the same hint. For example: `"Synced 42 new activities; 314 Runs awaiting detail."` Computed once via `count_runs_awaiting_detail`.

## Display labels

Cosmetic mapping applied at render time only. Storage uses Strava's labels verbatim.

| Strava label    | Display    |
|-----------------|------------|
| `400m`          | `400m`     |
| `1/2 mile`      | `½mi`      |
| `1k`            | `1km`      |
| `1 mile`        | `1mi`      |
| `2 mile`        | `2mi`      |
| `5k`            | `5km`      |
| `10k`           | `10km`     |
| `15k`           | `15km`     |
| `Half-Marathon` | `Half`     |
| `Marathon`      | `Marathon` |

## Testing

100% branch coverage is preserved. `pytest-socket` blocks network; all HTTP is mocked via `respx`.

**`tests/test_best_efforts.py`** (new):

- `parse_best_efforts` — full ten labels, partial set (short run), unknown labels filtered, missing required fields raise.
- `fetch_detail` — 200 → parsed payload + `RateLimitUsage` updated from headers; 401 → propagates an `httpx.HTTPStatusError` mirroring `fetch_page`; 429 → same; transport errors → propagate.

**`tests/test_pbs.py`** (new):

- `format_time` boundaries: 0, 59, 60, 599, 3599, 3600, 86399.
- `compute_rows`:
  - Empty DB → `[]`.
  - One break → one row, one highlight, `None` cells elsewhere.
  - Equalled but not bettered → not a row.
  - Three distances broken in one race on one date → one row, three highlights.
  - Multiple dates → newest first; running best fills earlier rows correctly.
  - Column order matches `DISTANCE_LABELS` regardless of insertion order.
- `to_dataframe`:
  - Shape, formatted strings, mask alignment, em-dashes.
  - Display labels applied; mask columns match value columns.
  - Empty input → empty `DataFrame`s with the canonical columns.

**`tests/test_store.py`** (extend):

- `upsert_best_efforts` round-trip + idempotency.
- `delete_activities_not_in` removes child `best_effort` rows via the foreign-key cascade.
- `count_runs_awaiting_detail` counts only `sport_type='Run'` rows where `best_efforts_fetched_at IS NULL`.
- `mark_detail_fetched` flips the flag.
- New schema applies idempotently on a database created by an older `connect`; running the bootstrap twice is a no-op.
- Schema upgrade backfills `start_date_local` from `raw_json` on existing rows; `upsert_activity` writes it directly for new rows.

**`tests/test_sync.py`** (extend):

- Sync interleaving: each Run on a page triggers exactly one detail fetch; non-Run sport types don't.
- Rate-limit budget exhausted mid-detail: returns rate-limited; `best_efforts_fetched_at` still NULL on the un-fetched activities; resumed sync completes them without duplicating page fetches.
- Detail fetch returns 401 → `auth_failed`; 5xx → `http_error`; both leave `best_efforts_fetched_at` NULL so retry is natural.
- A `Run` whose detail returns no `best_efforts` (e.g. a sub-400m run) → activity is marked `best_efforts_fetched_at` so we don't refetch forever.

**No tests for `app.py`** — coverage exclusion is unchanged.

## Dependencies

Add `pandas>=2.2` to `[project.dependencies]` via `uv add pandas`. No other deps.

## Out of scope (deliberately)

- Pace-per-distance display, mile/km toggles.
- Other sport types.
- Manual PB entry / corrections.
- Charts or progression plots.
- Configurable distance subset.

These are simple to add later on top of the storage shape proposed here.

## Commit plan

Each commit serves a single purpose and leaves the tree green (`just check`).

1. `feat(store): enable PRAGMA foreign_keys=ON at connect`
2. `feat(store): persist start_date_local on activity` (column + upsert + json_extract backfill)
3. `feat(store): add best_effort table and best_efforts_fetched_at column`
4. `feat(best_efforts): add fetch_detail and parse_best_efforts modules`
5. `feat(store): add upsert_best_efforts, mark_detail_fetched, count_runs_awaiting_detail`
6. `feat(sync): fetch best_efforts for each Run during sync` (the behavioural change)
7. `feat(app): show backfill-progress hint in sync result messages`
8. `chore(deps): add pandas`
9. `feat(pbs): add compute_rows, to_dataframe, format_time`
10. `feat(app): add PBs tab rendering the PB table`

Each step lands with its own tests; PRs may bundle related steps if the bundle still has one purpose.
