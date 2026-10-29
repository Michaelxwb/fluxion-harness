"""Webchat 会话上限与保留期：设置解析与纯判定（不触库）。

两个产品语义分开：

- **数量上限**（`webchat.max_conversations_per_entry`）：每个
  `(tenant, user, agent, 入口)` 的**活跃**会话上限。达上限时自动归档最旧且无活跃 Run 的
  会话；历史会话仍可在保留期内查看/继续。
- **保留期**（`webchat.conversation_retention_days`）：以 `last_message_at`（缺失回落
  `create_time`）为准；超期的会话不出现在历史列表、不可继续，返回稳定码
  `CONVERSATION_EXPIRED`。`0` 是显式语义「永不过期」。

解析只取本模块需要的两个字段，避免在受理热路径重解析整份平台设置；坏值仍走 contracts
的 typed schema 显式失败（不静默回落默认）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import PlatformSettingsError, parse_platform_settings

#: 本模块从 `webchat` 分组读取的字段（其余字段由各自业务边界解析）。
POLICY_FIELDS = ("max_conversations_per_entry", "conversation_retention_days")


@dataclass(frozen=True, slots=True)
class ConversationPolicy:
    """一份设置快照里的会话策略；范围校验由 contracts schema 唯一持有。"""

    max_conversations_per_entry: int
    conversation_retention_days: int

    def __post_init__(self) -> None:
        if isinstance(self.max_conversations_per_entry, bool) or not (
            1 <= self.max_conversations_per_entry <= 1000
        ):
            raise ValueError("max_conversations_per_entry must be within [1, 1000]")
        if isinstance(self.conversation_retention_days, bool) or not (
            0 <= self.conversation_retention_days <= 3650
        ):
            raise ValueError("conversation_retention_days must be within [0, 3650]")

    def retention_cutoff(self, now: datetime) -> datetime | None:
        """保留期下界；`0` 天是显式的「永不过期」，返回 `None`。"""
        if self.conversation_retention_days == 0:
            return None
        return now - timedelta(days=self.conversation_retention_days)

    def is_expired(
        self,
        *,
        last_message_at: datetime | None,
        create_time: datetime,
        now: datetime,
    ) -> bool:
        """会话是否已超过保留期（最近一次真实可见活动为准）。"""
        cutoff = self.retention_cutoff(now)
        if cutoff is None:
            return False
        return (last_message_at or create_time) < cutoff


def assert_conversation_live(
    policy: ConversationPolicy,
    *,
    last_message_at: datetime | None,
    create_time: datetime,
    now: datetime,
) -> None:
    """超期会话的唯一公开裁决：历史读取与续聊都抛同一稳定码。"""
    if policy.is_expired(
        last_message_at=last_message_at, create_time=create_time, now=now
    ):
        raise AppError(ErrorCode.CONVERSATION_EXPIRED)


def conversation_policy_from_settings(
    settings: Mapping[str, Any] | None,
) -> ConversationPolicy:
    """平台设置文档 → 会话策略；坏文档显式失败（`PLATFORM_SETTINGS_UNAVAILABLE`）。"""
    webchat: Any = None if settings is None else settings.get("webchat")
    if webchat is None:
        section: dict[str, Any] = {}
    elif isinstance(webchat, Mapping):
        section = {key: webchat[key] for key in POLICY_FIELDS if key in webchat}
    else:
        raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)
    try:
        parsed = parse_platform_settings({"webchat": section}).webchat
    except PlatformSettingsError as exc:
        raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE) from exc
    return ConversationPolicy(
        max_conversations_per_entry=parsed.max_conversations_per_entry,
        conversation_retention_days=parsed.conversation_retention_days,
    )
