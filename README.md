# pbinator

A small Streamlit app that signs in with Strava and surfaces your running
personal bests across the ten standard race distances.

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

The logged-in view has two tabs: **Sync** and **PBs**.

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

## Development

`just check` runs lint, format-check, type-check, and tests with 100% branch
coverage.
