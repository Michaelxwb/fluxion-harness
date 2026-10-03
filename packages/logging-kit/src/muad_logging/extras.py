"""从 `LogRecord` 提取调用方通过 `extra={...}` 附加的结构化字段。

标准库把 `extra` 的每个键**直接挂成 record 属性**，所以「哪些键是调用方给的」只能靠
**减去标准属性**得到 —— 没有别的标记可用。

**为什么必须是这一条通道**：全仓 14 处调用方（`scheduler/service.py` 的 `schedule_id`、
`run_service.py` 的 `run_id`、`memory_tools.py` 的 `memory_key`、`metrics.py` 的 `metric`…）
写的都是 `extra={...}`；此前 formatter 与 RedactionFilter 只认 `record.fields`，于是这些字段
**静默消失**，而脱敏边界挂在一个没人用的通道上（2026-10-03 review）。
"""

from __future__ import annotations

import logging
from typing import Any

#: 标准 LogRecord 自带的属性（`message`/`asctime` 由 Formatter 事后补上，故一并排除）
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime"}

#: JSON 载荷的保留键：调用方的字段**不得覆写**它们
RESERVED_KEYS = frozenset({"timestamp", "service", "level", "logger", "message", "exception"})


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """调用方附加的结构化字段（`extra={...}`），已剔除标准属性与保留键。"""
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_ATTRS and key not in RESERVED_KEYS
    }
