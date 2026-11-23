"""Production memory registry and executor regressions against isolated PostgreSQL."""

import json
from dataclasses import replace

import pytest
import sqlalchemy as sa
from muad_agent_core.tools import ToolCall, ToolError, ToolRegistry
from muad_agent_runtime.application.memory_tools import memory_write_enabled
from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_confirmation_support import source, state
from tests.agent_runtime.memory_executor_support import execute, tool_call
from tests.agent_runtime.memory_metric_support import metric
from tests.agent_runtime.memory_redesign_write_support import (
    command,
    context,
    seed_v2_notes,
    upsert,
    writer,
)
from tests.agent_runtime.memory_tool_support import recall, tools

pytestmark = pytest.mark.integration


async def test_production_remember_factory_pauses_after_complete_round_without_saving(
    async_tool_database, monkeypatch
):
    factory, run, identity, submission_id = await source(async_tool_database)
    arguments = {
        "memory_kind": "PREFERENCE",
        "predicate": "output_style",
        "content": {"value": "blue"},
        "title": "输出风格",
    }
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
    await seed_v2_notes(async_tool_database, ctx, 8)
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
    with pytest.raises(ToolError) as caught:
        await tools(async_tool_database, mine).remember(
            {
                "memory_kind": "PREFERENCE",
                "predicate": "output_style",
                "content": {"value": "blue"},
                "title": "输出风格",
                "user_id": str(other.user_id),
            },
            call=ToolCall(call_id="forged", name="remember"),
        )
    assert caught.value.code == "MEMORY_CONFIRMATION_INVALID"


async def test_recall_queries_fresh_rows_instead_of_injection_cache(async_tool_database):
    from tests.agent_runtime.memory_context_support import loader

    ctx = context()
    from muad_agent_runtime.application.memory_service import MemoryService
    from muad_contracts.memory import RememberMemoryInput
    from sqlalchemy.ext.asyncio import async_sessionmaker

    service = MemoryService(async_sessionmaker(async_tool_database, expire_on_commit=False))
    for command_id, value in (("v1", "blue"), ("v2", "green")):
        candidate = RememberMemoryInput.model_validate(
            {
                "memory_kind": "PREFERENCE",
                "predicate": "output_style",
                "content": {"value": value},
                "title": "输出风格",
            }
        ).normalized(taxonomy_version=1)
        await service.apply_candidate(
            ctx, candidate, command_id=command_id, endpoint="test.seed"
        )
        memory = loader(async_tool_database, ctx)
        await memory.refresh()
    tool_set = tools(async_tool_database, ctx, memory=memory)
    receipt = await recall(tool_set, {"query": "green"})
    assert receipt["items"][0]["version"] == 2 and receipt["items"][0]["content"]["value"] == "green"


async def test_recall_limit_covers_complete_candidates_and_zero(async_tool_database):
    ctx = context(settings=MemoryPolicySettings(max_recall_bytes=16384))
    await seed_v2_notes(async_tool_database, ctx, 21, value="fact")
    policy = MemoryPolicySettings(max_recall_bytes=16384, recall_default_limit=20)
    tool_set = tools(async_tool_database, replace(ctx, settings=policy))
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


async def test_recall_byte_budget_can_return_fewer_items_than_requested(async_tool_database):
    budget = 1024
    ctx = context(settings=MemoryPolicySettings(max_recall_bytes=budget))
    await seed_v2_notes(async_tool_database, ctx, 21, value="small")
    result = await tools(async_tool_database, ctx).recall(
        {"limit": 20}, call=ToolCall(call_id="byte-cap", name="recall")
    )
    payload = json.loads(result.content)
    assert 0 < len(payload["items"]) < 20
    assert payload["truncated"] is True
    assert len(result.content.encode("utf-8")) <= budget


async def test_tool_schema_effects_and_write_switch(async_tool_database):
    from muad_agent_core.tools import ToolEffect

    ctx = context()
    registry = ToolRegistry()
    tools(async_tool_database, ctx).register(registry)
    assert registry.get("recall").effect is ToolEffect.READ
    assert registry.get("forget").effect is ToolEffect.WRITE
    assert registry.get("remember").effect is ToolEffect.WRITE

    disabled = replace(ctx, settings=MemoryPolicySettings(write_enabled=False))
    registry = ToolRegistry()
    tools(async_tool_database, disabled).register(registry)
    assert registry.get("recall") is not None and registry.get("forget") is not None
    from muad_agent_core.tools.registry import ToolNotFoundError

    with pytest.raises(ToolNotFoundError):
        registry.get("remember")


async def test_recall_rejects_invalid_arguments(async_tool_database):
    ctx = context()
    tool_set = tools(async_tool_database, ctx)
    with pytest.raises(ToolError) as exc:
        await tool_set.recall({"limit": 99}, call=ToolCall(call_id="limit", name="recall"))
    assert exc.value.code == "MEMORY_LIMIT_INVALID"
    with pytest.raises(ToolError) as exc:
        await tool_set.recall({"memory_key": "color"}, call=ToolCall(call_id="legacy-key", name="recall"))
    assert exc.value.code == "MEMORY_KEY_INVALID"


async def test_recall_read_failure_is_error_not_miss(async_tool_database, monkeypatch):
    from muad_agent_runtime.application.memory_queries import MemoryQueries

    async def boom(self, scope):
        raise __import__("sqlalchemy.exc", fromlist=["SQLAlchemyError"]).SQLAlchemyError("down")

    monkeypatch.setattr(MemoryQueries, "read_snapshot", boom)
    tool_set = tools(async_tool_database, context())
    before = metric("memory_recall_total", mode="SEARCH", outcome="ERROR")
    with pytest.raises(ToolError) as exc:
        await tool_set.recall({"query": "anything"}, call=ToolCall(call_id="read-failure", name="recall"))
    assert exc.value.code == "MEMORY_READ_FAILED"
    assert metric("memory_recall_total", mode="SEARCH", outcome="ERROR") == before + 1


async def test_recall_impossible_budget_fails_explicitly(async_tool_database):
    """空封套都放不进预算：显式预算错误，不伪装成读取失败。"""

    ctx = context(settings=MemoryPolicySettings(max_recall_bytes=1))
    await seed_v2_notes(async_tool_database, ctx, 1, value="x")
    with pytest.raises(ToolError) as exc:
        await tools(async_tool_database, ctx).recall(
            {"limit": 5}, call=ToolCall(call_id="tiny-budget", name="recall")
        )
    assert exc.value.code == "MEMORY_RESULT_BUDGET_EXCEEDED"


async def test_forget_works_when_writes_disabled(async_tool_database):
    ctx = context(settings=MemoryPolicySettings(write_enabled=False))
    await seed_v2_notes(async_tool_database, ctx, 1, value="x", prefix="wdis")
    async with async_tool_database.connect() as connection:
        memory_id = (
            await connection.execute(
                sa.text("SELECT id FROM runtime.user_memory WHERE tenant_id=:t LIMIT 1"),
                {"t": ctx.tenant_id},
            )
        ).scalar_one()
    tool_set = tools(async_tool_database, ctx)
    result = await tool_set.forget(
        {"memory_id": str(memory_id), "expected_version": 1},
        call=ToolCall(call_id="wdis", name="forget"),
    )
    payload = json.loads(result.content)
    assert payload["deleted"] is True and payload["version"] == 2


async def test_remember_schema_enumerates_taxonomy_predicates(async_tool_database):
    from muad_contracts.memory_taxonomy import PREDICATES_BY_KEY

    registry = ToolRegistry()
    tools(async_tool_database, context()).register(registry)
    schema = registry.get("remember").input_schema
    assert schema["properties"]["predicate"]["enum"] == sorted(PREDICATES_BY_KEY)
    assert "content" in schema["properties"]


async def test_remember_rejects_invented_predicate_with_taxonomy_code(async_tool_database):
    """An invented predicate must fail as a taxonomy error the model can retry."""

    tool_set = tools(async_tool_database, context(), confirmation=object())
    with pytest.raises(ToolError) as exc:
        await tool_set.remember(
            {
                "memory_kind": "FACT",
                "predicate": "name",
                "content": {"name": "Bingo"},
                "title": "用户的名字是 Bingo",
            },
            call=ToolCall(call_id="invented-predicate", name="remember"),
        )
    assert exc.value.code == "MEMORY_TAXONOMY_INVALID"
