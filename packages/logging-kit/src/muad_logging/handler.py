from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TextIO


class DailyServiceFileHandler(logging.Handler):
    # 保存到 {base_dir}/{service_name}/{YYYY-MM-DD}.log
    def __init__(self, base_dir: str | Path, service_name: str, encoding: str = "utf-8") -> None:
        super().__init__()
        self.base_dir = Path(base_dir)
        self.service_name = service_name
        self.encoding = encoding
        self._date = ""
        self._stream: TextIO | None = None

    def _today(self) -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d")

    def _ensure_stream(self) -> TextIO:
        today = self._today()
        if self._stream is None or self._date != today:
            if self._stream is not None:
                self._stream.flush()
                self._stream.close()
            service_dir = self.base_dir / self.service_name
            service_dir.mkdir(parents=True, exist_ok=True)
            self._stream = (service_dir / f"{today}.log").open(
                "a", encoding=self.encoding, buffering=1
            )
            self._date = today
        return self._stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = self._ensure_stream()
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
                self._stream.close()
            finally:
                self._stream = None
        super().close()
