"""TASK-012 · UPDATE 确认：Revision 只追加、CAS 只认 Draft 基线、旧执行按 started_at 收敛。

真实边界：真实 PG 行锁与真实 `task_execution`/`schedule_fire`/`task_event` 行。B-12 的三种旧
Execution（未开始 QUEUED、RUNNING、已开始后 reclaim 回 QUEUED）都用真实行播种，不用桩。

两个容易写错的判据在这里被钉住：

- `base_config_version` 只来自 Draft（Scheduler 推进 `next_fire_at/occurrence_count` 不改它，
  所以正常调度进度不会让待确认 Draft 失效）；
- 取消范围是「旧 Revision 且 `started_at IS NULL`」：已开始的执行保留旧 Snapshot，尚未生成
  Fire 的 occurrence 只重算指针，不伪造一条 Fire。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.application.schedule_drafts import schedule_draft_hash
from muad_agent_worker.application.task_events import TaskEventType
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
    TaskScheduleDraft,
    TaskScheduleRevision,
)
from muad_agent_worker.infrastructure.models.task_submission import TaskSubmission
from muad_contracts import ScheduleDraftRequest, SchedulePolicy

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
INSTRUCTION = "每周一 9 点提醒我发周报"


def payload_of(body: dict[str, Any]) -> dict[str, Any]:
    payload = ScheduleDraftRequest.model_validate(body).model_dump(mode="json")
    payload["delivery_intent"] = "NONE"
    return payload


async def seed_schedule(
    tenant: TenantContext,
    *,
    owner: uuid.UUID,
    agent: uuid.UUID,
    name: str = "周报",
    cron: str = "0 9 * * 1",
    reminder_text: str = "发周报",
    config_version: int = 1,
    status: str = "ACTIVE",
) -> tuple[TaskSchedule, TaskScheduleRevision]:
    now = datetime.now(UTC)
    async with tenant.session_factory() as session:
        schedule = TaskSchedule(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            owner_user_id=owner,
            agent_id=agent,
            name=name,
            status=status,
            config_version=config_version,
            next_fire_at=now + timedelta(hours=1) if status == "ACTIVE" else None,
            created_by=owner,
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            revision_no=1,
            mode="REMINDER",
            instruction_raw=INSTRUCTION,
            normalized_goal=reminder_text,
            recurrence_kind="CRON",
            cron_expr=cron,
            timezone=TZ,
            schedule_policy=SchedulePolicy().model_dump(mode="json"),
            context_json={"parameters": {}, "output": {}},
            execution_spec={"mode": "REMINDER", "reminder_text": reminder_text},
            delivery_intent="NONE",
            preflight_json={},
            created_by=owner,
        )
        session.add(revision)
        await session.flush()
        schedule.current_revision_id = revision.id
        await session.commit()
    return schedule, revision


def update_body(
    schedule: TaskSchedule,
    *,
    base_config_version: int,
    cron: str = "0 9 * * 1",
    reminder_text: str = "发周报",
    name: str = "周报",
) -> dict[str, Any]:
    return {
        "kind": "UPDATE",
        "mode": "REMINDER",
        "instruction": INSTRUCTION,
        "recurrence": {"kind": "CRON", "cron": cron, "timezone": TZ},
        "execution_spec": {"mode": "REMINDER", "reminder_text": reminder_text},
        "base_schedule_id": str(schedule.id),
        "base_config_version": base_config_version,
        "name": name,
    }


async def seed_draft(
    tenant: TenantContext,
    *,
    owner: uuid.UUID,
    agent: uuid.UUID,
    body: dict[str, Any],
    expires_in: int = 600,
) -> TaskScheduleDraft:
    payload = payload_of(body)
    now = datetime.now(UTC)
    draft = TaskScheduleDraft(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        owner_user_id=owner,
        agent_id=agent,
        kind="UPDATE",
        base_schedule_id=uuid.UUID(payload["base_schedule_id"]),
        base_config_version=payload["base_config_version"],
        draft_hash=schedule_draft_hash(payload),
        payload_json=payload,
        preview_json={
            "mode": payload["mode"],
            "what": "发周报",
            "when": f"CRON {payload['recurrence']['cron']} ({TZ})",
            "first_fire_at": (now + timedelta(hours=1)).isoformat(),
            "delivery": "NONE",
            "dependencies": [],
            "budget": {},
        },
        status="PENDING",
        expires_at=now + timedelta(seconds=expires_in),
        idempotency_key=f"draft-{uuid.uuid4()}",
    )
    async with tenant.session_factory() as session:
        session.add(draft)
        await session.commit()
    return draft


async def seed_execution(
    tenant: TenantContext,
    *,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    scheduled_at: datetime,
    status: str = "QUEUED",
    started_at: datetime | None = None,
) -> tuple[TaskExecution, ScheduleFire]:
    async with tenant.session_factory() as session:
        fire = ScheduleFire(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            schedule_revision_id=revision.id,
            scheduled_at=scheduled_at,
            status="EXECUTION_CREATED",
        )
        session.add(fire)
        await session.flush()
        execution = TaskExecution(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            schedule_fire_id=fire.id,
            schedule_revision_id=revision.id,
            scheduled_at=scheduled_at,
            agent_id=schedule.agent_id,
            actor_user_id=schedule.owner_user_id,
            trigger_type="SCHEDULED",
            task_type="REMINDER",
            status=status,
            input_json={},
            execution_snapshot_schema_version=4,
            execution_snapshot_json={"schema_version": 4},
            snapshot_hash="sha256:" + "a" * 64,
            idempotency_key=f"schedule:{schedule.id}:{scheduled_at.isoformat()}",
            attempt=0,
            max_attempts=3,
            not_before=scheduled_at,
            deadline_at=scheduled_at + timedelta(hours=1),
            started_at=started_at,
        )
        session.add(execution)
        await session.flush()
        fire.execution_id = execution.id
        await session.commit()
    return execution, fire


def headers(tenant: TenantContext, *, actor: uuid.UUID, key: str | None = None) -> dict[str, str]:
    return internal_service_headers(
        tenant.tenant_id,
        **{
            "X-Actor-User-Id": str(actor),
            "Idempotency-Key": key or f"confirm-{uuid.uuid4()}",
            "X-Confirmation-Surface": "WEBCHAT",
            "X-Confirmation-Ref": f"webchat-message-{uuid.uuid4()}",
        },
    )


async def confirm(
    client: AsyncClient,
    tenant: TenantContext,
    draft: TaskScheduleDraft,
    *,
    actor: uuid.UUID,
    name: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {"draft_hash": draft.draft_hash}
    if name is not None:
        body["name"] = name
    body.update(extra or {})
    return await client.post(
        f"/internal/schedule-drafts/{draft.id}/confirm",
        json=body,
        headers=headers(tenant, actor=actor),
    )


async def load_draft(tenant: TenantContext, draft_id: uuid.UUID) -> TaskScheduleDraft:
    async with tenant.session_factory() as session:
        draft = await session.get(TaskScheduleDraft, draft_id)
    assert draft is not None
    return draft


async def load_schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session:
        schedule = await session.get(TaskSchedule, schedule_id)
    assert schedule is not None
    return schedule


async def load_revision(
    tenant: TenantContext, revision_id: uuid.UUID
) -> TaskScheduleRevision | None:
    async with tenant.session_factory() as session:
        return await session.get(TaskScheduleRevision, revision_id)


async def revisions_of(tenant: TenantContext, schedule_id: uuid.UUID) -> list[TaskScheduleRevision]:
    async with tenant.session_factory() as session:
        rows = await session.execute(
            sa.select(TaskScheduleRevision)
            .where(
                TaskScheduleRevision.tenant_id == tenant.tenant_id,
                TaskScheduleRevision.schedule_id == schedule_id,
            )
            .order_by(TaskScheduleRevision.revision_no)
        )
        return list(rows.scalars().all())


async def load_execution(tenant: TenantContext, execution_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session:
        execution = await session.get(TaskExecution, execution_id)
    assert execution is not None
    return execution


async def load_fire(tenant: TenantContext, fire_id: uuid.UUID) -> ScheduleFire:
    async with tenant.session_factory() as session:
        fire = await session.get(ScheduleFire, fire_id)
    assert fire is not None
    return fire


async def count_rows(tenant: TenantContext, model: Any) -> int:
    async with tenant.session_factory() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(model)
                .where(model.tenant_id == tenant.tenant_id)
            )
            or 0
        )


async def events_for(tenant: TenantContext, entity_id: uuid.UUID) -> list[TaskEvent]:
    async with tenant.session_factory() as session:
        rows = await session.execute(
            sa.select(TaskEvent).where(
                TaskEvent.tenant_id == tenant.tenant_id, TaskEvent.entity_id == entity_id
            )
        )
        return list(rows.scalars().all())


async def assert_nothing_confirmed(tenant: TenantContext, draft: TaskScheduleDraft) -> None:
    unchanged = await load_draft(tenant, draft.id)
    assert unchanged.status == "PENDING"
    assert unchanged.confirmed_at is None and unchanged.confirmed_by_user_id is None
    assert unchanged.confirmation_surface is None and unchanged.confirmation_ref is None
    assert unchanged.schedule_id is None
    assert await count_rows(tenant, TaskSubmission) == 0


async def test_update_appends_a_revision_and_bumps_config_version(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, first = await seed_schedule(tenant, owner=owner, agent=agent)
    draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(schedule, base_config_version=1, cron="0 10 * * 1"),
    )

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    updated = await load_schedule(tenant, schedule.id)
    revisions = await revisions_of(tenant, schedule.id)
    assert [item.revision_no for item in revisions] == [1, 2]
    assert data["revision_no"] == 2
    assert data["config_version"] == 2
    assert updated.config_version == 2
    assert updated.current_revision_id == revisions[1].id
    assert revisions[1].cron_expr == "0 10 * * 1"
    assert revisions[1].created_by == owner
    # Schedule 的 next_fire_at 按新 Revision 重算，且旧 Revision 一字未改。
    assert updated.next_fire_at is not None
    assert updated.next_fire_at.astimezone(ZoneInfo(TZ)).hour == 10
    assert revisions[0].cron_expr == "0 9 * * 1"
    assert revisions[0].context_json == {"parameters": {}, "output": {}}
    assert revisions[0].execution_spec == {"mode": "REMINDER", "reminder_text": "发周报"}
    assert (await load_draft(tenant, draft.id)).schedule_id == schedule.id
    assert (await load_draft(tenant, draft.id)).status == "CONFIRMED"

    events = await events_for(tenant, schedule.id)
    assert [(item.event_type, item.seq) for item in events] == [
        (TaskEventType.UPDATED.value, 1)
    ]
    assert events[0].payload_json["revision_no"] == 2


async def test_stale_base_version_rolls_the_confirmation_back(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, _ = await seed_schedule(tenant, owner=owner, agent=agent, config_version=3)
    draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(schedule, base_config_version=1, cron="0 10 * * 1"),
    )

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_VERSION_CONFLICT"
    await assert_nothing_confirmed(tenant, draft)
    unchanged = await load_schedule(tenant, schedule.id)
    assert unchanged.config_version == 3
    assert len(await revisions_of(tenant, schedule.id)) == 1


async def test_request_body_cannot_override_the_cas_baseline(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """`expected_config_version` 不是确认请求的字段：体里出现即拒绝，不会变成覆盖入口。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, _ = await seed_schedule(tenant, owner=owner, agent=agent)
    draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(schedule, base_config_version=1, cron="0 10 * * 1"),
    )

    rejected = await confirm(
        client, tenant, draft, actor=owner, extra={"expected_config_version": 999}
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "COMMON_VALIDATION_ERROR"
    await assert_nothing_confirmed(tenant, draft)
    assert len(await revisions_of(tenant, schedule.id)) == 1


async def test_scheduler_progress_does_not_invalidate_a_pending_draft(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """`next_fire_at/occurrence_count/last_fire_at` 是调度进度，不是用户配置。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, _ = await seed_schedule(tenant, owner=owner, agent=agent)
    draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(schedule, base_config_version=1, cron="0 10 * * 1"),
    )
    async with tenant.session_factory() as session:
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == schedule.id)
            .values(
                next_fire_at=datetime.now(UTC) + timedelta(days=3),
                last_fire_at=datetime.now(UTC),
                occurrence_count=7,
            )
        )
        await session.commit()

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["config_version"] == 2
    updated = await load_schedule(tenant, schedule.id)
    assert updated.occurrence_count == 7  # 进度不被确认覆盖
    assert updated.last_fire_at is not None


async def test_execution_affecting_update_cancels_only_not_started_executions(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-12：未开始 QUEUED 取消；RUNNING 与已开始后 reclaim 回 QUEUED 的保留；不造新 Fire。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, first = await seed_schedule(tenant, owner=owner, agent=agent)
    now = datetime.now(UTC)
    queued, queued_fire = await seed_execution(
        tenant, schedule=schedule, revision=first, scheduled_at=now + timedelta(minutes=5)
    )
    running, running_fire = await seed_execution(
        tenant,
        schedule=schedule,
        revision=first,
        scheduled_at=now,
        status="RUNNING",
        started_at=now,
    )
    reclaimed, reclaimed_fire = await seed_execution(
        tenant,
        schedule=schedule,
        revision=first,
        scheduled_at=now + timedelta(minutes=10),
        status="QUEUED",
        started_at=now - timedelta(minutes=5),
    )
    fires_before = await count_rows(tenant, ScheduleFire)
    draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(
            schedule, base_config_version=1, cron="0 10 * * 1", reminder_text="发新周报"
        ),
    )

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 200, response.text
    cancelled = await load_execution(tenant, queued.id)
    assert cancelled.status == "CANCELLED"
    assert cancelled.cancel_requested is True and cancelled.finished_at is not None
    cancelled_fire = await load_fire(tenant, queued_fire.id)
    assert cancelled_fire.status == "CANCELLED"
    assert cancelled_fire.skip_reason == "PAUSED_BY_UPDATE"

    still_running = await load_execution(tenant, running.id)
    assert still_running.status == "RUNNING" and still_running.started_at is not None
    assert (await load_fire(tenant, running_fire.id)).status == "EXECUTION_CREATED"

    requeued = await load_execution(tenant, reclaimed.id)
    assert requeued.status == "QUEUED" and requeued.started_at is not None
    assert (await load_fire(tenant, reclaimed_fire.id)).status == "EXECUTION_CREATED"

    # 未生成的 occurrence 不补 Fire：总数不变，新 Revision 只推进 next_fire_at。
    assert await count_rows(tenant, ScheduleFire) == fires_before
    revisions = await revisions_of(tenant, schedule.id)
    assert [item.revision_no for item in revisions] == [1, 2]
    assert revisions[1].execution_spec == {"mode": "REMINDER", "reminder_text": "发新周报"}

    assert [item.event_type for item in await events_for(tenant, queued.id)] == [
        TaskEventType.CANCELLED.value
    ]
    assert [item.event_type for item in await events_for(tenant, queued_fire.id)] == [
        TaskEventType.CANCELLED.value
    ]
    assert await events_for(tenant, running.id) == []


async def test_display_only_change_keeps_executions_and_next_fire(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, first = await seed_schedule(tenant, owner=owner, agent=agent)
    now = datetime.now(UTC)
    queued, fire = await seed_execution(
        tenant, schedule=schedule, revision=first, scheduled_at=now + timedelta(minutes=5)
    )
    next_fire_before = (await load_schedule(tenant, schedule.id)).next_fire_at
    draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(schedule, base_config_version=1, name="周报（新名字）"),
    )

    response = await confirm(client, tenant, draft, actor=owner, name="周报（新名字）")

    assert response.status_code == 200, response.text
    updated = await load_schedule(tenant, schedule.id)
    assert updated.name == "周报（新名字）"
    assert updated.config_version == 2
    assert updated.next_fire_at == next_fire_before
    assert (await load_execution(tenant, queued.id)).status == "QUEUED"
    assert (await load_fire(tenant, fire.id)).status == "EXECUTION_CREATED"
    assert await count_rows(tenant, TaskSubmission) == 1


@pytest.mark.parametrize("status", ["ENDED", "CANCELLED"])
async def test_terminal_schedule_cannot_be_updated(
    client: AsyncClient, tenant: TenantContext, status: str
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, _ = await seed_schedule(tenant, owner=owner, agent=agent, status=status)
    draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(schedule, base_config_version=1, cron="0 10 * * 1"),
    )

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_STATE_INVALID"
    await assert_nothing_confirmed(tenant, draft)
    assert len(await revisions_of(tenant, schedule.id)) == 1


async def test_concurrent_updates_from_the_same_base_let_exactly_one_win(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """两个 Draft 都读到版本 1：先提交者生效，后提交者明确冲突且不覆盖。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, _ = await seed_schedule(tenant, owner=owner, agent=agent)
    first_draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(schedule, base_config_version=1, cron="0 10 * * 1"),
    )
    second_draft = await seed_draft(
        tenant,
        owner=owner,
        agent=agent,
        body=update_body(
            schedule, base_config_version=1, cron="0 11 * * 1", reminder_text="并发版本"
        ),
    )

    first, second = await asyncio.gather(
        confirm(client, tenant, first_draft, actor=owner),
        confirm(client, tenant, second_draft, actor=owner),
    )

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409]
    loser = second if first.status_code == 200 else first
    assert loser.json()["code"] == "SCHEDULE_VERSION_CONFLICT"
    updated = await load_schedule(tenant, schedule.id)
    revisions = await revisions_of(tenant, schedule.id)
    assert updated.config_version == 2
    assert len(revisions) == 2
    assert revisions[1].revision_no == 2
    # 败者的 Draft 未被消耗、也没有留下任何确认事实。
    loser_draft = second_draft if second.status_code == 409 else first_draft
    untouched = await load_draft(tenant, loser_draft.id)
    assert untouched.status == "PENDING" and untouched.confirmed_at is None
    assert untouched.schedule_id is None
    assert await count_rows(tenant, TaskSubmission) == 1


async def test_revisions_are_append_only_across_updates(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, first = await seed_schedule(tenant, owner=owner, agent=agent)
    original = {
        "cron_expr": first.cron_expr,
        "context_json": first.context_json,
        "execution_spec": first.execution_spec,
        "instruction_raw": first.instruction_raw,
        "update_time": first.update_time,
    }

    second_body = update_body(schedule, base_config_version=1, cron="0 10 * * 1")
    second_draft = await seed_draft(tenant, owner=owner, agent=agent, body=second_body)
    assert (await confirm(client, tenant, second_draft, actor=owner)).status_code == 200

    third_body = update_body(
        schedule,
        base_config_version=2,
        cron="0 11 * * 1",
        reminder_text="发新周报",
    )
    third_draft = await seed_draft(tenant, owner=owner, agent=agent, body=third_body)
    assert (await confirm(client, tenant, third_draft, actor=owner)).status_code == 200

    revisions = await revisions_of(tenant, schedule.id)
    assert [item.revision_no for item in revisions] == [1, 2, 3]
    assert revisions[0].cron_expr == original["cron_expr"]
    assert revisions[0].context_json == original["context_json"]
    assert revisions[0].execution_spec == original["execution_spec"]
    assert revisions[0].instruction_raw == original["instruction_raw"]
    assert revisions[0].update_time == original["update_time"]
    assert revisions[1].cron_expr == "0 10 * * 1"
    assert revisions[2].cron_expr == "0 11 * * 1"
    updated = await load_schedule(tenant, schedule.id)
    assert updated.config_version == 3
    assert updated.current_revision_id == revisions[2].id
    assert updated.name == "周报"
    assert await count_rows(tenant, TaskSubmission) == 2
