"""Runtime configuration via environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # CSL bulk feed
    csl_bulk_url: str = Field(
        default=(
            "https://data.trade.gov/downloadable_consolidated_screening_list"
            "/v1/consolidated.json"
        ),
    )
    csl_refresh_seconds: int = 24 * 60 * 60
    csl_http_timeout: int = 60

    # Force bundled sample data (no network)
    use_sample_only: bool = False

    # Scoring thresholds
    match_min_score: float = 0.82
    match_fail_score: float = 0.95

    # Server
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
