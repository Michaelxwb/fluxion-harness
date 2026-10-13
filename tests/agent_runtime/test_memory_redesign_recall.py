"""B-06: real PG, complete recall receipts, strict model input and search metrics."""

import json
from dataclasses import replace

import pytest
import sqlalchemy as sa
from muad_agent_core.memory.render import render_recall
from muad_agent_runtime.application.memory_queries import MemoryReadScope
from muad_api import AppError, render_metrics
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_redesign_write_support import command, context, seed_facts, upsert, writer
from tests.agent_runtime.memory_tool_support import recall, tools
from tests.agent_runtime.test_memory_redesign_cache import queries

pytestmark = pytest.mark.integration


def metric(name, **labels):
    text = render_metrics()
    return sum(
        float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line.startswith(name + "{") and all(f'{key}="{value}"' in line for key, value in labels.items())
    )


async def test_b06_memory_redesign_search_miss_list_and_metadata(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert(value="蓝色")))
    await seed_facts(async_tool_database, ctx, 1, author="AGENT", prefix="inferred")
    await seed_facts(async_tool_database, ctx, 1, expired=True, prefix="expired")
    before = metric("memory_recall_hit_total", hit="false")
    tool_set = tools(async_tool_database, ctx)
    assert (await recall(tool_set, {"query": "unrelated_zebra"}))["items"] == []
    assert metric("memory_recall_hit_total", hit="false") == before + 1
    listed = await recall(tool_set, {"query": "  "})
    assert {row["memory_key"] for row in listed["items"]} == {"color", "inferred.1"}
    assert metric("memory_recall_hit_total", hit="false") == before + 1
    admin = next(row for row in listed["items"] if row["author"] == "ADMIN")
    inferred = next(row for row in listed["items"] if row["author"] == "AGENT")
    assert admin["version"] == 1 and admin["expires_at"] is None
    assert inferred["expires_at"] and inferred["updated_at"] and inferred["explicit_request"] is False
    assert listed["notice"] and listed["truncated"] is False
    assert (await recall(tool_set, {"category": "EXPLICIT"}))["items"] == []
    assert (await recall(tool_set, {"memory_key": "color"}))["items"] == [admin]


async def test_b06_memory_redesign_final_json_exact_budget_and_no_first_item_overflow(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert(value="长" * 400), upsert("short", "短")))
    snapshot = await queries(async_tool_database).read_snapshot(
        MemoryReadScope(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    )
    short = next(row for row in snapshot.records if row.memory_key == "short")
    long = next(row for row in snapshot.records if row.memory_key == "color")
    budget = len(render_recall([long, short], limit=10, max_bytes=500).text.encode())
    for amount, count in ((budget, 1), (budget - 1, 0)):
        bounded = replace(ctx, settings=MemoryPolicySettings(max_recall_bytes=amount))
        payload = await tools(async_tool_database, bounded).recall({}, call_id="bounded")
        assert len(payload.encode()) <= amount
        document = json.loads(payload)
        assert len(document["items"]) == count and document["truncated"] is True
        if count:
            assert document["items"][0]["content"]["value"] == "短"


async def test_b06_memory_redesign_zero_limit_and_fixed_envelope_error(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    before = metric("memory_recall_hit_total", hit="false")
    assert (await recall(tools(async_tool_database, ctx), {"query": "unknown", "limit": 0}))["items"] == []
    assert metric("memory_recall_hit_total", hit="false") == before
    tiny = replace(ctx, settings=MemoryPolicySettings(max_recall_bytes=1))
    with pytest.raises(AppError) as caught:
        await recall(tools(async_tool_database, tiny), {})
    assert caught.value.code == "MEMORY_RESULT_BUDGET_EXCEEDED"


async def test_b06_memory_redesign_empty_scope_has_no_search_miss(async_tool_database):
    before = metric("memory_recall_hit_total", hit="false")
    assert (await recall(tools(async_tool_database, context()), {"query": "blue"}))["items"] == []
    assert metric("memory_recall_hit_total", hit="false") == before


@pytest.mark.parametrize(
    "arguments,code",
    [
        ({"query": 1}, "MEMORY_QUERY_INVALID"),
        ({"query": "x" * 513}, "MEMORY_QUERY_INVALID"),
        ({"category": "wrong"}, "MEMORY_CATEGORY_NOT_ALLOWED"),
        ({"limit": True}, "MEMORY_LIMIT_INVALID"),
        ({"limit": 21}, "MEMORY_LIMIT_INVALID"),
        ({"limit": -1}, "MEMORY_LIMIT_INVALID"),
        ({"prefix": "color"}, "MEMORY_QUERY_INVALID"),
        ({"tenant_id": "other"}, "MEMORY_QUERY_INVALID"),
    ],
)
async def test_b06_memory_redesign_rejects_invalid_inputs(async_tool_database, arguments, code):
    with pytest.raises(AppError) as caught:
        await recall(tools(async_tool_database, context()), arguments)
    assert caught.value.code == code


async def test_b06_memory_redesign_read_failure_is_error_and_never_miss(async_tool_database, caplog):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    tool_set = tools(async_tool_database, ctx)
    before = metric("memory_recall_hit_total", hit="false")
    errors = metric("memory_recall_total", mode="SEARCH", outcome="ERROR")
    async with async_tool_database.begin() as connection:
        await connection.execute(sa.text("ALTER TABLE runtime.user_memory RENAME TO unavailable_memory"))
    with pytest.raises(AppError) as caught:
        await recall(tool_set, {"query": "blue"})
    assert caught.value.code == "MEMORY_READ_FAILED"
    assert metric("memory_recall_hit_total", hit="false") == before
    assert metric("memory_recall_total", mode="SEARCH", outcome="ERROR") == errors + 1
    assert "memory_recall_failed" in caplog.text and "blue" not in caplog.text


async def test_memory_tool_schema_effects_and_write_switch(async_tool_database):
    from muad_agent_core.tools import ToolConcurrency, ToolEffect, ToolRegistry

    ctx = context()
    registry = ToolRegistry()
    tools(async_tool_database, ctx).register(registry)
    remember = registry.get("remember")
    schema = remember.input_schema
    assert set(schema["properties"]) == {"memory_key", "value", "category", "source_quote"}
    assert set(schema["required"]) == {"memory_key", "value", "category"}
    assert schema["additionalProperties"] is False
    assert registry.get("forget").effect is ToolEffect.WRITE
    assert registry.get("forget").concurrency is ToolConcurrency.SERIAL
    assert registry.get("recall").externalizable_result is False
    disabled = ToolRegistry()
    tools(async_tool_database, replace(ctx, settings=MemoryPolicySettings(write_enabled=False))).register(
        disabled
    )
    assert {item.name for item in disabled.list()} == {"recall", "forget"}


async def test_production_remember_is_only_a_proposal_and_cannot_set_source(async_tool_database):
    from tests.agent_runtime.memory_confirmation_support import candidate, service, source, state

    factory, run, identity, submission_id = await source(async_tool_database)
    ctx = context(tenant=run.tenant_id, user=run.user_id)
    confirmation = service(factory, identity, submission_id)
    tool_set = tools(async_tool_database, ctx, confirmation=confirmation)
    for extra in ({"author": "USER"}, {"source_type": "USER_EXPLICIT"}, {"explicit_request": True}):
        with pytest.raises(AppError) as caught:
            await tool_set.remember({**candidate().model_dump(mode="json"), **extra}, call_id="forged")
        assert caught.value.code == "MEMORY_CONFIRMATION_INVALID"
    receipt = json.loads(await tool_set.remember(candidate().model_dump(mode="json"), call_id="remember-1"))
    assert receipt["saved"] is False and receipt["confirmation_required"] is True
    assert str(confirmation.suspend_request.interrupt_id) == receipt["proposal_id"]
    current, interrupts, checkpoint, _ = await state(factory, run)
    assert current.status == "RUNNING" and interrupts == [] and checkpoint is None
    async with async_tool_database.connect() as connection:
        assert (
            await connection.execute(sa.text("SELECT count(*) FROM runtime.user_memory"))
        ).scalar_one() == 0
    with pytest.raises(AppError) as caught:
        await tool_set.remember(candidate().model_dump(mode="json"), call_id="remember-2")
    assert caught.value.code == "MEMORY_CONFIRMATION_PENDING"


async def test_forget_version_conflict_unknown_key_and_writes_disabled(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    ctx = replace(ctx, settings=MemoryPolicySettings(write_enabled=False))
    tool_set = tools(async_tool_database, ctx)
    for key, version, code in (("unknown", 1, "MEMORY_NOT_FOUND"), ("color", 2, "MEMORY_VERSION_CONFLICT")):
        with pytest.raises(AppError) as caught:
            await tool_set.forget({"memory_key": key, "expected_version": version}, call_id=key)
        assert caught.value.code == code
    receipt = json.loads(
        await tool_set.forget({"memory_key": "color", "expected_version": 1}, call_id="delete")
    )
    assert receipt["deleted"] is True and receipt["version"] == 2
    assert receipt["forget_epoch"] == 1 and receipt["memory_key"] == "color"
