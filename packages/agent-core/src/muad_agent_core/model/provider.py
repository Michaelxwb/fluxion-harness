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
class ImagePart:
    """图像内容块。

    **承载形态取 base64 data URL**（设计 §5 R-09 的待定项在此定下）：产物落在集群内共享卷
    （RWX PVC）上，模型供应商访问不到；走预签名 URL 需要额外的对外暴露与时效管理。data URL
    是 OpenAI 兼容协议的原生形态，且与"本地不可达"这个部署事实相容。
    """

    media_type: str
    data_base64: str

    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.data_base64}"


#: 消息内容：纯文本，或内容块序列（文本块 + 图像块）。
#: 放宽成联合类型是为了让图片能进上下文；**纯文本路径的请求体必须逐字节不变**（S-03/B-06）。
ModelContent = str | tuple[str | ImagePart, ...]


def text_of(content: ModelContent) -> str:
    """取消息的纯文本视图：多模态消息丢弃图像块、拼接文本块。

    供需要"文本形态"的落库/事件路径使用（那些路径不接受内容块）。纯文本消息原样返回。
    """
    if isinstance(content, str):
        return content
    return "".join(part for part in content if isinstance(part, str))


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: ModelRole
    content: ModelContent
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
    #: 这一次请求的 I/O 超时（秒）。由调用方按**剩余总预算**算出（ADR-07：单次请求预算 =
    #: min(上限, 剩余总预算)）；None = 用 provider 建立时的默认值。provider 必须**逐请求**生效，
    #: 否则总 deadline 就约束不到正在飞的那次调用。
    timeout_sec: float | None = None


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
