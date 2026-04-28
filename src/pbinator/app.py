"""Streamlit entry point. Excluded from coverage; logic lives in pbinator.strava."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import httpx
import streamlit as st
from pydantic import ValidationError

from pbinator.settings import Settings
from pbinator.strava import build_authorize_url, exchange_code

if TYPE_CHECKING:
    from pbinator.strava import TokenPayload


def _load_settings() -> Settings | None:
    try:
        # pydantic-settings injects required fields from env vars; ty can't see this.
        return Settings()  # ty: ignore[missing-argument]
    except ValidationError:
        st.error("Configure STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env")
        st.stop()
        return None


def _render_logged_out(settings: Settings) -> None:
    if "oauth_state" not in st.session_state:
        st.session_state.oauth_state = secrets.token_urlsafe(32)
    url = build_authorize_url(settings, state=st.session_state.oauth_state)
    st.link_button("Authorize with Strava", url)


def _render_logged_in(token: TokenPayload) -> None:
    """Show the athlete's name and a logout button."""
    st.write(f"Logged in as {token.athlete_first_name} {token.athlete_last_name}")
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()


def _handle_callback(settings: Settings) -> None:
    """Process ?code=/?state=/?error= query params; mutates st.session_state."""
    params = st.query_params
    if "error" in params:
        st.warning("Authorization denied — try again.")
        st.query_params.clear()
        return
    if "code" not in params:
        return

    expected_state = st.session_state.get("oauth_state")
    received_state = params.get("state")
    if not expected_state or received_state != expected_state:
        st.error("Invalid OAuth state. Please log in again.")
        st.session_state.clear()
        st.query_params.clear()
        return

    try:
        token = exchange_code(params["code"], settings)
    except httpx.HTTPError as exc:
        st.error(f"Login failed: {exc}")
        st.query_params.clear()
        return

    st.session_state.token = token
    st.session_state.pop("oauth_state", None)
    st.query_params.clear()
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="pbinator", page_icon=":runner:")
    st.title("pbinator")
    settings = _load_settings()
    if settings is None:  # st.stop already called; satisfies the type checker
        return

    if "token" not in st.session_state:
        _handle_callback(settings)

    token = st.session_state.get("token")
    if token is not None:
        _render_logged_in(token)
    else:
        _render_logged_out(settings)


main()
