from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WELCOME_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://gramly_welcome:gramly_welcome@postgres:5432/gramly_welcome"
    interface_bot_token: str = ""
    interface_bot_username: str = ""
    interface_webhook_secret: str = ""
    public_webhook_base_url: str = "https://gramly.tech/welcome/client"
    accept_webhooks: bool = True
    max_webhook_body_bytes: int = Field(default=1_048_576, ge=1024, le=4_194_304)
    lease_seconds: int = Field(default=60, ge=15, le=600)
    worker_batch_size: int = Field(default=50, ge=1, le=500)
    worker_concurrency: int = Field(default=8, ge=1, le=64)
    worker_poll_seconds: float = Field(default=0.25, ge=0.05, le=10)
    max_attempts: int = Field(default=12, ge=1, le=100)
    database_pool_size: int = Field(default=4, ge=1, le=50)
    database_max_overflow: int = Field(default=2, ge=0, le=50)
    raw_event_retention_days: int = Field(default=7, ge=1, le=30)
    technical_retention_days: int = Field(default=180, ge=7, le=730)
    token_encryption_keys: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"
    valkey_url: str = "redis://redis:6379/1"
    bot_rate_limit_per_second: int = Field(default=25, ge=1, le=30)
    chat_rate_limit_per_second: int = Field(default=1, ge=1, le=5)
    s3_endpoint_url: str = "http://minio:9000"
    s3_region_name: str = "auto"
    s3_bucket_name: str = "gramly-welcome-media"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_addressing_style: str = "path"
    media_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    mini_app_auth_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    mini_app_session_seconds: int = Field(default=43_200, ge=300, le=604_800)
    mini_app_cookie_name: str = "gramly_welcome_session"
    mini_app_cookie_secure: bool = True
    public_service_base_url: str = "https://gramly.tech/welcome"


@lru_cache
def get_settings() -> Settings:
    return Settings()
