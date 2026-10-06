from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .context import get_log_context
from .extras import RESERVED_KEYS, extra_fields


def _json_default(value: object) -> str:
    """不可序列化的值 → **类型占位符**（刻意不写 repr）。

    第三方 logger 会把对象随手塞进 `extra`：websockets 的
    `self.logger.info("connection open")` 就带着连接对象（LoggerAdapter 的 extra），于是整条日志
    抛 `TypeError: Object of type ServerConnection is not JSON serializable` —— 既丢了这条日志，又
    往 stderr 刷一坨 "--- Logging error ---"（2026-10-06 在 CI 日志里实测）。

    兜底不能退回 `str(value)`：对象 repr 里可能带请求头 / 凭据，而脱敏滤镜只看得见**键名命中**的
    字段与字符串值，repr 是它看不到的通道。记类型名足以让问题可见，又不会漏内容。
    """
    return f"<unserializable:{type(value).__name__}>"


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": self.service_name,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in get_log_context().items():
            if key not in RESERVED_KEYS:
                payload[key] = value
        # 调用方的 `extra={...}` 覆盖 log context（更贴近这一次事件），但同样不得覆写保留键
        for key, value in extra_fields(record).items():
            payload[key] = value
        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=_json_default
        )
