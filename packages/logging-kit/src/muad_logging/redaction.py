from __future__ import annotations

import logging
import re
from typing import Any

from .extras import extra_fields

REDACTED = "***"

#: 敏感键名。**末尾那个裸 `token` 是有意加的**（2026-10-03）：签名取件直链把令牌放在 URL 的
#: `?token=…` 里（TASK-008 的产物取件），而访问日志/代理/浏览器历史都会原样带上整个 URL。
#: 只覆盖 `access_token`/`refresh_token`/`id_token` 这类**限定名**等于给"换个参数名就漏"留门。
_SENSITIVE_KEY = (
    r"authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token"
    r"|id[_-]?token|secret|password|passwd|credential|private[_-]?key|token"
)
_SENSITIVE_KEY_PATTERN = re.compile(_SENSITIVE_KEY, re.IGNORECASE)
_KEY_VALUE_PATTERN = re.compile(
    rf"(?P<key>{_SENSITIVE_KEY})(?P<sep>\s*[:=]\s*)(?P<value>[^\s,;&'\"]+)",
    re.IGNORECASE,
)
_AUTH_SCHEME_PATTERN = re.compile(r"\b(?P<scheme>bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def redact_text(value: str) -> str:
    value = _AUTH_SCHEME_PATTERN.sub(lambda match: f"{match.group('scheme')} {REDACTED}", value)
    return _KEY_VALUE_PATTERN.sub(lambda match: f"{match.group('key')}{match.group('sep')}{REDACTED}", value)


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if _SENSITIVE_KEY_PATTERN.search(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        # `extra={...}` 的每个键都被标准库挂成了 record 属性 —— 这里**必须**一并脱敏。
        # 它与 formatter 是同一个通道的两半：只修 formatter 那一半，等于把未脱敏的字段
        # 直接写进日志文件（2026-10-03 review）。
        for key, value in extra_fields(record).items():
            record.__dict__[key] = (
                REDACTED if _SENSITIVE_KEY_PATTERN.search(key) else redact_value(value)
            )
        return True
