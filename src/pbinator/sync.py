"""Sync orchestrator: drives ``activities_api`` and ``store`` to populate the DB.

Pure-logic module aside from creating its own ``httpx.Client``. No Streamlit,
no global state. Returns ``SyncResult`` rather than raising into callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pbinator.activities_api import RateLimitUsage


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one ``sync.run`` or ``sync.full_rescan`` invocation."""

    inserted_or_updated: int
    pages_fetched: int
    rate_limited: bool
    usage: RateLimitUsage | None
    error: str | None  # None on success/rate-limit; else "auth_failed" | "http_error"
    deleted: int  # only nonzero on a clean full_rescan; else 0


def max_iso(a: str | None, b: str | None) -> str | None:
    """Return the lexicographically larger ISO-UTC string, treating None as smallest.

    Returns:
        ``a`` or ``b``, or ``None`` if both are ``None``.
    """
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def would_exceed_next_call(usage: RateLimitUsage, margin: int = 2) -> bool:
    """Whether one more call would push usage past either the short or daily limit.

    Returns:
        ``True`` if the next call (plus a safety ``margin``) would breach a limit.
    """
    return (
        usage.short_used + 1 + margin > usage.short_limit
        or usage.daily_used + 1 + margin > usage.daily_limit
    )
