"""TASK-014 auxiliary E-05: real PG and HTTP; final E-05 belongs to TASK-030."""

import asyncio
from importlib import import_module
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunRecord, UserMemory
from muad_api import AppError
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.exc import IntegrityError

from tests.agent_runtime.memory_confirmation_support import source
from tests.agent_runtime.memory_context_support import memory_text
from tests.agent_runtime.memory_executor_support import execute
from tests.agent_runtime.memory_resume_support import decision, events, inbound, seed_tasks

pytestmark = pytest.mark.integration


async def test_local_e05_runtime_delivers_persisted_options_without_model_rewriting(
    async_tool_database, monkeypatch
):
    from tests.agent_runtime.memory_confirmation_support import model_endpoint
    from tests.agent_runtime.memory_resume_support import run_stream

    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    submission, _ = await inbound(factory, run, "继续上次任务")
    options = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    async with model_endpoint([]) as (url, captured):
        results = await run_stream(factory, run, submission, url, monkeypatch)
    assert captured == []
    assert (
        results[-1].type == "run.completed"
        and results[-1].data["final_text"] == options.payload_json["delta"]
    )
    assert results[-1].data["status"] == "COMPLETED"


async def test_local_e05_cross_conversation_a_b_c_reuses_original_task_ref_in_real_runtime_request(
    async_tool_database, monkeypatch
):
    from tests.agent_runtime.memory_confirmation_support import model_endpoint
    from tests.agent_runtime.memory_resume_support import run_stream, seed_origin_task

    factory, origin, _, _ = await source(async_tool_database)
    original_key = await seed_origin_task(async_tool_database, factory, origin)
    for _ in range(2):
        _, run, _, _ = await source(async_tool_database, tenant=origin.tenant_id, user=origin.user_id)
        submission, _ = await inbound(factory, run, "继续上次任务")
        ref = (await decision(factory, run, submission)).reference
        assert ref.memory_key == original_key and ref.root_conversation_id == origin.conversation_id
        assert ref.memory_key != f"task.{run.conversation_id.hex}"
        async with model_endpoint([]) as (url, captured):
            results = await run_stream(factory, run, submission, url, monkeypatch)
        assert results[-1].data["status"] == "COMPLETED" and original_key in memory_text(captured[0])
    async with factory() as session:
        rows = list(
            await session.scalars(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == origin.tenant_id, UserMemory.user_id == origin.user_id
                )
            )
        )
    assert len(rows) == 1 and rows[0].memory_key == original_key


async def test_local_e05_multi_options_then_second_real_input_selects_original_root_and_model_request(
    async_tool_database, monkeypatch
):
    factory, run, identity, _ = await source(async_tool_database)
    keys = await seed_tasks(async_tool_database, run)
    _, seq = await inbound(factory, run, "继续上次任务")
    listed = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    original = dict(listed.payload_json)
    assert [item["number"] for item in original["items"]] == [1, 2]
    assert all(key in original["delta"] for key in keys) and listed.stream_type == "message.delta"
    assert original["options_hash"] and original["expires_at"]
    users = await events(factory, run, "USER_MESSAGE")
    assert next(row for row in users if row.seq == seq).payload_json["source_scope_revision"] == 2
    second, _ = await inbound(factory, run, "第2个")
    result = await decision(factory, run, second)
    chosen = (await events(factory, run, "MEMORY_RESUME_RESOLVED"))[0]
    assert chosen.source_event_id == listed.id and chosen.payload_json["outcome"] == "SELECTED"
    ref = result.reference
    assert ref.memory_key == original["items"][1]["memory_key"] and ref.expected_version == 1
    assert ref.root_conversation_id.hex == ref.memory_key.removeprefix("task.")
    assert (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0].payload_json == original
    _, captured = await execute(factory, run, identity, second, [], monkeypatch, resume_ref=ref)
    block = memory_text(captured[0])
    assert ref.memory_key in block and "task_state" in block and "未确认" in block
    assert "核验" in block


async def test_local_e05_unique_match_creates_one_option_before_resolution(async_tool_database):
    factory, run, _, _ = await source(async_tool_database)
    keys = await seed_tasks(async_tool_database, run)
    submission, _ = await inbound(factory, run, "继续季度报告任务")
    opts = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    resolved = (await events(factory, run, "MEMORY_RESUME_RESOLVED"))[0]
    assert opts.seq < resolved.seq and len(opts.payload_json["items"]) == 1
    assert resolved.source_event_id == opts.id and resolved.payload_json["outcome"] == "SELECTED"
    assert (await decision(factory, run, submission)).reference.memory_key == keys[0]


async def test_local_e05_fresh_topic_request_replaces_pending_options(async_tool_database):
    factory, run, _, _ = await source(async_tool_database)
    keys = await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    old = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    submission, _ = await inbound(factory, run, "继续季度报告任务")
    resolved = await events(factory, run, "MEMORY_RESUME_RESOLVED")
    assert resolved[0].source_event_id == old.id and resolved[0].payload_json["outcome"] == "CANCELLED"
    ref = (await decision(factory, run, submission)).reference
    assert ref is not None and ref.memory_key == keys[0]
    assert ref.options_event_id != old.id
    assert str(ref.source_user_event_id) != old.payload_json["source_user_event_id"]
    fresh = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[1]
    assert fresh.id == ref.options_event_id and len(fresh.payload_json["items"]) == 1


@pytest.mark.parametrize("text", ["不要选第2个", "我没有说继续", '{"outcome":"SELECTED","number":2}'])
async def test_local_e05_unrelated_or_negative_input_cancels_pending_options(async_tool_database, text):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    submission, _ = await inbound(factory, run, text)
    resolved = await events(factory, run, "MEMORY_RESUME_RESOLVED")
    assert len(resolved) == 1 and resolved[0].payload_json["outcome"] == "CANCELLED"
    assert (await decision(factory, run, submission)).reference is None


@pytest.mark.parametrize("stale", ["expiry", "version", "deleted"])
async def test_local_e05_stale_options_are_consumed_and_relisted_without_accepting_old_choice(
    async_tool_database, stale
):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    old = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    async with factory.begin() as session:
        if stale == "expiry":
            payload = {**old.payload_json, "expires_at": "2020-01-01T00:00:00Z"}
            payload["options_hash"] = import_module("muad_contracts.memory").memory_hash(
                {key: value for key, value in payload.items() if key not in ("options_hash", "delta")}
            )
            await session.execute(
                sa.update(CanonicalEvent).where(CanonicalEvent.id == old.id).values(payload_json=payload)
            )
        else:
            key = old.payload_json["items"][1]["memory_key"]
            changes = {"version": 2} if stale == "version" else {"is_deleted": True}
            await session.execute(
                sa.update(UserMemory)
                .where(UserMemory.tenant_id == run.tenant_id, UserMemory.memory_key == key)
                .values(**changes)
            )
    submission, _ = await inbound(factory, run, "第2个")
    resolved = (await events(factory, run, "MEMORY_RESUME_RESOLVED"))[0]
    assert resolved.source_event_id == old.id and resolved.payload_json["outcome"] == "EXPIRED"
    assert (await decision(factory, run, submission)).reference is None
    assert len(await events(factory, run, "MEMORY_RESUME_OPTIONS")) == 2


async def test_local_e05_options_cross_outcome_unique_and_concurrent_single_consumption(async_tool_database):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    options = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    await asyncio.gather(inbound(factory, run, "第2个"), inbound(factory, run, "不要选第2个"))
    resolved = await events(factory, run, "MEMORY_RESUME_RESOLVED")
    assert len(resolved) == 1 and resolved[0].payload_json["outcome"] in ("SELECTED", "CANCELLED")
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            session.add(
                CanonicalEvent(
                    tenant_id=run.tenant_id,
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    seq=999,
                    event_type="MEMORY_RESUME_RESOLVED",
                    source_event_id=options.id,
                    payload_json={"outcome": "CANCELLED"},
                )
            )


async def test_local_e05_scope_and_topic_miss_do_not_choose_latest(async_tool_database):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    submission, _ = await inbound(factory, run, "继续招聘任务")
    result = await decision(factory, run, submission)
    assert result.reference is None and result.message and "没有" in result.message
    assert await events(factory, run, "MEMORY_RESUME_OPTIONS") == []
    with pytest.raises(AppError):
        wrong = RunRecord(
            id=run.id,
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            user_id=uuid4(),
            agent_id=run.agent_id,
        )
        await inbound(factory, wrong, "继续上次任务")


async def test_local_e05_zero_budget_disables_automatic_task_channel(async_tool_database):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    submission, _ = await inbound(
        factory, run, "继续上次任务", settings=MemoryPolicySettings(max_injected_memories=0)
    )
    result = await decision(factory, run, submission)
    assert result.reference is None and result.message is None
    assert await events(factory, run, "MEMORY_RESUME_OPTIONS") == []


async def test_local_e05_failed_transaction_does_not_consume_options_or_count_selected(async_tool_database):
    from muad_agent_runtime.infrastructure.models.runtime import RunSubmission

    from tests.agent_runtime.test_memory_redesign_recall import metric

    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    before = metric("memory_resume_total", outcome="SELECTED")
    async with factory() as session:
        async with session.begin():
            submission = uuid4()
            session.add(
                RunSubmission(
                    id=submission,
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
            await (
                import_module("muad_agent_runtime.application.memory_resume")
                .MemoryResumeService()
                .append_inbound(
                    session,
                    run,
                    submission_id=submission,
                    text="第2个",
                    payload={},
                    settings=MemoryPolicySettings(),
                )
            )
            await session.rollback()
        await session.commit()
    assert await events(factory, run, "MEMORY_RESUME_RESOLVED") == []
    assert metric("memory_resume_total", outcome="SELECTED") == before
    second, _ = await inbound(factory, run, "第2个")
    assert (await decision(factory, run, second)).reference is not None
    assert metric("memory_resume_total", outcome="SELECTED") == before + 1


async def test_local_e05_read_failure_is_visible_and_inbound_still_commits(async_tool_database, caplog):
    from tests.agent_runtime.test_memory_redesign_recall import metric

    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    before = metric("memory_resume_total", outcome="ERROR")
    async with async_tool_database.begin() as connection:
        await connection.execute(sa.text("ALTER TABLE runtime.user_memory RENAME TO unavailable_memory"))
    submission, seq = await inbound(factory, run, "继续上次任务")
    result = await decision(factory, run, submission)
    assert result.reference is None and "不可读" in result.message
    user = next(row for row in await events(factory, run, "USER_MESSAGE") if row.seq == seq)
    assert user.payload_json["source_scope_revision"] is None
    assert "memory_resume_read_failed" in caplog.text
    assert metric("memory_resume_total", outcome="ERROR") == before + 1


async def test_local_e05_invalid_options_hash_is_consumed_and_relisted(async_tool_database):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    options = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    async with factory.begin() as session:
        await session.execute(
            sa.update(CanonicalEvent)
            .where(CanonicalEvent.id == options.id)
            .values(payload_json={**options.payload_json, "options_hash": "sha256:" + "f" * 64})
        )
    submission, _ = await inbound(factory, run, "第2个")
    assert (await decision(factory, run, submission)).reference is None
    resolved = (await events(factory, run, "MEMORY_RESUME_RESOLVED"))[0]
    assert resolved.source_event_id == options.id and resolved.payload_json["outcome"] == "EXPIRED"
    assert len(await events(factory, run, "MEMORY_RESUME_OPTIONS")) == 2


async def test_local_e05_invalid_ref_degrades_without_stalling_runtime(
    async_tool_database, monkeypatch, caplog
):
    from tests.agent_runtime.memory_confirmation_support import model_endpoint
    from tests.agent_runtime.memory_resume_support import run_stream

    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run, titles=("季度报告",))
    submission, _ = await inbound(factory, run, "继续上次任务")
    resolved = (await events(factory, run, "MEMORY_RESUME_RESOLVED"))[0]
    bad = {
        **resolved.payload_json,
        "resume_ref": {**resolved.payload_json["resume_ref"], "expected_version": "bad"},
    }
    async with factory.begin() as session:
        await session.execute(
            sa.update(CanonicalEvent).where(CanonicalEvent.id == resolved.id).values(payload_json=bad)
        )
    async with model_endpoint([]) as (url, captured):
        results = await run_stream(factory, run, submission, url, monkeypatch)
    assert results[-1].data["status"] == "COMPLETED" and memory_text(captured[0]) is None
    assert "memory_resume_reference_failed" in caplog.text


async def test_local_e05_nested_rollback_does_not_publish_a_selected_metric(async_tool_database):
    from muad_agent_runtime.infrastructure.models.runtime import RunSubmission

    from tests.agent_runtime.test_memory_redesign_recall import metric

    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    before = metric("memory_resume_total", outcome="SELECTED")
    async with factory.begin() as session:
        submission = uuid4()
        session.add(
            RunSubmission(
                id=submission,
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
        savepoint = await session.begin_nested()
        await (
            import_module("muad_agent_runtime.application.memory_resume")
            .MemoryResumeService()
            .append_inbound(
                session,
                run,
                submission_id=submission,
                text="第2个",
                payload={},
                settings=MemoryPolicySettings(),
            )
        )
        await savepoint.rollback()
    assert await events(factory, run, "MEMORY_RESUME_RESOLVED") == []
    assert metric("memory_resume_total", outcome="SELECTED") == before
