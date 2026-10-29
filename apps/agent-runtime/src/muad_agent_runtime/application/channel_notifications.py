"""Run 终态/待确认的用户通知：与状态/确认事件**同一事务**写入（design §3.3/§3.6）。

职责与边界：

- **同事务**：终态（完成/失败/取消）与待确认（interrupt）在产生它们的那个事务里写通知——
  先提交终态、之后再补通知是禁止的：进程在两步之间崩溃会让用户永远看不到结果（design §3.6
  「Run 的状态/确认事件与通知在 Runtime 同一事务提交」）。
- **锁序**：`(tenant,user)` 的 feed 行锁在既有 Conversation/Run 行锁**之后**取得；调用点必须
  先持有 Conversation→Run 行锁（`lock_run_source` / `lock_execution`），本模块不反向取锁。
- **seq**：feed 行锁内 `last_seq + 1`；`(tenant_id,user_id,seq)` 与 `(tenant_id,delivery_key)`
  是数据库完整唯一事实。`delivery_key` 稳定 ⇒ 重投（崩溃重放/主动补投）返回**原行**：不新占
  seq、不新增未读；隐藏（软删）通知同样保留唯一回执，不因隐藏而重新投递。
- **投递状态按入口通道选择**（design §3.6「机器人终态交付」）：机器人入口
  （见 `muad_contracts.OUTBOUND_CHANNELS`：有真实主动投递适配器的通道）的
  投递泵按租约经既有 Gateway 适配器交付，**不依赖 Gateway 内存里的订阅/回复会话**；网页持久
  入箱是 `NOT_REQUIRED`（站内通知没有出站目标）。`TASK_RESULT` 恒为 `NOT_REQUIRED`：任务结果
  的机器人出站由 Worker 投递链按自己的投递路由负责，不在这里重复表达。
- **Task 结果入箱**（TASK-064）：`kind=TASK_RESULT` 必须带真实 `task_id` 与属于该 Task 的
  `artifact_ids`；核验真实来源在读取层（`channel_notification_queries`），本模块只保证行内事实
  与词表一致。
- **无入口归属的会话不进网页收件箱**：`entry_id` 是通知 DTO 的必填身份，
  `conversation.channel_account_id IS NULL` 时返回 `None`（不编造入口 UUID）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import OUTBOUND_CHANNELS, ChannelContext
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import (
    ChannelNotification,
    ChannelNotificationFeed,
    Conversation,
    RunRecord,
)

#: `channel_notification.kind` 取值（与 0026 的 CHECK 同词表）。
NotificationKind = Literal["RUN_COMPLETED", "RUN_WAITING_INPUT", "TASK_RESULT"]
KIND_RUN_COMPLETED: NotificationKind = "RUN_COMPLETED"
KIND_RUN_WAITING_INPUT: NotificationKind = "RUN_WAITING_INPUT"
KIND_TASK_RESULT: NotificationKind = "TASK_RESULT"
NOTIFICATION_KINDS: tuple[NotificationKind, ...] = (
    KIND_RUN_COMPLETED,
    KIND_RUN_WAITING_INPUT,
    KIND_TASK_RESULT,
)
#: 网页持久入箱：站内通知不需要出站投递。
DELIVERY_NOT_REQUIRED = "NOT_REQUIRED"
#: 出站投递意图（机器人交付路径显式选择；本模块只认词表，不替调用方决定）。
DELIVERY_PENDING = "PENDING"
#: `channel_notification.delivery_state` 取值（与 0026 的 CHECK 同词表）。
DELIVERY_STATES = (DELIVERY_PENDING, "DELIVERING", "DELIVERED", "UNKNOWN", DELIVERY_NOT_REQUIRED)
#: 有真实主动投递能力的机器人通道见 `muad_contracts.OUTBOUND_CHANNELS`（词表定义处只此一处）。


def terminal_delivery_state(run: RunRecord) -> str:
    """Run 终态通知的投递状态：机器人入口写 `PENDING` 出站意图，其余（网页入箱）`NOT_REQUIRED`。

    路由只从 Run 自己持久化的 `channel_json` 读：缺接收方/坏载荷一律按网页入箱处理
    （没有可交付的目标就不该占投递泵的重试预算），不猜来源。
    """
    if not run.channel_json:
        return DELIVERY_NOT_REQUIRED
    try:
        channel = ChannelContext.model_validate(run.channel_json)
    except ValidationError:
        return DELIVERY_NOT_REQUIRED
    if not channel.external_user_id or channel.type not in OUTBOUND_CHANNELS:
        return DELIVERY_NOT_REQUIRED
    return DELIVERY_PENDING


def run_terminal_delivery_key(run_id: uuid.UUID) -> str:
    """Run 终态的稳定交付键：一个 Run 只有一个终态，重投必须命中同一回执。"""
    return f"run:{run_id}:terminal"


def run_waiting_delivery_key(run_id: uuid.UUID, interrupt_id: uuid.UUID) -> str:
    """待确认的稳定交付键：一次中断一份确认，重投按中断身份命中同一回执。"""
    return f"run:{run_id}:waiting-input:{interrupt_id}"


def task_result_delivery_key(task_id: uuid.UUID) -> str:
    """Task 结果的稳定交付键：一个 Task 一份结果，重投命中同一回执（与交付契约同形）。"""
    return f"task:{task_id}:final"


@dataclass(frozen=True, slots=True)
class RunNotificationReceipt:
    """写入结果：`created=False` 即命中既有回执（重投），此时未分配 seq、未新增未读。"""

    id: uuid.UUID
    seq: int
    created: bool
    entry_id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    user_id: uuid.UUID
    kind: str
    event_id: uuid.UUID | None
    task_id: uuid.UUID | None
    created_at: datetime


async def write_run_notification(
    session: AsyncSession,
    *,
    run: RunRecord,
    kind: str,
    delivery_key: str,
    event_id: uuid.UUID | None = None,
    delivery_state: str | None = None,
    task_id: uuid.UUID | None = None,
    artifact_ids: Sequence[uuid.UUID] = (),
) -> RunNotificationReceipt | None:
    """事务内写一条 Run 用户通知（调用方负责提交；失败即与终态一起整体回滚）。

    返回 `None` 表示该会话没有入口归属（不进网页收件箱）。

    `delivery_state=None` 时按**通知类别 + 入口通道**选择（design §3.6）：Run 终态/待确认在
    机器人入口写 `PENDING` 出站意图，其余写网页持久入箱 `NOT_REQUIRED`。显式传值时以调用方
    选择为准（本模块不覆盖调用方的判断）。
    """
    state = delivery_state
    if state is None:
        state = (
            terminal_delivery_state(run)
            if kind in (KIND_RUN_COMPLETED, KIND_RUN_WAITING_INPUT)
            else DELIVERY_NOT_REQUIRED
        )
    _validate(kind=kind, delivery_state=state, task_id=task_id)
    entry_id = await _entry_id_of(session, run)
    if entry_id is None:
        return None
    feed_id = await _lock_feed(session, run.tenant_id, run.user_id)
    existing = await _existing_receipt(session, run.tenant_id, delivery_key)
    if existing is not None:
        return existing
    seq = await _next_seq(session, feed_id)
    row = ChannelNotification(
        tenant_id=run.tenant_id,
        feed_id=feed_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        user_id=run.user_id,
        entry_id=entry_id,
        kind=kind,
        seq=seq,
        delivery_key=delivery_key,
        event_id=event_id,
        task_id=task_id,
        artifact_ids=list(artifact_ids),
        delivery_state=state,
    )
    session.add(row)
    await session.flush()
    return RunNotificationReceipt(
        id=row.id,
        seq=seq,
        created=True,
        entry_id=row.entry_id,
        conversation_id=row.conversation_id,
        run_id=row.run_id,
        user_id=row.user_id,
        kind=row.kind,
        event_id=row.event_id,
        task_id=row.task_id,
        created_at=row.create_time or datetime.now(UTC),
    )


def _validate(*, kind: str, delivery_state: str, task_id: uuid.UUID | None) -> None:
    """协议词表由数据库 CHECK 定义：未知取值显式失败，不静默落库等约束报错。

    `task_id` 与 kind 互斥性同契约（`WebchatNotification`）：TASK_RESULT 必须有真实 Task 身份，
    其余 kind 没有它的位置。
    """
    if kind not in NOTIFICATION_KINDS:
        raise ValueError(f"unknown notification kind: {kind}")
    if delivery_state not in DELIVERY_STATES:
        raise ValueError(f"unknown delivery state: {delivery_state}")
    if (kind == KIND_TASK_RESULT) != (task_id is not None):
        raise ValueError("task_id is required for TASK_RESULT and has no place for other kinds")


async def _entry_id_of(session: AsyncSession, run: RunRecord) -> uuid.UUID | None:
    """会话的入口归属：NULL 表示不是网页入口会话（不编造入口身份）。"""
    entry_id = await session.scalar(
        sa.select(Conversation.channel_account_id).where(
            Conversation.id == run.conversation_id, Conversation.tenant_id == run.tenant_id
        )
    )
    return None if entry_id is None else uuid.UUID(str(entry_id))


async def _existing_receipt(
    session: AsyncSession, tenant_id: str, delivery_key: str
) -> RunNotificationReceipt | None:
    """投递回执命中（含隐藏行）：不分配 seq、不新增未读，直接返回原行。"""
    row = await session.scalar(
        sa.select(ChannelNotification).where(
            ChannelNotification.tenant_id == tenant_id,
            ChannelNotification.delivery_key == delivery_key,
        )
    )
    if row is None:
        return None
    return RunNotificationReceipt(
        id=row.id,
        seq=int(row.seq),
        created=False,
        entry_id=row.entry_id,
        conversation_id=row.conversation_id,
        run_id=row.run_id,
        user_id=row.user_id,
        kind=row.kind,
        event_id=row.event_id,
        task_id=row.task_id,
        created_at=row.create_time or datetime.now(UTC),
    )


async def _lock_feed(session: AsyncSession, tenant_id: str, user_id: uuid.UUID) -> uuid.UUID:
    """注册（首次写入）并锁住该用户的 feed 行；锁序上它必须晚于 Conversation/Run 行锁。"""
    await session.execute(
        pg_insert(ChannelNotificationFeed)
        .values(tenant_id=tenant_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=["tenant_id", "user_id"])
    )
    feed_id = await session.scalar(
        sa.select(ChannelNotificationFeed.id)
        .where(
            ChannelNotificationFeed.tenant_id == tenant_id,
            ChannelNotificationFeed.user_id == user_id,
        )
        .with_for_update()
    )
    if feed_id is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return feed_id


async def _next_seq(session: AsyncSession, feed_id: uuid.UUID) -> int:
    """feed 行锁内分配下一个提交序号（不碰 canonical seq：通知不是会话事件）。"""
    seq = await session.scalar(
        sa.update(ChannelNotificationFeed)
        .where(ChannelNotificationFeed.id == feed_id)
        .values(last_seq=ChannelNotificationFeed.last_seq + 1, update_time=sa.func.now())
        .returning(ChannelNotificationFeed.last_seq)
    )
    if seq is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return int(seq)
