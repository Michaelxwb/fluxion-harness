from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .formatter import JsonLogFormatter
from .handler import DailyServiceFileHandler
from .redaction import RedactionFilter

_configured: set[str] = set()


def configure_logging(
    service_name: str,
    log_dir: str | Path | None = None,
    level: str | None = None,
    console: bool = True,
) -> None:
    if service_name in _configured:
        return

    default_log_dir = os.getenv("LOG_DIR", "./.data/logs")
    log_dir = Path(log_dir or default_log_dir)
    default_level = os.getenv("LOG_LEVEL", "INFO")
    level_name = (level or default_level).upper()
    resolved_level = getattr(logging, level_name, None)
    if not isinstance(resolved_level, int):
        raise ValueError(f"invalid log level: {level_name}")
    log_level: int = resolved_level

    formatter = JsonLogFormatter(service_name)
    redaction = RedactionFilter()

    root = logging.getLogger()
    root.setLevel(log_level)

    file_handler = DailyServiceFileHandler(log_dir, service_name)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)
    root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redaction)
        root.addHandler(console_handler)

    for noisy_logger in ("uvicorn.access", "httpcore", "httpx"):
        logging.getLogger(noisy_logger).setLevel(max(log_level, logging.WARNING))

    _configured.add(service_name)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
