"""Runtime configuration. All secrets come from the environment and stay server-side."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LARDER_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://larder:larder@db:5432/larder"
    data_dir: Path = Path("/data")
    # Session/cookie settings
    secret_key: str = Field(default="change-me", description="Used to encrypt backups when no backup key is set")
    session_days: int = 30
    cookie_secure: bool = True
    public_url: str = "https://larder.local"
    timezone: str = "Europe/London"

    # AI provider (server-side only). Empty key disables AI features gracefully.
    ai_provider: str = "anthropic"  # anthropic | none
    anthropic_api_key: str = Field(default="", repr=False)
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"
    ai_server_fallbacks: bool = True
    ai_timeout_seconds: float = 120.0

    # External product data
    off_enabled: bool = True
    off_base_url: str = "https://world.openfoodfacts.org"
    off_user_agent: str = "Larder/1.0 (self-hosted household app)"
    pet_off_base_url: str = "https://world.openpetfoodfacts.org"

    # Limits
    max_upload_mb: int = 15
    max_fetch_mb: int = 5
    fetch_timeout_seconds: float = 15.0
    import_rate_per_minute: int = 20
    login_rate_per_minute: int = 10

    # Backups
    backup_enabled: bool = True
    backup_hour_utc: int = 3
    backup_keep: int = 14
    backup_passphrase: str = Field(default="", repr=False)

    # Run background jobs inside the request (tests / tiny installs without a worker)
    inline_jobs: bool = False

    # Testing hooks
    allow_private_fetch: bool = False  # never enable in production; used by tests only

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def receipts_dir(self) -> Path:
        return self.data_dir / "receipts"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider != "none" and bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
