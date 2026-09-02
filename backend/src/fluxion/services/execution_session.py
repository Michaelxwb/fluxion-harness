"""ExecutionSession（TASK-009）：run/stream 共用的 per-execution 资源准备，去重复。

run() 与 stream() 此前各自重复 start_execution → poll_revision → clone tool_runtime
→ prepare model resolver → prepare MCP → build model tools 六步；本类收口为单一
`prepare()`，统一 per-execution 资源生命周期（随 context GC，不跨执行泄漏）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fluxion.plugins.contracts import ToolDescriptor
from fluxion.runtime.context import RuntimeContext

if TYPE_CHECKING:
    from fluxion.services.runtime_app import RuntimeApplicationService
    from fluxion.services.runtime_contracts import RunRuntimeRequest


@dataclass(slots=True)
class PreparedExecution:
    """一次 execution 的准备产物（context + 模型工具定义 + 放行工具名）。"""

    context: RuntimeContext
    model_tools: list[ToolDescriptor]
    allowed_model_tools: set[str]


class ExecutionSession:
    """执行会话（TASK-009）：封装 run/stream 共用的 per-execution 准备。"""

    def __init__(self, app: RuntimeApplicationService) -> None:
        self._app = app

    async def prepare(self, request: RunRuntimeRequest) -> PreparedExecution:
        from fluxion.services.runtime_utils import _request_context

        context = await self._app._runtime.start_execution(_request_context(request))
        await self._app._resolver.poll_revision(request.tenant_id)
        context.tool_runtime = self._app._tool_runtime.clone_for_execution()
        context.model_provider_resolver = await self._app._prepare_execution_model_resolver(context)
        mcp_tool_ids = await self._app._mcp_runtime.prepare(context, context.tool_runtime)
        context.mcp_tool_ids = mcp_tool_ids
        model_tools = await self._app._model_tool_definitions(context, mcp_tool_ids)
        return PreparedExecution(
            context=context,
            model_tools=model_tools,
            allowed_model_tools={tool.name for tool in model_tools},
        )
