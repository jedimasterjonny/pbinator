from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel

from pbinator.settings import Settings

_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"


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
