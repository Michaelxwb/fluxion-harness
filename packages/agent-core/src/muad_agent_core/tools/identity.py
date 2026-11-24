"""调用身份：模型协议名与注册表名在同一个对象上，不靠字符串猜测。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from muad_contracts import DEFAULT_TOOL_RETRY_SAFETY, ToolRetrySafety

if TYPE_CHECKING:
    from ..model.provider import ModelMessage


@dataclass(frozen=True, slots=True)
class ToolCall:
    """一次工具调用的稳定身份。

    `name` 是 registry / audit / checkpoint / recovery 使用的内部名；`model_name` 只用于
    回放 provider 历史。两者必须同时携带，任何下游都不得从其中一个推断另一个。
    """

    call_id: str
    name: str
    model_name: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """一个 assistant 回合：原始消息（provider 历史）与解析后的内部调用身份。"""

    message: ModelMessage
    calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedCall:
    """外部执行前持久化的调用事实；只带脱敏/有界参数，不存原始参数。"""

    call: ToolCall
    args_hash: str
    args_preview: Mapping[str, Any]
    source: str
    skill_id: UUID | None = None
    skill_key: str | None = None
    tool_name: str | None = None
    execution_mode: str | None = None
    retry_safety: ToolRetrySafety = DEFAULT_TOOL_RETRY_SAFETY
    effect: str = "EXTERNAL"
