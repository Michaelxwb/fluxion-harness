"""Runtime 侧的 Task owner 只读事实：归属复核与回执绑定。

表所有权仍在 Worker 的 `task` schema；Runtime 只按租户读这几列，用于**复核**调用方声明的
归属（tenant/actor/状态）与回执身份，不写、不缓存、不据此外推业务状态。跨 schema 引用是
逻辑 UUID，不建物理 FK（`tool_result_inbox.receipt_event_id` 同理）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import TaskStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TASK_OWNER = "TASK"
#: Task 结果回执挂载的实体类型：终态事件永远属于被执行的那个 Execution。
TASK_EVENT_ENTITY = "EXECUTION"

_TASK_EXECUTION_SQL = text(
    "SELECT actor_user_id, status, cancel_requested FROM task.task_execution "
    "WHERE tenant_id = :tenant_id AND id = :task_execution_id AND is_deleted = false"
)
_TASK_EVENT_SQL = text(
    "SELECT id FROM task.task_event "
    "WHERE tenant_id = :tenant_id AND entity_type = :entity_type "
    "AND entity_id = :entity_id AND seq = :seq AND is_deleted = false"
)
_TASK_EXECUTION_SNAPSHOT_SQL = text(
    "SELECT snapshot_hash FROM task.task_execution "
    "WHERE tenant_id = :tenant_id AND id = :task_execution_id AND is_deleted = false"
)


@dataclass(frozen=True, slots=True)
class TaskExecutionFacts:
    actor_user_id: UUID
    status: TaskStatus
    cancel_requested: bool


async def read_task_execution(
    session: AsyncSession, *, tenant_id: str, task_execution_id: UUID
) -> TaskExecutionFacts | None:
    """不存在/软删/跨租户一律 `None`（同一响应形状，不泄漏是否存在）。"""
    row = (
        await session.execute(
            _TASK_EXECUTION_SQL, {"tenant_id": tenant_id, "task_execution_id": task_execution_id}
        )
    ).first()
    if row is None:
        return None
    return TaskExecutionFacts(
        actor_user_id=cast(UUID, row.actor_user_id),
        status=TaskStatus(str(row.status)),
        cancel_requested=bool(row.cancel_requested),
    )


async def read_task_event(
    session: AsyncSession, *, tenant_id: str, entity_id: UUID, seq: int
) -> UUID | None:
    """回执事实来源：`(tenant, EXECUTION, entity_id, seq)` 定位到唯一的 `task_event`。

    返回事件 id；不存在即 `None`。回执的 `receipt_event_id` 由这里解析，调用方不能自己声明。
    """
    return cast(
        UUID | None,
        await session.scalar(
            _TASK_EVENT_SQL,
            {
                "tenant_id": tenant_id,
                "entity_type": TASK_EVENT_ENTITY,
                "entity_id": entity_id,
                "seq": seq,
            },
        ),
    )


async def read_task_execution_snapshot(
    session: AsyncSession, *, tenant_id: str, task_execution_id: UUID
) -> str | None:
    return cast(
        str | None,
        await session.scalar(
            _TASK_EXECUTION_SNAPSHOT_SQL,
            {"tenant_id": tenant_id, "task_execution_id": task_execution_id},
        ),
    )


async def require_task_execution(
    session: AsyncSession, *, tenant_id: str, task_execution_id: UUID, actor_user_id: UUID
) -> TaskExecutionFacts:
    """归属只认 Execution 行：租户不符即 404，操作者不符即无权（body 不是信任来源）。"""
    facts = await read_task_execution(
        session, tenant_id=tenant_id, task_execution_id=task_execution_id
    )
    if facts is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if facts.actor_user_id != actor_user_id:
        raise AppError(ErrorCode.TASK_ACCESS_DENIED)
    return facts
