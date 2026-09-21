from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from ..tools.registry import ToolDefinition

DeltaCallback = Callable[[str], Awaitable[None]]


class ModelRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: ModelRole
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model_id: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    finish_reason: str
    tool_calls: tuple[ModelToolCall, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelProvider(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class StreamingModelProvider(Protocol):
    """可选能力：流式返回文本增量，最终仍返回完整 ModelResponse。"""

    async def stream(
        self,
        request: ModelRequest,
        on_delta: DeltaCallback,
    ) -> ModelResponse: ...
