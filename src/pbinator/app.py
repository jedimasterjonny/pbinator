"""Streamlit entry point. Excluded from coverage; logic lives in pbinator.strava."""

from __future__ import annotations

import secrets

import httpx
import streamlit as st
from pydantic import ValidationError
from streamlit_cookies_controller import CookieController

from pbinator.settings import Settings
from pbinator.strava import TokenPayload, build_authorize_url, exchange_code

_COOKIE_NAME = "pbinator_strava"
_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 90  # 90 days


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
    """Persist the token to the browser as a JSON-encoded cookie."""
    controller.set(
        _COOKIE_NAME,
        token.model_dump_json(),
        max_age=_COOKIE_MAX_AGE_SECONDS,
        same_site="lax",
        path="/",
    )


def _render_logged_out(settings: Settings) -> None:
    if "oauth_state" not in st.session_state:
        st.session_state.oauth_state = secrets.token_urlsafe(32)
    url = build_authorize_url(settings, state=st.session_state.oauth_state)
    st.link_button("Authorize with Strava", url)


def _render_logged_in(token: TokenPayload, controller: CookieController) -> None:
    """Show the athlete's name and a logout button."""
    st.write(f"Logged in as {token.athlete_first_name} {token.athlete_last_name}")
    if st.button("Log out"):
        controller.remove(_COOKIE_NAME)
        st.session_state.clear()
        st.rerun()


def _handle_callback(settings: Settings, controller: CookieController) -> None:
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
        if controller.get(_COOKIE_NAME) is not None:
            controller.remove(_COOKIE_NAME)
        st.session_state.clear()
        st.query_params.clear()
        return

    try:
        token = exchange_code(params["code"], settings)
    except httpx.HTTPError as exc:
        st.error(f"Login failed: {exc}")
        st.query_params.clear()
        return

    _write_cookie(controller, token)
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

    controller = CookieController()

    if "token" not in st.session_state:
        cookie_token = _read_cookie(controller)
        if cookie_token is not None:
            st.session_state.token = cookie_token

    if "token" not in st.session_state:
        _handle_callback(settings, controller)

    token = st.session_state.get("token")
    if token is not None:
        _render_logged_in(token, controller)
    else:
        _render_logged_out(settings)


main()
