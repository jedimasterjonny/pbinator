"""Strava detailed-activity HTTP client and best_efforts parsing.

Pure-logic module: takes an httpx.Client and a token, returns dataclasses
or raises typed exceptions. No Streamlit, no env reads, no global state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from pbinator.activities_api import (
    AuthError,
    RateLimited,
    parse_rate_limit_headers,
)

if TYPE_CHECKING:
    from pbinator.activities_api import RateLimitUsage

_DETAIL_URL_TEMPLATE = "https://www.strava.com/api/v3/activities/{activity_id}"

# The full set of best_efforts labels Strava emits for a Run. Anything outside
# this allow-list is dropped on parse — we'd rather miss an unknown future
# label than store something we can't render.
KNOWN_LABELS: tuple[str, ...] = (
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
)


@dataclass(frozen=True)
class BestEffortRow:
    """One ``best_effort`` segment, normalised for storage."""

    distance_label: str
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    start_date: str


@dataclass(frozen=True)
class DetailFetch:
    """Outcome of one ``fetch_detail`` call: the raw payload + parsed usage."""

    detail: dict[str, Any]
    usage: RateLimitUsage


def parse_best_efforts(detail: dict[str, Any]) -> list[BestEffortRow]:
    """Extract and normalise the ``best_efforts`` array from a detailed activity.

    Returns:
        Rows for known labels, in the order Strava sent them. Unknown labels
        are filtered out. Missing or null ``best_efforts`` returns ``[]``.

    Note:
        Dict key access (``entry["moving_time"]`` etc.) will raise ``KeyError``
        if a known-label entry is missing a required field — this is intentional.
    """
    raw = detail.get("best_efforts")
    if not raw:
        return []
    rows: list[BestEffortRow] = []
    for entry in raw:
        label = str(entry["name"])
        if label not in KNOWN_LABELS:
            continue
        rows.append(
            BestEffortRow(
                distance_label=label,
                distance_m=float(entry["distance"]),
                moving_time_s=int(entry["moving_time"]),
                elapsed_time_s=int(entry["elapsed_time"]),
                start_date=str(entry["start_date"]),
            ),
        )
    return rows


def fetch_detail(
    client: httpx.Client,
    access_token: str,
    *,
    activity_id: int,
) -> DetailFetch:
    """Fetch one detailed activity by id.

    Returns:
        A ``DetailFetch`` carrying the raw JSON payload and parsed usage.

    Raises:
        AuthError: on 401.
        RateLimited: on 429, carries usage when headers were sent.
    """
    url = _DETAIL_URL_TEMPLATE.format(activity_id=activity_id)
    response = client.get(
        url,
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
    return DetailFetch(
        detail=response.json(),
        usage=parse_rate_limit_headers(response),
    )
