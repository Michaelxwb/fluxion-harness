"""B-08/B-09 and B-07 auxiliary: real PostgreSQL plus actual model HTTP bodies."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_core.memory.render import render_memory_block
from muad_agent_core.model import ModelMessage, ModelRole
from muad_api import render_metrics
from muad_contracts.memory import (
    ForgetMemoryInput,
    MemoryCandidate,
    MemoryCategory,
)
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_context_support import executor_request, loader, memory_text, request_round
from tests.agent_runtime.memory_redesign_write_support import (
    command,
    commands,
    context,
    install_history_failure,
    seed_facts,
    source_context,
    upsert,
    writer,
)
from tests.agent_runtime.test_memory_redesign_cache import queries, query_counts

pytestmark = pytest.mark.integration


async def test_b08_memory_redesign_oldest_of_200_participates_and_drops_are_complete(async_tool_database):
    ctx = context()
    await seed_facts(async_tool_database, ctx, 200)
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE runtime.user_memory SET category='EXPLICIT',content_json=jsonb_build_object('value',"
                " CASE WHEN memory_key='seed.1' THEN 'rare_target' ELSE 'unrelated' END),"
                "update_time=statement_timestamp()-(CASE WHEN memory_key='seed.1'"
                " THEN interval '1 day' ELSE interval '0' END)"
                " WHERE tenant_id=:tenant"
            ),
            {"tenant": ctx.tenant_id},
        )
    memory = loader(async_tool_database, ctx, query="rare_target")
    _, captured = await request_round(async_tool_database, ctx, memory)
    text = memory_text(captured[0])
    assert '"memory_key":"seed.1"' in text and text.count('"memory_key"') == 1
    assert len(memory.component.references) == 1 and memory.component.block.dropped_count == 0
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text("UPDATE runtime.user_memory SET category='PREFERENCE' WHERE tenant_id=:tenant"),
            {"tenant": ctx.tenant_id},
        )
        await connection.execute(
            sa.text("UPDATE runtime.memory_scope SET revision=revision+1 WHERE tenant_id=:tenant"),
            {"tenant": ctx.tenant_id},
        )
    # Raw fixtures do not create a scope; explicit invalidation still tests all 200 preference rows.
    memory.invalidate()
    before = _count_drops()
    memory = loader(async_tool_database, ctx, policy=MemoryPolicySettings(max_injected_memories=1))
    _, captured = await request_round(async_tool_database, ctx, memory)
    assert memory_text(captured[0]).count('"memory_key"') == 1
    assert memory.component.block.dropped_count == 199 and memory.component.block.dropped_bytes == 0
    assert _count_drops() - before == 199


def _count_drops():
    line = next(
        (
            line
            for line in render_metrics().splitlines()
            if line.startswith('memory_inject_dropped_total{reason="count"}')
        ),
        "0 0",
    )
    return float(line.rsplit(" ", 1)[1])


@pytest.mark.parametrize("difference", [0, -1])
async def test_b09_memory_redesign_final_http_block_has_exact_utf8_boundary(async_tool_database, difference):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert(value="请用中文")))
    snapshot = await queries(async_tool_database).read_snapshot(loader(async_tool_database, ctx).cache.scope)
    expected = render_memory_block(snapshot.records, max_records=10, max_bytes=16384).text
    size = len(expected.encode("utf-8"))
    memory = loader(
        async_tool_database, ctx, policy=MemoryPolicySettings(max_injected_bytes=size + difference)
    )
    _, captured = await request_round(async_tool_database, ctx, memory)
    assert memory_text(captured[0]) == (expected if difference == 0 else None)
    assert memory.component.block.dropped_bytes == (0 if difference == 0 else 1)


@pytest.mark.parametrize(("items", "budget"), [(0, 16384), (10, 0)])
async def test_b09_memory_redesign_zero_budget_never_queries_or_inserts_placeholder(
    async_tool_database, items, budget
):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    memory = loader(
        async_tool_database,
        ctx,
        policy=MemoryPolicySettings(max_injected_memories=items, max_injected_bytes=budget),
    )
    with query_counts(async_tool_database) as counts:
        _, captured = await request_round(async_tool_database, ctx, memory)
    assert counts == {"full": 0, "head": 0} and memory_text(captured[0]) is None


async def test_b09_memory_redesign_long_entry_skips_then_short_entry_fits(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(
        ctx, command(upsert("a.long", "甲" * 512), upsert("z.short", "好"))
    )
    reader = loader(async_tool_database, ctx)
    snapshot = await queries(async_tool_database).read_snapshot(reader.cache.scope)
    short = next(item for item in snapshot.records if item.memory_key == "z.short")
    budget = len(render_memory_block([short], max_records=10, max_bytes=16384).text.encode("utf-8"))
    memory = loader(async_tool_database, ctx, policy=MemoryPolicySettings(max_injected_bytes=budget))
    _, captured = await request_round(async_tool_database, ctx, memory)
    text = memory_text(captured[0])
    assert '"memory_key":"z.short"' in text and '"memory_key":"a.long"' not in text
    assert len(text.encode("utf-8")) == budget and memory.component.block.dropped_bytes == 1


@pytest.mark.parametrize("failed", [False, True])
async def test_b07_aux_memory_redesign_forget_removes_next_request_even_when_write_fails(
    async_tool_database, failed
):
    ctx = context()
    service = writer(async_tool_database)
    await service.execute(ctx, command(upsert(value="old_memory_value")))
    if failed:
        await install_history_failure(async_tool_database)
    memory = loader(async_tool_database, ctx)

    async def forget(arguments, *, call_id):
        outcome = await memory.forget(
            service, ctx, ForgetMemoryInput(memory_key="color", expected_version=1), command_id=call_id
        )
        return json.dumps({"deleted": outcome.response.changed_count == 1})

    _, captured = await request_round(
        async_tool_database,
        ctx,
        memory,
        handler=forget,
        extra_history=(
            ModelMessage(ModelRole.SYSTEM, "another system prompt"),
            ModelMessage(ModelRole.SYSTEM, "summary marker"),
        ),
    )
    assert "old_memory_value" in memory_text(captured[0]) and memory_text(captured[1]) is None
    for body in captured:
        assert [item["content"] for item in body["messages"] if item["role"] == "system"][:1] == [
            body["messages"][0]["content"]
        ]
        assert any(item["content"] == "another system prompt" for item in body["messages"])
        assert any(item["content"] == "summary marker" for item in body["messages"])
    tool = next(item for item in captured[1]["messages"] if item["role"] == "tool")
    assert ('"deleted": true' in tool["content"]) is not failed
    if failed:
        assert json.loads(tool["content"])["error"]["code"] == "COMMON_INTERNAL_ERROR"
        assert await memory.refresh() is None
    async with async_tool_database.connect() as connection:
        assert (
            await connection.execute(
                sa.text("SELECT is_deleted FROM runtime.user_memory WHERE tenant_id=:t"), {"t": ctx.tenant_id}
            )
        ).scalar_one() is not failed


async def test_b08_memory_redesign_history_does_not_duplicate_first_read_and_warm_request_uses_head(
    async_tool_database,
):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    memory = loader(async_tool_database, ctx)

    async def noop(arguments, *, call_id):
        return "noop"

    with query_counts(async_tool_database) as counts:
        _, captured = await request_round(async_tool_database, ctx, memory, handler=noop)
    assert counts == {"full": 1, "head": 1}
    assert memory_text(captured[0]) == memory_text(captured[1])
    assert len(memory.component.references) == 1


async def test_b08_memory_redesign_read_failure_clears_component_then_recovers_from_current_facts(
    async_tool_database,
):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    memory = loader(async_tool_database, ctx)
    assert await memory.refresh() is not None
    async with async_tool_database.begin() as connection:
        await connection.execute(sa.text("ALTER TABLE runtime.memory_scope RENAME TO hidden_scope"))
    _, captured = await request_round(async_tool_database, ctx, memory)
    assert memory_text(captured[0]) is None and memory.component is None
    async with async_tool_database.begin() as connection:
        await connection.execute(sa.text("ALTER TABLE runtime.hidden_scope RENAME TO memory_scope"))
    assert await memory.refresh() is not None


async def test_b09_memory_redesign_trusted_task_reference_is_bounded_in_actual_request(async_tool_database):
    from muad_agent_core.memory.resume import resume_candidate_hash
    from muad_contracts.memory import MemoryResumeRef, TaskStateContent, task_memory_key

    ctx = await source_context(async_tool_database, context(), actor="AGENT")
    root = uuid4()
    content = TaskStateContent(
        title="季度报告",
        goal="甲" * 200,
        constraints=[],
        open_items=["补数据"],
        next_action="乙" * 200,
        root_conversation_id=root,
        source_conversation_id=root,
    )
    candidate = MemoryCandidate(
        memory_key=task_memory_key(root), category=MemoryCategory.TASK_STATE, content=content
    )
    await writer(async_tool_database).execute(
        replace(ctx, actor_type="AGENT"),
        command(commands().MemoryMutation(action="UPSERT", candidate=candidate)),
    )
    row = (
        await queries(async_tool_database).read_snapshot(
            __import__(
                "muad_agent_runtime.application.memory_queries", fromlist=["MemoryReadScope"]
            ).MemoryReadScope(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        )
    ).records[0]
    ref = MemoryResumeRef(
        memory_key=task_memory_key(root),
        expected_version=1,
        root_conversation_id=root,
        options_event_id=uuid4(),
        resolution_event_id=uuid4(),
        source_user_event_id=ctx.evidence.source_event_id,
        candidate_hash=resume_candidate_hash(row),
    )
    memory = loader(
        async_tool_database,
        ctx,
        policy=MemoryPolicySettings(max_injected_bytes=500),
        task_state_ref=ref,
    )
    _, captured = await request_round(async_tool_database, ctx, memory)
    text = memory_text(captured[0])
    assert len(text.encode("utf-8")) <= 500 and '"omitted":true' in text and task_memory_key(root) in text
    assert memory.component.references[0].omitted is True
    unselected = loader(async_tool_database, ctx)
    _, captured = await request_round(async_tool_database, ctx, unselected)
    assert memory_text(captured[0]) is None


async def test_b08_memory_redesign_default_executor_registers_loader_with_frozen_run_scope(
    async_tool_database,
    monkeypatch,
):
    from muad_agent_runtime.application import executor as execution

    from tests.agent_runtime.memory_confirmation_support import model_endpoint, source

    factory, run, identity, submission = await source(async_tool_database)
    ctx = context(tenant=run.tenant_id, user=run.user_id)
    await writer(async_tool_database).execute(
        ctx, command(upsert(value="首轮中文偏好"), upsert("second", "other"))
    )
    # Only the connection factory is injected; production Builder/Cache/Executor/audit use actual PG.
    monkeypatch.setattr(execution, "get_session_factory", lambda: factory)

    async def is_cancelled():
        from muad_agent_runtime.infrastructure.models.runtime import RunRecord

        async with factory() as session:
            current = await session.get(RunRecord, run.id)
            return current.cancel_requested

    async with model_endpoint([]) as (url, captured):
        request = executor_request(run, url, is_cancelled)
        with query_counts(async_tool_database) as counts:
            executor = await execution.default_executor_factory(request)
            events = [event async for event in executor.run()]
    assert len(captured) == 1 and "首轮中文偏好" in memory_text(captured[0])
    assert memory_text(captured[0]).count('"memory_key"') == 1
    assert counts == {"full": 1, "head": 0}
    assert events[-1].data["status"] == "COMPLETED"


async def test_b08_memory_redesign_http_retry_refreshes_facts_again_before_resending(async_tool_database):
    from muad_agent_core.agent import AgentRunner, AgentRunRequest
    from muad_agent_core.model import OpenAICompatibleProvider
    from muad_agent_core.tools import ToolRegistry

    from tests.agent_runtime.memory_confirmation_support import model_endpoint

    ctx = context()
    service = writer(async_tool_database)
    await service.execute(ctx, command(upsert(value="old_before_retry")))
    memory = loader(async_tool_database, ctx)

    async def mutate(index):
        if index == 1:
            await service.execute(
                ctx,
                command(commands().MemoryMutation(action="DELETE", memory_key="color", expected_version=1)),
            )

    async with model_endpoint([], on_request=mutate, fail_first=True) as (url, captured):
        provider = OpenAICompatibleProvider(base_url=url, model="probe", api_key=None)
        try:
            runner = AgentRunner(provider=provider, registry=ToolRegistry(), context_loader=memory)
            await runner.run(
                AgentRunRequest(
                    model_id="probe",
                    instructions="fixed instructions",
                    messages=(ModelMessage(ModelRole.USER, "你好"),),
                )
            )
        finally:
            await provider.aclose()
    assert len(captured) == 2 and "old_before_retry" in memory_text(captured[0])
    assert memory_text(captured[1]) is None


async def test_b07_aux_memory_redesign_failed_forget_suppresses_the_rest_of_this_run(async_tool_database):
    from muad_api import AppError

    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert(), upsert("keep", "untargeted")))
    memory = loader(async_tool_database, ctx)
    await memory.refresh()
    await install_history_failure(async_tool_database)
    request = ForgetMemoryInput(memory_key="color", expected_version=1)
    with pytest.raises(AppError):
        await memory.forget(service, ctx, request, command_id="failed")
    async with async_tool_database.begin() as connection:
        await connection.execute(sa.text("DROP TRIGGER memory_test_failure ON runtime.user_memory_history"))
    assert (await memory.forget(service, ctx, request, command_id="successful")).response.changed_count == 1
    assert await memory.refresh() is None
    assert (await queries(async_tool_database).read_snapshot(memory.cache.scope)).records[
        0
    ].memory_key == "keep"


async def test_b07_aux_memory_redesign_invalidation_rejects_an_inflight_older_snapshot(async_tool_database):
    import asyncio

    from muad_agent_runtime.application.memory_context import MemoryContextLoader
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.agent_runtime.test_memory_redesign_cache import cache

    ctx, service = context(), writer(async_tool_database)
    await service.execute(ctx, command(upsert()))
    reached, release = asyncio.Event(), asyncio.Event()
    base = type(queries(async_tool_database))

    class InterleavedQueries(base):
        async def read_snapshot(self, scope):
            snapshot = await super().read_snapshot(scope)
            reached.set()
            await release.wait()
            return snapshot

    memory = MemoryContextLoader(
        cache(
            async_tool_database, ctx, repository=InterleavedQueries(async_sessionmaker(async_tool_database))
        ),
        settings=MemoryPolicySettings(),
        query="你好",
    )
    pending = asyncio.create_task(memory.refresh())
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
        await memory.forget(
            service, ctx, ForgetMemoryInput(memory_key="color", expected_version=1), command_id="delete"
        )
    finally:
        release.set()
    assert await pending is None and memory.component is None
    assert (await memory.refresh()).message is None


async def test_b08_memory_redesign_history_scope_keeps_other_users_summary_private(async_tool_database):
    from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
    from muad_agent_runtime.application.run_events import EventWriter

    from tests.agent_runtime.memory_confirmation_support import source

    factory, run, identity, submission = await source(async_tool_database)
    async with factory.begin() as session:
        await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="CONTEXT_SUMMARY",
            payload={
                "covers_up_to_seq": 1,
                "summary": {
                    "user_goal": "private summary",
                    "constraints": [],
                    "progress": [],
                    "open_items": [],
                    "artifacts": [],
                },
            },
        )
    builder = DbBackedContextBuilder(session_factory=lambda: factory)
    with query_counts(async_tool_database) as counts:
        other = await builder.load_history(
            tenant_id=run.tenant_id, conversation_id=run.conversation_id, user_id=uuid4()
        )
        own = await builder.load_history(
            tenant_id=run.tenant_id, conversation_id=run.conversation_id, user_id=run.user_id
        )
    assert other == () and "private summary" in own[0].content
    assert counts == {"full": 0, "head": 0}


async def test_b07_aux_memory_redesign_write_disabled_still_allows_user_forget(async_tool_database):
    ctx = context()
    service = writer(async_tool_database)
    await service.execute(ctx, command(upsert()))
    policy = MemoryPolicySettings(write_enabled=False)
    memory = loader(async_tool_database, ctx, policy=policy)
    result = await memory.forget(
        service,
        replace(ctx, settings=policy),
        ForgetMemoryInput(memory_key="color", expected_version=1),
        command_id="forget-with-writes-off",
    )
    assert result.response.changed_count == 1
    assert (await memory.refresh()).message is None


@pytest.mark.parametrize("failure", [None, "delete", "refresh"])
async def test_b07_memory_redesign_production_forget_receipt_next_http_and_history(
    async_tool_database, monkeypatch, failure
):
    from muad_agent_runtime.application.context_builder import DbBackedContextBuilder

    from tests.agent_runtime.memory_confirmation_support import source
    from tests.agent_runtime.memory_executor_support import execute

    factory, run, identity, submission_id = await source(async_tool_database)
    ctx = context(tenant=run.tenant_id, user=run.user_id)
    await writer(async_tool_database).execute(ctx, command(upsert(value="old_memory_value")))
    await writer(async_tool_database).execute(ctx, command(upsert("other", "unrelated")))
    await _seed_forget_history(factory, run)
    if failure == "delete":
        await install_history_failure(async_tool_database)
    if failure == "refresh":
        await _install_recall_corruption(async_tool_database)
    history = await DbBackedContextBuilder(session_factory=lambda: factory).load_history(
        tenant_id=run.tenant_id, user_id=run.user_id, conversation_id=run.conversation_id
    )
    calls = [
        {
            "id": "forget-1",
            "type": "function",
            "function": {
                "name": "forget",
                "arguments": json.dumps({"memory_key": "color", "expected_version": 1}),
            },
        }
    ]

    events, captured = await execute(
        factory,
        run,
        identity,
        submission_id,
        calls,
        monkeypatch,
        policy=MemoryPolicySettings(max_injected_memories=2),
        history=history,
    )
    assert events
    assert len(captured) == 2 and "old_memory_value" in memory_text(captured[0])
    assert memory_text(captured[1]) is None if failure else "old_memory_value" not in memory_text(captured[1])
    receipt = json.loads(next(item["content"] for item in captured[1]["messages"] if item["role"] == "tool"))
    assert receipt.get("deleted", False) is (failure != "delete")
    if failure == "delete":
        assert receipt["error"]["code"] == "COMMON_INTERNAL_ERROR"
    await _assert_forget_history(factory, run, failure)


async def _seed_forget_history(factory, run):
    from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent

    async with factory.begin() as session:
        await session.execute(
            sa.update(CanonicalEvent)
            .where(CanonicalEvent.run_id == run.id)
            .values(payload_json={"text": "historical old_memory_value", "source_scope_revision": 0})
        )


async def _install_recall_corruption(engine):
    # The deletion commits, but the next full read sees an invalid fact from an actual PG trigger.
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "CREATE FUNCTION runtime.corrupt_recall() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.memory_key='color' AND NEW.is_deleted THEN "
                "UPDATE runtime.user_memory SET content_json=jsonb_build_object('value',NULL) "
                "WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id AND memory_key='other'; "
                "END IF; RETURN NEW; END $$"
            )
        )
        await connection.execute(
            sa.text(
                "CREATE TRIGGER corrupt_recall AFTER UPDATE ON runtime.user_memory "
                "FOR EACH ROW EXECUTE FUNCTION runtime.corrupt_recall()"
            )
        )


async def _assert_forget_history(factory, run, failure):
    from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, UserMemory, UserMemoryHistory

    async with factory() as session:
        row = await session.scalar(
            sa.select(UserMemory).where(
                UserMemory.tenant_id == run.tenant_id,
                UserMemory.user_id == run.user_id,
                UserMemory.memory_key == "color",
            )
        )
        history = list(
            await session.scalars(
                sa.select(UserMemoryHistory)
                .where(
                    UserMemoryHistory.tenant_id == run.tenant_id,
                    UserMemoryHistory.user_id == run.user_id,
                    UserMemoryHistory.memory_key == "color",
                )
                .order_by(UserMemoryHistory.version)
            )
        )
        events = list(await session.scalars(sa.select(CanonicalEvent).where(CanonicalEvent.run_id == run.id)))
    assert row.is_deleted is (failure != "delete")
    assert [item.version for item in history] == ([1] if failure == "delete" else [1, 2])
    assert history[0].after_json["content_json"]["value"] == "old_memory_value"
    if failure != "delete":
        assert history[-1].change_reason == "USER_DELETE" and row.retirement_reason == "USER_DELETE"
    assert any(event.payload_json.get("text") == "historical old_memory_value" for event in events)
