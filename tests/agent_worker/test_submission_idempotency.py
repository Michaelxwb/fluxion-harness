"""命令幂等台账：advisory 锁 → 重放校验 → 写入，partial unique 兜底（RULE-api-002 / §3.5.1）。

真实边界：真实 PG 事务级 advisory 锁 + `task.task_submission` partial unique。
不 mock 数据库，也不以单次顺序调用冒充并发。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_worker.application.submissions import (
    SubmissionEndpoint,
    TaskSubmissionService,
    resolve_idempotency_key,
    submission_fingerprint,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_api import AppError
from muad_contracts.platform_settings import default_platform_settings
from sqlalchemy.ext.asyncio import AsyncSession

from agent_worker.conftest import TenantContext

COUNT_SUBMISSIONS = sa.text("SELECT count(*) FROM task.task_submission WHERE tenant_id = :tenant_id")
COUNT_TASKS = sa.text("SELECT count(*) FROM task.task_execution WHERE tenant_id = :tenant_id")
ALL_ENDPOINTS = list(SubmissionEndpoint)


async def _scalar(tenant: TenantContext, statement: sa.TextClause) -> int:
    async with tenant.session_factory() as session:
        return (await session.execute(statement, {"tenant_id": tenant.tenant_id})).scalar()


def _fingerprint(
    tenant: TenantContext,
    *,
    endpoint: SubmissionEndpoint = SubmissionEndpoint.TASKS,
    actor: uuid.UUID,
    target: uuid.UUID | None = None,
    action: dict[str, Any] | None = None,
) -> str:
    return submission_fingerprint(
        endpoint=endpoint,
        tenant_id=tenant.tenant_id,
        actor_user_id=actor,
        target_id=target,
        action=action,
    )


def _new_execution(tenant: TenantContext, actor: uuid.UUID, key: str) -> TaskExecution:
    now = datetime.now(UTC)
    return TaskExecution(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        agent_id=uuid.uuid4(),
        actor_user_id=actor,
        skill_id=uuid.uuid4(),
        skill_artifact_id=uuid.uuid4(),
        trigger_type="IMMEDIATE",
        task_type="SKILL",
        status="QUEUED",
        input_json={"customers": ["A"]},
        execution_snapshot_schema_version=4,
        execution_snapshot_json={"schema_version": 4},
        snapshot_hash="sha256:" + "a" * 64,
        idempotency_key=key,
        priority=100,
        attempt=0,
        max_attempts=default_platform_settings().task.max_attempts,
        not_before=now,
        deadline_at=now + timedelta(hours=1),
    )


async def _run_command(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    key: str,
    actor: uuid.UUID,
    endpoint: SubmissionEndpoint = SubmissionEndpoint.TASKS,
    target: uuid.UUID | None = None,
    action: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """一条命令：admit → 首次则写业务行 + 记录响应；重放则直接返回首次响应。"""
    service = TaskSubmissionService(session)
    fingerprint = _fingerprint(
        tenant, endpoint=endpoint, actor=actor, target=target, action=action
    )
    replay = await service.admit(
        tenant_id=tenant.tenant_id,
        idempotency_key=key,
        endpoint=endpoint,
        fingerprint=fingerprint,
    )
    if replay is not None:
        return "replay", replay.response_json
    task = _new_execution(tenant, actor, key)
    session.add(task)
    await session.flush()
    await service.record_in(
        tenant_id=tenant.tenant_id,
        idempotency_key=key,
        endpoint=endpoint,
        actor_user_id=actor,
        request_fingerprint=fingerprint,
        response={"task_id": str(task.id)},
        task_id=task.id,
    )
    return "first", {"task_id": str(task.id)}


async def _one_round(tenant: TenantContext, key: str, actor: uuid.UUID) -> dict[str, Any]:
    async with tenant.session_factory() as session:
        async with session.begin():
            _state, response = await _run_command(session, tenant, key=key, actor=actor)
    return response


async def test_same_key_same_payload_replays_first_response(tenant: TenantContext) -> None:
    """同 key 同指纹：重放首次响应，不重复建资源。"""
    actor = uuid.uuid4()
    first = await _one_round(tenant, "replay-1", actor)
    second = await _one_round(tenant, "replay-1", actor)

    assert first == second
    assert await _scalar(tenant, COUNT_TASKS) == 1
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


@pytest.mark.parametrize(
    "mutation",
    ["action", "actor", "target"],
)
async def test_same_key_with_different_command_is_rejected(
    tenant: TenantContext, mutation: str
) -> None:
    """同 key 不同动作载荷/actor/目标/endpoint：IDEMPOTENCY_MISMATCH，不覆盖首次结果。"""
    actor = uuid.uuid4()
    target = uuid.uuid4()
    base = await _one_round(tenant, f"mismatch-{mutation}", actor)

    changed_actor = uuid.uuid4() if mutation == "actor" else actor
    changed_target = uuid.uuid4() if mutation == "target" else target
    action = {"reason": "policy_check"} if mutation == "action" else None

    async with tenant.session_factory() as session:
        await session.begin()
        with pytest.raises(AppError) as conflict:
            await _run_command(
                session,
                tenant,
                key=f"mismatch-{mutation}",
                actor=changed_actor,
                endpoint=SubmissionEndpoint.TASKS,
                target=changed_target,
                action=action,
            )
        await session.rollback()

    assert str(conflict.value.code) == "IDEMPOTENCY_MISMATCH"
    assert await _task_ids(tenant) == [base["task_id"]]
    assert await _scalar(tenant, COUNT_TASKS) == 1


async def _task_ids(tenant: TenantContext) -> list[str]:
    async with tenant.session_factory() as session:
        rows = await session.execute(
            sa.text("SELECT id::text FROM task.task_execution WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )
        return [row[0] for row in rows.all()]


def test_header_key_must_agree_with_body_key() -> None:
    """Header 与 body 幂等键同时出现时必须一致。"""
    assert resolve_idempotency_key("same", "same") == "same"
    assert resolve_idempotency_key(None, "body") == "body"
    assert resolve_idempotency_key("header", None) == "header"
    with pytest.raises(AppError) as conflict:
        resolve_idempotency_key("a-different-key", "body-key")
    assert str(conflict.value.code) == "IDEMPOTENCY_MISMATCH"
    with pytest.raises(AppError) as missing:
        resolve_idempotency_key(None, None)
    assert str(missing.value.code) == "COMMON_VALIDATION_ERROR"


async def test_endpoint_is_part_of_the_key(tenant: TenantContext) -> None:
    """同名幂等键在不同 endpoint 上是两条独立命令，互不重放。"""
    actor = uuid.uuid4()
    async with tenant.session_factory() as session:
        async with session.begin():
            service = TaskSubmissionService(session)
            for endpoint in (SubmissionEndpoint.SCHEDULE_DRAFTS, SubmissionEndpoint.SCHEDULE_CONFIRM):
                assert (
                    await service.admit(
                        tenant_id=tenant.tenant_id,
                        idempotency_key="shared-key",
                        endpoint=endpoint,
                        fingerprint=_fingerprint(tenant, endpoint=endpoint, actor=actor),
                    )
                    is None
                )
                await service.record_in(
                    tenant_id=tenant.tenant_id,
                    idempotency_key="shared-key",
                    endpoint=endpoint,
                    actor_user_id=actor,
                    request_fingerprint=_fingerprint(tenant, endpoint=endpoint, actor=actor),
                    response={"endpoint": str(endpoint)},
                )
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 2


async def test_failed_command_does_not_reserve_the_key(tenant: TenantContext) -> None:
    """失败请求不占成功记录：事务回滚后同键仍可成功。"""
    actor = uuid.uuid4()
    async with tenant.session_factory() as session:
        await session.begin()
        await _run_command(session, tenant, key="fail-then-ok", actor=actor)
        await session.rollback()

    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 0

    accepted = await _one_round(tenant, "fail-then-ok", actor)
    assert accepted["task_id"]
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS)
async def test_every_command_endpoint_is_admitted(
    tenant: TenantContext, endpoint: SubmissionEndpoint
) -> None:
    """六类命令共用同一台账：endpoint 是短标识，各自独立成键。"""
    actor = uuid.uuid4()
    first = await _one_round_with(tenant, f"endpoint-{endpoint}", actor, endpoint)
    replay = await _one_round_with(tenant, f"endpoint-{endpoint}", actor, endpoint)

    assert first == replay
    async with tenant.session_factory() as session:
        endpoint_value = (
            await session.execute(
                sa.text(
                    "SELECT endpoint FROM task.task_submission "
                    "WHERE tenant_id = :tenant_id AND idempotency_key = :key"
                ),
                {"tenant_id": tenant.tenant_id, "key": f"endpoint-{endpoint}"},
            )
        ).scalar_one()
    assert endpoint_value == str(endpoint)


async def _one_round_with(
    tenant: TenantContext, key: str, actor: uuid.UUID, endpoint: SubmissionEndpoint
) -> dict[str, Any]:
    async with tenant.session_factory() as session:
        async with session.begin():
            _state, response = await _run_command(
                session, tenant, key=key, actor=actor, endpoint=endpoint
            )
    return response


async def test_concurrent_same_key_creates_one_resource(tenant: TenantContext) -> None:
    """并发同 key：advisory 锁串行化，只成功一次，落败者读到首次结果。"""
    actor = uuid.uuid4()

    async def attempt() -> tuple[str, dict[str, Any]]:
        async with tenant.session_factory() as session:
            async with session.begin():
                return await _run_command(session, tenant, key="concurrent-1", actor=actor)

    results = await asyncio.gather(
        *(attempt() for _ in range(4)), return_exceptions=True
    )
    failures = [item for item in results if isinstance(item, BaseException)]
    assert not failures, f"并发提交抛异常: {failures!r}"

    task_ids = {response["task_id"] for _state, response in results}
    assert len(task_ids) == 1, f"并发同 key 建出多个资源: {task_ids}"
    assert [state for state, _ in results].count("first") == 1
    assert await _scalar(tenant, COUNT_TASKS) == 1
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


async def test_partial_unique_is_the_backstop_when_lock_is_skipped(tenant: TenantContext) -> None:
    """绕过 admit 的并发写入由 partial unique 拦住：落败者拿回赢家那一行。"""
    actor = uuid.uuid4()
    fingerprint = _fingerprint(tenant, actor=actor)
    service_kwargs = {
        "tenant_id": tenant.tenant_id,
        "idempotency_key": "backstop-1",
        "endpoint": SubmissionEndpoint.EXECUTION_CANCEL,
        "actor_user_id": actor,
        "request_fingerprint": fingerprint,
        "response": {"cancel_recorded": True},
    }

    holder = tenant.session_factory()
    await holder.begin()
    winner = await TaskSubmissionService(holder).record_in(**service_kwargs)

    loser = tenant.session_factory()
    await loser.begin()
    raced = asyncio.create_task(TaskSubmissionService(loser).record_in(**service_kwargs))
    await asyncio.sleep(0.3)
    assert not raced.done(), "并发插入没有被 partial unique 拦住"

    await holder.commit()
    await holder.close()
    result = await asyncio.wait_for(raced, timeout=5)
    await loser.commit()
    await loser.close()

    assert result.id == winner.id
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


async def test_partial_unique_backstop_still_rejects_mismatch(tenant: TenantContext) -> None:
    """兜底路径同样判指纹：不同载荷撞同一键仍是 IDEMPOTENCY_MISMATCH。"""
    actor = uuid.uuid4()
    common = {
        "tenant_id": tenant.tenant_id,
        "idempotency_key": "backstop-mismatch",
        "endpoint": SubmissionEndpoint.HUMAN_RESPOND,
        "actor_user_id": actor,
        "response": {"ok": True},
    }
    async with tenant.session_factory() as session:
        async with session.begin():
            await TaskSubmissionService(session).record_in(
                **common, request_fingerprint=_fingerprint(tenant, actor=actor, action={"a": 1})
            )
        with pytest.raises(AppError) as conflict:
            async with session.begin():
                await TaskSubmissionService(session).record_in(
                    **common,
                    request_fingerprint=_fingerprint(tenant, actor=actor, action={"a": 2}),
                )
    assert str(conflict.value.code) == "IDEMPOTENCY_MISMATCH"
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


def test_fingerprint_is_stable_and_discriminating() -> None:
    """指纹键序无关，且对 endpoint/tenant/actor/目标/载荷都敏感。"""
    actor, target = uuid.uuid4(), uuid.uuid4()
    base = {
        "endpoint": SubmissionEndpoint.SCHEDULE_ACTIONS,
        "tenant_id": "test-1",
        "actor_user_id": actor,
        "target_id": target,
        "action": {"cancel_scope": "ALL_ACTIVE", "expected_config_version": 3},
    }
    fingerprint = submission_fingerprint(**base)
    assert submission_fingerprint(**{**base, "action": {
        "expected_config_version": 3,
        "cancel_scope": "ALL_ACTIVE",
    }}) == fingerprint
    assert submission_fingerprint(**{**base, "tenant_id": "test-2"}) != fingerprint
    assert submission_fingerprint(**{**base, "actor_user_id": uuid.uuid4()}) != fingerprint
    assert submission_fingerprint(**{**base, "target_id": uuid.uuid4()}) != fingerprint
    assert submission_fingerprint(
        **{**base, "endpoint": SubmissionEndpoint.EXECUTION_CANCEL}
    ) != fingerprint
    assert submission_fingerprint(
        **{**base, "action": {"cancel_scope": "FUTURE_ONLY", "expected_config_version": 3}}
    ) != fingerprint
