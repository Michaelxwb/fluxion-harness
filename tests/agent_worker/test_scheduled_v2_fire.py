"""TASK-014 · 生成 Fire 并原子提交 Execution 与调度进度。

覆盖：S-04/S-07/S-08、E-02/E-03/E-06/E-10/E-13、B-01..B-04。

真实边界：真实 PostgreSQL（行锁、唯一键、循环 FK、advisory 锁）与真实 Scheduler/Fire 写入
路径；只把 Console 的网络取数替成协议实现（`resolve_schedule_materials`），快照组装、
准入分类、Fire 台账、Execution 冻结与进度推进全部走生产代码。

一条事实一个用例：occurrence 身份、幂等键、并发策略、准入分类、misfire/DST/配额、双
Scheduler 竞态、事务原子性、锁外准入与租户公平性。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_worker.application.task_events import EntityType
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.scheduler import fire as fire_module
from muad_agent_worker.scheduler.client import ScheduleMaterialsError
from muad_agent_worker.scheduler.fire import TRANSIENT_FAILURES_BEFORE_ATTENTION
from muad_agent_worker.scheduler.recurrence import execution_key
from muad_agent_worker.scheduler.service import SchedulerLoop
from muad_contracts import ScheduleAdmissionMaterials, ScheduleAdmissionRequest, ScheduleMode

from agent_worker.conftest import TenantContext
from agent_worker.helpers import persist_task
from agent_worker.test_scheduled_v2_snapshot import make_materials

pytestmark = pytest.mark.integration

SHANGHAI = "Asia/Shanghai"
NEW_YORK = "America/New_York"
#: 2026-03-08 09:00 Asia/Shanghai（上海无 DST，作为确定性基准槽）。
SLOT = datetime(2026, 3, 8, 1, 0, tzinfo=UTC)
OWNER = uuid.UUID("5a1d0c1e-0000-4000-8000-00000000f014")
AGENT = uuid.UUID("5a1d0c1e-0000-4000-8000-0000000af014")
SKILL = uuid.UUID("5a1d0c1e-0000-4000-8000-000000005014")


# --------------------------------------------------------------------- doubles


class MaterialsClient:
    """`resolve_schedule_materials` 的可编排替身：只有网络边界被替换。"""

    def __init__(
        self,
        materials: ScheduleAdmissionMaterials | None = None,
        *,
        errors: list[Exception] | None = None,
        failure: Exception | None = None,
        hook: Any = None,
    ) -> None:
        self.materials = materials
        self.errors = list(errors or [])
        self.failure = failure
        self.hook = hook
        self.calls: list[tuple[str, ScheduleAdmissionRequest]] = []

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        self.calls.append((tenant_id, payload))
        if self.hook is not None:
            await self.hook()
        if self.errors:
            raise self.errors.pop(0)
        if self.failure is not None:
            raise self.failure
        assert self.materials is not None
        return self.materials


class SettingsClient:
    """平台设置快照替身：默认空文档（等价于「该租户无记录」）。"""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> Any:
        from muad_agent_worker.application.ports import PlatformSettingsSnapshot

        return PlatformSettingsSnapshot(revision=1, settings=self.settings)


def settings_with(**schedule: int) -> SettingsClient:
    return SettingsClient({"schedule": schedule})


def materials_for(skill_id: uuid.UUID = SKILL) -> ScheduleAdmissionMaterials:
    return make_materials(mode=ScheduleMode.SKILL, skill_id=skill_id)


# --------------------------------------------------------------------- seeds


@dataclass(frozen=True)
class Seeded:
    schedule_id: uuid.UUID
    revision_id: uuid.UUID
    agent_id: uuid.UUID
    owner_user_id: uuid.UUID


def payload_for(mode: str, *, skill_id: uuid.UUID = SKILL, input_template=None) -> dict[str, Any]:
    if mode == "REMINDER":
        return {"kind": "REMINDER", "text": "开会"}
    if mode == "SKILL":
        return {
            "kind": "SKILL_PLAN",
            "skill_id": str(skill_id),
            "input": input_template or {"report_day": "{{previous_day}}"},
            "argv": [],
            "window": {"kind": "DAY", "offset": -1},
        }
    return {
        "kind": "AGENT_TURN",
        "prompt": "检查一次",
        "tools_allow": [],
        "budget_ref": "platform",
    }


async def seed(
    tenant: TenantContext,
    *,
    tenant_id: str | None = None,
    agent_id: uuid.UUID = AGENT,
    owner: uuid.UUID = OWNER,
    mode: str = "SKILL",
    kind: str = "CRON",
    cron: str | None = "0 9 * * *",
    timezone: str = SHANGHAI,
    run_at: datetime | None = None,
    next_fire_at: datetime | None = None,
    occurrence_count: int = 0,
    schedule_policy: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    status: str = "ACTIVE",
    context: dict[str, Any] | None = None,
) -> Seeded:
    """种一条 Schedule + Revision 1；不退化成 Draft/确认流程（那是 TASK-012 的用例面）。"""
    once = kind == "ONCE"
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskSchedule(
                id=schedule_id,
                tenant_id=tenant_id or tenant.tenant_id,
                owner_user_id=owner,
                agent_id=agent_id,
                name="policy check",
                status=status,
                config_version=1,
                occurrence_count=occurrence_count,
                next_fire_at=next_fire_at,
                created_by=owner,
            )
        )
        await session.flush()
        session.add(
            TaskScheduleRevision(
                id=revision_id,
                tenant_id=tenant_id or tenant.tenant_id,
                schedule_id=schedule_id,
                revision_no=1,
                mode=mode,
                instruction_raw="每天检查一次",
                normalized_goal="检查一次",
                recurrence_kind=kind,
                cron_expr=None if once else cron,
                run_at=run_at.astimezone(UTC) if run_at is not None else None,
                timezone=timezone,
                schedule_policy=schedule_policy or {},
                context_json=context or {},
                payload_kind={"REMINDER": "REMINDER", "SKILL": "SKILL_PLAN", "AGENTIC": "AGENT_TURN"}[mode],
                payload_json=payload or payload_for(mode),
                delivery_intent="NONE",
                preflight_json={},
                created_by=owner,
            )
        )
        await session.flush()
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(current_revision_id=revision_id)
        )
    return Seeded(schedule_id, revision_id, agent_id, owner)


async def set_next(tenant: TenantContext, schedule_id: uuid.UUID, moment: datetime) -> None:
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(next_fire_at=moment)
        )


async def fetch_schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session:
        row = await session.get(TaskSchedule, schedule_id)
    assert row is not None
    return row


async def fires(tenant: TenantContext, schedule_id: uuid.UUID) -> list[ScheduleFire]:
    async with tenant.session_factory() as session:
        rows = await session.scalars(
            sa.select(ScheduleFire)
            .where(ScheduleFire.schedule_id == schedule_id)
            .order_by(ScheduleFire.scheduled_at)
        )
    return list(rows)


async def executions(tenant: TenantContext, schedule_id: uuid.UUID) -> list[TaskExecution]:
    async with tenant.session_factory() as session:
        rows = await session.scalars(
            sa.select(TaskExecution)
            .where(TaskExecution.schedule_id == schedule_id)
            .order_by(TaskExecution.create_time)
        )
    return list(rows)


async def events(
    tenant: TenantContext, entity_id: uuid.UUID, entity_type: EntityType
) -> list[TaskEvent]:
    async with tenant.session_factory() as session:
        rows = await session.scalars(
            sa.select(TaskEvent)
            .where(TaskEvent.entity_id == entity_id, TaskEvent.entity_type == entity_type.value)
            .order_by(TaskEvent.seq)
        )
    return list(rows)


async def event_types(
    tenant: TenantContext, entity_id: uuid.UUID, entity_type: EntityType
) -> list[str]:
    return [event.event_type for event in await events(tenant, entity_id, entity_type)]


def loop_for(
    tenant: TenantContext,
    client: MaterialsClient,
    *,
    settings_client: Any = None,
) -> SchedulerLoop:
    return SchedulerLoop(
        tenant.session_factory,
        client,
        tenant.settings,
        settings_client=settings_client or SettingsClient(),
    )


# --------------------------------------------------------------------- 1. 写入形状


async def test_fire_freezes_execution_and_advances_progress(tenant: TenantContext) -> None:
    """一次触发：唯一 Fire + 冻结 Execution + 进度推进；`config_version` 不动。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    materials = materials_for()
    client = MaterialsClient(materials)
    loop = loop_for(tenant, client)

    execution_id = await loop.run_once(now=SLOT + timedelta(seconds=1))

    assert execution_id is not None
    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "EXECUTION_CREATED"
    assert fire.execution_id == execution_id
    assert fire.schedule_revision_id == seeded.revision_id
    assert fire.scheduled_at == SLOT
    assert fire.missed_count == 0 and fire.skip_reason is None

    [execution] = await executions(tenant, seeded.schedule_id)
    assert execution.id == execution_id
    assert execution.schedule_fire_id == fire.id
    assert execution.schedule_revision_id == seeded.revision_id
    assert execution.scheduled_at == SLOT
    assert execution.trigger_type == "SCHEDULED"
    assert execution.task_type == "SKILL"
    assert execution.status == "QUEUED"
    assert execution.started_at is None
    assert execution.idempotency_key == execution_key(seeded.schedule_id, SLOT)
    assert re.fullmatch(rf"schedule:{seeded.schedule_id}:.+\d{{6}}Z", execution.idempotency_key)
    # SKILL 绑定本次准入冻结的 artifact，不是创建时的配置。
    assert execution.skill_id == materials.skills[0].skill_id
    assert execution.skill_artifact_id == materials.skills[0].artifact_id
    assert execution.execution_snapshot_schema_version == 4
    assert execution.snapshot_hash.startswith("sha256:") and execution.snapshot_bytes > 0
    assert "sk-" not in str(execution.execution_snapshot_json)
    assert execution.input_json == {
        "kind": "SKILL_PLAN",
        "mode": "SKILL",
        "session_target": "isolated",
        "skill_id": str(SKILL),
        "input": {"report_day": "2026-03-07"},
        "argv": [],
        "window": {"kind": "DAY", "offset": -1},
        "context": {},
    }

    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.occurrence_count == 1
    assert refreshed.last_fire_at == SLOT
    assert refreshed.status == "ACTIVE"
    assert refreshed.config_version == 1, "调度进度不得让待确认 Draft 的 CAS 失效"
    assert refreshed.next_fire_at == SLOT + timedelta(days=1), "上海 09:00 的下一槽"

    assert await event_types(tenant, fire.id, EntityType.FIRE) == ["EXECUTION_CREATED"]
    assert await event_types(tenant, execution_id, EntityType.EXECUTION) == ["QUEUED"]
    assert [tenant_id for tenant_id, _payload in client.calls] == [tenant.tenant_id]


async def test_once_schedule_ends_completed_after_its_single_fire(tenant: TenantContext) -> None:
    run_at = SLOT
    seeded = await seed(
        tenant, kind="ONCE", cron=None, run_at=run_at, next_fire_at=run_at
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=run_at + timedelta(seconds=1)) is not None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "EXECUTION_CREATED"
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ENDED"
    assert refreshed.end_reason == "COMPLETED"
    assert refreshed.ended_at is not None
    assert refreshed.next_fire_at is None
    assert refreshed.last_fire_at == run_at
    assert refreshed.occurrence_count == 1


@pytest.mark.parametrize("mode", ["REMINDER", "AGENTIC"])
async def test_reminder_and_agentic_executions_bind_no_skill(
    tenant: TenantContext, mode: str
) -> None:
    """只有 SKILL 模式绑定 Skill 列；REMINDER/AGENTIC 的 task_type 就是各自模式。"""
    seeded = await seed(
        tenant, mode=mode, next_fire_at=SLOT, payload=payload_for(mode)
    )
    materials = make_materials(mode=ScheduleMode(mode), skill_id=SKILL)
    loop = loop_for(tenant, MaterialsClient(materials))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None

    [execution] = await executions(tenant, seeded.schedule_id)
    assert execution.task_type == mode
    assert execution.skill_id is None
    assert execution.skill_artifact_id is None
    assert execution.skill_execution_mode is None
    assert execution.input_json["mode"] == mode
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.execution_id) == ("EXECUTION_CREATED", execution.id)


# --------------------------------------------------------------------- 2. 幂等身份


async def test_fire_identity_ignores_revision_and_never_executes_twice(
    tenant: TenantContext,
) -> None:
    """同一计划时刻在配置更新后仍只有一个 Fire；revision 是属性不是身份。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(materials_for()))
    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None

    # 模拟配置更新：新 Revision 成为 current，指针被重算回同一时刻（测试专用回拨）。
    new_revision_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskScheduleRevision(
                id=new_revision_id,
                tenant_id=tenant.tenant_id,
                schedule_id=seeded.schedule_id,
                revision_no=2,
                mode="SKILL",
                instruction_raw="每天检查两次",
                normalized_goal="检查一次",
                recurrence_kind="CRON",
                cron_expr="0 9 * * *",
                timezone=SHANGHAI,
                schedule_policy={},
                context_json={},
                payload_kind="SKILL_PLAN",
                payload_json=payload_for("SKILL"),
                delivery_intent="NONE",
                preflight_json={},
                created_by=seeded.owner_user_id,
            )
        )
        await session.flush()
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == seeded.schedule_id)
            .values(current_revision_id=new_revision_id, config_version=2)
        )
    await set_next(tenant, seeded.schedule_id, SLOT)

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.schedule_revision_id == seeded.revision_id, "已生成 Fire 的 Revision 不变"
    assert len(await executions(tenant, seeded.schedule_id)) == 1
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.occurrence_count == 1, "冲突的 occurrence 不重复计槽"
    assert refreshed.last_fire_at == SLOT
    assert refreshed.next_fire_at is not None and refreshed.next_fire_at > SLOT, "指针必须前移"
    assert refreshed.config_version == 2


async def test_repeated_tick_for_the_same_pointer_creates_nothing(tenant: TenantContext) -> None:
    seeded = await seed(tenant, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(materials_for()))
    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None

    assert await loop.run_once(now=SLOT + timedelta(seconds=2)) is None
    assert len(await fires(tenant, seeded.schedule_id)) == 1
    assert len(await executions(tenant, seeded.schedule_id)) == 1


# --------------------------------------------------------------------- 3. 并发策略


async def test_skip_if_running_blocks_on_any_non_terminal_root(tenant: TenantContext) -> None:
    """阻断集含 QUEUED：上一轮排队中的根执行同样挡住本次 occurrence。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    running = await persist_task(
        tenant,
        schedule_id=seeded.schedule_id,
        status="RUNNING",
        task_type="SKILL",
        trigger_type="SCHEDULED",
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "SKIPPED"
    assert fire.skip_reason == "OVERLAP"
    assert fire.execution_id is None
    # 只保留预先存在的那条执行，本次不建新执行。
    assert [item.id for item in await executions(tenant, seeded.schedule_id)] == [running.id]
    assert await event_types(tenant, fire.id, EntityType.FIRE) == ["SKIPPED"]
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ACTIVE"
    assert refreshed.last_fire_at is None, "跳过不是一次触发"
    assert refreshed.next_fire_at == SLOT + timedelta(days=1)


async def test_skip_if_running_ignores_child_executions(tenant: TenantContext) -> None:
    """Child 不是根执行，不阻断；Fire 照常建 Execution。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    parent = await persist_task(tenant, status="RUNNING", task_type="BATCH")
    await persist_task(
        tenant,
        schedule_id=seeded.schedule_id,
        parent_id=parent.id,
        root_id=parent.id,
        item_key="item-1",
        status="RUNNING",
        task_type="BATCH",
        trigger_type="SCHEDULED",
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "EXECUTION_CREATED"


@pytest.mark.parametrize(
    "policy",
    [
        {"concurrency_policy": "QUEUE"},
        {"concurrency_policy": "ALLOW_PARALLEL", "max_concurrency": 2},
    ],
)
async def test_queue_and_parallel_always_create_execution(
    tenant: TenantContext, policy: dict[str, Any]
) -> None:
    """QUEUE/ALLOW_PARALLEL 的 Fire 创建总是建 Execution；并发由 claim 决定，不在此降级。"""
    seeded = await seed(tenant, next_fire_at=SLOT, schedule_policy=policy)
    await persist_task(
        tenant,
        schedule_id=seeded.schedule_id,
        status="RUNNING",
        task_type="SKILL",
        trigger_type="SCHEDULED",
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "EXECUTION_CREATED"
    created = [item for item in await executions(tenant, seeded.schedule_id) if item.schedule_fire_id]
    assert len(created) == 1
    assert created[0].status == "QUEUED"
    assert created[0].scheduled_at == SLOT


# --------------------------------------------------------------------- 4. 准入分类


def _permanent(code: str = "SKILL_DISABLED") -> ScheduleMaterialsError:
    return ScheduleMaterialsError(
        "denied", code="SCHEDULE_DEPENDENCY_MISSING", data={"admission_code": code}
    )


async def test_permanent_admission_failure_moves_cron_to_needs_attention(
    tenant: TenantContext,
) -> None:
    seeded = await seed(tenant, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(failure=_permanent()))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "FAILED_ADMISSION"
    assert fire.admission_error_code == "SKILL_DISABLED"
    assert fire.execution_id is None
    assert await executions(tenant, seeded.schedule_id) == []
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "NEEDS_ATTENTION"
    assert refreshed.attention_reason == "SKILL_DISABLED"
    assert refreshed.attention_since is not None
    assert refreshed.next_fire_at is None
    assert "NEEDS_ATTENTION" in await event_types(tenant, seeded.schedule_id, EntityType.SCHEDULE)
    assert await event_types(tenant, fire.id, EntityType.FIRE) == ["FAILED_ADMISSION"]


async def test_once_permanent_admission_failure_ends_with_admission_failed(
    tenant: TenantContext,
) -> None:
    seeded = await seed(tenant, kind="ONCE", cron=None, run_at=SLOT, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(failure=_permanent("GRANT_REVOKED")))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ENDED"
    assert refreshed.end_reason == "ADMISSION_FAILED"
    assert refreshed.next_fire_at is None
    assert refreshed.attention_reason is None, "ONCE 不进 NEEDS_ATTENTION"
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.admission_error_code) == ("FAILED_ADMISSION", "GRANT_REVOKED")


async def test_once_transient_failure_retries_inside_one_admission_then_ends(
    tenant: TenantContext,
) -> None:
    """ONCE 的有界重试只在本次锁外 Admission 内；仍失败即 ENDED，Fire 之后不补建 Execution。"""
    seeded = await seed(tenant, kind="ONCE", cron=None, run_at=SLOT, next_fire_at=SLOT)
    transient = ScheduleMaterialsError("unavailable", code="RESOLVE_UNAVAILABLE")
    client = MaterialsClient(errors=[transient, transient])
    loop = loop_for(tenant, client)

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    assert len(client.calls) == 2, "一次 Admission 内允许一次重试，不跨 tick"
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert (refreshed.status, refreshed.end_reason) == ("ENDED", "ADMISSION_FAILED")
    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "FAILED_ADMISSION"
    assert await executions(tenant, seeded.schedule_id) == []


async def test_transient_failure_keeps_cron_active_until_the_streak_threshold(
    tenant: TenantContext,
) -> None:
    seeded = await seed(tenant, next_fire_at=SLOT, cron="0 * * * *", timezone="UTC")
    transient = ScheduleMaterialsError("unavailable", code="RESOLVE_UNAVAILABLE")
    loop = loop_for(tenant, MaterialsClient(errors=[transient] * TRANSIENT_FAILURES_BEFORE_ATTENTION))

    for attempt in range(TRANSIENT_FAILURES_BEFORE_ATTENTION):
        moment = SLOT + timedelta(hours=attempt)
        await set_next(tenant, seeded.schedule_id, moment)
        assert await loop.run_once(now=moment + timedelta(seconds=1)) is None
        refreshed = await fetch_schedule(tenant, seeded.schedule_id)
        if attempt < TRANSIENT_FAILURES_BEFORE_ATTENTION - 1:
            assert refreshed.status == "ACTIVE", "临时错误不立刻判死"
            assert refreshed.next_fire_at == moment + timedelta(hours=1)

    final = await fetch_schedule(tenant, seeded.schedule_id)
    assert final.status == "NEEDS_ATTENTION"
    assert final.attention_reason == "RESOLVE_UNAVAILABLE"
    assert final.next_fire_at is None
    rows = await fires(tenant, seeded.schedule_id)
    assert [row.status for row in rows] == ["FAILED_ADMISSION"] * TRANSIENT_FAILURES_BEFORE_ATTENTION
    assert await executions(tenant, seeded.schedule_id) == []


# --------------------------------------------------------------------- 5. Misfire / DST / 配额


async def test_misfire_writes_one_skipped_fire_aggregating_extra_misses(
    tenant: TenantContext,
) -> None:
    """错过区间只有一条 SKIPPED(MISFIRE)：`missed_count` 记额外次数，不逐条伪造 Fire。"""
    seeded = await seed(tenant, next_fire_at=SLOT, cron="0 * * * *", timezone="UTC")
    client = MaterialsClient(materials_for())
    loop = loop_for(tenant, client)
    now = SLOT + timedelta(hours=3, minutes=2)

    assert await loop.run_once(now=now) is None

    assert client.calls == [], "错过触发不得进入 Admission（不补发）"
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.skip_reason) == ("SKIPPED", "MISFIRE")
    assert fire.scheduled_at == SLOT
    # 指针 01:00 起 01:00/02:00/03:00/04:00 都过期：Fire 记 01:00 这一槽，额外错过 3 次。
    assert fire.missed_count == 3
    assert await executions(tenant, seeded.schedule_id) == []
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ACTIVE"
    assert refreshed.occurrence_count == 4
    assert refreshed.last_fire_at is None
    assert refreshed.next_fire_at == SLOT + timedelta(hours=4)
    assert await event_types(tenant, fire.id, EntityType.FIRE) == ["SKIPPED"]


async def test_missed_once_ends_with_missed_reason(tenant: TenantContext) -> None:
    seeded = await seed(
        tenant,
        kind="ONCE",
        cron=None,
        run_at=SLOT,
        next_fire_at=SLOT,
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=SLOT + timedelta(hours=2)) is None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.skip_reason, fire.missed_count) == ("SKIPPED", "MISFIRE", 0)
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ENDED"
    assert refreshed.end_reason == "MISSED", "错过不是完成"
    assert refreshed.next_fire_at is None


async def test_quota_exhaustion_writes_skipped_quota_fire(tenant: TenantContext) -> None:
    seeded = await seed(tenant, next_fire_at=SLOT, cron="0 * * * *", timezone="UTC")
    # 同一本地自然日已用掉唯一额度：计数只算真实尝试（CREATED/EXECUTION_CREATED/FAILED_ADMISSION）。
    async with tenant.session_factory() as session, session.begin():
        session.add(
            ScheduleFire(
                tenant_id=tenant.tenant_id,
                schedule_id=seeded.schedule_id,
                schedule_revision_id=seeded.revision_id,
                scheduled_at=SLOT - timedelta(hours=1),
                status="EXECUTION_CREATED",
                missed_count=0,
            )
        )
    loop = loop_for(
        tenant,
        MaterialsClient(materials_for()),
        settings_client=settings_with(max_fires_per_day_per_user=1),
    )

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    fire = (await fires(tenant, seeded.schedule_id))[-1]
    assert (fire.scheduled_at, fire.status, fire.skip_reason) == (SLOT, "SKIPPED", "QUOTA")
    assert fire.execution_id is None
    assert await executions(tenant, seeded.schedule_id) == []
    assert await event_types(tenant, fire.id, EntityType.FIRE) == ["SKIPPED"]
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "ACTIVE"
    assert refreshed.next_fire_at == SLOT + timedelta(hours=1), "超限也要推进，不热循环"


async def test_dst_gap_after_the_fire_is_evented_and_counted_in_one_transaction(
    tenant: TenantContext,
) -> None:
    """春季跳变：03-08 02:30 纽约不存在 ⇒ 只写 SCHEDULE 事件，仍按本地槽计一次。"""
    slot = datetime(2026, 3, 7, 7, 30, tzinfo=UTC)  # 03-07 02:30 EST
    seeded = await seed(
        tenant, next_fire_at=slot, cron="30 2 * * *", timezone=NEW_YORK
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=slot + timedelta(seconds=1)) is not None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "EXECUTION_CREATED" and fire.scheduled_at == slot
    schedule_events = await events(tenant, seeded.schedule_id, EntityType.SCHEDULE)
    assert [event.event_type for event in schedule_events] == ["DST_GAP_SKIPPED"]
    assert schedule_events[0].payload_json["local_date"] == "2026-03-08"
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.occurrence_count == 2, "触发槽 + gap 槽各计一次"
    assert refreshed.next_fire_at == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
    assert len(await fires(tenant, seeded.schedule_id)) == 1, "gap 不伪造 Fire"


async def test_dst_fold_fires_first_mapping_and_skips_the_second(tenant: TenantContext) -> None:
    """秋季重复的本地时刻只触发第一次；第二次只写 SCHEDULE 事件，不计槽。"""
    slot = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 11-01 01:30 EDT（第一次）
    seeded = await seed(
        tenant, next_fire_at=slot, cron="30 1 * * *", timezone=NEW_YORK
    )
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    assert await loop.run_once(now=slot + timedelta(seconds=1)) is not None

    [fire] = await fires(tenant, seeded.schedule_id)
    assert fire.status == "EXECUTION_CREATED"
    assert fire.dst_resolution == "FOLD_FIRST"
    assert fire.scheduled_at == slot
    schedule_events = await events(tenant, seeded.schedule_id, EntityType.SCHEDULE)
    assert [event.event_type for event in schedule_events] == ["DST_FOLD_SECOND_SKIPPED"]
    assert schedule_events[0].payload_json["local_time"] == "01:30:00"
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.occurrence_count == 1, "fold 的第二次是同一槽，不额外计数"
    assert refreshed.next_fire_at == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


# --------------------------------------------------------------------- 6. 竞态与原子性


class _BarrierClient(MaterialsClient):
    """两个 Scheduler 都在锁外准入处会合，制造确定的「同时写同一 occurrence」窗口。"""

    def __init__(self, parties: int) -> None:
        super().__init__(materials_for())
        self._barrier = asyncio.Barrier(parties)
        self.arrived = asyncio.Event()

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        materials = await super().resolve_schedule_materials(tenant_id, payload)
        self.arrived.set()
        await asyncio.wait_for(self._barrier.wait(), timeout=10)
        return materials


async def test_two_schedulers_create_exactly_one_execution(tenant: TenantContext) -> None:
    seeded = await seed(tenant, next_fire_at=SLOT)
    client = _BarrierClient(parties=2)
    first = loop_for(tenant, client)
    second = loop_for(tenant, client)

    pending = asyncio.create_task(first.run_once(now=SLOT + timedelta(seconds=1)))
    await asyncio.wait_for(client.arrived.wait(), timeout=10)
    outcomes = await asyncio.gather(pending, second.run_once(now=SLOT + timedelta(seconds=1)))

    assert len([item for item in outcomes if item is not None]) == 1
    assert len(await fires(tenant, seeded.schedule_id)) == 1
    assert len(await executions(tenant, seeded.schedule_id)) == 1
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.occurrence_count == 1, "进度只推进一次"
    assert refreshed.next_fire_at == SLOT + timedelta(days=1)


async def test_pause_winning_the_race_discards_the_admission(
    tenant: TenantContext,
) -> None:
    """锁内 CAS 失败（暂停赢）⇒ 本轮准入结果整份作废，不写 Fire、不推进。"""
    seeded = await seed(tenant, next_fire_at=SLOT)

    async def pause() -> None:
        from muad_agent_worker.application.schedule_lifecycle import ScheduleService

        async with tenant.session_factory() as session, session.begin():
            service = ScheduleService(session, now=SLOT + timedelta(seconds=1))
            await service.pause(
                seeded.schedule_id, tenant_id=tenant.tenant_id, actor_user_id=None
            )

    loop = loop_for(tenant, MaterialsClient(materials_for(), hook=pause))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None

    assert await fires(tenant, seeded.schedule_id) == []
    assert await executions(tenant, seeded.schedule_id) == []
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.status == "PAUSED"
    assert refreshed.next_fire_at == SLOT, "竞态失败方不得推进 Schedule"
    assert refreshed.occurrence_count == 0


async def test_revision_change_winning_the_race_discards_the_admission(
    tenant: TenantContext,
) -> None:
    seeded = await seed(tenant, next_fire_at=SLOT)

    async def bump_revision() -> None:
        async with tenant.session_factory() as session, session.begin():
            await session.execute(
                sa.update(TaskSchedule)
                .where(TaskSchedule.id == seeded.schedule_id)
                .values(config_version=2, next_fire_at=SLOT + timedelta(hours=2))
            )

    loop = loop_for(tenant, MaterialsClient(materials_for(), hook=bump_revision))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is None
    assert await fires(tenant, seeded.schedule_id) == []
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.next_fire_at == SLOT + timedelta(hours=2)


async def test_admission_runs_outside_the_schedule_lock(tenant: TenantContext) -> None:
    """准入是网络调用：调用期间 Schedule 行不得被本事务锁住（否则生命周期动作被钉住）。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    locked_during_admission: list[bool] = []

    async def probe() -> None:
        async with tenant.session_factory() as session, session.begin():
            row = await session.scalar(
                sa.text(
                    "SELECT id FROM task.task_schedule WHERE id = :id FOR NO KEY UPDATE NOWAIT"
                ).bindparams(id=seeded.schedule_id)
            )
        locked_during_admission.append(row is not None)

    loop = loop_for(tenant, MaterialsClient(materials_for(), hook=probe))

    assert await loop.run_once(now=SLOT + timedelta(seconds=1)) is not None
    assert locked_during_admission == [True], "准入期间必须能拿到行（未持锁）"


async def test_transaction_failure_leaves_no_partial_rows(
    tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fire/Execution/进度同一事务：任一步失败整体回滚，不留半个事实。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    loop = loop_for(tenant, MaterialsClient(materials_for()))

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("execution insert failed")

    monkeypatch.setattr(fire_module, "_insert_execution", boom)

    with pytest.raises(RuntimeError):
        await loop.run_once(now=SLOT + timedelta(seconds=1))

    assert await fires(tenant, seeded.schedule_id) == []
    assert await executions(tenant, seeded.schedule_id) == []
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.next_fire_at == SLOT
    assert refreshed.occurrence_count == 0
    assert refreshed.last_fire_at is None
    assert await events(tenant, seeded.schedule_id, EntityType.SCHEDULE) == []


async def test_a_failing_schedule_does_not_starve_other_tenants(tenant: TenantContext) -> None:
    """有界扫描按 `(next_fire_at, tenant_id)`：一个租户的解析故障不影响别的租户。"""
    other_tenant = f"{tenant.tenant_id}-b"
    broken = await seed(
        tenant,
        tenant_id=other_tenant,
        agent_id=uuid.UUID("5a1d0c1e-0000-4000-8000-0000000af0ff"),
        next_fire_at=SLOT,
    )
    healthy = await seed(tenant, next_fire_at=SLOT + timedelta(seconds=1))
    broken_agent = broken.agent_id

    class _Selective(MaterialsClient):
        async def resolve_schedule_materials(self, tenant_id, payload):  # type: ignore[override]
            self.calls.append((tenant_id, payload))
            if payload.agent_id == broken_agent:
                raise RuntimeError("console exploded for this tenant")
            return self.materials  # type: ignore[return-value]

    loop = loop_for(tenant, _Selective(materials_for()))

    assert await loop.run_due(now=SLOT + timedelta(seconds=2)) == 2

    assert len(await executions(tenant, healthy.schedule_id)) == 1
    assert await executions(tenant, broken.schedule_id) == []
    await _purge_tenant(tenant, other_tenant)


async def _purge_tenant(tenant: TenantContext, tenant_id: str) -> None:
    """清理测试里额外造的租户行（`tenant` fixture 只清自己的租户）。"""
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE task.task_schedule SET current_revision_id = NULL WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
        for table in (
            "task.task_event",
            "task.task_execution",
            "task.schedule_fire",
            "task.task_schedule_revision",
            "task.task_schedule",
        ):
            await session.execute(sa.text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant_id})
