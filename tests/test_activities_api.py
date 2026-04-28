import httpx
import pytest
import respx

from pbinator.activities_api import (
    ActivityPage,
    AuthError,
    RateLimited,
    RateLimitUsage,
    fetch_page,
    parse_rate_limit_headers,
)

_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


def test_parse_prefers_read_specific_headers() -> None:
    response = httpx.Response(
        200,
        headers={
            "X-RateLimit-Limit": "200,2000",
            "X-RateLimit-Usage": "12,150",
            "X-ReadRateLimit-Limit": "100,1000",
            "X-ReadRateLimit-Usage": "5,80",
        },
    )

    usage = parse_rate_limit_headers(response)

    assert usage == RateLimitUsage(short_used=5, short_limit=100, daily_used=80, daily_limit=1000)


def test_parse_falls_back_to_overall_headers() -> None:
    response = httpx.Response(
        200,
        headers={
            "X-RateLimit-Limit": "200,2000",
            "X-RateLimit-Usage": "12,150",
        },
    )

    usage = parse_rate_limit_headers(response)

    assert usage == RateLimitUsage(short_used=12, short_limit=200, daily_used=150, daily_limit=2000)


def test_parse_raises_when_neither_header_pair_present() -> None:
    response = httpx.Response(200, headers={})

    with pytest.raises(KeyError):
        parse_rate_limit_headers(response)


def _ok_headers() -> dict[str, str]:
    return {
        "X-ReadRateLimit-Limit": "100,1000",
        "X-ReadRateLimit-Usage": "10,200",
        "X-RateLimit-Limit": "200,2000",
        "X-RateLimit-Usage": "12,250",
    }


@respx.mock
def test_fetch_page_returns_activities_and_usage() -> None:
    route = respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(
            200,
            headers=_ok_headers(),
            json=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        )
    )

    with httpx.Client(timeout=5.0) as client:
        page = fetch_page(
            client,
            access_token="bearer-xyz",  # noqa: S106
            after=None,
            page=1,
            per_page=200,
        )

    assert isinstance(page, ActivityPage)
    assert [a["id"] for a in page.activities] == [1, 2]
    assert page.usage.short_used == 10
    assert page.usage.daily_limit == 1000

    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer bearer-xyz"
    qs = dict(httpx.QueryParams(request.url.query.decode()))
    assert qs["page"] == "1"
    assert qs["per_page"] == "200"
    assert "after" not in qs


@respx.mock
def test_fetch_page_passes_after_when_provided() -> None:
    route = respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, headers=_ok_headers(), json=[])
    )

    with httpx.Client(timeout=5.0) as client:
        fetch_page(
            client,
            access_token="bearer-xyz",  # noqa: S106
            after=1714000000,
            page=2,
            per_page=200,
        )

    qs = dict(httpx.QueryParams(route.calls.last.request.url.query.decode()))
    assert qs["after"] == "1714000000"
    assert qs["page"] == "2"


@respx.mock
def test_fetch_page_raises_auth_error_on_401() -> None:
    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(401, headers=_ok_headers(), json={"message": "Unauthorized"})
    )

    with httpx.Client(timeout=5.0) as client, pytest.raises(AuthError):
        fetch_page(client, access_token="bad", after=None, page=1, per_page=200)  # noqa: S106


@respx.mock
def test_fetch_page_raises_rate_limited_on_429() -> None:
    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(
            429,
            headers={
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "100,500",
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "150,800",
            },
            json={"message": "Rate Limit Exceeded"},
        )
    )

    with httpx.Client(timeout=5.0) as client, pytest.raises(RateLimited) as excinfo:
        fetch_page(client, access_token="t", after=None, page=1, per_page=200)  # noqa: S106

    assert excinfo.value.usage is not None
    assert excinfo.value.usage.short_used == 100


@respx.mock
def test_fetch_page_raises_rate_limited_with_no_headers_on_429() -> None:
    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(429, headers={}, json={"message": "Rate Limit Exceeded"})
    )

    with httpx.Client(timeout=5.0) as client, pytest.raises(RateLimited) as excinfo:
        fetch_page(client, access_token="t", after=None, page=1, per_page=200)  # noqa: S106

    assert excinfo.value.usage is None


@respx.mock
def test_fetch_page_propagates_other_http_errors() -> None:
    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(500, headers=_ok_headers(), json={"message": "boom"})
    )

    with httpx.Client(timeout=5.0) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_page(client, access_token="t", after=None, page=1, per_page=200)  # noqa: S106
