from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from pbinator.settings import Settings

_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
_TOKEN_URL = "https://www.strava.com/oauth/token"  # noqa: S105 — OAuth endpoint URL, not a credential
_REQUEST_TIMEOUT_SECONDS = 10.0


def build_authorize_url(settings: Settings, state: str) -> str:
    """Build the Strava OAuth2 authorize URL for the activity:read scope.

    Returns:
        The fully-qualified authorize URL with all required query parameters.
    """
    params = {
        "client_id": settings.strava_client_id,
        "redirect_uri": settings.strava_redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "activity:read",
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, settings: Settings) -> "TokenPayload":
    """Exchange an authorization code for a Strava access token.

    Returns:
        A populated ``TokenPayload`` with access/refresh tokens and athlete info.
    """
    response = httpx.post(
        _TOKEN_URL,
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret.get_secret_value(),
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return TokenPayload.from_strava_response(response.json())


def refresh(token: "TokenPayload", settings: Settings) -> "TokenPayload":
    """Refresh the access token, preserving athlete info from the input token.

    Returns:
        A new ``TokenPayload`` with rotated access/refresh tokens and expiry.
    """
    response = httpx.post(
        _TOKEN_URL,
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret.get_secret_value(),
            "refresh_token": token.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    return token.model_copy(
        update={
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_at": body["expires_at"],
        },
    )


class TokenPayload(BaseModel):
    """Strava OAuth token + minimal athlete info, persisted to a cookie."""

    access_token: str
    refresh_token: str
    expires_at: int
    athlete_id: int
    athlete_first_name: str
    athlete_last_name: str

    @classmethod
    def from_strava_response(cls, body: dict[str, Any]) -> "TokenPayload":
        """Build from a Strava `POST /oauth/token` (authorization-code) response.

        Returns:
            A populated ``TokenPayload`` instance.
        """
        athlete = body["athlete"]
        return cls(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_at=body["expires_at"],
            athlete_id=athlete["id"],
            athlete_first_name=athlete["firstname"],
            athlete_last_name=athlete["lastname"],
        )
