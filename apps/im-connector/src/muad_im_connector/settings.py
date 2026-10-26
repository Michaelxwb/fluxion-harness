"""Connector 进程设置：只声明本运行时角色新增的旋钮。

共享基础设施（Redis/Console/租户/artifact/企微 WS）仍来自 `SharedSettings` 这一处来源；
这里不复制既有默认值，避免出现第二份"真相"。
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConnectorSettings(BaseSettings):
    """Connector 专有配置（env 名即字段名大写）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    #: 进程身份：lease owner 与 outbound consumer 标识。默认 hostname:pid，便于定位接管者。
    connector_owner_id: str = Field(default="")
    #: 本 Connector 接管的通道（逗号分隔）。默认只接管企微；WebChat 由 Gateway 直接处理。
    connector_handled_channels: str = Field(default="WECOM")
    #: bot lease 的存活时间与续租节拍。续租必须显著早于过期，避免抖动即掉线。
    connector_lease_ttl_sec: float = Field(default=30.0, ge=5.0)
    connector_lease_renew_sec: float = Field(default=10.0, gt=0.0)
    #: 入站 Stream 分片数与单 Stream 上限（与 design §3.3.1 同口径）。
    connector_inbound_shards: int = Field(default=32, ge=1)
    connector_inbound_stream_maxlen: int = Field(default=100_000, ge=1)
    #: 入站标记超过该时长仍是 RECEIVED（XADD 前崩溃）即显式记 LOST；不假装成功。
    connector_inbound_lost_after_sec: int = Field(default=60, ge=1)
    #: 验收故障注入：在写 RECEIVED 之后、XADD 之前暂停（0 = 关闭）。生产必须为 0。
    connector_test_pause_before_xadd_sec: float = Field(default=0.0, ge=0.0)
    #: 出站命令最大 age：超过即显式 EXPIRED，不重放远古命令。
    connector_command_max_age_sec: int = Field(default=600, ge=1)
    #: 出站单次 XREAD 批量。
    connector_outbound_batch: int = Field(default=64, ge=1, le=1000)
    #: 单条命令执行失败重试次数（之后写 FAILED + dead-letter，不无限重试）。
    connector_command_max_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _validate(self) -> ConnectorSettings:
        if self.connector_lease_renew_sec >= self.connector_lease_ttl_sec / 2:
            raise ValueError("CONNECTOR_LEASE_RENEW_SEC must be < lease TTL / 2")
        if not self.connector_handled_channels.strip():
            raise ValueError("CONNECTOR_HANDLED_CHANNELS must not be empty")
        return self

    @property
    def owner_id(self) -> str:
        if self.connector_owner_id.strip():
            return self.connector_owner_id.strip()
        return f"{socket.gethostname()}:{os.getpid()}"

    @property
    def handled_channels(self) -> frozenset[str]:
        return frozenset(
            item.strip().upper()
            for item in self.connector_handled_channels.split(",")
            if item.strip()
        )


@lru_cache(maxsize=1)
def connector_settings() -> ConnectorSettings:
    return ConnectorSettings()


def reset_connector_settings_cache() -> None:
    """测试专用：环境变更后重建缓存。"""
    connector_settings.cache_clear()
