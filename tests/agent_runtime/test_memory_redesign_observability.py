"""LOCAL S-02 evidence: actual pipeline/audit/PG and commit-sensitive counters."""

import json
from importlib import import_module
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_core.tools.pipeline import ToolExecutionPipeline
from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit, UserMemory
from muad_agent_runtime.metrics import install_runtime_metrics
from muad_api import render_metrics

from tests.agent_runtime.memory_confirmation_support import round_recorder, source
from tests.agent_runtime.memory_redesign_write_support import (
    command,
    context,
    install_history_failure,
    upsert,
    writer,
)
from tests.agent_runtime.test_memory_redesign_recall import metric

pytestmark = pytest.mark.integration
CANARY = "private_candidate_别写审计"


def capture_projections(monkeypatch):
    from muad_agent_runtime.application import executor as executor_module

    projections, project = [], executor_module.memory_audit_preview

    def capture(arguments, **kwargs):
        result = project(arguments, **kwargs)
        projections.append(result)
        return result

    monkeypatch.setattr(executor_module, "memory_audit_preview", capture)
    return projections


def test_memory_catalog_is_visible_without_traffic_and_has_only_bounded_labels():
    from muad_agent_runtime.metrics import CATALOG

    expected = {
        "memory_confirmation_total",
        "memory_consolidation_enqueue_total",
        "memory_consolidation_total",
        "memory_quota_evictions_total",
        "memory_expiry_total",
        "memory_write_conflicts_total",
    }
    install_runtime_metrics(FastAPI())
    text = render_metrics()
    assert all(f"# TYPE {name} counter" in text for name in expected)
    definitions = {name: labels for name, _, labels, _ in CATALOG if name in expected}
    assert set(definitions) == expected
    assert all(set(labels) <= {"outcome", "reason"} for labels in definitions.values())


@pytest.mark.parametrize("labels", [{"user_id": "user-1"}, {"outcome": str(uuid4())}])
def test_memory_observability_rejects_unbounded_labels_before_registering_a_sample(labels):
    module = import_module("muad_agent_runtime.application.memory_observability")
    before = render_metrics()
    with pytest.raises(ValueError):
        module.observe("memory_confirmation_total", labels=labels)
    assert render_metrics() == before


@pytest.mark.parametrize("valid", [True, False])
async def test_remember_audit_is_redacted_before_handler_even_for_invalid_schema(
    async_tool_database, monkeypatch, valid,
):
    from muad_agent_core.model import ModelMessage, ModelRole

    factory, run, _, _ = await source(async_tool_database)
    recorder, registry = round_recorder(factory, run), ToolRegistry()
    projections = capture_projections(monkeypatch)
    arguments = {"memory_key": "color", "category": "PREFERENCE", "value": CANARY, "source_quote": CANARY}
    if not valid:
        arguments["extra"] = CANARY

    async def handler(arguments, *, call_id):
        assert projections and all(CANARY not in json.dumps(item) for item in projections)
        return '{"saved":false}'

    registry.register(
        ToolDefinition(
            name="remember",
            description="fixture",
            effect=ToolEffect.WRITE,
            input_schema={
                "type": "object",
                "properties": {
                    key: {"type": "string"} for key in ("memory_key", "category", "value", "source_quote")
                },
                "additionalProperties": False,
            },
            handler=handler,
        )
    )
    pipeline = ToolExecutionPipeline(registry=registry, audit=recorder)
    result = await pipeline.execute(
        pipeline.prepare(call_id="audit-case", tool_name="remember", arguments=arguments)
    )
    await recorder.finish_round(
        [ModelMessage(role=ModelRole.TOOL, content=result.content, tool_call_id="audit-case")]
    )
    async with factory() as session:
        audit = await session.scalar(sa.select(ToolCallAudit).where(ToolCallAudit.run_id == run.id))
    assert audit is not None and CANARY not in json.dumps(audit.args_preview_json, ensure_ascii=False)
    assert set(audit.args_preview_json) <= {"memory_key", "category", "args_hash", "status"}
    assert CANARY not in result.content
    assert audit.status == ("OK" if valid else "ERROR")


async def test_failed_history_has_warning_error_counter_and_no_success(async_tool_database, caplog):
    ctx, service = context(), writer(async_tool_database)
    await install_history_failure(async_tool_database)
    before_ok, before_error = (
        metric("memory_write_total", outcome="OK"),
        metric("memory_write_total", outcome="ERROR"),
    )
    from muad_api import AppError

    with pytest.raises(AppError):
        await service.execute(ctx, command(upsert(value=CANARY)))
    assert metric("memory_write_total", outcome="OK") == before_ok
    assert metric("memory_write_total", outcome="ERROR") == before_error + 1
    assert any(record.getMessage() == "memory_command_failed" for record in caplog.records)
    assert CANARY not in caplog.text


async def test_success_counter_waits_for_outer_commit_and_nested_rollback_is_not_success(async_tool_database):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    ctx, service = context(), writer(async_tool_database)
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    before = metric("memory_write_total", outcome="OK")
    async with factory() as session:
        await session.begin()
        await service.execute_in_session(session, ctx, command(upsert()))
        assert metric("memory_write_total", outcome="OK") == before
        await session.rollback()
    assert metric("memory_write_total", outcome="OK") == before
    async with factory.begin() as session:
        async with session.begin_nested() as nested:
            await service.execute_in_session(session, ctx, command(upsert("rolled-back")))
            await nested.rollback()
        await service.execute_in_session(session, ctx, command(upsert("committed")))
        assert metric("memory_write_total", outcome="OK") == before
    assert metric("memory_write_total", outcome="OK") == before + 1
    async with factory() as session:
        keys = list(
            await session.scalars(
                sa.select(UserMemory.memory_key).where(UserMemory.tenant_id == ctx.tenant_id)
            )
        )
    assert keys == ["committed"]


@pytest.mark.parametrize("commit", [True, False])
async def test_nested_commit_does_not_publish_success_before_outer_decision(async_tool_database, commit):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    ctx, service = context(), writer(async_tool_database)
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    before = metric("memory_write_total", outcome="OK")
    async with factory() as session:
        await session.begin()
        async with session.begin_nested():
            await service.execute_in_session(session, ctx, command(upsert()))
        assert metric("memory_write_total", outcome="OK") == before
        if commit:
            await session.commit()
        else:
            await session.rollback()
    assert metric("memory_write_total", outcome="OK") == before + int(commit)
    async with factory() as session:
        rows = list(await session.scalars(sa.select(UserMemory).where(UserMemory.tenant_id == ctx.tenant_id)))
    assert len(rows) == int(commit)
