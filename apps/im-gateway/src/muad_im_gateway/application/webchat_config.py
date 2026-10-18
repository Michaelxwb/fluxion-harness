"""API-19 聊天客户端非敏感配置白名单投影（design §3.4/§3.7；TASK-057）。

- **唯一来源**：业务上限/重连参数只来自 Console 权威设置快照（经 contracts schema 解析默认与
  范围，坏版本/未知键显式失败）；传输上限/心跳只来自 Gateway 启动参数（装配根注入的
  `SharedSettings` 值）。本模块不读环境、不复制默认值、不持库。
- **白名单投影**：只经 `WebchatClientConfig.from_settings` 组装浏览器需要的 5+5 个字段；
  内部地址、连接池、模型额度、密钥、保留期字段与启动硬上限**没有字段位置**（契约拒绝未知键）。
- **失败即失败**：设置源不可达/文档非法/超启动硬预算 ⇒ 明确错误（RULE-06），绝不回退默认。
- **transport_revision**：传输投影的确定性指纹（同值稳定、任一项变化即变化、纯十六进制不透明）
  —— 活跃连接据此绑定原参数，新连接重新读取有效版本。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from muad_contracts.webchat import WebchatClientConfig

from .platform_settings import resolve_platform_settings
from .ports import PlatformSettingsClient, PlatformSettingsSnapshot

#: 传输指纹长度（十六进制摘要前缀）：够区分版本，又不把任何原始内部值带进响应。
TRANSPORT_FINGERPRINT_HEX_LENGTH = 16


@dataclass(frozen=True, slots=True)
class WebchatTransportBudget:
    """Gateway 启动参数里的传输预算（`SharedSettings`，装配根注入；进程启动后固定）。"""

    sse_event_max_bytes: int
    sse_heartbeat_sec: float

    def __post_init__(self) -> None:
        if isinstance(self.sse_event_max_bytes, bool) or self.sse_event_max_bytes < 1:
            raise ValueError("sse_event_max_bytes must be a positive integer")
        if isinstance(self.sse_heartbeat_sec, bool) or self.sse_heartbeat_sec <= 0:
            raise ValueError("sse_heartbeat_sec must be a positive number")


def transport_revision(
    transport: WebchatTransportBudget,
    *,
    reconnect_base_ms: int,
    reconnect_cap_ms: int,
    reconnect_max_attempts: int,
) -> str:
    """传输投影的确定性指纹：同一组参数永远得到同一个 revision，任一项变化即变化。"""
    payload = json.dumps(
        {
            "sse_event_max_bytes": transport.sse_event_max_bytes,
            "sse_heartbeat_sec": transport.sse_heartbeat_sec,
            "reconnect_base_ms": reconnect_base_ms,
            "reconnect_cap_ms": reconnect_cap_ms,
            "reconnect_max_attempts": reconnect_max_attempts,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:TRANSPORT_FINGERPRINT_HEX_LENGTH]


def project_client_config(
    snapshot: PlatformSettingsSnapshot,
    *,
    transport: WebchatTransportBudget,
    chat_message_hard_max_bytes: int,
    chat_upload_hard_max_bytes: int,
) -> WebchatClientConfig:
    """快照 + 启动参数 → API-19 白名单投影（设置非法/超启动硬预算在此显式失败）。"""
    webchat = resolve_platform_settings(
        snapshot,
        chat_message_hard_max_bytes=chat_message_hard_max_bytes,
        chat_upload_hard_max_bytes=chat_upload_hard_max_bytes,
    ).webchat
    return WebchatClientConfig.from_settings(
        settings_revision=snapshot.revision,
        transport_revision=transport_revision(
            transport,
            reconnect_base_ms=webchat.reconnect_base_ms,
            reconnect_cap_ms=webchat.reconnect_cap_ms,
            reconnect_max_attempts=webchat.reconnect_max_attempts,
        ),
        webchat=webchat,
        sse_event_max_bytes=transport.sse_event_max_bytes,
        sse_heartbeat_sec=transport.sse_heartbeat_sec,
    )


class WebchatClientConfigService:
    """API-19 应用服务：每次页面初始化取一次有效设置快照，再投影白名单。"""

    def __init__(
        self,
        *,
        settings: PlatformSettingsClient,
        tenant_id: str,
        transport: WebchatTransportBudget,
        chat_message_hard_max_bytes: int,
        chat_upload_hard_max_bytes: int,
    ) -> None:
        self._settings = settings
        self._tenant_id = tenant_id
        self._transport = transport
        self._chat_message_hard_max_bytes = chat_message_hard_max_bytes
        self._chat_upload_hard_max_bytes = chat_upload_hard_max_bytes

    async def load(self) -> WebchatClientConfig:
        snapshot = await self._settings.fetch_snapshot(tenant_id=self._tenant_id)
        return project_client_config(
            snapshot,
            transport=self._transport,
            chat_message_hard_max_bytes=self._chat_message_hard_max_bytes,
            chat_upload_hard_max_bytes=self._chat_upload_hard_max_bytes,
        )
