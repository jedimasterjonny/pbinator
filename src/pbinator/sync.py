"""Sync orchestrator: drives ``activities_api`` and ``store`` to populate the DB.

Pure-logic module aside from creating its own ``httpx.Client``. No Streamlit,
no global state. Returns ``SyncResult`` rather than raising into callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from pbinator import activities_api, store
from pbinator import best_efforts as best_efforts_api
from pbinator.activities_api import AuthError, RateLimited

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from pbinator.activities_api import ActivityPage, RateLimitUsage
    from pbinator.settings import Settings
    from pbinator.strava import TokenPayload


class _RateBudgetError(Exception):
    """Internal signal: pre-flight check tripped before a detail fetch."""

    def __init__(self, usage: RateLimitUsage) -> None:
        super().__init__("rate budget exhausted")
        self.usage = usage


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


def _process_detail_fetches_for_page(  # noqa: PLR0913, PLR0917 — six params are all distinct concerns for this private helper
    client: httpx.Client,
    token_access: str,
    session: Session,
    athlete_id: int,
    activities: list[dict[str, Any]],
    usage_in: RateLimitUsage,
) -> RateLimitUsage:
    """Fetch detail + upsert best_efforts for each unfetched Run on a page.

    Mutates the DB. Returns the latest ``RateLimitUsage`` observed.

    Returns:
        Updated ``RateLimitUsage`` after all detail fetches for this page.

    Raises:
        _RateBudgetError: when the preflight check trips before a fetch.
    """
    run_ids = [int(a["id"]) for a in activities if str(a["sport_type"]) == "Run"]
    already_fetched = store.already_fetched_run_ids(session, athlete_id=athlete_id, run_ids=run_ids)

    usage = usage_in
    for activity in activities:
        if str(activity["sport_type"]) != "Run":
            continue
        if int(activity["id"]) in already_fetched:
            continue
        if would_exceed_next_call(usage):
            raise _RateBudgetError(usage)
        fetched = best_efforts_api.fetch_detail(
            client, token_access, activity_id=int(activity["id"])
        )
        usage = fetched.usage
        rows = best_efforts_api.parse_best_efforts(fetched.detail)
        with store.write_transaction(session):
            store.upsert_best_efforts(
                session,
                athlete_id=athlete_id,
                activity_id=int(activity["id"]),
                efforts=rows,
            )
            store.mark_detail_fetched(
                session,
                athlete_id=athlete_id,
                activity_id=int(activity["id"]),
                fetched_at=_now_iso(),
            )
    return usage


def _reconcile_deletions(
    session: Session,
    athlete_id: int,
    seen_ids: set[int],
    *,
    rate_limited: bool,
    error: str | None,
) -> int:
    """Delete activities for ``athlete_id`` not in ``seen_ids`` when safe to do so.

    Only fires when the rescan completed without rate-limit truncation, without
    errors, AND saw at least one activity.

    Returns:
        Number of rows deleted (0 when reconciliation is skipped).
    """
    if rate_limited or error is not None or not seen_ids:
        return 0
    with store.write_transaction(session):
        return store.delete_activities_not_in(session, athlete_id=athlete_id, kept_ids=seen_ids)


def run(
    token: TokenPayload,
    settings: Settings,  # noqa: ARG001 — reserved for future use; kept for interface symmetry
    session: Session,
    on_page: Callable[[int, int], None] | None = None,
) -> SyncResult:
    """Run an incremental sync for ``token.athlete_id``.

    Returns:
        A ``SyncResult`` describing the outcome. Cursor is always advanced
        with progress made (if any), even on errors.
    """
    cursor = store.get_cursor(session, athlete_id=token.athlete_id)
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
                with store.write_transaction(session):  # one transaction per page
                    for activity in page_data.activities:
                        store.upsert_activity(
                            session, athlete_id=token.athlete_id, activity=activity
                        )
                        inserted += 1
                        max_seen_start = max_iso(max_seen_start, str(activity["start_date"]))
                usage = _process_detail_fetches_for_page(
                    client,
                    token.access_token,
                    session,
                    token.athlete_id,
                    page_data.activities,
                    usage,
                )
                if on_page is not None:
                    on_page(page, len(page_data.activities))
                if would_exceed_next_call(usage):
                    rate_limited = True
                    break
                page += 1
    except _RateBudgetError as exc:
        rate_limited = True
        usage = exc.usage
    except RateLimited as exc:
        rate_limited = True
        usage = exc.usage if exc.usage is not None else usage
    except AuthError:
        error = "auth_failed"
    except httpx.HTTPError:
        error = "http_error"
    finally:
        with store.write_transaction(session):
            store.update_cursor(
                session,
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
    session: Session,
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
    cursor = store.get_cursor(session, athlete_id=token.athlete_id)
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
                with store.write_transaction(session):
                    for activity in page_data.activities:
                        store.upsert_activity(
                            session, athlete_id=token.athlete_id, activity=activity
                        )
                        seen_ids.add(int(activity["id"]))
                        inserted += 1
                        max_seen_start = max_iso(max_seen_start, str(activity["start_date"]))
                usage = _process_detail_fetches_for_page(
                    client,
                    token.access_token,
                    session,
                    token.athlete_id,
                    page_data.activities,
                    usage,
                )
                if on_page is not None:
                    on_page(page, len(page_data.activities))
                if would_exceed_next_call(usage):
                    rate_limited = True
                    break
                page += 1
    except _RateBudgetError as exc:
        rate_limited = True
        usage = exc.usage
    except RateLimited as exc:
        rate_limited = True
        usage = exc.usage if exc.usage is not None else usage
    except AuthError:
        error = "auth_failed"
    except httpx.HTTPError:
        error = "http_error"

    deleted = _reconcile_deletions(
        session, token.athlete_id, seen_ids, rate_limited=rate_limited, error=error
    )

    with store.write_transaction(session):
        store.update_cursor(
            session,
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
