"""Strava activities-list HTTP client and rate-limit header parsing.

Pure-logic module: takes an httpx.Client and a token, returns dataclasses
or raises typed exceptions. No Streamlit, no env reads, no global state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


@dataclass(frozen=True)
class RateLimitUsage:
    """Strava rate-limit headers, parsed.

    All four fields refer to the **read** rate limit when read-specific
    headers are present, otherwise to the overall rate limit.
    """

    short_used: int  # 15-minute window
    short_limit: int
    daily_used: int
    daily_limit: int


@dataclass(frozen=True)
class ActivityPage:
    """One page of SummaryActivity dicts plus the rate-limit usage observed."""

    activities: list[dict[str, Any]]
    usage: RateLimitUsage


class RateLimited(Exception):  # noqa: N818
    """Raised when Strava returns 429. Carries the parsed usage if present."""

    def __init__(self, usage: RateLimitUsage | None) -> None:
        super().__init__("Strava rate limit hit")
        self.usage = usage


class AuthError(Exception):
    """Raised when Strava returns 401."""


def parse_rate_limit_headers(response: httpx.Response) -> RateLimitUsage:
    """Parse Strava's rate-limit headers, preferring the read-specific pair.

    Returns:
        A populated ``RateLimitUsage``.

    Raises KeyError if neither read-specific nor overall headers are present.
    """
    limit_header = (
        response.headers.get("X-ReadRateLimit-Limit") or response.headers["X-RateLimit-Limit"]
    )
    usage_header = (
        response.headers.get("X-ReadRateLimit-Usage") or response.headers["X-RateLimit-Usage"]
    )
    short_limit, daily_limit = (int(x) for x in limit_header.split(","))
    short_used, daily_used = (int(x) for x in usage_header.split(","))
    return RateLimitUsage(
        short_used=short_used,
        short_limit=short_limit,
        daily_used=daily_used,
        daily_limit=daily_limit,
    )


def fetch_page(
    client: httpx.Client,
    access_token: str,
    *,
    after: int | None,
    page: int,
    per_page: int,
) -> ActivityPage:
    """Fetch one page of the authenticated athlete's SummaryActivity list.

    Returns:
        An ``ActivityPage`` (which may have an empty ``activities`` list).

    Raises:
        AuthError: on 401.
        RateLimited: on 429, carries usage when headers were sent.
    """
    params: dict[str, int] = {"page": page, "per_page": per_page}
    if after is not None:
        params["after"] = after
    response = client.get(
        _ACTIVITIES_URL,
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code == httpx.codes.UNAUTHORIZED:
        raise AuthError
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        usage: RateLimitUsage | None
        try:
            usage = parse_rate_limit_headers(response)
        except KeyError:
            usage = None
        raise RateLimited(usage)
    response.raise_for_status()
    body = response.json()
    return ActivityPage(activities=list(body), usage=parse_rate_limit_headers(response))
