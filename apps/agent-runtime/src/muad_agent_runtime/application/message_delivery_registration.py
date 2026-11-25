"""Task outbox 的消息受理面（design §4.6）：校验所有者/路由/附件后写 `message_delivery`。

Worker 只提交**已经冻结**的消息事实（message key、目标、正文与附件），Runtime 是
`message_delivery` 的 Owner：

- owner 必须是本租户已成功完成的 Task（软删/跨租户/失败态一律拒绝）；
- 路由必须属于同一个用户-Agent，且通道在 `OUTBOUND_CHANNELS`、绑定仍为 ACTIVE；
- 附件必须属于该 Task（`artifact.task_id`），快照按**数据库行**重建，不信任调用方给的
  storage_key；
- `target_key` 必须与 `delivery_target_key(route)` 一致，否则同一条消息可能记成两个目标。

本模块不发送、不读 outbox；发送仍由 MessageDeliveryPump 统一完成。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    OUTBOUND_CHANNELS,
    AttachmentRef,
    MessageDeliveryEnqueueReceipt,
    MessageDeliveryEnqueueRequest,
    delivery_target_key,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import Artifact
from .attachments.reference import attachment_ref
from .message_delivery import (
    MESSAGE_KIND_TASK_RESULT,
    MessageDeliveryPayload,
    enqueue_message_delivery,
)

TASK_COMPLETED = "COMPLETED"
ROUTE_ACTIVE = "ACTIVE"

_TASK_OWNER_QUERY = sa.text(
    "SELECT actor_user_id, agent_id, status FROM task.task_execution "
    "WHERE id = :owner_id AND tenant_id = :tenant_id AND is_deleted = false"
)
_ROUTE_QUERY = sa.text(
    "SELECT 1 FROM task.delivery_route "
    "WHERE tenant_id = :tenant_id AND platform_user_id = :platform_user_id "
    "AND channel = :channel AND bot_id = :bot_id AND external_user_id = :external_user_id "
    "AND external_conversation_id IS NOT DISTINCT FROM :external_conversation_id "
    "AND status = :active AND is_deleted = false "
    "LIMIT 1"
)
#: 根任务允许携带直接子任务产出的附件（BATCH 聚合结果）。
_TASK_EXECUTION = sa.table(
    "task_execution",
    sa.column("id", sa.Uuid()),
    sa.column("parent_id", sa.Uuid()),
    sa.column("tenant_id", sa.String()),
    sa.column("is_deleted", sa.Boolean()),
    schema="task",
)


@dataclass(frozen=True, slots=True)
class _TaskOwner:
    actor_user_id: uuid.UUID
    agent_id: uuid.UUID
    status: str


async def enqueue_task_message_delivery(
    session: AsyncSession,
    *,
    tenant_id: str,
    request: MessageDeliveryEnqueueRequest,
) -> MessageDeliveryEnqueueReceipt:
    """校验并幂等受理一条 Task 消息；同 `(message_key, target_key)` 返回首次结果。"""
    if request.tenant_id != tenant_id:
        raise AppError(ErrorCode.FORBIDDEN)
    owner = await _task_owner(session, tenant_id=tenant_id, task_id=request.owner_id)
    await _assert_route_owner(
        session,
        tenant_id=tenant_id,
        platform_user_id=owner.actor_user_id,
        request=request,
    )
    attachments = await _owned_attachments(
        session, tenant_id=tenant_id, task_id=request.owner_id, requested=request.payload.attachments
    )
    expected_target = delivery_target_key(request.route)
    if request.target_key != expected_target:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    payload = MessageDeliveryPayload(
        text=request.payload.text,
        attachments=tuple(attachments),
    )
    receipt = await enqueue_message_delivery(
        session,
        tenant_id=tenant_id,
        owner_type="TASK",
        owner_id=request.owner_id,
        message_key=request.message_key,
        kind=MESSAGE_KIND_TASK_RESULT,
        target_key=request.target_key,
        route=request.route,
        payload=payload,
        agent_id=owner.agent_id,
        platform_user_id=owner.actor_user_id,
    )
    return MessageDeliveryEnqueueReceipt(
        delivery_id=receipt.id,
        status=receipt.status,  # type: ignore[arg-type]
        message_key=receipt.message_key,
        target_key=receipt.target_key,
        duplicate=not receipt.created,
    )


async def _task_owner(
    session: AsyncSession, *, tenant_id: str, task_id: uuid.UUID
) -> _TaskOwner:
    row = (
        await session.execute(
            _TASK_OWNER_QUERY, {"owner_id": task_id, "tenant_id": tenant_id}
        )
    ).one_or_none()
    if row is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    owner = _TaskOwner(
        actor_user_id=row.actor_user_id,
        agent_id=row.agent_id,
        status=str(row.status),
    )
    if owner.status != TASK_COMPLETED:
        raise AppError(ErrorCode.COMMON_CONFLICT, data={"reason": "TASK_NOT_COMPLETED"})
    return owner


async def _assert_route_owner(
    session: AsyncSession,
    *,
    tenant_id: str,
    platform_user_id: uuid.UUID,
    request: MessageDeliveryEnqueueRequest,
) -> None:
    route = request.route
    if route.channel not in OUTBOUND_CHANNELS:
        raise AppError(
            ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED,
            data={"reason": "DELIVERY_ROUTE_UNAVAILABLE"},
        )
    found = await session.scalar(
        _ROUTE_QUERY,
        {
            "tenant_id": tenant_id,
            "platform_user_id": platform_user_id,
            "channel": route.channel,
            "bot_id": route.bot_id,
            "external_user_id": route.external_user_id,
            "external_conversation_id": route.external_conversation_id,
            "active": ROUTE_ACTIVE,
        },
    )
    if found is None:
        raise AppError(
            ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED,
            data={"reason": "DELIVERY_ROUTE_UNAVAILABLE"},
        )


async def _owned_attachments(
    session: AsyncSession,
    *,
    tenant_id: str,
    task_id: uuid.UUID,
    requested: list[AttachmentRef],
) -> list[AttachmentRef]:
    """按 artifact 行重建附件快照：调用方给的 storage_key/元信息不参与发送。"""
    if not requested:
        return []
    artifact_ids = [attachment.artifact_id for attachment in requested]
    child_ids = sa.select(_TASK_EXECUTION.c.id).where(
        _TASK_EXECUTION.c.tenant_id == tenant_id,
        _TASK_EXECUTION.c.parent_id == task_id,
        _TASK_EXECUTION.c.is_deleted.is_(False),
    )
    rows = (
        await session.scalars(
            sa.select(Artifact).where(
                Artifact.tenant_id == tenant_id,
                sa.or_(
                    Artifact.task_id == task_id,
                    Artifact.task_id.in_(child_ids),
                ),
                Artifact.id.in_(artifact_ids),
                Artifact.is_deleted.is_(False),
            )
        )
    ).all()
    by_id = {row.id: row for row in rows}
    attachments: list[AttachmentRef] = []
    for attachment in requested:
        artifact_id = attachment.artifact_id
        if artifact_id is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        row = by_id.get(artifact_id)
        if row is None or row.checksum != attachment.checksum:
            raise AppError(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
        attachments.append(attachment_ref(row))
    return attachments


__all__ = ["enqueue_task_message_delivery"]
