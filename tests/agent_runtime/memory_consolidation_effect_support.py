"""Real HTTP job execution and actual writer mutations for atomic effect tests."""

import json
from dataclasses import replace
from importlib import import_module

import sqlalchemy as sa
from muad_agent_runtime.application.memory_commands import MemoryWriteContext
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    MemoryConsolidationJob,
    MemoryScope,
    RunRecord,
    UserMemory,
    UserMemoryHistory,
)

from tests.agent_runtime.memory_consolidation_support import provider_factory
from tests.agent_runtime.memory_redesign_write_support import command, upsert


def output(*keys):
    return json.dumps(
        {
            "candidates": [
                {"memory_key": key, "category": "PREFERENCE", "content": {"value": "blue"}} for key in keys
            ],
            "task_completed": False,
        }
    )


async def pending(factory, job, *, revision=None):
    async with factory.begin() as session:
        row = await session.get(MemoryConsolidationJob, job.id)
        row.status, row.attempt, row.lease_until, row.lease_owner = "PENDING", 0, None, None
        if revision is not None:
            row.enqueued_scope_revision = revision


def execution(factory, client):
    invoker = import_module("muad_agent_runtime.application.memory_consolidation").ConsolidationInvoker(
        factory, client, provider_factory=provider_factory
    )
    return import_module("muad_agent_runtime.application.memory_consolidation_effects").ConsolidationWorker(
        factory, invoker
    )


async def counts(factory, run):
    async with factory() as session:
        where = {"memory": UserMemory, "history": UserMemoryHistory, "completed": CanonicalEvent}
        result = {}
        for key, model in where.items():
            query = sa.select(sa.func.count()).select_from(model).where(model.tenant_id == run.tenant_id)
            if key == "completed":
                query = query.where(CanonicalEvent.event_type == "MEMORY_CONSOLIDATED")
            result[key] = await session.scalar(query)
        result["run_status"] = (await session.get(RunRecord, run.id)).status
        return result


def admin_context(run):
    return MemoryWriteContext(
        tenant_id=run.tenant_id, user_id=run.user_id, actor_type="ADMIN", actor_id="real-admin"
    )


async def seed_admin(factory, run, *, key="infer.color"):
    return await MemoryService(factory).execute(admin_context(run), command(upsert(key=key)))


async def seed_author(factory, run, author):
    from muad_contracts.memory import MemorySourceEvidence

    async with factory() as session:
        event = await session.scalar(
            sa.select(CanonicalEvent).where(
                CanonicalEvent.run_id == run.id, CanonicalEvent.event_type == "USER_MESSAGE"
            )
        )
    ctx = replace(
        admin_context(run),
        actor_type=author,
        source_run_id=run.id,
        conversation_id=run.conversation_id,
        actor_id=str(run.user_id),
        confirmation_event_id=event.id if author == "USER" else None,
        evidence=MemorySourceEvidence(
            source_event_id=event.id,
            source_seq=event.seq,
            source_scope_revision=event.payload_json["source_scope_revision"],
        ),
    )
    return await MemoryService(factory).execute(ctx, command(upsert(key="infer.color")))


async def current_revision(factory, run):
    async with factory() as session:
        return (
            await session.scalar(
                sa.select(MemoryScope.revision).where(
                    MemoryScope.tenant_id == run.tenant_id, MemoryScope.user_id == run.user_id
                )
            )
            or 0
        )
