# Whoop-only activity filter — design

**Date:** 2026-05-02
**Status:** Approved (brainstorming complete; awaiting implementation plan)

Builds on `2026-05-02-whoop-strava-compare-design.md`. Lands on the same feature branch (`feat/whoop-strava-compare`).

## Goal

Add a multi-select dropdown above the **Whoop-only** table in the Whoop tab so the user can narrow the table to one or more Whoop `activity_name` values (e.g. show only `Pilates` and `Activity`). The Time-mismatches table is untouched.

## Decisions

| Decision | Choice |
|---|---|
| Widget | `st.multiselect` |
| Placement | Inside the Whoop-only section, between `st.subheader("Whoop-only")` and the `st.dataframe` |
| Visibility | Only rendered when `result.whoop_only` is non-empty (the existing empty-state `st.success` wins otherwise) |
| Options | Sorted unique `activity_name` values present in `result.whoop_only` |
| Default selection | All options selected (filter is a no-op until the user touches it) |
| Filter scope | Local to the Whoop-only table only — does NOT update the summary count at the top of the tab |
| All-deselected state | Render an empty dataframe; no extra caption (Streamlit's natural behaviour) |
| Sort order | Newest-first by `whoop.start_utc`, same as today |

## Implementation sketch

In `_render_whoop_tab`, inside the existing `else` branch of the Whoop-only section:

```python
options = sorted({o.whoop.activity_name for o in result.whoop_only})
selected = st.multiselect("Filter by activity", options, default=options)
filtered = [o for o in result.whoop_only if o.whoop.activity_name in selected]
rows_o = [
    {
        "Whoop start (UTC)": o.whoop.start_utc.strftime("%Y-%m-%d %H:%M"),
        "Sport": o.whoop.activity_name,
        "Duration (min)": o.whoop.duration_min,
        "Reason": reason_label[o.reason],
    }
    for o in sorted(filtered, key=lambda x: x.whoop.start_utc, reverse=True)
]
st.dataframe(rows_o, width="stretch")
```

The `lambda` sort key matches what `_render_whoop_tab` already uses for both result lists today.

## Out of scope

- Filter on the Time-mismatches table.
- Filter by `Reason` (`No Strava match` / `Unmapped sport`).
- Persisting the filter selection across reruns / sessions.
- Updating the headline summary count to reflect the filter.

## Testing

`app.py` is coverage-excluded. The filter is one list comprehension; no unit test is warranted. Verified manually via `just run`.

## Commit plan

One commit: `feat(app): filter Whoop-only table by activity`.
