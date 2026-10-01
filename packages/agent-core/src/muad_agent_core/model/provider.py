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
    # 思考模式的思维链。**带 `tool_calls` 的 assistant 消息必须原样回传**，否则供应商直接
    # 拒绝（实测 deepseek-flash：`reasoning_content ... must be passed back`；纯文本
    # assistant 消息则不需要）。不带 tool_calls 的回合无需保留。
    reasoning_content: str | None = None


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
    # 思考模式的思维链，随响应一并返回（见 `ModelMessage.reasoning_content`）。
    reasoning_content: str | None = None


class ModelProvider(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class StreamingModelProvider(Protocol):
    """可选能力：流式返回文本增量，最终仍返回完整 ModelResponse。"""

    async def stream(
        self,
        request: ModelRequest,
        on_delta: DeltaCallback,
    ) -> ModelResponse: ...
