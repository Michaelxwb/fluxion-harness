"""API-10：Human 回答的双侧 CAS、幂等与恢复注入（design §3.2.6 / §3.3.8 / §3.5.8）。

一次回答同时满足两侧事实，缺一不可：

- **interaction**：`OPEN → RESOLVED`，同事务写回答与 `resolved_at`；
- **execution**：`WAITING_HUMAN → QUEUED`，`lease_epoch+1`、`resume_count+1`、
  `last_resume_reason=HUMAN`，并 CAS `cancel_requested=false`、`deadline_at > PG now`、
  旧 lease 已释放（`lease_owner/lease_until` 为空）。

终态执行永远不复活：CAS 落败即 `EXECUTION_NOT_RESUMABLE`，绝不按"状态看起来像等待"
就把行改回 QUEUED。回答的幂等以**回答内容**为键：同 interaction 同 response 重放首次结果，
不同 response 是冲突（`IDEMPOTENCY_MISMATCH`）——与 `Idempotency-Key` 台账互补，不互相替代。

恢复注入：回答事务在 `wait_payload_json` 留一次性续接标记（`human_interaction_id`），
claim 消费后清空；执行器按"回答时间晚于最近 checkpoint"取回这一次的回答内容。
问题本身不属于任何 Conversation（design §3.2.6）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import HumanInteractionStatus, TaskStatus, canonical_json
from pydantic import JsonValue
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models.task import (
    TaskExecution,
    TaskExecutionCheckpoint,
    TaskHumanInteraction,
)
from .task_events import EntityType, TaskEventSeed, TaskEventType, append_events
from .terminal_tasks import lock_task_tree

WAITING_HUMAN = str(TaskStatus.WAITING_HUMAN)
QUEUED = str(TaskStatus.QUEUED)
OPEN = str(HumanInteractionStatus.OPEN)
RESOLVED = str(HumanInteractionStatus.RESOLVED)
TIMED_OUT = str(HumanInteractionStatus.TIMED_OUT)
CANCELLED = str(HumanInteractionStatus.CANCELLED)
RESUME_REASON_HUMAN = "HUMAN"
#: `wait_payload_json` 上的一次性续接标记：claim 读到即按 Human 续接处理并清空。
HUMAN_RESUME_KEY = "human_interaction_id"


def response_hash(response: Mapping[str, Any]) -> str:
    """回答的规范化指纹：键序无关，同内容恒等（用于幂等判定与事件审计）。"""
    return "sha256:" + sha256(canonical_json(dict(response)).encode("utf-8")).hexdigest()


def resume_marker(interaction_id: uuid.UUID) -> dict[str, JsonValue]:
    return {HUMAN_RESUME_KEY: str(interaction_id)}


def pending_interaction_id(wait_payload: Mapping[str, Any] | None) -> uuid.UUID | None:
    """claim 侧读取一次性续接标记；坏形状按"没有标记"处理（不猜、不复活）。"""
    if not isinstance(wait_payload, Mapping):
        return None
    raw = wait_payload.get(HUMAN_RESUME_KEY)
    try:
        return uuid.UUID(str(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class HumanAnswer:
    """一次已被接受的回答（恢复注入的唯一输入）。"""

    interaction_id: uuid.UUID
    execution_id: uuid.UUID
    response: dict[str, Any]
    resolved_at: datetime


class HumanInteractionService:
    """回答一个 OPEN interaction；调用方负责提交或回滚，本服务不 commit。"""

    def __init__(self, session: AsyncSession, *, now: datetime) -> None:
        self._session = session
        self._now = now

    async def respond(
        self,
        interaction_id: uuid.UUID,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        response: Mapping[str, Any],
    ) -> tuple[uuid.UUID, uuid.UUID, str]:
        """返回 `(interaction_id, execution_id, execution_status)`；重放返回首次结果。"""
        interaction = await self._load(interaction_id, tenant_id)
        self._check_owner(interaction, actor_user_id=actor_user_id, agent_id=agent_id)
        if interaction.status != OPEN:
            return self._settled(interaction, response)
        execution = await self._load_execution(interaction)
        # 锁序与取消/超时一致：先任务树（operation→Schedule→Fire→root→parent→本行），再 interaction。
        await lock_task_tree(self._session, execution)
        interaction = await self._lock_interaction(interaction.id, tenant_id)
        if interaction.status != OPEN:
            return self._settled(interaction, response)
        if interaction.deadline_at <= self._now:
            # 过期不再改写：超时 sweep 是 TIMED_OUT 的唯一写入方。
            raise AppError(ErrorCode.HUMAN_INTERACTION_EXPIRED)
        _validate_response(interaction.input_schema, response)
        execution = await self._lock_execution(execution.id, tenant_id)
        await self._requeue(execution, interaction, response)
        return interaction.id, execution.id, QUEUED

    async def _load(self, interaction_id: uuid.UUID, tenant_id: str) -> TaskHumanInteraction:
        interaction = await self._session.scalar(
            select(TaskHumanInteraction).where(
                TaskHumanInteraction.id == interaction_id,
                TaskHumanInteraction.tenant_id == tenant_id,
                TaskHumanInteraction.is_deleted.is_(False),
            )
        )
        if interaction is None:
            # 跨租户与不存在同形：不暴露存在性。
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return interaction

    def _check_owner(
        self,
        interaction: TaskHumanInteraction,
        *,
        actor_user_id: uuid.UUID,
        agent_id: uuid.UUID | None,
    ) -> None:
        """回答只属于提问的 owner 与 Agent；调用方声明了 Agent 就必须一致。"""
        if interaction.owner_user_id != actor_user_id:
            raise AppError(ErrorCode.TASK_ACCESS_DENIED)
        if agent_id is not None and interaction.agent_id != agent_id:
            raise AppError(ErrorCode.TASK_ACCESS_DENIED)

    def _settled(
        self, interaction: TaskHumanInteraction, response: Mapping[str, Any]
    ) -> tuple[uuid.UUID, uuid.UUID, str]:
        """已决的 interaction：同内容重放首次结果，不同内容冲突，超时/取消显式拒绝。"""
        if interaction.status == RESOLVED:
            stored = interaction.response_json or {}
            if response_hash(stored) != response_hash(response):
                raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
            return interaction.id, interaction.execution_id, QUEUED
        if interaction.status == TIMED_OUT:
            raise AppError(ErrorCode.HUMAN_INTERACTION_EXPIRED)
        if interaction.status == CANCELLED:
            raise AppError(ErrorCode.EXECUTION_NOT_RESUMABLE)
        raise AppError(ErrorCode.EXECUTION_NOT_RESUMABLE)

    async def _load_execution(self, interaction: TaskHumanInteraction) -> TaskExecution:
        execution = await self._session.scalar(
            select(TaskExecution).where(
                TaskExecution.id == interaction.execution_id,
                TaskExecution.tenant_id == interaction.tenant_id,
                TaskExecution.is_deleted.is_(False),
            )
        )
        if execution is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return execution

    async def _lock_interaction(
        self, interaction_id: uuid.UUID, tenant_id: str
    ) -> TaskHumanInteraction:
        interaction = await self._session.scalar(
            select(TaskHumanInteraction)
            .where(
                TaskHumanInteraction.id == interaction_id,
                TaskHumanInteraction.tenant_id == tenant_id,
                TaskHumanInteraction.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert interaction is not None  # 上一读已确认存在且同租户
        return interaction

    async def _lock_execution(self, execution_id: uuid.UUID, tenant_id: str) -> TaskExecution:
        execution = await self._session.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.id == execution_id,
                TaskExecution.tenant_id == tenant_id,
                TaskExecution.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert execution is not None
        return execution

    async def _requeue(
        self,
        execution: TaskExecution,
        interaction: TaskHumanInteraction,
        response: Mapping[str, Any],
    ) -> None:
        """两侧 CAS 与事件同一事务：任何一侧落败都整体回滚。"""
        moment = self._now
        requeued = await self._session.execute(
            update(TaskExecution)
            .where(
                TaskExecution.id == execution.id,
                TaskExecution.tenant_id == execution.tenant_id,
                TaskExecution.status == WAITING_HUMAN,
                TaskExecution.cancel_requested.is_(False),
                TaskExecution.deadline_at > moment,
                TaskExecution.lease_owner.is_(None),
                TaskExecution.lease_until.is_(None),
                TaskExecution.is_deleted.is_(False),
            )
            .values(
                status=QUEUED,
                lease_epoch=TaskExecution.lease_epoch + 1,
                resume_count=TaskExecution.resume_count + 1,
                last_resume_reason=RESUME_REASON_HUMAN,
                not_before=moment,
                wait_kind=None,
                wait_payload_json=resume_marker(interaction.id),
                update_time=moment,
            )
            .returning(TaskExecution.lease_epoch, TaskExecution.resume_count)
            .execution_options(synchronize_session=False)
        )
        row = requeued.one_or_none()
        if row is None:
            raise AppError(ErrorCode.EXECUTION_NOT_RESUMABLE)
        lease_epoch, resume_count = int(row[0]), int(row[1])
        closed = await self._session.execute(
            update(TaskHumanInteraction)
            .where(
                TaskHumanInteraction.id == interaction.id,
                TaskHumanInteraction.tenant_id == interaction.tenant_id,
                TaskHumanInteraction.status == OPEN,
                TaskHumanInteraction.is_deleted.is_(False),
            )
            .values(
                status=RESOLVED,
                response_json=dict(response),
                resolved_at=moment,
                update_time=moment,
            )
            .returning(TaskHumanInteraction.id)
            .execution_options(synchronize_session=False)
        )
        if closed.one_or_none() is None:
            # 另一写方（取消/超时）已关闭它：本次整体回滚，执行不得被复活。
            raise AppError(ErrorCode.EXECUTION_NOT_RESUMABLE)
        await append_events(
            self._session,
            [
                TaskEventSeed(
                    tenant_id=execution.tenant_id,
                    entity_type=EntityType.EXECUTION,
                    entity_id=execution.id,
                    event_type=TaskEventType.RESUMED,
                    occurred_at=moment,
                    payload={
                        "reason": RESUME_REASON_HUMAN,
                        "interaction_id": str(interaction.id),
                        "lease_epoch": lease_epoch,
                        "resume_count": resume_count,
                    },
                ),
                TaskEventSeed(
                    tenant_id=execution.tenant_id,
                    entity_type=EntityType.HUMAN,
                    entity_id=interaction.id,
                    event_type=TaskEventType.RESOLVED,
                    occurred_at=moment,
                    payload={
                        "execution_id": str(execution.id),
                        "response_hash": response_hash(response),
                        "resolved_at": moment.isoformat(),
                    },
                ),
            ],
        )


def _validate_response(
    input_schema: Mapping[str, Any] | None, response: Mapping[str, Any]
) -> None:
    """按提问冻结的 `input_schema` 二次校验；非法回答不改任何状态。"""
    if not input_schema:
        return
    import jsonschema

    try:
        jsonschema.validate(instance=dict(response), schema=dict(input_schema))
    except jsonschema.ValidationError as exc:
        raise AppError(
            ErrorCode.COMMON_VALIDATION_ERROR,
            data={"reason": "HUMAN_RESPONSE_INVALID", "path": [str(part) for part in exc.absolute_path]},
        ) from exc
    except jsonschema.SchemaError as exc:
        # 冻结的 schema 本身坏了：显式拒绝，不把任何形状当成合法回答。
        raise AppError(
            ErrorCode.COMMON_VALIDATION_ERROR, data={"reason": "HUMAN_INPUT_SCHEMA_INVALID"}
        ) from exc


async def load_human_answer(
    sessions: async_sessionmaker[AsyncSession], task: TaskExecution
) -> HumanAnswer | None:
    """恢复同一个 Execution 时取出这一次的回答。

    口径是"回答发生在最近 checkpoint 之后"：续接跑出新 checkpoint 后再次恢复（回收/重试）
    不会重复注入；没有新回答时返回 None，恢复按 checkpoint 继续但不注入用户消息。
    """
    if task.last_checkpoint_id is None:
        return None
    async with sessions() as session:
        checkpoint_at = await session.scalar(
            select(TaskExecutionCheckpoint.create_time).where(
                TaskExecutionCheckpoint.id == task.last_checkpoint_id,
                TaskExecutionCheckpoint.tenant_id == task.tenant_id,
            )
        )
        if checkpoint_at is None:
            return None
        interaction = await session.scalar(
            select(TaskHumanInteraction)
            .where(
                TaskHumanInteraction.tenant_id == task.tenant_id,
                TaskHumanInteraction.execution_id == task.id,
                TaskHumanInteraction.status == RESOLVED,
                TaskHumanInteraction.resolved_at > checkpoint_at,
                TaskHumanInteraction.is_deleted.is_(False),
            )
            .order_by(TaskHumanInteraction.resolved_at.desc(), TaskHumanInteraction.id)
            .limit(1)
        )
    if interaction is None or interaction.resolved_at is None:
        return None
    return HumanAnswer(
        interaction_id=interaction.id,
        execution_id=interaction.execution_id,
        response=dict(interaction.response_json or {}),
        resolved_at=interaction.resolved_at,
    )


def human_answer_content(answer: HumanAnswer) -> str:
    """回答在模型上下文里的形态：用户消息携带严格 JSON（键序稳定，便于复现）。"""
    return json.dumps(answer.response, ensure_ascii=False, sort_keys=True)


__all__ = [
    "HUMAN_RESUME_KEY",
    "HumanAnswer",
    "HumanInteractionService",
    "human_answer_content",
    "load_human_answer",
    "pending_interaction_id",
    "response_hash",
    "resume_marker",
]
