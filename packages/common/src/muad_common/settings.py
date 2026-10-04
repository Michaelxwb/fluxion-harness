from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SharedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_dir: str = "./.data/logs"
    log_level: str = "INFO"
    default_locale: str = "zh-CN"
    default_tenant_id: str = "default"
    api_messages_file: str = "./config/api-messages.yaml"
    # Agent 的 `current_time` 工具所用的 IANA 时区名。调度侧的时区是**每条 schedule 必填**的，
    # 而"现在几点"（相对时间推算的前提）没有 per-run 来源，故给一个平台级默认。
    # 非法值在 `resolve_zone()` 处显式报错，不静默回退。
    default_timezone: str = "Asia/Shanghai"

    database_url: str | None = None
    redis_url: str | None = None
    internal_service_token: str | None = None
    channel_probe_url: str | None = None
    # WeCom SDK 连接地址与 CA：仅用于本地真实协议探针（默认空 = SDK 官方地址 + certifi 校验）
    wecom_ws_url: str | None = None
    wecom_ws_ca_file: str | None = None

    artifact_root: str = "./.data/artifacts"
    # 产物保留期（天）：`cleanup-artifacts` 按它算"多久以前的算过期"。做成配置项而不是写死在
    # CLI 里，是因为这是**运维策略**（不同部署的盘与合规要求不同），而 CLI 的 `--retention-days`
    # 只是本次覆盖。判定用的是既有的 `runtime.artifact.create_time`，不新增列。
    artifact_retention_days: int = 30
    mcp_max_tools_per_server: int = 200
    skill_cache_root: str = "./.data/skill-cache"
    migrations_dir: str = "./migrations/versions"

    console_platform_url: str = "http://127.0.0.1:8000"
    agent_runtime_url: str = "http://127.0.0.1:8001"
    agent_worker_url: str = "http://127.0.0.1:8002"
    im_gateway_url: str = "http://127.0.0.1:8003"
    # 活跃执行的计时节拍：IM 里每帧状态都会让客户端**整帧重排并滚动到底**，1 秒一帧实测
    # 让对话框滚动明显发涩，产品口径改为 5 秒（帧数降到 1/5）。验收栈按「可注入节拍一律注入
    # 小值」的规矩注入 1s（`tests/acceptance/im_gateway/environment.py`），生产默认值由
    # `tests/gateway/test_execution_progress.py` 的毫秒级用例钉住。
    im_progress_interval_sec: float = Field(default=5.0, ge=1.0)
    im_progress_updates_per_second: float = Field(default=10.0, ge=1.0)

    run_lease_sec: int = 60
    run_heartbeat_sec: int = 20
    run_reaper_interval_sec: int = 30
    run_event_heartbeat_sec: int = 15

    task_lease_sec: int = 60
    task_heartbeat_sec: int = 20
    task_cancel_check_sec: int = 2
    task_default_deadline_hours: int = 24
    worker_poll_interval_sec: int = 5
    task_max_attempts: int = 3
    batch_max_concurrency: int = 8
    batch_platform_limit: int = 16
    scheduler_poll_interval_sec: int = 10
    scheduler_batch_size: int = 100
    task_deadline_sweep_interval_sec: int = 30
    misfire_grace_sec: int = 60
    delivery_poll_interval_sec: int = 5
    delivery_batch_size: int = 20
    delivery_max_attempts: int = 5
    # 投递重试退避窗口 = 本值 × 2^delivery_attempts（见 delivery/service.py 的 SQL 表达式）。
    # **生产默认 5**（→ 5/10/20/40…）。做成设置项而不写死，是为了让验收能用同一条代码路径、
    # 同一套断言验证「退避按几何级数增长 + 耗尽后写审计」，而不必真的等满 10+20+40+80 秒。
    delivery_backoff_base_sec: int = 5

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required but not configured")
        return self.database_url

    def require_redis_url(self) -> str:
        if not self.redis_url:
            raise RuntimeError("REDIS_URL is required but not configured")
        return self.redis_url
