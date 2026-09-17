from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

MCP_TOOL_NAMESPACE = "mcp"


class ToolEffect(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    EXTERNAL = "EXTERNAL"


class ToolHandler(Protocol):
    async def __call__(self, arguments: Mapping[str, Any]) -> str: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    effect: ToolEffect
    handler: ToolHandler | None = None


class ToolNotFoundError(LookupError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"tool not found: {name}")


class ToolAlreadyRegisteredError(ValueError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"tool already registered: {name}")


class ToolRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._items:
            raise ToolAlreadyRegisteredError(definition.name)
        self._items[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._items[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def list(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._items.values())

    @staticmethod
    def namespaced(server: str, tool: str) -> str:
        if not server or not tool:
            raise ValueError("mcp namespaced tool requires non-empty server and tool")
        return f"{MCP_TOOL_NAMESPACE}::{server}::{tool}"
