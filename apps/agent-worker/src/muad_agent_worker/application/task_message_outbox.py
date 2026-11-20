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
from muad_contracts.enums import OUTBOUND_CHANNELS
from muad_logging import get_logger
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import (
    DeliveryRoute,
    TaskDelivery,
    TaskExecution,
    TaskMessageOutbox,
    TaskScheduleRevision,
)

logger = get_logger(__name__)

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
    *,
    result: Mapping[str, Any] | None = None,
) -> bool:
    """成功终态写一条消息；返回**该任务是否已有** outbox（供调用方跳过文本投递）。

    失败/取消由调用方决定不进入本函数；本函数只处理成功终态的事实。
    """
    if task.parent_id is not None:
        # JOIN/BATCH 子任务不直接向用户投递；父任务终态聚合子产物。
        return False
    pushes = await _push_routes(session, task)
    if not pushes:
        return False
    attachments = await _output_attachments(session, task)
    if not attachments:
        return False
    payload = MessageDeliveryPayloadInput(
        text=_result_text(task, result),
        attachments=attachments,
    )
    message_key = f"execution:{task.id}:final"
    written = False
    for push in pushes:
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
                continue
        written = True
    if not written:
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


async def _push_routes(session: AsyncSession, task: TaskExecution) -> list[_PushRoute]:
    """All push targets for this task: every valid bound identity, then frozen fallback.

    A user normally has one identity; if several are bound (the edge case), each enabled
    bot gets its own outbox row and therefore its own delivery. The frozen revision route
    is the fallback when no identity is currently readable (for example old test rows).
    """
    if task.schedule_revision_id is not None:
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
            return []
        identity_routes = await _identity_push_routes(session, task)
        if identity_routes:
            return identity_routes
        fallback = await _route_row_to_push(session, task, revision.delivery_route_id)
        return [fallback] if fallback is not None else []
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
        return []
    identity_routes = await _identity_push_routes(session, task)
    if identity_routes:
        return identity_routes
    fallback = await _route_row_to_push(session, task, delivery_route_id)
    return [fallback] if fallback is not None else []


_CHANNEL_IDENTITY = sa.table(
    "channel_identity",
    sa.column("tenant_id", sa.String()),
    sa.column("channel", sa.String()),
    sa.column("external_user_id", sa.String()),
    sa.column("bot_account_id", sa.Uuid()),
    sa.column("platform_user_id", sa.Uuid()),
    sa.column("is_deleted", sa.Boolean()),
    sa.column("bound_at", sa.DateTime(timezone=True)),
    sa.column("last_active_at", sa.DateTime(timezone=True)),
    schema="control",
)
_BOT_ACCOUNT = sa.table(
    "bot_account",
    sa.column("id", sa.Uuid()),
    sa.column("tenant_id", sa.String()),
    sa.column("bot_id", sa.String()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("enabled", sa.Boolean()),
    sa.column("is_deleted", sa.Boolean()),
    schema="control",
)


async def _identity_push_routes(
    session: AsyncSession, task: TaskExecution
) -> list[_PushRoute]:
    """Current valid outbound identities for this task's actor + agent, de-duplicated."""
    rows = (
        await session.execute(
            sa.select(
                _CHANNEL_IDENTITY.c.channel,
                _BOT_ACCOUNT.c.bot_id,
                _CHANNEL_IDENTITY.c.external_user_id,
            )
            .select_from(
                _CHANNEL_IDENTITY.join(
                    _BOT_ACCOUNT, _BOT_ACCOUNT.c.id == _CHANNEL_IDENTITY.c.bot_account_id
                )
            )
            .where(
                _CHANNEL_IDENTITY.c.tenant_id == task.tenant_id,
                _CHANNEL_IDENTITY.c.platform_user_id == task.actor_user_id,
                _BOT_ACCOUNT.c.agent_id == task.agent_id,
                _CHANNEL_IDENTITY.c.is_deleted.is_(False),
                _BOT_ACCOUNT.c.is_deleted.is_(False),
                _BOT_ACCOUNT.c.enabled.is_(True),
                _CHANNEL_IDENTITY.c.channel.in_(sorted(OUTBOUND_CHANNELS)),
            )
            .order_by(
                sa.func.coalesce(
                    _CHANNEL_IDENTITY.c.last_active_at, _CHANNEL_IDENTITY.c.bound_at
                ).desc(),
                _CHANNEL_IDENTITY.c.external_user_id,
            )
        )
    ).all()
    from .delivery_routes import upsert_delivery_route

    pushes: list[_PushRoute] = []
    seen: set[str] = set()
    for row in rows:
        route = DeliveryRouteInput(
            channel=str(row.channel),
            bot_id=str(row.bot_id),
            external_user_id=str(row.external_user_id),
            external_conversation_id=None,
        )
        key = delivery_target_key(route)
        if key in seen:
            continue
        seen.add(key)
        # Runtime 的收件人校验读 `task.delivery_route`；扇出的每个目标都要先落成
        # owner-scoped route，否则第二个 bot 会被判为 DELIVERY_ROUTE_UNAVAILABLE。
        await upsert_delivery_route(
            session,
            tenant_id=task.tenant_id,
            platform_user_id=task.actor_user_id,
            route=route,
        )
        pushes.append(_PushRoute(route=route, target_key=key))
    return pushes


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


def _result_text(task: TaskExecution, fresh: Mapping[str, Any] | None = None) -> str:
    """冻结用户可见的正文摘要；没有摘要就只发附件（不伪造渲染文案）。

    ``fresh`` 是同一事务里刚写入的结果。终止事务用 UPDATE 落库后，ORM 实例上的
    ``result_json`` 可能还是旧值；只读实例会把正文丢成空串（只剩附件）。
    """
    result: Mapping[str, Any] = (
        fresh
        if isinstance(fresh, Mapping)
        else task.result_json
        if isinstance(task.result_json, Mapping)
        else {}
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
