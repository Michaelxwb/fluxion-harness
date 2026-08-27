from __future__ import annotations

import pytest

from fluxion.plugins.contracts import ModelRequest, ModelResponse
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)


class StreamingEchoProvider:
    """支持流式输出的测试 provider：逐 token yield 最终答案。"""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(provider_id="custom-stream", content="你好世界")

    async def stream(self, request: ModelRequest):
        del request
        for token in ("你", "好", "世界"):
            yield token


@pytest.mark.asyncio
async def test_stream_yields_tokens_when_provider_supports_streaming() -> None:
    registry = ModelProviderRegistry()
    registry.register("custom-stream", StreamingEchoProvider())
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = RuntimeApplicationService(store, model_providers=registry)
    await service.initialize()
    try:
        await service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
                request_timeout_ms=1_000,
            )
        )
        from tests.runtime_helpers import seed_agent_definition
        await seed_agent_definition(store, provider_id="custom-stream")

        await service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
            )
        )
        events = [
            event
            async for event in service.stream(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    runtime_profile_id="assistant",
                    session_id="session-a",
                    input_message="hi",
                )
            )
        ]
    finally:
        await service.close()

    assert [event.event for event in events] == ["started", "token", "token", "token", "completed"]
    tokens = [event.data["content"] for event in events if event.event == "token"]
    assert tokens == ["你", "好", "世界"]
    completed = next(event for event in events if event.event == "completed")
    assert completed.data["output"] == "你好世界"
