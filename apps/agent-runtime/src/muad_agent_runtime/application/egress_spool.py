"""Bounded JSONL spool for audit write failures; never carries headers or bodies."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class EgressAuditSpool:
    def __init__(self, path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        if max_bytes <= 0:
            raise ValueError("spool max_bytes must be positive")
        self._path = path
        self._max_bytes = max_bytes

    def append(self, event: dict[str, Any]) -> bool:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        encoded = payload.encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        current = self._path.stat().st_size if self._path.exists() else 0
        if current + len(encoded) > self._max_bytes:
            return False
        with self._path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return True

    def drain(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self._path.open(encoding="utf-8") as stream:
            for line in stream:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("spool record must be an object")
                rows.append(value)
        self._path.unlink()
        return rows

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
