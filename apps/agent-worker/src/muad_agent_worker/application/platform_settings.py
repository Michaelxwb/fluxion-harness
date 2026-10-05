"""平台设置快照 → 强类型设置文档（Worker 侧唯一解析缝；TASK-006）。

非法文档 ⇒ 明确失败（统一错误码），绝不静默回退到过期默认值（RULE-06）。
"""

from __future__ import annotations

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import (
    PlatformSettings,
    PlatformSettingsError,
    parse_platform_settings,
)

from .ports import PlatformSettingsSnapshot


def resolve_platform_settings(
    snapshot: PlatformSettingsSnapshot, *, batch_platform_limit: int | None = None
) -> PlatformSettings:
    try:
        return parse_platform_settings(snapshot.settings, batch_platform_limit=batch_platform_limit)
    except PlatformSettingsError as exc:
        raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR)) from exc
