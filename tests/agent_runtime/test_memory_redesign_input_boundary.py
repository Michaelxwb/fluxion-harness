"""B-21 auxiliary: eligible summary/through_seq and whole-batch output validation."""

import json
from importlib import import_module
from uuid import uuid4

import pytest
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent
from muad_contracts.memory import task_memory_key

from tests.agent_runtime.memory_confirmation_support import source
from tests.agent_runtime.memory_consolidation_support import completed_job

pytestmark = pytest.mark.integration


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
