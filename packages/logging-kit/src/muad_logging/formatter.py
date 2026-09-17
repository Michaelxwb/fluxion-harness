from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .context import get_log_context

_RESERVED_KEYS = frozenset({"timestamp", "service", "level", "logger", "message", "exception"})


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
            if key not in _RESERVED_KEYS:
                payload[key] = value
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key not in _RESERVED_KEYS:
                    payload[key] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
