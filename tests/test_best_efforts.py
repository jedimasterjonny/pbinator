import httpx
import pytest
import respx

from pbinator import best_efforts
from pbinator.activities_api import AuthError, RateLimited, RateLimitUsage
from pbinator.best_efforts import BestEffortRow

_DETAIL_URL = "https://www.strava.com/api/v3/activities/12345"


def _ok_headers(short_used: int = 5, daily_used: int = 100) -> dict[str, str]:
    return {
        "X-ReadRateLimit-Limit": "100,1000",
        "X-ReadRateLimit-Usage": f"{short_used},{daily_used}",
        "X-RateLimit-Limit": "200,2000",
        "X-RateLimit-Usage": "10,200",
    }


def _effort(name: str, distance: float, moving_time: int) -> dict[str, object]:
    return {
        "name": name,
        "distance": distance,
        "moving_time": moving_time,
        "elapsed_time": moving_time + 1,
        "start_date": "2024-04-15T07:00:00Z",
    }


def test_parse_best_efforts_returns_full_set() -> None:
    detail = {
        "best_efforts": [
            _effort("400m", 400.0, 60),
            _effort("1/2 mile", 804.672, 130),
            _effort("1k", 1000.0, 200),
            _effort("1 mile", 1609.34, 320),
            _effort("2 mile", 3218.69, 700),
            _effort("5k", 5000.0, 1100),
            _effort("10k", 10000.0, 2280),
            _effort("15k", 15000.0, 3550),
            _effort("Half-Marathon", 21097.5, 5090),
            _effort("Marathon", 42195.0, 10760),
        ],
    }

    rows = best_efforts.parse_best_efforts(detail)

    labels = [row.distance_label for row in rows]
    assert labels == [
        "400m",
        "1/2 mile",
        "1k",
        "1 mile",
        "2 mile",
        "5k",
        "10k",
        "15k",
        "Half-Marathon",
        "Marathon",
    ]
    assert all(isinstance(row, BestEffortRow) for row in rows)
    assert rows[5].moving_time_s == 1100
    assert rows[5].distance_m == pytest.approx(5000.0)


def test_parse_best_efforts_handles_partial_set() -> None:
    detail = {"best_efforts": [_effort("1k", 1000.0, 200)]}

    rows = best_efforts.parse_best_efforts(detail)

    assert len(rows) == 1
    assert rows[0].distance_label == "1k"


def test_parse_best_efforts_filters_unknown_labels() -> None:
    detail = {
        "best_efforts": [
            _effort("1k", 1000.0, 200),
            _effort("Lunar Mile", 1609.34, 999),
        ],
    }

    rows = best_efforts.parse_best_efforts(detail)

    assert [row.distance_label for row in rows] == ["1k"]


def test_parse_best_efforts_returns_empty_when_field_absent() -> None:
    """Strava omits ``best_efforts`` for sub-400m runs."""
    rows = best_efforts.parse_best_efforts({})
    assert rows == []


def test_parse_best_efforts_returns_empty_when_field_null() -> None:
    rows = best_efforts.parse_best_efforts({"best_efforts": None})
    assert rows == []


def test_parse_best_efforts_raises_on_missing_required_field() -> None:
    detail = {"best_efforts": [{"name": "1k", "distance": 1000.0}]}  # missing moving_time

    with pytest.raises(KeyError):
        best_efforts.parse_best_efforts(detail)


@respx.mock
def test_fetch_detail_returns_payload_and_usage() -> None:
    body = {"id": 12345, "best_efforts": [_effort("1k", 1000.0, 200)]}
    respx.get(_DETAIL_URL).mock(
        return_value=httpx.Response(200, headers=_ok_headers(short_used=7), json=body)
    )

    with httpx.Client(timeout=10.0) as client:
        result = best_efforts.fetch_detail(client, "acc-1", activity_id=12345)

    assert result.detail == body
    assert isinstance(result.usage, RateLimitUsage)
    assert result.usage.short_used == 7


@respx.mock
def test_fetch_detail_raises_auth_error_on_401() -> None:
    respx.get(_DETAIL_URL).mock(return_value=httpx.Response(401, headers=_ok_headers(), json={}))

    with httpx.Client(timeout=10.0) as client, pytest.raises(AuthError):
        best_efforts.fetch_detail(client, "acc-1", activity_id=12345)


@respx.mock
def test_fetch_detail_raises_rate_limited_on_429() -> None:
    respx.get(_DETAIL_URL).mock(
        return_value=httpx.Response(
            429,
            headers={
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "100,500",
            },
            json={},
        )
    )

    with httpx.Client(timeout=10.0) as client, pytest.raises(RateLimited) as excinfo:
        best_efforts.fetch_detail(client, "acc-1", activity_id=12345)

    assert excinfo.value.usage is not None
    assert excinfo.value.usage.short_used == 100


@respx.mock
def test_fetch_detail_raises_rate_limited_on_429_without_headers() -> None:
    respx.get(_DETAIL_URL).mock(return_value=httpx.Response(429, json={}))

    with httpx.Client(timeout=10.0) as client, pytest.raises(RateLimited) as excinfo:
        best_efforts.fetch_detail(client, "acc-1", activity_id=12345)

    assert excinfo.value.usage is None


@respx.mock
def test_fetch_detail_raises_http_error_on_5xx() -> None:
    respx.get(_DETAIL_URL).mock(return_value=httpx.Response(500, headers=_ok_headers(), json={}))

    with httpx.Client(timeout=10.0) as client, pytest.raises(httpx.HTTPStatusError):
        best_efforts.fetch_detail(client, "acc-1", activity_id=12345)
