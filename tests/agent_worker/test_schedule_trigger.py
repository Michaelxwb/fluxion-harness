"""B-115 / E-02：Schedule 触发原子化、幂等与 fail-closed（设计 §3.2.3）。

真实边界：真实 Console resolve（HTTP over ASGI）→ control schema 的 grants/Binding/Artifact
→ task schema 的 Task 落库。不 mock resolve、不 mock 授权关系、不 mock Task 写入。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from conftest import TenantContext
from helpers import create_schedule_payload
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_agent_worker.scheduler.client import ConsoleResolveClient
from muad_agent_worker.scheduler.service import SchedulerLoop, ScheduleService
from muad_common import SharedSettings
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    AgentSkillBinding,
    ModelDefinition,
    PlatformUser,
    Skill,
    SkillArtifact,
)
from muad_console_platform.main import app as console_app
from muad_contracts import ResolveDefinitionResponse, ScheduleSpec, UpdateScheduleRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

CONSOLE_URL = "http://console"
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
BARRIER_TIMEOUT_SEC = 10

CLEANUP_TASK = (
    "DELETE FROM task.task_event WHERE tenant_id = :tenant_id",
    "DELETE FROM task.task_submission WHERE tenant_id = :tenant_id",
    "DELETE FROM task.task_execution WHERE tenant_id = :tenant_id",
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
async def stack(database_guard: None) -> AsyncIterator[TriggerStack]:
    """真实 Console 应用 + 真实 control/task schema 数据，共用一个 tenant。"""
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
            base_url="https://api.example.com/v1",
            enabled=True,
        )
        session.add_all([actor, model])
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
            resolver=ConsoleResolveClient(CONSOLE_URL, http_client),
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


async def _create_schedule(stack: TriggerStack) -> TaskSchedule:
    payload = create_schedule_payload(
        cast(TenantContext, stack),
        skill_id=stack.skill_id,
        agent_id=stack.agent_id,
        actor_user_id=stack.actor_user_id,
    )
    async with stack.session_factory() as session:
        created = await ScheduleService(session, stack.settings).create_schedule(
            stack.tenant_id, payload
        )
        await session.commit()
    return created


async def _set_next_fire_at(
    stack: TriggerStack, schedule_id: uuid.UUID, next_fire_at: datetime
) -> None:
    async with stack.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(next_fire_at=next_fire_at)
        )


async def _schedule_row(stack: TriggerStack, schedule_id: uuid.UUID) -> TaskSchedule:
    async with stack.session_factory() as session:
        return (
            await session.execute(sa.select(TaskSchedule).where(TaskSchedule.id == schedule_id))
        ).scalar_one()


async def _tasks(stack: TriggerStack, schedule_id: uuid.UUID) -> list[TaskExecution]:
    async with stack.session_factory() as session:
        return list(
            (
                await session.execute(
                    sa.select(TaskExecution)
                    .where(TaskExecution.schedule_id == schedule_id)
                    .order_by(TaskExecution.create_time)
                )
            )
            .scalars()
            .all()
        )


async def _event_types(stack: TriggerStack, task_id: uuid.UUID) -> list[str]:
    async with stack.session_factory() as session:
        rows = (
            await session.execute(
                sa.text("SELECT event_type FROM task.task_event WHERE task_id = :id ORDER BY seq"),
                {"id": task_id},
            )
        ).all()
    return [row[0] for row in rows]


class _BarrierResolver:
    """让两个 Scheduler 都在真正落库前完成 claim，制造确定的 claim/fire 竞态窗口。

    第一个 Scheduler 进入 resolve 时置位 `arrived`，测试据此再启动第二个：
    两个 claim 事务不再重叠，避免 `SKIP LOCKED` 让其中一侧空手而归、另一侧死等。
    """

    def __init__(self, inner: ConsoleResolveClient, parties: int) -> None:
        self._inner = inner
        self._barrier = asyncio.Barrier(parties)
        self.arrived = asyncio.Event()

    async def resolve(
        self, agent_id: uuid.UUID, actor_user_id: uuid.UUID, tenant_id: str
    ) -> ResolveDefinitionResponse:
        response = await self._inner.resolve(agent_id, actor_user_id, tenant_id)
        self.arrived.set()
        try:
            await asyncio.wait_for(self._barrier.wait(), timeout=BARRIER_TIMEOUT_SEC)
        except TimeoutError as exc:
            raise AssertionError("只有一侧 Scheduler 进入 resolve，barrier 超时") from exc
        return response


class _RaceActionResolver:
    """真实 resolve 之后、落库之前插入一个管理动作，模拟管理动作赢得竞态。"""

    def __init__(self, inner: ConsoleResolveClient, action: Any) -> None:
        self._inner = inner
        self._action = action

    async def resolve(
        self, agent_id: uuid.UUID, actor_user_id: uuid.UUID, tenant_id: str
    ) -> ResolveDefinitionResponse:
        response = await self._inner.resolve(agent_id, actor_user_id, tenant_id)
        await self._action()
        return response


async def test_b115_two_schedulers_create_exactly_one_task(stack: TriggerStack) -> None:
    """同一 fire_time 被两个 Scheduler 同时 claim 时只创建一个 Task、只推进一次。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, fire_at)

    barrier = _BarrierResolver(stack.resolver, 2)
    first = SchedulerLoop(stack.session_factory, barrier, stack.settings)
    second = SchedulerLoop(stack.session_factory, barrier, stack.settings)

    first_run = asyncio.create_task(first.run_once(now=now))
    try:
        await asyncio.wait_for(barrier.arrived.wait(), timeout=BARRIER_TIMEOUT_SEC)
    except TimeoutError as exc:
        raise AssertionError("第一个 Scheduler 未能在 fire 窗口内 claim") from exc
    created = await asyncio.gather(first_run, second.run_once(now=now))

    assert len([task_id for task_id in created if task_id is not None]) == 1
    tasks = await _tasks(stack, schedule.id)
    assert len(tasks) == 1, "同一 fire_time 只能创建一个 Task"
    assert await _event_types(stack, tasks[0].id) == ["CREATED"]

    refreshed = await _schedule_row(stack, schedule.id)
    assert refreshed.last_fire_at == fire_at
    assert refreshed.next_fire_at is not None
    assert refreshed.next_fire_at > now
    local = refreshed.next_fire_at.astimezone(LOCAL_ZONE)
    assert (local.hour, local.minute) == (9, 0), "推进必须走时区正确的下一次触发计算"


async def test_b115_repeated_run_for_same_fire_time_creates_nothing(stack: TriggerStack) -> None:
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    fire_at = now - timedelta(seconds=1)
    loop = SchedulerLoop(stack.session_factory, stack.resolver, stack.settings)

    await _set_next_fire_at(stack, schedule.id, fire_at)
    first_id = await loop.run_once(now=now)
    assert first_id is not None

    await _set_next_fire_at(stack, schedule.id, fire_at)
    assert await loop.run_once(now=now) is None
    assert len(await _tasks(stack, schedule.id)) == 1


async def test_b115_pause_winning_race_creates_no_task(stack: TriggerStack) -> None:
    """claim 后暂停赢得竞态：不复核就会照样建 Task（本用例即该缺陷的守卫）。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, fire_at)

    async def pause() -> None:
        async with stack.session_factory() as session:
            await ScheduleService(session, stack.settings).pause_schedule(
                stack.tenant_id, schedule.id
            )
            await session.commit()

    loop = SchedulerLoop(
        stack.session_factory, _RaceActionResolver(stack.resolver, pause), stack.settings
    )
    assert await loop.run_once(now=now) is None
    assert await _tasks(stack, schedule.id) == []
    refreshed = await _schedule_row(stack, schedule.id)
    assert refreshed.status == "PAUSED"
    assert refreshed.next_fire_at == fire_at, "竞态失败方不得推进 Schedule"
    assert refreshed.last_fire_at is None


async def test_b115_delete_winning_race_creates_no_task(stack: TriggerStack) -> None:
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, fire_at)

    async def soft_delete() -> None:
        async with stack.session_factory() as session:
            await ScheduleService(session, stack.settings).delete_schedule(
                stack.tenant_id, schedule.id
            )
            await session.commit()

    loop = SchedulerLoop(
        stack.session_factory, _RaceActionResolver(stack.resolver, soft_delete), stack.settings
    )
    assert await loop.run_once(now=now) is None
    assert await _tasks(stack, schedule.id) == []
    refreshed = await _schedule_row(stack, schedule.id)
    assert refreshed.is_deleted is True
    assert refreshed.next_fire_at == fire_at


async def test_b115_update_winning_race_creates_no_task(stack: TriggerStack) -> None:
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, fire_at)

    async def reschedule() -> None:
        async with stack.session_factory() as session:
            await ScheduleService(session, stack.settings).update_schedule(
                stack.tenant_id,
                schedule.id,
                UpdateScheduleRequest.model_validate(
                    {"schedule": ScheduleSpec(type="CRON", cron="0 10 * * *", timezone="UTC")}
                ),
            )
            await session.commit()

    loop = SchedulerLoop(
        stack.session_factory, _RaceActionResolver(stack.resolver, reschedule), stack.settings
    )
    assert await loop.run_once(now=now) is None
    assert await _tasks(stack, schedule.id) == []
    refreshed = await _schedule_row(stack, schedule.id)
    assert refreshed.revision == 2
    assert refreshed.next_fire_at != fire_at


async def test_b115_new_fire_freezes_current_definition_and_keeps_old_task(
    stack: TriggerStack,
) -> None:
    """新触发冻结当前定义；已创建 Task 的 Snapshot 不被后续变更改写。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    loop = SchedulerLoop(stack.session_factory, stack.resolver, stack.settings)

    first_fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, first_fire_at)
    assert await loop.run_once(now=now) is not None
    first_task = (await _tasks(stack, schedule.id))[0]
    first_snapshot = first_task.execution_snapshot_json

    # 变更 current 定义：Agent revision 提升 + 新 Artifact 成为 current
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

    second_fire_at = now + timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, second_fire_at)
    assert await loop.run_once(now=second_fire_at) is not None

    tasks = await _tasks(stack, schedule.id)
    assert len(tasks) == 2
    second_task = tasks[1]
    assert second_task.skill_artifact_id == new_artifact_id
    assert second_task.execution_snapshot_json["agent"]["revision"] == 9
    assert second_task.snapshot_hash != first_task.snapshot_hash

    unchanged = await _tasks(stack, schedule.id)
    assert unchanged[0].execution_snapshot_json == first_snapshot
    assert unchanged[0].skill_artifact_id == stack.artifact_id
    assert unchanged[0].snapshot_hash == first_task.snapshot_hash


async def test_e02_revoked_grant_fails_closed_with_reason(stack: TriggerStack) -> None:
    """撤权后触发：不创建可执行 Snapshot，在 Schedule 上留下失败原因并推进。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, fire_at)

    async with console_session_factory()() as session:
        await session.execute(
            sa.update(AgentAccessGrant)
            .where(
                AgentAccessGrant.user_id == stack.actor_user_id,
                AgentAccessGrant.agent_id == stack.agent_id,
            )
            .values(is_deleted=True)
        )
        await session.commit()

    loop = SchedulerLoop(stack.session_factory, stack.resolver, stack.settings)
    assert await loop.run_once(now=now) is None

    assert await _tasks(stack, schedule.id) == [], "撤权后不得创建可执行 Task"
    refreshed = await _schedule_row(stack, schedule.id)
    assert refreshed.last_error_code == "AGENT_ACCESS_DENIED"
    assert refreshed.last_error_message
    assert refreshed.last_skipped_at is not None
    assert refreshed.next_fire_at is not None
    assert refreshed.next_fire_at > now, "失败后必须推进，否则每轮重复 claim 形成热循环"
    assert refreshed.last_fire_at is None


async def test_e02_revoked_binding_fails_closed_with_reason(stack: TriggerStack) -> None:
    """Binding 软删除后触发：当前有效 Skill 集合不再包含它，fail closed 并留原因。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(stack)
    fire_at = now - timedelta(seconds=1)
    await _set_next_fire_at(stack, schedule.id, fire_at)

    async with console_session_factory()() as session:
        await session.execute(
            sa.update(AgentSkillBinding)
            .where(
                AgentSkillBinding.agent_id == stack.agent_id,
                AgentSkillBinding.skill_id == stack.skill_id,
            )
            .values(is_deleted=True)
        )
        await session.commit()

    loop = SchedulerLoop(stack.session_factory, stack.resolver, stack.settings)
    assert await loop.run_once(now=now) is None

    assert await _tasks(stack, schedule.id) == [], "Binding 撤销后不得创建可执行 Task"
    refreshed = await _schedule_row(stack, schedule.id)
    assert refreshed.last_error_code == "SKILL_NOT_EFFECTIVE"
    assert refreshed.last_error_message
    assert refreshed.last_skipped_at is not None
    assert refreshed.next_fire_at is not None
    assert refreshed.next_fire_at > now
