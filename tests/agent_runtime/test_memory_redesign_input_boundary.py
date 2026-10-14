"""B-21 auxiliary: eligible summary/through_seq and whole-batch output validation."""

import json
from importlib import import_module
from uuid import uuid4

import pytest
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent
from muad_contracts.memory import task_memory_key

from tests.agent_runtime.memory_confirmation_support import source
from tests.agent_runtime.memory_consolidation_support import completed_job


@pytest.mark.parametrize("change_scope", [False, True])
async def test_b21_memory_redesign_retry_preserves_original_hash_and_revision_or_becomes_stale(
    async_tool_database, change_scope
):
    import sqlalchemy as sa
    from muad_agent_runtime.application.memory_jobs import MemoryJobs
    from muad_agent_runtime.application.run_events import EventWriter
    from muad_agent_runtime.infrastructure.models.runtime import MemoryConsolidationJob

    from tests.agent_runtime.memory_consolidation_effect_support import execution, pending, seed_admin
    from tests.agent_runtime.memory_consolidation_support import model_stack

    async with model_stack(async_tool_database) as stack:
        factory, run, job, snapshot, client, requests, *_ = stack
        await pending(factory, job)
        owners, worker = MemoryJobs(factory), execution(factory, client)
        first = await owners.claim("first")
        async with worker.invoker.open(job, snapshot) as invocation:
            payload = await owners.freeze_input(first, credential_canaries=invocation.canaries)
            await invocation.generate(payload)
        async with factory() as session:
            before_hash = (await session.get(MemoryConsolidationJob, job.id)).input_hash
        await worker.fail(first, reason="FAILED")
        async with factory.begin() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            row.next_attempt_at = await session.scalar(sa.select(sa.func.now()))
            await EventWriter(session).append(
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                event_type="CONTEXT_SUMMARY",
                payload={"covers_up_to_seq": 1, "summary": {"goal": "R2 must never enter R1"}},
            )
        if change_scope:
            await seed_admin(factory, run)
        result = await worker.execute(await owners.claim("second"))
        assert result["reason"] == ("STALE" if change_scope else "NO_CANDIDATES")
        assert len(requests) == (1 if change_scope else 2)
        if not change_scope:
            assert requests[0][0]["messages"][1]["content"] == requests[1][0]["messages"][1]["content"]
        async with factory() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            assert row.input_hash == before_hash and row.input_json == payload
            assert row.input_scope_revision == 0 and row.through_seq == 1


pytestmark = pytest.mark.integration


async def test_b21_memory_redesign_lineage_updates_and_retires_original_root(async_tool_database):
    from tests.agent_runtime.memory_consolidation_lineage_support import step

    origin, original, _, _ = await step(async_tool_database)
    second, updated, job_b, request_b = await step(async_tool_database, origin=origin)
    third, retired, job_c, request_c = await step(async_tool_database, origin=origin, completed=True)
    assert original.id == updated.id == retired.id
    assert [original.version, updated.version, retired.version] == [1, 2, 3]
    assert retired.memory_key == task_memory_key(origin.conversation_id)
    assert updated.content_json["root_conversation_id"] == str(origin.conversation_id)
    assert updated.content_json["source_conversation_id"] == str(second.conversation_id)
    assert retired.is_deleted and retired.retirement_reason == "TASK_COMPLETE"
    assert job_b.resume_ref_json["expected_version"] == 1 and job_c.resume_ref_json["expected_version"] == 2
    assert job_c.resume_ref_json["root_conversation_id"] == str(origin.conversation_id)
    assert str(second.conversation_id) in request_c["messages"][1]["content"]
    assert second.conversation_id != third.conversation_id != origin.conversation_id


async def test_local_b21_input_builder_never_reads_later_summary_or_tool_result(async_tool_database):
    module = import_module("muad_agent_runtime.application.memory_consolidation_input")
    factory, run, _, _ = await source(async_tool_database)
    job, _ = await completed_job(factory, run)
    job.through_seq = 4
    async with factory.begin() as session:
        seed_summaries(session, run)
    async with factory() as session:
        built = await module.build_input(session, job, credential_canaries=())
    text = json.dumps(built.model_dump(mode="json"))
    assert built.summary_seq == 2 and built.covers_up_to_seq == 1
    assert "R1 only" in text and "R1 assistant" in text
    assert "R2 must not appear" not in text and "full tool result excluded" not in text


def seed_summaries(session, run):
    session.add_all(
        [
            CanonicalEvent(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                seq=2,
                event_type="CONTEXT_SUMMARY",
                payload_json=summary_payload("R1 only", 1),
            ),
            CanonicalEvent(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                seq=3,
                event_type="ASSISTANT_MESSAGE",
                payload_json={"text": "R1 assistant"},
            ),
            CanonicalEvent(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                seq=4,
                event_type="TOOL_CALL",
                payload_json={"preview": "full tool result excluded"},
            ),
            CanonicalEvent(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                seq=5,
                event_type="CONTEXT_SUMMARY",
                payload_json=summary_payload("R2 must not appear", 4),
            ),
        ]
    )


def summary_payload(goal, covered):
    return {
        "covers_up_to_seq": covered,
        "summary": {"user_goal": goal, "constraints": [], "progress": [], "open_items": [], "artifacts": []},
    }


@pytest.mark.parametrize("covered", [3, -1, "1", True])
async def test_local_b21_ineligible_summary_cover_does_not_remove_source_messages(
    async_tool_database, covered
):
    module = import_module("muad_agent_runtime.application.memory_consolidation_input")
    factory, run, _, _ = await source(async_tool_database)
    job, _ = await completed_job(factory, run)
    job.through_seq = 2
    async with factory.begin() as session:
        session.add(
            CanonicalEvent(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                seq=2,
                event_type="CONTEXT_SUMMARY",
                payload_json=summary_payload("invalid cover", covered),
            )
        )
    async with factory() as session:
        built = await module.build_input(session, job, credential_canaries=())
    assert built.summary_seq is None and built.covers_up_to_seq == 0
    assert [message.text for message in built.messages] == ["记住蓝色"]


@pytest.mark.parametrize("invalid", ["duplicate", "root", "author", "secret", "category"])
def test_consolidation_output_rejects_entire_batch_for_one_invalid_candidate(invalid):
    module = import_module("muad_agent_runtime.application.memory_consolidation_output")
    root, conversation = uuid4(), uuid4()
    candidate = {"memory_key": "language", "category": "PREFERENCE", "content": {"value": "中文"}}
    bad = {**candidate, "memory_key": "other"}
    if invalid == "duplicate":
        bad = candidate
    elif invalid == "author":
        bad["author"] = "USER"
    elif invalid == "category":
        bad["category"] = "OTHER"
    elif invalid == "secret":
        bad["content"] = {"value": "sk-abcdefghijklmnopqrstuvwxyz0123456789"}
    else:
        bad = {
            "memory_key": task_memory_key(root),
            "category": "TASK_STATE",
            "content": {
                "title": "task",
                "goal": "goal",
                "constraints": [],
                "open_items": [],
                "next_action": "",
                "root_conversation_id": str(root),
                "source_conversation_id": str(conversation),
            },
        }
    with pytest.raises(module.ConsolidationRejected):
        module.parse_output(
            json.dumps({"candidates": [candidate, bad], "task_completed": False}),
            finish_reason="stop",
            conversation_id=conversation,
            root_conversation_id=conversation,
            max_candidates=8,
            credential_canaries=(),
        )
