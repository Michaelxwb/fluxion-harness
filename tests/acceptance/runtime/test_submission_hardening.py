"""S-02/E-01/02/07/16: live services, frozen Console authority and fault HTTP."""

import asyncio
import json
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from muad_agent_runtime.infrastructure.models.async_tools import ToolControlOutbox, ToolOperation
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeOperation
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_common import SharedSettings
from muad_contracts import CompletionMode, ControlCommand, OperationStatus, ResolveDefinitionResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.acceptance.task_schedule.conftest import live_stack as schedule_stack
from tests.acceptance.task_schedule.environment import INTERNAL_TOKEN
from tests.async_tool_submissions import seed_submission

pytestmark = pytest.mark.e2e


@pytest.fixture(name="live_stack", scope="module")
def hardening_stack(tmp_path_factory):
    yield from schedule_stack.__wrapped__(tmp_path_factory)


@pytest.fixture(name="http")
def hardening_http():
    with httpx.Client(timeout=30, trust_env=False) as client:
        yield client


def _wait(check, seconds=45):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError("durable condition did not become true")


def _db(work):
    async def run():
        engine = create_async_engine(SharedSettings().database_url)
        try:
            return await work(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _resolve(stack, client):
    response = client.post(
        stack.console_url + "/internal/runtime/resolve-definition",
        json={"agent_id": str(stack.agent_id), "actor_user_id": str(stack.platform_user_id)},
        headers=stack.service_headers(),
    )
    assert response.status_code == 200, response.text
    return ResolveDefinitionResponse.model_validate(response.json()["data"])


def _run(stack, client, arguments, *, routed=False):
    client.post(
        stack.llm_url + "/script",
        json={
            "tool_name": "execute_skill",
            "tool_arguments": json.dumps(arguments),
            "final_text": "submitted",
        },
    ).raise_for_status()
    channel = {"type": "WECOM", "bot_id": stack.bot_id, "external_conversation_id": str(uuid4())}
    if routed:
        channel["external_user_id"] = "submission-recipient"
    response = client.post(
        stack.runtime_url + "/v1/runs",
        headers=stack.service_headers(),
        json={
            "agent_id": str(stack.agent_id),
            "platform_user_id": str(stack.platform_user_id),
            "channel": channel,
            "message": {"id": str(uuid4()), "type": "text", "text": "execute"},
        },
    )
    assert response.status_code == 200, response.text
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    return UUID(events[0]["run_id"]), events


@pytest.mark.parametrize("routed", [True, False])
def test_s02_detach_completion_and_independent_delivery(live_stack, http, routed):
    http.post(live_stack.channel_url + "/probe/reset").raise_for_status()
    run_id, events = _run(
        live_stack,
        http,
        {"skill_key": live_stack.skill_key, "completion_mode": "DETACH", "input": {"case": str(uuid4())}},
        routed=routed,
    )
    assert any(
        event["type"] == "run.completed" and event["data"]["status"] == "COMPLETED" for event in events
    )

    async def row(factory):
        async with factory() as session:
            return (
                await session.scalars(select(TaskExecution).where(TaskExecution.source_run_id == run_id))
            ).one()

    task = _wait(lambda: _db(row))
    http.post(
        f"{live_stack.runtime_url}/v1/runs/{run_id}/cancel", headers=live_stack.service_headers()
    ).raise_for_status()
    task = _wait(lambda: value if (value := _db(row)).status == "COMPLETED" else None)
    assert not task.cancel_requested and task.max_attempts == 1
    if routed:
        _wait(lambda: _db(row).delivery_status == "SENT")
        deliveries = http.get(live_stack.channel_url + "/probe/deliveries").json()["deliveries"]
        assert sum(item.get("delivery_key") == f"task:{task.id}:final" for item in deliveries) == 1
    else:
        assert task.delivery_mode == "NONE"
        assert http.get(live_stack.channel_url + "/probe/deliveries").json()["deliveries"] == []


@pytest.mark.parametrize("arguments", [{"skill_key": "not-authorized"}, {"skill_key": 42}])
def test_e01_unauthorized_and_invalid_parameters_are_audited(live_stack, http, arguments):
    run_id, _ = _run(live_stack, http, arguments)

    async def counts(factory):
        from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(ToolOperation).where(ToolOperation.run_id == run_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskExecution)
                    .where(TaskExecution.source_run_id == run_id)
                )
                == 0
            )
            audit = (await session.scalars(select(ToolCallAudit).where(ToolCallAudit.run_id == run_id))).one()
            assert audit.status in ("DENY", "ERROR") and audit.prepared_args_hash.startswith("sha256:")

    _db(counts)


@pytest.mark.parametrize("rewrite", [{"skill_key": 42}, {"skill_key": "unauthorized"}])
def test_e01_hook_final_parameters_are_revalidated_and_reauthorized(live_stack, http, rewrite):
    from muad_agent_core.agent import AgentPolicy
    from muad_agent_core.hooks import HookContext, HookEvent, HookPipeline
    from muad_agent_core.tools.pipeline import ToolExecutionPipeline
    from muad_agent_runtime.application.executor import (
        ExecutorRequest,
        ExecutorRunContext,
        ToolCallRecorder,
        _frozen_tool_policy,
    )
    from muad_agent_runtime.application.skill_tools import build_default_skill_cache, build_skill_registry
    from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
    from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit

    resolved = _resolve(live_stack, http)

    async def exercise(factory):
        context, skill, run = await seed_submission(
            factory, resolved=resolved, tenant=live_stack.tenant_id, actor=live_stack.platform_user_id
        )

        async def cancelled():
            return False

        request = ExecutorRequest(
            agent=resolved.agent,
            model=resolved.model,
            input_text="",
            is_cancel_requested=cancelled,
            skills=tuple(resolved.skills),
        )
        recorder = ToolCallRecorder(
            context=ExecutorRunContext(run.tenant_id, run.id, run.conversation_id, run.user_id),
            artifact_writer=None,
            audit_writer=RuntimeAuditWriter(
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                task_id=None,
                session_factory=lambda: factory,
            ),
        )
        hooks = HookPipeline()

        async def edit(event):
            return HookContext(event.event, {**event.payload, "arguments": rewrite})

        hooks.register(HookEvent.PRE_TOOL_USE, edit)
        registry = build_skill_registry(
            cache=build_default_skill_cache(), skills=resolved.skills, policy=AgentPolicy()
        )
        pipeline = ToolExecutionPipeline(
            registry=registry, audit=recorder, hooks=hooks, policy=_frozen_tool_policy(request)
        )
        result = await pipeline.execute(
            pipeline.prepare(
                call_id="hook-deny", tool_name="execute_skill", arguments={"skill_key": skill.key}
            )
        )
        assert result.status in ("FAILED", "POLICY_DENIED")
        from muad_agent_core.model import ModelMessage, ModelRole

        await recorder.finish_round(
            [ModelMessage(role=ModelRole.TOOL, content=result.content, tool_call_id="hook-deny")]
        )
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(ToolOperation).where(ToolOperation.run_id == run.id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskExecution)
                    .where(TaskExecution.source_run_id == run.id)
                )
                == 0
            )
            audit = (await session.scalars(select(ToolCallAudit).where(ToolCallAudit.run_id == run.id))).one()
            assert audit.status in ("DENY", "ERROR") and audit.prepared_args_hash == result.args_hash

    _db(exercise)


async def _fault_submission(stack, client, *, exhaust):
    from muad_agent_runtime.application.async_tools.control_dispatcher import (
        ControlDispatcher,
        ControlDispatchPolicy,
    )
    from muad_agent_runtime.application.async_tools.operations import DurableTaskSubmitter

    resolved = _resolve(stack, client)
    engine = create_async_engine(SharedSettings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context, skill, run = await seed_submission(
        factory, resolved=resolved, tenant=stack.tenant_id, actor=stack.platform_user_id
    )
    faults = {"drop": True}

    async def proxy(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        headers = dict(line.split(": ", 1) for line in head.split("\r\n")[1:] if ": " in line)
        size = int(next(value for key, value in headers.items() if key.lower() == "content-length"))
        body = await reader.readexactly(size)
        path = head.split(" ")[1]
        async with httpx.AsyncClient(trust_env=False) as remote:
            response = await remote.post(stack.worker_url + path, content=body, headers=headers)
        if not faults["drop"] or "cancel" in path:
            payload = response.content
            writer.write(
                (
                    f"HTTP/1.1 {response.status_code} OK\r\nContent-Length: {len(payload)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                + payload
            )
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        async with httpx.AsyncClient(trust_env=False) as remote:
            dispatcher = ControlDispatcher(
                factory,
                remote,
                url,
                service_token=INTERNAL_TOKEN,
                policy=ControlDispatchPolicy(max_attempts=2, retry_base_sec=0.01, retry_cap_sec=0.01),
            )
            submitter = DurableTaskSubmitter(factory, dispatcher)
            from muad_agent_core.agent import AgentPolicy, AgentRunner, AgentRunRequest
            from muad_agent_runtime.application.executor import _model_provider
            from muad_agent_runtime.application.skill_tools import (
                build_default_skill_cache,
                build_skill_registry,
            )

            arguments = {"skill_key": skill.key, "input": {"case": str(uuid4())}}
            client.post(
                stack.llm_url + "/script",
                json={
                    "tool_name": "execute_skill",
                    "tool_arguments": json.dumps(arguments),
                    "final_text": "admission still pending",
                },
            ).raise_for_status()
            provider = await _model_provider(resolved.model, timeout_sec=5)
            registry = build_skill_registry(
                cache=build_default_skill_cache(),
                skills=resolved.skills,
                policy=AgentPolicy(),
                task_client=submitter,
                task_context=context,
            )
            try:
                runner = AgentRunner(provider=provider, registry=registry)
                await runner.run(
                    AgentRunRequest(model_id=resolved.model.model_id, instructions="Run the requested skill")
                )
            finally:
                await provider.aclose()
            requests = client.get(stack.llm_url + "/requests").json()["requests"]
            messages = [
                message
                for request in requests
                for message in request["messages"]
                if message["role"] == "tool"
            ]
            receipt = json.loads(messages[-1]["content"])
            assert receipt["status"] == "SUBMISSION_PENDING" and receipt["task_id"] is None
            faults["drop"] = exhaust
            await dispatcher.run_once(now=datetime.now(UTC) + timedelta(seconds=1))
            async with factory() as session:
                operation = await session.get(ToolOperation, UUID(receipt["operation_id"]))
                tasks = list(
                    await session.scalars(
                        select(TaskExecution).where(TaskExecution.source_operation_id == operation.id)
                    )
                )
                assert len(tasks) == 1
                if exhaust:
                    assert operation.status == OperationStatus.FAILED and operation.error_phase == "SUBMIT"
                    assert tasks[0].status != "FAILED"
                    cancel = (
                        await session.scalars(
                            select(ToolControlOutbox).where(
                                ToolControlOutbox.operation_id == operation.id,
                                ToolControlOutbox.command == ControlCommand.CANCEL_OPERATION,
                            )
                        )
                    ).one()
                    assert cancel.status == "PENDING"
                else:
                    assert operation.task_id == tasks[0].id
            if exhaust:
                await dispatcher.run_once(now=datetime.now(UTC) + timedelta(seconds=2))
                async with factory() as session:
                    tombstone = (
                        await session.scalars(
                            select(RuntimeOperation).where(RuntimeOperation.operation_id == operation.id)
                        )
                    ).one()
                    assert tombstone.cancel_requested
                faults["drop"] = False
                async with factory() as session:
                    response = await remote.post(
                        url + "/internal/tasks",
                        json=operation.submission_json,
                        headers={
                            **stack.service_headers(),
                            "Idempotency-Key": f"runtime-op:{operation.id}:submit",
                        },
                    )
                    assert response.status_code in (200, 409)
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(TaskExecution)
                            .where(TaskExecution.source_operation_id == operation.id)
                        )
                        == 1
                    )
            return operation.id, tasks[0].id, run.id
    finally:
        server.close()
        await server.wait_closed()
        await engine.dispose()


@pytest.mark.parametrize("exhaust", [False, True], ids=["e02-replay", "e16-exhaustion"])
def test_e02_e16_lost_admission_and_cancel_intent(live_stack, http, exhaust):
    # Freeze the live Runtime while exercising a second independent control sender.
    process = live_stack.processes["runtime"]._process
    os.kill(process.pid, signal.SIGSTOP)
    try:
        operation_id, task_id, run_id = asyncio.run(_fault_submission(live_stack, http, exhaust=exhaust))
    finally:
        os.kill(process.pid, signal.SIGCONT)

    async def received(factory):
        from muad_agent_runtime.infrastructure.models.async_tools import ToolResultInbox
        from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent

        async with factory() as session:
            inbox = await session.scalar(
                select(ToolResultInbox).where(ToolResultInbox.operation_id == operation_id)
            )
            if inbox is None:
                return False
            assert inbox.task_id == task_id and inbox.late == exhaust
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CanonicalEvent)
                    .where(CanonicalEvent.run_id == run_id, CanonicalEvent.event_type == "RUN_RESUMED")
                )
                == 0
            )
            return True

    _wait(lambda: _db(received))


@pytest.mark.parametrize("created_first", [False, True])
def test_e07_cancellation_tombstone_precedes_real_http_submission(live_stack, http, created_first):
    from muad_agent_runtime.application.async_tools.operations import reserve_operation

    resolved = _resolve(live_stack, http)

    async def reserve(factory):
        context, skill, _ = await seed_submission(
            factory, resolved=resolved, tenant=live_stack.tenant_id, actor=live_stack.platform_user_id
        )
        operation = await reserve_operation(
            factory,
            context,
            skill=skill,
            input_data={},
            call_id="cancel-first",
            completion_mode=CompletionMode.JOIN,
        )
        async with factory() as session, session.begin():
            from sqlalchemy import update

            await session.execute(
                update(ToolControlOutbox)
                .where(ToolControlOutbox.operation_id == operation.id)
                .values(not_before=datetime.now(UTC) + timedelta(hours=1))
            )
        return operation

    process = live_stack.processes["runtime"]._process
    os.kill(process.pid, signal.SIGSTOP)
    try:
        operation = _db(reserve)
        admitted_task = None
        if created_first:
            admitted = http.post(
                live_stack.worker_url + "/internal/tasks",
                headers={
                    **live_stack.service_headers(),
                    "Idempotency-Key": f"runtime-op:{operation.id}:submit",
                },
                json=operation.submission_json,
            )
            admitted.raise_for_status()
            admitted_task = admitted.json()["data"]["task_id"]
        os.kill(process.pid, signal.SIGCONT)
        response = http.post(
            f"{live_stack.runtime_url}/v1/runs/{operation.run_id}/cancel",
            headers=live_stack.service_headers(),
        )
        response.raise_for_status()
        os.kill(process.pid, signal.SIGSTOP)
        response = http.post(
            f"{live_stack.worker_url}/internal/runtime-operations/{operation.id}/cancel",
            headers={**live_stack.service_headers(), "Idempotency-Key": f"runtime-op:{operation.id}:cancel"},
            json={
                "source_run_id": str(operation.run_id),
                "source_tool_call_id": operation.source_tool_call_id,
                "actor_user_id": str(operation.actor_user_id),
            },
        )
        assert response.status_code == 200 and response.json()["data"]["cancel_recorded"]
        if created_first:
            assert response.json()["data"]["task_id"] == admitted_task

            async def cancelled(factory):
                async with factory() as session:
                    task = await session.get(TaskExecution, UUID(admitted_task))
                    assert task.status in ("COMPLETED", "FAILED", "CANCELLED") or task.cancel_requested

            _db(cancelled)
        response = http.post(
            live_stack.worker_url + "/internal/tasks",
            headers={**live_stack.service_headers(), "Idempotency-Key": f"runtime-op:{operation.id}:submit"},
            json=operation.submission_json,
        )
        if created_first:
            assert response.status_code == 200 and response.json()["data"]["task_id"] == admitted_task
        else:
            assert response.status_code == 409 and response.json()["code"] == "TASK_OPERATION_CANCELLED"
    finally:
        os.kill(process.pid, signal.SIGCONT)


def test_e07_batch_cancel_and_last_fanin_have_one_terminal(live_stack, http):
    from concurrent.futures import ThreadPoolExecutor

    from muad_agent_worker.application.batch_fanin import settle_child
    from muad_agent_worker.infrastructure.models.task import TaskEvent

    from tests.async_tool_helpers import seed_operation

    async def seed(factory):
        parent, _, _ = await seed_operation(factory, task_status="WAITING")
        child, _, _ = await seed_operation(factory, task_status="COMPLETED")
        async with factory() as session, session.begin():
            row = await session.get(TaskExecution, child.id)
            row.parent_id, row.root_id, row.tenant_id = parent.id, parent.id, parent.tenant_id
        return parent, child

    parent, child = _db(seed)

    def cancel():
        response = httpx.post(
            live_stack.worker_url + f"/internal/tasks/{parent.id}/cancel",
            headers={
                "X-Tenant-Id": parent.tenant_id,
                "X-Internal-Service": INTERNAL_TOKEN,
                "X-Actor-User-Id": str(parent.actor_user_id),
            },
        )
        assert response.status_code in (200, 409), response.text

    def fanin():
        async def settle(factory):
            async with factory() as session, session.begin():
                row = await session.get(TaskExecution, child.id)
                await settle_child(session, row, datetime.now(UTC))

        _db(settle)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(cancel), pool.submit(fanin)
        first.result(timeout=15)
        second.result(timeout=15)

    async def verify(factory):
        async with factory() as session:
            row = await session.get(TaskExecution, parent.id)
            assert row.status in ("COMPLETED", "CANCELLED")
            count = await session.scalar(
                select(func.count())
                .select_from(TaskEvent)
                .where(
                    TaskEvent.task_id == parent.id,
                    TaskEvent.event_type.in_(("COMPLETED", "CANCELLED", "FAN_IN")),
                )
            )
            assert count == 1

    _db(verify)
