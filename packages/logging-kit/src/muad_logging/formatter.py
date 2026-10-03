from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .context import get_log_context
from .extras import RESERVED_KEYS, extra_fields


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
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
