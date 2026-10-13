"""Genuine USER_MESSAGE/submissions, original TaskState roots and PG event reads."""

from dataclasses import replace
from importlib import import_module
from uuid import uuid4

import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunSubmission
from muad_contracts.memory import MemoryCandidate, MemoryCategory, TaskStateContent, task_memory_key
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_redesign_write_support import (
    command,
    commands,
    context,
    source_context,
    writer,
)


async def run_stream(factory, run, submission, endpoint, monkeypatch):
    from muad_agent_runtime.application import executor as executor_module
    from muad_agent_runtime.application import run_service as service_module
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
    from muad_agent_runtime.application.ports import NullPlatformSettingsClient

    from tests.agent_runtime.memory_context_support import executor_request

    monkeypatch.setattr(service_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(executor_module, "get_session_factory", lambda: factory)

    async def cancelled():
        return False

    request = executor_request(run, endpoint, cancelled)
    resolver = _resolver(request)
    supervisor = ExecutionSupervisor()
    try:
        async with factory() as session:
            from muad_agent_runtime.infrastructure.models.runtime import RunRecord

            fresh = await session.get(RunRecord, run.id)
            current = next(
                event
                for event in await events(factory, run, "USER_MESSAGE")
                if event.submission_id == submission
            )
            service = service_module.RunService(
                session, resolver, "memory-owner", NullPlatformSettingsClient(), supervisor=supervisor
            )
            return [
                event
                async for event in service._stream_run(
                    run=fresh,
                    agent=request.agent,
                    model=request.model,
                    skills=(),
                    mcp_servers=(),
                    mcp_secrets={},
                    history=(),
                    submission_id=submission,
                    resumed=False,
                    execution=request.execution,
                    current_text=current.payload_json["text"],
                )
            ]
    finally:
        await supervisor.close()


def _resolver(request):
    from muad_contracts import ResolveDefinitionResponse

    from tests.agent_runtime.conftest import FakeResolveClient

    return FakeResolveClient(
        ResolveDefinitionResponse(agent=request.agent, model=request.model, skills=[], mcp_servers=[])
    )


async def seed_origin_task(engine, factory, run):
    from muad_contracts.memory import MemorySourceEvidence

    source = (await events(factory, run, "USER_MESSAGE"))[0]
    ctx = replace(
        context(tenant=run.tenant_id, user=run.user_id),
        actor_type="AGENT",
        source_run_id=run.id,
        conversation_id=run.conversation_id,
        evidence=MemorySourceEvidence(
            source_event_id=source.id, source_seq=source.seq, source_scope_revision=0
        ),
    )
    content = TaskStateContent(
        title="原季度报告",
        goal="报告尚未完成",
        constraints=[],
        open_items=["补数据"],
        next_action="核验状态与授权",
        root_conversation_id=run.conversation_id,
        source_conversation_id=run.conversation_id,
    )
    candidate = MemoryCandidate(
        memory_key=task_memory_key(run.conversation_id), category=MemoryCategory.TASK_STATE, content=content
    )
    await writer(engine).execute(
        ctx, command(commands().MemoryMutation(action="UPSERT", candidate=candidate))
    )
    return candidate.memory_key


async def seed_tasks(engine, run, titles=("季度报告", "发布计划")):
    keys = []
    for title in titles:
        root = uuid4()
        ctx = await source_context(
            engine,
            replace(context(tenant=run.tenant_id, user=run.user_id), conversation_id=root),
            actor="AGENT",
        )
        content = TaskStateContent(
            title=title,
            goal=title + "尚未完成",
            constraints=[],
            open_items=["等待用户补充"],
            next_action="核验真实状态及授权",
            root_conversation_id=root,
            source_conversation_id=root,
        )
        candidate = MemoryCandidate(
            memory_key=task_memory_key(root), category=MemoryCategory.TASK_STATE, content=content
        )
        await writer(engine).execute(
            ctx, command(commands().MemoryMutation(action="UPSERT", candidate=candidate))
        )
        keys.append(candidate.memory_key)
    return keys


async def inbound(factory, run, text, *, settings=None):
    service = import_module("muad_agent_runtime.application.memory_resume").MemoryResumeService()
    submission_id = uuid4()
    async with factory.begin() as session:
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
        seq = await service.append_inbound(
            session,
            run,
            submission_id=submission_id,
            text=text,
            payload={},
            settings=settings or MemoryPolicySettings(),
        )
    return submission_id, seq


async def events(factory, run, kind):
    async with factory() as session:
        return list(
            await session.scalars(
                sa.select(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == run.tenant_id,
                    CanonicalEvent.conversation_id == run.conversation_id,
                    CanonicalEvent.event_type == kind,
                )
                .order_by(CanonicalEvent.seq)
            )
        )


async def decision(factory, run, submission_id):
    return await import_module("muad_agent_runtime.application.memory_resume").resume_decision(
        factory, run, submission_id=submission_id
    )
