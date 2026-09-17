from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MessageSpec:
    code: str
    http_status: int
    messages: dict[str, str]


class MessageCatalog:
    def __init__(self, file_path: str | Path, default_locale: str = "zh-CN") -> None:
        self.file_path = Path(file_path)
        self.default_locale = default_locale
        self._specs: dict[str, MessageSpec] = {}
        self.reload()

    def reload(self) -> None:
        raw = yaml.safe_load(self.file_path.read_text(encoding="utf-8")) or {}
        codes = raw.get("codes", {})
        specs: dict[str, MessageSpec] = {}
        for code, item in codes.items():
            specs[str(code)] = MessageSpec(
                code=str(code),
                http_status=int(item.get("http_status", 500)),
                messages={str(k): str(v) for k, v in (item.get("messages") or {}).items()},
            )
        self._specs = specs

    def spec(self, code: str) -> MessageSpec:
        return self._specs.get(code) or self._specs["COMMON_INTERNAL_ERROR"]

    def message(self, code: str, locale: str, args: dict[str, Any] | None = None) -> str:
        spec = self.spec(code)
        template = (
            spec.messages.get(locale)
            or spec.messages.get(self.default_locale)
            or spec.messages.get("en-US")
            or code
        )
        try:
            return template.format(**(args or {}))
        except Exception:
            return template
