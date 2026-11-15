"""TASK-021 · Human 提问的回答、超时、取消与双侧 CAS（S-06/S-09/S-10、E-07/E-08/E-15、B-05/B-08）。

真实边界：真实 FastAPI 路由 + 真实 PostgreSQL 行锁/部分唯一索引 + 真实 claim/lease。断言只看行：

- 回答在同一事务里把 interaction `OPEN→RESOLVED` 与 execution `WAITING_HUMAN→QUEUED` 一起落；
- 终态执行永不复活（CAS 落败即拒绝），超时是 `TIMED_OUT` 的唯一写方；
- `FUTURE_ONLY` 保留 OPEN 问题与等待，`ALL_ACTIVE` 才原子关闭；
- Human 续接只计 `resume_count`，不消耗业务 `attempt`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.model import ModelMessage, ModelRole
from muad_agent_worker.application.agentic.run import ResumeState
from muad_agent_worker.application.human_interactions import (
    HUMAN_RESUME_KEY,
    human_answer_content,
    load_human_answer,
    response_hash,
)
from muad_agent_worker.application.schedule_lifecycle import ExecutionService, ScheduleService
from muad_agent_worker.application.task_events import EntityType
from muad_agent_worker.infrastructure.models.task import (
    TaskExecution,
    TaskExecutionCheckpoint,
    TaskHumanInteraction,
)
from muad_agent_worker.worker.agentic_executor import _with_human_answer
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.human_timeouts import HUMAN_TIMEOUT, HumanTimeoutSweeper
from muad_contracts import CancelScope

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_events, fetch_task, internal_service_headers, persist_task
from agent_worker.test_scheduled_v2_lifecycle import (
    AGENT,
    OWNER,
    event_types,
    read_execution,
    read_interaction,
    seed_fire,
    seed_schedule,
)

pytestmark = pytest.mark.integration

OPEN = "OPEN"
RESOLVED = "RESOLVED"
TIMED_OUT = "TIMED_OUT"
CANCELLED = "CANCELLED"
WAITING_HUMAN = "WAITING_HUMAN"
QUEUED = "QUEUED"

ANSWER = {"customer": "A"}


# --------------------------------------------------------------------- seeds


@dataclass(frozen=True)
class HumanSeed:
    execution_id: uuid.UUID
    interaction_id: uuid.UUID


async def _seed_interaction(
    tenant: TenantContext,
    execution_id: uuid.UUID,
    *,
    owner: uuid.UUID,
    agent: uuid.UUID,
    deadline_at: datetime,
    input_schema: dict[str, Any] | None = None,
    status: str = OPEN,
    response_json: dict[str, Any] | None = None,
) -> uuid.UUID:
    interaction_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskHumanInteraction(
                id=interaction_id,
                tenant_id=tenant.tenant_id,
                execution_id=execution_id,
                owner_user_id=owner,
                agent_id=agent,
                interaction_type="INPUT",
                status=status,
                question="请确认客户名单",
                input_schema=input_schema,
                response_json=response_json,
                deadline_at=deadline_at,
            )
        )
    return interaction_id


async def seed_human(
    tenant: TenantContext,
    *,
    execution_status: str = WAITING_HUMAN,
    attempt: int = 1,
    resume_count: int = 0,
    execution_deadline_at: datetime | None = None,
    interaction_deadline_at: datetime | None = None,
    input_schema: dict[str, Any] | None = None,
    interaction_status: str = OPEN,
    response_json: dict[str, Any] | None = None,
    owner: uuid.UUID = OWNER,
    agent: uuid.UUID = AGENT,
) -> HumanSeed:
    now = datetime.now(UTC)
    execution = await persist_task(
        tenant,
        agent_id=agent,
        actor_user_id=owner,
        status=execution_status,
        started_at=now - timedelta(minutes=30),
        attempt=attempt,
        resume_count=resume_count,
        not_before=now,
        deadline_at=execution_deadline_at or now + timedelta(hours=1),
        lease_owner=None,
        lease_until=None,
    )
    interaction_id = await _seed_interaction(
        tenant,
        execution.id,
        owner=owner,
        agent=agent,
        deadline_at=interaction_deadline_at or now + timedelta(minutes=30),
        input_schema=input_schema,
        status=interaction_status,
        response_json=response_json,
    )
    return HumanSeed(execution.id, interaction_id)


def respond_headers(
    tenant: TenantContext,
    *,
    actor: uuid.UUID = OWNER,
    agent: uuid.UUID | None = None,
    key: str | None = None,
) -> dict[str, str]:
    extra = {"X-Actor-User-Id": str(actor), "Idempotency-Key": key or f"answer-{uuid.uuid4()}"}
    if agent is not None:
        extra["X-Agent-Id"] = str(agent)
    return internal_service_headers(tenant.tenant_id, **extra)


async def answer(
    client: AsyncClient,
    tenant: TenantContext,
    seed: HumanSeed,
    response: dict[str, Any] | None = None,
    **header_overrides: Any,
):
    return await client.post(
        f"/internal/human-interactions/{seed.interaction_id}/respond",
        json={"response": ANSWER if response is None else response},
        headers=respond_headers(tenant, **header_overrides),
    )


async def human_events(tenant: TenantContext, interaction_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        event.payload_json
        for event in await fetch_events(tenant, interaction_id, entity_type=EntityType.HUMAN)
    ]


# ------------------------------------------------------------------ answering


async def test_answer_closes_both_sides_in_one_transaction(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await seed_human(tenant)

    response = await answer(client, tenant, seed)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == {
        "interaction_id": str(seed.interaction_id),
        "status": RESOLVED,
        "execution_id": str(seed.execution_id),
        "execution_status": QUEUED,
    }
    interaction = await read_interaction(tenant, seed.interaction_id)
    assert interaction.status == RESOLVED
    assert interaction.response_json == ANSWER
    assert interaction.resolved_at is not None
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == QUEUED
    assert execution.resume_count == 1, "Human 回答计一次 resume"
    assert execution.attempt == 1, "回答不是业务重试"
    assert execution.last_resume_reason == "HUMAN"
    assert execution.lease_epoch >= 1
    assert execution.lease_owner is None and execution.lease_until is None
    assert execution.cancel_requested is False
    assert execution.wait_payload_json == {HUMAN_RESUME_KEY: str(seed.interaction_id)}, (
        "留一次性续接标记给 claim 消费"
    )
    assert await event_types(tenant, seed.execution_id, EntityType.EXECUTION) == ["RESUMED"]
    payloads = await human_events(tenant, seed.interaction_id)
    assert len(payloads) == 1 and payloads[0]["response_hash"] == response_hash(ANSWER), (
        "事件只记回答指纹，不落回答正文"
    )
    assert "response" not in payloads[0] and ANSWER["customer"] not in str(payloads[0])


async def test_same_answer_replays_first_result_and_changed_answer_conflicts(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await seed_human(tenant)
    first = await answer(client, tenant, seed)
    assert first.status_code == 200, first.text
    epoch = (await read_execution(tenant, seed.execution_id)).lease_epoch

    replay = await answer(client, tenant, seed)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"], "同 interaction 同回答重放首次结果"

    conflict = await answer(client, tenant, seed, response={"customer": "B"})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_MISMATCH"
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.lease_epoch == epoch, "重放/冲突都不推进 epoch"
    assert (await read_interaction(tenant, seed.interaction_id)).response_json == ANSWER


async def test_answer_requires_owner_and_agent(client: AsyncClient, tenant: TenantContext) -> None:
    seed = await seed_human(tenant)
    stranger = await answer(client, tenant, seed, actor=uuid.uuid4())
    assert stranger.status_code == 403 and stranger.json()["code"] == "TASK_ACCESS_DENIED"
    other_agent = await answer(client, tenant, seed, agent=uuid.uuid4())
    assert other_agent.status_code == 403 and other_agent.json()["code"] == "TASK_ACCESS_DENIED"
    other_tenant = await client.post(
        f"/internal/human-interactions/{seed.interaction_id}/respond",
        json={"response": ANSWER},
        headers=internal_service_headers(
            f"{tenant.tenant_id}-other",
            **{"X-Actor-User-Id": str(OWNER), "Idempotency-Key": f"answer-{uuid.uuid4()}"},
        ),
    )
    assert other_tenant.status_code == 404, "跨租户与不存在同形"
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == WAITING_HUMAN
    assert (await read_interaction(tenant, seed.interaction_id)).status == OPEN


@pytest.mark.parametrize("terminal", ["CANCELLED", "FAILED", "COMPLETED"])
async def test_terminal_execution_is_never_revived(
    client: AsyncClient, tenant: TenantContext, terminal: str
) -> None:
    """历史/异常数据里可能留下 OPEN 问题挂在终态执行上：回答不得把它改回 QUEUED。"""
    seed = await seed_human(tenant, execution_status=terminal)

    response = await answer(client, tenant, seed)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EXECUTION_NOT_RESUMABLE"
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == terminal
    interaction = await read_interaction(tenant, seed.interaction_id)
    assert interaction.status == OPEN and interaction.response_json is None, "整体回滚"


async def test_answer_rolls_back_when_execution_cancel_is_requested(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await seed_human(tenant)
    async with tenant.session_factory() as session, session.begin():
        execution = await session.get(TaskExecution, seed.execution_id)
        assert execution is not None
        execution.cancel_requested = True

    response = await answer(client, tenant, seed)

    assert response.status_code == 409, response.text
    assert (await read_interaction(tenant, seed.interaction_id)).status == OPEN


async def test_answer_after_question_deadline_is_rejected_without_writes(
    client: AsyncClient, tenant: TenantContext
) -> None:
    now = datetime.now(UTC)
    seed = await seed_human(
        tenant,
        interaction_deadline_at=now - timedelta(seconds=5),
        execution_deadline_at=now + timedelta(hours=1),
    )

    response = await answer(client, tenant, seed)

    assert response.status_code == 410, response.text
    assert response.json()["code"] == "HUMAN_INTERACTION_EXPIRED"
    assert (await read_interaction(tenant, seed.interaction_id)).status == OPEN, (
        "TIMED_OUT 的唯一写方是超时 sweep"
    )
    assert (await read_execution(tenant, seed.execution_id)).status == WAITING_HUMAN


async def test_invalid_response_schema_rolls_back(client: AsyncClient, tenant: TenantContext) -> None:
    seed = await seed_human(
        tenant,
        input_schema={
            "type": "object",
            "properties": {"customer": {"enum": ["A", "B"]}},
            "required": ["customer"],
            "additionalProperties": False,
        },
    )

    invalid = await answer(client, tenant, seed, response={"customer": "C"})
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["data"]["reason"] == "HUMAN_RESPONSE_INVALID"
    assert (await read_interaction(tenant, seed.interaction_id)).status == OPEN
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == WAITING_HUMAN and execution.resume_count == 0
    assert await event_types(tenant, seed.execution_id, EntityType.EXECUTION) == []

    accepted = await answer(client, tenant, seed, response={"customer": "B"})
    assert accepted.status_code == 200, accepted.text


# -------------------------------------------------------------------- timeout


async def test_timeout_fails_execution_and_closes_interaction(
    tenant: TenantContext,
) -> None:
    now = datetime.now(UTC)
    seed = await seed_human(
        tenant,
        interaction_deadline_at=now - timedelta(minutes=1),
        execution_deadline_at=now + timedelta(hours=1),
    )
    sweeper = HumanTimeoutSweeper(tenant.session_factory)

    closed = await sweeper.sweep()

    assert closed == 1
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == "FAILED"
    assert execution.error_code == HUMAN_TIMEOUT
    assert execution.failure_class == HUMAN_TIMEOUT
    assert execution.finished_at is not None
    assert execution.lease_owner is None and execution.lease_until is None
    interaction = await read_interaction(tenant, seed.interaction_id)
    assert interaction.status == TIMED_OUT and interaction.resolved_at is not None
    assert await event_types(tenant, seed.execution_id, EntityType.EXECUTION) == ["FAILED"]
    assert await event_types(tenant, seed.interaction_id, EntityType.HUMAN) == ["TIMED_OUT"]
    assert await sweeper.sweep() == 0, "已收尾的不再重复写"


async def test_timeout_only_closes_interaction_when_deadline_sweep_already_failed_execution(
    tenant: TenantContext,
) -> None:
    """执行已终态（deadline sweep 赢）：问题仍要关闭，但不得改写执行或再写执行事件。"""
    now = datetime.now(UTC)
    seed = await seed_human(
        tenant,
        execution_status="FAILED",
        interaction_deadline_at=now - timedelta(minutes=1),
    )
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "UPDATE task.task_execution SET error_code = 'TASK_DEADLINE_EXCEEDED' WHERE id = :id"
            ),
            {"id": seed.execution_id},
        )

    assert await HumanTimeoutSweeper(tenant.session_factory).sweep() == 1

    interaction = await read_interaction(tenant, seed.interaction_id)
    assert interaction.status == TIMED_OUT
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.error_code == "TASK_DEADLINE_EXCEEDED", "终态不被超时改名"
    assert await event_types(tenant, seed.execution_id, EntityType.EXECUTION) == [], (
        "非等待执行不因问题超时补写执行事件"
    )
    assert await event_types(tenant, seed.interaction_id, EntityType.HUMAN) == ["TIMED_OUT"]


async def test_timeout_sweep_is_bounded(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    seeds = [
        await seed_human(tenant, interaction_deadline_at=now - timedelta(minutes=index + 1))
        for index in range(3)
    ]

    sweeper = HumanTimeoutSweeper(tenant.session_factory, batch=2)

    assert await sweeper.sweep() == 2, "单轮最多收尾 batch 条"
    assert await sweeper.sweep() == 1
    for seed in seeds:
        assert (await read_interaction(tenant, seed.interaction_id)).status == TIMED_OUT


async def test_answer_loses_against_timeout_without_revival(
    client: AsyncClient, tenant: TenantContext
) -> None:
    now = datetime.now(UTC)
    seed = await seed_human(
        tenant,
        interaction_deadline_at=now - timedelta(minutes=1),
        execution_deadline_at=now + timedelta(hours=1),
    )
    assert await HumanTimeoutSweeper(tenant.session_factory).sweep() == 1

    response = await answer(client, tenant, seed)

    assert response.status_code == 410, response.text
    assert (await read_interaction(tenant, seed.interaction_id)).status == TIMED_OUT
    assert (await read_execution(tenant, seed.execution_id)).status == "FAILED"


# --------------------------------------------------------------------- cancel


async def test_future_only_keeps_open_question_and_all_active_closes_it(
    tenant: TenantContext,
) -> None:
    seeded = await seed_schedule(tenant)
    fire_id = await seed_fire(tenant, seeded)
    execution = await persist_task(
        tenant,
        agent_id=seeded.agent_id,
        actor_user_id=seeded.owner_user_id,
        trigger_type="SCHEDULED",
        schedule_id=seeded.schedule_id,
        schedule_revision_id=seeded.revision_id,
        schedule_fire_id=fire_id,
        status=WAITING_HUMAN,
        started_at=datetime.now(UTC) - timedelta(minutes=10),
        not_before=datetime.now(UTC),
    )
    interaction_id = await _seed_interaction(
        tenant,
        execution.id,
        owner=seeded.owner_user_id,
        agent=seeded.agent_id,
        deadline_at=datetime.now(UTC) + timedelta(minutes=30),
    )

    async with tenant.session_factory() as session, session.begin():
        await ScheduleService(session, now=datetime.now(UTC)).cancel(
            seeded.schedule_id,
            tenant_id=tenant.tenant_id,
            actor_user_id=OWNER,
            scope=CancelScope.FUTURE_ONLY,
        )
    assert (await read_execution(tenant, execution.id)).status == WAITING_HUMAN
    assert (await read_interaction(tenant, interaction_id)).status == OPEN, (
        "FUTURE_ONLY 关掉问题会让等待的执行永远等不到回答"
    )

    async with tenant.session_factory() as session, session.begin():
        await ExecutionService(session, now=datetime.now(UTC)).cancel(
            execution.id, tenant_id=tenant.tenant_id, actor_user_id=OWNER
        )
    assert (await read_execution(tenant, execution.id)).status == CANCELLED
    assert (await read_interaction(tenant, interaction_id)).status == CANCELLED


# --------------------------------------------------------------------- resume


async def test_human_resume_counts_resume_not_attempt(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await seed_human(tenant, attempt=2)
    assert (await answer(client, tenant, seed)).status_code == 200
    answered = await read_execution(tenant, seed.execution_id)

    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    claimed = await claimer.claim_one("worker-human")

    assert claimed is not None and claimed.id == seed.execution_id
    resumed = await read_execution(tenant, seed.execution_id)
    assert resumed.status == "RUNNING"
    assert resumed.attempt == 2, "Human 续接不消耗业务 attempt"
    assert resumed.resume_count == 1, "回答事务已计一次，claim 不重复计数"
    assert resumed.last_resume_reason == "HUMAN"
    assert resumed.wait_payload_json is None, "一次性续接标记必须被 claim 消费"
    assert resumed.lease_epoch == answered.lease_epoch + 1, "每次领取都推进 epoch"


async def test_waiting_human_is_not_claimable_by_the_timer(tenant: TenantContext) -> None:
    """等待人工没有定时唤醒源：只有回答把行改回 QUEUED 才续接（否则到期即凭空起跑）。"""
    now = datetime.now(UTC)
    seed = await seed_human(tenant, interaction_deadline_at=now + timedelta(minutes=30))

    claimer = TaskClaimer(tenant.session_factory, tenant.settings)

    assert await claimer.claim_one("worker-a") is None
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == WAITING_HUMAN and execution.resume_count == 0


async def test_human_answer_is_injected_into_resume_messages(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await seed_human(tenant)
    # 生产顺序：挂起时写 checkpoint → 用户回答 → claim 恢复。
    checkpoint_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskExecutionCheckpoint(
                id=checkpoint_id,
                tenant_id=tenant.tenant_id,
                execution_id=seed.execution_id,
                checkpoint_no=1,
                state_json={"schema_version": 1},
            )
        )
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE task.task_execution SET last_checkpoint_id = :cid WHERE id = :id"),
            {"cid": checkpoint_id, "id": seed.execution_id},
        )
    assert (await answer(client, tenant, seed)).status_code == 200
    task = await fetch_task(tenant, seed.execution_id)

    answer_state = await load_human_answer(tenant.session_factory, task)
    assert answer_state is not None and answer_state.response == ANSWER
    assert human_answer_content(answer_state) == '{"customer": "A"}'
    resume = ResumeState(
        checkpoint=RunnerCheckpoint(),
        messages=(ModelMessage(role=ModelRole.USER, content="原始目标"),),
    )
    injected = await _with_human_answer(tenant.session_factory, task, resume)
    assert [message.content for message in injected.messages] == [
        "原始目标",
        '{"customer": "A"}',
    ]
    assert injected.messages[-1].role is ModelRole.USER

    # 续接跑出新 checkpoint 后，同一个回答不再重复注入。
    newer = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskExecutionCheckpoint(
                id=newer,
                tenant_id=tenant.tenant_id,
                execution_id=seed.execution_id,
                checkpoint_no=2,
                state_json={"schema_version": 1},
            )
        )
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE task.task_execution SET last_checkpoint_id = :cid WHERE id = :id"),
            {"cid": newer, "id": seed.execution_id},
        )
    task = await fetch_task(tenant, seed.execution_id)
    assert await load_human_answer(tenant.session_factory, task) is None
    replayed = await _with_human_answer(tenant.session_factory, task, resume)
    assert [message.content for message in replayed.messages] == ["原始目标"]


async def test_two_executions_keep_independent_questions(
    client: AsyncClient, tenant: TenantContext
) -> None:
    first = await seed_human(tenant)
    second = await seed_human(tenant)

    assert (await answer(client, tenant, first, response={"customer": "A"})).status_code == 200
    untouched = await read_execution(tenant, second.execution_id)
    assert untouched.status == WAITING_HUMAN
    assert (await read_interaction(tenant, second.interaction_id)).status == OPEN

    assert (await answer(client, tenant, second, response={"customer": "B"})).status_code == 200
    assert (await read_interaction(tenant, first.interaction_id)).response_json == {"customer": "A"}
    assert (await read_interaction(tenant, second.interaction_id)).response_json == {"customer": "B"}
