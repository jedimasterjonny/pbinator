"""Streamlit entry point. Excluded from coverage; logic lives in pbinator.strava."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import streamlit as st
from pydantic import ValidationError
from streamlit_cookies_controller import CookieController

from pbinator import pbs, store, sync
from pbinator.settings import Settings
from pbinator.strava import TokenPayload, build_authorize_url, exchange_code, refresh

if TYPE_CHECKING:
    import sqlite3

    from pbinator.activities_api import RateLimitUsage
    from pbinator.sync import SyncResult

_COOKIE_NAME = "pbinator_strava"
_OAUTH_STATE_COOKIE_NAME = "pbinator_oauth_state"
_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 90  # 90 days
# CSRF state must survive the external redirect to Strava, so it cannot live in
# st.session_state (which is bound to the Streamlit WebSocket session and dies
# when the browser navigates away). 10 minutes is enough for the consent screen.
_OAUTH_STATE_COOKIE_MAX_AGE_SECONDS = 60 * 10
_REFRESH_WINDOW_SECONDS = 60


def _load_settings() -> Settings | None:
    try:
        # pydantic-settings injects required fields from env vars; ty can't see this.
        return Settings()  # ty: ignore[missing-argument]
    except ValidationError:
        st.error("Configure STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env")
        st.stop()
        return None


def _read_cookie(controller: CookieController) -> TokenPayload | None:
    """Return a parsed TokenPayload from the cookie, or None on absent/corrupt."""
    raw = controller.get(_COOKIE_NAME)
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            return TokenPayload.model_validate_json(raw)
        return TokenPayload.model_validate(raw)
    except ValidationError:
        controller.remove(_COOKIE_NAME)
        return None


def _write_cookie(controller: CookieController, token: TokenPayload) -> None:
    """Persist the token to the browser as a JSON-encoded cookie.

    Pass ``expires`` explicitly: streamlit-cookies-controller defaults it to
    24h when omitted (cookie_controller.py:81-82), which would override the
    90-day ``max_age`` and force the user to re-authorise after one day.
    """
    controller.set(
        _COOKIE_NAME,
        token.model_dump_json(),
        expires=datetime.now(UTC) + timedelta(seconds=_COOKIE_MAX_AGE_SECONDS),
        max_age=_COOKIE_MAX_AGE_SECONDS,
        same_site="lax",
        path="/",
    )


def _maybe_refresh(
    token: TokenPayload, settings: Settings, controller: CookieController
) -> TokenPayload | None:
    """Refresh the token if near expiry; return the (possibly updated) token, or None on failure.

    Returns:
        The original token, a refreshed token, or ``None`` if refresh failed.
    """
    if token.expires_at - int(time.time()) > _REFRESH_WINDOW_SECONDS:
        return token
    try:
        refreshed = refresh(token, settings)
    except httpx.HTTPError:
        if controller.get(_COOKIE_NAME) is not None:
            controller.remove(_COOKIE_NAME)
        st.session_state.clear()
        st.warning("Session expired. Please log in again.")
        return None
    _write_cookie(controller, refreshed)
    return refreshed


def _format_resume_message(usage: RateLimitUsage | None) -> str:
    """Human-readable hint for when the rate limit resets.

    Returns:
        A short string like "Try again at 14:15 UTC" (15-min window) or
        "Try again after midnight UTC" (daily limit hit).
    """
    # Mirror the same threshold sync.would_exceed_next_call uses (margin=2):
    # if the daily count is what tripped the stop, the 15-min message is wrong.
    margin = 2
    now = datetime.now(UTC)
    if usage is not None and usage.daily_used + 1 + margin > usage.daily_limit:
        return "Try again after midnight UTC."
    minute = (now.minute // 15 + 1) * 15
    if minute >= 60:  # noqa: PLR2004 — minutes-per-hour boundary
        next_reset = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_reset = now.replace(minute=minute, second=0, microsecond=0)
    return f"Try again at {next_reset.strftime('%H:%M')} UTC."


def _backfill_suffix(conn: sqlite3.Connection, athlete_id: int) -> str:
    """Suffix showing count of runs awaiting detail; empty if none.

    Returns:
        A string like " N Runs still awaiting detail." or "" if none.
    """
    awaiting = store.count_runs_awaiting_detail(conn, athlete_id=athlete_id)
    if awaiting == 0:
        return ""
    return f" {awaiting} Runs still awaiting detail."


def _render_sync_result(
    result: SyncResult,
    controller: CookieController,
    conn: sqlite3.Connection,
    athlete_id: int,
) -> None:
    if result.error == "auth_failed":
        st.error("Session expired — please log in again.")
        if controller.get(_COOKIE_NAME) is not None:
            controller.remove(_COOKIE_NAME)
        st.session_state.clear()
        return
    if result.error == "http_error":
        st.error("Sync failed. Please try again.")
        return
    suffix = _backfill_suffix(conn, athlete_id)
    if result.rate_limited:
        st.warning(
            f"Rate-limited after {result.inserted_or_updated} activities. "
            f"{_format_resume_message(result.usage)}{suffix}"
        )
        return
    if result.deleted:
        st.success(
            f"Synced {result.inserted_or_updated} activities; "
            f"removed {result.deleted} no longer on Strava.{suffix}"
        )
        return
    st.success(f"Synced {result.inserted_or_updated} new activities.{suffix}")


def _run_sync_with_status(
    token: TokenPayload,
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    full: bool,
) -> SyncResult:
    label = "Full rescan…" if full else "Syncing…"
    pages_seen = 0
    with st.status(label, expanded=True) as status:

        def on_page(page_number: int, count: int) -> None:
            nonlocal pages_seen
            pages_seen += 1
            status.write(f"Page {page_number} — {count} activities")

        if full:
            result = sync.full_rescan(token, settings, conn, on_page=on_page)
        else:
            result = sync.run(token, settings, conn, on_page=on_page)
        if pages_seen == 0 and result.error is None and not result.rate_limited:
            status.write("No new activities.")
        status.update(state="complete")
    return result


def _render_logged_out(settings: Settings, controller: CookieController) -> None:
    cached = controller.get(_OAUTH_STATE_COOKIE_NAME)
    if isinstance(cached, str) and cached:
        state = cached
    else:
        state = secrets.token_urlsafe(32)
        controller.set(
            _OAUTH_STATE_COOKIE_NAME,
            state,
            max_age=_OAUTH_STATE_COOKIE_MAX_AGE_SECONDS,
            same_site="lax",
            path="/",
        )
    url = build_authorize_url(settings, state=state)
    st.link_button("Authorize with Strava", url)


def _render_sync_tab(
    token: TokenPayload,
    settings: Settings,
    db_conn: sqlite3.Connection,
    controller: CookieController,
) -> None:
    """Render the Sync tab body (the existing logged-in UI)."""
    count = store.count_activities(db_conn, athlete_id=token.athlete_id)
    cursor = store.get_cursor(db_conn, athlete_id=token.athlete_id)
    last_synced = cursor.last_synced_at if cursor is not None else None
    st.write(f"Stored activities: **{count}**")
    if last_synced:
        st.caption(f"Last synced: {last_synced}")

    col_sync, col_rescan, col_logout = st.columns(3)
    clicked_sync = col_sync.button("Sync activities")
    confirm_rescan = col_rescan.checkbox("Confirm full rescan")
    clicked_rescan = col_rescan.button("Full rescan", disabled=not confirm_rescan)
    clicked_logout = col_logout.button("Log out")

    if clicked_sync or clicked_rescan:
        # Do NOT call st.rerun() on success — it would wipe the status
        # block and result message before the user can read them. The
        # count display at the top is stale until the next interaction;
        # that's an acceptable trade-off for v1.
        result = _run_sync_with_status(token, settings, db_conn, full=clicked_rescan)
        _render_sync_result(result, controller, db_conn, token.athlete_id)
        if result.error == "auth_failed":
            st.rerun()

    if clicked_logout:
        controller.remove(_COOKIE_NAME)
        st.session_state.clear()
        st.rerun()


def _render_pbs_tab(db_conn: sqlite3.Connection, athlete_id: int) -> None:
    """Render the PBs tab body."""
    rows = pbs.compute_rows(db_conn, athlete_id=athlete_id)
    if not rows:
        st.info("No PBs yet — click Sync activities, then come back.")
        return
    values_df, mask_df = pbs.to_dataframe(rows)
    pb_style = "background-color: rgba(78, 161, 255, 0.18); color: #4ea1ff; font-weight: 700"
    styler = values_df.style.apply(
        lambda col: [pb_style if mask_df.at[idx, col.name] else "" for idx in col.index],
        axis=0,
    )
    st.dataframe(styler, width="stretch")
    awaiting = store.count_runs_awaiting_detail(db_conn, athlete_id=athlete_id)
    if awaiting > 0:
        st.caption(f"{awaiting} Runs still awaiting detail — keep clicking Sync.")


def _render_logged_in(
    token: TokenPayload, settings: Settings, controller: CookieController
) -> None:
    """Show the athlete header, sync UI, and the PBs tab."""
    st.write(f"Logged in as {token.athlete_first_name} {token.athlete_last_name}")

    db_conn = store.connect(settings.pbinator_db_path)
    try:
        tab_sync, tab_pbs = st.tabs(["Sync", "PBs"])
        with tab_sync:
            _render_sync_tab(token, settings, db_conn, controller)
        with tab_pbs:
            _render_pbs_tab(db_conn, token.athlete_id)
    finally:
        db_conn.close()


def _handle_callback(settings: Settings, controller: CookieController) -> None:
    """Process ?code=/?state=/?error= query params; mutates st.session_state."""
    params = st.query_params
    if "error" in params:
        st.warning("Authorization denied — try again.")
        if controller.get(_OAUTH_STATE_COOKIE_NAME) is not None:
            controller.remove(_OAUTH_STATE_COOKIE_NAME)
        st.query_params.clear()
        return
    if "code" not in params:
        return

    expected_state = controller.get(_OAUTH_STATE_COOKIE_NAME)
    received_state = params.get("state")
    if not expected_state or received_state != expected_state:
        st.error("Invalid OAuth state. Please log in again.")
        if controller.get(_COOKIE_NAME) is not None:
            controller.remove(_COOKIE_NAME)
        if controller.get(_OAUTH_STATE_COOKIE_NAME) is not None:
            controller.remove(_OAUTH_STATE_COOKIE_NAME)
        st.session_state.clear()
        st.query_params.clear()
        return

    try:
        token = exchange_code(params["code"], settings)
    except httpx.HTTPError:
        st.error("Login failed. Please try again.")
        st.query_params.clear()
        return

    _write_cookie(controller, token)
    st.session_state.token = token
    if controller.get(_OAUTH_STATE_COOKIE_NAME) is not None:
        controller.remove(_OAUTH_STATE_COOKIE_NAME)
    st.query_params.clear()
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="pbinator", page_icon=":runner:")
    st.title("pbinator")
    settings = _load_settings()
    if settings is None:  # st.stop already called; satisfies the type checker
        return

    # CookieController's Python-side cache is empty on the first render after a
    # page load — its custom component needs a JS round-trip before it can ship
    # real cookies back. Streamlit stores the component's value under its `key`
    # in session_state, so the absence of "cookies" tells us we're on that first
    # render. Defer until the post-sync rerun; otherwise the OAuth state cookie
    # appears missing and every callback fails CSRF validation.
    cookies_synced = "cookies" in st.session_state
    controller = CookieController()
    if not cookies_synced:
        return

    if "token" not in st.session_state:
        cookie_token = _read_cookie(controller)
        if cookie_token is not None:
            refreshed = _maybe_refresh(cookie_token, settings, controller)
            if refreshed is not None:
                st.session_state.token = refreshed

    if "token" not in st.session_state:
        _handle_callback(settings, controller)

    token = st.session_state.get("token")
    if token is not None:
        _render_logged_in(token, settings, controller)
    else:
        _render_logged_out(settings, controller)


main()
