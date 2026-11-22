"""S-17/API-30：Task-owned ASYNC 操作的提交 / 查找 / 读取 / 取消（真实 PG + 真实 HTTP）。

只断言本 TASK 拥有的边界：调用身份唯一（input_hash 只做冲突判定）、归属按 Execution 行复核、
取消意图先持久化（含 cancel-before-submit 的墓碑）、读取可重放。Worker 侧的补扫与消费在
TASK-020 自己的套件里覆盖。
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from muad_agent_runtime.api.tool_operations import router as tool_operations_router
from muad_agent_runtime.infrastructure.db import get_session
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_api import install_api_foundation
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.acceptance.runtime.conftest import _Server

pytestmark = pytest.mark.integration

TOKEN = "task-operation-internal-token"
HASH = "sha256:" + "a" * 64


@pytest.fixture
def runtime_api(async_tool_database, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    app = FastAPI()
    install_api_foundation(app)
    app.include_router(tool_operations_router)

    async def sessions():
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(async_tool_database.url)
        try:
            async with async_sessionmaker(engine)() as session:
                yield session
        finally:
            await engine.dispose()

    app.dependency_overrides[get_session] = sessions
    server = _Server(app)
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


@pytest.fixture
async def factory(async_tool_database):
    return async_sessionmaker(async_tool_database, expire_on_commit=False)


def headers(tenant: str, actor: uuid.UUID) -> dict[str, str]:
    return {
        "X-Internal-Service": TOKEN,
        "X-Tenant-Id": tenant,
        "X-Actor-User-Id": str(actor),
    }


async def seed_execution(
    factory,
    *,
    tenant: str | None = None,
    actor: uuid.UUID | None = None,
    status: str = "RUNNING",
    cancel_requested: bool = False,
) -> TaskExecution:
    tenant, actor = tenant or str(uuid.uuid4()), actor or uuid.uuid4()
    now = datetime.now(UTC)
    execution = TaskExecution(
        id=uuid.uuid4(),
        tenant_id=tenant,
        agent_id=uuid.uuid4(),
        actor_user_id=actor,
        trigger_type="SCHEDULED",
        task_type="AGENTIC",
        status=status,
        input_json={},
        execution_snapshot_schema_version=4,
        execution_snapshot_json={},
        snapshot_hash=HASH,
        idempotency_key=str(uuid.uuid4()),
        cancel_requested=cancel_requested,
        not_before=now,
        deadline_at=now + timedelta(hours=1),
    )
    async with factory() as session, session.begin():
        session.add(execution)
    return execution


def submit_body(
    execution: TaskExecution, *, call_id: str = "call-1", input_hash: str = HASH
) -> dict[str, Any]:
    return {
        "owner": {"kind": "TASK", "task_execution_id": str(execution.id)},
        "source_tool_call_id": call_id,
        "tool_name": "crm.customer.report",
        "input_hash": input_hash,
        "arguments": {"customer": "A"},
        "completion_mode": "JOIN",
    }


async def count(factory, model) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)))


async def operation_rows(factory) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, owner_type, run_id, owner_task_execution_id, status, "
                    "cancel_requested, input_hash, task_id, submission_json "
                    "FROM runtime.tool_operation ORDER BY create_time"
                )
            )
        ).all()
    return [dict(row._mapping) for row in rows]


async def control_commands(factory) -> list[str]:
    async with factory() as session:
        return list(
            await session.scalars(
                text("SELECT command FROM runtime.tool_control_outbox ORDER BY create_time")
            )
        )


async def test_submit_records_task_owner_without_touching_run_or_conversation(runtime_api, factory):
    execution = await seed_execution(factory)
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
    assert response.status_code == 200, response.text
    data = json.loads(response.text)["data"]
    assert data["owner"] == {"kind": "TASK", "task_execution_id": str(execution.id)}
    assert data["source_tool_call_id"] == "call-1"
    assert data["tool_name"] == "crm.customer.report"
    assert data["status"] == "SUBMITTED"
    assert data["task_id"] is None and data["result"] is None
    rows = await operation_rows(factory)
    assert len(rows) == 1
    assert rows[0]["owner_type"] == "TASK" and rows[0]["run_id"] is None
    assert rows[0]["owner_task_execution_id"] == execution.id
    assert rows[0]["status"] == "SUBMITTED" and rows[0]["cancel_requested"] is False
    assert await count(factory, TaskExecution) == 1
    async with factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM runtime.run_record")) == 0
        assert await session.scalar(text("SELECT count(*) FROM runtime.conversation")) == 0


async def test_retry_returns_same_operation_and_changed_input_fails_explicitly(runtime_api, factory):
    execution = await seed_execution(factory)
    tenant, actor = execution.tenant_id, execution.actor_user_id
    async with httpx.AsyncClient(trust_env=False) as client:
        first = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers=headers(tenant, actor),
        )
        replay = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution) | {"arguments": {"customer": "A"}},
            headers=headers(tenant, actor),
        )
        changed = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution, input_hash="sha256:" + "b" * 64),
            headers=headers(tenant, actor),
        )
    assert first.status_code == replay.status_code == 200, replay.text
    assert json.loads(first.text)["data"]["operation_id"] == json.loads(replay.text)["data"]["operation_id"]
    assert changed.status_code == 409 and json.loads(changed.text)["code"] == "TOOL_OPERATION_INPUT_MISMATCH"
    assert len(await operation_rows(factory)) == 1


async def test_each_call_gets_its_own_operation(runtime_api, factory):
    execution = await seed_execution(factory)
    body = submit_body(execution)
    async with httpx.AsyncClient(trust_env=False) as client:
        for call_id in ("call-a", "call-b"):
            response = await client.post(
                runtime_api + "/internal/tool-operations",
                json=body | {"source_tool_call_id": call_id},
                headers=headers(execution.tenant_id, execution.actor_user_id),
            )
            assert response.status_code == 200, response.text
    assert {row["submission_json"]["source_tool_call_id"] for row in await operation_rows(factory)} == {
        "call-a",
        "call-b",
    }


async def test_concurrent_submit_of_one_call_creates_one_operation(runtime_api, factory):
    execution = await seed_execution(factory)
    body = submit_body(execution)
    async with httpx.AsyncClient(trust_env=False) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    runtime_api + "/internal/tool-operations",
                    json=body,
                    headers=headers(execution.tenant_id, execution.actor_user_id),
                )
                for _ in range(2)
            )
        )
    assert {response.status_code for response in responses} == {200}
    ids = {json.loads(response.text)["data"]["operation_id"] for response in responses}
    assert len(ids) == 1 and len(await operation_rows(factory)) == 1


async def test_owner_is_verified_against_the_execution_row(runtime_api, factory):
    execution = await seed_execution(factory)
    async with httpx.AsyncClient(trust_env=False) as client:
        cross_tenant = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers=headers(str(uuid.uuid4()), execution.actor_user_id),
        )
        foreign_actor = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers=headers(execution.tenant_id, uuid.uuid4()),
        )
        anonymous = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers={"X-Internal-Service": TOKEN, "X-Tenant-Id": execution.tenant_id},
        )
        no_identity = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers={"X-Tenant-Id": execution.tenant_id, "X-Actor-User-Id": str(execution.actor_user_id)},
        )
    assert cross_tenant.status_code == 404 and json.loads(cross_tenant.text)["code"] == "COMMON_NOT_FOUND"
    assert foreign_actor.status_code == 403, foreign_actor.text
    assert json.loads(foreign_actor.text)["code"] == "TASK_ACCESS_DENIED"
    assert anonymous.status_code == 403 and json.loads(anonymous.text)["code"] == "TASK_ACCESS_DENIED"
    assert no_identity.status_code == 403 and json.loads(no_identity.text)["code"] == "FORBIDDEN"
    assert await operation_rows(factory) == []


async def test_submit_refuses_cancelled_or_terminal_execution(runtime_api, factory):
    cancelled = await seed_execution(factory, cancel_requested=True)
    finished = await seed_execution(factory, status="COMPLETED")
    async with httpx.AsyncClient(trust_env=False) as client:
        for execution in (cancelled, finished):
            response = await client.post(
                runtime_api + "/internal/tool-operations",
                json=submit_body(execution),
                headers=headers(execution.tenant_id, execution.actor_user_id),
            )
            assert response.status_code == 409, response.text
            assert json.loads(response.text)["code"] == "TASK_OPERATION_CANCELLED"
    assert await operation_rows(factory) == []


async def test_list_is_owner_scoped_and_filters_by_ready(runtime_api, factory):
    execution = await seed_execution(factory)
    other = await seed_execution(factory)
    async with httpx.AsyncClient(trust_env=False) as client:
        for call_id in ("call-a", "call-b"):
            await client.post(
                runtime_api + "/internal/tool-operations",
                json=submit_body(execution, call_id=call_id),
                headers=headers(execution.tenant_id, execution.actor_user_id),
            )
        listing = await client.get(
            runtime_api + "/internal/tool-operations",
            params={"task_execution_id": str(execution.id)},
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
        pending = await client.get(
            runtime_api + "/internal/tool-operations",
            params={"task_execution_id": str(execution.id), "ready": "true"},
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
        unready = await client.get(
            runtime_api + "/internal/tool-operations",
            params={"task_execution_id": str(execution.id), "ready": "false"},
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
        foreign = await client.get(
            runtime_api + "/internal/tool-operations",
            params={"task_execution_id": str(execution.id)},
            headers=headers(other.tenant_id, other.actor_user_id),
        )
    assert listing.status_code == 200, listing.text
    items = json.loads(listing.text)["data"]
    assert items["total"] == 2 and len(items["items"]) == 2
    assert items["page"] == 1 and items["page_size"] == 20
    assert {item["source_tool_call_id"] for item in items["items"]} == {"call-a", "call-b"}
    assert json.loads(pending.text)["data"]["total"] == 0
    assert json.loads(unready.text)["data"]["total"] == 2
    assert foreign.status_code == 404


async def test_list_pages_the_owner_operations(runtime_api, factory):
    execution = await seed_execution(factory)
    async with httpx.AsyncClient(trust_env=False) as client:
        for call_id in ("call-a", "call-b"):
            await client.post(
                runtime_api + "/internal/tool-operations",
                json=submit_body(execution, call_id=call_id),
                headers=headers(execution.tenant_id, execution.actor_user_id),
            )
        first = await client.get(
            runtime_api + "/internal/tool-operations",
            params={"task_execution_id": str(execution.id), "page": 1, "page_size": 1},
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
        second = await client.get(
            runtime_api + "/internal/tool-operations",
            params={"task_execution_id": str(execution.id), "page": 2, "page_size": 1},
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
    first_data = json.loads(first.text)["data"]
    second_data = json.loads(second.text)["data"]
    assert first_data["total"] == second_data["total"] == 2
    assert first_data["page_size"] == 1 and len(first_data["items"]) == 1
    assert second_data["page"] == 2 and len(second_data["items"]) == 1
    assert first_data["items"][0]["operation_id"] != second_data["items"][0]["operation_id"]


async def test_get_by_id_serves_only_task_owned_operations(runtime_api, factory):
    from tests.async_tool_helpers import seed_operation

    execution = await seed_execution(factory)
    run_task, run_operation, _ = await seed_operation(factory)
    async with httpx.AsyncClient(trust_env=False) as client:
        created = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
        operation_id = json.loads(created.text)["data"]["operation_id"]
        fetched = await client.get(
            runtime_api + f"/internal/tool-operations/{operation_id}",
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
        unknown = await client.get(
            runtime_api + f"/internal/tool-operations/{uuid.uuid4()}",
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
        run_owned = await client.get(
            runtime_api + f"/internal/tool-operations/{run_operation.id}",
            headers=headers(run_task.tenant_id, run_task.actor_user_id),
        )
    assert fetched.status_code == 200, fetched.text
    assert json.loads(fetched.text)["data"]["operation_id"] == operation_id
    assert unknown.status_code == 404
    assert run_owned.status_code == 404, run_owned.text


async def test_cancel_before_submit_leaves_a_durable_tombstone(runtime_api, factory):
    execution = await seed_execution(factory)
    tenant, actor = execution.tenant_id, execution.actor_user_id
    cancel = {
        "owner": {"kind": "TASK", "task_execution_id": str(execution.id)},
        "source_tool_call_id": "call-1",
    }
    async with httpx.AsyncClient(trust_env=False) as client:
        first = await client.post(
            runtime_api + "/internal/tool-operations/cancel", json=cancel, headers=headers(tenant, actor)
        )
        second = await client.post(
            runtime_api + "/internal/tool-operations/cancel", json=cancel, headers=headers(tenant, actor)
        )
    assert first.status_code == second.status_code == 200, first.text
    body = json.loads(first.text)["data"]
    assert body["status"] == "CANCELLED" and body["cancel_requested"] is True
    rows = await operation_rows(factory)
    assert len(rows) == 1 and rows[0]["status"] == "CANCELLED" and rows[0]["cancel_requested"] is True
    assert rows[0]["run_id"] is None and rows[0]["owner_task_execution_id"] == execution.id
    assert await control_commands(factory) == ["CANCEL_OPERATION"]
    # 提交晚到：身份补齐但状态保持 CANCELLED，绝不复活、也不生成第二个 operation。
    async with httpx.AsyncClient(trust_env=False) as client:
        submitted = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers=headers(tenant, actor),
        )
    assert submitted.status_code == 200, submitted.text
    data = json.loads(submitted.text)["data"]
    assert data["operation_id"] == body["operation_id"]
    assert data["status"] == "CANCELLED" and data["tool_name"] == "crm.customer.report"
    rows = await operation_rows(factory)
    assert len(rows) == 1
    assert rows[0]["input_hash"] == HASH and rows[0]["submission_json"]["arguments"] == {"customer": "A"}
    assert rows[0]["status"] == "CANCELLED"


async def test_cancel_after_submit_persists_intent_and_dispatches_once(runtime_api, factory):
    execution = await seed_execution(factory)
    tenant, actor = execution.tenant_id, execution.actor_user_id
    cancel = {
        "owner": {"kind": "TASK", "task_execution_id": str(execution.id)},
        "source_tool_call_id": "call-1",
    }
    async with httpx.AsyncClient(trust_env=False) as client:
        created = await client.post(
            runtime_api + "/internal/tool-operations",
            json=submit_body(execution),
            headers=headers(tenant, actor),
        )
        operation_id = json.loads(created.text)["data"]["operation_id"]
        for _ in range(2):
            cancelled = await client.post(
                runtime_api + "/internal/tool-operations/cancel", json=cancel, headers=headers(tenant, actor)
            )
            assert cancelled.status_code == 200, cancelled.text
        fetched = await client.get(
            runtime_api + f"/internal/tool-operations/{operation_id}", headers=headers(tenant, actor)
        )
    assert json.loads(cancelled.text)["data"]["operation_id"] == operation_id
    assert json.loads(fetched.text)["data"]["status"] == "CANCELLED"
    assert await control_commands(factory) == ["CANCEL_OPERATION"]
    assert len(await operation_rows(factory)) == 1


async def test_cancel_does_not_rewrite_a_settled_operation(runtime_api, factory):
    """迟到取消：结果已落库时不写取消标记、也不重写终态事实。"""
    from muad_agent_runtime.application.async_tools.task_operations import submit as submit_operation
    from muad_contracts import TaskToolOperationSubmitRequest

    execution = await seed_execution(factory)
    async with factory() as session, session.begin():
        view = await submit_operation(
            session,
            tenant_id=execution.tenant_id,
            actor_user_id=execution.actor_user_id,
            request=TaskToolOperationSubmitRequest.model_validate(submit_body(execution)),
        )
        await session.execute(
            text(
                "UPDATE runtime.tool_operation SET status = 'COMPLETED', completed_at = now() "
                "WHERE id = :id"
            ),
            {"id": view.operation_id},
        )
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(
            runtime_api + "/internal/tool-operations/cancel",
            json={
                "owner": {"kind": "TASK", "task_execution_id": str(execution.id)},
                "source_tool_call_id": "call-1",
            },
            headers=headers(execution.tenant_id, execution.actor_user_id),
        )
    assert response.status_code == 200, response.text
    data = json.loads(response.text)["data"]
    assert data["status"] == "COMPLETED" and data["cancel_requested"] is False
    assert await control_commands(factory) == []


async def test_cancel_command_reaches_the_worker_with_the_task_owner(factory):
    """控制命令仍走同一条 outbox：TASK 行没有 Run，投递体与 ack 校验按 owner 联合收敛。"""
    from muad_agent_runtime.application.async_tools.control_dispatcher import (
        ControlDispatcher,
        ControlDispatchPolicy,
    )
    from muad_agent_runtime.application.async_tools.task_operations import cancel
    from muad_contracts import TaskToolOperationCancelRequest

    execution = await seed_execution(factory)
    async with factory() as session, session.begin():
        await cancel(
            session,
            tenant_id=execution.tenant_id,
            actor_user_id=execution.actor_user_id,
            request=TaskToolOperationCancelRequest.model_validate(
                {
                    "owner": {"kind": "TASK", "task_execution_id": str(execution.id)},
                    "source_tool_call_id": "call-1",
                }
            ),
        )
    bodies: list[dict[str, Any]] = []
    worker = FastAPI()

    @worker.post("/internal/runtime-operations/{operation_id}/cancel")
    async def _cancel(operation_id: uuid.UUID, request: Request) -> dict[str, Any]:
        bodies.append(await request.json())
        return {
            "code": "0",
            "data": {
                "operation_id": str(operation_id),
                "cancel_recorded": True,
                "task_id": None,
                "task_status": None,
                "cancel_requested": True,
            },
        }

    server = _Server(worker)
    url = server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            dispatcher = ControlDispatcher(
                factory, client, url, service_token=TOKEN, policy=ControlDispatchPolicy()
            )
            assert await dispatcher.run_once(now=datetime.now(UTC)) == 1
    finally:
        server.stop()
    assert bodies == [
        {
            "owner": {"kind": "TASK", "task_execution_id": str(execution.id)},
            "source_tool_call_id": "call-1",
            "actor_user_id": str(execution.actor_user_id),
        }
    ]
    async with factory() as session:
        assert await session.scalar(text("SELECT status FROM runtime.tool_control_outbox")) == "SENT"


async def test_recovery_view_returns_frozen_arguments(runtime_api, factory) -> None:
    execution = await seed_execution(factory)
    call_id = "call-1"
    async with httpx.AsyncClient() as client:
        submitted = await client.post(
            runtime_api + "/internal/tool-operations",
            headers=headers(execution.tenant_id, execution.actor_user_id),
            json={
                "owner": {"task_execution_id": str(execution.id)},
                "source_tool_call_id": call_id,
                "tool_name": "skill::s::run",
                "input_hash": HASH,
                "arguments": {"input": {"q": "A"}, "argv": []},
                "completion_mode": "JOIN",
            },
        )
        assert submitted.status_code == 200, submitted.text
        operation_id = submitted.json()["data"]["operation_id"]

        recovered = await client.get(
            runtime_api + "/internal/tool-operations/recovery",
            headers=headers(execution.tenant_id, execution.actor_user_id),
            params={"task_execution_id": str(execution.id), "call_id": call_id},
        )
        assert recovered.status_code == 200, recovered.text
        data = recovered.json()["data"]
        assert data["operation_id"] == operation_id
        assert data["arguments"] == {"input": {"q": "A"}, "argv": []}
        assert data["input_hash"] == HASH
        assert data["tool_name"] == "skill::s::run"
        assert data["task_id"] is None


async def test_recovery_view_missing_or_foreign_is_404(runtime_api, factory) -> None:
    execution = await seed_execution(factory)
    other = await seed_execution(factory)
    async with httpx.AsyncClient() as client:
        missing = await client.get(
            runtime_api + "/internal/tool-operations/recovery",
            headers=headers(execution.tenant_id, execution.actor_user_id),
            params={"task_execution_id": str(execution.id), "call_id": "missing"},
        )
        assert missing.status_code == 404

        foreign = await client.get(
            runtime_api + "/internal/tool-operations/recovery",
            headers=headers(execution.tenant_id, other.actor_user_id),
            params={"task_execution_id": str(execution.id), "call_id": "call-1"},
        )
        assert foreign.status_code in (403, 404)
