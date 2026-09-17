from pydantic_settings import BaseSettings, SettingsConfigDict


class SharedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_dir: str = "./.data/logs"
    log_level: str = "INFO"
    default_locale: str = "zh-CN"
    api_messages_file: str = "./config/api-messages.yaml"

    database_url: str = "postgresql+asyncpg://muad:muad@localhost:5432/muad"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: str = "./.data/artifacts"
    skill_cache_root: str = "./.data/skill-cache"
