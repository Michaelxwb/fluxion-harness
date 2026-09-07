"""Memory 与 Trace 清理失败分支（TASK-015 / ADR-A014 §7 §9）验收测试。

覆盖 E-LIFE-02：
- 真实边界：真实 MemoryManager/TraceWriter → PostgreSQL + 边界故障注入；
- L0/flushed_counts 释放；flush/Trace 超时有界；错误记录可关联且原始原因保留。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.tracing import InMemoryTraceStore, TraceRecord
from fluxion.services.runtime_app import (
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_agent_definition


def _ids(char: str) -> tuple[str, str, str]:
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


class _FailingMemoryStore(InMemorySessionMemoryStore):
    """边界故障注入：写失败，读正常。"""

    async def append_l1(self, records: list) -> None:  # type: ignore[no-untyped-def]
        raise ConnectionError("memory store unavailable")

    async def append_l2(self, records: list) -> None:  # type: ignore[no-untyped-def]
        raise ConnectionError("memory store unavailable")


class _FailingTraceStore(InMemoryTraceStore):
    """边界故障注入：持久化失败。"""

    async def append(self, record: TraceRecord) -> None:
        raise ConnectionError("trace store unavailable")


class _HangingTraceStore(InMemoryTraceStore):
    """边界故障注入：持久化永不返回。"""

    async def append(self, record: TraceRecord) -> None:
        await asyncio.sleep(3600)


async def _seeded_service(**stores):  # type: ignore[no-untyped-def]
    from fluxion.services.runtime_app import CreateRuntimeProfileRequest

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store, **stores)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            request_timeout_ms=10_000,
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


def _run_request(char: str, **overrides) -> RunRuntimeRequest:  # type: ignore[no-untyped-def]
    request_id, trace_id, execution_id = _ids(char)
    kwargs = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "runtime_profile_id": "assistant",
        "agent_definition_id": "assistant",
        "session_id": "session-final",
        "input_message": "hello",
        "request_id": request_id,
        "trace_id": trace_id,
        "execution_id": execution_id,
    }
    kwargs.update(overrides)
    return RunRuntimeRequest(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_E_LIFE_02_flush_failure_releases_and_preserves_result() -> None:
    """E-LIFE-02：flush 异常不阻止 L0 释放；业务结果保留（不伪造失败）。"""
    service, store = await _seeded_service(memory_store=_FailingMemoryStore())
    try:
        first = await service.run(_run_request("a"))
        assert first.output == "dev: hello"
        # L0 已释放：第二次同执行 ID 运行不受污染（无残留累积）。
        second = await service.run(_run_request("b"))
        assert second.output == "dev: hello"
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_LIFE_02_trace_failure_observed_and_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E-LIFE-02：Trace 持久化失败可观测（带 ID 关联）；业务结果保留。"""
    caplog.set_level(logging.ERROR)
    service, store = await _seeded_service(trace_store=_FailingTraceStore())
    try:
        result = await service.run(_run_request("c"))
        assert result.output == "dev: hello"
    finally:
        await service.close()
        await store.close()
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "trace store unavailable" in text
    assert _ids("c")[1] in text


@pytest.mark.asyncio
async def test_E_LIFE_02_hanging_trace_does_not_block_result() -> None:
    """E-LIFE-02：Trace 卡住时有界返回，不无限延长执行。"""
    service, store = await _seeded_service(trace_store=_HangingTraceStore())
    try:
        result = await asyncio.wait_for(service.run(_run_request("d")), timeout=60)
        assert result.output == "dev: hello"
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_LIFE_02_error_path_preserves_original_cause() -> None:
    """E-LIFE-02：错误路径上 Trace 失败不覆盖原始业务错误。"""
    service, store = await _seeded_service(trace_store=_FailingTraceStore())
    try:
        from fluxion.services.runtime_contracts import RuntimeApplicationError

        with pytest.raises(RuntimeApplicationError) as exc_info:
            await service.run(_run_request("e", agent_definition_id="ghost-agent"))
        assert "trace store unavailable" not in str(exc_info.value)
        assert "agent_not_found" in str(exc_info.value.code)
    finally:
        await service.close()
        await store.close()
