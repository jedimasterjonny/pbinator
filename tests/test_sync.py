from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from pbinator import store
from pbinator.activities_api import RateLimitUsage
from pbinator.settings import Settings
from pbinator.strava import TokenPayload
from pbinator.sync import SyncResult, full_rescan, max_iso, run, would_exceed_next_call


def test_sync_result_defaults_have_zero_counts() -> None:
    result = SyncResult(
        inserted_or_updated=0,
        pages_fetched=0,
        rate_limited=False,
        usage=None,
        error=None,
        deleted=0,
    )

    assert result.inserted_or_updated == 0
    assert result.error is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (None, "2024-04-15T07:00:00Z", "2024-04-15T07:00:00Z"),
        ("2024-04-15T07:00:00Z", None, "2024-04-15T07:00:00Z"),
        (None, None, None),
        (
            "2024-04-15T07:00:00Z",
            "2024-05-01T07:00:00Z",
            "2024-05-01T07:00:00Z",
        ),
        (
            "2024-05-01T07:00:00Z",
            "2024-04-15T07:00:00Z",
            "2024-05-01T07:00:00Z",
        ),
    ],
)
def test_max_iso_picks_lex_larger_or_only_non_none(
    a: str | None, b: str | None, expected: str | None
) -> None:
    assert max_iso(a, b) == expected


def _usage(short_used: int = 0, daily_used: int = 0) -> RateLimitUsage:
    return RateLimitUsage(
        short_used=short_used,
        short_limit=100,
        daily_used=daily_used,
        daily_limit=1000,
    )


def test_would_exceed_returns_false_with_plenty_of_room() -> None:
    assert would_exceed_next_call(_usage(short_used=0, daily_used=0)) is False


def test_would_exceed_triggers_on_short_window_threshold() -> None:
    # margin defaults to 2; trip when short_used + 1 + 2 > 100 -> short_used >= 98
    assert would_exceed_next_call(_usage(short_used=97)) is False
    assert would_exceed_next_call(_usage(short_used=98)) is True


def test_would_exceed_triggers_on_daily_threshold() -> None:
    assert would_exceed_next_call(_usage(daily_used=997)) is False
    assert would_exceed_next_call(_usage(daily_used=998)) is True


def test_would_exceed_respects_custom_margin() -> None:
    # short_used=100, margin=0 -> 100 + 1 + 0 > 100 -> True
    assert would_exceed_next_call(_usage(short_used=100), margin=0) is True
    assert would_exceed_next_call(_usage(short_used=99), margin=0) is False


_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "pbinator.db"


def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("PBINATOR_DB_PATH", str(tmp_path / "ignored.db"))
    return Settings()  # ty: ignore[missing-argument]


def _token() -> TokenPayload:
    return TokenPayload(
        access_token="acc-1",  # noqa: S106 — fixture, not a credential
        refresh_token="ref-1",  # noqa: S106 — fixture, not a credential
        expires_at=int(datetime.now(UTC).timestamp()) + 3600,
        athlete_id=42,
        athlete_first_name="Jane",
        athlete_last_name="Doe",
    )


def _activity(activity_id: int, start_date: str) -> dict[str, Any]:
    return {
        "id": activity_id,
        "name": f"Activity {activity_id}",
        "sport_type": "Run",
        "start_date": start_date,
        "start_date_local": start_date.replace("Z", ""),
        "distance": 5000.0,
        "moving_time": 1500,
        "elapsed_time": 1530,
        "total_elevation_gain": 30.0,
    }


def _ok_headers(short_used: int = 5, daily_used: int = 100) -> dict[str, str]:
    return {
        "X-ReadRateLimit-Limit": "100,1000",
        "X-ReadRateLimit-Usage": f"{short_used},{daily_used}",
        "X-RateLimit-Limit": "200,2000",
        "X-RateLimit-Usage": "10,200",
    }


@respx.mock
def test_run_cold_start_paginates_until_empty(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    page1 = [_activity(1, "2024-04-15T07:00:00Z"), _activity(2, "2024-04-16T07:00:00Z")]
    page2 = [_activity(3, "2024-04-17T07:00:00Z")]
    respx.get(_ACTIVITIES_URL).mock(
        side_effect=[
            httpx.Response(200, headers=_ok_headers(), json=page1),
            httpx.Response(200, headers=_ok_headers(), json=page2),
            httpx.Response(200, headers=_ok_headers(), json=[]),
        ]
    )

    conn = store.connect(db_path)
    try:
        result = run(_token(), settings, conn)
        cursor = store.get_cursor(conn, athlete_id=42)
        count = store.count_activities(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.inserted_or_updated == 3
    assert result.pages_fetched == 3
    assert result.rate_limited is False
    assert result.error is None
    assert result.deleted == 0
    assert cursor is not None
    assert cursor.last_activity_start == "2024-04-17T07:00:00Z"
    assert cursor.last_synced_at is not None
    assert count == 3


@respx.mock
def test_run_warm_start_passes_after_epoch_from_cursor(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    conn = store.connect(db_path)
    try:
        with conn:  # commit so the next connection can read it
            store.update_cursor(
                conn,
                athlete_id=42,
                last_activity_start="2024-04-15T07:00:00Z",
                last_synced_at="2024-04-15T08:00:00Z",
            )
    finally:
        conn.close()

    route = respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, headers=_ok_headers(), json=[])
    )

    conn = store.connect(db_path)
    try:
        result = run(_token(), settings, conn)
    finally:
        conn.close()

    assert result.inserted_or_updated == 0
    assert result.pages_fetched == 1
    qs = dict(httpx.QueryParams(route.calls.last.request.url.query.decode()))
    # 2024-04-15T07:00:00Z -> epoch 1713164400
    assert qs["after"] == "1713164400"


@respx.mock
def test_run_breaks_when_rate_limit_preflight_trips(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    page1 = [_activity(1, "2024-04-15T07:00:00Z")]
    page2 = [_activity(2, "2024-04-16T07:00:00Z")]
    respx.get(_ACTIVITIES_URL).mock(
        side_effect=[
            httpx.Response(200, headers=_ok_headers(short_used=50), json=page1),
            httpx.Response(200, headers=_ok_headers(short_used=99), json=page2),
            # third call should not happen — preflight after page 2 trips
            httpx.Response(200, headers=_ok_headers(), json=[]),
        ]
    )

    conn = store.connect(db_path)
    try:
        result = run(_token(), settings, conn)
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.rate_limited is True
    assert result.inserted_or_updated == 2
    assert result.pages_fetched == 2
    assert cursor is not None
    assert cursor.last_activity_start == "2024-04-16T07:00:00Z"


@respx.mock
def test_run_returns_auth_failed_on_401(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(401, headers=_ok_headers(), json={})
    )

    conn = store.connect(db_path)
    try:
        result = run(_token(), settings, conn)
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.error == "auth_failed"
    assert result.inserted_or_updated == 0
    # Cursor still advances last_synced_at, even on error.
    assert cursor is not None
    assert cursor.last_synced_at is not None


@respx.mock
def test_run_returns_http_error_on_5xx(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(500, headers=_ok_headers(), json={})
    )

    conn = store.connect(db_path)
    try:
        result = run(_token(), settings, conn)
    finally:
        conn.close()

    assert result.error == "http_error"
    assert result.rate_limited is False


@respx.mock
def test_run_returns_rate_limited_on_429(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(
            429,
            headers={
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "100,500",
            },
            json={},
        )
    )

    conn = store.connect(db_path)
    try:
        result = run(_token(), settings, conn)
    finally:
        conn.close()

    assert result.rate_limited is True
    assert result.error is None


@respx.mock
def test_run_progress_callback_invoked_per_page(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    respx.get(_ACTIVITIES_URL).mock(
        side_effect=[
            httpx.Response(200, headers=_ok_headers(), json=[_activity(1, "2024-04-15T07:00:00Z")]),
            httpx.Response(200, headers=_ok_headers(), json=[]),
        ]
    )
    seen: list[tuple[int, int]] = []

    conn = store.connect(db_path)
    try:
        run(_token(), settings, conn, on_page=lambda p, n: seen.append((p, n)))
    finally:
        conn.close()

    assert seen == [(1, 1)]


@respx.mock
def test_full_rescan_clean_run_reconciles_deletions(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    conn = store.connect(db_path)
    try:
        with conn:  # commit setup so full_rescan's connection can see it
            for activity_id in (1, 2, 3, 99):
                store.upsert_activity(
                    conn,
                    athlete_id=42,
                    activity=_activity(activity_id, "2024-04-15T07:00:00Z"),
                )
            # Other athlete must remain untouched.
            store.upsert_activity(
                conn,
                athlete_id=999,
                activity=_activity(1, "2024-04-15T07:00:00Z"),
            )
    finally:
        conn.close()

    respx.get(_ACTIVITIES_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                headers=_ok_headers(),
                json=[
                    _activity(1, "2024-04-15T07:00:00Z"),
                    _activity(2, "2024-04-16T07:00:00Z"),
                ],
            ),
            httpx.Response(200, headers=_ok_headers(), json=[]),
        ]
    )

    conn = store.connect(db_path)
    try:
        result = full_rescan(_token(), settings, conn)
        remaining_42 = {
            row["activity_id"]
            for row in conn.execute(
                "SELECT activity_id FROM activity WHERE athlete_id = ?",
                (42,),
            ).fetchall()
        }
        other_count = store.count_activities(conn, athlete_id=999)
    finally:
        conn.close()

    assert result.error is None
    assert result.rate_limited is False
    assert result.deleted == 2  # 3 and 99 were not in seen_ids
    assert remaining_42 == {1, 2}
    assert other_count == 1


@respx.mock
def test_full_rescan_truncated_does_not_reconcile(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    conn = store.connect(db_path)
    try:
        with conn:  # commit setup
            for activity_id in (1, 2, 3, 99):
                store.upsert_activity(
                    conn,
                    athlete_id=42,
                    activity=_activity(activity_id, "2024-04-15T07:00:00Z"),
                )
    finally:
        conn.close()

    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(
            200,
            headers=_ok_headers(short_used=99),  # preflight trips immediately
            json=[_activity(1, "2024-04-15T07:00:00Z")],
        )
    )

    conn = store.connect(db_path)
    try:
        result = full_rescan(_token(), settings, conn)
        count = store.count_activities(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.rate_limited is True
    assert result.deleted == 0
    assert count == 4  # nothing was reconciled away


@respx.mock
def test_full_rescan_empty_first_page_does_not_wipe_db(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    conn = store.connect(db_path)
    try:
        with conn:  # commit setup
            store.upsert_activity(
                conn,
                athlete_id=42,
                activity=_activity(1, "2024-04-15T07:00:00Z"),
            )
            store.update_cursor(
                conn,
                athlete_id=42,
                last_activity_start="2024-04-15T07:00:00Z",
                last_synced_at="2024-04-15T08:00:00Z",
            )
    finally:
        conn.close()

    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, headers=_ok_headers(), json=[])
    )

    conn = store.connect(db_path)
    try:
        result = full_rescan(_token(), settings, conn)
        count = store.count_activities(conn, athlete_id=42)
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.error is None
    assert result.rate_limited is False
    assert result.deleted == 0
    assert count == 1  # safety belt: empty first page DOES NOT trigger reconcile
    assert cursor is not None
    assert cursor.last_activity_start == "2024-04-15T07:00:00Z"


@respx.mock
def test_full_rescan_error_does_not_reconcile(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    conn = store.connect(db_path)
    try:
        with conn:  # commit setup
            store.upsert_activity(
                conn,
                athlete_id=42,
                activity=_activity(1, "2024-04-15T07:00:00Z"),
            )
            store.update_cursor(
                conn,
                athlete_id=42,
                last_activity_start="2024-04-15T07:00:00Z",
                last_synced_at="2024-04-15T08:00:00Z",
            )
    finally:
        conn.close()

    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(500, headers=_ok_headers(), json={})
    )

    conn = store.connect(db_path)
    try:
        result = full_rescan(_token(), settings, conn)
        count = store.count_activities(conn, athlete_id=42)
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.error == "http_error"
    assert result.deleted == 0
    assert count == 1
    assert cursor is not None
    assert cursor.last_activity_start == "2024-04-15T07:00:00Z"


@respx.mock
def test_full_rescan_auth_error_does_not_reconcile(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    conn = store.connect(db_path)
    try:
        with conn:
            store.upsert_activity(
                conn,
                athlete_id=42,
                activity=_activity(1, "2024-04-15T07:00:00Z"),
            )
            store.update_cursor(
                conn,
                athlete_id=42,
                last_activity_start="2024-04-15T07:00:00Z",
                last_synced_at="2024-04-15T08:00:00Z",
            )
    finally:
        conn.close()

    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(401, headers=_ok_headers(), json={})
    )

    conn = store.connect(db_path)
    try:
        result = full_rescan(_token(), settings, conn)
        count = store.count_activities(conn, athlete_id=42)
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.error == "auth_failed"
    assert result.deleted == 0
    assert count == 1
    assert cursor is not None
    assert cursor.last_activity_start == "2024-04-15T07:00:00Z"


@respx.mock
def test_full_rescan_rate_limited_429_does_not_reconcile(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    conn = store.connect(db_path)
    try:
        with conn:
            for activity_id in (1, 2):
                store.upsert_activity(
                    conn,
                    athlete_id=42,
                    activity=_activity(activity_id, "2024-04-15T07:00:00Z"),
                )
            store.update_cursor(
                conn,
                athlete_id=42,
                last_activity_start="2024-04-15T07:00:00Z",
                last_synced_at="2024-04-15T08:00:00Z",
            )
    finally:
        conn.close()

    respx.get(_ACTIVITIES_URL).mock(
        return_value=httpx.Response(
            429,
            headers={
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "100,500",
            },
            json={},
        )
    )

    conn = store.connect(db_path)
    try:
        result = full_rescan(_token(), settings, conn)
        count = store.count_activities(conn, athlete_id=42)
        cursor = store.get_cursor(conn, athlete_id=42)
    finally:
        conn.close()

    assert result.rate_limited is True
    assert result.error is None
    assert result.deleted == 0
    assert count == 2
    assert cursor is not None
    assert cursor.last_activity_start == "2024-04-15T07:00:00Z"


@respx.mock
def test_full_rescan_progress_callback_invoked_per_page(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, monkeypatch)
    respx.get(_ACTIVITIES_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                headers=_ok_headers(),
                json=[_activity(1, "2024-04-15T07:00:00Z")],
            ),
            httpx.Response(200, headers=_ok_headers(), json=[]),
        ]
    )
    seen: list[tuple[int, int]] = []

    conn = store.connect(db_path)
    try:
        full_rescan(_token(), settings, conn, on_page=lambda p, n: seen.append((p, n)))
    finally:
        conn.close()

    assert seen == [(1, 1)]
