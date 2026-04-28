from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from pydantic import ValidationError

from pbinator.settings import Settings
from pbinator.strava import TokenPayload, build_authorize_url, exchange_code, refresh


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


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8501/")
    # ty: see test_settings.py for justification of the missing-argument ignore
    return Settings()  # ty: ignore[missing-argument]


def test_build_authorize_url_contains_required_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    url = build_authorize_url(settings, state="csrf-abc")

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.strava.com"
    assert parsed.path == "/oauth/authorize"

    params = parse_qs(parsed.query)
    assert params["client_id"] == ["client-123"]
    assert params["redirect_uri"] == ["http://localhost:8501/"]
    assert params["response_type"] == ["code"]
    assert params["approval_prompt"] == ["auto"]
    assert params["scope"] == ["activity:read"]
    assert params["state"] == ["csrf-abc"]


@respx.mock
def test_exchange_code_returns_token_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)

    route = respx.post("https://www.strava.com/oauth/token").mock(
        return_value=httpx.Response(200, json=_strava_token_response()),
    )

    payload = exchange_code("auth-code-123", settings)

    assert route.called
    sent = route.calls.last.request
    body = dict(httpx.QueryParams(sent.content.decode()))
    assert body["client_id"] == "client-123"
    assert body["client_secret"] == "secret-xyz"  # noqa: S105 — fixture value, not a real credential
    assert body["code"] == "auth-code-123"
    assert body["grant_type"] == "authorization_code"

    assert payload.access_token == "access-abc"  # noqa: S105 — fixture value, not a real credential
    assert payload.athlete_first_name == "Jane"


@respx.mock
def test_exchange_code_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)

    respx.post("https://www.strava.com/oauth/token").mock(
        return_value=httpx.Response(400, json={"message": "Bad Request"}),
    )

    with pytest.raises(httpx.HTTPStatusError):
        exchange_code("bad-code", settings)


@respx.mock
def test_refresh_preserves_athlete_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    existing = TokenPayload.from_strava_response(_strava_token_response())

    route = respx.post("https://www.strava.com/oauth/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "access-NEW",
                "refresh_token": "refresh-NEW",
                "expires_at": 1735693200,
                "expires_in": 21600,
            },
        ),
    )

    refreshed = refresh(existing, settings)

    assert route.called
    body = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "refresh-xyz"  # noqa: S105 — fixture value, not a real credential
    assert body["client_id"] == "client-123"
    assert body["client_secret"] == "secret-xyz"  # noqa: S105 — fixture value, not a real credential

    assert refreshed.access_token == "access-NEW"  # noqa: S105 — fixture value, not a real credential
    assert refreshed.refresh_token == "refresh-NEW"  # noqa: S105 — fixture value, not a real credential
    assert refreshed.expires_at == 1735693200
    assert refreshed.athlete_id == existing.athlete_id
    assert refreshed.athlete_first_name == existing.athlete_first_name
    assert refreshed.athlete_last_name == existing.athlete_last_name


@respx.mock
def test_refresh_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    existing = TokenPayload.from_strava_response(_strava_token_response())

    respx.post("https://www.strava.com/oauth/token").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"}),
    )

    with pytest.raises(httpx.HTTPStatusError):
        refresh(existing, settings)
