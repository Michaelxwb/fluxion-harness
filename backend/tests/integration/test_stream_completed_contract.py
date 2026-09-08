"""FEAT-02 流式 completed 与非流式全等（P1）。

真实边界：真实 RuntimeApplicationService.stream + run（同 store，不同 session）。
执行域字段（request_id/trace_id/execution_id/latency_ms）两次执行必然不同，
断言：键集合一致 + 非执行域字段值一致（含真实 model_provider_id）。
"""

from __future__ import annotations

import pytest

from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.e2e.test_runtime_streaming import StreamingEchoProvider
from tests.runtime_helpers import TEST_POSTGRES_DSN

_SHARED_FIELDS = (
    "service_instance_id",
    "runtime_profile_id",
    "runtime_profile_version",
    "output",
    "model_provider_id",
    "tool_results",
)
_EXECUTION_FIELDS = ("request_id", "trace_id", "execution_id", "latency_ms")


async def _service() -> RuntimeApplicationService:
    registry = ModelProviderRegistry()
    registry.register("custom-stream", StreamingEchoProvider())
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
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
    from tests.runtime_helpers import seed_agent_definition

    await seed_agent_definition(store, provider_id="custom-stream")
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
        )
    )
    return service


def _request(session: str) -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_definition_id="assistant",
        runtime_profile_id="assistant",
        session_id=session,
        input_message="hi",
    )


@pytest.mark.asyncio
async def test_s02_stream_completed_matches_run_payload() -> None:
    """S-02：流式 completed 与 run().to_payload() 键集合一致，共享字段值一致。"""
    service = await _service()
    try:
        streamed = [event async for event in service.stream(_request("session-stream"))]
        result = await service.run(_request("session-run"))
    finally:
        await service.close()
    completed = next(event for event in streamed if event.event == "completed")
    assert [event.event for event in streamed].count("token") == 3
    payload = result.to_payload()
    assert set(completed.data.keys()) == set(payload.keys()), (
        set(payload.keys()) ^ set(completed.data.keys())
    )
    for field in _SHARED_FIELDS:
        assert completed.data[field] == payload[field], field
    assert completed.data["model_provider_id"] == "custom-stream"
    for field in _EXECUTION_FIELDS:
        assert completed.data[field], field
