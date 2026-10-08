from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SharedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_dir: str = "./.data/logs"
    log_level: str = "INFO"
    default_tenant_id: str = "default"
    api_messages_file: str = "./config/api-messages.yaml"

    database_url: str | None = None
    redis_url: str | None = None
    internal_service_token: str | None = None
    channel_probe_url: str | None = None
    # WeCom SDK 连接地址与 CA：仅用于本地真实协议探针（默认空 = SDK 官方地址 + certifi 校验）
    wecom_ws_url: str | None = None
    wecom_ws_ca_file: str | None = None

    artifact_root: str = "./.data/artifacts"
    skill_cache_root: str = "./.data/skill-cache"
    migrations_dir: str = "./migrations/versions"

    console_platform_url: str = "http://127.0.0.1:8000"
    agent_runtime_url: str = "http://127.0.0.1:8001"
    agent_worker_url: str = "http://127.0.0.1:8002"
    im_gateway_url: str = "http://127.0.0.1:8003"
    # 每机器人状态刷新预算（正文/最终结果不占此额度）：**服务资源预算上限**，留在环境。
    im_progress_updates_per_second: float = Field(default=10.0, ge=1.0)

    run_lease_sec: int = 60
    run_heartbeat_sec: int = 20
    run_reaper_interval_sec: int = 30
    run_event_heartbeat_sec: int = 15

    task_lease_sec: int = 60
    task_heartbeat_sec: int = 20
    task_cancel_check_sec: int = 2
    worker_poll_interval_sec: int = 5
    batch_platform_limit: int = 16
    scheduler_poll_interval_sec: int = 10
    scheduler_batch_size: int = 100
    task_deadline_sweep_interval_sec: int = 30
    delivery_poll_interval_sec: int = 5
    delivery_batch_size: int = 20
    # Technical dispatcher settings are frozen at service startup, never model controlled.
    async_tool_dispatch_timeout_sec: float = Field(default=5, gt=0)
    async_tool_dispatch_lease_sec: float = Field(default=30, gt=5)
    async_tool_dispatch_poll_sec: float = Field(default=1, gt=0)
    async_tool_dispatch_batch_size: int = Field(default=32, ge=1, le=1000)
    async_tool_dispatch_retry_base_sec: float = Field(default=1, gt=0)
    async_tool_dispatch_retry_cap_sec: float = Field(default=30, gt=0)
    async_tool_dispatch_max_attempts: int = Field(default=20, ge=1)

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required but not configured")
        return self.database_url

    def require_redis_url(self) -> str:
        if not self.redis_url:
            raise RuntimeError("REDIS_URL is required but not configured")
        return self.redis_url
