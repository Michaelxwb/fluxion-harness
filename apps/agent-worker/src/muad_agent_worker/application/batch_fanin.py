"""Child 终态时的原子 fan-in（设计 §3.2.4）。

在 Child 终态的同事务内锁住 Parent、统计兄弟终态：
- 仍有非终态兄弟 → 不聚合，按并发上限释放停放的 Child；
- 全部终态 → 按 `aggregate_mode` 聚合，CAS Parent 终态并写 `FAN_IN` 事件。

Parent 行锁先于兄弟统计获取，因此最后一个提交的 Child 事务一定能看到全部终态，
不依赖 Redis 唤醒顺序，也不会重复聚合。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from muad_contracts import TaskStatus
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskExecution
from .batch_fanout import AGGREGATE_ALL, AGGREGATE_BEST_EFFORT, BATCH_REF_KEY, PARKED_NOT_BEFORE
from .task_events import TaskEventType, append_event

BATCH_CHILD_FAILED = "BATCH_CHILD_FAILED"
NON_TERMINAL_STATUSES = (
    str(TaskStatus.QUEUED),
    str(TaskStatus.RUNNING),
    str(TaskStatus.WAITING),
)
TERMINAL_STATUSES = (
    str(TaskStatus.COMPLETED),
    str(TaskStatus.FAILED),
    str(TaskStatus.CANCELLED),
)


@dataclass(frozen=True, slots=True)
class FaninOutcome:
    aggregated: bool
    parent_status: str | None
    released: int


async def settle_child(
    session: AsyncSession,
    child: TaskExecution,
    moment: datetime,
) -> FaninOutcome:
    """Child 终态后的同事务收尾；非 Child 或 Parent 已终态时是 no-op。"""
    if child.parent_id is None:
        return FaninOutcome(aggregated=False, parent_status=None, released=0)
    parent = (
        await session.execute(
            select(TaskExecution)
            .where(
                TaskExecution.id == child.parent_id,
                TaskExecution.is_deleted.is_(False),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if parent is None:
        return FaninOutcome(aggregated=False, parent_status=None, released=0)
    if parent.status in TERMINAL_STATUSES:
        return FaninOutcome(aggregated=False, parent_status=parent.status, released=0)
    siblings = list(
        (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.parent_id == parent.id,
                    TaskExecution.is_deleted.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    pending = [item for item in siblings if item.status in NON_TERMINAL_STATUSES]
    if pending:
        released = await _release_parked(session, parent, siblings, moment)
        return FaninOutcome(aggregated=False, parent_status=parent.status, released=released)
    status, result, error_code, error_message = _aggregate(parent, siblings)
    rowcount = await _cas_parent(
        session, parent, status, result, error_code, error_message, moment
    )
    if rowcount != 1:
        return FaninOutcome(aggregated=False, parent_status=None, released=0)
    await append_event(
        session,
        tenant_id=parent.tenant_id,
        task_id=parent.id,
        event_type=TaskEventType.FAN_IN,
        payload=dict(result),
    )
    return FaninOutcome(aggregated=True, parent_status=status, released=0)


def _is_parked(item: TaskExecution) -> bool:
    """只有 fan-out 停放哨兵才算停放；重试退避中的 QUEUED 有自己的 not_before，不是停放。"""
    return item.status == str(TaskStatus.QUEUED) and item.not_before == PARKED_NOT_BEFORE


async def _release_parked(
    session: AsyncSession,
    parent: TaskExecution,
    siblings: list[TaskExecution],
    moment: datetime,
) -> int:
    """按 Parent 记录的并发上限，把空出的名额释放给最早停放的 Child。

    占用名额的是所有「已放行但未终态」的 Child——包括重试退避中的 QUEUED 和外部等待
    中的 WAITING；否则它们到期后会和新释放的 Child 一起跑，突破并发上限。
    """
    concurrency = _concurrency(parent)
    active = [
        item
        for item in siblings
        if item.status in NON_TERMINAL_STATUSES and not _is_parked(item)
    ]
    slots = concurrency - len(active)
    if slots <= 0:
        return 0
    parked = sorted(
        (item for item in siblings if _is_parked(item)),
        key=lambda item: (item.item_key or "", str(item.id)),
    )[:slots]
    if not parked:
        return 0
    await session.execute(
        update(TaskExecution)
        .where(TaskExecution.id.in_([item.id for item in parked]))
        .values(not_before=moment, update_time=moment)
    )
    return len(parked)


def _concurrency(parent: TaskExecution) -> int:
    batch = (parent.external_ref_json or {}).get(BATCH_REF_KEY) or {}
    value = batch.get("concurrency")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return 1


def _aggregate(
    parent: TaskExecution, siblings: list[TaskExecution]
) -> tuple[str, dict[str, Any], str | None, str | None]:
    mode = ((parent.external_ref_json or {}).get(BATCH_REF_KEY) or {}).get(
        "aggregate_mode", AGGREGATE_ALL
    )
    succeeded = sum(1 for item in siblings if item.status == str(TaskStatus.COMPLETED))
    failed = sum(1 for item in siblings if item.status == str(TaskStatus.FAILED))
    cancelled = sum(1 for item in siblings if item.status == str(TaskStatus.CANCELLED))
    result = {
        "mode": mode,
        "total": len(siblings),
        "succeeded": succeeded,
        "failed": failed,
        "cancelled": cancelled,
    }
    if mode == AGGREGATE_BEST_EFFORT or succeeded == len(siblings):
        return str(TaskStatus.COMPLETED), result, None, None
    message = f"{failed + cancelled} of {len(siblings)} children did not succeed"
    return str(TaskStatus.FAILED), result, BATCH_CHILD_FAILED, message


async def _cas_parent(
    session: AsyncSession,
    parent: TaskExecution,
    status: str,
    result: dict[str, Any],
    error_code: str | None,
    error_message: str | None,
    moment: datetime,
) -> int:
    updated = await session.execute(
        update(TaskExecution)
        .where(
            TaskExecution.id == parent.id,
            TaskExecution.status.in_((str(TaskStatus.WAITING), str(TaskStatus.RUNNING))),
            TaskExecution.is_deleted.is_(False),
        )
        .values(
            status=status,
            result_json=result,
            error_code=error_code,
            error_message=error_message,
            finished_at=moment,
            lease_owner=None,
            lease_until=None,
            update_time=moment,
        )
    )
    return int(cast(CursorResult[Any], updated).rowcount)


__all__ = [
    "BATCH_CHILD_FAILED",
    "FaninOutcome",
    "settle_child",
]
