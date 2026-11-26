"""E-05: task clues are unconfirmed hints; only explicit selection binds a resume ref."""

from __future__ import annotations

from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_confirmation_support import model_endpoint, source
from tests.agent_runtime.memory_resume_support import (
    decision,
    events,
    inbound,
    run_stream,
    seed_tasks,
)


async def test_single_candidate_stays_pending_and_model_answers(async_tool_database, monkeypatch):
    factory, run, _, _ = await source(async_tool_database)
    keys = await seed_tasks(async_tool_database, run, titles=("季度报告",))
    submission, _ = await inbound(factory, run, "继续上次任务")

    options = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]
    assert "delta" not in options.payload_json
    assert options.stream_type is None
    assert await events(factory, run, "MEMORY_RESUME_RESOLVED") == []

    result = await decision(factory, run, submission)
    assert result.reference is None
    assert result.hint is not None
    assert "季度报告" in result.hint and "未经确认" in result.hint
    assert "1. 季度报告 — `" in result.hint
    assert f"`{keys[0]}`" in result.hint

    async with model_endpoint([]) as (url, captured):
        results = await run_stream(factory, run, submission, url, monkeypatch)
    assert captured, "bare continue must call the model"
    assert results[-1].type == "run.completed"
    assert results[-1].data["final_text"] == "ok"


async def test_explicit_selection_binds_the_original_task_ref(async_tool_database, monkeypatch):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run)
    await inbound(factory, run, "继续上次任务")
    listed = (await events(factory, run, "MEMORY_RESUME_OPTIONS"))[0]

    second, _ = await inbound(factory, run, "第2个")
    result = await decision(factory, run, second)
    assert result.reference is not None
    selected = listed.payload_json["items"][1]
    assert result.reference.memory_key == selected["memory_key"]
    assert result.hint is not None
    assert "已明确选择第 2 个任务线索" in result.hint
    assert selected["title"] in result.hint
    resolved = (await events(factory, run, "MEMORY_RESUME_RESOLVED"))[0]
    assert resolved.source_event_id == listed.id
    assert resolved.payload_json["outcome"] == "SELECTED"


async def test_no_candidates_does_not_write_platform_notice_and_model_answers(
    async_tool_database, monkeypatch
):
    factory, run, _, _ = await source(async_tool_database)
    submission, _ = await inbound(factory, run, "继续季度报告")

    assert await events(factory, run, "MEMORY_RESUME_OPTIONS") == []
    assert await events(factory, run, "MEMORY_RESUME_NOTICE") == []
    result = await decision(factory, run, submission)
    assert result.reference is None and result.hint is None

    async with model_endpoint([]) as (url, captured):
        results = await run_stream(factory, run, submission, url, monkeypatch)
    assert captured
    assert results[-1].data["status"] == "COMPLETED"


async def test_zero_budget_disables_task_channel(async_tool_database):
    factory, run, _, _ = await source(async_tool_database)
    await seed_tasks(async_tool_database, run, titles=("季度报告",))
    submission, _ = await inbound(
        factory,
        run,
        "继续上次任务",
        settings=MemoryPolicySettings(max_injected_memories=0),
    )

    result = await decision(factory, run, submission)
    assert result.reference is None and result.hint is None
    assert await events(factory, run, "MEMORY_RESUME_OPTIONS") == []
