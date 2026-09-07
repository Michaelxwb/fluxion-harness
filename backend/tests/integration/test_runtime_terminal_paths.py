"""run/stream 终态路径统一（TASK-016 / ADR-A014）验收测试。

覆盖 S-LIFE-02 / E-LIFE-03：
- 真实边界：RuntimeApplicationService.run/stream → ExecutionSession；
  真实运行应用 → 取消/关闭/模型超时。
"""

from __future__ import annotations

import asyncio

import pytest

from fluxion.agents.definitions import AgentDefinition, AgentModelPolicy
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ExactResourceVersion, ResourceKind
from fluxion.services.runtime_app import (
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import (
    TEST_POSTGRES_DSN,
    publish_resource,
    seed_agent_definition,
    seed_model_definition,
    seed_tenant_policy,
)


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


async def _seed_deadline_agent(store: PostgreSQLRegistryStore) -> None:
    """截止 1.5s 的 agent（真实超时机制触发 E-LIFE-03 超时分支）。"""
    model = await seed_model_definition(
        store, tenant_id="tenant-a", provider_id="dev.echo", model_name="dev"
    )
    await seed_tenant_policy(store, tenant_id="tenant-a")
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="slow-agent",
        version="1",
        spec=AgentDefinition(
            name="slow-agent",
            description="fixture timeout agent",
            system_prompt="p",
            owner="fixture",
            model_policy=AgentModelPolicy(
                primary_model_ref=ExactResourceVersion(id=model.id, version=model.version),
                model_timeout_ms=500,
                model_deadline_ms=1500,
            ),
        ).model_dump(mode="json"),
    )


def _run_request(char: str, **overrides) -> RunRuntimeRequest:  # type: ignore[no-untyped-def]
    request_id, trace_id, execution_id = _ids(char)
    kwargs = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "runtime_profile_id": "assistant",
        "agent_definition_id": "assistant",
        "session_id": "session-terminal",
        "input_message": "hello",
        "request_id": request_id,
        "trace_id": trace_id,
        "execution_id": execution_id,
    }
    kwargs.update(overrides)
    return RunRuntimeRequest(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_S_LIFE_02_run_success_single_terminal() -> None:
    """S-LIFE-02：run 成功只有一次终态和 finalization。"""
    service, store = await _service()
    real_finish = service._runtime.finish_execution
    finished_contexts: list[int] = []

    async def recording_finish(context):  # type: ignore[no-untyped-def]
        finished_contexts.append(id(context))
        return await real_finish(context)

    service._runtime.finish_execution = recording_finish  # type: ignore[method-assign]
    try:
        result = await service.run(_run_request("a"))
        assert result.output == "dev: hello"
        assert len(finished_contexts) == 1
        record = await service.trace_store.get(_ids("a")[1])
        assert record is not None
        assert record.execution_id == _ids("a")[2]
    finally:
        service._runtime.finish_execution = real_finish  # type: ignore[method-assign]
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_S_LIFE_02_stream_success_single_terminal_per_context() -> None:
    """S-LIFE-02：stream 成功每个 context 恰结算一次（dev 回退路径如实记录）。"""
    service, store = await _service()
    real_finish = service._runtime.finish_execution
    finished_contexts: list[int] = []

    async def recording_finish(context):  # type: ignore[no-untyped-def]
        finished_contexts.append(id(context))
        return await real_finish(context)

    service._runtime.finish_execution = recording_finish  # type: ignore[method-assign]
    try:
        events = [event async for event in service.stream(_run_request("b"))]
        kinds = [event.event for event in events]
        assert kinds[0] == "started"
        assert kinds[-1] == "completed"
        # 每个 context 恰一次（dev 回退开第二 context 即记两次，无重复结算）。
        assert len(finished_contexts) == len(set(finished_contexts))
    finally:
        service._runtime.finish_execution = real_finish  # type: ignore[method-assign]
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_LIFE_03_cancel_close_leaves_no_residue() -> None:
    """E-LIFE-03：取消正确终态；显式 aclose 无本地残留（Trace 可查、后续可跑）。"""
    from fluxion.services.runtime_utils import DevEchoModelProvider

    service, store = await _service()
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    try:
        _, trace_id, _ = _ids("c")
        # 显式 aclose（started 后、开工前）：干净返回，无事可结算也不崩。
        idle = service.stream(_run_request("c"))
        assert (await idle.__anext__()).event == "started"
        await idle.aclose()

        # 工作中取消：同一 task 全程驱动（跨 task 驱动 generator 会错位
        # ContextVar token，属测试写法约束，非产品语义）。
        stream = service.stream(_run_request("c"))

        async def _drain() -> list:
            started = await stream.__anext__()
            assert started.event == "started"
            return [event async for event in stream]

        consumer = asyncio.create_task(_drain())
        # 等子生成器进入回退 run 的模型睡眠（工作中取消，非 started 后立即关）。
        await asyncio.sleep(2)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        record = await service.trace_store.get(trace_id)
        assert record is not None
        assert record.execution_id == _ids("c")[2]
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
        await service.close()
        await store.close()

    # 无残留：新栈后续执行正常（provider 已恢复）。
    service2, store2 = await _service()
    try:
        result = await service2.run(_run_request("d"))
        assert result.output == "dev: hello"
    finally:
        await service2.close()
        await store2.close()


@pytest.mark.asyncio
async def test_E_LIFE_03_model_timeout_terminal() -> None:
    """E-LIFE-03：模型超时正确终态（真实 deadline 机制 + 受控慢 Adapter）。"""
    from fluxion.services.runtime_contracts import RuntimeApplicationError
    from fluxion.services.runtime_utils import DevEchoModelProvider

    service, store = await _service()
    await _seed_deadline_agent(store)
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeApplicationError) as exc_info:
            await service.run(_run_request("e", agent_definition_id="slow-agent"))
        # 单次调用超时（model_timeout_ms=500 先于 deadline 触发），终态 TIMED_OUT。
        assert exc_info.value.code == "model_provider_timeout"
        record = await service.trace_store.get(_ids("e")[1])
        assert record is not None
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
        await service.close()
        await store.close()
