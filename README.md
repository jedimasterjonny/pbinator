# pbinator

A small Streamlit app that signs in with Strava, surfaces your running
personal bests across the ten standard race distances, and reconciles your
activities against a Whoop bulk-export CSV.

## Setup

1. Install dependencies (creates `.venv`):

   ```sh
   uv sync
   ```

2. Register a Strava API application at <https://www.strava.com/settings/api>.
   Set **Authorization Callback Domain** to `localhost`.

3. Copy `.env.example` to `.env` and fill in `STRAVA_CLIENT_ID` and
   `STRAVA_CLIENT_SECRET` from your Strava app:

   ```sh
   cp .env.example .env
   ```

## Running

```sh
just run
```

This launches the app at <http://localhost:8501/>. Click **Authorize with
Strava**, approve on the Strava consent screen, and you should return logged in
as your athlete. The session is persisted in a browser cookie for 90 days and
the access token is refreshed automatically before expiry.

The logged-in view has three tabs: **Sync**, **PBs**, and **Whoop**.

The **Sync** tab is where you click **Sync activities** to populate
`data/pbinator.db` with your Strava history. The first sync paginates
your full history; subsequent syncs only fetch new activities. For each
Run, the sync also fetches the activity's `best_efforts` segments
(400m, ½mi, 1km, 1mi, 2mi, 5km, 10km, 15km, Half-Marathon, Marathon).
The app tracks Strava's read rate limit and will stop early if it gets
close — just click **Sync activities** again later to resume. The
result message tells you how many Runs are still awaiting detail. Use
**Full rescan** (with the confirmation checkbox) to re-fetch everything
and reconcile any activities you've deleted on Strava.

The **PBs** tab shows a table of dates a personal best was broken
(newest first), with one column per distance. Cells where a PB was
broken on that date are highlighted; other cells show the running best
as of that date, or an em-dash if no PB has been set at that distance
yet. While backfill is still in progress, the tab shows a small caption
telling you how many Runs are awaiting detail.

The **Whoop** tab compares a Whoop bulk-export CSV against your Strava
activities, treating Strava as the source of truth. By default it reads
`data/workouts.csv`; a file uploader on the tab lets you swap in a
different CSV for the current session. Whoop and Strava activities are
paired sport-aware within ±10 minutes of each other; pairs whose start
or end time differ by more than ±2 minutes appear in the **Time
mismatches** table (with a clickable Strava link). Whoop rows with no
Strava match — or whose Whoop sport doesn't map to a Strava `sport_type`
(e.g. `Pilates`, `Activity`) — appear in the **Whoop-only** table,
which has a "Filter by activity" multiselect to narrow it down. The
Whoop CSV format must include `Cycle timezone`, `Workout start time`,
`Workout end time`, `Duration (min)`, and `Activity name`; distance is
not compared because the Whoop export does not carry it.

## Development

`just check` runs lint, format-check, type-check, and tests with 100% branch
coverage.
