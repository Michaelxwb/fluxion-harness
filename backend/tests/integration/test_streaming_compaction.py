"""TASK-009: 流式统一压缩验收（S-09/E-05）。

真实边界：真实 AgentRuntime + 真实 SessionMemoryStore + 真实 compaction
调度 + 真实 `_model_messages` Prompt 构建 + 记录请求的确定性流式 Provider
适配器（service 层覆盖持久化一次 + trace）。不额外调用 run_step。
"""

from __future__ import annotations

import pytest

from fluxion.plugins.contracts import ModelProviderError, ModelRequest, ModelResponse
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime import AgentRuntime
from fluxion.runtime.context import RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore, MemoryPolicy
from fluxion.runtime.summarizer import SummarizerRegistry
from fluxion.services.context_resolver import ContextResolver, ContextResolverSnapshotBuilder
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import publish_resource, seed_agent_definition, seed_tenant_policy, TEST_POSTGRES_DSN

OLD_FACT = "流式旧事实ZZ999"
SUMMARY_BOUNDARY = "历史会话摘要，仅作上下文资料"


class RecordingStreamProvider:
    """记录请求的确定性流式 Provider（complete/stream 计数 + 请求捕获）。"""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.complete_calls = 0
        self.stream_calls = 0
        self.fail_stream = False

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        self.requests.append(request)
        return ModelResponse(provider_id="recording-stream", content="流式答案")

    async def stream(self, request: ModelRequest):  # type: ignore[no-untyped-def]
        self.stream_calls += 1
        self.requests.append(request)
        if self.fail_stream:
            raise RuntimeError("stream boom")
        for token in ("流", "式"):
            yield token


def _registry(provider: RecordingStreamProvider) -> ModelProviderRegistry:
    registry = ModelProviderRegistry()
    registry.register("recording-stream", provider)
    return registry


async def _seed_chain(store: PostgreSQLRegistryStore) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_agent_definition(store, provider_id="recording-stream", model_name="rec")
    await seed_tenant_policy(store, tenant_id="tenant-a")


def _runtime(
    store: PostgreSQLRegistryStore,
    provider: RecordingStreamProvider,
    *,
    max_context_tokens: int = 20,
) -> AgentRuntime:
    return AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(store)),
        memory_store=InMemorySessionMemoryStore(),
        memory_policy=MemoryPolicy(max_context_tokens=max_context_tokens, retain_latest_turns=2),
        model_providers=_registry(provider),
    )


async def _start(runtime: AgentRuntime, session_id: str = "session-s09"):  # type: ignore[no-untyped-def]
    return await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            agent_definition_id="assistant",
            runtime_profile_id="assistant",
            session_id=session_id,
        )
    )


def _user_texts(messages: list) -> list[str]:  # type: ignore[no-untyped-def]
    return [message.content for message in messages if message.role == "user"]


class TestS09StreamingCompaction:
    @pytest.mark.asyncio
    async def test_stream_triggers_compaction_before_model_call(self) -> None:
        """S-09：无工具流式在模型调用前触发压缩（与非流式一致的历史准备）。"""
        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        await store.initialize()
        try:
            await _seed_chain(store)
            provider = RecordingStreamProvider()
            runtime = _runtime(store, provider)
            context = await _start(runtime)
            await runtime.run_step(context, f"记住{OLD_FACT}备用")
            await runtime.run_step(context, "闲聊两句天气")
            tokens = [token async for token in runtime.stream_final_answer(context, "查事实")]
            assert tokens == ["流", "式"]
            assert any("memory.compacted" == event.name for event in context.trace), "流式未触发压缩"
            texts = _user_texts(provider.requests[-1].messages)
            assert any(OLD_FACT in text for text in texts), "流式请求丢失摘要事实"
            assert any(SUMMARY_BOUNDARY in text for text in texts)
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_stream_history_equivalent_to_non_stream(self) -> None:
        """S-09：流式与非流式得到等价历史（同摘要 + 同保留）。"""
        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        await store.initialize()
        try:
            await _seed_chain(store)

            async def _non_stream_texts() -> list[str]:
                provider = RecordingStreamProvider()
                runtime = _runtime(store, provider)
                context = await _start(runtime, "session-eq-a")
                await runtime.run_step(context, f"记住{OLD_FACT}备用")
                await runtime.run_step(context, "闲聊两句天气")
                await runtime.run_step(context, "查事实")
                return _user_texts(provider.requests[-1].messages)

            async def _stream_texts() -> list[str]:
                provider = RecordingStreamProvider()
                runtime = _runtime(store, provider)
                context = await _start(runtime, "session-eq-b")
                await runtime.run_step(context, f"记住{OLD_FACT}备用")
                await runtime.run_step(context, "闲聊两句天气")
                await runtime.run_step(context, "再聊一句")
                [token async for token in runtime.stream_final_answer(context, "查事实")]
                return _user_texts(provider.requests[-1].messages)

            non_stream = await _non_stream_texts()
            streamed = await _stream_texts()
            # 两者都含摘要边界 + 旧事实（等价历史），当前输入各一次。
            for texts in (non_stream, streamed):
                assert any(OLD_FACT in text for text in texts)
                assert any(SUMMARY_BOUNDARY in text for text in texts)
            assert streamed.count("查事实") == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_service_stream_persists_input_output_once(self) -> None:
        """S-09：成功流式输入输出各持久化一次（service 保存分支）。"""
        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        provider = RecordingStreamProvider()
        service = RuntimeApplicationService(store, model_providers=_registry(provider))
        await service.initialize()
        try:
            await service.create_runtime_profile(
                CreateRuntimeProfileRequest(
                    tenant_id="tenant-a", runtime_profile_id="assistant",
                    version="1", default=True,
                )
            )
            await seed_agent_definition(store, provider_id="recording-stream", model_name="rec")
            await service.publish_runtime_profile(
                PublishRuntimeProfileRequest(tenant_id="tenant-a", runtime_profile_id="assistant", version="1")
            )
            events = [
                event async for event in service.stream(
                    RunRuntimeRequest(
                        tenant_id="tenant-a", user_id="user-a", runtime_profile_id="assistant",
                        session_id="session-persist", input_message="持久化检查",
                        agent_definition_id="assistant",
                    )
                )
            ]
            assert events[0].event == "started"
            assert events[-1].event == "completed"
            memory = service._runtime.memory
            records = await memory._store.read_l1("tenant-a", "session-persist")
            user_inputs = [record.content for record in records if record.role == "user"]
            assistant_outputs = [record.content for record in records if record.role == "assistant"]
            assert user_inputs.count("持久化检查") == 1, f"输入持久化次数错误：{user_inputs}"
            assert assistant_outputs.count("流式") == 1, f"输出持久化次数错误：{assistant_outputs}"
        finally:
            await service.close()
            await store.close()


class TestE05StreamingFailurePolicy:
    @pytest.mark.asyncio
    async def test_no_deletion_without_successful_summary(self) -> None:
        """E-05：摘要彻底失败时不删旧消息（有界失败策略，无静默丢数据）。"""
        from fluxion.runtime.summarizer import SUMMARIZER_DETERMINISTIC

        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        await store.initialize()
        try:
            await _seed_chain(store)
            provider = RecordingStreamProvider()

            class _AlwaysFail:
                async def summarize(self, records: list, *, token_budget: int):  # type: ignore[no-untyped-def]
                    raise RuntimeError("summarizer down")

            registry = SummarizerRegistry()
            registry.register(SUMMARIZER_DETERMINISTIC, _AlwaysFail())
            runtime = AgentRuntime(
                snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(store)),
                memory_store=InMemorySessionMemoryStore(),
                memory_policy=MemoryPolicy(max_context_tokens=10, retain_latest_turns=2),
                model_providers=_registry(provider),
                summarizer_registry=registry,
            )
            context = await _start(runtime)
            await runtime.run_step(context, f"记住{OLD_FACT}备用")
            await runtime.run_step(context, "闲聊两句天气")
            # 此时历史已超预算且 older 非空 → 压缩调度触发，摘要彻底失败上抛。
            with pytest.raises(RuntimeError, match="summarizer down"):
                await runtime.run_step(context, "触发压缩")
            l1 = await runtime.memory._store.read_l1("tenant-a", "session-s09")
            assert any(OLD_FACT in record.content for record in l1), "失败时旧消息被删除"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_stream_failure_does_not_reexecute(self) -> None:
        """E-05：流式失败不重复执行（不回退静默重试，不调 complete）。"""
        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        await store.initialize()
        try:
            await _seed_chain(store)
            provider = RecordingStreamProvider()
            provider.fail_stream = True
            runtime = _runtime(store, provider, max_context_tokens=10_000)
            context = await _start(runtime)
            with pytest.raises(ModelProviderError):
                [token async for token in runtime.stream_final_answer(context, "会失败的流")]
            assert provider.stream_calls == 1
            assert provider.complete_calls == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_failed_stream_releases_resources(self) -> None:
        """E-05：流式失败释放流资源（aclose 被调用，不泄漏迭代器）。"""
        closed: list[bool] = []

        class _LeakyStream:
            def __init__(self) -> None:
                self.closed = False

            def __aiter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __anext__(self):  # type: ignore[no-untyped-def]
                raise RuntimeError("mid-stream boom")

            async def aclose(self) -> None:
                self.closed = True
                closed.append(True)

        class _LeakyProvider(RecordingStreamProvider):
            def stream(self, request):  # type: ignore[no-untyped-def]
                self.stream_calls += 1
                self.requests.append(request)
                return _LeakyStream()

        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        await store.initialize()
        try:
            await _seed_chain(store)
            provider = _LeakyProvider()
            runtime = _runtime(store, provider, max_context_tokens=10_000)
            context = await _start(runtime)
            with pytest.raises(ModelProviderError):
                [token async for token in runtime.stream_final_answer(context, "boom")]
            assert closed == [True]
        finally:
            await store.close()
