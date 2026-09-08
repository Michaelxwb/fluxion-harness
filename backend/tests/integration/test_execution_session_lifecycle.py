"""ExecutionSession 生命周期（TASK-014 / ADR-A014）验收测试。

覆盖 S-LIFE-01 / E-LIFE-01：
- 真实边界：真实 ExecutionSession → AgentRuntime → MemoryManager；
- 真实准备流水线 → 故障 Adapter → finalizer。
"""

from __future__ import annotations

import asyncio

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.execution_session import ExecutionSession
from fluxion.services.runtime_app import (
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from fluxion.services.runtime_contracts import ExecutionTerminalState
from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_agent_definition


def _ids(char: str) -> tuple[str, str, str]:
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


async def _service() -> tuple[RuntimeApplicationService, PostgreSQLRegistryStore]:
    from fluxion.services.runtime_app import CreateRuntimeProfileRequest

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            default=True,
        )
    )
    await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a", runtime_profile_id="assistant", version="1"
        )
    )
    return service, store


def _run_request(char: str) -> RunRuntimeRequest:
    request_id, trace_id, execution_id = _ids(char)
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        agent_definition_id="assistant",
        session_id="session-lifecycle",
        input_message="hello",
        request_id=request_id,
        trace_id=trace_id,
        execution_id=execution_id,
    )


@pytest.mark.asyncio
async def test_S_LIFE_01_success_finalizes_exactly_once() -> None:
    """S-LIFE-01：成功结束只结算一次（重复 finalize 不重复 flush）。"""
    service, store = await _service()
    real_finish = service._runtime.finish_execution
    calls = 0

    async def counting_finish(context):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return await real_finish(context)

    service._runtime.finish_execution = counting_finish  # type: ignore[method-assign]
    try:
        session = ExecutionSession(service)
        prepared = await session.prepare(_run_request("a"))
        first = await session.finalize(prepared.context)
        second = await session.finalize(prepared.context)
        assert first is ExecutionTerminalState.COMPLETED
        assert second is ExecutionTerminalState.COMPLETED
        assert calls == 1
    finally:
        service._runtime.finish_execution = real_finish  # type: ignore[method-assign]
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_LIFE_01_prepare_failure_still_releases() -> None:
    """E-LIFE-01：部分初始化失败也释放；finalize 幂等（故障 Adapter 注入）。"""
    service, store = await _service()
    real_prepare = service._mcp_runtime.prepare
    calls = 0

    async def failing_prepare(context, tool_runtime):  # type: ignore[no-untyped-def]
        raise RuntimeError("mcp prepare boom")

    async def counting_finish(context):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1

    real_finish = service._runtime.finish_execution
    service._mcp_runtime.prepare = failing_prepare  # type: ignore[method-assign]
    service._runtime.finish_execution = counting_finish  # type: ignore[method-assign]
    try:
        session = ExecutionSession(service)
        with pytest.raises(RuntimeError, match="mcp prepare boom"):
            await session.prepare(_run_request("b"))
        # 失败已结算为 FAILED；显式再 finalize 不重复清理。
        assert session.terminal is ExecutionTerminalState.FAILED
        assert session.context is not None
        again = await session.finalize(session.context)
        assert again is ExecutionTerminalState.FAILED
        assert calls == 1
    finally:
        service._mcp_runtime.prepare = real_prepare  # type: ignore[method-assign]
        service._runtime.finish_execution = real_finish  # type: ignore[method-assign]
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_LIFE_01_finalize_bounded_when_finish_hangs() -> None:
    """E-LIFE-01：finish 卡住时 finalize 有界返回，不无限延长取消。"""
    service, store = await _service()
    real_finish = service._runtime.finish_execution

    async def hanging_finish(context):  # type: ignore[no-untyped-def]
        await asyncio.sleep(3600)

    service._runtime.finish_execution = hanging_finish  # type: ignore[method-assign]
    try:
        session = ExecutionSession(service)
        prepared = await session.prepare(_run_request("c"))
        state = await asyncio.wait_for(session.finalize(prepared.context), timeout=30)
        assert state is ExecutionTerminalState.COMPLETED
    finally:
        service._runtime.finish_execution = real_finish  # type: ignore[method-assign]
        await service.close()
        await store.close()
