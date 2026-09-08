"""TASK-006: 输入 token 估算修复验收（S-06）。

真实边界：真实 RuntimeApplicationService.run（含 AgentRuntime.run_step、
真实 SessionMemoryStore、真实 trace 持久化）+ dev.echo Provider。
不读 Provider usage（不存在），不新增 tokenizer 依赖。
"""

from __future__ import annotations

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import seed_agent_definition, TEST_POSTGRES_DSN


async def _step_attributes(input_message: str) -> dict[str, object]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store)
    await service.initialize()
    try:
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
        result = await service.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-t",
                input_message=input_message,
                agent_definition_id="assistant",
            )
        )
        trace = await service.trace_store.get(result.trace_id)
        assert trace is not None
        steps = [event for event in trace.events if event.name == "execution.step"]
        assert steps, "缺少 execution.step 事件"
        return dict(steps[-1].attributes)
    finally:
        await service.close()
        await store.close()


class TestS06TokenEstimation:
    @pytest.mark.asyncio
    async def test_chinese_input_not_one(self) -> None:
        """S-06：「帮我查询订单」估算不再为 1（复用 Memory CJK 算法）。"""
        attributes = await _step_attributes("帮我查询订单")
        assert attributes["input_tokens"] == 7

    @pytest.mark.asyncio
    async def test_estimate_markers_present(self) -> None:
        """S-06：估算口径标记清晰（来源/范围），不冒充计费 usage。"""
        attributes = await _step_attributes("hello world")
        assert attributes["input_tokens"] == 2
        assert attributes["token_source"] == "estimate"
        assert attributes["token_scope"] == "input_message"

    @pytest.mark.asyncio
    async def test_mixed_and_empty_boundaries(self) -> None:
        """S-06：中英混合/空输入/长输入边界固定、可重复。"""
        first = await _step_attributes("hello 查询订单 test")
        second = await _step_attributes("hello 查询订单 test")
        assert first["input_tokens"] == second["input_tokens"] == 3 + 4
        empty = await _step_attributes("")
        assert empty["input_tokens"] == 1
        long_text = "订单" * 5000
        long_result = await _step_attributes(long_text)
        assert long_result["input_tokens"] == 1 + 10000

    @pytest.mark.asyncio
    async def test_memory_cjk_no_regression(self) -> None:
        """S-06：Memory CJK 估算不倒退（六字中文样例 = 7）。"""
        from fluxion.runtime.tokens import estimate_text_tokens

        assert estimate_text_tokens("帮我查询订单") == 7
        assert estimate_text_tokens("hello world") == 2
        assert estimate_text_tokens("") == 1


class TestS06MemoryStoreTokens:
    @pytest.mark.asyncio
    async def test_session_message_tokens_use_shared_estimator(self) -> None:
        """S-06：SessionMemoryStore 落库 tokens 与共享估算一致（单一工具）。"""
        from fluxion.runtime.tokens import estimate_text_tokens

        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        service = RuntimeApplicationService.create_dev_bundle(store)
        await service.initialize()
        try:
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
            await service.run(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    runtime_profile_id="assistant",
                    session_id="session-m",
                    input_message="帮我查询订单",
                    agent_definition_id="assistant",
                )
            )
            memory = service._runtime.memory  # type: ignore[union-attr]
            records = await memory._store.read_l1("tenant-a", "session-m")
            user_records = [record for record in records if record.role == "user"]
            assert user_records, "用户消息未持久化"
            assert user_records[-1].tokens == estimate_text_tokens("帮我查询订单") == 7
        finally:
            await service.close()
            await store.close()
