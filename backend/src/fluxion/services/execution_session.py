"""ExecutionSession（TASK-009）：run/stream 共用的 per-execution 资源准备，去重复。

run() 与 stream() 此前各自重复 start_execution → poll_revision → clone tool_runtime
→ prepare model resolver → prepare MCP → build model tools 六步；本类收口为单一
`prepare()`，统一 per-execution 资源生命周期（随 context GC，不跨执行泄漏）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fluxion.plugins.contracts import ToolDescriptor
from fluxion.runtime.context import RuntimeContext
from fluxion.services.runtime_contracts import (
    FINALIZE_BUDGET_MS,
    ExecutionTerminalState,
    resolve_terminal_state,
)

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
        self._lock = asyncio.Lock()
        self._terminal: ExecutionTerminalState | None = None
        self._context: RuntimeContext | None = None

    @property
    def terminal(self) -> ExecutionTerminalState | None:
        """已确定的业务终态（未结算为 None）。"""
        return self._terminal

    @property
    def context(self) -> RuntimeContext | None:
        """prepare 已创建的 context（失败路径也有，用于清理后观测）。"""
        return self._context

    async def prepare(self, request: RunRuntimeRequest) -> PreparedExecution:
        from fluxion.services.runtime_utils import _request_context

        context = await self._app._runtime.start_execution(_request_context(request))
        # TASK-014（ADR-A014 §3）：context 创建即获得所有权；后续步骤失败也清理。
        self._context = context
        try:
            await self._app._resolver.poll_revision(request.tenant_id)
            context.tool_runtime = self._app._tool_runtime.clone_for_execution()
            context.model_provider_resolver = (
                await self._app._prepare_execution_model_resolver(context)
            )
            mcp_tool_ids = await self._app._mcp_runtime.prepare(context, context.tool_runtime)
            context.mcp_tool_ids = mcp_tool_ids
            model_tools = await self._app._model_tool_definitions(context, mcp_tool_ids)
        except BaseException as exc:
            await self.finalize(context, error=exc)
            raise
        return PreparedExecution(
            context=context,
            model_tools=model_tools,
            allowed_model_tools={tool.name for tool in model_tools},
        )

    async def finalize(
        self, context: RuntimeContext, *, error: BaseException | None = None
    ) -> ExecutionTerminalState:
        """有界 finalize_once（ADR-A014 §2/§6）：幂等且并发安全。

        成功/失败都只结算一次；重复调用返回首次终态。清理本身有界
        （FINALIZE_BUDGET_MS）；取消必须重新传播；清理失败只记录，
        永不改变已确定的业务终态。
        """
        async with self._lock:
            if self._terminal is not None:
                return self._terminal
            state = resolve_terminal_state(error)
            cleanup_error: str | None = None
            try:
                await asyncio.wait_for(
                    self._app._runtime.finish_execution(context),
                    timeout=FINALIZE_BUDGET_MS / 1000,
                )
            except TimeoutError:
                cleanup_error = (
                    f"finalize_budget_exceeded: 清理超 {FINALIZE_BUDGET_MS}ms 预算"
                )
            except asyncio.CancelledError:
                context.emit("execution.cleanup_error", {"error": "finalize_cancelled"})
                raise
            except Exception as exc:  # noqa: BLE001 -- 清理失败只记录，不覆盖业务终态
                cleanup_error = f"{type(exc).__name__}: {exc}"
            if cleanup_error is not None:
                context.emit("execution.cleanup_error", {"error": cleanup_error})
            self._terminal = state
            return state
