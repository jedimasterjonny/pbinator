import httpx
import pytest

from pbinator.activities_api import RateLimitUsage, parse_rate_limit_headers


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
