from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_logger = logging.getLogger(__name__)

_REQUIRED_LOCALES = ("zh-CN", "en-US")
_FALLBACK_CODE = "COMMON_INTERNAL_ERROR"


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
        codes = raw.get("codes")
        if not isinstance(codes, dict) or not codes:
            raise ValueError(f"invalid message catalog: {self.file_path}")

        specs: dict[str, MessageSpec] = {}
        for code, item in codes.items():
            if not isinstance(item, dict):
                raise ValueError(f"invalid entry for code {code}: {item!r}")
            http_status = item.get("http_status")
            if not isinstance(http_status, int) or not 100 <= http_status <= 599:
                raise ValueError(f"invalid http_status for code {code}: {http_status!r}")
            messages = item.get("messages") or {}
            if not isinstance(messages, dict):
                raise ValueError(f"invalid messages for code {code}")
            normalized = {str(key): str(value) for key, value in messages.items()}
            for locale in _REQUIRED_LOCALES:
                if not normalized.get(locale):
                    raise ValueError(f"missing {locale} message for code {code}")
            specs[str(code)] = MessageSpec(
                code=str(code),
                http_status=http_status,
                messages=normalized,
            )

        if _FALLBACK_CODE not in specs:
            raise ValueError(f"message catalog must define {_FALLBACK_CODE}")
        self._specs = specs

    def has(self, code: str) -> bool:
        return code in self._specs

    def codes(self) -> frozenset[str]:
        return frozenset(self._specs)

    def spec(self, code: str) -> MessageSpec:
        spec = self._specs.get(code)
        if spec is not None:
            return spec
        _logger.warning("unknown error code %s, falling back to %s", code, _FALLBACK_CODE)
        return self._specs[_FALLBACK_CODE]

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
        except (KeyError, IndexError, ValueError):
            _logger.warning("message format failed for code %s", code, exc_info=True)
            return template
