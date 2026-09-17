from __future__ import annotations

from .base import PlatformAdapter


class PlatformAdapterNotFound(LookupError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"platform adapter not found: {key}")


class PlatformAdapterAlreadyRegistered(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"platform adapter already registered: {key}")


class PlatformAdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        if adapter.key in self._items:
            raise PlatformAdapterAlreadyRegistered(adapter.key)
        self._items[adapter.key] = adapter

    def get(self, key: str) -> PlatformAdapter:
        try:
            return self._items[key]
        except KeyError as exc:
            raise PlatformAdapterNotFound(key) from exc

    def list(self) -> tuple[PlatformAdapter, ...]:
        return tuple(self._items.values())
