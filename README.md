# pbinator

A small Streamlit app that signs in with Strava, surfaces your running personal
bests across the eleven standard race distances, and reconciles your activities
against Whoop and Garmin bulk-export CSVs.

- **[Personal bests](#personal-bests)** — every date you broke a PB, one column per distance.
- **[Whoop comparison](#whoop-comparison)** — find workouts Whoop recorded that never reached Strava.
- **[Garmin comparison](#garmin-comparison)** — find where Strava's copy has drifted from Garmin's.

Everything runs locally against your own Strava API keys. Nothing is uploaded
anywhere; your data stays in `data/pbinator.db`.

---

## Setup

Requires **Python 3.14** and [uv](https://docs.astral.sh/uv/) — `uv` will fetch
the right interpreter for you.

1. **Install dependencies** (creates `.venv`):

   ```sh
   uv sync
   ```

2. **Register a Strava API application** at <https://www.strava.com/settings/api>.
   Set **Authorization Callback Domain** to `localhost`.

3. **Configure credentials.** Copy the example env file and fill in
   `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` from your Strava app:

   ```sh
   cp .env.example .env
   ```

   `.env.example` lists every setting, including optional paths for the database
   and the two CSVs. Unknown keys are rejected, so a typo fails loudly at startup
   rather than being silently ignored.

## Running

```sh
just run
```

This launches the app at <http://localhost:8501/>. Click **Authorize with
Strava** and approve on the consent screen; you should return logged in as your
athlete. The session persists in a browser cookie for 90 days, and the access
token is refreshed automatically before it expires.

The logged-in view has four tabs — **Sync**, **PBs**, **Whoop**, **Garmin** —
covered below.

---

## Syncing your history

The **Sync** tab populates `data/pbinator.db` from your Strava history. This is
the foundation for everything else, so run it first.

| Button | What it does |
| --- | --- |
| **Sync activities** | First run paginates your entire history; later runs fetch only what's new. |
| **Full rescan** | Re-fetches everything and reconciles activities you deleted on Strava. Behind a confirmation checkbox. |

For each **Run**, the sync also fetches that activity's `best_efforts` segments —
the raw material for the PB table.

**On rate limits.** Strava caps how much you can read in a window. The app tracks
this and stops early rather than getting cut off mid-flight. If it stops, the
result message tells you how many Runs are still awaiting detail — just click
**Sync activities** again later and it picks up exactly where it left off.

## Personal bests

The **PBs** tab shows a table of **every date you broke a personal best**, newest
first, with one column per distance:

> 400m · ½mi · 1km · 1mi · 2mi · 5km · 10km · 15km · 10mi · Half · Marathon

Reading a row:

- **Highlighted cell** — you set a new PB at that distance on that date.
- **Plain cell** — your running best at that distance *as of* that date.
- **Em-dash (—)** — you hadn't set a PB at that distance yet.

While the best-effort backfill is still running, a caption tells you how many
Runs are still awaiting detail.

---

## Whoop comparison

The **Whoop** tab compares a Whoop bulk-export CSV against your Strava
activities, treating **Strava as the source of truth**. It answers: *what did
Whoop record that Strava doesn't know about?*

Reads `data/workouts.csv`.

> [!WARNING]
> The file uploader **overwrites that file on disk**. The upload is saved, not
> scoped to your session, so it replaces any CSV already there.

**How activities are paired:** sport-aware, within **±10 minutes** of each other.

Two tables render the result:

- **Time mismatches** — paired activities whose start *or* end time differ by
  more than **±2 minutes**, with a clickable Strava link.
- **Whoop-only** — Whoop rows with no Strava match, *or* whose sport doesn't map
  to a Strava `sport_type` at all (e.g. `Pilates`, `Activity`). A **Filter by
  activity** multiselect narrows it down.

**Required CSV columns:** `Cycle timezone`, `Workout start time`,
`Workout end time`, `Duration (min)`, `Activity name`.

Distance is not compared — the Whoop export doesn't carry it.

---

## Garmin comparison

The **Garmin** tab compares a Garmin Connect bulk-export CSV against your Strava
activities, treating **Garmin as the upstream source of truth**. Strava
auto-syncs *from* Garmin, so any disagreement is drift Strava introduced.

Reads `data/Activities.csv`.

> [!WARNING]
> As on the Whoop tab, the file uploader **overwrites that file on disk**.

**How activities are paired:** sport-agnostic, within **±60 seconds** on
`start_date_local`. Sport is deliberately *not* a pairing key — a sport
disagreement becomes a flagged field rather than a missing pair.

### Fields compared

| Field | Tolerance |
| --- | --- |
| Activity Type | exact match |
| Title | exact match |
| Start time | 2 s |
| Distance | 10 m |
| Moving time | 10 s |
| Elapsed time | **5 s** — widens to **25 s in September** (see below) |
| Calories | 1 |
| Avg HR | 1 bpm |

**Why September is special.** September activities show a consistent 13–21 s
elapsed-time drift between Garmin and Strava, which looks like a one-off
platform-side change rather than genuine sync drift. The tolerance widens for
that month only; 5 s everywhere else still surfaces real problems.

### Fields deliberately *not* compared

Cadence, power, elevation, and max HR. Garmin and Strava apply different
smoothing, averaging, and correction algorithms to the same raw streams, so they
disagree on essentially every paired activity:

| Field | Typical divergence |
| --- | --- |
| Power | 2–20 W |
| Cadence | 0–11 spm |
| Elevation | 1–17 m |
| Max HR | 2–10 bpm |

That's systematic algorithmic divergence, not sync drift. Comparing them would
flag everything and therefore tell you nothing.

### Results

- **Field mismatches** — one row per disagreeing field on a paired activity: the
  Garmin value, the Strava value, the signed delta, and a clickable Strava link.
  A **Field** selectbox filters to one field (e.g. show only `title` drift).

  Pairs carrying *default-generated* names on **both** sides are skipped
  entirely — Strava's `Morning`/`Lunch`/`Afternoon`/`Evening`/`Night Run` **and**
  Garmin's `<location> Running`. Their disagreements are noise, not drift.

- **Garmin-only** — Garmin rows with no Strava counterpart in the ±60 s window.
  A sync from Garmin to Strava that never landed.

- **Strava-only** — Strava activities inside the Garmin export's date range with
  no Garmin counterpart. Manual entries, or a sync from another device that
  bypassed Garmin.

**Required CSV columns:** `Activity Type`, `Date`, `Title`, `Distance`,
`Calories`, `Time`, `Avg HR`, `Elapsed Time`.

Cells rendered as `--` parse to *absent*, and that field is skipped for that pair
— absent is not the same as mismatched.

---

## Development

```sh
just check   # lint + format-check + type-check + tests
just         # list every command
```

Coverage is enforced at **100% branch coverage** over the logic modules.
`app.py` — the Streamlit and OAuth glue — is excluded and exercised by hand.
