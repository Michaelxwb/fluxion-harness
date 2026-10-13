"""Current proposal/read/delete observability through real tools, PG and HTTP."""

import json
import logging

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit
from muad_api import AppError

from tests.agent_runtime.memory_confirmation_support import service, source
from tests.agent_runtime.memory_context_support import loader
from tests.agent_runtime.memory_executor_support import execute, tool_call
from tests.agent_runtime.memory_redesign_write_support import (
    command,
    context,
    install_history_failure,
    upsert,
    writer,
)
from tests.agent_runtime.memory_tool_support import tools
from tests.agent_runtime.test_memory_redesign_recall import metric

pytestmark = pytest.mark.integration


async def test_rule_log_001_memory_value_is_never_written_to_logs(async_tool_database, caplog):
    factory, run, identity, submission_id = await source(async_tool_database)
    ctx = context(tenant=run.tenant_id, user=run.user_id)
    sentinel = "sensitive_user_preference_别落日志"
    await writer(async_tool_database).execute(ctx, command(upsert(value=sentinel)))
    memory = loader(async_tool_database, ctx)
    tool_set = tools(
        async_tool_database, ctx, memory=memory, confirmation=service(factory, identity, submission_id)
    )
    with caplog.at_level(logging.INFO):
        receipt = await tool_set.remember(
            {"memory_key": "other", "value": sentinel, "category": "PREFERENCE"}, call_id="proposal"
        )
        await tool_set.recall({"query": sentinel}, call_id="read")
        await memory.refresh()
    assert json.loads(receipt)["saved"] is False
    records = [record for record in caplog.records if record.name.startswith("muad_agent_runtime")]
    assert records
    assert all(sentinel not in str(record.__dict__) for record in records)


async def test_memory_metrics_cover_inject_and_recall_without_faking_a_write(async_tool_database):
    ctx = context()
    await writer(async_tool_database).execute(ctx, command(upsert()))
    memory = loader(async_tool_database, ctx)
    tool_set = tools(async_tool_database, ctx, memory=memory)
    before_inject = metric("memory_inject_total", outcome="OK")
    before_recall = metric("memory_recall_total", mode="LIST", outcome="OK")
    await memory.refresh()
    receipt = await tool_set.recall({}, call_id="read")
    assert json.loads(receipt)["items"]
    assert metric("memory_inject_total", outcome="OK") == before_inject + 1
    assert metric("memory_recall_total", mode="LIST", outcome="OK") == before_recall + 1


async def test_rule_07_delete_failure_is_visible_in_audit_metric_and_safe_receipt(
    async_tool_database, monkeypatch, caplog
):
    factory, run, identity, submission_id = await source(async_tool_database)
    ctx = context(tenant=run.tenant_id, user=run.user_id)
    await writer(async_tool_database).execute(ctx, command(upsert(value="private_value_canary")))
    await install_history_failure(async_tool_database)
    before = metric("tool_calls_total", tool="forget", status="ERROR")
    _, captured = await execute(
        factory,
        run,
        identity,
        submission_id,
        [tool_call("forget", {"memory_key": "color", "expected_version": 1})],
        monkeypatch,
    )
    receipt = json.loads(next(item["content"] for item in captured[1]["messages"] if item["role"] == "tool"))
    assert receipt["error"]["code"] == "COMMON_INTERNAL_ERROR" and "deleted" not in receipt
    assert "private_value_canary" not in str(receipt) and "private_value_canary" not in caplog.text
    assert metric("tool_calls_total", tool="forget", status="ERROR") == before + 1
    async with factory() as session:
        audit = await session.scalar(sa.select(ToolCallAudit).where(ToolCallAudit.run_id == run.id))
    assert audit.status == "ERROR" and audit.error_code == "COMMON_INTERNAL_ERROR"
    assert caplog.records


async def test_invalid_source_is_a_rejection_not_a_saved_receipt(async_tool_database):
    with pytest.raises(AppError) as caught:
        await tools(async_tool_database, context()).remember(
            {
                "memory_key": "color",
                "value": "blue",
                "category": "PREFERENCE",
                "source_type": "USER_EXPLICIT",
            },
            call_id="forged",
        )
    assert caught.value.code == "MEMORY_CONFIRMATION_INVALID"
