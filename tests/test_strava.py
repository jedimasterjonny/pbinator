import pytest
from pydantic import ValidationError

from pbinator.strava import TokenPayload


def _strava_token_response() -> dict[str, object]:
    """A representative Strava POST /oauth/token response body.

    Returns:
        A dict matching the Strava token exchange response shape.
    """
    return {
        "token_type": "Bearer",
        "access_token": "access-abc",
        "refresh_token": "refresh-xyz",
        "expires_at": 1735689600,
        "expires_in": 21600,
        "athlete": {
            "id": 12345,
            "firstname": "Jane",
            "lastname": "Doe",
            "username": "jane",
        },
    }


def test_token_payload_parses_strava_response() -> None:
    payload = TokenPayload.from_strava_response(_strava_token_response())

    assert payload.access_token == "access-abc"  # noqa: S105 — fixture value, not a real credential
    assert payload.refresh_token == "refresh-xyz"  # noqa: S105 — fixture value, not a real credential
    assert payload.expires_at == 1735689600
    assert payload.athlete_id == 12345
    assert payload.athlete_first_name == "Jane"
    assert payload.athlete_last_name == "Doe"


def test_token_payload_round_trips_via_json() -> None:
    payload = TokenPayload.from_strava_response(_strava_token_response())

    serialised = payload.model_dump_json()
    restored = TokenPayload.model_validate_json(serialised)

    assert restored == payload


def test_token_payload_rejects_corrupt_data() -> None:
    with pytest.raises(ValidationError):
        TokenPayload.model_validate({"access_token": "only-this"})
