from math import isfinite
from re import fullmatch
from typing import Self

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SharedSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", allow_inf_nan=False, hide_input_in_errors=True,
    )

    env: str = "dev"
    log_dir: str = "./.data/logs"
    log_level: str = "INFO"
    default_tenant_id: str = "default"
    api_messages_file: str = "./config/api-messages.yaml"

    database_url: str | None = Field(default=None, repr=False)
    redis_url: str | None = Field(default=None, repr=False)
    internal_service_token: str | None = Field(default=None, repr=False)
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

    runtime_execution_concurrency: int = Field(default=8, ge=1, le=256)
    worker_execution_concurrency: int = Field(default=4, ge=1, le=64)
    gateway_max_concurrent_handlers: int = Field(default=8, ge=1)
    gateway_max_pending_handlers: int = Field(default=256, ge=1)
    gateway_max_pending_per_route: int = Field(default=8, ge=1)
    gateway_max_pending_commands: int = Field(default=32, ge=1)
    gateway_max_sse_subscribers: int = Field(default=1100, ge=1, le=100000)
    runtime_max_sse_subscribers: int = Field(default=1100, ge=1, le=100000)

    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=0, ge=0)
    db_pool_timeout_sec: float = Field(default=5.0, gt=0)
    redis_max_connections: int = Field(default=50, ge=1)

    # Pools are separate per process. Deployment-wide budgets belong to the capacity profile.
    control_http_max_connections: int = Field(default=32, ge=1)
    control_http_max_keepalive: int = Field(default=16, ge=0)
    stream_http_max_connections: int = Field(default=1100, ge=1)
    stream_http_max_keepalive: int = Field(default=64, ge=0)
    model_http_max_connections: int = Field(default=64, ge=1)
    model_http_max_keepalive: int = Field(default=32, ge=0)
    mcp_http_max_connections: int = Field(default=16, ge=1)
    mcp_http_max_keepalive: int = Field(default=8, ge=0)
    mcp_max_inflight: int = Field(default=8, ge=1)
    # 业务出站池按目标并发定容：单 Pod 的 Gateway 每承载一个用户就要一条到 Runtime 的连接
    # （建会话/提交消息），池小于并发时多出的请求只能排队，池等待超时会让正常突发变成 500。
    egress_http_max_connections: int = Field(default=1024, ge=1)
    egress_http_max_keepalive: int = Field(default=256, ge=0)
    egress_max_inflight: int = Field(default=8, ge=1)
    upload_http_max_connections: int = Field(default=8, ge=1)
    upload_http_max_keepalive: int = Field(default=4, ge=0)

    http_connect_timeout_sec: float = Field(default=5.0, gt=0)
    http_pool_timeout_sec: float = Field(default=2.0, gt=0)
    control_http_read_timeout_sec: float = Field(default=10.0, gt=0)
    stream_http_read_timeout_sec: float = Field(default=45.0, gt=0)
    model_http_read_timeout_sec: float = Field(default=60.0, gt=0)
    upload_http_read_timeout_sec: float = Field(default=60.0, gt=0)
    mcp_http_read_timeout_sec: float = Field(default=30.0, gt=0)
    egress_http_read_timeout_sec: float = Field(default=30.0, gt=0)

    run_lease_sec: int = Field(default=60, ge=1)
    run_heartbeat_sec: int = Field(default=20, ge=1)
    run_reaper_interval_sec: int = Field(default=30, ge=1)
    run_event_heartbeat_sec: int = Field(default=15, ge=1)
    run_event_fallback_poll_sec: float = Field(default=5.0, gt=0)
    model_call_lease_sec: int = Field(default=60, ge=1)
    model_call_heartbeat_sec: int = Field(default=20, ge=1)
    stream_delta_flush_interval_ms: int = Field(default=50, ge=10, le=500)
    stream_delta_flush_bytes: int = Field(default=4096, ge=1024, le=65536)
    sse_event_max_bytes: int = Field(default=262144, ge=1)
    sse_subscriber_buffer_bytes: int = Field(default=1048576, ge=1)
    sse_total_buffer_bytes: int = Field(default=67108864, ge=1)

    chat_auth_recheck_sec: float = Field(default=15.0, gt=0, le=60)
    chat_exchange_max_body_bytes: int = Field(default=4096, ge=1)
    chat_exchange_per_ip_per_minute: int = Field(default=6000, ge=1)
    chat_message_hard_max_bytes: int = Field(default=1048576, ge=1)
    chat_upload_hard_max_bytes: int = Field(default=52428800, ge=1)

    channel_delivery_concurrency: int = Field(default=4, ge=1)
    channel_delivery_timeout_sec: float = Field(default=5.0, gt=0)
    channel_delivery_lease_sec: float = Field(default=30.0, gt=0)
    channel_delivery_poll_sec: float = Field(default=1.0, gt=0)
    channel_delivery_batch_size: int = Field(default=32, ge=1)
    channel_delivery_retry_base_sec: float = Field(default=1.0, gt=0)
    channel_delivery_retry_cap_sec: float = Field(default=30.0, gt=0)
    channel_delivery_max_attempts: int = Field(default=20, ge=1)

    task_lease_sec: int = Field(default=60, ge=1)
    task_heartbeat_sec: int = Field(default=20, ge=1)
    task_cancel_check_sec: int = Field(default=2, ge=1)
    worker_poll_interval_sec: int = Field(default=5, ge=1)
    batch_platform_limit: int = Field(default=16, ge=1)
    scheduler_poll_interval_sec: int = Field(default=10, ge=1)
    scheduler_batch_size: int = Field(default=100, ge=1)
    task_deadline_sweep_interval_sec: int = Field(default=30, ge=1)
    delivery_poll_interval_sec: int = Field(default=5, ge=1)
    delivery_batch_size: int = Field(default=20, ge=1)
    # Technical dispatcher settings are frozen at service startup, never model controlled.
    async_tool_dispatch_timeout_sec: float = Field(default=5, gt=0)
    async_tool_dispatch_lease_sec: float = Field(default=30, gt=5)
    async_tool_dispatch_poll_sec: float = Field(default=1, gt=0)
    async_tool_dispatch_batch_size: int = Field(default=32, ge=1, le=1000)
    async_tool_dispatch_retry_base_sec: float = Field(default=1, gt=0)
    async_tool_dispatch_retry_cap_sec: float = Field(default=30, gt=0)
    async_tool_dispatch_max_attempts: int = Field(default=20, ge=1)
    # 入口快照节拍（`BotSnapshotCache` 的拉取间隔）：默认与模块常量 `BOT_SNAPSHOT_POLL_SEC` 同值，
    # 验收栈注入小值，让刚建的 WEBCHAT 入口在可接受等待内进入网关快照（生产行为不变）。
    bot_snapshot_poll_sec: float = Field(default=30.0, gt=0)

    @field_validator("*", mode="before")
    @classmethod
    def _strict_resource_numbers(cls, value: object, info: ValidationInfo) -> object:
        if info.field_name is None:
            return value
        kind = cls.model_fields[info.field_name].annotation
        if kind not in (int, float):
            return value
        if isinstance(value, bool):
            raise ValueError("resource budgets must not be booleans")
        if kind is int:
            if isinstance(value, int):
                return value
            if isinstance(value, str) and fullmatch(r"[+-]?[0-9]+", value):
                return int(value)
            raise ValueError("resource budget must be an integer")
        if isinstance(value, (int, float, str)):
            try:
                number = float(value)
            except (ValueError, OverflowError) as exc:
                raise ValueError("resource budget must be a finite number") from exc
            if isfinite(number):
                return number
        raise ValueError("resource budget must be a finite number")

    @model_validator(mode="after")
    def _validate_resource_budgets(self) -> Self:
        self._validate_lease_budgets()
        self._validate_http_budgets()
        self._validate_stream_budgets()
        self._validate_delivery_budgets()
        return self

    def _validate_lease_budgets(self) -> None:
        for prefix in ("run", "task", "model_call"):
            if getattr(self, f"{prefix}_lease_sec") < 3 * getattr(self, f"{prefix}_heartbeat_sec"):
                raise ValueError(f"{prefix}_lease_sec must cover at least 3 * {prefix}_heartbeat_sec")

    def _validate_http_budgets(self) -> None:
        for prefix in ("control", "stream", "model", "mcp", "egress", "upload"):
            maximum: int = getattr(self, f"{prefix}_http_max_connections")
            if getattr(self, f"{prefix}_http_max_keepalive") > maximum:
                raise ValueError(f"{prefix}_http_max_keepalive must not exceed {prefix}_http_max_connections")
        # 模型池必须覆盖单进程执行并发：每个在飞执行至多占一条模型连接。池小于并发时，多出来的
        # 调用只能排队，池等待（`http_pool_timeout_sec`，默认 2s）会把正常并发误判成模型不可用。
        if self.model_http_max_connections < self.runtime_execution_concurrency:
            raise ValueError(
                "model_http_max_connections must cover runtime_execution_concurrency"
            )

    def _validate_stream_budgets(self) -> None:
        if self.stream_http_read_timeout_sec < 3 * self.run_event_heartbeat_sec:
            raise ValueError("stream_http_read_timeout_sec must cover at least 3 * run_event_heartbeat_sec")
        if self.sse_subscriber_buffer_bytes < 2 * self.sse_event_max_bytes:
            raise ValueError("sse_subscriber_buffer_bytes must cover at least 2 * sse_event_max_bytes")
        if self.sse_total_buffer_bytes < self.sse_subscriber_buffer_bytes:
            raise ValueError("sse_total_buffer_bytes must cover sse_subscriber_buffer_bytes")

    def _validate_delivery_budgets(self) -> None:
        for prefix in ("channel_delivery", "async_tool_dispatch"):
            if getattr(self, f"{prefix}_timeout_sec") >= getattr(self, f"{prefix}_lease_sec"):
                raise ValueError(f"{prefix}_timeout_sec must be less than {prefix}_lease_sec")
            if getattr(self, f"{prefix}_retry_base_sec") > getattr(self, f"{prefix}_retry_cap_sec"):
                raise ValueError(f"{prefix}_retry_base_sec must not exceed {prefix}_retry_cap_sec")

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required but not configured")
        return self.database_url

    def require_redis_url(self) -> str:
        if not self.redis_url:
            raise RuntimeError("REDIS_URL is required but not configured")
        return self.redis_url
