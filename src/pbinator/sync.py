"""Sync orchestrator: drives ``activities_api`` and ``store`` to populate the DB.

Pure-logic module aside from creating its own ``httpx.Client``. No Streamlit,
no global state. Returns ``SyncResult`` rather than raising into callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from pbinator import activities_api, store
from pbinator.activities_api import AuthError, RateLimited

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from pbinator.activities_api import ActivityPage, RateLimitUsage
    from pbinator.settings import Settings
    from pbinator.strava import TokenPayload


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


_HTTP_TIMEOUT_SECONDS = 10.0
_PER_PAGE = 200


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _iso_to_epoch(iso_utc: str) -> int:
    """Parse a Strava ISO-UTC timestamp (``...Z``) into a Unix epoch.

    Returns:
        Integer Unix timestamp.
    """
    # Strava emits "2024-04-15T07:00:00Z"; fromisoformat in 3.13 accepts this.
    return int(datetime.fromisoformat(iso_utc).timestamp())


def run(
    token: TokenPayload,
    settings: Settings,  # noqa: ARG001 — reserved for future use; kept for interface symmetry
    conn: sqlite3.Connection,
    on_page: Callable[[int, int], None] | None = None,
) -> SyncResult:
    """Run an incremental sync for ``token.athlete_id``.

    Returns:
        A ``SyncResult`` describing the outcome. Cursor is always advanced
        with progress made (if any), even on errors.
    """
    cursor = store.get_cursor(conn, athlete_id=token.athlete_id)
    after_epoch: int | None = (
        _iso_to_epoch(cursor.last_activity_start)
        if cursor is not None and cursor.last_activity_start is not None
        else None
    )
    max_seen_start: str | None = cursor.last_activity_start if cursor is not None else None

    page = 1
    pages_fetched = 0
    inserted = 0
    rate_limited = False
    error: str | None = None
    usage: RateLimitUsage | None = None

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            while True:
                page_data: ActivityPage = activities_api.fetch_page(
                    client,
                    token.access_token,
                    after=after_epoch,
                    page=page,
                    per_page=_PER_PAGE,
                )
                pages_fetched += 1
                usage = page_data.usage
                if not page_data.activities:
                    break
                with conn:  # one transaction per page
                    for activity in page_data.activities:
                        store.upsert_activity(conn, athlete_id=token.athlete_id, activity=activity)
                        inserted += 1
                        max_seen_start = max_iso(max_seen_start, str(activity["start_date"]))
                if on_page is not None:
                    on_page(page, len(page_data.activities))
                if would_exceed_next_call(usage):
                    rate_limited = True
                    break
                page += 1
    except RateLimited as exc:
        rate_limited = True
        usage = exc.usage if exc.usage is not None else usage
    except AuthError:
        error = "auth_failed"
    except httpx.HTTPError:
        error = "http_error"
    finally:
        # Wrap in `with conn:` so the cursor write commits on clean exit
        # — sqlite3's default isolation_level holds writes in an implicit
        # transaction that would otherwise roll back on conn.close().
        with conn:
            store.update_cursor(
                conn,
                athlete_id=token.athlete_id,
                last_activity_start=max_seen_start,
                last_synced_at=_now_iso(),
            )

    return SyncResult(
        inserted_or_updated=inserted,
        pages_fetched=pages_fetched,
        rate_limited=rate_limited,
        usage=usage,
        error=error,
        deleted=0,
    )


def full_rescan(
    token: TokenPayload,
    settings: Settings,  # noqa: ARG001 — reserved for future use; kept for interface symmetry
    conn: sqlite3.Connection,
    on_page: Callable[[int, int], None] | None = None,
) -> SyncResult:
    """Re-fetch every activity, upsert, and reconcile deletions on a clean run.

    Reconciliation (deleting unseen rows for this athlete) only fires when the
    run completed without rate-limit truncation, without errors, AND saw at
    least one activity. An empty first page is treated as a transient/visibility
    issue and does NOT wipe the local DB.

    Returns:
        A ``SyncResult`` with ``deleted`` set on a clean reconciling run, else 0.
    """
    seen_ids: set[int] = set()
    # Seed from the existing cursor so a no-op rescan (empty first page,
    # network/auth/rate-limit error before any page completes) does not wipe
    # the previously stored last_activity_start when update_cursor runs below.
    cursor = store.get_cursor(conn, athlete_id=token.athlete_id)
    max_seen_start: str | None = cursor.last_activity_start if cursor is not None else None
    page = 1
    pages_fetched = 0
    inserted = 0
    rate_limited = False
    error: str | None = None
    usage: RateLimitUsage | None = None

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            while True:
                page_data = activities_api.fetch_page(
                    client,
                    token.access_token,
                    after=None,
                    page=page,
                    per_page=_PER_PAGE,
                )
                pages_fetched += 1
                usage = page_data.usage
                if not page_data.activities:
                    break
                with conn:
                    for activity in page_data.activities:
                        store.upsert_activity(conn, athlete_id=token.athlete_id, activity=activity)
                        seen_ids.add(int(activity["id"]))
                        inserted += 1
                        max_seen_start = max_iso(max_seen_start, str(activity["start_date"]))
                if on_page is not None:
                    on_page(page, len(page_data.activities))
                if would_exceed_next_call(usage):
                    rate_limited = True
                    break
                page += 1
    except RateLimited as exc:
        rate_limited = True
        usage = exc.usage if exc.usage is not None else usage
    except AuthError:
        error = "auth_failed"
    except httpx.HTTPError:
        error = "http_error"

    deleted = 0
    # See the SQLite commit gotcha at the top of this plan: every write must
    # be inside `with conn:` to commit before the connection is closed.
    if not rate_limited and error is None and seen_ids:
        with conn:
            deleted = store.delete_activities_not_in(
                conn, athlete_id=token.athlete_id, kept_ids=seen_ids
            )

    with conn:
        store.update_cursor(
            conn,
            athlete_id=token.athlete_id,
            last_activity_start=max_seen_start,
            last_synced_at=_now_iso(),
        )

    return SyncResult(
        inserted_or_updated=inserted,
        pages_fetched=pages_fetched,
        rate_limited=rate_limited,
        usage=usage,
        error=error,
        deleted=deleted,
    )
