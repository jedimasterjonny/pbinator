"""Strava activities-list HTTP client and rate-limit header parsing.

Pure-logic module: takes an httpx.Client and a token, returns dataclasses
or raises typed exceptions. No Streamlit, no env reads, no global state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


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


def parse_rate_limit_headers(response: httpx.Response) -> RateLimitUsage:
    """Parse Strava's rate-limit headers, preferring the read-specific pair.

    Returns:
        A populated ``RateLimitUsage``.
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
