"""Streamlit entry point. Excluded from coverage; logic lives in pbinator.strava."""

from __future__ import annotations

import secrets

import streamlit as st
from pydantic import ValidationError

from pbinator.settings import Settings
from pbinator.strava import build_authorize_url


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


def main() -> None:
    st.set_page_config(page_title="pbinator", page_icon=":runner:")
    st.title("pbinator")
    settings = _load_settings()
    if settings is None:  # st.stop already called; satisfies the type checker
        return
    _render_logged_out(settings)


main()
