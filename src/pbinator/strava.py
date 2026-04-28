from typing import Any

from pydantic import BaseModel


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
