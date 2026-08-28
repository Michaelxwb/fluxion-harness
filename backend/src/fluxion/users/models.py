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
    learning_enabled: bool = Field(
        default=True,
        title="自动学习开关",
        description="关闭后 learned 写入（ProfileAttribute/Memory）一律拒绝（停学 gate）",
    )
    personalization_policy_ref: str | None = Field(
        default=None,
        title="个性化策略",
        description="PersonalizationPolicy 引用（id@version）；Phase 2 深做，契约先锁",
    )


class ProfileAttribute(SensitiveSpecModel):
    """行级用户画像属性（P1C-09 / closure §2.3.2）。

    与 BasicProfile（UserProfileSpec）分层：结构化基础字段走 Profile，
    learned/可扩展属性走行级 attribute，每行自带 provenance。
    """

    tenant_id: str = Field(min_length=1, max_length=128)
    platform_user_id: str = Field(min_length=1, max_length=128)
    key: str = Field(
        min_length=1,
        max_length=128,
        title="属性键",
        description="dot-path（如 output.report_style）",
    )
    value: str = Field(min_length=1, max_length=4096, title="属性值")
    source: Literal["explicit", "conversation", "inference"] = Field(title="来源")
    source_ref: str | None = Field(default=None, max_length=255, title="溯源坐标")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, title="置信度")
    is_explicit: bool = Field(default=True, title="用户显式给出")
    user_editable: bool = Field(default=True, title="可否用户编辑")
    visibility: Literal["private", "agent"] = Field(default="private", title="可见性")
    valid_from: str | None = Field(default=None, max_length=64)
    valid_until: str | None = Field(default=None, max_length=64)
    superseded_by: str | None = Field(default=None, max_length=128)


GRANTED_SCOPES = Literal["invoke", "manage"]
