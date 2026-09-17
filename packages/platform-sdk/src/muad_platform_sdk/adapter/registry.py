from .base import PlatformAdapter


class PlatformAdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        if adapter.key in self._items:
            raise ValueError(f"duplicate adapter key: {adapter.key}")
        self._items[adapter.key] = adapter

    def get(self, key: str) -> PlatformAdapter:
        return self._items[key]
