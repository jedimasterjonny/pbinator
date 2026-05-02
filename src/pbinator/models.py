"""SQLModel table definitions for pbinator.

Schema-only: column declarations, primary keys, foreign keys, indexes.
Queries and helpers live in ``store.py``.
"""

from sqlalchemy import ForeignKeyConstraint, Index, column
from sqlmodel import Field, SQLModel


class Activity(SQLModel, table=True):
    """One Strava SummaryActivity, scoped per athlete."""

    __tablename__ = "activity"

    athlete_id: int = Field(primary_key=True)
    activity_id: int = Field(primary_key=True)
    sport_type: str
    start_date: str
    start_date_local: str | None = None
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    total_elev_gain_m: float
    name: str
    raw_json: str
    fetched_at: str
    best_efforts_fetched_at: str | None = None

    __table_args__ = (
        Index(
            "idx_activity_athlete_date",
            "athlete_id",
            column("start_date").desc(),
        ),
        Index(
            "idx_activity_athlete_sport",
            "athlete_id",
            "sport_type",
            column("start_date").desc(),
        ),
    )


class BestEffort(SQLModel, table=True):
    """One ``best_effort`` row from a Strava DetailedActivity."""

    __tablename__ = "best_effort"

    athlete_id: int = Field(primary_key=True)
    activity_id: int = Field(primary_key=True)
    distance_label: str = Field(primary_key=True)
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    start_date: str

    __table_args__ = (
        ForeignKeyConstraint(
            ["athlete_id", "activity_id"],
            ["activity.athlete_id", "activity.activity_id"],
            ondelete="CASCADE",
        ),
        Index(
            "idx_best_effort_athlete_label_time",
            "athlete_id",
            "distance_label",
            "moving_time_s",
        ),
    )


class SyncCursor(SQLModel, table=True):
    """Per-athlete sync progress.

    ``last_activity_start`` is the ISO-UTC ``start_date`` of the newest
    activity stored. ``last_synced_at`` is when the most recent sync
    finished (success, partial, or error).
    """

    __tablename__ = "sync_cursor"

    athlete_id: int = Field(primary_key=True)
    last_activity_start: str | None = None
    last_synced_at: str | None = None
