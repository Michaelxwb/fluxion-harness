from pydantic_settings import BaseSettings, SettingsConfigDict


class SharedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_dir: str = "./.data/logs"
    log_level: str = "INFO"
    default_locale: str = "zh-CN"
    default_tenant_id: str = "default"
    api_messages_file: str = "./config/api-messages.yaml"

    database_url: str | None = None
    redis_url: str | None = None
    secret_provider: str = "env"
    internal_service_token: str | None = None

    artifact_root: str = "./.data/artifacts"
    mcp_max_tools_per_server: int = 200
    skill_cache_root: str = "./.data/skill-cache"
    migrations_dir: str = "./migrations/versions"

    console_platform_url: str = "http://127.0.0.1:8000"
    agent_runtime_url: str = "http://127.0.0.1:8001"
    agent_worker_url: str = "http://127.0.0.1:8002"
    im_gateway_url: str = "http://127.0.0.1:8003"

    run_lease_sec: int = 60
    run_heartbeat_sec: int = 20
    run_reaper_interval_sec: int = 30
    run_event_heartbeat_sec: int = 15

    task_lease_sec: int = 60
    task_heartbeat_sec: int = 20
    task_default_deadline_hours: int = 24
    worker_poll_interval_sec: int = 5
    task_max_attempts: int = 3
    scheduler_poll_interval_sec: int = 10
    misfire_grace_sec: int = 60
    delivery_poll_interval_sec: int = 5
    delivery_max_attempts: int = 5

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required but not configured")
        return self.database_url

    def require_redis_url(self) -> str:
        if not self.redis_url:
            raise RuntimeError("REDIS_URL is required but not configured")
        return self.redis_url
