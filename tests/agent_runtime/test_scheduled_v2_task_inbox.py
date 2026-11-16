"""S-17：Task 结果收件箱的 kind 分流、绑定校验、幂等与可重放读取（真实 PG）。

原则：Task 结果只以 Task-owned inbox 为准；回执指向 `task.task_event`（逻辑 UUID，无跨
schema FK），绝不写 canonical Run 事件冒充 Task receipt；读取不改任何行，消费水位由 Worker
自己的事务持有。RUN 的既有链路（canonical event + continuation）在同一入口保持可用。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from muad_agent_runtime.application.async_tools.task_operations import submit, view
from muad_agent_runtime.application.tool_result_receipts import receive_result
from muad_agent_runtime.infrastructure.models.async_tools import (
    RunContinuation,
    ToolOperation,
    ToolResultInbox,
)
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
from muad_api import AppError
from muad_contracts import (
    TaskToolOperationSubmitRequest,
    TerminalStatus,
    ToolResultRequest,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration

HASH = "sha256:" + "a" * 64
RESULT_HASH = "sha256:" + "b" * 64


@pytest.fixture
async def factory(async_tool_database):
    return async_sessionmaker(async_tool_database, expire_on_commit=False)


async def _execution(factory, *, tenant: str, actor: uuid.UUID, snapshot_hash: str, **extra: Any):
    now = datetime.now(UTC)
    execution = TaskExecution(
        id=uuid.uuid4(),
        tenant_id=tenant,
        agent_id=uuid.uuid4(),
        actor_user_id=actor,
        trigger_type="SCHEDULED",
        task_type="AGENTIC",
        status=extra.pop("status", "RUNNING"),
        input_json={},
        execution_snapshot_schema_version=4,
        execution_snapshot_json={},
        snapshot_hash=snapshot_hash,
        idempotency_key=str(uuid.uuid4()),
        not_before=now,
        deadline_at=now + timedelta(hours=1),
        **extra,
    )
    async with factory() as session, session.begin():
        session.add(execution)
    return execution


async def _event(factory, execution: TaskExecution, *, seq: int = 1) -> TaskEvent:
    event = TaskEvent(
        tenant_id=execution.tenant_id,
        entity_type="EXECUTION",
        entity_id=execution.id,
        seq=seq,
        event_type="COMPLETED",
        payload_json={},
    )
    async with factory() as session, session.begin():
        session.add(event)
    return event


async def seed_task_operation(factory) -> tuple[TaskExecution, TaskExecution, TaskEvent, uuid.UUID]:
    """owner Execution + 执行 skill 的子 Task（终态事件挂它）+ 已提交的 operation。"""
    tenant, actor = str(uuid.uuid4()), uuid.uuid4()
    owner = await _execution(factory, tenant=tenant, actor=actor, snapshot_hash=HASH)
    child = await _execution(
        factory, tenant=tenant, actor=actor, snapshot_hash=RESULT_HASH, status="COMPLETED"
    )
    event = await _event(factory, child)
    async with factory() as session, session.begin():
        created = await submit(
            session,
            tenant_id=tenant,
            actor_user_id=actor,
            request=TaskToolOperationSubmitRequest.model_validate(
                {
                    "owner": {"kind": "TASK", "task_execution_id": str(owner.id)},
                    "source_tool_call_id": "call-1",
                    "tool_name": "crm.customer.report",
                    "input_hash": HASH,
                    "arguments": {"customer": "A"},
                }
            ),
        )
    return owner, child, event, created.operation_id


def task_receipt(
    owner: TaskExecution,
    child: TaskExecution,
    event: TaskEvent,
    operation_id: uuid.UUID,
    *,
    event_id: uuid.UUID | None = None,
    **overrides: Any,
) -> ToolResultRequest:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "event_id": event_id or uuid.uuid4(),
        "operation_id": operation_id,
        "task_id": child.id,
        "task_event_seq": event.seq,
        "owner": {"kind": "TASK", "task_execution_id": str(owner.id)},
        "actor_user_id": child.actor_user_id,
        "task_snapshot_hash": child.snapshot_hash,
        "terminal_status": "COMPLETED",
        "completed_at": datetime.now(UTC),
        "result": {"answer": "真实结果"},
    }
    payload.update(overrides)
    return ToolResultRequest.model_validate(payload)


async def inbox_rows(factory) -> list[ToolResultInbox]:
    async with factory() as session:
        return list(await session.scalars(select(ToolResultInbox).order_by(ToolResultInbox.create_time)))


async def test_task_receipt_keeps_task_event_identity_and_writes_no_run_event(factory):
    owner, child, event, operation_id = await seed_task_operation(factory)
    request = task_receipt(owner, child, event, operation_id)
    async with factory() as session, session.begin():
        receipt = await receive_result(session, owner.tenant_id, request)
    assert receipt.persisted is True and receipt.duplicate is False
    rows = await inbox_rows(factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.run_id is None and row.task_execution_id == owner.id
    assert row.receipt_event_type == "TASK_EVENT" and row.receipt_event_id == event.id
    assert row.receipt_seq == event.seq and row.task_event_seq == event.seq
    assert row.canonical_event_id is None and row.materialized is False and row.late is False
    assert row.task_id == child.id
    async with factory() as session:
        # 禁止用 canonical Run 事件冒充 Task receipt，也不因此建任何续接/通知。
        assert await session.scalar(select(func.count()).select_from(CanonicalEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(RunContinuation)) == 0
        stored = await session.get(ToolOperation, operation_id)
        assert stored is not None
        # Task owner 没有提交快照：首份回执把子 Task 的冻结快照写进 operation。
        assert stored.task_snapshot_hash == RESULT_HASH
        operation = await view(session, stored)
    assert operation.status.value == "RESULT_RECEIVED"
    assert operation.task_id == child.id
    assert operation.result is not None
    assert operation.result.task_event_id == event.id
    assert operation.result.terminal_status is TerminalStatus.COMPLETED
    assert operation.result.late is False


async def _operation_row(session, operation_id: uuid.UUID) -> ToolOperation:
    return await session.get(ToolOperation, operation_id)


async def test_duplicate_event_is_idempotent_and_changed_payload_conflicts(factory):
    owner, child, event, operation_id = await seed_task_operation(factory)
    request = task_receipt(owner, child, event, operation_id)
    async with factory() as session, session.begin():
        await receive_result(session, owner.tenant_id, request)
    async with factory() as session, session.begin():
        duplicate = await receive_result(session, owner.tenant_id, request)
    assert duplicate.persisted is True and duplicate.duplicate is True
    assert len(await inbox_rows(factory)) == 1
    changed = ToolResultRequest.model_validate(
        request.model_dump(mode="json") | {"result": {"answer": "被改写的结果"}}
    )
    async with factory() as session:
        with pytest.raises(AppError, match="IDEMPOTENCY_MISMATCH"):
            await receive_result(session, owner.tenant_id, changed)
        await session.rollback()
    assert len(await inbox_rows(factory)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"task_event_seq": 999},
        {"task_event_seq": 2},
        {"task_snapshot_hash": "sha256:" + "c" * 64},
        {"task_id": uuid.uuid4()},
        {"actor_user_id": uuid.uuid4()},
    ],
)
async def test_receipt_must_bind_to_this_tasks_own_terminal_event(factory, changes):
    owner, child, event, operation_id = await seed_task_operation(factory)
    request = task_receipt(owner, child, event, operation_id, **changes)
    async with factory() as session:
        with pytest.raises(AppError, match="TOOL_RESULT_BINDING_MISMATCH"):
            await receive_result(session, owner.tenant_id, request)
        await session.rollback()
    assert await inbox_rows(factory) == []


async def test_receipt_from_another_tenant_or_owner_is_rejected(factory):
    owner, child, event, operation_id = await seed_task_operation(factory)
    request = task_receipt(owner, child, event, operation_id)
    async with factory() as session:
        with pytest.raises(AppError, match="TOOL_RESULT_BINDING_MISMATCH"):
            await receive_result(session, str(uuid.uuid4()), request)
        await session.rollback()
    foreign_owner = await _execution(
        factory, tenant=owner.tenant_id, actor=owner.actor_user_id, snapshot_hash=HASH
    )
    forged = ToolResultRequest.model_validate(
        request.model_dump(mode="json")
        | {"owner": {"kind": "TASK", "task_execution_id": str(foreign_owner.id)}}
    )
    async with factory() as session:
        with pytest.raises(AppError, match="TOOL_RESULT_BINDING_MISMATCH"):
            await receive_result(session, owner.tenant_id, forged)
        await session.rollback()
    assert await inbox_rows(factory) == []


async def test_late_result_after_cancel_is_recorded_but_never_resumes(factory):
    owner, child, event, operation_id = await seed_task_operation(factory)
    async with factory() as session, session.begin():
        operation = await _operation_row(session, operation_id)
        operation.cancel_requested = True
        operation.status = "CANCELLED"
    request = task_receipt(owner, child, event, operation_id)
    async with factory() as session, session.begin():
        await receive_result(session, owner.tenant_id, request)
    row = (await inbox_rows(factory))[0]
    assert row.late is True
    async with factory() as session:
        operation = await _operation_row(session, operation_id)
        assert operation.status.value == "LATE"
        assert await session.scalar(select(func.count()).select_from(CanonicalEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(RunContinuation)) == 0


async def test_reads_are_replayable_and_never_consume(factory):
    owner, child, event, operation_id = await seed_task_operation(factory)
    request = task_receipt(owner, child, event, operation_id)
    async with factory() as session, session.begin():
        await receive_result(session, owner.tenant_id, request)
    views = []
    for _ in range(3):
        async with factory() as session:
            operation = await _operation_row(session, operation_id)
            views.append(await view(session, operation))
    assert [item.result.event_id for item in views] == [request.event_id] * 3
    rows = await inbox_rows(factory)
    assert len(rows) == 1 and rows[0].materialized is False


async def test_run_owned_receipt_keeps_canonical_event_and_continuation(async_tool_database):
    """RUN owner 的既有链路不被 Task 协议改写：canonical 事件 + continuation 仍照旧。"""
    from tests.async_tool_continuations import operation as reserve_operation
    from tests.async_tool_continuations import receipt as run_receipt
    from tests.async_tool_continuations import running_source

    source_factory, context, skill, run, _ = await running_source(async_tool_database)
    operation = await reserve_operation(source_factory, context, skill)
    async with source_factory() as session, session.begin():
        session.add(
            RunContinuation(
                tenant_id=run.tenant_id,
                run_id=run.id,
                snapshot_id=run.snapshot_id,
                context_upto_seq=0,
                runner_state_json={},
            )
        )
    request = await run_receipt(source_factory, run, operation, {"ok": True})
    async with source_factory() as session:
        row = (await session.scalars(select(ToolResultInbox))).one()
        assert row.run_id == run.id and row.task_execution_id is None
        assert row.receipt_event_type == "RUN_EVENT" and row.canonical_event_id is not None
        assert row.receipt_event_id == row.canonical_event_id
        assert row.receipt_seq > 0
        assert await session.scalar(select(func.count()).select_from(CanonicalEvent)) == 1
        continuation = await session.scalar(select(RunContinuation))
        assert continuation is not None and continuation.ready is True
    assert request.owner.kind == "RUN"


async def test_run_owned_receipt_rejects_changed_snapshot_hash(async_tool_database):
    """RUN 行保留提交时冻结的快照 hash 比对：换 hash 的回执仍是绑定不匹配。"""
    from tests.async_tool_continuations import operation as reserve_operation
    from tests.async_tool_continuations import running_source

    source_factory, context, skill, run, _ = await running_source(async_tool_database)
    operation = await reserve_operation(source_factory, context, skill)
    request = ToolResultRequest.model_validate(
        {
            "schema_version": 1,
            "event_id": uuid.uuid4(),
            "operation_id": operation.id,
            "task_id": uuid.uuid4(),
            "task_event_seq": 1,
            "owner": {"kind": "RUN", "run_id": str(run.id)},
            "actor_user_id": run.user_id,
            "task_snapshot_hash": "sha256:" + "c" * 64,
            "terminal_status": "COMPLETED",
            "completed_at": datetime.now(UTC),
            "result": {"ok": True},
        }
    )
    async with source_factory() as session:
        with pytest.raises(AppError, match="TOOL_RESULT_BINDING_MISMATCH"):
            await receive_result(session, run.tenant_id, request)
        await session.rollback()
    async with source_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ToolResultInbox)) == 0


def test_owner_union_is_discriminated():
    """owner 联合必须显式带 kind：缺 kind 或未知 kind 一律 422，不做“按字段猜”。"""
    from pydantic import ValidationError

    base = {
        "schema_version": 1,
        "event_id": uuid.uuid4(),
        "operation_id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "task_event_seq": 1,
        "actor_user_id": uuid.uuid4(),
        "task_snapshot_hash": HASH,
        "terminal_status": "COMPLETED",
        "completed_at": datetime.now(UTC),
        "result": {"ok": True},
    }
    with pytest.raises(ValidationError):
        ToolResultRequest.model_validate(base | {"owner": {"run_id": str(uuid.uuid4())}})
    with pytest.raises(ValidationError):
        ToolResultRequest.model_validate(base | {"owner": {"kind": "PROBE", "probe_id": str(uuid.uuid4())}})
