"""官方企微 SDK 的日志出口：**只统一时间格式**，其余保持 SDK 原样。

SDK 不走 Python `logging`：`aibot/logger.py` 的 `DefaultLogger` 直接 `print(..., file=sys.stderr)`，
时间戳是 UTC ISO8601（`datetime.now(timezone.utc).isoformat()`）。我们的 `muad_logging` 用本地时区
`%Y-%m-%d %H:%M:%S.%f %z`；两种格式混在同一个日志文件里既难按时间排序，也对不上排障时间线。

这里复刻 SDK 的输出（stderr、`[AiBotSDK]` 前缀、级别标签、消息体与 `*args` 语义都不变），
**只把时间戳换成与 `muad_logging` 相同的格式**。
"""

from __future__ import annotations

import sys
from datetime import datetime

DEFAULT_PREFIX = "AiBotSDK"


class LocalTimeSdkLogger:
    """`WSClientOptions.logger` 的实现：接口与 `aibot.logger.DefaultLogger` 一致。"""

    def __init__(self, prefix: str = DEFAULT_PREFIX) -> None:
        self._prefix = prefix

    @staticmethod
    def _timestamp() -> str:
        """与 `muad_logging.formatter` 同格式：本地时区 + 毫秒 + `%z`。"""
        stamp = datetime.now().astimezone()
        return stamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " " + stamp.strftime("%z")

    def _emit(self, level: str, message: str, *args: object) -> None:
        print(f"{self._timestamp()} [{self._prefix}] [{level}] {message}", *args, file=sys.stderr)

    def debug(self, message: str, *args: object) -> None:
        self._emit("DEBUG", message, *args)

    def info(self, message: str, *args: object) -> None:
        self._emit("INFO", message, *args)

    def warn(self, message: str, *args: object) -> None:
        self._emit("WARN", message, *args)

    def error(self, message: str, *args: object) -> None:
        self._emit("ERROR", message, *args)
