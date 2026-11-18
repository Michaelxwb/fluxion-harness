"""Task 成功终态的消息发件箱（design §4.6）：冻结“正文 + 附件 + push 路由”。

只在**同一终态事务**里调用。三条纪律：

1. 只有成功终态、有 Schedule 冻结的 PUSH 路由、且本执行（含直接子任务）确有 `OUTPUT`
   附件时才写行——没有附件时留给既有文本投递，避免同一条结果被推两次；
2. `payload_json` 冻结有界快照（正文摘要 + 附件引用）；后续重试/接管只读它；
3. 附件按 `logical_file_id` 取最新版本，按 `artifact_id + checksum` 引用，任务中心与
   push 读同一份事实。

Worker 消费者在 `delivery/message_outbox.py`；本模块只写，不发送。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from muad_contracts import (
    MAX_MESSAGE_DELIVERY_ATTACHMENTS,
    AttachmentRef,
    DeliveryIntent,
    DeliveryRouteInput,
    MessageDeliveryPayloadInput,
    delivery_target_key,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import (
    DeliveryRoute,
    TaskDelivery,
    TaskExecution,
    TaskMessageOutbox,
    TaskScheduleRevision,
)

logger = logging.getLogger(__name__)

MESSAGE_KIND_TASK_RESULT = "TASK_RESULT"
OUTBOX_PENDING = "PENDING"
DELIVERY_KIND_PUSH = "PUSH"
DELIVERY_STATUS_PENDING = "PENDING"
DELIVERY_STATUS_CANCELLED = "CANCELLED"
DELIVERY_INTENT_PUSH = str(DeliveryIntent.PUSH)
MAX_OUTBOX_TEXT_CHARS = 1500
MAX_OUTBOX_ATTACHMENTS = MAX_MESSAGE_DELIVERY_ATTACHMENTS
SUMMARY_KEYS = ("summary", "text", "message", "answer")
KNOWN_KINDS = ("IMAGE", "DOCUMENT", "OTHER")

_OUTPUT_ATTACHMENTS_QUERY = sa.text(
    "SELECT * FROM ("
    "  SELECT DISTINCT ON (a.logical_file_id) "
    "    a.id, a.storage_key, a.media_type, a.size, a.checksum, a.metadata_json, a.create_time "
    "  FROM runtime.artifact a "
    "  JOIN task.task_execution e ON e.id = a.task_id "
    "  WHERE e.tenant_id = :tenant_id AND e.is_deleted = false "
    "  AND (a.task_id = :task_id OR e.parent_id = :task_id) "
    "  AND a.is_deleted = false AND a.artifact_type = 'OUTPUT' "
    "  ORDER BY a.logical_file_id, a.version DESC"
    ") latest ORDER BY latest.create_time ASC, latest.id "
    "LIMIT :limit"
)


async def enqueue_task_message_outbox(
    session: AsyncSession,
    task: TaskExecution,
    moment: datetime,
) -> bool:
    """成功终态写一条消息；返回**该任务是否已有** outbox（供调用方跳过文本投递）。

    失败/取消由调用方决定不进入本函数；本函数只处理成功终态的事实。
    """
    if task.parent_id is not None:
        # JOIN/BATCH 子任务不直接向用户投递；父任务终态聚合子产物。
        return False
    push = await _push_route(session, task)
    if push is None:
        return False
    attachments = await _output_attachments(session, task)
    if not attachments:
        return False
    payload = MessageDeliveryPayloadInput(
        text=_result_text(task),
        attachments=attachments,
    )
    message_key = f"execution:{task.id}:final"
    values = {
        "tenant_id": task.tenant_id,
        "task_id": task.id,
        "message_key": message_key,
        "kind": MESSAGE_KIND_TASK_RESULT,
        "target_key": push.target_key,
        "route_json": push.route.model_dump(mode="json"),
        "payload_json": payload.model_dump(mode="json"),
        "status": OUTBOX_PENDING,
        "attempts": 0,
        "next_attempt_at": moment,
    }
    inserted = (
        await session.execute(
            pg_insert(TaskMessageOutbox)
            .values(id=uuid.uuid4(), **values)
            .on_conflict_do_nothing(
                index_elements=[
                    TaskMessageOutbox.tenant_id,
                    TaskMessageOutbox.task_id,
                    TaskMessageOutbox.message_key,
                    TaskMessageOutbox.target_key,
                ]
            )
            .returning(TaskMessageOutbox.id)
        )
    ).scalar_one_or_none()
    if inserted is None:
        existing_id = await session.scalar(
            sa.select(TaskMessageOutbox.id).where(
                TaskMessageOutbox.tenant_id == task.tenant_id,
                TaskMessageOutbox.task_id == task.id,
                TaskMessageOutbox.message_key == message_key,
                TaskMessageOutbox.target_key == push.target_key,
            )
        )
        if existing_id is None:
            return False
    # 立即任务在创建时就建了 `task_delivery` 行；outbox 接手后同事务作废它，避免双发。
    await session.execute(
        sa.update(TaskDelivery)
        .where(
            TaskDelivery.tenant_id == task.tenant_id,
            TaskDelivery.execution_id == task.id,
            TaskDelivery.delivery_kind == DELIVERY_KIND_PUSH,
            TaskDelivery.status == DELIVERY_STATUS_PENDING,
            TaskDelivery.is_deleted.is_(False),
        )
        .values(status=DELIVERY_STATUS_CANCELLED, update_time=moment)
    )
    return True


async def _push_route(session: AsyncSession, task: TaskExecution) -> _PushRoute | None:
    """解析这条 Task 的 PUSH 路由：定时看冻结 Revision，立即任务看创建时的投递记录。"""
    if task.schedule_revision_id is not None:
        return await _revision_push_route(session, task)
    delivery_route_id = await session.scalar(
        sa.select(TaskDelivery.delivery_route_id).where(
            TaskDelivery.tenant_id == task.tenant_id,
            TaskDelivery.execution_id == task.id,
            TaskDelivery.delivery_kind == DELIVERY_KIND_PUSH,
            TaskDelivery.status == DELIVERY_STATUS_PENDING,
            TaskDelivery.is_deleted.is_(False),
        )
    )
    if delivery_route_id is None:
        return None
    return await _route_row_to_push(session, task, delivery_route_id)


async def _revision_push_route(
    session: AsyncSession, task: TaskExecution
) -> _PushRoute | None:
    """按冻结 Revision 解析 PUSH 路由；NONE/无路由/坏路由返回 None（走既有文本投递）。"""
    revision = await session.scalar(
        sa.select(TaskScheduleRevision).where(
            TaskScheduleRevision.id == task.schedule_revision_id,
            TaskScheduleRevision.tenant_id == task.tenant_id,
            TaskScheduleRevision.is_deleted.is_(False),
        )
    )
    if (
        revision is None
        or revision.delivery_intent != DELIVERY_INTENT_PUSH
        or revision.delivery_route_id is None
    ):
        return None
    return await _route_row_to_push(session, task, revision.delivery_route_id)


async def _route_row_to_push(
    session: AsyncSession, task: TaskExecution, route_id: uuid.UUID
) -> _PushRoute | None:
    route_row = await session.scalar(
        sa.select(DeliveryRoute).where(
            DeliveryRoute.id == route_id,
            DeliveryRoute.tenant_id == task.tenant_id,
        )
    )
    if route_row is None:
        logger.warning(
            "task_outbox_route_missing",
            extra={"task_id": str(task.id), "route_id": str(route_id)},
        )
        return None
    try:
        route = DeliveryRouteInput.model_validate(route_row.route_json or {})
    except ValueError:
        logger.warning(
            "task_outbox_route_invalid",
            extra={"task_id": str(task.id), "route_id": str(route_row.id)},
        )
        return None
    return _PushRoute(route=route, target_key=delivery_target_key(route))


async def _output_attachments(
    session: AsyncSession, task: TaskExecution
) -> list[AttachmentRef]:
    """本执行与直接子任务的 `OUTPUT` 最新版本；有界，截断事实可见。"""
    rows = (
        await session.execute(
            _OUTPUT_ATTACHMENTS_QUERY,
            {
                "tenant_id": task.tenant_id,
                "task_id": task.id,
                "limit": MAX_OUTBOX_ATTACHMENTS + 1,
            },
        )
    ).all()
    if len(rows) > MAX_OUTBOX_ATTACHMENTS:
        logger.warning(
            "task_outbox_attachments_truncated",
            extra={
                "task_id": str(task.id),
                "kept": MAX_OUTBOX_ATTACHMENTS,
                "seen": len(rows),
            },
        )
        rows = rows[:MAX_OUTBOX_ATTACHMENTS]
    attachments: list[AttachmentRef] = []
    for row in rows:
        if int(row.size) <= 0:
            continue
        metadata = dict(row.metadata_json or {})
        kind = metadata.get("kind")
        attachments.append(
            AttachmentRef(
                storage_key=row.storage_key,
                kind=kind if kind in KNOWN_KINDS else "OTHER",
                media_type=row.media_type,
                size=int(row.size),
                filename=metadata.get("filename") or None,
                checksum=row.checksum,
                artifact_id=row.id,
            )
        )
    return attachments


def _result_text(task: TaskExecution) -> str:
    """冻结用户可见的正文摘要；没有摘要就只发附件（不伪造渲染文案）。"""
    result: Mapping[str, Any] = (
        task.result_json if isinstance(task.result_json, Mapping) else {}
    )
    for key in SUMMARY_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_OUTBOX_TEXT_CHARS]
    return ""


class _PushRoute:
    __slots__ = ("route", "target_key")

    def __init__(self, *, route: DeliveryRouteInput, target_key: str) -> None:
        self.route = route
        self.target_key = target_key


__all__ = [
    "MESSAGE_KIND_TASK_RESULT",
    "OUTBOX_PENDING",
    "enqueue_task_message_outbox",
]
