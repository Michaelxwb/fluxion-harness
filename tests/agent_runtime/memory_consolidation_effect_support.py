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


#: 每个 key 用一个稳定 predicate，保证不同候选有不同身份（SINGLE）。
_PREDICATES = ("output_style", "language", "role", "team")


def _candidate(index: int, *, value: str = "blue", title: str = "color") -> dict[str, object]:
    predicate = _PREDICATES[index % len(_PREDICATES)]
    return {
        "memory_kind": "PREFERENCE" if predicate in ("output_style", "language") else "FACT",
        "predicate": predicate,
        "scope_state": "ABSENT",
        "content": {"value": value},
        "title": title,
        "summary": "",
        "tags": [],
    }


def output(*keys, value="blue"):
    return json.dumps(
        {
            "candidates": [_candidate(index, value=value, title=key) for index, key in enumerate(keys)],
            "task_completed": False,
        }
    )


def fact_candidate(value: str = "blue"):
    from muad_contracts.memory import RememberMemoryInputV2

    return RememberMemoryInputV2.model_validate(_candidate(0, value=value)).normalized(
        taxonomy_version=1
    )


def fact_key(value: str = "blue") -> str:
    return fact_key_at(0, value)


def fact_key_at(index: int, value: str = "blue") -> str:
    from muad_agent_runtime.application.memory_identity import memory_key as build_memory_key

    from muad_contracts.memory import RememberMemoryInputV2

    candidate = RememberMemoryInputV2.model_validate(_candidate(index, value=value)).normalized(
        taxonomy_version=1
    )
    return build_memory_key(candidate)


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


async def seed_admin(factory, run, *, value="blue"):
    from uuid import uuid4

    return await MemoryService(factory).apply_candidate(
        admin_context(run),
        fact_candidate(value),
        command_id=f"seed-{uuid4().hex}",
        endpoint="test.seed",
    )


async def seed_author(factory, run, author, *, value="blue"):
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
    from uuid import uuid4

    return await MemoryService(factory).apply_candidate(
        ctx, fact_candidate(value), command_id=f"seed-{uuid4().hex}", endpoint="test.seed"
    )


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
