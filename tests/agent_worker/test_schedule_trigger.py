"""B-115 / E-02：Schedule 触发原子化、幂等与 fail-closed（设计 §3.2.9 / §3.3.6）。

真实边界：真实 Console `resolve_schedule_materials`（HTTP over ASGI）→ control schema 的
grants/Binding/Artifact → task schema 的 Fire/Execution 落库。不 mock 物料解析、不 mock 授权
关系、不 mock 写入。

v2 口径（TASK-014）：每次触发写唯一 `schedule_fire` + 冻结的 `task_execution`；准入是**永久**
失败（授权撤销/依赖缺失）时 CRON 进入 `NEEDS_ATTENTION`，不是"留在 ACTIVE 记个原因码"。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_agent_worker.scheduler.client import ConsoleResolveClient
from muad_agent_worker.scheduler.service import SchedulerLoop
from muad_common import SharedSettings
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    AgentSkillBinding,
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
    PlatformUser,
    Skill,
    SkillArtifact,
)
from muad_console_platform.main import app as console_app
from muad_contracts import ScheduleAdmissionMaterials, ScheduleAdmissionRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_worker.test_scheduled_v2_fire import SettingsClient, payload_for
from tests.internal_service import TOKEN

CONSOLE_URL = "http://console"
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
BARRIER_TIMEOUT_SEC = 10

SKILL_FRONTMATTER = {
    "invocation_contract": {
        "version": 1,
        "invocation": "stdin",
        "schema": {
            "type": "object",
            "properties": {"report_day": {"type": "string", "format": "date"}},
            "required": ["report_day"],
            "additionalProperties": False,
        },
    }
}

CLEANUP_TASK = (
    "UPDATE task.task_schedule SET current_revision_id = NULL WHERE tenant_id = :tenant_id",
    "UPDATE task.schedule_fire SET execution_id = NULL WHERE tenant_id = :tenant_id",
    "DELETE FROM task.task_event WHERE tenant_id = :tenant_id",
    "DELETE FROM task.task_submission WHERE tenant_id = :tenant_id",
    "DELETE FROM task.task_execution WHERE tenant_id = :tenant_id",
    "DELETE FROM task.schedule_fire WHERE tenant_id = :tenant_id",
    "DELETE FROM task.task_schedule_revision WHERE tenant_id = :tenant_id",
    "DELETE FROM task.task_schedule WHERE tenant_id = :tenant_id",
    "DELETE FROM task.delivery_route WHERE tenant_id = :tenant_id",
)
CLEANUP_CONTROL = (
    "DELETE FROM control.agent_skill_binding WHERE agent_id IN "
    "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
    "DELETE FROM control.agent_access_grant WHERE agent_id IN "
    "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
    "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
    "DELETE FROM control.skill_user_grant WHERE skill_id IN "
    "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
    "DELETE FROM control.skill_artifact WHERE skill_id IN "
    "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
    "DELETE FROM control.skill WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
    "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
    "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class TriggerStack:
    tenant_id: str
    agent_id: uuid.UUID
    actor_user_id: uuid.UUID
    skill_id: uuid.UUID
    artifact_id: uuid.UUID
    session_factory: async_sessionmaker[AsyncSession]
    settings: SharedSettings
    resolver: ConsoleResolveClient


@pytest.fixture
async def stack(
    database_guard: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[TriggerStack]:
    """真实 Console 应用 + 真实 control/task schema 数据，共用一个 tenant。"""
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    tenant_id = f"test-{uuid.uuid4()}"
    control = console_session_factory()
    worker = get_session_factory()
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_app), base_url=CONSOLE_URL
    )
    async with control() as session:
        actor = PlatformUser(
            tenant_id=tenant_id,
            user_code=f"user-{uuid.uuid4()}",
            display_name="Schedule Actor",
        )
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="Schedule Model",
            model_id="gpt-4o-mini",
            enabled=True,
        )
        session.add_all([actor, model])
        await session.flush()
        credential = ModelCredential(
            tenant_id=tenant_id,
            key=f"cred-{uuid.uuid4()}",
            provider_key="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-schedule-fixture",
            api_key_digest=hashlib.sha256(b"sk-schedule-fixture").hexdigest(),
            max_inflight=8,
        )
        session.add(credential)
        await session.flush()
        session.add(
            ModelDeployment(
                tenant_id=tenant_id,
                key=f"member-{uuid.uuid4()}",
                model_id=model.id,
                credential_id=credential.id,
                weight=1,
            )
        )
        await session.flush()
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=f"agent-{uuid.uuid4()}",
            name="Schedule Agent",
            instructions="You are scheduled.",
            model_id=model.id,
            runtime_config={},
            revision=1,
            enabled=True,
        )
        skill = Skill(
            tenant_id=tenant_id,
            key=f"skill-{uuid.uuid4()}",
            name="Policy Check",
            description="scheduled policy check",
            user_scope="ALL",
            enabled=True,
        )
        session.add_all([agent, skill])
        await session.flush()
        artifact = SkillArtifact(
            skill_id=skill.id,
            version="1.0.0",
            checksum="sha256:" + "1" * 64,
            storage_key=f"skills/{skill.id}/v1/skill.zip",
            execution_mode="ASYNC",
            instructions="",
            package_size=1024,
            validation_status="READY",
            frontmatter_json=SKILL_FRONTMATTER,
            created_by=actor.id,
        )
        session.add(artifact)
        await session.flush()
        skill.current_artifact_id = artifact.id
        session.add_all(
            [
                AgentAccessGrant(user_id=actor.id, agent_id=agent.id, granted_by=actor.id),
                AgentSkillBinding(agent_id=agent.id, skill_id=skill.id),
            ]
        )
        await session.commit()
        context = TriggerStack(
            tenant_id=tenant_id,
            agent_id=agent.id,
            actor_user_id=actor.id,
            skill_id=skill.id,
            artifact_id=artifact.id,
            session_factory=worker,
            settings=SharedSettings(),
            resolver=ConsoleResolveClient(CONSOLE_URL, http_client, service_token=TOKEN),
        )
    try:
        yield context
    finally:
        await http_client.aclose()
        async with worker() as session:
            for statement in CLEANUP_TASK:
                await session.execute(sa.text(statement), {"tenant_id": tenant_id})
            await session.commit()
        async with control() as session:
            for statement in CLEANUP_CONTROL:
                await session.execute(sa.text(statement), {"tenant_id": tenant_id})
            await session.commit()


async def _seed(stack: TriggerStack, *, next_fire_at: datetime) -> uuid.UUID:
    """种 Schedule + Revision 1（Draft/确认流程由 TASK-012 的用例面覆盖）。"""
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    async with stack.session_factory() as session, session.begin():
        session.add(
            TaskSchedule(
                id=schedule_id,
                tenant_id=stack.tenant_id,
                owner_user_id=stack.actor_user_id,
                agent_id=stack.agent_id,
                name="policy check",
                status="ACTIVE",
                config_version=1,
                next_fire_at=next_fire_at,
                created_by=stack.actor_user_id,
            )
        )
        await session.flush()
        session.add(
            TaskScheduleRevision(
                id=revision_id,
                tenant_id=stack.tenant_id,
                schedule_id=schedule_id,
                revision_no=1,
                mode="SKILL",
                instruction_raw="每天检查一次",
                normalized_goal="检查一次",
                recurrence_kind="CRON",
                cron_expr="0 9 * * *",
                timezone="Asia/Shanghai",
                schedule_policy={},
                context_json={},
                payload_kind="SKILL_PLAN",
                payload_json=payload_for("SKILL", skill_id=stack.skill_id),
                delivery_intent="NONE",
                preflight_json={},
                created_by=stack.actor_user_id,
            )
        )
        await session.flush()
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(current_revision_id=revision_id)
        )
    return schedule_id


def _loop(stack: TriggerStack, client: Any) -> SchedulerLoop:
    return SchedulerLoop(
        stack.session_factory, client, stack.settings, settings_client=SettingsClient()
    )


async def _set_next(stack: TriggerStack, schedule_id: uuid.UUID, moment: datetime) -> None:
    async with stack.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(next_fire_at=moment)
        )


async def _schedule(stack: TriggerStack, schedule_id: uuid.UUID) -> TaskSchedule:
    async with stack.session_factory() as session:
        row = await session.get(TaskSchedule, schedule_id)
    assert row is not None
    return row


async def _fires(stack: TriggerStack, schedule_id: uuid.UUID) -> list[ScheduleFire]:
    async with stack.session_factory() as session:
        rows = await session.scalars(
            sa.select(ScheduleFire)
            .where(ScheduleFire.schedule_id == schedule_id)
            .order_by(ScheduleFire.scheduled_at)
        )
    return list(rows)


async def _executions(stack: TriggerStack, schedule_id: uuid.UUID) -> list[TaskExecution]:
    async with stack.session_factory() as session:
        rows = await session.scalars(
            sa.select(TaskExecution)
            .where(TaskExecution.schedule_id == schedule_id)
            .order_by(TaskExecution.create_time)
        )
    return list(rows)


async def _event_types(stack: TriggerStack, entity_id: uuid.UUID, kind: str) -> list[str]:
    async with stack.session_factory() as session:
        rows = await session.scalars(
            sa.select(TaskEvent.event_type)
            .where(TaskEvent.entity_id == entity_id, TaskEvent.entity_type == kind)
            .order_by(TaskEvent.seq)
        )
    return list(rows)


class _BarrierClient:
    """让两个 Scheduler 都在锁外准入处会合，制造确定的写库竞态窗口。"""

    def __init__(self, inner: ConsoleResolveClient, parties: int) -> None:
        self._inner = inner
        self._barrier = asyncio.Barrier(parties)
        self.arrived = asyncio.Event()

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        materials = await self._inner.resolve_schedule_materials(tenant_id, payload)
        self.arrived.set()
        try:
            await asyncio.wait_for(self._barrier.wait(), timeout=BARRIER_TIMEOUT_SEC)
        except TimeoutError as exc:
            raise AssertionError("只有一侧 Scheduler 进入准入，barrier 超时") from exc
        return materials


class _RaceClient:
    """真实解析之后、落库之前插入一个管理动作，模拟管理动作赢得竞态。"""

    def __init__(self, inner: ConsoleResolveClient, action: Any) -> None:
        self._inner = inner
        self._action = action

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        materials = await self._inner.resolve_schedule_materials(tenant_id, payload)
        await self._action()
        return materials


async def test_b115_two_schedulers_create_exactly_one_execution(stack: TriggerStack) -> None:
    """同一 fire_time 被两个 Scheduler 同时处理时只写一个 Fire、一个 Execution、推一次指针。"""
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))
    barrier = _BarrierClient(stack.resolver, 2)
    first = _loop(stack, barrier)
    second = _loop(stack, barrier)

    pending = asyncio.create_task(first.run_once(now=now))
    try:
        await asyncio.wait_for(barrier.arrived.wait(), timeout=BARRIER_TIMEOUT_SEC)
    except TimeoutError as exc:
        raise AssertionError("第一个 Scheduler 未能在 fire 窗口内进入准入") from exc
    outcomes = await asyncio.gather(pending, second.run_once(now=now))

    assert len([item for item in outcomes if item is not None]) == 1
    assert len(await _fires(stack, schedule_id)) == 1, "同一计划时刻只能有一个 Fire"
    executions = await _executions(stack, schedule_id)
    assert len(executions) == 1
    assert await _event_types(stack, executions[0].id, "EXECUTION") == ["QUEUED"]

    refreshed = await _schedule(stack, schedule_id)
    assert refreshed.last_fire_at is not None
    assert refreshed.occurrence_count == 1
    assert refreshed.next_fire_at is not None and refreshed.next_fire_at > now
    local = refreshed.next_fire_at.astimezone(LOCAL_ZONE)
    assert (local.hour, local.minute) == (9, 0), "推进必须走时区正确的下一槽"


async def test_b115_repeated_tick_for_same_fire_time_creates_nothing(stack: TriggerStack) -> None:
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))
    loop = _loop(stack, stack.resolver)

    assert await loop.run_once(now=now) is not None
    assert await loop.run_once(now=now + timedelta(seconds=1)) is None
    assert len(await _fires(stack, schedule_id)) == 1
    assert len(await _executions(stack, schedule_id)) == 1


async def test_b115_pause_winning_race_writes_no_fire(stack: TriggerStack) -> None:
    """准入后暂停赢得竞态：锁内 CAS 取不到行，不写 Fire、不建 Execution、不推进。"""
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))

    async def pause() -> None:
        from muad_agent_worker.application.schedule_lifecycle import ScheduleService

        async with stack.session_factory() as session, session.begin():
            await ScheduleService(session, now=now).pause(
                schedule_id, tenant_id=stack.tenant_id, actor_user_id=None
            )

    loop = _loop(stack, _RaceClient(stack.resolver, pause))

    assert await loop.run_once(now=now) is None
    assert await _fires(stack, schedule_id) == []
    assert await _executions(stack, schedule_id) == []
    refreshed = await _schedule(stack, schedule_id)
    assert refreshed.status == "PAUSED"
    assert refreshed.next_fire_at == now - timedelta(seconds=1), "竞态失败方不得推进"
    assert refreshed.last_fire_at is None


async def test_b115_cancel_winning_race_writes_no_fire(stack: TriggerStack) -> None:
    """取消（终态）赢得竞态：准入结果作废，不写 Fire、不把取消改回 ACTIVE。"""
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))

    async def cancel() -> None:
        from muad_agent_worker.application.schedule_lifecycle import ScheduleService

        async with stack.session_factory() as session, session.begin():
            await ScheduleService(session, now=now).cancel(
                schedule_id, tenant_id=stack.tenant_id, actor_user_id=None
            )

    loop = _loop(stack, _RaceClient(stack.resolver, cancel))

    assert await loop.run_once(now=now) is None
    assert await _fires(stack, schedule_id) == []
    refreshed = await _schedule(stack, schedule_id)
    assert refreshed.status == "CANCELLED"
    assert refreshed.end_reason == "USER_CANCELLED"
    assert refreshed.next_fire_at is None
    assert refreshed.last_fire_at is None


async def test_b115_delete_winning_race_writes_no_fire(stack: TriggerStack) -> None:
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))

    async def soft_delete() -> None:
        from muad_agent_worker.application.schedule_lifecycle import ScheduleService

        async with stack.session_factory() as session, session.begin():
            await ScheduleService(session, now=now).soft_delete(
                schedule_id, tenant_id=stack.tenant_id, actor_user_id=None
            )

    loop = _loop(stack, _RaceClient(stack.resolver, soft_delete))

    assert await loop.run_once(now=now) is None
    assert await _fires(stack, schedule_id) == []
    refreshed = await _schedule(stack, schedule_id)
    assert refreshed.is_deleted is True
    assert refreshed.next_fire_at == now - timedelta(seconds=1)


async def test_b115_update_winning_race_writes_no_fire(stack: TriggerStack) -> None:
    """更新（新 Revision 成为 current）赢得竞态：旧准入作废，用新事实重算指针。"""
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))
    new_next = now + timedelta(hours=1)

    async def reschedule() -> None:
        async with stack.session_factory() as session, session.begin():
            await session.execute(
                sa.update(TaskSchedule)
                .where(TaskSchedule.id == schedule_id)
                .values(config_version=2, next_fire_at=new_next)
            )

    loop = _loop(stack, _RaceClient(stack.resolver, reschedule))

    assert await loop.run_once(now=now) is None
    assert await _fires(stack, schedule_id) == []
    refreshed = await _schedule(stack, schedule_id)
    assert refreshed.config_version == 2
    assert refreshed.next_fire_at == new_next


async def _complete(stack: TriggerStack, execution: TaskExecution) -> None:
    """用真实终态写入收尾上一轮执行（状态 CAS + 事件），不留半条执行。"""
    from muad_agent_worker.application.task_events import TaskEventType
    from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
    from muad_contracts import TerminalStatus

    async with stack.session_factory() as session, session.begin():
        stored = await session.get(TaskExecution, execution.id)
        assert stored is not None
        written = await write_terminal(
            session,
            stored,
            TerminalChange(status=TerminalStatus.COMPLETED),
            datetime.now(UTC),
            event_type=TaskEventType.COMPLETED,
        )
        assert written


async def test_b115_new_fire_freezes_current_definition_and_keeps_old_execution(
    stack: TriggerStack,
) -> None:
    """新触发冻结触发时点的定义；已创建 Execution 的快照不被后续变更改写。"""
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))
    loop = _loop(stack, stack.resolver)

    assert await loop.run_once(now=now) is not None
    [first] = await _executions(stack, schedule_id)
    first_snapshot = first.execution_snapshot_json

    async with console_session_factory()() as session:
        await session.execute(
            sa.update(AgentDefinition)
            .where(AgentDefinition.id == stack.agent_id)
            .values(revision=9, instructions="You are scheduled v2.")
        )
        new_artifact = SkillArtifact(
            skill_id=stack.skill_id,
            version="2.0.0",
            checksum="sha256:" + "2" * 64,
            storage_key=f"skills/{stack.skill_id}/v2/skill.zip",
            execution_mode="ASYNC",
            instructions="",
            package_size=2048,
            validation_status="READY",
            frontmatter_json=SKILL_FRONTMATTER,
            created_by=stack.actor_user_id,
        )
        session.add(new_artifact)
        await session.flush()
        await session.execute(
            sa.update(Skill)
            .where(Skill.id == stack.skill_id)
            .values(current_artifact_id=new_artifact.id)
        )
        await session.commit()
        new_artifact_id = new_artifact.id

    # 上一轮执行收尾后才有下一次触发（默认 `SKIP_IF_RUNNING` 会挡住未结束的根执行）。
    await _complete(stack, first)

    second_slot = datetime(2026, 3, 9, 1, 0, tzinfo=UTC)  # 上海 09:00
    await _set_next(stack, schedule_id, second_slot)
    assert await loop.run_once(now=second_slot + timedelta(seconds=1)) is not None

    executions = await _executions(stack, schedule_id)
    assert len(executions) == 2
    second = next(item for item in executions if item.scheduled_at == second_slot)
    assert second.skill_artifact_id == new_artifact_id, "新触发用触发时点的最新 artifact"
    assert second.execution_snapshot_json["agent"]["revision"] == 9
    assert second.snapshot_hash != first.snapshot_hash

    unchanged = next(item for item in executions if item.id == first.id)
    assert unchanged.execution_snapshot_json == first_snapshot
    assert unchanged.skill_artifact_id == stack.artifact_id


@pytest.mark.parametrize("revoke", ["grant", "binding"])
async def test_e02_revoked_access_is_a_permanent_failure(
    stack: TriggerStack, revoke: str
) -> None:
    """撤权或撤 Binding：本次准入永久失败，不建 Execution，CRON 进入 NEEDS_ATTENTION。"""
    now = datetime.now(UTC)
    schedule_id = await _seed(stack, next_fire_at=now - timedelta(seconds=1))
    async with console_session_factory()() as session:
        if revoke == "grant":
            await session.execute(
                sa.update(AgentAccessGrant)
                .where(
                    AgentAccessGrant.user_id == stack.actor_user_id,
                    AgentAccessGrant.agent_id == stack.agent_id,
                )
                .values(is_deleted=True)
            )
        else:
            await session.execute(
                sa.update(AgentSkillBinding)
                .where(
                    AgentSkillBinding.agent_id == stack.agent_id,
                    AgentSkillBinding.skill_id == stack.skill_id,
                )
                .values(is_deleted=True)
            )
        await session.commit()

    loop = _loop(stack, stack.resolver)
    assert await loop.run_once(now=now) is None

    assert await _executions(stack, schedule_id) == [], "撤权后不得创建可执行 Execution"
    [fire] = await _fires(stack, schedule_id)
    assert fire.status == "FAILED_ADMISSION"
    assert fire.admission_error_code == "GRANT_REVOKED"
    assert fire.execution_id is None
    refreshed = await _schedule(stack, schedule_id)
    assert refreshed.status == "NEEDS_ATTENTION"
    assert refreshed.attention_reason == "GRANT_REVOKED"
    assert refreshed.next_fire_at is None
    assert refreshed.last_fire_at is None
