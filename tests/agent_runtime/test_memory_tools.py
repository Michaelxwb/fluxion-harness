"""Production memory registry and executor regressions against isolated PostgreSQL."""

import json
from dataclasses import replace

import pytest
import sqlalchemy as sa
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.memory_tools import memory_write_enabled
from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit
from muad_api import AppError
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_confirmation_support import source, state
from tests.agent_runtime.memory_executor_support import execute, tool_call
from tests.agent_runtime.memory_redesign_write_support import command, context, seed_facts, upsert, writer
from tests.agent_runtime.memory_tool_support import recall, tools

pytestmark = pytest.mark.integration


async def test_production_remember_factory_pauses_after_complete_round_without_saving(
    async_tool_database, monkeypatch
):
    factory, run, identity, submission_id = await source(async_tool_database)
    arguments = {"memory_key": "color", "value": "blue", "category": "PREFERENCE"}
    events, captured = await execute(
        factory,
        run,
        identity,
        submission_id,
        [tool_call("remember", arguments), tool_call("recall", {}, "recall-1")],
        monkeypatch,
    )
    assert len(captured) == 1
    assert events[-1].data["status"] == "WAITING_INPUT"
    current, interrupts, checkpoint, canonical = await state(factory, run)
    assert current.status == "WAITING_INPUT" and current.lease_owner is None
    assert len(interrupts) == 1 and checkpoint.turns == 1 and checkpoint.tool_calls == 2
    results = [event for event in canonical if event.event_type == "TOOL_CALL"]
    assert {event.payload_json["tool_name"] for event in results} == {"remember", "recall"}
    receipt = json.loads(
        next(
            event.payload_json["preview"]
            for event in results
            if event.payload_json["tool_name"] == "remember"
        )
    )
    assert receipt["saved"] is False and receipt["confirmation_required"] is True
    assert interrupts[0].proposal_json["submission_id"] == str(submission_id)
    async with factory() as session:
        audits = list(await session.scalars(sa.select(ToolCallAudit).where(ToolCallAudit.run_id == run.id)))
        assert len(audits) == 2 and all(row.status == "OK" for row in audits)
        assert (await session.execute(sa.text("SELECT count(*) FROM runtime.user_memory"))).scalar_one() == 0


async def test_b04_recall_result_is_not_truncated_by_artifact_externalization(
    async_tool_database, monkeypatch
):
    factory, run, identity, submission_id = await source(async_tool_database)
    ctx = context(tenant=run.tenant_id, user=run.user_id)
    for index in range(8):
        await writer(async_tool_database).execute(ctx, command(upsert(f"fact.{index}", "汉" * 500)))
    policy = MemoryPolicySettings(max_recall_bytes=16384, recall_default_limit=20)
    _, captured = await execute(
        factory, run, identity, submission_id, [tool_call("recall", {})], monkeypatch, policy=policy
    )
    definition = ToolRegistry()
    tools(async_tool_database, replace(ctx, settings=policy)).register(definition)
    assert definition.get("recall").externalizable_result is False
    body = next(item["content"] for item in captured[1]["messages"] if item["role"] == "tool")
    assert 8192 < len(body.encode()) <= 16384
    assert len(json.loads(body)["items"]) == 8 and "artifact" not in json.loads(body)
    async with factory() as session:
        audit = await session.scalar(sa.select(ToolCallAudit).where(ToolCallAudit.run_id == run.id))
    assert audit.status == "OK" and audit.artifact_id is None


async def test_e01_cross_user_arguments_are_rejected_and_reads_are_scoped(async_tool_database):
    mine, other = context(), context()
    await writer(async_tool_database).execute(other, command(upsert(value="private-other")))
    assert (await recall(tools(async_tool_database, mine), {}))["items"] == []
    with pytest.raises(AppError) as caught:
        await tools(async_tool_database, mine).remember(
            {"memory_key": "color", "value": "blue", "category": "PREFERENCE", "user_id": str(other.user_id)},
            call_id="forged",
        )
    assert caught.value.code == "MEMORY_CONFIRMATION_INVALID"


async def test_recall_queries_fresh_rows_instead_of_injection_cache(async_tool_database):
    from tests.agent_runtime.memory_context_support import loader

    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert(value="blue")))
    memory = loader(async_tool_database, ctx)
    await memory.refresh()
    await writer(async_tool_database).execute(ctx, command(upsert(value="green", expected=1)))
    tool_set = tools(async_tool_database, ctx, memory=memory)
    receipt = await recall(tool_set, {"memory_key": "color"})
    assert receipt["items"][0]["version"] == 2 and receipt["items"][0]["content"]["value"] == "green"


async def test_recall_limit_covers_complete_candidates_and_zero(async_tool_database):
    ctx = context()
    await seed_facts(async_tool_database, ctx, 21)
    tool_set = tools(async_tool_database, ctx)
    for limit in (0, 1, 20):
        assert len((await recall(tool_set, {"limit": limit}))["items"]) == limit


@pytest.mark.parametrize(
    "value,expected",
    [(False, False), (True, True), ("false", False), ("off", False), ("TRUE", True), ("yes", True)],
)
async def test_memory_write_switch_explicit_values(async_tool_database, value, expected):
    assert memory_write_enabled({"memory_write": value}) is expected
    assert memory_write_enabled({}, default=False) is False


@pytest.mark.parametrize("value", [1, None, [], "garbage"])
def test_memory_write_switch_rejects_loose_types(value):
    with pytest.raises(ValueError):
        memory_write_enabled({"memory_write": value})
