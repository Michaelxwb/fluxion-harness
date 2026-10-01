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
    """工具处理器协议。

    `call_id` 是**模型本次调用**的 id（`ModelToolCall.id`），**必须**由调用方传入：
    它是「一次 Run 内同一工具被调用多次」时区分各次调用的唯一标识。缺了它，下游只能拿
    工具名兜底，而 `runtime.tool_call_audit` 上的唯一约束正是 `(run_id, tool_call_id)`
    —— 于是同名工具第二次调用必定插入失败（历史缺陷，见 2026-10-01 排查）。
    叶子处理器可以不使用它，但**必须**声明并透传，否则包装层拿不到。
    """

    async def __call__(self, arguments: Mapping[str, Any], *, call_id: str) -> str: ...


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
