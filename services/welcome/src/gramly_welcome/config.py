from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WELCOME_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://gramly_welcome:gramly_welcome@postgres:5432/gramly_welcome"
    interface_webhook_secret: str = ""
    max_webhook_body_bytes: int = Field(default=1_048_576, ge=1024, le=4_194_304)
    lease_seconds: int = Field(default=60, ge=15, le=600)
    worker_batch_size: int = Field(default=50, ge=1, le=500)
    worker_poll_seconds: float = Field(default=0.25, ge=0.05, le=10)
    max_attempts: int = Field(default=12, ge=1, le=100)
    raw_event_retention_days: int = Field(default=7, ge=1, le=30)
    technical_retention_days: int = Field(default=180, ge=7, le=730)
    token_encryption_keys: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"


@lru_cache
def get_settings() -> Settings:
    return Settings()
