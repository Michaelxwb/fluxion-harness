"""Live Run/submission, real canonical rounds and a real HTTP model endpoint."""

import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from muad_agent_runtime.application.async_tools.continuation_service import ExecutionIdentity
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunRecord, RunSubmission
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_submissions import seed_submission


async def source(engine, *, deadline_seconds=600, tenant=None, user=None):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx, skill, run = await seed_submission(factory, tenant=tenant, actor=user)
    submission_id = uuid4()
    async with factory.begin() as session:
        row = await session.get(RunRecord, run.id)
        row.lease_owner = "memory-owner"
        row.lease_until = datetime.now(UTC) + timedelta(minutes=10)
        row.execution_epoch = 1
        row.deadline_at = datetime.now(UTC) + timedelta(seconds=deadline_seconds)
        session.add(
            RunSubmission(
                id=submission_id,
                tenant_id=run.tenant_id,
                actor_user_id=run.user_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                idempotency_key=uuid4().hex,
                endpoint="CREATE",
                request_fingerprint="fixture",
            )
        )
        await session.flush()
        await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            submission_id=submission_id,
            event_type="USER_MESSAGE",
            payload={"text": "记住蓝色", "source_scope_revision": 0},
        )
    identity = ExecutionIdentity(run.tenant_id, run.id, "memory-owner", 1)
    return factory, run, identity, submission_id


def service(factory, identity, submission_id):
    return import_module("muad_agent_runtime.application.memory_confirmation").MemoryConfirmationService(
        factory, identity, submission_id=submission_id
    )


def candidate(**overrides):
    from muad_contracts.memory import RememberMemoryInput

    return RememberMemoryInput.model_validate(
        {"memory_key": "color", "category": "PREFERENCE", "value": "blue", **overrides}
    )


async def complete_round(factory, run, *, call_ids=("remember-1",), answer_ids=None):
    async with factory.begin() as session:
        writer = EventWriter(session)
        await writer.append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="ASSISTANT_TURN",
            payload={"tool_calls": [{"id": key, "name": "remember"} for key in call_ids]},
        )
        for key in call_ids if answer_ids is None else answer_ids:
            await writer.append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="TOOL_CALL",
                payload={"tool_call_id": key, "preview": '{"saved":false}'},
            )


async def state(factory, run):
    from muad_agent_runtime.infrastructure.models.async_tools import RunContinuation
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt

    async with factory() as session:
        current = await session.get(RunRecord, run.id)
        interrupts = list(await session.scalars(sa.select(RunInterrupt).where(RunInterrupt.run_id == run.id)))
        saved = await session.scalar(sa.select(RunContinuation).where(RunContinuation.run_id == run.id))
        events = list(
            await session.scalars(
                sa.select(CanonicalEvent).where(CanonicalEvent.run_id == run.id).order_by(CanonicalEvent.seq)
            )
        )
    return current, interrupts, saved, events


@asynccontextmanager
async def model_endpoint(tool_calls, *, on_request=None, fail_first=False):
    app, requests = FastAPI(), []

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        requests.append(await request.json())
        if on_request is not None:
            await on_request(len(requests))
        if fail_first and len(requests) == 1:
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": {"message": "retry probe"}}, status_code=503)
        return {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                    "message": {
                        "role": "assistant",
                        "content": "" if len(requests) == 1 else "should not be called",
                        "tool_calls": tool_calls if len(requests) == 1 else [],
                    },
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}/v1", requests
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()


def runner_round(factory, run, memory, provider):
    from muad_agent_core.agent import AgentRunner
    from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
    from muad_agent_core.tools.pipeline import ToolExecutionPipeline
    from muad_contracts.memory import RememberMemoryInput

    async def remember(arguments, *, call_id):
        receipt = await memory.propose(RememberMemoryInput.model_validate(arguments), call_id=call_id)
        return receipt.model_dump_json()

    async def echo(arguments, *, call_id):
        current, interrupts, saved, _ = await state(factory, run)
        assert current.status == "RUNNING" and interrupts == [] and saved is None
        return "echo finished before pause"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="remember",
            description="candidate",
            effect=ToolEffect.WRITE,
            input_schema=RememberMemoryInput.model_json_schema(),
            handler=remember,
            externalizable_result=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            effect=ToolEffect.READ,
            input_schema={"type": "object"},
            handler=echo,
        )
    )
    recorder = round_recorder(factory, run)
    return AgentRunner(
        provider=provider,
        registry=registry,
        tool_round_results=recorder,
        tool_pipeline=ToolExecutionPipeline(registry=registry, audit=recorder),
        tool_suspension=memory,
    )


def round_recorder(factory, run):
    from muad_agent_runtime.application.executor import ExecutorRunContext, ToolCallRecorder
    from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter

    audit = RuntimeAuditWriter(
        tenant_id=run.tenant_id,
        run_id=run.id,
        task_id=None,
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        session_factory=lambda: factory,
    )
    return ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=run.tenant_id, run_id=run.id, conversation_id=run.conversation_id, user_id=run.user_id
        ),
        audit_writer=audit,
        artifact_writer=None,
    )


def round_callbacks(factory, run):
    from muad_agent_core.model import text_of

    async def assistant(message):
        async with factory.begin() as session:
            await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="ASSISTANT_TURN",
                payload={
                    "text": text_of(message.content),
                    "tool_calls": [{"id": call.id, "name": call.name} for call in message.tool_calls],
                },
            )

    async def completed(call_id, name, status, artifact_id, result_text):
        async with factory.begin() as session:
            await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="TOOL_CALL",
                payload={
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "preview": result_text,
                    "status": status,
                },
            )

    return assistant, completed


async def run_model_round(factory, run, memory, tool_calls, tool_budget):
    from muad_agent_core.agent import AgentPolicy, AgentRunRequest
    from muad_agent_core.model import ModelMessage, ModelRole, OpenAICompatibleProvider

    async with model_endpoint(tool_calls) as (url, requests):
        provider = OpenAICompatibleProvider(base_url=url, model="probe", api_key=None)
        try:
            runner = runner_round(factory, run, memory, provider)
            assistant, completed = round_callbacks(factory, run)
            result = await runner.run(
                AgentRunRequest(
                    model_id="probe",
                    instructions="",
                    messages=(ModelMessage(ModelRole.USER, "记住蓝色"),),
                    policy=AgentPolicy(max_tool_calls=tool_budget),
                ),
                on_assistant_turn=assistant,
                on_tool_completed=completed,
            )
        finally:
            await provider.aclose()
    return result, len(requests)
