# pbinator

A small Streamlit app that signs in with Strava.

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

Once logged in, click **Sync activities** to populate
`data/pbinator.db` with your Strava history. The first sync paginates
your full history; subsequent syncs only fetch new activities. The app
tracks Strava's read rate limit and will stop early if it gets close —
just click **Sync activities** again later to resume. Use **Full
rescan** (with the confirmation checkbox) to re-fetch everything and
reconcile any activities you've deleted on Strava.

## Development

`just check` runs lint, format-check, type-check, and tests with 100% branch
coverage.
