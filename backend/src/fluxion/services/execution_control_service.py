"""ExecutionControlService：单 Pod 执行控制编排（设计 §11）。

拼装三件套：PG `runtime_executions`（事实源）+ `ActiveExecutionRegistry`
（本实例立即取消）+ `CancellationToken`（执行边界协作取消）。
Redis 加速与跨 Pod 在 TASK-006；本任务只做单 Pod 完整语义。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fluxion.registry import AuditRecord, ChannelRegistryStore
from fluxion.registry.execution_control import ActiveExecutionExists, ExecutionRecord
from fluxion.runtime.active_execution_registry import ActiveExecutionRegistry
from fluxion.runtime.cancellation import CancellationToken
from fluxion.runtime.context import RuntimeContext
from fluxion.services.runtime_contracts import (
    CancelExecutionResult,
    ExecutionState,
    ExecutionTerminalState,
)


class SessionBusyError(RuntimeError):
    """同一 Session 已有 active execution（§4.4，一次只跑一个）。"""

    code = "session_busy"

    def __init__(self, session_id: str) -> None:
        super().__init__(f"当前会话仍有任务在运行（session={session_id}），请等待或使用 /stop")
        self.session_id = session_id


class ExecutionControlService:
    """执行控制编排（无状态服务 + Pod 级 registry 引用）。"""

    def __init__(
        self,
        store: ChannelRegistryStore,
        *,
        service_instance_id: str,
        clock: Callable[[], datetime] | None = None,
        active_registry: ActiveExecutionRegistry | None = None,
    ) -> None:
        self._store = store
        self._service_instance_id = service_instance_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._active = active_registry or ActiveExecutionRegistry()

    @property
    def active_registry(self) -> ActiveExecutionRegistry:
        return self._active

    async def get(
        self, tenant_id: str, execution_id: str
    ) -> ExecutionRecord | None:
        return await self._store.get_execution(tenant_id=tenant_id, execution_id=execution_id)

    async def get_active_for_session(
        self, tenant_id: str, platform_user_id: str, agent_id: str, session_id: str
    ) -> ExecutionRecord | None:
        return await self._store.get_active_execution_for_session(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
            session_id=session_id,
        )

    async def has_active_execution(self, head: object) -> bool:
        """`/new` 冲突探针（SessionActivityProbe 协议：读 active 记录）。"""
        tenant_id = getattr(head, "tenant_id", "")
        platform_user_id = getattr(head, "platform_user_id", "")
        agent_id = getattr(head, "agent_id", "")
        session_id = getattr(head, "active_session_id", "")
        if not session_id:
            return False
        active = await self.get_active_for_session(
            tenant_id, platform_user_id, agent_id, session_id
        )
        return active is not None

    async def begin_execution(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        execution_id: str,
        request_id: str,
        trace_id: str,
        requested_skill_id: str | None,
        task: asyncio.Task[object],
        context: RuntimeContext,
    ) -> CancellationToken:
        """创建控制记录并注册本实例取消句柄（run/stream 起始调用）。

        task 必须由调用方在 shield 之外捕获传入（shield 内 current_task()
        是内层任务，注册它会导致取消打空）。
        Session 已有 active → SessionBusyError（调用方转 session_busy）。
        """
        now = self._clock()
        token = CancellationToken()
        record = ExecutionRecord(
            execution_id=execution_id,
            tenant_id=tenant_id,
            platform_user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=ExecutionState.CREATED.value,
            request_id=request_id,
            trace_id=trace_id,
            owner_instance_id=self._service_instance_id,
            requested_skill_id=requested_skill_id,
            started_at=now,
            cancel_requested_at=None,
            cancel_reason=None,
            finished_at=None,
            error_code=None,
            revision=0,
        )
        try:
            await self._store.create_execution(record)
        except ActiveExecutionExists as exc:
            raise SessionBusyError(session_id) from exc
        self._active.register(execution_id, task, token)
        context.cancellation = token
        await self._store.mark_execution_running(tenant_id=tenant_id, execution_id=execution_id)
        return token

    async def end_execution(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        terminal: ExecutionTerminalState | None,
        context: RuntimeContext,
    ) -> None:
        """结算终态（幂等）并注销本实例句柄。清理失败只记录，不覆盖业务终态。

        终态为 cancelled 时追加 execution.cancelled 审计（§22.1，用记录自带的
        request/trace 关联，不含业务载荷）。
        """
        state = terminal.value if terminal is not None else ExecutionState.FAILED.value
        try:
            finished = await self._store.finish_execution(
                tenant_id=tenant_id,
                execution_id=execution_id,
                state=state,
                now=self._clock(),
            )
        except Exception as exc:  # noqa: BLE001 -- 清理失败只记录
            context.emit("execution.cleanup_error", {"error": f"{type(exc).__name__}: {exc}"})
            finished = None
        finally:
            self._active.unregister(execution_id)
        if finished is not None and finished.state == ExecutionState.CANCELLED.value:
            await self._store.append_audit(
                AuditRecord(
                    audit_id=f"audit_{uuid4().hex}",
                    tenant_id=finished.tenant_id,
                    actor_id=finished.platform_user_id,
                    request_id=finished.request_id,
                    action="execution.cancelled",
                    target_type="execution",
                    target_id=finished.execution_id,
                    before=None,
                    after={"session_id": finished.session_id},
                    created_at=self._clock(),
                    trace_id=finished.trace_id,
                )
            )

    async def request_cancel(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        agent_id: str,
        session_id: str,
        reason: str = "stop_requested",
    ) -> CancelExecutionResult:
        """`/stop` 持久化语义：RUNNING→CANCELLING + 本实例立即取消。

        单 Pod：owner 恒为本实例（多 Pod 路由在 TASK-006）。
        """
        active = await self.get_active_for_session(
            tenant_id, platform_user_id, agent_id, session_id
        )
        if active is None:
            return CancelExecutionResult(code="nothing_to_stop")
        if active.state == ExecutionState.CANCELLING.value:
            return CancelExecutionResult(code="stop_already_requested")
        if active.state != ExecutionState.RUNNING.value:
            return CancelExecutionResult(code="nothing_to_stop")
        cancelled = await self._store.request_execution_cancel(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
            session_id=session_id,
            reason=reason,
            now=self._clock(),
        )
        if cancelled is None:
            # CAS 竞争落败：重读定结果（完成 vs 已取消）。
            active = await self.get_active_for_session(
                tenant_id, platform_user_id, agent_id, session_id
            )
            if active is None:
                return CancelExecutionResult(code="nothing_to_stop")
            if active.state == ExecutionState.CANCELLING.value:
                return CancelExecutionResult(code="stop_already_requested")
            return CancelExecutionResult(code="nothing_to_stop")
        self._active.cancel_local(cancelled.execution_id, reason)
        return CancelExecutionResult(code="stop_requested", execution_id=cancelled.execution_id)

    async def reconcile_orphans(self, *, older_than_seconds: int = 300) -> list[str]:
        """认领超期 CANCELLING 孤儿（owner 消失）：结算为 CANCELLED。

        只有 cancel_requested_at 足够久远的才认领——活着的 owner 仍在推进
        的取消不受影响。返回被认领的 execution_id 列表。
        """
        from datetime import timedelta

        cutoff = self._clock() - timedelta(seconds=older_than_seconds)
        stale = await self._store.list_stale_cancelling(before=cutoff)
        reconciled: list[str] = []
        for record in stale:
            finished = await self._store.finish_execution(
                tenant_id=record.tenant_id,
                execution_id=record.execution_id,
                state=ExecutionState.CANCELLED.value,
                now=self._clock(),
                error_code="orphan_reconciled",
            )
            if finished is not None and finished.state == ExecutionState.CANCELLED.value:
                reconciled.append(record.execution_id)
        return reconciled
