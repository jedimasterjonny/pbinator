# Whoop-only activity filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-select dropdown above the Whoop-only table that narrows the rendered rows to one or more selected Whoop `activity_name` values.

**Architecture:** One-line addition to `_render_whoop_tab` in `src/pbinator/app.py`. No new modules. No tests (`app.py` is coverage-excluded; the change is a single list comprehension and a Streamlit widget call). Verified by manual smoke test.

**Tech Stack:** Streamlit (`st.multiselect`).

**Spec:** `docs/superpowers/specs/2026-05-02-whoop-only-filter-design.md`.

---

## Conventions

- Run `just check` (lint + format-check + typecheck + test) before committing.
- Conventional commits required.
- Stage by named path; never `git add -A`.
- Pre-commit hooks must NOT be bypassed.

## File map

**Modified:**
- `src/pbinator/app.py` — extend the `else` branch of the Whoop-only section (currently lines 326–340) with an `st.multiselect` widget and a list-comprehension filter.

That is the entire scope.

---

## Task 1: Add multiselect filter to the Whoop-only section

**Files:**
- Modify: `src/pbinator/app.py` — the `else:` branch of `if not result.whoop_only:` inside `_render_whoop_tab`

- [ ] **Step 1: Replace the existing `else` block**

In `src/pbinator/app.py`, locate the Whoop-only section in `_render_whoop_tab`. The current code (around lines 326–340) reads:

```python
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
```

Replace the `else:` body so it inserts a multiselect and applies the resulting filter:

```python
    st.subheader("Whoop-only")
    if not result.whoop_only:
        st.success("Every Whoop workout has a Strava match.")
    else:
        reason_label = {"no_strava_match": "No Strava match", "unmapped_sport": "Unmapped sport"}
        options = sorted({o.whoop.activity_name for o in result.whoop_only})
        selected = st.multiselect("Filter by activity", options, default=options)
        rows_o = [
            {
                "Whoop start (UTC)": o.whoop.start_utc.strftime("%Y-%m-%d %H:%M"),
                "Sport": o.whoop.activity_name,
                "Duration (min)": o.whoop.duration_min,
                "Reason": reason_label[o.reason],
            }
            for o in sorted(result.whoop_only, key=lambda x: x.whoop.start_utc, reverse=True)
            if o.whoop.activity_name in selected
        ]
        st.dataframe(rows_o, width="stretch")
```

The two diffs from today's code are:
- Two new lines computing `options` (sorted unique activity names from the Whoop-only rows) and rendering `st.multiselect("Filter by activity", options, default=options)`.
- An `if o.whoop.activity_name in selected` filter at the tail of the existing list comprehension.

The Time-mismatches section is unchanged. The summary line at the top of the tab is unchanged.

- [ ] **Step 2: Run `just check`**

Run: `just check`

Expected: all green — ruff, ruff format-check, ty, pytest with 100% branch coverage on every module other than `app.py` (which is excluded by `pyproject.toml`).

If ruff complains about the function's mccabe complexity (the cap is 10, per `[tool.ruff.lint.mccabe]`), the cleanest fix is to extract a helper `_render_whoop_only_section(whoop_only: list[WhoopOnly]) -> None` and move the new multiselect + filter + dataframe rendering into it. `_render_whoop_tab` would then call `_render_whoop_only_section(result.whoop_only)` from the `else:` branch. Apply this refactor only if `just check` actually flags it.

- [ ] **Step 3: Commit**

```bash
git add src/pbinator/app.py
git commit -m "feat(app): filter Whoop-only table by activity"
```

---

## Self-review notes

| Spec section | Task |
|---|---|
| Goal — multi-select dropdown above Whoop-only table | T1 |
| Decisions table — `st.multiselect`, placement, options derivation, default-all-selected, filter-local-to-table, all-deselected → empty dataframe, sort unchanged | T1 |
| Implementation sketch | T1 (verbatim) |
| Out of scope (mismatches filter, reason filter, persistence, headline-count update) | Implicitly satisfied — none of those are touched |
| Testing — manual smoke only; `app.py` excluded from coverage | Manual smoke is run by the orchestrator after Step 2; not in the task |
| Commit plan — one commit | T1 Step 3 |

No placeholders. Type/method names match existing code.
