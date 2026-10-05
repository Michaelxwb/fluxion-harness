"""平台设置快照 → 强类型文档 + 回复生命周期取值（Gateway 侧唯一解析缝；TASK-007）。

非法文档 ⇒ 明确失败（统一错误码），绝不静默回退到过期默认值（RULE-06）。解析走
`muad_contracts` 的整份文档 schema，`im.progress_interval_sec` 的下界 `ge=1.0` 因此只有
一处来源（`PROGRESS_INTERVAL_SEC` 常量已删除）。
"""

from __future__ import annotations

from dataclasses import dataclass

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import (
    PlatformSettings,
    PlatformSettingsError,
    parse_platform_settings,
)

from .ports import PlatformSettingsSnapshot


@dataclass(frozen=True, slots=True)
class ReplySettings:
    """一条入站消息的回复生命周期内固定的展示设置（design §3.1 ADR-04）。"""

    locale: str
    progress_interval_sec: float


def resolve_platform_settings(
    snapshot: PlatformSettingsSnapshot, *, batch_platform_limit: int | None = None
) -> PlatformSettings:
    try:
        return parse_platform_settings(snapshot.settings, batch_platform_limit=batch_platform_limit)
    except PlatformSettingsError as exc:
        raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR)) from exc


def resolve_reply_settings(
    snapshot: PlatformSettingsSnapshot, *, batch_platform_limit: int | None = None
) -> ReplySettings:
    """在回复生命周期开始时取到的快照里解析出这份回复要用的 locale 与节拍。"""
    settings = resolve_platform_settings(snapshot, batch_platform_limit=batch_platform_limit)
    return ReplySettings(
        locale=settings.locale.default_locale,
        progress_interval_sec=settings.im.progress_interval_sec,
    )
