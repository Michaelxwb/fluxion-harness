"""Nested and concurrent background boundaries restore API and logging context."""

import asyncio

import pytest
from muad_api.context import context_scope, current_tenant_id, current_trace_id, trace_correlation_fields
from muad_logging.context import get_log_context


async def test_scope_restores_nested_tokens_and_isolates_concurrent_executions():
    original = get_log_context(), current_trace_id(), current_tenant_id(), trace_correlation_fields()
    with context_scope(trace_id="outer", tenant_id="tenant", run_id="run"):
        assert get_log_context()["run_id"] == "run"
        async def isolated(call):
            with context_scope(trace_id=call, tool_call_id=call, task_id=f"task-{call}"):
                await asyncio.sleep(0)
                assert current_trace_id() == call and get_log_context()["tool_call_id"] == call
                assert get_log_context()["run_id"] == "run"
        await asyncio.gather(isolated("a"), isolated("b"))
        assert current_trace_id() == "outer" and get_log_context()["tool_call_id"] == ""
        with pytest.raises(RuntimeError), context_scope(trace_id="nested"):
            raise RuntimeError("unwind")
        assert current_trace_id() == "outer"
        with pytest.raises(ValueError), context_scope(operation_id="unknown"):
            pass
    restored = (get_log_context(), current_trace_id(), current_tenant_id(), trace_correlation_fields())
    assert restored == original
