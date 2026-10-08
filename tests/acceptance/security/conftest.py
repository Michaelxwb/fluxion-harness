"""E-11 验收环境：task-schedule 真栈（真实 Console/Runtime/Worker 进程、真实 PG/Redis）。

本域不做任何业务 mock：内部服务门控、结果回流的绑定校验与 Console 租户投影都打在真实 HTTP
服务 + 真实 PostgreSQL 上。用例自带**另一个租户**作为跨租户/伪造租户头的对照面，并在模块
收尾清理本域种下的 async-tool / task 行（task-schedule 收尾只清它自己租户的既有表）。

Console 登录用的是 task-schedule 栈种子里的真实管理员账号（`seed_control`）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.task_schedule.conftest import live_stack as schedule_stack
from tests.acceptance.task_schedule.environment import TENANT, LiveStack, run_db

#: 跨租户/伪造租户头对照面：与 task-schedule 栈租户不同的另一个真实落库租户。
OTHER_TENANT = "e2e-task-schedule-other"
SNAPSHOT_HASH = "sha256:" + "a" * 64

#: 本域为验证绑定校验而种下的行；删除顺序按 FK 依赖（先引用方、后被引用方）。
_ASYNC_TOOL_TABLES = ("tool_control_outbox", "tool_result_inbox", "run_continuation", "tool_operation")


@dataclass(frozen=True)
class SeededOperation:
    """真实落库的一条“已受理”异步操作（含 Task 与 Run 身份）。"""

    run_id: uuid.UUID
    run_trace_id: str
    actor_user_id: uuid.UUID
    operation_id: uuid.UUID
    task_id: uuid.UUID
    task_snapshot_hash: str


@pytest.fixture(scope="module", name="live_stack")
def security_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    yield from schedule_stack.__wrapped__(tmp_path_factory)  # type: ignore[attr-defined]


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30, trust_env=False) as client:
        yield client


def _task_row(*, tenant: str, actor: uuid.UUID, agent_id: uuid.UUID, operation_id: uuid.UUID):
    from muad_agent_worker.infrastructure.models.task import TaskExecution

    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    return TaskExecution(
        id=task_id,
        tenant_id=tenant,
        source_run_id=None,  # 事务内由调用方补真实 run_id
        source_operation_id=operation_id,
        source_tool_call_id="call-security",
        agent_id=agent_id,
        actor_user_id=actor,
        intent_key="security",
        skill_id=uuid.uuid4(),
        skill_artifact_id=uuid.uuid4(),
        trigger_type="IMMEDIATE",
        execution_mode="ASYNC",
        task_type="SKILL",
        status="RUNNING",
        input_json={},
        execution_snapshot_schema_version=1,
        execution_snapshot_json={},
        snapshot_hash=SNAPSHOT_HASH,
        idempotency_key=str(task_id),
        priority=100,
        attempt=1,
        max_attempts=1,
        not_before=now,
        deadline_at=now + timedelta(hours=1),
        delivery_mode="NONE",
        delivery_status="NONE",
        delivery_key=f"task:{task_id}:final",
        delivery_attempts=0,
        lease_owner="security-seed",
        lease_until=now + timedelta(minutes=10),
    )


async def seed_accepted_operation(
    factory: async_sessionmaker[AsyncSession], *, tenant: str
) -> SeededOperation:
    """种一条已受理的 operation（TASK_ACCEPTED + 真实 Task 行 + 快照 hash）。"""
    from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation
    from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
    from muad_contracts import CompletionMode, OperationStatus, RunStatus

    actor, operation_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as session, session.begin():
        conversation = Conversation(tenant_id=tenant, user_id=actor, agent_id=uuid.uuid4())
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant,
            conversation_id=conversation.id,
            user_id=actor,
            agent_id=conversation.agent_id,
            status=RunStatus.RUNNING,
            input_text="security-canary-input",
            trace_id=f"security-{uuid.uuid4().hex[:12]}",
            deadline_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        session.add(run)
        await session.flush()
        task = _task_row(
            tenant=tenant, actor=actor, agent_id=conversation.agent_id, operation_id=operation_id
        )
        task.source_run_id = run.id
        session.add(task)
        session.add(
            ToolOperation(
                id=operation_id,
                tenant_id=tenant,
                run_id=run.id,
                actor_user_id=actor,
                source_tool_call_id="call-security",
                completion_mode=CompletionMode.JOIN,
                status=OperationStatus.TASK_ACCEPTED,
                task_id=task.id,
                task_snapshot_hash=SNAPSHOT_HASH,
                submission_json={},
                input_hash=SNAPSHOT_HASH,
            )
        )
        await session.flush()
        return SeededOperation(
            run_id=run.id,
            run_trace_id=run.trace_id,
            actor_user_id=actor,
            operation_id=operation_id,
            task_id=task.id,
            task_snapshot_hash=SNAPSHOT_HASH,
        )


@pytest.fixture(scope="module", autouse=True)
def clean_security_rows(live_stack: LiveStack) -> Iterator[None]:
    """模块收尾把本域种子清零；依赖 `live_stack` 确保先于栈清理收尾执行。"""
    yield

    async def purge(factory: async_sessionmaker[AsyncSession]) -> None:
        tenants = {"a": TENANT, "b": OTHER_TENANT}
        async with factory() as session, session.begin():
            for table in _ASYNC_TOOL_TABLES:
                await session.execute(
                    text(f"DELETE FROM runtime.{table} WHERE tenant_id IN (:a, :b)"), tenants
                )
            await session.execute(
                text("DELETE FROM task.task_execution WHERE tenant_id IN (:a, :b)"), tenants
            )
            await session.execute(
                text("DELETE FROM runtime.canonical_event WHERE tenant_id IN (:a, :b)"), tenants
            )
            await session.execute(
                text("DELETE FROM runtime.run_record WHERE tenant_id IN (:a, :b)"), tenants
            )
            await session.execute(
                text("DELETE FROM runtime.conversation WHERE tenant_id IN (:a, :b)"), tenants
            )

    run_db(purge)
