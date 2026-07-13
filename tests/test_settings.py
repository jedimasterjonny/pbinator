from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from pbinator.settings import Settings


@pytest.fixture
def isolated_settings_cls(
    monkeypatch: pytest.MonkeyPatch,
) -> type[Settings]:
    """Settings subclass that ignores any local .env file during tests.

    Returns:
        A Settings subclass configured to read from environment variables only.
    """

    class _IsolatedSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=None,
            env_file_encoding="utf-8",
            extra="forbid",
        )

    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("STRAVA_REDIRECT_URI", raising=False)
    monkeypatch.delenv("PBINATOR_DB_PATH", raising=False)
    monkeypatch.delenv("WHOOP_CSV_PATH", raising=False)
    monkeypatch.delenv("GARMIN_CSV_PATH", raising=False)
    return _IsolatedSettings


def test_strava_credentials_load_from_env(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")

    s = isolated_settings_cls()

    assert s.strava_client_id == "client-123"
    assert s.strava_client_secret.get_secret_value() == "secret-xyz"


def test_redirect_uri_defaults_to_localhost(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")

    s = isolated_settings_cls()

    assert s.strava_redirect_uri == "http://localhost:8501/"


def test_redirect_uri_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:9000/")

    s = isolated_settings_cls()

    assert s.strava_redirect_uri == "http://localhost:9000/"


def test_missing_credentials_raises_validation_error(
    isolated_settings_cls: type[Settings],
) -> None:
    with pytest.raises(ValidationError):
        isolated_settings_cls()


def test_db_path_defaults_to_data_pbinator_db(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")

    s = isolated_settings_cls()

    assert s.pbinator_db_path == Path("data/pbinator.db")


def test_db_path_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("PBINATOR_DB_PATH", "/tmp/custom.db")  # noqa: S108 — test fixture path, not real I/O

    s = isolated_settings_cls()

    assert s.pbinator_db_path == Path("/tmp/custom.db")  # noqa: S108 — same as above


def test_whoop_csv_path_defaults_to_data_workouts_csv(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")

    s = isolated_settings_cls()

    assert s.whoop_csv_path == Path("data/workouts.csv")


def test_whoop_csv_path_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("WHOOP_CSV_PATH", "/tmp/custom-whoop.csv")  # noqa: S108 — test fixture path

    s = isolated_settings_cls()

    assert s.whoop_csv_path == Path("/tmp/custom-whoop.csv")  # noqa: S108 — same as above


def test_garmin_csv_path_defaults_to_data_activities_csv(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")

    s = isolated_settings_cls()

    assert s.garmin_csv_path == Path("data/Activities.csv")


def test_garmin_csv_path_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, isolated_settings_cls: type[Settings]
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("GARMIN_CSV_PATH", "/tmp/custom-garmin.csv")  # noqa: S108 — test fixture path

    s = isolated_settings_cls()

    assert s.garmin_csv_path == Path("/tmp/custom-garmin.csv")  # noqa: S108 — same as above
