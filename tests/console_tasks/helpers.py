"""Console 调度/任务用例的共享种子：直接落 v2 的 Worker 真值行。

Console 不直连 `task.*`，但**测试**可以：种子替代「用户经 WebChat 自然语言确认 Draft」这条
前置链（Draft/确认由 Worker 套件覆盖），Console 用例只断言它读出来的事实。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskEvent,
    TaskExecution,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleRevision,
)


def _hash(seed: str) -> str:
    return "sha256:" + seed * 64


async def seed_schedule(
    tenant_id: str,
    *,
    name: str = "A 客户周报",
    status: str = "ACTIVE",
    mode: str = "AGENTIC",
    owner_user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    config_version: int = 3,
    next_fire_at: datetime | None = None,
    end_reason: str | None = None,
    attention_reason: str | None = None,
    timezone: str = "Asia/Shanghai",
    cron_expr: str = "0 9 * * 1",
    delivery_intent: str = "NONE",
    schedule_policy: dict[str, Any] | None = None,
    context_json: dict[str, Any] | None = None,
    execution_spec: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """写一条「已确认」的 Schedule + 当前 Revision（v2 配置只在 Revision 里）。"""
    owner = owner_user_id or uuid.uuid4()
    agent = agent_id or uuid.uuid4()
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    spec = execution_spec
    if spec is None:
        if mode == "REMINDER":
            spec = {"mode": "REMINDER", "reminder_text": "开会"}
        elif mode == "SKILL":
            spec = {"mode": "SKILL", "skill_id": str(uuid.uuid4()), "input_template": {}}
        else:
            spec = {
                "mode": "AGENTIC",
                "goal": "汇总 A 客户上周数据",
                "tool_allowlist": ["MCP:crm:customer.report"],
                "budget": {"max_steps": 12, "max_tool_calls": 20, "max_tokens": 0},
                "memory_mode": "READ_ONLY",
            }
    async with get_session_factory()() as session, session.begin():
        session.add(
            TaskSchedule(
                id=schedule_id,
                tenant_id=tenant_id,
                owner_user_id=owner,
                agent_id=agent,
                name=name,
                status=status,
                config_version=config_version,
                next_fire_at=next_fire_at or datetime.now(UTC) + timedelta(days=1),
                end_reason=end_reason,
                attention_reason=attention_reason,
                attention_since=datetime.now(UTC) if attention_reason else None,
                created_by=owner,
            )
        )
        await session.flush()
        session.add(
            TaskScheduleRevision(
                id=revision_id,
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                revision_no=2,
                mode=mode,
                instruction_raw="每周一给 A 客户发周报",
                normalized_goal="汇总 A 客户上周数据",
                recurrence_kind="CRON",
                cron_expr=cron_expr,
                timezone=timezone,
                schedule_policy=schedule_policy or {"concurrency_policy": "QUEUE"},
                context_json=context_json
                or {"parameters": {"customer": "A"}, "output": {"format": "markdown"}},
                execution_spec=spec,
                delivery_intent=delivery_intent,
                preflight_json={},
                created_by=owner,
            )
        )
        await session.flush()
        schedule = await session.get(TaskSchedule, schedule_id)
        assert schedule is not None
        schedule.current_revision_id = revision_id
    return schedule_id, revision_id


async def seed_fire(
    tenant_id: str,
    schedule_id: uuid.UUID,
    revision_id: uuid.UUID,
    *,
    scheduled_at: datetime,
    status: str = "EXECUTION_CREATED",
    execution_id: uuid.UUID | None = None,
    skip_reason: str | None = None,
    missed_count: int = 0,
    dst_resolution: str | None = None,
    admission_error_code: str | None = None,
) -> uuid.UUID:
    fire_id = uuid.uuid4()
    async with get_session_factory()() as session, session.begin():
        session.add(
            ScheduleFire(
                id=fire_id,
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                schedule_revision_id=revision_id,
                scheduled_at=scheduled_at,
                status=status,
                execution_id=execution_id,
                skip_reason=skip_reason,
                missed_count=missed_count,
                dst_resolution=dst_resolution,
                admission_error_code=admission_error_code,
            )
        )
    return fire_id


async def seed_execution(
    tenant_id: str,
    *,
    schedule_id: uuid.UUID | None = None,
    status: str = "COMPLETED",
    task_type: str = "SKILL",
    trigger_type: str = "SCHEDULED",
    actor_user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    wait_kind: str | None = None,
    wait_payload: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> uuid.UUID:
    execution_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with get_session_factory()() as session, session.begin():
        session.add(
            TaskExecution(
                id=execution_id,
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                agent_id=agent_id or uuid.uuid4(),
                actor_user_id=actor_user_id or uuid.uuid4(),
                trigger_type=trigger_type,
                task_type=task_type,
                status=status,
                wait_kind=wait_kind,
                wait_payload_json=wait_payload,
                input_json={},
                result_json=result,
                execution_snapshot_schema_version=4 if snapshot else 3,
                execution_snapshot_json=snapshot or {"schema_version": 3},
                snapshot_hash=_hash("c"),
                idempotency_key=f"console-scheduled-{execution_id}",
                not_before=now,
                deadline_at=now + timedelta(hours=1),
                started_at=started_at,
                finished_at=finished_at,
            )
        )
    return execution_id


async def seed_human(
    tenant_id: str,
    execution_id: uuid.UUID,
    *,
    question: str = "请选择客户",
    status: str = "OPEN",
    deadline_at: datetime | None = None,
) -> uuid.UUID:
    interaction_id = uuid.uuid4()
    async with get_session_factory()() as session, session.begin():
        session.add(
            TaskHumanInteraction(
                id=interaction_id,
                tenant_id=tenant_id,
                execution_id=execution_id,
                owner_user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                interaction_type="INPUT",
                status=status,
                question=question,
                deadline_at=deadline_at or datetime.now(UTC) + timedelta(hours=2),
            )
        )
    return interaction_id


async def seed_execution_event(
    tenant_id: str,
    execution_id: uuid.UUID,
    *,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    async with get_session_factory()() as session, session.begin():
        seq = int(
            await session.scalar(
                sa.select(sa.func.coalesce(sa.func.max(TaskEvent.seq), 0)).where(
                    TaskEvent.tenant_id == tenant_id,
                    TaskEvent.entity_type == "EXECUTION",
                    TaskEvent.entity_id == execution_id,
                )
            )
            or 0
        )
        session.add(
            TaskEvent(
                id=event_id,
                tenant_id=tenant_id,
                entity_type="EXECUTION",
                entity_id=execution_id,
                seq=seq + 1,
                event_type=event_type,
                payload_json=payload,
                occurred_at=occurred_at or datetime.now(UTC),
            )
        )
    return event_id


async def seed_schedule_event(
    tenant_id: str,
    schedule_id: uuid.UUID,
    *,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    async with get_session_factory()() as session, session.begin():
        seq = int(
            await session.scalar(
                sa.select(sa.func.coalesce(sa.func.max(TaskEvent.seq), 0)).where(
                    TaskEvent.tenant_id == tenant_id,
                    TaskEvent.entity_type == "SCHEDULE",
                    TaskEvent.entity_id == schedule_id,
                )
            )
            or 0
        )
        session.add(
            TaskEvent(
                id=event_id,
                tenant_id=tenant_id,
                entity_type="SCHEDULE",
                entity_id=schedule_id,
                seq=seq + 1,
                event_type=event_type,
                payload_json=payload,
                occurred_at=occurred_at or datetime.now(UTC),
            )
        )
    return event_id
