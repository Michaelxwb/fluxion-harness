from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/isf"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    redis_url: str = "redis://localhost:6379/0"
    worker_poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 30

    # V1.7 Reference Defaults (deployment-overridable, range-checked below).
    human_wait_default_timeout_hours: int = Field(default=24, ge=1, le=720)
    delivery_max_attempts: int = Field(default=5, ge=1, le=10)
    delivery_backoff_base_seconds: int = Field(default=30, ge=1, le=300)
    channel_owner_lease_ttl_seconds: int = Field(default=30, ge=5, le=300)
    channel_owner_renew_interval_seconds: int = Field(default=10, ge=1, le=300)

    @model_validator(mode="after")
    def _check_cross_field_bounds(self) -> "Settings":
        if self.channel_owner_renew_interval_seconds >= self.channel_owner_lease_ttl_seconds:
            raise ValueError("channel_owner_renew_interval_seconds must be < channel_owner_lease_ttl_seconds")
        max_elapsed = self.delivery_backoff_base_seconds * (2**self.delivery_max_attempts - 1)
        if max_elapsed <= 0:
            raise ValueError("delivery backoff window must be positive")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
