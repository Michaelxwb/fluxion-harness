"""User Domain typed models（Gate 1B / TASK-U102..U105）。

ADR-011 SoT：Profile/Preference JSON 载荷一律经 typed model 校验后落库，
杜绝 spec/日志散乱键。持久化 record 数据类在 registry/user_store.py。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fluxion.resources.contracts import SensitiveSpecModel


class UserProfileSpec(SensitiveSpecModel):
    """用户画像（U102/U103）。P0 最小集；扩展走显式加字段。"""

    display_name: str = Field(min_length=1, max_length=128, title="展示名")
    bio: str = Field(default="", max_length=1024, title="简介")
    timezone: str = Field(default="Asia/Shanghai", max_length=64, title="时区")
    language: str = Field(default="zh-CN", max_length=16, title="语言")


class UserPreferenceSpec(SensitiveSpecModel):
    """偏好设置 + 个性化策略引用（U104）。"""

    theme: Literal["system", "light", "dark"] = "system"
    notification_enabled: bool = True
    personalization_policy_ref: str | None = Field(
        default=None,
        title="个性化策略",
        description="PersonalizationPolicy 引用（id@version）；Phase 2 深做，契约先锁",
    )


GRANTED_SCOPES = Literal["invoke", "manage"]
