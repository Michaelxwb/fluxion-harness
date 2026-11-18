"""B-07: real PG atomic terminal writes, inbox and durable HTTP acknowledgement.

只覆盖 TASK-003 拥有的边界：`write_terminal` 的终态事务、RuntimeOperation 的取消墓碑、
以及 outbox 的派发协议。Worker/Scheduler 的收尾接线（租约 CAS、deadline sweep、claim）
由各自 TASK 的套件覆盖。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from muad_agent_runtime.api.tool_results import router as result_router
from muad_agent_runtime.infrastructure.db import get_session
from muad_agent_runtime.infrastructure.models.async_tools import ToolResultInbox
from muad_agent_worker.application.runtime_operations import cancel_operation, prepare_submission
from muad_agent_worker.application.task_events import EntityType, TaskEventType
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeResultOutbox
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
from muad_agent_worker.results.dispatcher import ResultDispatcher, ResultDispatchPolicy
from muad_api import AppError, install_api_foundation
from muad_contracts import (
    CancelOperationRequest,
    ControlOutboxStatus,
    CreateTaskRequest,
    RunToolOperationOwner,
    TerminalStatus,
)
from muad_contracts.platform_settings import default_platform_settings
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.acceptance.runtime.conftest import _Server
from tests.async_tool_helpers import seed_operation

#: 执行超时来自平台设置默认值；测试不钉死具体秒数。
_TIMEOUT_SEC = default_platform_settings().task.execution_timeout_sec

pytestmark = pytest.mark.integration


async def _terminal(factory, task, status=TerminalStatus.COMPLETED):
    change = TerminalChange(
        status=status,
        result={"answer": "真实结果"} if status is TerminalStatus.COMPLETED else None,
        error_code=None if status is TerminalStatus.COMPLETED else "TEST_FAILURE",
    )
    async with factory() as session, session.begin():
        assert await write_terminal(session, task, change, datetime.now(UTC))
    async with factory() as session:
        return (
            await session.scalars(select(RuntimeResultOutbox).where(RuntimeResultOutbox.task_id == task.id))
        ).one()


async def _execution_events(factory, task_id) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(TaskEvent)
                .where(
                    TaskEvent.entity_type == EntityType.EXECUTION.value,
                    TaskEvent.entity_id == task_id,
                )
            )
        )


async def test_b01_pending_limit_row_lock_and_distinct_tool_calls(async_tool_database):
    import asyncio

    from muad_agent_runtime.application.async_tools.operations import reserve_operation
    from muad_agent_runtime.infrastructure.models.async_tools import ToolControlOutbox, ToolOperation
    from muad_contracts import CompletionMode

    from tests.async_tool_submissions import seed_submission

    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    context, skill, _ = await seed_submission(factory)
    start = asyncio.Event()

    async def reserve(call_id):
        await start.wait()
        try:
            return await reserve_operation(
                factory,
                context,
                skill=skill,
                input_data={"same": True},
                call_id=call_id,
                completion_mode=CompletionMode.JOIN,
            )
        except AppError as exc:
            return str(exc.code)

    tasks = [asyncio.create_task(reserve(call)) for call in ("call-a", "call-b")]
    start.set()
    results = await asyncio.gather(*tasks)
    assert sum(isinstance(result, ToolOperation) for result in results) == 1
    assert "TOOL_OPERATION_CAPACITY_EXCEEDED" in results
    operation = next(result for result in results if isinstance(result, ToolOperation))
    replay = await reserve_operation(
        factory,
        context,
        skill=skill,
        input_data={"same": True},
        call_id=operation.source_tool_call_id,
        completion_mode=CompletionMode.JOIN,
    )
    assert replay.id == operation.id
    with pytest.raises(AppError) as conflict:
        await reserve_operation(
            factory,
            context,
            skill=skill,
            input_data={"same": False},
            call_id=operation.source_tool_call_id,
            completion_mode=CompletionMode.JOIN,
        )
    assert str(conflict.value.code) == "IDEMPOTENCY_MISMATCH"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ToolControlOutbox)) == 1
        assert operation.task_id is None
        assert operation.submission_json["idempotency_key"] == f"runtime-op:{operation.id}:submit"
        assert operation.submission_json["execution_snapshot"]["budget"]["max_attempts"] == 1


async def test_b01_close_registration_barrier_keeps_durable_intents(async_tool_database):
    import asyncio

    from muad_agent_runtime.application.async_tools.operations import reserve_operation
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
    from muad_agent_runtime.infrastructure.models.async_tools import ToolControlOutbox, ToolOperation
    from muad_contracts import CompletionMode

    from tests.async_tool_submissions import seed_submission

    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    context, skill, _ = await seed_submission(factory)
    registered, release = asyncio.Event(), asyncio.Event()
    supervisor = ExecutionSupervisor(close_timeout_sec=1)

    async def execution():
        operation = await reserve_operation(
            factory,
            context,
            skill=skill,
            input_data={},
            call_id="close-race",
            completion_mode=CompletionMode.JOIN,
        )
        registered.set()
        await release.wait()
        return operation.id

    assert await supervisor.submit(context.source_run_id, execution)
    await registered.wait()
    await supervisor.close()
    assert supervisor.execution_count == 0
    assert not await supervisor.submit(uuid4(), execution)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ToolOperation)) == 1
        assert await session.scalar(select(func.count()).select_from(ToolControlOutbox)) == 1


async def test_b07_atomic_terminal_rollback_and_single_event(async_tool_database):
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory)
    async with factory() as session:
        await write_terminal(
            session,
            task,
            TerminalChange(status=TerminalStatus.COMPLETED, result={"ok": True}),
            datetime.now(UTC),
        )
        await session.rollback()
        assert (await session.get(TaskExecution, task.id)).status == "QUEUED"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TaskEvent)
                .where(
                    TaskEvent.entity_type == EntityType.EXECUTION.value,
                    TaskEvent.entity_id == task.id,
                )
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RuntimeResultOutbox)
                .where(RuntimeResultOutbox.task_id == task.id)
            )
            == 0
        )
    outbox = await _terminal(factory, task)
    async with factory() as session, session.begin():
        assert not await write_terminal(
            session, task, TerminalChange(status=TerminalStatus.FAILED, error_code="LATE"), datetime.now(UTC)
        )
        assert await _execution_events(factory, task.id) == 1
        assert outbox.payload_json["result"] == {"answer": "真实结果"}


@pytest.mark.parametrize("status", list(TerminalStatus))
async def test_b07_each_terminal_shape_is_durable(async_tool_database, status):
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory)
    outbox = await _terminal(factory, task, status)
    assert outbox.payload_json["terminal_status"] == status.value
    assert outbox.payload_json["task_event_seq"] == 1
    assert outbox.status is ControlOutboxStatus.PENDING
    async with factory() as session:
        event = (
            await session.scalars(
                select(TaskEvent).where(TaskEvent.id == outbox.task_event_id)
            )
        ).one()
        assert event.entity_id == task.id
        assert event.event_type == status.value


async def test_b07_real_http_durable_ack_conflict_and_exhaustion(
    async_tool_database, monkeypatch, caplog
):
    """投递协议：只有 `persisted=true` 且回执 event_id 匹配才算送达；否则按上限退避到 FAILED。"""
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, operation, _ = await seed_operation(factory)
    outbox = await _terminal(factory, task)
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-internal-service-token")
    runtime = FastAPI()
    install_api_foundation(runtime)
    runtime.include_router(result_router)

    async def sessions():
        # A separate loop-owned engine is required by the actual TCP server.
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(async_tool_database.url)
        try:
            async with async_sessionmaker(engine)() as session:
                yield session
        finally:
            await engine.dispose()

    runtime.dependency_overrides[get_session] = sessions
    server = _Server(runtime)
    runtime_url = server.start()
    faults = {"mode": "no-ack"}
    proxy = FastAPI()

    @proxy.post("/internal/tool-results")
    async def forward(request: Request):
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                runtime_url + "/internal/tool-results",
                content=await request.body(),
                headers=dict(request.headers),
            )
        assert response.status_code == 200
        if faults["mode"] == "no-ack":
            return {"code": "0", "data": {"event_id": str(outbox.event_id), "persisted": False}}
        if faults["mode"] == "wrong-event":
            return {"code": "0", "data": {"event_id": str(uuid4()), "persisted": True}}
        return response.json()

    proxy_server = _Server(proxy)
    proxy_url = proxy_server.start()
    policy = ResultDispatchPolicy(max_attempts=2, retry_base_sec=0.01, retry_cap_sec=0.01)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            dispatcher = ResultDispatcher(
                factory,
                client,
                proxy_url,
                service_token="test-internal-service-token",
                policy=policy,
                instance_id="dispatch-a",
            )
            now = datetime.now(UTC)
            assert await dispatcher.run_once(now=now) == 1
            faults["mode"] = "wrong-event"
            assert await dispatcher.run_once(now=now + timedelta(seconds=1)) == 1
            async with factory() as session:
                row = await session.get(RuntimeResultOutbox, outbox.id)
                assert row.status is ControlOutboxStatus.FAILED and row.attempts == 2
                assert row.last_error_code == "RESULT_ACK_INVALID"
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ToolResultInbox)
                        .where(ToolResultInbox.operation_id == operation.id)
                    )
                    == 1
                )
            assert "runtime_result_dispatch_failed" in caplog.text
            headers = {"X-Internal-Service": "test-internal-service-token", "X-Tenant-Id": task.tenant_id}
            changed = dict(outbox.payload_json) | {"result": {"changed": True}}
            response = await client.post(
                runtime_url + "/internal/tool-results", json=changed, headers=headers
            )
            assert response.status_code == 409 and response.json()["code"] == "IDEMPOTENCY_MISMATCH"
    finally:
        proxy_server.stop()
        server.stop()


async def test_b07_lease_reservation_blocks_stale_ack(async_tool_database):
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory)
    outbox = await _terminal(factory, task)
    async with httpx.AsyncClient() as client:
        dispatcher = ResultDispatcher(factory, client, "http://127.0.0.1:1", instance_id="first")
        now = datetime.now(UTC)
        claimed = await dispatcher.claim(now)
        assert claimed[0].attempts == 1
        other = ResultDispatcher(factory, client, "http://127.0.0.1:1", instance_id="second")
        assert await other.claim(now) == []
        assert len(await other.claim(now + timedelta(seconds=31))) == 1
        assert not await dispatcher.ack(outbox.id, claimed[0].lease_until, now + timedelta(seconds=32))


async def test_b07_detach_and_children_never_enqueue_root_results(async_tool_database):
    from muad_contracts import CompletionMode

    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    parent, _, _ = await seed_operation(factory, mode=CompletionMode.DETACH)
    child, _, _ = await seed_operation(factory)
    async with factory() as session, session.begin():
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == child.id)
            .values(parent_id=parent.id, root_id=parent.id, tenant_id=parent.tenant_id)
        )
        child = await session.get(TaskExecution, child.id)
        assert await write_terminal(
            session, child, TerminalChange(status=TerminalStatus.COMPLETED, result={}), datetime.now(UTC)
        )
        assert await write_terminal(
            session, parent, TerminalChange(status=TerminalStatus.COMPLETED, result={}), datetime.now(UTC)
        )
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RuntimeResultOutbox)) == 0


async def test_b07_failed_prefix_does_not_starve_other_tenants(async_tool_database):
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    rows = []
    for _ in range(3):
        task, _, _ = await seed_operation(factory)
        rows.append(await _terminal(factory, task))
    now = datetime.now(UTC)
    async with httpx.AsyncClient() as client:
        dispatcher = ResultDispatcher(
            factory,
            client,
            "http://127.0.0.1:1",
            policy=ResultDispatchPolicy(batch_size=1),
            instance_id="fair",
        )
        first = (await dispatcher.claim(now))[0]
        async with factory() as session, session.begin():
            await session.execute(
                update(RuntimeResultOutbox)
                .where(RuntimeResultOutbox.id == first.id)
                .values(lease_owner=None, lease_until=None)
            )
        second = (await dispatcher.claim(now))[0]
        third = (await dispatcher.claim(now))[0]
        assert {first.id, second.id, third.id} == {row.id for row in rows}


async def test_b07_big_result_does_not_rollback_verified_terminal(async_tool_database):
    """发件投影失败只丢发件，不改写已验证的终态（Worker 正常路径会先外置/预览）。"""
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory)
    async with factory() as session, session.begin():
        assert await write_terminal(
            session,
            task,
            TerminalChange(status=TerminalStatus.COMPLETED, result={"large": "x" * (256 * 1024)}),
            datetime.now(UTC),
        )
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(RuntimeResultOutbox).where(
                    RuntimeResultOutbox.task_id == task.id
                )
            )
            == 0
        )
        stored = await session.get(TaskExecution, task.id)
        assert stored is not None and stored.status == "COMPLETED"


async def test_b07_cancel_before_submission_is_a_durable_tombstone(async_tool_database):
    """取消先到：operation 墓碑落在提交前，之后的受理被拒且不建执行行。"""
    from tests.contracts.test_runtime_strict_json import _submission

    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    data = _submission()
    data["execution_snapshot"] = {
        "schema_version": 1,
        "agent": {},
        "model": {},
        "skills": [],
        "mcp": [],
        "prompt_template_version": "v1",
        "budget": {"execution_timeout_sec": _TIMEOUT_SEC},
    }
    payload = CreateTaskRequest.model_validate(data)
    source = payload.runtime_operation
    cancel = CancelOperationRequest(
        owner=RunToolOperationOwner(run_id=source.source_run_id),
        source_tool_call_id=source.source_tool_call_id,
        actor_user_id=payload.actor_user_id,
    )
    async with factory() as session, session.begin():
        response = await cancel_operation(session, payload.tenant_id, source.operation_id, cancel)
        assert response.task_id is None and response.cancel_recorded

    async with factory() as session:
        with pytest.raises(AppError, match="TASK_OPERATION_CANCELLED"):
            await prepare_submission(session, payload)
        await session.rollback()
        from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeOperation

        row = (
            await session.scalars(
                select(RuntimeOperation).where(RuntimeOperation.operation_id == source.operation_id)
            )
        ).one()
        assert row.cancel_requested and row.submission_hash is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TaskExecution)
                .where(TaskExecution.tenant_id == payload.tenant_id)
            )
            == 0
        )
        with pytest.raises(AppError, match="TOOL_RESULT_BINDING_MISMATCH"):
            await cancel_operation(
                session,
                payload.tenant_id,
                source.operation_id,
                cancel.model_copy(update={"actor_user_id": uuid4()}),
            )


async def test_b07_terminal_event_is_execution_scoped(async_tool_database):
    """终态事件挂在 EXECUTION 实体上，不再写 `task_event.task_id`。"""
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    task, _, _ = await seed_operation(factory)
    await _terminal(factory, task)
    async with factory() as session:
        event = (
            await session.scalars(
                select(TaskEvent).where(
                    TaskEvent.entity_type == EntityType.EXECUTION.value,
                    TaskEvent.entity_id == task.id,
                )
            )
        ).one()
        assert event.event_type == str(TaskEventType.COMPLETED)
        assert event.seq == 1
