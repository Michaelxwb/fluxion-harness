"""远端 ASYNC operation 取消：意图在事务内落库，网络在提交之后（design §3.4.5）。

`ALL_ACTIVE` 取消要终止等待外部结果的执行，而那些 operation 归 Runtime 持有（API-30）。
因此本模块把动作拆成两半：

1. 事务内（`schedule_lifecycle`）先落「已请求取消」事实与执行终态；
2. 提交之后（本模块）才发 HTTP。远端失败**只追加失败证据**，绝不回滚已停下的未来调度，
   也不假称远端副作用已回滚；没有装配取消端口时保持只有「REQUESTED」证据（待核实）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
from muad_contracts import TaskToolOperationCancelRequest, TaskToolOperationOwner
from sqlalchemy.ext.asyncio import AsyncSession

from .schedule_lifecycle import RemoteCancel
from .task_events import EntityType, TaskEventSeed, TaskEventType, append_events

logger = logging.getLogger(__name__)

CANCEL_PATH = "/internal/tool-operations/cancel"


class ToolCancelError(Exception):
    """远端取消失败（传输失败或服务端拒绝）；`code` 是稳定错误码。"""

    def __init__(self, message: str, *, code: str = "REMOTE_CANCEL_FAILED") -> None:
        super().__init__(message)
        self.code = code


class ToolOperationCanceller(Protocol):
    """按调用身份取消 Task-owned operation（Runtime API-30）。"""

    async def cancel(self, *, tenant_id: str, item: RemoteCancel) -> None: ...


@dataclass(frozen=True, slots=True)
class RemoteCancelReport:
    confirmed: int = 0
    failed: int = 0


class RuntimeToolOperationCanceller:
    """Runtime `/internal/tool-operations/cancel` 的 Worker 侧客户端。"""

    def __init__(
        self, base_url: str, client: httpx.AsyncClient, *, service_token: str | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token

    async def cancel(self, *, tenant_id: str, item: RemoteCancel) -> None:
        headers = {"X-Tenant-Id": tenant_id, "X-Actor-User-Id": str(item.actor_user_id)}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        body = TaskToolOperationCancelRequest(
            owner=TaskToolOperationOwner(task_execution_id=item.execution_id),
            source_tool_call_id=item.source_tool_call_id,
        )
        try:
            response = await self._client.post(
                f"{self._base_url}{CANCEL_PATH}",
                json=body.model_dump(mode="json"),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ToolCancelError(str(exc), code="RESOLVE_UNAVAILABLE") from exc
        if response.status_code >= 400:
            raise ToolCancelError(
                f"runtime cancel rejected with {response.status_code}",
                code=_error_code(response),
            )


def _error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "REMOTE_CANCEL_FAILED"
    code = payload.get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, str) and code else "REMOTE_CANCEL_FAILED"


async def dispatch_remote_cancels(
    session: AsyncSession,
    *,
    tenant_id: str,
    items: tuple[RemoteCancel, ...],
    canceller: ToolOperationCanceller | None,
    now: datetime,
) -> RemoteCancelReport:
    """提交后执行远端取消并写结果证据；本函数自己提交证据事务。

    没有端口（未装配）时什么都不写：只有事务内的 `REQUESTED` 证据，表示待核实。
    """
    if not items or canceller is None:
        return RemoteCancelReport()
    confirmed = failed = 0
    for item in items:
        try:
            await canceller.cancel(tenant_id=tenant_id, item=item)
        except Exception as exc:  # noqa: BLE001 - 远端失败必须降级为证据，不回滚本地取消
            failed += 1
            code = exc.code if isinstance(exc, ToolCancelError) else "REMOTE_CANCEL_FAILED"
            logger.warning(
                "schedule_remote_cancel_failed",
                extra={
                    "tenant_id": tenant_id,
                    "execution_id": str(item.execution_id),
                    "operation_id": str(item.operation_id),
                    "error_code": code,
                },
            )
            await _evidence(
                session,
                tenant_id=tenant_id,
                item=item,
                event_type=TaskEventType.FAILED,
                payload={"state": "FAILED", "error_code": code},
                now=now,
            )
        else:
            confirmed += 1
            await _evidence(
                session,
                tenant_id=tenant_id,
                item=item,
                event_type=TaskEventType.CANCELLED,
                payload={"state": "CONFIRMED"},
                now=now,
            )
        await session.commit()
    return RemoteCancelReport(confirmed=confirmed, failed=failed)


async def _evidence(
    session: AsyncSession,
    *,
    tenant_id: str,
    item: RemoteCancel,
    event_type: TaskEventType,
    payload: dict[str, object],
    now: datetime,
) -> None:
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=tenant_id,
                entity_type=EntityType.ASYNC,
                entity_id=item.operation_id,
                event_type=event_type,
                payload={
                    **payload,
                    "execution_id": str(item.execution_id),
                    "source_tool_call_id": item.source_tool_call_id,
                },
                occurred_at=now,
            )
        ],
    )


__all__ = [
    "RemoteCancelReport",
    "RuntimeToolOperationCanceller",
    "ToolCancelError",
    "ToolOperationCanceller",
    "dispatch_remote_cancels",
]
