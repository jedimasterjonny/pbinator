from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    strava_client_id: str
    strava_client_secret: SecretStr
    strava_redirect_uri: str = "http://localhost:8501/"
    pbinator_db_path: Path = Path("data/pbinator.db")
