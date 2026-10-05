"""平台设置的敏感键扫描与校验错误定位（design §3.4 API-02、§3.5 安全性）。

- 敏感键扫描：任何分组/嵌套键名含 `password`/`secret`/`token`/`api_key`/`dsn`/`credential`
  形状即拒绝（`PLATFORM_SETTINGS_SECRET_REJECTED`），**不回显**触发的键或值。
- 校验错误定位：把 `muad_contracts.platform_settings` 的 `PlatformSettingsError` 文本映射为
  `details[].path`，供前端把错误落到具体字段行（前端 design E-11）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import PlatformSettingsError

#: 命中任一子串即判定为敏感键（归一化去掉 `_`/`-` 后比对）。
SECRET_MARKERS = ("password", "secret", "token", "apikey", "dsn", "credential")

#: 错误文本前置的字段路径（如 `task.max_attempts`、`compaction.summary.model_ref`）。
_PATH_PREFIX = re.compile(r"^([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)")
_UNKNOWN_GROUP = re.compile(r"未知分组:\s*([^,\s]+)")
_UNKNOWN_KEY = re.compile(r"^([a-z_][a-z0-9_]*) 含未知配置键:\s*(.+)$")


def _normalize(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "")


def _scan(document: Any, path: str) -> str | None:
    if not isinstance(document, Mapping):
        return None
    for key, value in document.items():
        child = f"{path}.{key}" if path else str(key)
        if any(marker in _normalize(str(key)) for marker in SECRET_MARKERS):
            return child
        nested = _scan(value, child)
        if nested is not None:
            return nested
    return None


def find_secret_key(document: Mapping[str, Any]) -> str | None:
    """返回首个敏感键的路径（仅用于内部判断与日志；不外显给客户端）。"""
    return _scan(document, "")


def reject_secret_keys(document: Mapping[str, Any]) -> None:
    """提交体含敏感键即拒绝；错误体不含触发的键名或值。"""
    if find_secret_key(document) is not None:
        raise AppError(ErrorCode.PLATFORM_SETTINGS_SECRET_REJECTED)


def validation_detail(error: PlatformSettingsError) -> dict[str, str]:
    """从校验异常文本提取 `{path, message}`；无法定位时 `path` 为空串。"""
    message = str(error)
    return {"path": _extract_path(message), "message": message}


def _extract_path(message: str) -> str:
    group = _UNKNOWN_GROUP.search(message)
    if group is not None:
        return group.group(1)
    unknown_key = _UNKNOWN_KEY.match(message)
    if unknown_key is not None:
        return f"{unknown_key.group(1)}.{unknown_key.group(2)}"
    prefix = _PATH_PREFIX.match(message)
    return prefix.group(1) if prefix is not None else ""
