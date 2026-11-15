"""TASK-021 · Human 回答/取消/超时的真实并发（S-09/S-10、E-07/E-15、B-05/B-08）。

每个并发方都用自己的 `AsyncSession` 与自己的事务打到真实 PostgreSQL：串行化只来自行锁与
CAS，不靠测试里人为排序。断言的是**排他结果**——谁先拿到行锁谁生效，落败方整体回滚：

- 同一回答并发 → 一次生效，另一次按幂等重放；
- 不同回答并发 → 一个成功、一个 `IDEMPOTENCY_MISMATCH`；
- 回答 vs `ALL_ACTIVE` 取消 → 要么"已回答再被取消"，要么"取消先赢、回答被拒"，绝不复活；
- 回答 vs 超时 → 回答必然被拒，执行按设计落 `CANCELLED(HUMAN_INTERACTION_EXPIRED)` + `HUMAN_TIMEOUT`；
- 两个超时 sweep 并发 → 只有一份终态与一条事件。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_worker.application.human_interactions import HumanInteractionService
from muad_agent_worker.application.schedule_lifecycle import ExecutionService
from muad_agent_worker.application.task_events import EntityType
from muad_agent_worker.infrastructure.db_clock import database_now
from muad_agent_worker.worker.human_timeouts import (
    HUMAN_TIMEOUT,
    HUMAN_TIMEOUT_CODE,
    HumanTimeoutSweeper,
)
from muad_api import AppError

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_human import (
    AGENT,
    ANSWER,
    OWNER,
    event_types,
    human_events,
    read_execution,
    read_interaction,
    seed_human,
)

pytestmark = pytest.mark.integration


async def _respond(
    tenant: TenantContext, interaction_id: uuid.UUID, response: dict[str, object]
) -> tuple[uuid.UUID, uuid.UUID, str]:
    async with tenant.session_factory() as session, session.begin():
        return await HumanInteractionService(session, now=await database_now(session)).respond(
            interaction_id,
            tenant_id=tenant.tenant_id,
            actor_user_id=OWNER,
            agent_id=AGENT,
            response=response,
        )


async def _cancel(tenant: TenantContext, execution_id: uuid.UUID) -> None:
    async with tenant.session_factory() as session, session.begin():
        await ExecutionService(session, now=await database_now(session)).cancel(
            execution_id, tenant_id=tenant.tenant_id, actor_user_id=OWNER
        )


async def _sweep(tenant: TenantContext, **kwargs: int) -> int:
    return await HumanTimeoutSweeper(tenant.session_factory, **kwargs).sweep()


async def test_concurrent_same_answer_takes_effect_once(tenant: TenantContext) -> None:
    seed = await seed_human(tenant)

    results = await asyncio.gather(
        _respond(tenant, seed.interaction_id, ANSWER),
        _respond(tenant, seed.interaction_id, dict(ANSWER)),
    )

    assert {result[2] for result in results} == {"QUEUED"}
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == "QUEUED"
    assert execution.resume_count == 1, "只有一次生效"
    assert execution.attempt == 1
    assert (
        await event_types(tenant, seed.execution_id, EntityType.EXECUTION)
    ) == ["RESUMED"], "重放不写第二条事件"
    assert len(await human_events(tenant, seed.interaction_id)) == 1


async def test_concurrent_conflicting_answers_one_wins(tenant: TenantContext) -> None:
    seed = await seed_human(tenant)

    results = await asyncio.gather(
        _respond(tenant, seed.interaction_id, {"customer": "A"}),
        _respond(tenant, seed.interaction_id, {"customer": "B"}),
        return_exceptions=True,
    )

    outcomes = [result for result in results if isinstance(result, tuple)]
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(outcomes) == 1 and len(errors) == 1
    assert errors[0].code == "IDEMPOTENCY_MISMATCH"
    interaction = await read_interaction(tenant, seed.interaction_id)
    assert interaction.status == "RESOLVED"
    winner = interaction.response_json
    assert winner in ({"customer": "A"}, {"customer": "B"})
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == "QUEUED" and execution.resume_count == 1


async def test_answer_versus_all_active_cancel(tenant: TenantContext) -> None:
    seed = await seed_human(tenant)

    result = await asyncio.gather(
        _respond(tenant, seed.interaction_id, ANSWER),
        _cancel(tenant, seed.execution_id),
        return_exceptions=True,
    )

    interaction = await read_interaction(tenant, seed.interaction_id)
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == "CANCELLED", "取消总是可以赢，但回答不得把取消过的行拉回 QUEUED"
    assert interaction.status in {"RESOLVED", "CANCELLED"}
    if interaction.status == "CANCELLED":
        assert isinstance(result[0], AppError) and result[0].code == "EXECUTION_NOT_RESUMABLE"
        assert interaction.response_json is None
    else:
        assert not isinstance(result[1], BaseException)
        assert interaction.response_json == ANSWER
    assert await event_types(tenant, seed.execution_id, EntityType.EXECUTION) != []


async def test_answer_versus_timeout_sweep_never_revives(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    seed = await seed_human(
        tenant,
        interaction_deadline_at=now - timedelta(minutes=1),
        execution_deadline_at=now + timedelta(hours=1),
    )

    result = await asyncio.gather(
        _respond(tenant, seed.interaction_id, ANSWER),
        _sweep(tenant),
        return_exceptions=True,
    )

    assert isinstance(result[0], AppError)
    assert result[0].code == "HUMAN_INTERACTION_EXPIRED"
    assert result[1] == 1
    execution = await read_execution(tenant, seed.execution_id)
    assert execution.status == "CANCELLED", "设计口径：超时按取消收尾（不是 FAILED）"
    assert execution.error_code == HUMAN_TIMEOUT_CODE
    assert execution.failure_class == HUMAN_TIMEOUT
    interaction = await read_interaction(tenant, seed.interaction_id)
    assert interaction.status == "TIMED_OUT" and interaction.response_json is None


async def test_concurrent_timeout_sweeps_write_one_terminal_state(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    seed = await seed_human(tenant, interaction_deadline_at=now - timedelta(minutes=1))

    closed = await asyncio.gather(_sweep(tenant), _sweep(tenant))

    assert sum(closed) == 1, "同一 interaction 只有一个事务能收尾"
    assert await event_types(tenant, seed.execution_id, EntityType.EXECUTION) == ["CANCELLED"]
    assert await event_types(tenant, seed.interaction_id, EntityType.HUMAN) == ["TIMED_OUT"]


async def test_two_executions_answer_concurrently(tenant: TenantContext) -> None:
    first = await seed_human(tenant)
    second = await seed_human(tenant)

    await asyncio.gather(
        _respond(tenant, first.interaction_id, {"customer": "A"}),
        _respond(tenant, second.interaction_id, {"customer": "B"}),
    )

    for seed, expected in ((first, {"customer": "A"}), (second, {"customer": "B"})):
        interaction = await read_interaction(tenant, seed.interaction_id)
        assert interaction.status == "RESOLVED" and interaction.response_json == expected
        execution = await read_execution(tenant, seed.execution_id)
        assert execution.status == "QUEUED" and execution.resume_count == 1
