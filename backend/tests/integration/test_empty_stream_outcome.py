"""流式能力与空输出区分（TASK-023）验收测试。

覆盖 S-STR-01 / B-STR-01 / E-STR-01：
- 真实边界：真实 AgentRuntime + 可计数 Provider → ApplicationService；
  真实流式分派 → 非流式 Provider/空 token；真实流式迭代 → Provider 错误。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from fluxion.agents.definitions import AgentModelPolicy
from fluxion.plugins.contracts import ModelProviderError, ModelRequest, ModelResponse
from fluxion.plugins.model_provider import ModelProviderRegistry
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
    seed_model_definition,
    seed_tenant_policy,
)


class _EmptyStreamProvider:
    """支持流式但零 token 结束（completed 空结果）。"""

    def __init__(self) -> None:
        self.stream_calls = 0
        self.complete_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        self.stream_calls += 1
        return
        yield  # pragma: no cover - 使其成为异步生成器

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        return ModelResponse(provider_id="test.empty", content="")


class _PlainProvider:
    """明确不支持流式（无 stream 方法）→ 允许 fallback。"""

    def __init__(self) -> None:
        self.complete_calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        content = request.messages[-1].content if request.messages else ""
        return ModelResponse(provider_id="test.plain", content=f"plain: {content}")


class _FlakyProvider:
    """流式输出 1 token 后失败。"""

    def __init__(self) -> None:
        self.stream_calls = 0
        self.complete_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        self.stream_calls += 1
        yield "partial"
        raise ModelProviderError("model provider test.flaky failed")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        return ModelResponse(provider_id="test.flaky", content="unreachable")


async def _seed_agent(
    store: PostgreSQLRegistryStore, agent_id: str, provider_id: str, model_id: str
) -> None:
    from fluxion.agents.definitions import AgentDefinition as AD

    await seed_model_definition(
        store, tenant_id="tenant-a", provider_id=provider_id, model_name="dev"
    )
    await seed_tenant_policy(store, tenant_id="tenant-a")
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id=agent_id,
        version="1",
        spec=AD(
            name=agent_id,
            description="fixture",
            system_prompt="p",
            owner="fixture",
            model_policy=AgentModelPolicy(
                primary_model_ref=ExactResourceVersion(id=model_id, version="1")
            ),
        ).model_dump(mode="json"),
    )


async def _service(
    providers: dict[str, object],
) -> tuple[RuntimeApplicationService, PostgreSQLRegistryStore, str]:
    from fluxion.services.runtime_app import CreateRuntimeProfileRequest

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    registry = ModelProviderRegistry()
    for provider_id, provider in providers.items():
        registry.register(provider_id, provider)  # type: ignore[arg-type]
    service = RuntimeApplicationService(store, model_providers=registry)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            default=True,
        )
    )
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a", runtime_profile_id="assistant", version="1"
        )
    )
    return service, store, ""


def _ids(char: str) -> tuple[str, str, str]:
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


def _run_request(char: str, agent_id: str = "assistant") -> RunRuntimeRequest:
    request_id, trace_id, execution_id = _ids(char)
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        agent_definition_id=agent_id,
        session_id="session-stream",
        input_message="hello",
        request_id=request_id,
        trace_id=trace_id,
        execution_id=execution_id,
    )


@pytest.mark.asyncio
async def test_S_STR_01_empty_stream_completes_once_with_empty_output() -> None:
    """S-STR-01：正常空结束只请求模型一次，completed output 为空（不回退）。"""
    empty = _EmptyStreamProvider()
    service, store, _ = await _service({"test.empty": empty})
    try:
        await seed_model_definition(
            store, tenant_id="tenant-a", provider_id="test.empty", model_name="dev"
        )
        await _seed_agent(store, "empty-agent", "test.empty", "model.test.empty")
        events = [event async for event in service.stream(_run_request("a", "empty-agent"))]
        kinds = [event.event for event in events]
        assert kinds[0] == "started"
        assert kinds[-1] == "completed"
        completed = next(event for event in events if event.event == "completed")
        assert completed.data["output"] == ""
        assert empty.stream_calls == 1
        assert empty.complete_calls == 0
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_B_STR_01_non_streaming_provider_falls_back_with_same_identity() -> None:
    """B-STR-01：明确 unsupported 才 fallback；身份不变；结果与 run 一致。"""
    plain = _PlainProvider()
    service, store, _ = await _service({"test.plain": plain})
    try:
        await seed_model_definition(
            store, tenant_id="tenant-a", provider_id="test.plain", model_name="dev"
        )
        await _seed_agent(store, "plain-agent", "test.plain", "model.test.plain")
        events = [event async for event in service.stream(_run_request("b", "plain-agent"))]
        completed = next(event for event in events if event.event == "completed")
        assert completed.data["output"] == "plain: hello"
        assert completed.data["execution_id"] == _ids("b")[2]
        direct = await service.run(_run_request("c", "plain-agent"))
        assert direct.output == "plain: hello"
        assert plain.complete_calls == 2
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_STR_01_partial_output_error_propagates_without_recall() -> None:
    """E-STR-01：部分输出后 Provider 错误传播；不二次请求模型；Trace 完整。"""
    from fluxion.services.runtime_contracts import RuntimeApplicationError

    flaky = _FlakyProvider()
    service, store, _ = await _service({"test.flaky": flaky})
    try:
        await seed_model_definition(
            store, tenant_id="tenant-a", provider_id="test.flaky", model_name="dev"
        )
        await _seed_agent(store, "flaky-agent", "test.flaky", "model.test.flaky")
        seen: list = []

        async def _drain() -> None:
            async for event in service.stream(_run_request("d", "flaky-agent")):
                seen.append(event)

        with pytest.raises(RuntimeApplicationError):
            await _drain()
        assert [event.event for event in seen] == ["started", "token"]
        assert flaky.stream_calls == 1
        assert flaky.complete_calls == 0
        record = await service.trace_store.get(_ids("d")[1])
        assert record is not None
    finally:
        await service.close()
        await store.close()
