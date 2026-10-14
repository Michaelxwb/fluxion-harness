"""Canonical real-user selection, job enqueue and HTTP extraction for A → B → C."""

import json

import sqlalchemy as sa
from muad_agent_runtime.application.memory_job_enqueue import enqueue_completed
from muad_agent_runtime.application.memory_jobs import MemoryJobs
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    MemoryConsolidationJob,
    RunRecord,
    UserMemory,
)
from muad_contracts.memory import task_memory_key

from tests.agent_runtime.memory_consolidation_effect_support import execution, pending
from tests.agent_runtime.memory_consolidation_support import model_stack
from tests.agent_runtime.memory_resume_support import inbound


async def step(engine, *, origin=None, completed=False):
    root = origin.conversation_id if origin else None
    response = [""]
    async with model_stack(
        engine,
        tenant=origin.tenant_id if origin else None,
        user=origin.user_id if origin else None,
        output=lambda _: response[0],
    ) as stack:
        factory, run, job, _, client, requests, *_ = stack
        root = root or run.conversation_id
        response[0] = json.dumps(
            {
                "candidates": []
                if completed
                else [
                    {
                        "memory_key": task_memory_key(root),
                        "category": "TASK_STATE",
                        "content": {
                            "title": "季度报告",
                            "goal": "progress from " + str(run.conversation_id),
                            "constraints": [],
                            "open_items": ["pending"],
                            "next_action": "核验当前授权",
                            "root_conversation_id": str(root),
                            "source_conversation_id": str(run.conversation_id),
                        },
                    }
                ],
                "task_completed": completed,
            }
        )
        if origin:
            job = await selected_job(factory, run, job)
        else:
            await pending(factory, job)
        result = await execution(factory, client).execute(await MemoryJobs(factory).claim("lineage-owner"))
        assert result["written"] == 1 and len(requests) == 1
        async with factory() as session:
            rows = list(
                await session.scalars(sa.select(UserMemory).where(UserMemory.tenant_id == run.tenant_id))
            )
            saved = await session.get(MemoryConsolidationJob, job.id)
        assert len(rows) == 1
        return run, rows[0], saved, requests[0][0]


async def selected_job(factory, run, placeholder):
    async with factory.begin() as session:
        row = await session.get(RunRecord, run.id)
        row.status = "RUNNING"
        await session.delete(await session.get(MemoryConsolidationJob, placeholder.id))
    await inbound(factory, run, "继续")
    async with factory.begin() as session:
        row = await session.get(RunRecord, run.id)
        row.status = "COMPLETED"
        through = (await session.get(Conversation, run.conversation_id)).last_seq
        assert await enqueue_completed(session, row, through_seq=through)
    async with factory() as session:
        return await session.scalar(
            sa.select(MemoryConsolidationJob).where(MemoryConsolidationJob.run_id == run.id)
        )
