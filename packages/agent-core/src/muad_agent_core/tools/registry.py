from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from muad_contracts import DEFAULT_TOOL_RETRY_SAFETY, ToolRetrySafety

from .identity import ToolCall
from .result import ToolResult

MCP_TOOL_NAMESPACE = "mcp"
_MODEL_TOOL_NAME_MAX = 64
_MODEL_TOOL_INVALID = re.compile(r"[^A-Za-z0-9_-]")


def static_write_targets(*targets: str) -> Callable[[Mapping[str, Any]], frozenset[str]]:
    """Declare a fixed write target set for a tool definition."""
    frozen = frozenset(target for target in targets if target)
    return lambda _arguments: frozen


def _safe_model_tool_name(name: str) -> str:
    """OpenAI-compatible function names allow only letters, digits, `_` and `-`."""
    return _MODEL_TOOL_INVALID.sub("_", name)[:_MODEL_TOOL_NAME_MAX] or "tool"


class ToolCapability(StrEnum):
    SKILL_CATALOG = "skill.catalog"
    SKILL_INSTRUCTIONS = "skill.instructions"
    SKILL_RESOURCE = "skill.resource"
    SKILL_EXECUTE = "skill.execute"
    ATTACHMENT_READ = "attachment.read"
    ATTACHMENT_WRITE = "attachment.write"
    ATTACHMENT_DELIVER = "attachment.deliver"
    MCP_CALL = "mcp.call"
    WORKSPACE = "workspace"
    DRAFT = "draft"
    TASK_QUERY = "task.query"
    MEMORY = "memory"


class ToolEffect(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    EXTERNAL = "EXTERNAL"


class ToolConcurrency(StrEnum):
    """不可由模型设置的并发声明（FLOW-06）。

    `SERIAL` 是默认：未显式声明可重入、无共享可变副作用与明确资源键的工具一律串行。
    `PARALLEL_READ` 只放行**已证明只读**的处理器，且必须同时给出 `resource_key`。
    """

    SERIAL = "SERIAL"
    PARALLEL_READ = "PARALLEL_READ"


class ToolHandler(Protocol):
    """工具处理器协议。

    `call` 携带稳定调用身份（`call_id`、内部名、模型名、归一化参数）。处理器返回 typed
    `ToolResult`；失败要么抛 `ToolError`，要么在 `ToolResult.outcome` 上显式表达。
    """

    async def __call__(self, arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    effect: ToolEffect
    handler: ToolHandler | None = None
    # 结果是否允许被「大结果外置」规则截成 Artifact 引用。
    # `True`（默认）保持既有行为——顺带的大块数据（一次大查询/脚本输出）不该撑爆上下文。
    # `False` 用于**内容投递**类工具：它们的返回**本身就是给模型读的正文**，外置等于把工具
    # 废掉（2026-10-01 事故：`load_skill` 返回 8.3KB 被外置成 400 字符预览，模型实际只看到
    # 正文的 1/20，且**没有任何报错**）。注意这不是「无限直通」——调用方仍有独立上限。
    externalizable_result: bool = True
    # 并发声明（FLOW-06）：默认 SERIAL；PARALLEL_READ 需要 handler 可重入、无共享可变副作用、
    # 明确资源键且彼此独立。**不可由模型设置**：模型只能看到 name/description/input_schema。
    concurrency: ToolConcurrency = ToolConcurrency.SERIAL
    # `PARALLEL_READ` 的资源键声明（只读资源标识）：非空键相同的调用不得并发；返回空/None
    # 表示未知依赖，保持串行。`None`（未声明）同样按未知依赖处理。
    resource_key: Callable[[Mapping[str, Any]], str | None] | None = None
    # 可信重试安全性（design §3.6「重试与外部副作用」）：**Owner 侧声明**，默认 NON_RETRYABLE
    # ——未声明即未知，未知不得自动重做外部副作用。MCP `readOnlyHint` 之类提示字段**不在**
    # ToolDefinition 上、也不参与本值：目录 effect（可能由提示归一化而来）不等于重试安全。
    # 消费方在操作提交时把它冻进 execution policy（`operations.reserve_operation`）。
    retry_safety: ToolRetrySafety = DEFAULT_TOOL_RETRY_SAFETY
    #: Provider-safe function name (OpenAI-compatible `[A-Za-z0-9_-]{1,64}`).
    #: Registry/audit still use `name`; `ToolRegistry.model_tools()` fills this.
    model_name: str = ""
    #: 冻结执行模式（SYNC/ASYNC）；仅对绑定单一 Skill 的工具声明，prepared 事件直接复制。
    execution_mode: str | None = None
    #: 装配期能力声明；提示词承诺必须能在已注册工具里找到对应能力。
    capabilities: frozenset[ToolCapability] = frozenset()
    #: Host-owned write targets for this call. The resolver sees the post-hook arguments;
    #: an empty result falls back to the tool's own namespace, never a global wildcard.
    write_targets: Callable[[Mapping[str, Any]], frozenset[str]] | None = None
    #: Optional host-owned resolver for a canonical business operation key across entry points.
    write_operation_key: Callable[[Mapping[str, Any]], str | None] | None = None


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
        self._model_names: dict[str, str] = {}
        self._model_name_for: dict[str, str] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._items:
            raise ToolAlreadyRegisteredError(definition.name)
        self._items[definition.name] = definition
        model_name = self._allocate_model_name(definition.name)
        self._model_names[model_name] = definition.name
        self._model_name_for[definition.name] = model_name

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._items[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def get_by_model_name(self, model_name: str) -> ToolDefinition:
        """Resolve the provider-safe name back to the registry definition."""
        try:
            return self._items[self._model_names[model_name]]
        except KeyError as exc:
            raise ToolNotFoundError(model_name) from exc

    def model_name_for(self, name: str) -> str:
        """Provider-facing name for a registry name; unknown names keep their own name."""
        return self._model_name_for.get(name, name)

    def list(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._items.values())

    def model_tools(self) -> tuple[ToolDefinition, ...]:
        """Definitions with a provider-safe `model_name` for the model request."""
        return tuple(
            replace(definition, model_name=self._model_name_for[definition.name])
            for definition in self._items.values()
        )

    def _allocate_model_name(self, name: str) -> str:
        candidate = _safe_model_tool_name(name)
        if candidate not in self._model_names:
            return candidate
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        base = candidate[: _MODEL_TOOL_NAME_MAX - len(digest) - 2]
        candidate = f"{base}__{digest}"
        counter = 2
        while candidate in self._model_names:
            suffix = f"__{digest}{counter}"
            candidate = f"{candidate[: _MODEL_TOOL_NAME_MAX - len(suffix)]}{suffix}"
            counter += 1
        return candidate

    @staticmethod
    def namespaced(server: str, tool: str) -> str:
        if not server or not tool:
            raise ValueError("mcp namespaced tool requires non-empty server and tool")
        return f"{MCP_TOOL_NAMESPACE}::{server}::{tool}"
