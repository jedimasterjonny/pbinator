"""Sync orchestrator: drives ``activities_api`` and ``store`` to populate the DB.

Pure-logic module aside from creating its own ``httpx.Client``. No Streamlit,
no global state. Returns ``SyncResult`` rather than raising into callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class _Ctx:
    """The handles every step of one sync pass needs."""

    client: httpx.Client
    token: TokenPayload
    session: Session


def _process_detail_fetches_for_page(
    ctx: _Ctx,
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
    athlete_id = ctx.token.athlete_id
    run_ids = [int(a["id"]) for a in activities if str(a["sport_type"]) == "Run"]
    already_fetched = store.already_fetched_run_ids(
        ctx.session, athlete_id=athlete_id, run_ids=run_ids
    )

    usage = usage_in
    for activity in activities:
        if str(activity["sport_type"]) != "Run":
            continue
        if int(activity["id"]) in already_fetched:
            continue
        if would_exceed_next_call(usage):
            raise _RateBudgetError(usage)
        fetched = best_efforts_api.fetch_detail(
            ctx.client, ctx.token.access_token, activity_id=int(activity["id"])
        )
        usage = fetched.usage
        rows = best_efforts_api.parse_best_efforts(fetched.detail)
        with store.write_transaction(ctx.session):
            store.upsert_best_efforts(
                ctx.session,
                athlete_id=athlete_id,
                activity_id=int(activity["id"]),
                efforts=rows,
            )
            store.mark_detail_fetched(
                ctx.session,
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


@dataclass
class _Progress:
    """Running totals for one pagination pass.

    Mutated in place by ``_fetch_pages`` so that partial work survives an
    exception raised part-way through the walk.
    """

    max_seen_start: str | None
    seen_ids: set[int] = field(default_factory=set)
    pages_fetched: int = 0
    inserted: int = 0
    rate_limited: bool = False
    usage: RateLimitUsage | None = None
    # True only when the walk reached the end of the list. A pass cut short by
    # the rate limit or an error leaves this False -- see _sync for why it matters.
    completed: bool = False


def _fetch_pages(
    ctx: _Ctx,
    progress: _Progress,
    on_page: Callable[[int, int], None] | None,
    *,
    after: int | None,
) -> None:
    """Walk activity pages from ``after`` onward, upserting each one.

    Stops on an empty page or when the next call would breach the rate limit.
    """
    athlete_id = ctx.token.athlete_id
    page = 1
    while True:
        page_data: ActivityPage = activities_api.fetch_page(
            ctx.client,
            ctx.token.access_token,
            after=after,
            page=page,
            per_page=_PER_PAGE,
        )
        progress.pages_fetched += 1
        usage = page_data.usage
        progress.usage = usage
        if not page_data.activities:
            progress.completed = True  # empty page == end of the list
            return
        with store.write_transaction(ctx.session):  # one transaction per page
            for activity in page_data.activities:
                store.upsert_activity(ctx.session, athlete_id=athlete_id, activity=activity)
                progress.seen_ids.add(int(activity["id"]))
                progress.inserted += 1
                progress.max_seen_start = max_iso(
                    progress.max_seen_start, str(activity["start_date"])
                )
        usage = _process_detail_fetches_for_page(ctx, page_data.activities, usage)
        progress.usage = usage
        if on_page is not None:
            on_page(page, len(page_data.activities))
        if would_exceed_next_call(usage):
            progress.rate_limited = True
            return
        page += 1


def _sync(
    token: TokenPayload,
    session: Session,
    on_page: Callable[[int, int], None] | None,
    *,
    incremental: bool,
) -> SyncResult:
    """Paginate, upsert, and advance the cursor for ``token.athlete_id``.

    When ``incremental``, resume from the stored cursor and never delete.
    Otherwise re-fetch everything and reconcile deletions on a clean run.

    Returns:
        A ``SyncResult`` describing the outcome.
    """
    # Seed from the existing cursor so a no-op pass (empty first page, or an
    # error before any page completes) does not wipe the stored position.
    cursor = store.get_cursor(session, athlete_id=token.athlete_id)
    last_start: str | None = cursor.last_activity_start if cursor is not None else None
    after = _iso_to_epoch(last_start) if incremental and last_start is not None else None

    progress = _Progress(max_seen_start=last_start)
    error: str | None = None

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            _fetch_pages(_Ctx(client, token, session), progress, on_page, after=after)
    except _RateBudgetError as exc:
        progress.rate_limited = True
        progress.usage = exc.usage
    except RateLimited as exc:
        progress.rate_limited = True
        progress.usage = exc.usage if exc.usage is not None else progress.usage
    except AuthError:
        error = "auth_failed"
    except httpx.HTTPError:
        error = "http_error"
    finally:
        # Only move the watermark when the walk actually reached the end of the
        # list. Strava returns activities NEWEST FIRST, so a pass cut short by
        # the rate limit has stored the newest activities but not the older ones
        # still queued behind them. Advancing to the newest anyway would make the
        # next `after=` query skip straight past those older activities, and no
        # amount of re-syncing would ever list them again.
        watermark = progress.max_seen_start if progress.completed else last_start
        with store.write_transaction(session):
            store.update_cursor(
                session,
                athlete_id=token.athlete_id,
                last_activity_start=watermark,
                last_synced_at=_now_iso(),
            )

    deleted = (
        0
        if incremental
        else _reconcile_deletions(
            session,
            token.athlete_id,
            progress.seen_ids,
            rate_limited=progress.rate_limited,
            error=error,
        )
    )

    return SyncResult(
        inserted_or_updated=progress.inserted,
        pages_fetched=progress.pages_fetched,
        rate_limited=progress.rate_limited,
        usage=progress.usage,
        error=error,
        deleted=deleted,
    )


def run(
    token: TokenPayload,
    session: Session,
    on_page: Callable[[int, int], None] | None = None,
) -> SyncResult:
    """Run an incremental sync for ``token.athlete_id``.

    Returns:
        A ``SyncResult`` describing the outcome. Cursor is always advanced
        with progress made (if any), even on errors.
    """
    return _sync(token, session, on_page, incremental=True)


def full_rescan(
    token: TokenPayload,
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
    return _sync(token, session, on_page, incremental=False)
