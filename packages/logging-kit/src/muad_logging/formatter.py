from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from .context import get_log_context
from .extras import RESERVED_KEYS, extra_fields

#: 字符串字段需要加引号的字符：空白、竖线、引号（避免破坏 ` | key=value` 的可读边界）。
_QUOTE_FIELD = re.compile(r"[\s|\"]")


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


def _format_field(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False) if _QUOTE_FIELD.search(value) else value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        except (TypeError, ValueError):
            return f"<unserializable:{type(value).__name__}>"
    if value is None:
        return "null"
    if isinstance(value, (int, float, bool)):
        return str(value)
    return f"<unserializable:{type(value).__name__}>"


class TextLogFormatter(logging.Formatter):
    """单行文本日志，字段以 `key=value` 追加在消息之后。

    示例：
    `2026-10-29 20:19:43.553 +0800 [INFO] muad-agent-worker muad_agent_worker.worker.service
      execution_slots_configured | slots=8`

    - 关联字段只打印非空值；`trace_id/request_id/tenant_id` 等空串不再刷屏
    - `extra={...}` 仍通过 `RedactionFilter` 先脱敏，再进入文本
    - 异常/堆栈保持换行在原消息之后
    """

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    @staticmethod
    def _timestamp(record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).astimezone()
        return stamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " " + stamp.strftime("%z")

    def format(self, record: logging.LogRecord) -> str:
        line = (
            f"{self._timestamp(record)} [{record.levelname}] {self.service_name} "
            f"{record.name} {record.getMessage()}"
        )
        fields: dict[str, Any] = {
            key: value
            for key, value in get_log_context().items()
            if key not in RESERVED_KEYS and value not in (None, "")
        }
        # 调用方的 `extra={...}` 覆盖 log context（更贴近这一次事件），但同样不得覆写保留键
        for key, value in extra_fields(record).items():
            if key in RESERVED_KEYS:
                continue
            if value in (None, ""):
                fields.pop(key, None)
                continue
            fields[key] = value
        if fields:
            line += " | " + " ".join(f"{key}={_format_field(value)}" for key, value in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line
