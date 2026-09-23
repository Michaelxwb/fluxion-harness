"""BATCH Parent 的确定性 fan-out（设计 §3.2.4）。

Parent 在单个事务内创建 Child：`root_id=parent.root_id`、`parent_id=parent.id`、
`idempotency_key=parent:{parent_id}:{item_key}`；partial unique
`(parent_id, item_key) WHERE parent_id IS NOT NULL AND is_deleted=false` 保证同一
Child 只创建一次。并发上限取 `min(plan.max_concurrency, system_max, platform_limit)`，
超出上限的 Child 先停放（`not_before` 置远期），由 fan-in 在兄弟终态时逐个释放。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from muad_common import SharedSettings
from muad_contracts import DeliveryMode, DeliveryStatus, TaskStatus
from sqlalchemy import false, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models.task import TaskExecution
from .task_events import TaskEventSeed, TaskEventType, append_events
from .task_service import EXECUTION_MODE_ASYNC, INITIAL_PRIORITY, TASK_TYPE_SKILL

BATCH_PLAN_KEY = "batch"
AGGREGATE_ALL = "ALL"
AGGREGATE_BEST_EFFORT = "BEST_EFFORT"
TASK_TYPE_BATCH = "BATCH"
ITEM_KEY_MAX_LENGTH = 128
# 停放的 Child 不参与 claim（`not_before <= now()` 恒不成立），等待 fan-in 释放。
PARKED_NOT_BEFORE = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
BATCH_REF_KEY = "batch"


class BatchPlanError(Exception):
    """TaskPlan 结构非法：无法确定性 fan-out。"""

    code = "BATCH_PLAN_INVALID"


@dataclass(frozen=True, slots=True)
class BatchPlan:
    items: tuple[Mapping[str, Any], ...]
    max_concurrency: int | None
    aggregate_mode: str

    @classmethod
    def parse(cls, payload: Any) -> BatchPlan | None:
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise BatchPlanError("batch plan must be an object")
        items = payload.get("items")
        if (
            not isinstance(items, Sequence)
            or isinstance(items, (str, bytes))
            or not items
        ):
            raise BatchPlanError("batch plan requires a non-empty items list")
        for item in items:
            if not isinstance(item, Mapping):
                raise BatchPlanError("batch plan items must be objects")
        max_concurrency = payload.get("max_concurrency")
        if max_concurrency is not None and (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise BatchPlanError("max_concurrency must be a positive integer")
        aggregate_mode = payload.get("aggregate_mode", AGGREGATE_ALL)
        if aggregate_mode not in (AGGREGATE_ALL, AGGREGATE_BEST_EFFORT):
            raise BatchPlanError("aggregate_mode must be ALL or BEST_EFFORT")
        return cls(
            items=tuple(dict(item) for item in items),
            max_concurrency=max_concurrency,
            aggregate_mode=str(aggregate_mode),
        )


@dataclass(frozen=True, slots=True)
class FanoutSummary:
    parent_id: uuid.UUID
    created: int
    item_count: int
    concurrency: int
    aggregate_mode: str

    def as_ref(self) -> dict[str, Any]:
        return {
            "aggregate_mode": self.aggregate_mode,
            "item_count": self.item_count,
            "concurrency": self.concurrency,
        }


class BatchFanoutProtocol(Protocol):
    async def fan_out(self, parent: TaskExecution, plan: BatchPlan) -> FanoutSummary: ...


def item_key_of(index: int, item: Mapping[str, Any]) -> str:
    """确定性项键：显式 `item_key` 优先，否则对规范化 JSON 取 sha256 前缀。"""
    explicit = item.get("item_key")
    if isinstance(explicit, str) and explicit:
        return explicit[:ITEM_KEY_MAX_LENGTH]
    canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "item-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class BatchFanoutService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SharedSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or SharedSettings()

    def effective_concurrency(self, requested: int | None) -> int:
        candidates = [self._settings.batch_max_concurrency, self._settings.batch_platform_limit]
        if requested is not None:
            candidates.append(requested)
        return max(1, min(candidates))

    async def fan_out(self, parent: TaskExecution, plan: BatchPlan) -> FanoutSummary:
        moment = datetime.now(UTC)
        concurrency = self.effective_concurrency(plan.max_concurrency)
        async with self._session_factory() as session:
            async with session.begin():
                locked = (
                    await session.execute(
                        select(TaskExecution)
                        .where(
                            TaskExecution.id == parent.id,
                            TaskExecution.is_deleted.is_(False),
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if locked is None:
                    raise BatchPlanError(f"parent task {parent.id} not found")
                created = await self._insert_children(session, locked, plan, moment, concurrency)
                ref = FanoutSummary(
                    parent_id=locked.id,
                    created=created,
                    item_count=len(plan.items),
                    concurrency=concurrency,
                    aggregate_mode=plan.aggregate_mode,
                )
                await session.execute(
                    update(TaskExecution)
                    .where(TaskExecution.id == locked.id)
                    .values(
                        task_type=TASK_TYPE_BATCH,
                        external_ref_json={BATCH_REF_KEY: ref.as_ref()},
                        update_time=moment,
                    )
                )
                if created:
                    await append_events(
                        session,
                        [
                            TaskEventSeed(
                                tenant_id=locked.tenant_id,
                                task_id=locked.id,
                                event_type=TaskEventType.FAN_OUT,
                                payload={
                                    "created": created,
                                    "item_count": ref.item_count,
                                    "concurrency": concurrency,
                                    "aggregate_mode": plan.aggregate_mode,
                                },
                            )
                        ],
                    )
        return ref

    async def _insert_children(
        self,
        session: AsyncSession,
        parent: TaskExecution,
        plan: BatchPlan,
        moment: datetime,
        concurrency: int,
    ) -> int:
        created = 0
        for index, item in enumerate(plan.items):
            values = self._child_values(parent, index, item, moment, concurrency)
            inserted_id = (
                await session.execute(
                    pg_insert(TaskExecution)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[TaskExecution.parent_id, TaskExecution.item_key],
                        index_where=sa.and_(
                            TaskExecution.parent_id.is_not(None),
                            TaskExecution.is_deleted == false(),
                        ),
                    )
                    .returning(TaskExecution.id)
                )
            ).scalar_one_or_none()
            if inserted_id is not None:
                created += 1
        return created

    def _child_values(
        self,
        parent: TaskExecution,
        index: int,
        item: Mapping[str, Any],
        moment: datetime,
        concurrency: int,
    ) -> dict[str, Any]:
        child_id = uuid.uuid4()
        item_key = item_key_of(index, item)
        return {
            "id": child_id,
            "tenant_id": parent.tenant_id,
            "parent_id": parent.id,
            "root_id": parent.root_id or parent.id,
            "item_key": item_key,
            "agent_id": parent.agent_id,
            "actor_user_id": parent.actor_user_id,
            "intent_key": parent.intent_key,
            "skill_id": parent.skill_id,
            "skill_artifact_id": parent.skill_artifact_id,
            "trigger_type": parent.trigger_type,
            "execution_mode": EXECUTION_MODE_ASYNC,
            "task_type": TASK_TYPE_SKILL,
            "status": str(TaskStatus.QUEUED),
            "input_json": dict(item),
            "execution_snapshot_schema_version": parent.execution_snapshot_schema_version,
            "execution_snapshot_json": parent.execution_snapshot_json,
            "snapshot_hash": parent.snapshot_hash,
            "idempotency_key": f"parent:{parent.id}:{item_key}",
            "priority": parent.priority if parent.priority is not None else INITIAL_PRIORITY,
            "attempt": 0,
            "max_attempts": parent.max_attempts,
            "not_before": moment if index < concurrency else PARKED_NOT_BEFORE,
            "deadline_at": parent.deadline_at,
            "delivery_mode": str(DeliveryMode.NONE),
            "delivery_status": str(DeliveryStatus.NONE),
            "delivery_key": f"task:{child_id}:final",
            "delivery_attempts": 0,
        }
