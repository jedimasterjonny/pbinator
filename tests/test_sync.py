import pytest

from pbinator.activities_api import RateLimitUsage
from pbinator.sync import SyncResult, max_iso, would_exceed_next_call


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
